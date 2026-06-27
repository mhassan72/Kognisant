# Channels System — Developer Documentation

## Overview

The channels system provides two capabilities:

1. **Remote Assistant** — Owner communicates with Kognisant from any messaging platform (Telegram, Discord, X, etc.), with full tool access, agents, and project context.
2. **Developer Marketing Engine** — Autonomous social media management: content posting, community replies, moderation, and engagement analytics.

This document covers the internal architecture, module responsibilities, IPC protocol, and extension points.

---

## Module Map

```
cli_kognisant/
├── channels.py          # Core: ChannelManager, ChannelServer, ChannelRouter,
│                        #   ChannelProtocol, CredentialManager, SessionAuth,
│                        #   AuditLogger, ResponseFormatter, data models
├── channel_daemon.py    # Daemon integration: ChannelDaemonService
│                        #   (spawns adapters, routes events, manages lifecycle)
└── adapters/
    ├── __init__.py
    ├── channel_telegram.py              # Reference Telegram adapter
    └── channel_telegram_requirements.txt
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  Daemon (_main_loop)                                                  │
│                                                                       │
│  ┌──────────────────────────────────────────────────────────────┐    │
│  │  ChannelDaemonService.poll()  [called every cycle]            │    │
│  │                                                                │    │
│  │  1. Check channel states (starting/running/stopped)            │    │
│  │  2. Spawn adapters for "starting" channels                     │    │
│  │  3. Monitor heartbeats for "running" channels                  │    │
│  │  4. Read events from connected adapters                        │    │
│  │  5. Route events via ChannelRouter                             │    │
│  │  6. Process responses and send actions back                    │    │
│  └──────────────────────────────────────────────────────────────┘    │
│                                                                       │
│  ┌────────────────────┐     ┌──────────────────────────────┐        │
│  │  ChannelServer      │     │  ChannelRouter                │        │
│  │  (UDS per channel)  │────▶│  is_owner? → chat.py          │        │
│  │  poll() + recv()    │     │  public?   → manager_respond() │        │
│  └────────────────────┘     └──────────────────────────────┘        │
└───────────────────────────────────┬─────────────────────────────────┘
                                    │ UDS (AF_UNIX)
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Adapter Subprocess (own venv, own dependencies)                      │
│  e.g. channel_telegram.py + python-telegram-bot                       │
│                                                                       │
│  - Connects to UDS socket                                             │
│  - Performs hello/hello_ack handshake                                  │
│  - Polls platform for events → sends to daemon                        │
│  - Receives actions from daemon → executes on platform                │
│  - Sends heartbeats every 30s                                         │
└─────────────────────────────────────────────────────────────────────┘
```

---

## IPC Protocol (v1.0)

### Wire Format

Length-prefixed binary over Unix domain socket:

```
┌──────────────────┬──────────────────────────────────┐
│ 4 bytes (BE)     │ JSON payload (UTF-8)              │
│ payload length   │                                   │
└──────────────────┴──────────────────────────────────┘
```

Implementation in `ChannelProtocol`:
```python
def send(sock, message: dict):
    data = json.dumps(message).encode("utf-8")
    sock.sendall(struct.pack(">I", len(data)) + data)

def recv(sock) -> dict | None:
    header = _recv_exact(sock, 4)
    length = struct.unpack(">I", header)[0]
    data = _recv_exact(sock, length)
    return json.loads(data.decode("utf-8"))
```

### Handshake Sequence

```
Adapter → Daemon:  {"type": "hello", "protocol_version": "1.0", "adapter_version": "...", "platform": "telegram"}
Daemon → Adapter:  {"type": "hello_ack", "protocol_version": "1.0", "daemon_version": "...", "config_path": "/tmp/..."}
Adapter → Daemon:  {"type": "ready", "capabilities": ["message", "dm", "reply", "like"]}
```

Version negotiation: major version mismatch → reject. Minor mismatch → warn and continue.

### Message Types

**Adapter → Daemon:**
| Type | Purpose |
|------|---------|
| `event` | Platform event (message, mention, DM, etc.) |
| `heartbeat` | Liveness signal with seq number |
| `action_result` | Confirmation that an action was executed |
| `error` | Adapter-side error report |

**Daemon → Adapter:**
| Type | Purpose |
|------|---------|
| `action` | Command to execute on platform (reply, post, etc.) |
| `heartbeat` | Daemon-side liveness signal |
| `heartbeat_ack` | Response to adapter's heartbeat |
| `config_reload` | Re-read config from disk |
| `shutdown` | Graceful shutdown request |

### Heartbeat Protocol

Bidirectional, every 30 seconds, with sequence numbers:
- Either side misses 3 consecutive heartbeats (90s) → connection considered dead
- Daemon restarts the adapter subprocess
- Adapter exits cleanly if daemon is unresponsive

---

## Key Classes

### `ChannelManager`

CRUD for channel configs. Stores in `~/.kognisant_core/channels/channels.json`.

- `add_channel(name, platform, mode, owner_ids)` — validates, creates config with defaults
- `remove_channel(name)` — deletes config + session data + credentials
- `update_state(name, state)` — transition channel state
- `update_config(name, updates)` — deep-merge config changes
- `write_temp_config(name)` — write channel config to temp file for adapter

Uses `FileLock` from `jobs.py` for concurrent access safety. Atomic writes via tempfile + rename.

**Accessible from three surfaces:**
- CLI: `kognisant channel add/remove/list/...` (main.py `_handle_channel()`)
- Chat: `/channel add/remove/status/start/stop/pause/escalations` (chat.py slash command)
- Daemon: `ChannelDaemonService` reads state to spawn/stop adapters

### `ChannelServer`

Per-channel Unix domain socket server.

- `start()` — create + bind + listen (non-blocking)
- `accept()` — accept pending connection (non-blocking)
- `handshake(config_path)` — protocol v1.0 hello/hello_ack/ready
- `send_message(msg)` / `recv_message()` — framed communication
- `send_heartbeat()` / `check_health()` — bidirectional heartbeat
- `close()` — cleanup socket file

Uses `select.poll()` for multiplexing (avoids macOS fd limits with `select.select()`).

### `ChannelRouter`

State machine routing based on sender identity + context:

```python
# Hybrid mode logic:
if is_owner(sender) and event_type == "dm":       → ASSISTANT
if is_owner(sender) and content.startswith("/"):   → ASSISTANT  
if is_owner(sender):                               → MANAGER (brand voice)
else:                                              → MANAGER
```

### `ChannelDaemonService`

Daemon-side orchestrator. Called by `_main_loop()` every poll cycle.

- `poll()` — check states, spawn/monitor adapters, process events
- `_start_channel()` — resolve script, create UDS, decrypt creds → env vars, spawn subprocess
- `_route_event()` — ChannelRouter → chat.py (assistant) or manager_respond() (manager)
- `_run_chat_pipeline()` — inject message into LLM with project context
- `shutdown_all()` — graceful stop of all adapter processes

### `CredentialManager`

Tiered encrypted storage:

1. **`cryptography` package** — AES-256-GCM, PBKDF2 (600K iterations)
2. **OS keyring** — macOS `security` CLI, Linux `secret-tool`
3. **Hard failure** — refuses to store, aborts channel setup

Core decrypts → injects as env vars at adapter spawn time. Adapter reads `os.environ`.

### `SessionAuth`

In-memory (ephemeral) session tokens for remote assistant security:

- PIN-based activation with configurable timeout (default 8h)
- Inactivity timeout (2h)
- Max 1 concurrent session per channel
- `revoke()` / `revoke_all()` for lockdown

---

## Adapter Script Contract

Any adapter script must:

1. Read `KOGNISANT_SOCKET` env var → connect to UDS
2. Send `{"type": "hello", ...}` → receive `{"type": "hello_ack", ...}`
3. Read config from path in `hello_ack` or `KOGNISANT_CONFIG_PATH` env var
4. Send `{"type": "ready", "capabilities": [...]}` 
5. Enter event loop:
   - Poll platform → emit `{"type": "event", ...}` to daemon
   - Read daemon actions → execute on platform → emit `{"type": "action_result", ...}`
   - Send heartbeat every 30s
6. Handle `{"type": "shutdown"}` → flush pending → exit cleanly
7. Handle `SIGTERM` → same as shutdown

Adapter scripts:
- Live in `~/.kognisant_core/scripts/channel_{platform}.py`
- Get their own virtualenv at `channel_{platform}_venv/`
- Are spawned with decrypted credentials as env vars
- stdout/stderr → log file at `~/.kognisant_core/channels/logs/{name}.log`

---

## File System Layout

```
~/.kognisant_core/
├── channels/
│   ├── channels.json         # Channel registry (atomic writes + fcntl lock)
│   ├── channels.lock         # Advisory lock
│   ├── credentials/          # Encrypted (0o600)
│   │   └── {name}.enc       # AES-256-GCM encrypted credential blob
│   ├── sessions/
│   │   └── {name}/
│   │       ├── state.db      # SQLite (Phase 2a: threads, metrics)
│   │       └── audit.jsonl   # Append-only audit log
│   ├── templates/            # Template banks (Phase 2a)
│   ├── escalations.jsonl     # Human review queue
│   └── logs/
│       └── {name}.log        # Adapter stdout/stderr
├── scripts/
│   ├── channel_telegram.py   # Adapter script (copied from bundled on first use)
│   ├── channel_telegram_venv/
│   └── ...
│
# Runtime (ephemeral):
/tmp/
├── kognisant_channel_{name}.sock   # UDS
└── kognisant_cfg_{name}.json       # Temp config (0o600)
```

---

## Extending: Writing a Custom Adapter

Create a new adapter for any platform:

```python
#!/usr/bin/env python3
"""Custom adapter for MyPlatform."""
import os, socket, struct, json, signal, time
from datetime import datetime, timezone

# ... (copy proto_send/proto_recv helpers from reference adapter)

def main():
    sock, config = connect_to_daemon()  # hello/hello_ack/ready
    
    while not shutdown:
        # Poll your platform for events
        events = my_platform_poll()
        for event in events:
            proto_send(sock, {
                "type": "event",
                "event_id": event.id,
                "platform": "myplatform",
                "event_type": "message",
                "sender_id": f"mp:{event.user_id}",
                "sender_name": event.username,
                "content": event.text,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "context": {"chat_id": event.chat_id},
                "reply_to": None,
                "attachments": [],
            })

        # Check for daemon actions
        action = try_recv(sock)
        if action and action["type"] == "action":
            my_platform_send(action["content"], action["metadata"]["chat_id"])
            proto_send(sock, {"type": "action_result", "action_id": action["action_id"], "success": True})

        # Heartbeat
        send_heartbeat(sock)
        time.sleep(1)

if __name__ == "__main__":
    main()
```

Save as `~/.kognisant_core/scripts/channel_myplatform.py`, create a venv, and register:
```bash
kognisant channel add my-channel --platform myplatform --mode assistant
```

---

## Cross-References

- [Architecture](architecture.md) — overall system architecture
- [Job Lifecycle](job-lifecycle.md) — persistent job management (channels use this)
- [Security](security.md) — credential handling, symlink containment
- [CLI Reference](cli-reference.md) — `kognisant channel` commands
