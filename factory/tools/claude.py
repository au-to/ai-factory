from __future__ import annotations

import json
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
    output_format: str = "stream-json"
    timeout_seconds: int = 1800
    permission_mode: str = "auto"
    run_id: Optional[str] = None
    cancel_event: Optional[threading.Event] = None


def _format_stream_event(evt: dict) -> str | None:
    """Convert a stream-json event into a human-readable log line. Returns None to skip."""
    etype = evt.get("type", "")

    # System init — show model info
    if etype == "system" and evt.get("subtype") == "init":
        model = evt.get("model", "unknown")
        return f"[System] Session started | model={model}\n"

    # Skip other system messages
    if etype == "system":
        return None

    # Streaming content deltas (thinking or text)
    if etype == "stream_event":
        event = evt.get("event", {})
        delta = event.get("delta", {})
        if not delta:
            return None

        if "thinking_delta" in delta:
            return delta["thinking_delta"]
        elif "text_delta" in delta:
            return delta["text_delta"]
        elif "signature_delta" in delta:
            return None  # skip signature deltas
        elif "input_json_delta" in delta:
            return delta["input_json_delta"]

        return None

    # Tool use block
    if etype == "stream_event":
        event = evt.get("event", {})
        if event.get("type") == "content_block_start":
            cb = event.get("content_block", {})
            if cb.get("type") == "tool_use":
                name = cb.get("name", "?")
                inp = cb.get("input", {})
                # Summarize tool input
                if name in ("Bash",):
                    cmd = inp.get("command", "")
                    return f"\n[Tool] {name}: {cmd[:120]}\n"
                elif name == "Write":
                    fp = inp.get("file_path", "?")
                    return f"\n[Tool] Write: {fp}\n"
                elif name == "Edit":
                    fp = inp.get("file_path", "?")
                    return f"\n[Tool] Edit: {fp}\n"
                elif name == "Read":
                    fp = inp.get("file_path", "?")
                    return f"\n[Tool] Read: {fp}\n"
                else:
                    return f"\n[Tool] {name}\n"
        return None

    # Tool result (user message type)
    if etype == "user":
        message = evt.get("message", {})
        content = message.get("content", [])
        for block in content:
            if block.get("type") == "tool_result":
                out = block.get("content", "")
                if isinstance(out, list):
                    out = " ".join(str(c.get("text", "")) for c in out)
                truncated = out[:200] + "..." if len(out) > 200 else out
                return f"[Tool Result] {truncated}\n"
        return None

    # Assistant message — extract text content
    if etype == "assistant":
        message = evt.get("message", {})
        content = message.get("content", [])
        parts = []
        for block in content:
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                name = block.get("name", "?")
                inp = json.dumps(block.get("input", {}), ensure_ascii=False)
                parts.append(f"\n[Tool] {name}: {inp[:200]}\n")
        return "".join(parts) if parts else None

    # Result event — contains final text
    if etype == "result":
        result_text = evt.get("result", "")
        return result_text

    return None


def invoke_claude(inv: ClaudeInvocation, log_file: Optional[Path] = None,
                  active_procs: Optional[dict] = None) -> ClaudeResult:
    """Invoke Claude Code CLI in -p mode with stream-json for real-time log output.

    Uses PTY for line-buffered I/O. Parses JSONL stream events and writes
    human-readable log lines to log_file as they arrive.
    """
    cmd = ["claude", "-p", inv.prompt]

    cmd.extend(["--output-format", inv.output_format])
    cmd.extend(["--include-partial-messages"])
    cmd.extend(["--verbose"])

    if inv.system_prompt:
        cmd.extend(["--system-prompt", inv.system_prompt])

    if inv.allowed_tools:
        cmd.extend(["--allowedTools", ",".join(inv.allowed_tools)])

    if inv.permission_mode:
        cmd.extend(["--permission-mode", inv.permission_mode])

    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.write_text("")

    # Open PTY for line-buffered output
    try:
        master_fd, slave_fd = pty.openpty()
    except OSError:
        return _invoke_with_pipe_legacy(cmd, inv, log_file, active_procs)

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
        return ClaudeResult(success=False, stderr="Claude Code CLI not found. Is it installed?", exit_code=-1)

    os.close(slave_fd)

    if active_procs is not None and inv.run_id:
        active_procs[inv.run_id] = proc

    all_text: list[str] = []
    result_text = ""
    cancelled = False

    def read_output():
        nonlocal cancelled, result_text
        buf = ""
        while True:
            if inv.cancel_event and inv.cancel_event.is_set():
                cancelled = True
                break
            try:
                data = os.read(master_fd, 4096)
                if not data:
                    break
                text = data.decode("utf-8", errors="replace")
                buf += text

                # Process complete lines
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue

                    # Write raw JSON line to log for debugging
                    log_line = None
                    try:
                        evt = json.loads(line)
                        log_line = _format_stream_event(evt)
                        if evt.get("type") == "result":
                            result_text = evt.get("result", "")
                    except json.JSONDecodeError:
                        log_line = None

                    if log_line:
                        all_text.append(log_line)
                        if log_file:
                            try:
                                with open(log_file, "a") as f:
                                    f.write(log_line)
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

        msg = "\n[Cancelled]\n" if cancelled else f"\n[Timeout after {inv.timeout_seconds}s]\n"
        if log_file:
            try:
                with open(log_file, "a") as f:
                    f.write(msg)
            except Exception:
                pass

        if active_procs and inv.run_id:
            active_procs.pop(inv.run_id, None)

        return ClaudeResult(
            success=False,
            stdout=result_text or "".join(all_text),
            stderr="Cancelled" if cancelled else f"Timeout after {inv.timeout_seconds}s",
            exit_code=-1,
        )

    os.close(master_fd)
    proc.wait()

    if active_procs and inv.run_id:
        active_procs.pop(inv.run_id, None)

    # Use the result text if available, otherwise use accumulated text
    final_output = result_text if result_text else "".join(all_text)
    return ClaudeResult(
        success=proc.returncode == 0,
        stdout=final_output,
        stderr="",
        exit_code=proc.returncode,
    )


def _invoke_with_pipe_legacy(cmd, inv, log_file, active_procs):
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
        return ClaudeResult(success=False, stderr="Claude Code CLI not found. Is it installed?", exit_code=-1)

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
            success=False, stdout=stdout,
            stderr="Cancelled" if cancelled else f"Timeout after {inv.timeout_seconds}s",
            exit_code=-1,
        )

    proc.wait()
    stdout = "".join(stdout_lines)
    stderr = proc.stderr.read()

    if active_procs and inv.run_id:
        active_procs.pop(inv.run_id, None)

    return ClaudeResult(success=proc.returncode == 0, stdout=stdout, stderr=stderr, exit_code=proc.returncode)


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
