from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

from factory.bus.schemas import GateStatus
from factory.gates.review import render_review_md

logger = logging.getLogger(__name__)


class GateManager:
    """Manages human-in-the-loop approval gates via filesystem files.

    Each gate is a GATE_REVIEW.md file in <workspace>/gates/<run-id>/<stage-name>.md.
    Approval/rejection/changes-requested are signaled by writing companion files.

    The gate lifecycle:
        1. Pipeline creates GATE_REVIEW.md (with checkbox)
        2. Human toggles checkbox (or CLI command writes APPROVED/REJECTED/CHANGES_REQUESTED)
        3. Pipeline polls for the decision file
        4. On CHANGES_REQUESTED: human writes FEEDBACK.md, pipeline retries the stage
    """

    POLL_INTERVAL = 5  # seconds
    MAX_WAIT = 3600 * 8  # 8 hours

    def __init__(self, workspace_path: str | Path):
        self.workspace = Path(workspace_path)
        self.gates_dir = self.workspace / "gates"

    def gate_dir(self, run_id: str, stage_name: str) -> Path:
        return self.gates_dir / run_id / stage_name

    def create_gate(self, run_id: str, stage_name: str, label: str,
                    artifact: object = None) -> Path:
        """Create a GATE_REVIEW.md file for human approval."""
        gate_dir = self.gate_dir(run_id, stage_name)
        gate_dir.mkdir(parents=True, exist_ok=True)

        # Build artifact summary for display
        artifact_summary = ""
        if artifact and hasattr(artifact, 'model_dump'):
            import json
            artifact_summary = json.dumps(artifact.model_dump(), indent=2, ensure_ascii=False)

        content = render_review_md(run_id, stage_name, label, artifact_summary)
        gate_file = gate_dir / "GATE_REVIEW.md"
        gate_file.write_text(content)
        return gate_file

    def approve(self, run_id: str, stage_name: str) -> bool:
        """Approve a stage gate."""
        return self._write_decision(run_id, stage_name, GateStatus.APPROVED)

    def reject(self, run_id: str, stage_name: str, reason: str = "") -> bool:
        """Reject a stage gate."""
        if reason:
            feedback_file = self.gate_dir(run_id, stage_name) / "FEEDBACK.md"
            feedback_file.parent.mkdir(parents=True, exist_ok=True)
            feedback_file.write_text(f"# Rejection Reason\n\n{reason}")
        return self._write_decision(run_id, stage_name, GateStatus.REJECTED)

    def request_changes(self, run_id: str, stage_name: str, feedback: str) -> bool:
        """Request changes on a stage gate."""
        feedback_file = self.gate_dir(run_id, stage_name) / "FEEDBACK.md"
        feedback_file.parent.mkdir(parents=True, exist_ok=True)
        feedback_file.write_text(feedback)
        return self._write_decision(run_id, stage_name, GateStatus.CHANGES_REQUESTED)

    def get_feedback(self, run_id: str, stage_name: str) -> Optional[str]:
        """Read feedback from FEEDBACK.md if it exists."""
        feedback_file = self.gate_dir(run_id, stage_name) / "FEEDBACK.md"
        if feedback_file.exists():
            return feedback_file.read_text()
        return None

    def wait_for_decision(self, run_id: str, stage_name: str,
                          poll_interval: int = None,
                          max_wait: int = None) -> GateStatus:
        """Poll for human decision on a gate. Blocks until decided."""
        poll_interval = poll_interval or self.POLL_INTERVAL
        max_wait = max_wait or self.MAX_WAIT
        elapsed = 0

        while elapsed < max_wait:
            decision = self._read_decision(run_id, stage_name)
            if decision and decision != GateStatus.PENDING:
                return decision
            time.sleep(poll_interval)
            elapsed += poll_interval

        logger.warning(f"Gate for {stage_name} timed out after {max_wait}s — auto-rejecting")
        self.reject(run_id, stage_name, "Timeout: no decision within the wait period")
        return GateStatus.REJECTED

    def get_decision(self, run_id: str, stage_name: str) -> GateStatus:
        """Non-blocking check of gate decision."""
        return self._read_decision(run_id, stage_name)

    def _write_decision(self, run_id: str, stage_name: str, status: GateStatus) -> bool:
        decision_file = self.gate_dir(run_id, stage_name) / "DECISION"
        decision_file.parent.mkdir(parents=True, exist_ok=True)
        decision_file.write_text(status.value)
        return True

    def _read_decision(self, run_id: str, stage_name: str) -> GateStatus:
        decision_file = self.gate_dir(run_id, stage_name) / "DECISION"
        if not decision_file.exists():
            return GateStatus.PENDING
        value = decision_file.read_text().strip()
        try:
            return GateStatus(value)
        except ValueError:
            return GateStatus.PENDING
