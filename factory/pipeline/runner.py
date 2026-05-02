from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from factory.agents.prompt import PromptBuilder
from factory.agents.registry import AgentRegistry
from factory.bus.schemas import STAGE_ARTIFACT_MAP, StageStatus
from factory.bus.store import ContextBus
from factory.pipeline.config import StageConfig
from factory.pipeline.state import PipelineState
from factory.tools.claude import ClaudeResult, ClaudeRunner


class StageRunner:
    """Executes a single pipeline stage: render prompt -> invoke Claude -> parse output."""

    def __init__(self, registry: AgentRegistry, builder: PromptBuilder,
                 runner: ClaudeRunner, bus: ContextBus, state: PipelineState):
        self.registry = registry
        self.builder = builder
        self.runner = runner
        self.bus = bus
        self.state = state
    def execute(self, stage: StageConfig, run_id: str,
                extra_context: Optional[dict] = None) -> ClaudeResult:
        """Execute a single pipeline stage. Returns ClaudeResult."""
        agent = self.registry.get(stage.agent)
        if not agent:
            raise ValueError(f"Agent not found: {stage.agent}")

        self.state.set_stage_status(run_id, stage.name, StageStatus.IN_PROGRESS)

        # Build prompt from upstream artifacts
        prompt = self.builder.build_stage_prompt(
            f"{stage.name}.j2",
            run_id=run_id,
            bus=self.bus,
            stage_inputs=stage.input,
            extra_context=extra_context,
        )

        # Determine working directory
        working_dir = None
        if stage.working_dir:
            working_dir = self.bus.workspace / stage.working_dir
            working_dir.mkdir(parents=True, exist_ok=True)

        # Execute
        result = self.runner.run_stage(
            run_id=run_id,
            stage=stage.name,
            prompt=prompt,
            system_prompt=agent.system_prompt if agent.system_prompt else None,
            working_dir=working_dir,
            allowed_tools=stage.allowed_tools or agent.allowed_tools,
        )

        if result.success:
            self._extract_and_store_artifact(stage, run_id, result)
            self.state.set_stage_status(run_id, stage.name, StageStatus.COMPLETED)
        else:
            self.state.set_stage_status(
                run_id, stage.name, StageStatus.FAILED,
                error=result.stderr[:1000] if result.stderr else "Unknown error",
            )

        return result

    def _extract_and_store_artifact(self, stage: StageConfig, run_id: str,
                                     result: ClaudeResult) -> Optional[int]:
        """Extract structured JSON from Claude's output and store as artifact.

        Tries in order:
        1. Parse JSON from stdout (markdown code blocks or raw JSON)
        2. Look for JSON files Claude wrote to the artifacts directory
        """
        output_data = self._parse_output(result.stdout)

        # Fallback: read from JSON files Claude wrote directly
        if not output_data:
            output_data = self._read_claude_json_files(stage, run_id)

        if output_data:
            try:
                return self.bus.write_raw(stage.output_artifact, output_data, run_id)
            except Exception:
                return None
        return None

    def _read_claude_json_files(self, stage: StageConfig, run_id: str) -> Optional[dict]:
        """Look for JSON files that Claude wrote directly to the artifacts directory."""
        import json as _json
        artifacts_dir = self.bus.artifacts_dir(run_id)
        if not artifacts_dir.exists():
            return None
        for candidate in artifacts_dir.glob("*.json"):
            if candidate.name == "manifest.json":
                continue
            try:
                with open(candidate) as f:
                    return _json.load(f)
            except Exception:
                pass
        return None

    @staticmethod
    def _parse_output(text: str) -> Optional[dict]:
        """Parse Claude Code JSON output.

        With --output-format json, stdout is a JSON envelope:
        {"type":"result","structured_output":{...},"result":"..."}
        We extract structured_output first, then try to find JSON in the result text.
        """
        # Strategy 1: parse the JSON envelope
        try:
            envelope = json.loads(text.strip())
            if isinstance(envelope, dict):
                # Best case: structured_output is present
                if "structured_output" in envelope and envelope["structured_output"]:
                    return envelope["structured_output"]
                # result is a string — search it for JSON code blocks
                if "result" in envelope and isinstance(envelope["result"], str):
                    parsed = StageRunner._extract_json(envelope["result"])
                    if parsed:
                        return parsed
        except json.JSONDecodeError:
            pass

        # Strategy 2: direct artifact JSON (for text format without envelope)
        parsed = StageRunner._extract_json(text)
        if parsed:
            return parsed

        return None

    @staticmethod
    def _extract_json(text: str) -> Optional[dict]:
        """Extract a JSON object from text, trying multiple strategies."""
        # JSON code block
        m = re.search(r'```json\s*([\s\S]*?)```', text)
        if m:
            result = StageRunner._try_parse_json(m.group(1))
            if result:
                return result

        # Raw JSON object (greedy match for the outermost object)
        m = re.search(r'\{[\s\S]*\}', text)
        if m:
            result = StageRunner._try_parse_json(m.group(0))
            if result:
                return result

        return None

    @staticmethod
    def _try_parse_json(text: str) -> Optional[dict]:
        """Try to parse JSON text, applying repairs for common LLM formatting errors."""
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Repair: escape unescaped control characters inside JSON strings
        repaired = StageRunner._sanitize_json_strings(text)
        if repaired != text:
            try:
                return json.loads(repaired)
            except json.JSONDecodeError:
                pass

        return None

    @staticmethod
    def _sanitize_json_strings(text: str) -> str:
        """Escape literal newlines, tabs, and carriage returns inside JSON strings."""
        result = []
        in_string = False
        escape = False
        for ch in text:
            if escape:
                escape = False
                result.append(ch)
            elif ch == '\\':
                escape = True
                result.append(ch)
            elif ch == '"':
                in_string = not in_string
                result.append(ch)
            elif ch == '\n' and in_string:
                result.append('\\n')
            elif ch == '\r' and in_string:
                result.append('\\r')
            elif ch == '\t' and in_string:
                result.append('\\t')
            else:
                result.append(ch)
        return ''.join(result)
