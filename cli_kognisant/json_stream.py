"""
JSON Stream Protocol — Machine-readable output mode for Kognisant CLI.

When --json-stream is active, all output goes through this module as structured
JSON events (one per line to stdout). Stdin is read by a background thread for
commands from the frontend.

Protocol version: 1.0
"""

import json
import queue
import sys
import threading
import time
from datetime import datetime, timezone

# ─── Module State ──────────────────────────────────────────────────────────────

_json_stream_active: bool = False
_stdin_reader: "StdinReader | None" = None
_heartbeat_emitter: "HeartbeatEmitter | None" = None
_session_id: str | None = None
_protocol_version: str = "1.0"
_frontend_connected: bool = False


def is_active() -> bool:
    """Check if JSON stream mode is active."""
    return _json_stream_active


def activate(session_id: str | None = None) -> None:
    """Activate JSON stream mode. Call once at startup when --json-stream flag is set."""
    global _json_stream_active, _stdin_reader, _session_id
    _json_stream_active = True
    _session_id = session_id or f"sess_{int(time.time() * 1000)}"
    _stdin_reader = StdinReader()
    _stdin_reader.start()


def shutdown() -> None:
    """Clean shutdown of JSON stream mode."""
    global _json_stream_active, _stdin_reader, _heartbeat_emitter
    if _heartbeat_emitter:
        _heartbeat_emitter.stop()
        _heartbeat_emitter = None
    if _stdin_reader:
        _stdin_reader.stop()
        _stdin_reader = None
    _json_stream_active = False


# ─── Output (CLI → Frontend) ──────────────────────────────────────────────────

def emit(event: dict) -> None:
    """Emit a single JSON event to stdout.

    Every event gets a timestamp if not already present.
    No-op if json_stream mode is not active.
    """
    if not _json_stream_active:
        return
    if "ts" not in event:
        event["ts"] = datetime.now(timezone.utc).isoformat()
    try:
        line = json.dumps(event, ensure_ascii=False, default=str)
        sys.stdout.write(line + "\n")
        sys.stdout.flush()
    except (IOError, OSError):
        pass  # Pipe broken — frontend disconnected


def emit_session_start(
    cli_version: str,
    project: str | None,
    model: str | None,
    provider: str | None,
    valence: int = 0,
    capabilities: list[str] | None = None,
) -> None:
    """Emit the initial session_start event."""
    emit({
        "type": "session_start",
        "protocol_version": _protocol_version,
        "cli_version": cli_version,
        "session_id": _session_id,
        "capabilities": capabilities or ["chat", "tools", "agents", "channels", "studio"],
        "project": project,
        "model": model,
        "provider": provider,
        "valence": valence,
    })


def emit_session_end(reason: str = "user_exit") -> None:
    """Emit session_end event."""
    emit({"type": "session_end", "reason": reason})


def emit_hello_ack(heartbeat_interval_ms: int = 5000, prompt_timeout_ms: int = 300000) -> None:
    """Emit hello_ack after receiving hello from frontend."""
    global _frontend_connected, _heartbeat_emitter
    _frontend_connected = True
    emit({
        "type": "hello_ack",
        "session_id": _session_id,
        "heartbeat_interval_ms": heartbeat_interval_ms,
        "prompt_timeout_ms": prompt_timeout_ms,
    })
    # Start heartbeat after handshake
    _heartbeat_emitter = HeartbeatEmitter(interval_ms=heartbeat_interval_ms)
    _heartbeat_emitter.start()


def emit_user_message(content: str) -> None:
    emit({"type": "user_message", "content": content})


def emit_classification(level: str, tokens_in_estimate: int) -> None:
    emit({"type": "classification", "level": level, "tokens_in_estimate": tokens_in_estimate})


def emit_thinking_start() -> None:
    emit({"type": "thinking_start"})


def emit_thinking_delta(content: str) -> None:
    emit({"type": "thinking_delta", "content": content})


def emit_thinking_end(duration_ms: float) -> None:
    emit({"type": "thinking_end", "duration_ms": round(duration_ms)})


def emit_content_start() -> None:
    emit({"type": "content_start"})


def emit_content_delta(content: str) -> None:
    emit({"type": "content_delta", "content": content})


def emit_content_end(duration_ms: float, tokens_out: int) -> None:
    emit({"type": "content_end", "duration_ms": round(duration_ms), "tokens_out": tokens_out})


def emit_tool_start(tool_call_id: str, tool: str, args: dict) -> None:
    emit({"type": "tool_start", "tool_call_id": tool_call_id, "tool": tool, "args": args})


def emit_tool_progress(tool_call_id: str, status: str, detail: str = "") -> None:
    emit({"type": "tool_progress", "tool_call_id": tool_call_id, "status": status, "detail": detail})


def emit_tool_result(tool_call_id: str, success: bool, summary: str, duration_ms: float) -> None:
    emit({"type": "tool_result", "tool_call_id": tool_call_id, "success": success,
          "summary": summary, "duration_ms": round(duration_ms)})


def emit_file_created(path: str, size: int) -> None:
    emit({"type": "file_created", "path": path, "size": size})


def emit_file_modified(path: str, edits_applied: int) -> None:
    emit({"type": "file_modified", "path": path, "edits_applied": edits_applied})


def emit_file_deleted(path: str) -> None:
    emit({"type": "file_deleted", "path": path})


def emit_asset_created(
    asset_type: str, path: str, size: int, mime: str,
    width: int | None = None, height: int | None = None,
    duration_seconds: float | None = None,
) -> None:
    event = {"type": "asset_created", "asset_type": asset_type, "path": path,
             "size": size, "mime": mime}
    if width:
        event["width"] = width
    if height:
        event["height"] = height
    if duration_seconds is not None:
        event["duration_seconds"] = duration_seconds
    emit(event)


def emit_swarm_start(task: str, subtasks_count: int, planner_model: str) -> None:
    emit({"type": "swarm_start", "task": task, "subtasks_count": subtasks_count,
          "planner_model": planner_model})


def emit_swarm_worker_start(worker_id: int, description: str, model: str) -> None:
    emit({"type": "swarm_worker_start", "worker_id": worker_id,
          "description": description, "model": model})


def emit_swarm_worker_complete(
    worker_id: int, success: bool, artifacts: list[str] | None = None,
    duration_ms: float = 0, tokens_in: int = 0, tokens_out: int = 0,
) -> None:
    emit({"type": "swarm_worker_complete", "worker_id": worker_id, "success": success,
          "artifacts": artifacts or [], "duration_ms": round(duration_ms),
          "tokens_in": tokens_in, "tokens_out": tokens_out})


def emit_swarm_complete(success: bool, duration_ms: float, workers_completed: int, artifacts: list[str]) -> None:
    emit({"type": "swarm_complete", "success": success, "duration_ms": round(duration_ms),
          "workers_completed": workers_completed, "artifacts": artifacts})


def emit_channel_event(channel: str, platform: str, event_type: str, sender: str, content: str) -> None:
    emit({"type": "channel_event", "channel": channel, "platform": platform,
          "event_type": event_type, "sender": sender, "content": content})


def emit_channel_response(channel: str, content: str, target_sender: str) -> None:
    emit({"type": "channel_response", "channel": channel, "content": content,
          "target_sender": target_sender})


def emit_error(code: str, message: str, recoverable: bool = False, **kwargs) -> None:
    event = {"type": "error", "code": code, "message": message, "recoverable": recoverable}
    event.update(kwargs)
    emit(event)


def emit_warning(code: str, message: str, **kwargs) -> None:
    event = {"type": "warning", "code": code, "message": message}
    event.update(kwargs)
    emit(event)


def emit_status(phase: str, **kwargs) -> None:
    event = {"type": "status", "phase": phase}
    event.update(kwargs)
    emit(event)


def emit_prompt(
    prompt_id: str,
    message: str,
    options: list[str],
    default: str = "no",
    timeout_ms: int = 300000,
    destructive: bool = False,
    **kwargs,
) -> None:
    """Emit a prompt that requires frontend response."""
    event = {
        "type": "prompt",
        "prompt_id": prompt_id,
        "message": message,
        "options": options,
        "default": default,
        "timeout_ms": timeout_ms,
        "destructive": destructive,
    }
    event.update(kwargs)
    emit(event)


def emit_prompt_timeout(prompt_id: str, default_selected: str) -> None:
    emit({"type": "prompt_timeout", "prompt_id": prompt_id, "default_selected": default_selected})


def emit_cost_estimate(prompt_id: str, message: str, estimated_usd: float) -> None:
    emit_prompt(
        prompt_id=prompt_id,
        message=message,
        options=["yes", "no"],
        default="no",
        timeout_ms=300000,
        destructive=False,
        estimated_usd=estimated_usd,
    )


def emit_degenerate_loop(message: str) -> None:
    emit({"type": "degenerate_loop", "message": message})


def emit_pong() -> None:
    emit({"type": "pong"})


# ─── Input (Frontend → CLI) ───────────────────────────────────────────────────

class StdinReader(threading.Thread):
    """Background thread that reads JSON commands from stdin without blocking the main loop.

    Handles:
    - Valid JSON → enqueued for main loop to poll
    - Malformed JSON → warning event emitted, line skipped
    - EOF → enqueue _eof sentinel, thread exits
    - IOError → thread exits silently
    """

    def __init__(self):
        super().__init__(daemon=True, name="StdinReader")
        self.command_queue: queue.Queue = queue.Queue()
        self._stop_event = threading.Event()

    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                line = sys.stdin.readline()
                if not line:
                    # EOF — frontend disconnected
                    self.command_queue.put({"type": "_eof"})
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    cmd = json.loads(line)
                    self.command_queue.put(cmd)
                except json.JSONDecodeError:
                    emit_warning("malformed_stdin", f"Invalid JSON on stdin: {line[:100]}")
            except (IOError, OSError):
                self.command_queue.put({"type": "_eof"})
                break

    def poll(self) -> dict | None:
        """Non-blocking check for pending commands."""
        try:
            return self.command_queue.get_nowait()
        except queue.Empty:
            return None

    def poll_blocking(self, timeout: float) -> dict | None:
        """Blocking wait with timeout. Used for prompt responses."""
        try:
            return self.command_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def stop(self) -> None:
        self._stop_event.set()


class HeartbeatEmitter(threading.Thread):
    """Background thread that emits periodic heartbeat events.

    Heartbeats tell the frontend the CLI is alive even during long operations
    (video generation, LLM timeout waits, etc.)
    """

    def __init__(self, interval_ms: int = 5000):
        super().__init__(daemon=True, name="HeartbeatEmitter")
        self.interval = interval_ms / 1000.0
        self.seq = 0
        self.state = "idle"
        self.operation: str | None = None
        self._stop_event = threading.Event()

    def run(self) -> None:
        while not self._stop_event.is_set():
            self._stop_event.wait(self.interval)
            if self._stop_event.is_set():
                break
            self.seq += 1
            event = {"type": "heartbeat", "seq": self.seq, "state": self.state}
            if self.operation:
                event["operation"] = self.operation
            emit(event)

    def set_state(self, state: str, operation: str | None = None) -> None:
        """Update heartbeat state (called by main loop at phase transitions)."""
        self.state = state
        self.operation = operation

    def stop(self) -> None:
        self._stop_event.set()


# ─── Command Processing ───────────────────────────────────────────────────────

def poll_command() -> dict | None:
    """Non-blocking check for stdin commands. Returns None if no command pending."""
    if not _stdin_reader:
        return None
    return _stdin_reader.poll()


def wait_for_prompt_response(prompt_id: str, timeout_ms: int, default: str) -> str:
    """Block until frontend sends a prompt_response or timeout expires.

    Args:
        prompt_id: The prompt we're waiting for.
        timeout_ms: Max wait time in milliseconds.
        default: Value to return on timeout.

    Returns:
        The user's choice string, or default on timeout.
    """
    if not _stdin_reader:
        return default

    deadline = time.time() + (timeout_ms / 1000.0)

    while time.time() < deadline:
        remaining = deadline - time.time()
        if remaining <= 0:
            break

        cmd = _stdin_reader.poll_blocking(timeout=min(remaining, 1.0))
        if cmd is None:
            continue

        cmd_type = cmd.get("type")

        # Handle the response we're waiting for
        if cmd_type == "prompt_response" and cmd.get("prompt_id") == prompt_id:
            return cmd.get("choice", default)

        # Handle prompt extension
        if cmd_type == "prompt_extend" and cmd.get("prompt_id") == prompt_id:
            additional_ms = cmd.get("additional_ms", 60000)
            deadline += additional_ms / 1000.0
            continue

        # Handle cancel (abort the prompt)
        if cmd_type == "cancel":
            return default

        # Handle ping (respond inline, don't break prompt wait)
        if cmd_type == "ping":
            emit_pong()
            continue

        # Handle EOF
        if cmd_type == "_eof":
            return default

        # Other commands queued back (they'll be processed after prompt resolves)
        # This is intentionally dropped during prompt waits to keep things simple.
        # The frontend should not send messages while a prompt is active.

    # Timeout reached
    emit_prompt_timeout(prompt_id, default)
    return default


def check_for_cancel() -> bool:
    """Quick check if a cancel command is pending. Used during streaming."""
    cmd = poll_command()
    if cmd is None:
        return False
    if cmd.get("type") == "cancel":
        return True
    if cmd.get("type") == "ping":
        emit_pong()
        return False
    if cmd.get("type") == "_eof":
        return True
    # Non-cancel command during streaming — ignore (frontend shouldn't send these)
    return False


def process_hello(cmd: dict) -> bool:
    """Process the hello handshake from frontend. Returns True if valid."""
    if cmd.get("type") != "hello":
        return False

    frontend = cmd.get("frontend", "unknown")
    frontend_version = cmd.get("frontend_version", "unknown")
    their_protocol = cmd.get("protocol_version", "")

    # Version check
    our_major = _protocol_version.split(".")[0]
    their_major = their_protocol.split(".")[0] if their_protocol else ""

    if their_major and their_major != our_major:
        emit_error("protocol_mismatch",
                   f"Protocol version mismatch: CLI={_protocol_version}, frontend={their_protocol}")
        return False

    emit_hello_ack()
    return True


def wait_for_hello(timeout: float = 5.0) -> bool:
    """Wait for frontend hello during startup. Returns False if timeout (dumb pipe mode)."""
    if not _stdin_reader:
        return False

    cmd = _stdin_reader.poll_blocking(timeout=timeout)
    if cmd and cmd.get("type") == "hello":
        return process_hello(cmd)
    # No hello received — assume dumb pipe consumer (CI/scripts)
    # Don't start heartbeat, don't require prompt responses
    return False


def set_heartbeat_state(state: str, operation: str | None = None) -> None:
    """Update the heartbeat state (called at phase transitions)."""
    if _heartbeat_emitter:
        _heartbeat_emitter.set_state(state, operation)
