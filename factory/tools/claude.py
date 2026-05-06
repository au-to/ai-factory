from __future__ import annotations

import os
import pty
import subprocess
import threading
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
    run_id: Optional[str] = None
    cancel_event: Optional[threading.Event] = None


def invoke_claude(inv: ClaudeInvocation, log_file: Optional[Path] = None,
                  active_procs: Optional[dict] = None) -> ClaudeResult:
    """Invoke Claude Code CLI in -p (print) mode.

    Uses a PTY (pseudo-terminal) so Claude produces line-buffered output,
    allowing real-time log streaming while the process runs.

    If active_procs dict and inv.run_id are provided, the subprocess reference
    is stored so it can be killed externally via cancel().
    """
    cmd = ["claude", "-p", inv.prompt]

    cmd.extend(["--output-format", inv.output_format])

    if inv.system_prompt:
        cmd.extend(["--system-prompt", inv.system_prompt])

    if inv.allowed_tools:
        cmd.extend(["--allowedTools", ",".join(inv.allowed_tools)])

    if inv.permission_mode:
        cmd.extend(["--permission-mode", inv.permission_mode])

    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.write_text("")

    try:
        master_fd, slave_fd = pty.openpty()
    except OSError:
        return _invoke_with_pipe(cmd, inv, log_file, active_procs)

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=slave_fd,
            stderr=slave_fd,
            text=True,
            cwd=str(inv.working_dir) if inv.working_dir else None,
            close_fds=True,
        )
    except FileNotFoundError:
        os.close(master_fd)
        os.close(slave_fd)
        return ClaudeResult(
            success=False,
            stderr="Claude Code CLI not found. Is it installed?",
            exit_code=-1,
        )

    os.close(slave_fd)

    # Register process for external cancellation
    if active_procs is not None and inv.run_id:
        active_procs[inv.run_id] = proc

    stdout_lines: list[str] = []
    cancelled = False

    def read_output():
        nonlocal cancelled
        while True:
            if inv.cancel_event and inv.cancel_event.is_set():
                cancelled = True
                break
            try:
                data = os.read(master_fd, 4096)
                if not data:
                    break
                text = data.decode("utf-8", errors="replace")
                stdout_lines.append(text)
                if log_file:
                    try:
                        with open(log_file, "a") as f:
                            f.write(text)
                    except Exception:
                        pass
            except OSError:
                break

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()

    try:
        reader.join(timeout=inv.timeout_seconds)
    except Exception:
        pass

    if reader.is_alive() or cancelled:
        try:
            proc.kill()
        except Exception:
            pass
        os.close(master_fd)
        reader.join(timeout=5)
        stdout = "".join(stdout_lines)
        if log_file:
            try:
                with open(log_file, "a") as f:
                    f.write(f"\n[{'Cancelled' if cancelled else 'Timeout after ' + str(inv.timeout_seconds) + 's'}]\n")
            except Exception:
                pass
        if active_procs and inv.run_id:
            active_procs.pop(inv.run_id, None)
        return ClaudeResult(
            success=False,
            stdout=stdout,
            stderr="Cancelled" if cancelled else f"Timeout after {inv.timeout_seconds}s",
            exit_code=-1,
        )

    os.close(master_fd)
    proc.wait()

    if active_procs and inv.run_id:
        active_procs.pop(inv.run_id, None)

    stdout = "".join(stdout_lines)
    return ClaudeResult(
        success=proc.returncode == 0,
        stdout=stdout,
        stderr="",
        exit_code=proc.returncode,
    )


def _invoke_with_pipe(cmd, inv, log_file, active_procs):
    """Fallback: use PIPE when PTY is unavailable."""
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(inv.working_dir) if inv.working_dir else None,
        )
    except FileNotFoundError:
        return ClaudeResult(
            success=False,
            stderr="Claude Code CLI not found. Is it installed?",
            exit_code=-1,
        )

    if active_procs is not None and inv.run_id:
        active_procs[inv.run_id] = proc

    stdout_lines: list[str] = []
    cancelled = False

    def read_stdout():
        nonlocal cancelled
        for line in proc.stdout:
            if inv.cancel_event and inv.cancel_event.is_set():
                cancelled = True
                break
            stdout_lines.append(line)
            if log_file:
                try:
                    with open(log_file, "a") as f:
                        f.write(line)
                except Exception:
                    pass

    reader = threading.Thread(target=read_stdout, daemon=True)
    reader.start()

    try:
        reader.join(timeout=inv.timeout_seconds)
    except Exception:
        pass

    if reader.is_alive() or cancelled:
        try:
            proc.kill()
        except Exception:
            pass
        reader.join(timeout=5)
        stdout = "".join(stdout_lines)
        if active_procs and inv.run_id:
            active_procs.pop(inv.run_id, None)
        return ClaudeResult(
            success=False,
            stdout=stdout,
            stderr="Cancelled" if cancelled else f"Timeout after {inv.timeout_seconds}s",
            exit_code=-1,
        )

    proc.wait()
    stdout = "".join(stdout_lines)
    stderr = proc.stderr.read()

    if active_procs and inv.run_id:
        active_procs.pop(inv.run_id, None)

    return ClaudeResult(
        success=proc.returncode == 0,
        stdout=stdout,
        stderr=stderr,
        exit_code=proc.returncode,
    )


class ClaudeRunner:
    """High-level interface for running Claude Code with factory-managed output files."""

    def __init__(self, workspace_path: str | Path):
        self.workspace = Path(workspace_path)
        self.sessions_dir = self.workspace / "sessions"
        self._active_procs: dict[str, subprocess.Popen] = {}

    def run_stage(self, run_id: str, stage: str, prompt: str,
                  system_prompt: Optional[str] = None,
                  working_dir: Optional[str | Path] = None,
                  allowed_tools: Optional[list[str]] = None,
                  cancel_event: Optional[threading.Event] = None) -> ClaudeResult:
        """Run a pipeline stage. Writes output to sessions/<run_id>/<stage>-output.txt
        in real-time so the log is readable while the stage executes."""
        log_file = self.sessions_dir / run_id / f"{stage}-output.txt"

        inv = ClaudeInvocation(
            prompt=prompt,
            system_prompt=system_prompt,
            working_dir=working_dir,
            allowed_tools=allowed_tools or ["Read", "Write", "Glob", "Grep", "Bash"],
            run_id=run_id,
            cancel_event=cancel_event,
        )

        return invoke_claude(inv, log_file=log_file, active_procs=self._active_procs)

    def cancel(self, run_id: str) -> bool:
        """Kill the running Claude subprocess for the given run_id."""
        proc = self._active_procs.pop(run_id, None)
        if proc:
            try:
                proc.kill()
            except Exception:
                pass
            return True
        return False

    def is_running(self, run_id: str) -> bool:
        """Check if a Claude subprocess is active for the given run_id."""
        proc = self._active_procs.get(run_id)
        return proc is not None and proc.poll() is None
