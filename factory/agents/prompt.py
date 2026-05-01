from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from jinja2 import Environment, FileSystemLoader, Template

from factory.bus.store import ContextBus


class PromptBuilder:
    """Renders Jinja2 prompt templates with context from the ContextBus."""

    def __init__(self, templates_dir: str | Path):
        self.templates_dir = Path(templates_dir)
        self.env = Environment(
            loader=FileSystemLoader(str(self.templates_dir)),
            autoescape=False,
        )
        # Custom filters
        self.env.filters["json"] = lambda v: str(v)

    def render(self, template_name: str, context: dict[str, Any]) -> str:
        tmpl = self.env.get_template(template_name)
        return tmpl.render(**context)

    def build_stage_prompt(self, template_name: str, run_id: str,
                           bus: ContextBus,
                           stage_inputs: list[str],
                           extra_context: Optional[dict[str, Any]] = None) -> str:
        """Build a prompt for a pipeline stage by collecting upstream artifacts as context."""
        ctx: dict[str, Any] = {"run_id": run_id}

        for upstream_stage in stage_inputs:
            artifact = bus.read(upstream_stage, run_id)
            if artifact:
                ctx[upstream_stage] = artifact.model_dump()
                ctx[f"{upstream_stage}_markdown"] = bus.read(upstream_stage, run_id)
                # Also provide human-readable path
                json_path = bus.artifacts_dir(run_id) / upstream_stage
                ctx[f"{upstream_stage}_path"] = str(json_path)

        if extra_context:
            ctx.update(extra_context)

        return self.render(template_name, ctx)
