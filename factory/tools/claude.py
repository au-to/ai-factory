from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class ClaudeResult:
    """Result from a Claude Code invocation."""
    success: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1


@dataclass
class ClaudeInvocation:
    """Parameters for a Claude Code CLI invocation in -p (print) mode."""
    prompt: str
    system_prompt: Optional[str] = None
    working_dir: Optional[str | Path] = None
    allowed_tools: list[str] = field(default_factory=lambda: ["Read", "Write", "Glob", "Grep"])
    output_format: str = "text"
    timeout_seconds: int = 1800
    permission_mode: str = "auto"


def invoke_claude(inv: ClaudeInvocation) -> ClaudeResult:
    """Invoke Claude Code CLI in -p (print) mode.

    Captures stdout/stderr. Use --permission-mode auto to skip prompts.
    JSON artifacts are extracted from markdown code blocks in the text output.
    """
    cmd = ["claude", "-p", inv.prompt]

    cmd.extend(["--output-format", inv.output_format])

    if inv.system_prompt:
        cmd.extend(["--system-prompt", inv.system_prompt])

    if inv.allowed_tools:
        cmd.extend(["--allowedTools", ",".join(inv.allowed_tools)])

    if inv.permission_mode:
        cmd.extend(["--permission-mode", inv.permission_mode])

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=inv.timeout_seconds,
            cwd=str(inv.working_dir) if inv.working_dir else None,
        )
        return ClaudeResult(
            success=result.returncode == 0,
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.returncode,
        )
    except subprocess.TimeoutExpired as e:
        return ClaudeResult(
            success=False,
            stdout=e.stdout.decode("utf-8", errors="replace") if e.stdout else "",
            stderr=f"Timeout after {inv.timeout_seconds}s\n" + (e.stderr.decode("utf-8", errors="replace") if e.stderr else ""),
            exit_code=-1,
        )
    except FileNotFoundError:
        return ClaudeResult(
            success=False,
            stderr="Claude Code CLI not found. Is it installed?",
            exit_code=-1,
        )


class ClaudeRunner:
    """High-level interface for running Claude Code with factory-managed output files."""

    def __init__(self, workspace_path: str | Path):
        self.workspace = Path(workspace_path)
        self.sessions_dir = self.workspace / "sessions"

    def run_stage(self, run_id: str, stage: str, prompt: str,
                  system_prompt: Optional[str] = None,
                  working_dir: Optional[str | Path] = None,
                  allowed_tools: Optional[list[str]] = None) -> ClaudeResult:
        """Run a pipeline stage. Saves output to sessions/<run_id>/<stage>-output.txt."""
        inv = ClaudeInvocation(
            prompt=prompt,
            system_prompt=system_prompt,
            working_dir=working_dir,
            allowed_tools=allowed_tools or ["Read", "Write", "Glob", "Grep", "Bash"],
        )

        result = invoke_claude(inv)

        # Save output to file for debugging
        output_dir = self.sessions_dir / run_id
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / f"{stage}-output.txt"
        output_file.write_text(result.stdout if result.success else result.stderr)

        return result
