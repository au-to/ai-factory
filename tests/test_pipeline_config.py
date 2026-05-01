"""Tests for pipeline configuration loading."""

import tempfile
from pathlib import Path

import pytest
import yaml

from factory.pipeline.config import PipelineConfig, load_pipeline_config, get_stage_map


@pytest.fixture
def valid_pipeline_yaml():
    return {
        "name": "test-pipeline",
        "description": "A test pipeline",
        "version": "1.0",
        "stages": [
            {
                "name": "requirements",
                "agent": "Requirements Analyst",
                "output_artifact": "requirements",
                "gate": True,
                "gate_label": "PRD Approval",
            },
            {
                "name": "development",
                "agent": "Developer",
                "output_artifact": "development",
                "input": ["requirements"],
            },
        ],
    }


class TestPipelineConfig:
    def test_load_from_file(self, valid_pipeline_yaml):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(valid_pipeline_yaml, f)
            path = f.name

        try:
            config = load_pipeline_config(path)
            assert config.name == "test-pipeline"
            assert len(config.stages) == 2
            assert config.stages[0].name == "requirements"
            assert config.stages[0].gate is True
            assert config.stages[1].input == ["requirements"]
        finally:
            Path(path).unlink()

    def test_get_stage_map(self, valid_pipeline_yaml):
        config = PipelineConfig.model_validate(valid_pipeline_yaml)
        stage_map = get_stage_map(config)
        assert "requirements" in stage_map
        assert "development" in stage_map
        assert stage_map["requirements"].agent == "Requirements Analyst"
