from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


class Manifest:
    """Manages the manifest.json file that tracks artifact versions per stage."""

    def __init__(self, path: Path):
        self.path = path
        self._data: dict[str, dict[str, int]] = {}
        if path.exists():
            self._load()

    def _load(self) -> None:
        with open(self.path) as f:
            self._data = json.load(f)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(self._data, f, indent=2)

    def get_version(self, stage: str, artifact_type: str) -> int:
        """Get the current version for a given stage and artifact type."""
        return self._data.get(stage, {}).get(artifact_type, 0)

    def set_version(self, stage: str, artifact_type: str, version: int) -> None:
        """Record a new version for a stage's artifact."""
        self._data.setdefault(stage, {})[artifact_type] = version
        self._save()

    def get_stage_artifacts(self, stage: str) -> dict[str, int]:
        """Get all artifact versions for a stage."""
        return self._data.get(stage, {})

    def get_all(self) -> dict[str, dict[str, int]]:
        """Get the full manifest data."""
        return dict(self._data)
