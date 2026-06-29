# JSON Stream Mode

Kognisant can output structured JSON events instead of terminal-formatted text. This enables GUIs, extensions, CI pipelines, and custom scripts to consume the CLI's output programmatically.

---

## Quick Start

```bash
# Normal mode (human-readable terminal output)
kognisant chat

# JSON stream mode (machine-readable events)
kognisant chat --json-stream

# One-shot commands also work
kognisant --json-stream status
kognisant --json-stream channel list
```

---

## What It Does

With `--json-stream`, every piece of output becomes a JSON object on its own line:

```jsonl
{"type": "session_start", "protocol_version": "1.0", "model": "gemini-3.5-flash", "project": "/home/user/my-app", "ts": "2026-06-29T10:00:00Z"}
{"type": "user_message", "content": "refactor the auth module", "ts": "..."}
{"type": "classification", "level": "COMPLEX", "tokens_in_estimate": 2100, "ts": "..."}
{"type": "thinking_start", "ts": "..."}
{"type": "thinking_delta", "content": "I need to read the auth module first...", "ts": "..."}
{"type": "thinking_end", "duration_ms": 1200, "ts": "..."}
{"type": "content_start", "ts": "..."}
{"type": "content_delta", "content": "I'll refactor the auth module to use JWT.", "ts": "..."}
{"type": "tool_start", "tool": "read_project_file", "args": {"file_path": "src/auth.py"}, "ts": "..."}
{"type": "tool_result", "success": true, "summary": "Read 3.2KB", "duration_ms": 2, "ts": "..."}
{"type": "content_end", "duration_ms": 4500, "tokens_out": 180, "ts": "..."}
{"type": "file_modified", "path": "src/auth.py", "edits_applied": 3, "ts": "..."}
```

---

## Use Cases

### Build a Custom GUI

Spawn the CLI as a subprocess, read its stdout for events, write commands to its stdin:

```python
import subprocess, json

proc = subprocess.Popen(
    ["kognisant", "chat", "--json-stream"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
    text=True, cwd="/path/to/project"
)

# Send a message
proc.stdin.write(json.dumps({"type": "message", "content": "explain the auth module"}) + "\n")
proc.stdin.flush()

# Read events
for line in proc.stdout:
    event = json.loads(line)
    if event["type"] == "content_delta":
        print(event["content"], end="")
    elif event["type"] == "content_end":
        print("\n--- Done ---")
        break
```

### CI/CD Integration

Extract structured data from one-shot commands:

```bash
# Get model info in CI
MODEL=$(kognisant --json-stream status | jq -r '.data.active_model')
echo "Using model: $MODEL"

# List channels as JSON
kognisant --json-stream channel list | jq '.data[] | .name'
```

### Pipe to Custom Scripts

```bash
# Log all tool calls to a file
kognisant chat --json-stream | jq -c 'select(.type == "tool_start" or .type == "tool_result")' >> tool_log.jsonl

# Monitor for errors
kognisant chat --json-stream | jq -c 'select(.type == "error")' | while read -r line; do
  echo "ERROR: $(echo $line | jq -r .message)"
done
```

---

## Sending Commands (stdin)

In interactive mode (`kognisant chat --json-stream`), you send commands via stdin:

```jsonl
{"type": "message", "content": "refactor the auth module"}
{"type": "cancel"}
{"type": "command", "slash": "/model", "args": "gemini-3.5-flash"}
{"type": "command", "slash": "/channel", "args": "add my-bot telegram hybrid"}
{"type": "exit"}
```

---

## Session Resumption

If your frontend crashes, resume from where you left off:

```bash
# Find the latest session file
ls ~/.kognisant_core/history/ | tail -1
# session_20260629_143000.json

# Resume it
kognisant chat --json-stream --resume-session session_20260629_143000.json
```

The CLI loads the previous conversation history and continues. You'll see:

```jsonl
{"type": "session_resumed", "session_file": "session_20260629_143000.json", "messages_loaded": 12, "ts": "..."}
```

---

## Protocol Handshake

For full-featured frontends (like Kognisant Studio), a handshake enables heartbeats and prompts:

```
CLI emits:     {"type": "session_start", "protocol_version": "1.0", ...}
You send:      {"type": "hello", "frontend": "my-app", "frontend_version": "1.0.0", "protocol_version": "1.0"}
CLI responds:  {"type": "hello_ack", "session_id": "...", "heartbeat_interval_ms": 5000}
```

After handshake, the CLI sends heartbeats every 5 seconds so you know it's alive:

```jsonl
{"type": "heartbeat", "seq": 1, "state": "idle", "ts": "..."}
{"type": "heartbeat", "seq": 2, "state": "streaming", "operation": "llm_response", "ts": "..."}
```

If you don't send `hello` within 5 seconds, the CLI works without heartbeats (simpler for scripts/CI).

---

## Event Reference

### Session

| Event | When |
|-------|------|
| `session_start` | CLI starts, includes model/project info |
| `session_end` | Session finished (reason: user_exit, frontend_disconnect) |
| `session_resumed` | Loaded history from --resume-session |

### Messages

| Event | When |
|-------|------|
| `user_message` | User's input received |
| `classification` | Message classified (SIMPLE/CONTEXT/COMPLEX/AUTONOMOUS) |
| `status` | Phase transition (bootstrap/planning/executing/reflecting) |

### Response Streaming

| Event | When |
|-------|------|
| `thinking_start/delta/end` | Model reasoning (if model supports it) |
| `content_start/delta/end` | Model response text, streamed character by character |

### Tools

| Event | When |
|-------|------|
| `tool_start` | Tool invocation begins (with tool name + args) |
| `tool_result` | Tool completed (success/failure + summary) |
| `file_created` | New file written |
| `file_modified` | Existing file edited (with edit count) |
| `file_deleted` | File or directory removed |

### Agent Swarm

| Event | When |
|-------|------|
| `swarm_start` | PERP swarm launched (with task + worker count) |
| `swarm_worker_start/complete` | Individual worker lifecycle |
| `swarm_complete` | All workers finished |

### Channels

| Event | When |
|-------|------|
| `channel_event` | Message received from a platform |
| `channel_response` | Reply sent to a platform |

### Status / Errors

| Event | When |
|-------|------|
| `heartbeat` | Every 5s (after handshake) with current state |
| `error` | Something failed (with code + recoverable flag) |
| `warning` | Non-fatal issue (retry, malformed input) |
| `degenerate_loop` | Model stuck repeating instead of using tools |
| `command_result` | Response to one-shot commands (status, channel list) |

---

## Tips

- Every event has a `ts` field (ISO 8601 UTC timestamp)
- Events are forward-compatible — unknown types should be ignored
- The `--json-stream` flag works with any subcommand
- Terminal output is completely suppressed in JSON mode (no ANSI, no spinners)
- The CLI never blocks on stdin — it reads non-blockingly via a background thread
