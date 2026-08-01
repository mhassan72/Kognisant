# Kognisant

An open-source AI CLI assistant that remembers your projects, runs autonomous agents, and works with any LLM.

[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![PyPI](https://img.shields.io/pypi/v/kognisant.svg)](https://pypi.org/project/kognisant/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://www.apache.org/licenses/LICENSE-2.0)
![Dependencies](https://img.shields.io/badge/dependencies-0-brightgreen.svg)

```
Requirements
  ✓ Python 3.10+
  ✓ No Docker
  ✓ No Node.js
  ✓ No external dependencies
  ✓ Works with Kognisant Cloud, any OpenAI-compatible API, or local models (Ollama, llama.cpp)
```

---

## What It Does

Stop re-explaining your project to AI every session. Kognisant remembers your architecture, learns your patterns, and gets better over time.

```bash
$ kognisant chat

You > refactor authentication to use JWT

⚡ DeepSeek-V4-Pro (Kognisant Cloud) | valence: +22 | 8 skills, 4 tools
📋 COMPLEX - ~2,100 tokens input
💭 Thinking...
  1. Read the current auth module to understand the structure.
  2. The bcrypt-based session system needs to be replaced with JWT.
  3. I'll need to update the middleware, login route, and tests.
💭 Thought for 12.4s
  ┌─ Read auth/middleware.py ────────────────────────────────────────────┐
  │ ✓ 2ms | 3.2KB read                                                  │
  └──────────────────────────────────────────────────────────────────────┘
  ┌─ Accepted edits to auth/middleware.py ──────────────────────────────┐
  │ ✓ 4ms | 3 edits applied                                             │
  └──────────────────────────────────────────────────────────────────────┘
  ┌─ Created auth/jwt_utils.py ─────────────────────────────────────────┐
  │ ✓ 1ms | created (1.8KB)                                             │
  └──────────────────────────────────────────────────────────────────────┘

Kognisant >
Done. I've replaced the session-based auth with JWT:
- Created `auth/jwt_utils.py` with token generation and verification
- Updated middleware to validate Bearer tokens
- Preserved the existing user lookup logic

🔍 18.2s | 2,100 in → 420 out | valence: +27 (+5) | 3 tool(s)
```

---

## Why Kognisant?

| Problem | Solution |
|---------|----------|
| You explain your project every session | Persistent memory loads automatically |
| AI forgets context between messages | Two-layer memory: per-project + global knowledge |
| Complex tasks need manual babysitting | Autonomous agents plan, execute, and reflect |
| Locked into one provider | Kognisant Cloud models out of the box, plus any LLM |
| Tools are hardcoded and limited | AI builds its own tools for new tasks |
| No visibility into what the AI is doing | Every phase is transparent: classification, tokens, timing, reasoning |
| Background tasks require separate tooling | Built-in daemon with cron scheduling and persistent services |

---

## Install

```bash
pip install kognisant
```

Or with the installer (creates an isolated venv):

```bash
curl -fsSL https://raw.githubusercontent.com/mhassan72/Kognisant/main/install.sh | sh
```

Or from source:

```bash
git clone https://github.com/mhassan72/Kognisant.git
cd Kognisant
pip install -e .
```

---

## Quick Start

### 1. Initialize your project

```bash
cd your-project
kognisant init
```

### 2. Log in to Kognisant Cloud (optional)

```bash
kognisant login
```

Gives you instant access to premium models (DeepSeek-V4-Pro, MiniMax-M3, Kimi-K2.7-Code, and more). No API keys to configure. If you prefer local models, Kognisant auto-detects Ollama.

### 3. Start chatting

```bash
kognisant chat
```

Use `/model` in chat to switch between any available model at any time.

### 4. Let the agent handle complex work

```
/agent research best practices for rate limiting and implement them
```

The agent swarm plans the work, executes in parallel, and writes the results to your project.

### 5. Check what it learned

```
/context
```

Shows the persistent memory that carries across all future sessions.

---

## Kognisant Cloud

Log in once and get access to a curated set of high-performance models:

| Model | Context | Throughput | Capabilities |
|-------|---------|------------|--------------|
| DeepSeek-V4-Pro | 1M tokens | 24 tok/s | Reasoning, tool calling |
| MiniMaxAI/MiniMax-M3 | 1M tokens | 190 tok/s | Reasoning, tool calling |
| Kimi-K2.7-Code | 256K tokens | 231 tok/s | Reasoning, tool calling |
| Cosmos3-Super-Reasoner | 256K tokens | 30 tok/s | Reasoning, tool calling, vision |
| Kimi-K2.6 | 256K tokens | 60 tok/s | Reasoning, tool calling, vision |

The CLI automatically selects the best model for each task and falls back gracefully if a model is unavailable. Cloud → external → local, transparent to the user.

Manage your account at [kognisant.xyz/console/billing](https://kognisant.xyz/console/billing).

---

## Examples

**Simple conversation:**
```
You > what are we working on?
Kognisant > Based on context.md, you're building a REST API with JWT auth.
            Tasks remaining: rate limiting middleware, integration tests.
```

**File operations:**
```
You > read the test file and add a test for the new endpoint
  ┌─ Read tests/test_api.py ────────────────────────────────┐
  │ ✓ 1ms | 4.1KB read                                      │
  └──────────────────────────────────────────────────────────┘
  ┌─ Accepted edits to tests/test_api.py ───────────────────┐
  │ ✓ 3ms | 1 edit applied                                  │
  └──────────────────────────────────────────────────────────┘
```

**Autonomous agent:**
```
/agent create a CLI dashboard that shows system metrics
  🐝 PERP Swarm Activated
  Planning with: DeepSeek-V4-Pro
  Workers: 4 subtasks identified
    ✅ Agent [1] Completed: Research psutil-free system metrics
    ✅ Agent [2] Completed: Create dashboard layout module
    ✅ Agent [3] Completed: Create metrics collection module
    ✅ Agent [4] Completed: Wire CLI entry point
  📝 Synthesizing results...
  ✨ Done — dashboard module created at src/dashboard.py
```

**Background jobs:**
```bash
kognisant job add --name health-check --script monitor.py --type scheduled --cron "*/5 * * * *"
```

**Channels — Remote AI access:**
```bash
kognisant channel add my-bot --platform telegram --mode hybrid --owner-id "tg:123456"
kognisant channel set-credentials my-bot
kognisant channel start my-bot
```

---

## Core Features

### Persistent Memory
Every project gets a `.kognisant/context.md` file that the AI reads on startup and updates after significant work. Global skills in `~/.kognisant_core/skills/` carry knowledge across all projects.

### Multi-Model Support
Kognisant Cloud provides instant access to premium models — just `kognisant login`. Also supports Ollama, llama.cpp, OpenAI, Anthropic, Groq, or any OpenAI-compatible endpoint. Switch mid-session with `/model`. Per-model reliability tracking with automatic fallback: cloud → external → local.

### Autonomous Agents
The `/agent` command dispatches a multi-agent swarm that plans, executes in parallel, reflects on outcomes, and synthesizes a coherent response. Complex tasks are auto-detected and delegated without manual intervention.

### Channels
Access Kognisant remotely from Telegram, Discord, X, or any messaging platform. In hybrid mode, your DMs get full AI assistant access while public messages are handled by a persona-driven bot.

### Background Daemon
A POSIX daemon (Linux/macOS) runs persistent services, cron jobs, and one-shot AI tasks without an open terminal. Crash recovery, atomic writes, and log rotation included.

### Self-Building Tools
When the AI encounters a task beyond its built-in toolkit, it creates new tools (JSON schema + Python implementation) stored globally. Available in all future sessions.

### Reasoning Display
Models that support reasoning stream their thinking in real-time. You see exactly how the AI is working through your request.

---

## Commands

### CLI

```bash
kognisant login             # Authenticate with Kognisant Cloud
kognisant logout            # Clear cloud credentials
kognisant init              # Initialize project memory
kognisant chat              # Start interactive session
kognisant setup             # Configure external model providers
kognisant status            # Workspace health check
kognisant spec <name>       # Feature specification workflow
kognisant daemon start      # Start background daemon
kognisant job add           # Schedule a job
kognisant channel add       # Create a channel
kognisant channel start     # Start a channel adapter
kognisant channel list      # Show all channels with status
```

### Chat Commands

| Command | Description |
|---------|-------------|
| `/help` | All commands |
| `/model` | Switch, add, or remove models |
| `/agent <task>` | Dispatch autonomous agent swarm |
| `/read <path>` | Load file into context |
| `/files` | List project files |
| `/context` | Show project memory |
| `/thinking` | Review AI reasoning |
| `/telemetry` | Execution statistics |
| `/goals` | World model improvement goals |
| `/channels` | List channels with status |
| `/jobs` | List background jobs |
| `/paste` | Multi-line input mode |
| `/spec` | Spec-driven development |

---

## Project Structure

```
Kognisant/
├── cli_kognisant/          # Source modules
│   ├── main.py             # CLI entry point
│   ├── auth.py             # Authentication (Firebase + API key)
│   ├── chat.py             # Interactive chat loop
│   ├── agents.py           # PERP swarm orchestration
│   ├── channels.py         # Channel system
│   ├── daemon.py           # Background daemon
│   ├── jobs.py             # Job queue and cron
│   ├── config.py           # Configuration and model pool
│   ├── network.py          # API transport layer
│   ├── tools.py            # Tool schemas and execution
│   ├── world_model.py      # Dependency graph
│   └── ...
├── tests/                  # 1000+ pytest tests
├── docs/                   # User guides and developer docs
├── pyproject.toml          # Zero-dependency build config
└── install.sh              # One-liner installer
```

---

## Documentation

| Guide | Content |
|-------|---------|
| [Getting Started](docs/user/getting-started.md) | Installation, first setup, first chat |
| [Persistent Memory](docs/user/persistent-memory.md) | Two-layer memory system |
| [Autonomous Agents](docs/user/autonomous-agents.md) | PERP swarm and /agent |
| [Background Daemon](docs/user/background-daemon.md) | Jobs and cron scheduling |
| [Channels](docs/user/channels.md) | Remote AI access |
| [Architecture](docs/developer/architecture.md) | System design and data flow |
| [Security](docs/developer/security.md) | Sandboxing and permissions |

Full documentation at [kognisant.xyz/docs](https://kognisant.xyz/docs).

---

## Contributing

1. Fork the repository
2. Create a feature branch
3. Commit your changes
4. Open a Pull Request

Design principle: **zero external dependencies**. Python 3.10+ standard library only.

---

## About

Kognisant is built by [Kognisant Ltd](https://kognisant.xyz), a UK-registered company (Companies House) focused on improving access to AI inference and cloud compute across Africa and emerging markets.

AI tooling should be accessible, portable, and private — not locked behind subscriptions, bloated dependency trees, or proprietary ecosystems.

---

## Links

| Resource | URL |
|----------|-----|
| Website | [kognisant.xyz](https://kognisant.xyz) |
| Console | [kognisant.xyz/console](https://kognisant.xyz/console) |
| Documentation | [kognisant.xyz/docs](https://kognisant.xyz/docs) |
| Support | [support@kognisant.xyz](mailto:support@kognisant.xyz) |
| GitHub | [github.com/mhassan72/Kognisant](https://github.com/mhassan72/Kognisant) |
| PyPI | [pypi.org/project/kognisant](https://pypi.org/project/kognisant/) |

---

## License

Apache License 2.0
