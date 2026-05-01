from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from factory.bus.manifest import Manifest
from factory.bus.schemas import STAGE_ARTIFACT_MAP, Artifact


class ContextBus:
    """Versioned, directory-based artifact store.

    Layout:
        workspace/
          artifacts/
            <run-id>/
              manifest.json
              prd/v1/prd.json
              prd/v1/prd.md
              tech-spec/v1/tech-spec.json
              ...
    """

    def __init__(self, workspace_path: str | Path):
        self.workspace = Path(workspace_path)

    def artifacts_dir(self, run_id: str) -> Path:
        return self.workspace / "artifacts" / run_id

    def manifest(self, run_id: str) -> Manifest:
        return Manifest(self.artifacts_dir(run_id) / "manifest.json")

    def read(self, stage: str, run_id: str, version: Optional[int] = None) -> Artifact | None:
        """Read the latest (or specific) version of a stage's artifact."""
        artifact_dir = self.artifacts_dir(run_id) / stage
        if not artifact_dir.exists():
            return None

        manifest = self.manifest(run_id)
        if version is None:
            version = manifest.get_version(stage, f"{stage}.json")
            if version == 0:
                return None

        json_path = artifact_dir / f"v{version}" / f"{stage}.json"
        if not json_path.exists():
            return None

        with open(json_path) as f:
            data = json.load(f)

        artifact_cls = STAGE_ARTIFACT_MAP.get(stage)
        if artifact_cls is None:
            raise ValueError(f"Unknown stage: {stage}")

        return artifact_cls.model_validate(data)

    def read_latest(self, run_id: str) -> dict[str, Artifact | None]:
        """Read the latest version of all stage artifacts for a run."""
        results: dict[str, Artifact | None] = {}
        for stage in STAGE_ARTIFACT_MAP:
            results[stage] = self.read(stage, run_id)
        return results

    def write(self, stage: str, artifact: Artifact, run_id: str) -> int:
        """Write an artifact, incrementing its version. Returns the new version."""
        manifest = self.manifest(run_id)
        current_version = manifest.get_version(stage, f"{stage}.json")
        new_version = current_version + 1

        artifact.version = new_version
        artifact.run_id = run_id

        json_path = self._build_path(run_id, stage, new_version, f"{stage}.json")
        json_path.parent.mkdir(parents=True, exist_ok=True)
        with open(json_path, "w") as f:
            f.write(artifact.model_dump_json(indent=2))

        # Also write a human-readable markdown if the artifact has enough structure
        md_path = self._build_path(run_id, stage, new_version, f"{stage}.md")
        md_content = self._to_markdown(stage, artifact)
        with open(md_path, "w") as f:
            f.write(md_content)

        manifest.set_version(stage, f"{stage}.json", new_version)
        return new_version

    def write_raw(self, stage: str, artifact: dict, run_id: str) -> int:
        """Write a raw dict artifact, validated against the stage schema. Returns version."""
        artifact_cls = STAGE_ARTIFACT_MAP.get(stage)
        if artifact_cls is None:
            raise ValueError(f"Unknown stage: {stage}")

        # Inject required fields that Claude might not include
        artifact.setdefault("run_id", run_id)
        artifact.setdefault("stage", stage)
        artifact.setdefault("version", 1)

        validated = artifact_cls.model_validate(artifact)
        return self.write(stage, validated, run_id)

    def _build_path(self, run_id: str, stage: str, version: int, filename: str) -> Path:
        return self.artifacts_dir(run_id) / stage / f"v{version}" / filename

    def _to_markdown(self, stage: str, artifact: Artifact) -> str:
        """Convert an artifact to a human-readable markdown summary."""
        data = artifact.model_dump()

        lines = [f"# {stage.replace('-', ' ').title()}", ""]
        lines.append(f"> Run: {data.get('run_id', 'N/A')} | Version: {data.get('version', 'N/A')}")
        lines.append("")

        if stage == "requirements":
            lines.append(f"**Title**: {data.get('title', 'N/A')}\n")
            lines.append(f"## Problem Statement\n\n{data.get('problem_statement', 'N/A')}\n")
            lines.append("## Functional Requirements")
            for req in data.get("functional_requirements", []):
                lines.append(f"- [{req['priority']}] **{req['id']}**: {req['description']}")
            lines.append("")
            lines.append("## Non-Functional Requirements")
            for nfr in data.get("non_functional_requirements", []):
                lines.append(f"- **{nfr['type']}**: {nfr['description']} (threshold: {nfr.get('threshold', 'N/A')})")
            lines.append("")
            lines.append("## Constraints")
            for c in data.get("constraints", []):
                lines.append(f"- {c}")
            lines.append("")

        elif stage == "design":
            lines.append(f"## Overview\n\n{data.get('overview', 'N/A')}\n")
            lines.append("## Components")
            for comp in data.get("components", []):
                lines.append(f"### {comp['name']}\n{comp['description']}")
                if comp.get("dependencies"):
                    lines.append(f"Depends on: {', '.join(comp['dependencies'])}")
                lines.append("")
            lines.append("## Tech Stack")
            for k, v in data.get("tech_stack", {}).items():
                lines.append(f"- **{k}**: {v}")
            lines.append("")

        elif stage == "development":
            lines.append(f"## Summary\n\n{data.get('summary', 'N/A')}\n")
            lines.append("## Files Created")
            for f in data.get("files_created", []):
                lines.append(f"- `{f['path']}` - {f['purpose']}")
            lines.append("")

        elif stage == "testing":
            lines.append(f"## Summary\n\n{data.get('summary', 'N/A')}\n")
            lines.append(f"**Results**: {data.get('passed', 0)} passed, {data.get('failed', 0)} failed, {data.get('skipped', 0)} skipped (total: {data.get('total_tests', 0)})")
            if data.get("coverage_percent") is not None:
                lines.append(f"**Coverage**: {data['coverage_percent']}%")
            lines.append("")

        elif stage == "deployment":
            lines.append(f"## Summary\n\n{data.get('summary', 'N/A')}\n")
            lines.append("## Services")
            for svc in data.get("services", []):
                lines.append(f"- **{svc['name']}**: ports {svc.get('ports', [])}")
            lines.append("")

        return "\n".join(lines)
