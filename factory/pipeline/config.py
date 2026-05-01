from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field


class StageConfig(BaseModel):
    """Configuration for a single pipeline stage."""
    name: str
    description: str = ""
    agent: str  # agent name to use
    input: list[str] = Field(default_factory=list)  # upstream stage names whose artifacts to read
    output_artifact: str  # the artifact type this stage produces
    gate: bool = False  # whether to pause for human approval after completion
    gate_label: str = ""  # human-readable label for the gate
    max_turns: int = 30
    allowed_tools: list[str] = Field(default_factory=lambda: ["Read", "Write", "Glob", "Grep", "Bash"])
    working_dir: Optional[str] = None  # relative to workspace, for dev stage
    retry_limit: int = 2  # max retries on failure before giving up


class PipelineConfig(BaseModel):
    """Full pipeline configuration loaded from YAML."""
    name: str
    description: str = ""
    version: str = "1.0"
    stages: list[StageConfig]
    pre_run_checks: list[str] = Field(default_factory=list)


def load_pipeline_config(path: str | Path) -> PipelineConfig:
    """Load and validate a pipeline configuration from YAML."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Pipeline config not found: {path}")

    with open(path) as f:
        data = yaml.safe_load(f)

    return PipelineConfig.model_validate(data)


def get_stage_map(config: PipelineConfig) -> dict[str, StageConfig]:
    """Return a map of stage name to stage config."""
    return {stage.name: stage for stage in config.stages}
