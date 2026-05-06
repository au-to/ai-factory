from __future__ import annotations

import logging
import shutil
import threading
from pathlib import Path
from typing import Optional

from factory.agents.prompt import PromptBuilder
from factory.agents.registry import AgentRegistry
from factory.bus.schemas import GateStatus, StageStatus
from factory.bus.store import ContextBus
from factory.gates.gate import GateManager
from factory.pipeline.config import PipelineConfig, StageConfig, load_pipeline_config
from factory.pipeline.runner import StageRunner
from factory.pipeline.state import PipelineState
from factory.tools.claude import ClaudeRunner

logger = logging.getLogger(__name__)


class PipelineEngine:
    """The core pipeline state machine.

    Drives stage transitions: pending -> in_progress -> awaiting_approval | completed | failed.
    Handles retries, gate checks, self-iteration loops, cancellation, and deletion.
    """

    def __init__(self, workspace_path: str | Path,
                 configs_dir: str | Path = "configs"):
        self.workspace = Path(workspace_path)
        self.configs_dir = Path(configs_dir)

        # Subsystems
        self.bus = ContextBus(workspace_path)
        self.state = PipelineState(self.workspace / "sessions" / "factory.db")
        self.registry = AgentRegistry(self.configs_dir / "agents")
        self.builder = PromptBuilder(self.workspace.parent / "templates" / "prompts")
        self.runner = ClaudeRunner(workspace_path)
        self.gates = GateManager(workspace_path)

        # Cancellation tracking
        self._cancelled: set[str] = set()
        self._cancel_events: dict[str, threading.Event] = {}

        # Stage runner
        self.stage_runner = StageRunner(
            registry=self.registry,
            builder=self.builder,
            runner=self.runner,
            bus=self.bus,
            state=self.state,
        )

    def run(self, pipeline_config: str | Path, run_id: str,
            extra_context: Optional[dict] = None) -> bool:
        """Run a full pipeline. Returns True if all stages completed successfully."""
        config = load_pipeline_config(pipeline_config)

        self.state.create_run(run_id, config.name)
        logger.info(f"Starting pipeline '{config.name}' (run: {run_id})")

        cancel_event = threading.Event()
        self._cancel_events[run_id] = cancel_event

        for stage in config.stages:
            if run_id in self._cancelled:
                logger.info(f"Pipeline '{run_id}': cancelled before stage [{stage.name}]")
                self.state.update_run_status(run_id, "cancelled", stage.name)
                return False

            logger.info(f"Stage [{stage.name}]: starting")
            self.state.update_run_status(run_id, "running", stage.name)

            success = self._run_stage_with_retry(stage, run_id, cancel_event, extra_context)
            if not success:
                if run_id in self._cancelled:
                    logger.info(f"Pipeline '{run_id}': cancelled during stage [{stage.name}]")
                    self.state.update_run_status(run_id, "cancelled", stage.name)
                    return False
                logger.error(f"Stage [{stage.name}]: failed after {stage.retry_limit} retries")
                self.state.update_run_status(run_id, "failed", stage.name)
                return False

            if stage.gate:
                approved = self._await_gate(stage, run_id)
                if not approved:
                    if run_id in self._cancelled:
                        logger.info(f"Pipeline '{run_id}': cancelled during gate [{stage.name}]")
                        self.state.update_run_status(run_id, "cancelled", stage.name)
                        return False
                    logger.warning(f"Stage [{stage.name}]: gate rejected")
                    self.state.update_run_status(run_id, "rejected", stage.name)
                    return False
                logger.info(f"Stage [{stage.name}]: gate approved")

            logger.info(f"Stage [{stage.name}]: completed")

        self.state.update_run_status(run_id, "completed")
        logger.info(f"Pipeline '{config.name}' (run: {run_id}) completed successfully")
        self._cleanup_run(run_id)
        return True

    def _run_stage_with_retry(self, stage: StageConfig, run_id: str,
                               cancel_event: threading.Event,
                               extra_context: Optional[dict]) -> bool:
        for attempt in range(stage.retry_limit + 1):
            if run_id in self._cancelled:
                return False
            if attempt > 0:
                logger.info(f"Stage [{stage.name}]: retry {attempt}/{stage.retry_limit}")

            result = self.stage_runner.execute(stage, run_id, cancel_event, extra_context)
            if result.success:
                return True

            if run_id in self._cancelled:
                return False

            logger.warning(f"Stage [{stage.name}]: attempt {attempt} failed — {result.stderr[:200]}")

        return False

    def _await_gate(self, stage: StageConfig, run_id: str) -> bool:
        """Set up the gate and wait for human approval. Checks for cancellation."""
        self.state.set_stage_status(run_id, stage.name, StageStatus.AWAITING_APPROVAL)
        label = stage.gate_label or f"{stage.name.title()} Approval"

        artifact = self.bus.read(stage.output_artifact, run_id)
        self.gates.create_gate(run_id, stage.name, label, artifact)

        # Poll for approval, checking cancellation alongside
        status = self.gates.wait_for_decision(run_id, stage.name,
                                              cancel_check=lambda: run_id in self._cancelled)
        if run_id in self._cancelled:
            return False

        self.state.set_gate_status(run_id, stage.name, status)

        if status == GateStatus.CHANGES_REQUESTED:
            feedback = self.gates.get_feedback(run_id, stage.name)
            logger.info(f"Stage [{stage.name}]: changes requested — {feedback}")
            self.state.set_stage_status(run_id, stage.name, StageStatus.IN_PROGRESS)
            return self._run_stage_with_retry(stage, run_id,
                                              self._cancel_events.get(run_id, threading.Event()),
                                              extra_context={"feedback": feedback})

        return status == GateStatus.APPROVED

    # --- Public API ---

    def cancel_run(self, run_id: str) -> bool:
        """Cancel a running pipeline. Kills the Claude subprocess if active,
        and rejects any pending gates so the pipeline thread exits cleanly."""
        run = self.state.get_run(run_id)
        if not run:
            return False
        if run["status"] not in ("running", "pending"):
            return False

        self._cancelled.add(run_id)

        # Signal the cancel event for any waiting thread
        event = self._cancel_events.get(run_id)
        if event:
            event.set()

        # Kill the running Claude subprocess if any
        self.runner.cancel(run_id)

        # Reject any pending gate so the polling loop exits immediately
        stages = self.state.get_all_stages(run_id)
        for s in stages:
            if s["status"] == "awaiting_approval":
                self.gates.reject(run_id, s["stage_name"], "Pipeline cancelled")
                self.state.set_gate_status(run_id, s["stage_name"], GateStatus.REJECTED)
                break

        # If the pipeline's thread is dead (e.g. dashboard restarted) and no
        # Claude subprocess is running, update the status directly.
        if not self.runner.is_running(run_id):
            self.state.update_run_status(run_id, "cancelled")

        logger.info(f"Pipeline '{run_id}': cancellation requested")
        return True

    def delete_run(self, run_id: str) -> bool:
        """Delete a pipeline run and all associated data. Refuses running runs."""
        run = self.state.get_run(run_id)
        if not run:
            return False
        if run["status"] == "running":
            return False

        # Stop preview if running
        preview_proc = getattr(self, '_preview_mgr', None)
        if preview_proc:
            preview_proc.stop(run_id)

        # Remove from cancellation tracking
        self._cancelled.discard(run_id)
        self._cancel_events.pop(run_id, None)

        # Remove SQLite records
        import sqlite3
        db_path = self.state.db_path
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute("DELETE FROM gates WHERE run_id = ?", (run_id,))
            conn.execute("DELETE FROM stages WHERE run_id = ?", (run_id,))
            conn.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))

        # Remove filesystem data
        for subdir in ["artifacts", "gates", "sessions"]:
            path = self.workspace / subdir / run_id
            if path.exists():
                shutil.rmtree(path)

        logger.info(f"Run '{run_id}': deleted")
        return True

    def _cleanup_run(self, run_id: str) -> None:
        """Remove cancellation state after pipeline finishes."""
        self._cancelled.discard(run_id)
        self._cancel_events.pop(run_id, None)

    def approve_stage(self, run_id: str, stage_name: str) -> bool:
        """Approve a stage gate from the CLI."""
        return self.gates.approve(run_id, stage_name)

    def reject_stage(self, run_id: str, stage_name: str, reason: str = "") -> bool:
        """Reject a stage gate from the CLI."""
        return self.gates.reject(run_id, stage_name, reason)

    def request_changes(self, run_id: str, stage_name: str, feedback: str) -> bool:
        """Request changes on a stage gate from the CLI."""
        return self.gates.request_changes(run_id, stage_name, feedback)

    def get_status(self, run_id: str) -> Optional[dict]:
        """Get the full status of a pipeline run."""
        run = self.state.get_run(run_id)
        if not run:
            return None

        stages = self.state.get_all_stages(run_id)
        gates = self.state.get_all_gates(run_id)

        return {
            "run": run,
            "stages": stages,
            "gates": gates,
        }
