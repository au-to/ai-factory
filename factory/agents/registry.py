from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel


class AgentConfig(BaseModel):
    """Configuration for an AI agent in the factory."""
    name: str
    description: str = ""
    model: str = "claude-sonnet-4-20250514"
    temperature: float = 0.3
    max_turns: int = 30
    system_prompt: str = ""
    allowed_tools: list[str] = ["Read", "Write", "Glob", "Grep", "Bash"]
    output_schema: Optional[str] = None  # JSON Schema file path (relative to project root)
    output_files: list[str] = []  # Jinja2-templated output paths


class AgentRegistry:
    """Loads and manages agent configurations from YAML files."""

    def __init__(self, config_dir: str | Path):
        self.config_dir = Path(config_dir)
        self._agents: dict[str, AgentConfig] = {}
        self._load_all()

    def _load_all(self) -> None:
        if not self.config_dir.exists():
            return
        for yaml_file in self.config_dir.glob("*.yaml"):
            self._load_file(yaml_file)
        for yaml_file in self.config_dir.glob("*.yml"):
            self._load_file(yaml_file)

    def _load_file(self, path: Path) -> None:
        with open(path) as f:
            data = yaml.safe_load(f)
        if data:
            agent = AgentConfig.model_validate(data)
            self._agents[agent.name] = agent

    def get(self, name: str) -> Optional[AgentConfig]:
        return self._agents.get(name)

    def list(self) -> list[str]:
        return list(self._agents.keys())

    def __contains__(self, name: str) -> bool:
        return name in self._agents
