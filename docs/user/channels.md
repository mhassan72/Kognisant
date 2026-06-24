# Channels — Remote AI & Social Media Management

Channels let you access Kognisant from anywhere and automate your project's social media presence.

---

## What Channels Do

**Two modes, one system:**

1. **Remote Assistant** — Message your Kognisant from Telegram, Discord, or any platform. It runs on your machine with full project context, tools, and agents. Code from your phone.

2. **Developer Marketing Engine** — Your Kognisant autonomously posts content, replies to community mentions, and moderates your social accounts. Replaces the $150/month Fiverr SMM gig.

Both modes can run on the same channel simultaneously (hybrid mode).

---

## Quick Start

### 1. Create a channel

```bash
kognisant channel add my-bot --platform telegram --mode hybrid --owner-id "tg:YOUR_TELEGRAM_ID"
```

Modes:
- `assistant` — Only you can talk to it (remote AI access)
- `manager` — Bot manages a public social account
- `hybrid` — Your DMs get full AI, everyone else gets managed replies

### 2. Set credentials

```bash
kognisant channel set-credentials my-bot
```

You'll be prompted for your bot token (Telegram) or API keys (X/Twitter). Credentials are encrypted with AES-256-GCM and stored securely.

> **Requires `cryptography` package:** `pip install cryptography`
> Without it, credential storage will refuse to proceed (no insecure fallback).

### 3. Set up the adapter environment

```bash
# Create isolated virtualenv for the Telegram adapter
python3 -m venv ~/.kognisant_core/scripts/channel_telegram_venv

# Install dependencies
~/.kognisant_core/scripts/channel_telegram_venv/bin/pip install python-telegram-bot
```

### 4. Start the channel

```bash
# Make sure daemon is running
kognisant daemon start

# Start the channel
kognisant channel start my-bot
```

### 5. Message your bot

Open Telegram, find your bot, and send a message. Your Kognisant processes it locally and responds through the same chat.

---

## How It Works

```
You (Telegram/Discord/X)
    │ message
    ▼
Adapter Script (runs on your machine, in its own venv)
    │ Unix socket
    ▼
Kognisant Daemon
    │ routes based on sender
    ▼
┌─────────────────────────────────────────────┐
│  Owner? → Full AI pipeline (tools, agents)   │
│  Public? → Manager mode (persona, templates) │
└─────────────────────────────────────────────┘
    │
    ▼
Response sent back through the same channel
```

Everything stays on your machine. No cloud relay. No data sharing.

---

## Remote Assistant Mode

When you message the bot as the owner, you get the same capabilities as the CLI:

| What you can do | Example |
|-----------------|---------|
| Ask questions about your project | "What's the status of the auth refactor?" |
| Edit files | "Add error handling to the login function" |
| Run agents | "/agent write tests for the payment module" |
| Check jobs | "/jobs" |
| Manage the daemon | "/daemon status" |
| Read files | "/read src/main.py" |
| Execute shell commands | Requires explicit permission in config |

### Security

Remote assistant gives machine access via a messaging platform. Security layers:

1. **Platform ID verification** — Only your verified account gets access
2. **Session PIN** (optional) — Send a PIN to activate a session (expires after 8h)
3. **Action confirmation** — Destructive operations require a challenge code
4. **Tool allowlist** — Disable shell, file ops, or agents per channel
5. **Audit log** — Every action is logged
6. **Lockdown** — `kognisant channel lockdown` stops everything immediately

### Setting up session auth

For extra security (recommended for channels with shell access):

```bash
kognisant channel config my-bot
```

In the config, set:
```json
"security": {
  "session_required": true,
  "session_timeout_hours": 8
}
```

Now you'll need to send your PIN before the bot responds to commands.

---

## Manager Mode (Social Media)

In manager mode, Kognisant operates your social account:

- **Posts content** on a schedule (content calendar)
- **Replies to mentions** with your brand voice
- **Moderates** spam and abuse
- **Escalates** tricky situations to you

### How replies work

Most mentions are repetitive ("how do I install?"). The system handles them in layers:

1. **Template match** — Instant reply, zero LLM cost (handles ~70% of mentions)
2. **LLM response** — For novel questions, queued and answered within active hours
3. **Escalation** — Sensitive content flagged for your review

### The queue

Your bot doesn't reply instantly (that would require a GPU running 24/7). Instead, it batches like a human SMM:

- Checks mentions every 15 minutes
- Template matches reply instantly
- LLM responses process one at a time
- Replies arrive within 1-2 hours during active hours

This is by design. A solo dev running a local 7B model can't parallelize responses, but the bot is consistent, never forgets, and costs $0/month.

### Content calendar (Phase 2a)

```json
"content_calendar": {
  "enabled": true,
  "cron": "0 9,14,18 * * *",
  "topics_source": "docs/content-plan.md",
  "require_approval": true
}
```

The bot reads your topics file, generates posts, and either auto-publishes or queues for your approval.

---

## CLI Commands

```bash
# Lifecycle
kognisant channel add <name> --platform <platform> --mode <mode> [--owner-id <id>]
kognisant channel remove <name>
kognisant channel list
kognisant channel status [name]
kognisant channel start <name>
kognisant channel stop <name>

# Configuration
kognisant channel set-credentials <name>
kognisant channel config <name>

# Monitoring
kognisant channel logs <name> [--follow]
kognisant channel test <name>

# Safety
kognisant channel lockdown          # Emergency stop ALL channels
kognisant channel revoke-sessions <name>
```

### Chat slash commands

Inside a `kognisant chat` session:

```
/channels                        List all channels with status
/channel status [name]           Detailed status
/channel start <name>            Start a channel
/channel stop <name>             Stop a channel
/channel pause <name>            Pause (queue events, don't respond)
/channel escalations             View pending human reviews
/channel metrics <name>          Performance metrics (Phase 2a)
```

---

## Supported Platforms

| Platform | Adapter | Status | Connection |
|----------|---------|--------|-----------|
| Telegram | `channel_telegram.py` | ✅ Shipped | Long polling |
| X/Twitter | `channel_x.py` | Phase 2a | API v2 |
| Discord | `channel_discord.py` | Phase 3 | WebSocket |
| Reddit | `channel_reddit.py` | Phase 3 | API polling |
| WhatsApp | `channel_whatsapp.py` | Phase 4 | Business API |
| Custom | Write your own | ✅ Available | Any |

### Writing a custom adapter

Kognisant can connect to any platform. Write a Python script that:
1. Connects to the daemon's Unix socket
2. Polls your platform for messages
3. Sends events to the daemon, receives actions back

See the [developer docs](../developer/channels.md) for the full adapter protocol spec.

---

## Hybrid Mode

The most powerful setup. One bot, two roles:

- **Your DMs** → Full Kognisant (edit code, run agents, check status)
- **Public mentions** → Brand bot (helpful replies, content posting, moderation)
- **Your public @bot with /command** → Still goes to assistant mode

```bash
kognisant channel add my-bot --platform telegram --mode hybrid --owner-id "tg:123456"
```

The router knows you by your platform ID. No one else gets assistant access.

---

## Configuration Reference

Full channel config (edited via `kognisant channel config <name>`):

```json
{
  "name": "my-bot",
  "platform": "telegram",
  "mode": "hybrid",
  "owner_ids": ["tg:123456789"],
  "assistant_config": {
    "full_tool_access": true,
    "allow_agent_swarm": true,
    "allow_file_ops": true,
    "allow_shell": false,
    "project_root": "/home/user/my-project",
    "confirmation_required": ["delete_project_path", "shell_exec"]
  },
  "manager_config": {
    "persona": {
      "voice": "friendly, technical, concise",
      "knowledge_base": ["docs/public-faq.md"],
      "language": "en"
    },
    "rate_limits": {
      "inbound_per_user_per_minute": 5,
      "outbound_replies_per_hour": 30
    },
    "cost_gate": {
      "max_llm_calls_per_hour": 10,
      "max_llm_calls_per_day": 50,
      "on_exceeded": "template_only"
    }
  },
  "security": {
    "session_required": false,
    "session_timeout_hours": 8
  }
}
```

---

## Troubleshooting

### "No adapter script found"

The adapter for your platform hasn't been installed yet. For Telegram:
```bash
python3 -m venv ~/.kognisant_core/scripts/channel_telegram_venv
~/.kognisant_core/scripts/channel_telegram_venv/bin/pip install python-telegram-bot
```

The reference script is auto-copied from Kognisant's bundled adapters on first start.

### "No secure credential storage available"

Install the cryptography package:
```bash
pip install cryptography
```

Kognisant refuses to store credentials without proper encryption. No insecure fallback exists by design.

### Channel shows "error" state

Check logs:
```bash
kognisant channel logs my-bot
```

Common causes:
- Invalid bot token
- Adapter dependencies not installed
- Network connectivity issues

### Bot not responding to messages

1. Check channel is running: `kognisant channel status my-bot`
2. Check daemon is running: `kognisant daemon status`
3. Check your owner ID matches: the `sender_id` format is `tg:YOUR_NUMERIC_ID` (not username)
4. Check logs for errors: `kognisant channel logs my-bot --follow`

---

## What's Coming Next

| Phase | Features |
|-------|----------|
| **Phase 2a** | Template bank, priority queue, content calendar, X/Twitter adapter, metrics |
| **Phase 2b** | Auto-template discovery, competitive tracking, moderation pipeline |
| **Phase 2c** | A/B testing, cross-platform posting, response feedback |
| **Phase 3** | Discord, Reddit adapters, remote PERP agents |
| **Phase 4** | Platform analytics, WhatsApp, community ecosystem |
