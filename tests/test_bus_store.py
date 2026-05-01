"""Tests for the Context Bus artifact store."""

import json
import tempfile
from pathlib import Path

import pytest

from factory.bus.schemas import (
    PRDArtifact,
    TechSpecArtifact,
    BuildLogArtifact,
    QaReportArtifact,
    DeployConfigArtifact,
)
from factory.bus.store import ContextBus


@pytest.fixture
def bus():
    with tempfile.TemporaryDirectory() as tmp:
        yield ContextBus(tmp)


@pytest.fixture
def run_id():
    return "test-run-001"


class TestContextBus:
    def test_write_and_read_prd(self, bus, run_id):
        prd = PRDArtifact(
            run_id=run_id,
            title="Test App",
            problem_statement="Users need to track tasks",
            functional_requirements=[
                {"id": "FR-001", "description": "Create task", "priority": "high"}
            ],
        )

        v = bus.write("requirements", prd, run_id)
        assert v == 1

        read_back = bus.read("requirements", run_id)
        assert read_back is not None
        assert read_back.title == "Test App"
        assert read_back.version == 1

    def test_version_increments(self, bus, run_id):
        prd1 = PRDArtifact(run_id=run_id, title="v1", problem_statement="First version")
        prd2 = PRDArtifact(run_id=run_id, title="v2", problem_statement="Second version")

        v1 = bus.write("requirements", prd1, run_id)
        v2 = bus.write("requirements", prd2, run_id)

        assert v1 == 1
        assert v2 == 2

        # Read latest
        latest = bus.read("requirements", run_id)
        assert latest.title == "v2"

        # Read specific version
        specific = bus.read("requirements", run_id, version=1)
        assert specific.title == "v1"

    def test_write_raw_validates(self, bus, run_id):
        raw = {"run_id": run_id, "title": "Raw PRD", "problem_statement": "Test"}
        v = bus.write_raw("requirements", raw, run_id)
        assert v == 1

    def test_write_raw_rejects_invalid(self, bus, run_id):
        raw = {"title": "Missing required fields"}
        with pytest.raises(Exception):
            bus.write_raw("requirements", raw, run_id)

    def test_read_all_stages(self, bus, run_id):
        bus.write("requirements", PRDArtifact(run_id=run_id, title="Test",
                                               problem_statement="Test"), run_id)
        bus.write("design", TechSpecArtifact(run_id=run_id, overview="Architecture",
                                              components=[], tech_stack={}), run_id)

        all_artifacts = bus.read_latest(run_id)
        assert all_artifacts["requirements"] is not None
        assert all_artifacts["design"] is not None
        assert all_artifacts["testing"] is None  # Not yet written

    def test_manifest_tracks_versions(self, bus, run_id):
        bus.write("requirements", PRDArtifact(run_id=run_id, title="Test",
                                               problem_statement="Test"), run_id)
        bus.write("design", TechSpecArtifact(run_id=run_id, overview="Test",
                                              components=[], tech_stack={}), run_id)

        manifest = bus.manifest(run_id)
        all_versions = manifest.get_all()
        assert all_versions["requirements"]["requirements.json"] == 1
        assert all_versions["design"]["design.json"] == 1

    def test_markdown_generated(self, bus, run_id):
        prd = PRDArtifact(
            run_id=run_id,
            title="MD Test",
            problem_statement="A problem",
            functional_requirements=[
                {"id": "FR-001", "description": "Do something", "priority": "high"}
            ],
        )
        bus.write("requirements", prd, run_id)

        md_path = bus.artifacts_dir(run_id) / "requirements" / "v1" / "requirements.md"
        assert md_path.exists()

        content = md_path.read_text()
        assert "MD Test" in content
        assert "FR-001" in content
