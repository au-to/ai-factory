from __future__ import annotations

import logging
from datetime import datetime
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
    Handles retries, gate checks, and self-iteration loops.
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

        for stage in config.stages:
            logger.info(f"Stage [{stage.name}]: starting")
            self.state.update_run_status(run_id, "running", stage.name)

            success = self._run_stage_with_retry(stage, run_id, extra_context)
            if not success:
                logger.error(f"Stage [{stage.name}]: failed after {stage.retry_limit} retries")
                self.state.update_run_status(run_id, "failed", stage.name)
                return False

            if stage.gate:
                approved = self._await_gate(stage, run_id)
                if not approved:
                    logger.warning(f"Stage [{stage.name}]: gate rejected")
                    self.state.update_run_status(run_id, "rejected", stage.name)
                    return False
                logger.info(f"Stage [{stage.name}]: gate approved")

            logger.info(f"Stage [{stage.name}]: completed")

        self.state.update_run_status(run_id, "completed")
        logger.info(f"Pipeline '{config.name}' (run: {run_id}) completed successfully")
        return True

    def _run_stage_with_retry(self, stage: StageConfig, run_id: str,
                               extra_context: Optional[dict]) -> bool:
        for attempt in range(stage.retry_limit + 1):
            if attempt > 0:
                logger.info(f"Stage [{stage.name}]: retry {attempt}/{stage.retry_limit}")

            result = self.stage_runner.execute(stage, run_id, extra_context)
            if result.success:
                return True

            logger.warning(f"Stage [{stage.name}]: attempt {attempt} failed — {result.stderr[:200]}")

        return False

    def _await_gate(self, stage: StageConfig, run_id: str) -> bool:
        """Set up the gate and wait for human approval."""
        self.state.set_stage_status(run_id, stage.name, StageStatus.AWAITING_APPROVAL)
        label = stage.gate_label or f"{stage.name.title()} Approval"

        # Create the gate review file
        artifact = self.bus.read(stage.output_artifact, run_id)
        self.gates.create_gate(run_id, stage.name, label, artifact)

        # Poll for approval
        status = self.gates.wait_for_decision(run_id, stage.name)
        self.state.set_gate_status(run_id, stage.name, status)

        if status == GateStatus.CHANGES_REQUESTED:
            feedback = self.gates.get_feedback(run_id, stage.name)
            logger.info(f"Stage [{stage.name}]: changes requested — {feedback}")
            self.state.set_stage_status(run_id, stage.name, StageStatus.IN_PROGRESS)
            return self._run_stage_with_retry(stage, run_id, extra_context={"feedback": feedback})

        return status == GateStatus.APPROVED

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
