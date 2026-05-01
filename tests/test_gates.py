"""Tests for the human-in-the-loop gate system."""

import tempfile
from pathlib import Path

import pytest

from factory.bus.schemas import GateStatus
from factory.gates.gate import GateManager


@pytest.fixture
def gate_manager():
    with tempfile.TemporaryDirectory() as tmp:
        yield GateManager(tmp)


class TestGateManager:
    def test_create_gate_creates_file(self, gate_manager):
        gate_manager.create_gate("run-1", "requirements", "PRD Approval")
        gate_file = gate_manager.gate_dir("run-1", "requirements") / "GATE_REVIEW.md"
        assert gate_file.exists()
        content = gate_file.read_text()
        assert "PRD Approval" in content
        assert "run-1" in content

    def test_approve_writes_decision(self, gate_manager):
        gate_manager.create_gate("run-1", "requirements", "PRD Approval")
        gate_manager.approve("run-1", "requirements")

        decision = gate_manager.get_decision("run-1", "requirements")
        assert decision == GateStatus.APPROVED

    def test_reject_writes_decision(self, gate_manager):
        gate_manager.create_gate("run-1", "requirements", "PRD Approval")
        gate_manager.reject("run-1", "requirements", "Not good enough")

        decision = gate_manager.get_decision("run-1", "requirements")
        assert decision == GateStatus.REJECTED

        feedback = gate_manager.get_feedback("run-1", "requirements")
        assert "Not good enough" in feedback

    def test_changes_requested(self, gate_manager):
        gate_manager.create_gate("run-1", "requirements", "PRD Approval")
        gate_manager.request_changes("run-1", "requirements", "Add more detail")

        decision = gate_manager.get_decision("run-1", "requirements")
        assert decision == GateStatus.CHANGES_REQUESTED

        feedback = gate_manager.get_feedback("run-1", "requirements")
        assert "Add more detail" in feedback

    def test_pending_when_no_decision(self, gate_manager):
        gate_manager.create_gate("run-1", "requirements", "PRD Approval")
        decision = gate_manager.get_decision("run-1", "requirements")
        assert decision == GateStatus.PENDING
