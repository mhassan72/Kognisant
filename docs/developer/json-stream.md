# JSON Stream Protocol — Developer Documentation

## Overview

The `--json-stream` flag transforms Kognisant's CLI output from terminal-formatted text into structured JSONL events on stdout. This enables any frontend (Electron Studio, VS Code extension, CI pipeline) to programmatically consume the CLI's output.

**Module:** `cli_kognisant/json_stream.py`

---

## Architecture

```
Frontend (Electron, VS Code, CI)
    │
    ├── spawns: kognisant chat --json-stream
    ├── reads stdout line-by-line (JSONL events)
    ├── writes stdin line-by-line (JSON commands)
    └── monitors process exit
          │
          ▼
┌─────────────────────────────────────────────────────────────┐
│  json_stream.py                                              │
│                                                               │
│  emit()           → write JSON + newline + flush to stdout   │
│  StdinReader      → daemon thread, reads stdin → queue       │
│  HeartbeatEmitter → daemon thread, 5s heartbeat with state   │
│  poll_command()   → non-blocking check for stdin commands    │
│  check_for_cancel() → non-blocking cancel detection          │
│  wait_for_prompt_response() → blocking with timeout          │
└─────────────────────────────────────────────────────────────┘
```

---

## Activation

```python
# In main.py after argparse:
if args.json_stream:
    from .json_stream import activate as activate_json_stream
    activate_json_stream()
```

All modules check `json_stream.is_active()` before emitting. If not active, all emit calls are no-ops (zero overhead in terminal mode).

---

## Key Components

### `emit(event: dict)`

Writes a single JSON line to stdout. Adds `ts` (ISO 8601 UTC) if not present. Handles broken pipe gracefully.

### `StdinReader` (daemon thread)

Reads stdin line-by-line in a background thread. Parses JSON. Enqueues valid commands. Emits warning for malformed JSON. Detects EOF (frontend disconnect).

**Thread-safe access:** Main loop calls `poll_command()` (non-blocking) or `wait_for_prompt_response()` (blocking with timeout).

### `HeartbeatEmitter` (daemon thread)

Emits `{"type": "heartbeat", "seq": N, "state": "..."}` every 5 seconds. State is updated by main loop via `set_heartbeat_state(state, operation)`.

States: `idle`, `streaming`, `tool_executing`, `waiting_prompt`, `swarm_active`

### `wait_for_prompt_response(prompt_id, timeout_ms, default)`

Blocks until frontend sends matching `prompt_response`, timeout expires (returns default), or cancel received. Handles `prompt_extend` to increase timeout.

---

## Protocol Handshake

```
CLI emits:      {"type": "session_start", "protocol_version": "1.0", ...}
Frontend sends: {"type": "hello", "frontend": "...", "protocol_version": "1.0"}
CLI emits:      {"type": "hello_ack", "session_id": "...", "heartbeat_interval_ms": 5000}
```

If no `hello` within 5 seconds, CLI assumes dumb pipe (no heartbeat, no prompts).

---

## Event Types Emitted

| Event | Emitted From | Description |
|-------|-------------|-------------|
| `session_start` | chat.py | Session begins, includes model/project info |
| `session_end` | chat.py | Session ends (user_exit, frontend_disconnect) |
| `session_resumed` | chat.py | Loaded messages from --resume-session file |
| `user_message` | chat.py | User's input text |
| `classification` | runtime.py (_plan) | SIMPLE/CONTEXT/COMPLEX/AUTONOMOUS |
| `status` | runtime.py (execute_message) | Phase transition (bootstrap/planning/executing/reflecting) |
| `thinking_start/delta/end` | runtime.py (_execute) | Model reasoning tokens |
| `content_start/delta/end` | runtime.py (_execute) | Model response content |
| `tool_start` | runtime.py (_execute_tools) | Tool invocation with args |
| `tool_result` | runtime.py (_execute_tools) | Tool completion with success/summary |
| `file_created` | tools.py | File written to disk |
| `file_modified` | tools.py | File edited (with edit count) |
| `file_deleted` | tools.py | File or directory removed |
| `swarm_start` | agents.py | PERP swarm launched |
| `swarm_worker_start/complete` | agents.py | Individual worker lifecycle |
| `swarm_complete` | agents.py | All workers finished |
| `channel_event` | channel_daemon.py | Message received from platform |
| `channel_response` | channel_daemon.py | Reply sent to platform |
| `error` | runtime.py | API/bootstrap errors |
| `warning` | runtime.py, json_stream.py | Retries, malformed stdin |
| `heartbeat` | json_stream.py (thread) | Periodic liveness signal |
| `pong` | json_stream.py | Response to frontend ping |
| `degenerate_loop` | runtime.py | Repetitive output detected |
| `command_result` | main.py | One-shot command JSON data |

---

## Adding Events to New Code

When adding a new feature that produces output:

```python
from . import json_stream

# Check if active (zero overhead if not)
if json_stream.is_active():
    json_stream.emit_my_event(...)

# Always keep terminal output alongside (don't replace):
if not json_stream.is_active():
    print("Terminal output here")
# OR use the is_json pattern:
is_json = json_stream.is_active()
if is_json:
    json_stream.emit_something(...)
else:
    print("Terminal version")
```

**Rules:**
- Use lazy import: `from . import json_stream` inside functions, not at module top
- Always guard with `is_active()` — the module may not be initialized
- Never replace terminal output — json events are additional
- Use typed emit_* functions when available, `emit({...})` for custom events

---

## One-Shot Commands

For commands that output data and exit (not interactive):

```python
def _handle_my_command():
    from . import json_stream
    if json_stream.is_active():
        json_stream.emit({
            "type": "command_result",
            "command": "my_command",
            "data": {...structured data...},
        })
        return  # Don't print terminal output
    
    # Normal terminal output
    print("...")
```

---

## Cross-References

- [Architecture](architecture.md) — Overall system design
- [Channels](channels.md) — Channel events in the protocol
- [CLI Reference](cli-reference.md) — `--json-stream` flag documentation
