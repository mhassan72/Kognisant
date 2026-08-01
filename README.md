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

⚡ gemma4:latest | valence: +22 | 8 skills, 4 tools
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

🔍 18.2s | 2,100 in > 420 out | valence: +27 (+5) | 3 tool(s)
```

---

## Why Switch to Kognisant?

| Problem | How Kognisant Solves It |
|---------|------------------------|
| You explain your project every session | Persistent memory loads automatically. No re-explaining. |
| AI forgets context between messages | Two-layer memory: per-project + global knowledge. |
| Complex tasks need manual babysitting | Autonomous agents plan, execute, and reflect without intervention. |
| Locked into one provider | Kognisant Cloud models out of the box, plus any LLM. Local or cloud. |
| Tools are hardcoded and limited | AI builds its own tools when it encounters new tasks. |
| No visibility into what the AI is doing | Every phase is transparent: classification, tokens, timing, reasoning. |
| Background tasks require separate tooling | Built-in daemon with cron scheduling and persistent services. |

---

## Install

```bash
pip install kognisant
```

Or with the installer (creates an isolated venv):

```bash
curl -fsSL https://raw.githubusercontent.com/mhassan72/Kognisant/main/install.sh | sh
```

Or from git:

```bash
pip install git+https://github.com/mhassan72/Kognisant.git
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

### 2. Start chatting

```bash
kognisant chat
```

It auto-detects Ollama locally. Or log in to use Kognisant Cloud models (DeepSeek-V4, MiniMax-M3, Kimi, and more):

```bash
kognisant login
```

Use `/model` in chat to switch between any available model.

### 3. Let the agent handle complex work

```
/agent research best practices for rate limiting and implement them
```

The agent swarm plans the work, executes in parallel, and writes the results to your project.

### 4. Check what it learned

```
/context
```

Shows the persistent memory that carries across all future sessions.

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
  Planning with: gemma4:latest
  Workers: 4 subtasks identified
    ✅ Agent [1] Completed: Research psutil-free system metrics
    ✅ Agent [2] Completed: Create dashboard layout module
    ✅ Agent [3] Completed: Create metrics collection module
    ✅ Agent [4] Completed: Wire CLI entry point
  ✨ PERP Swarm Process Finished Successfully!
```

**Background jobs:**
```bash
kognisant job add --name health-check --script monitor.py --type scheduled --cron "*/5 * * * *"
```

**Channels — Remote AI + Social Media Management:**
```bash
# Set up a Telegram bot to reach your Kognisant remotely
$ kognisant channel add my-bot --platform telegram --mode hybrid --owner-id "tg:123456"
$ kognisant channel set-credentials my-bot
$ kognisant channel start my-bot
```

Now message your bot from your phone — full AI with project context, tools, and agents:

```
You (Telegram): what's failing in the tests?

Kognisant: 2 failures in test_token_expiry.py:
  - test_refresh_expired: timezone-naive comparison on line 42
  - test_validate_stale: off-by-one in TTL check

You: /agent fix both

Kognisant: 🐝 PERP Swarm Activated (2 subtasks)
  ✅ Agent [1]: Fixed timezone comparison
  ✅ Agent [2]: Fixed TTL off-by-one
✨ Done. All 14 tests passing.
```

Two modes on one channel:

| Your DMs (owner) | Public messages (everyone else) |
|-------------------|---------------------------------|
| Full AI assistant — tools, agents, file ops | Brand bot — persona voice, templates, moderation |
| Direct `chat.py` pipeline | `manager_respond()` — zero tools, isolated |
| Instant response | Queued with priority + deadlines |

```bash
$ kognisant channel list

  Channels:

    ● running   my-bot (telegram, hybrid)
    ○ stopped   brand-x (x, manager)
```

---

## Core Features

### Persistent Memory
Every project gets a `.kognisant/context.md` file that the AI reads on startup and updates after significant work. Global skills in `~/.kognisant_core/skills/` carry knowledge across all projects. Teach it once, reuse forever.

### Multi-Model Support
Kognisant Cloud provides instant access to premium models (DeepSeek-V4-Pro, MiniMax-M3, Kimi-K2.7-Code, and more) — just `kognisant login`. Also supports Ollama, llama.cpp, OpenAI, Anthropic, Groq, NVidia, Nebius, or any OpenAI-compatible endpoint. Switch mid-session with `/model`. The system tracks per-model reliability, auto-switches on failures, and falls back gracefully from cloud → external → local.

### Autonomous Agents
The `/agent` command dispatches a multi-agent swarm that plans, executes in parallel, reflects on outcomes, and persists learnings. Complex tasks like "research X and write Y" are auto-detected and delegated to the swarm without manual intervention.

### Channels
Access Kognisant remotely from Telegram, Discord, X, or any messaging platform. In hybrid mode, your DMs get full AI assistant access while public messages are handled by a persona-driven brand bot with template responses, moderation, and content scheduling.

### Background Daemon
A POSIX daemon (Linux/macOS) runs persistent services, cron jobs, and one-shot AI tasks without an open terminal. Crash recovery, atomic writes, and log rotation included.

### Self-Building Tools
When the AI encounters a task beyond its built-in toolkit, it creates new tools (JSON schema + Python implementation) stored globally. Available in all future sessions automatically.

### Reasoning Display
Models that support reasoning (gemma4, deepseek-r1, qwen3) stream their thinking in real-time. You see exactly how the AI is working through your request.

---

## Advanced Features

These are documented in detail in [`docs/`](docs/):

- **World Model** — Living dependency graph of your codebase with confidence-tracked knowledge, goal generation, and graduated autonomy
- **Reflection Engine** — HOT (every turn), WARM (every 3rd), COLD (every 20th) health assessment with valence tracking
- **Circuit Breakers** — Per-model failure detection (5 failures in 30s opens the breaker, 30s cooldown)
- **Token Calibration** — Per-model correction factors that improve accuracy over time
- **Dynamic Escalation** — Automatic detection of multi-step tasks and delegation to the agent swarm
- **Spec-Driven Development** — Structured requirements, design, and task documents that agents execute against
- **Channel Adapters** — Standalone scripts in isolated virtualenvs, communicating via Unix domain sockets (protocol v1.0)
- **Telemetry** — Per-execution recording with `/telemetry` command for stats

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
kognisant channel add       # Create a channel (remote AI / social media)
kognisant channel start     # Start a channel adapter
kognisant channel list      # Show all channels with status
```

### Chat Slash Commands

| Command | What It Does |
|---------|-------------|
| `/help` | All commands |
| `/model` | Switch, add, or remove models |
| `/agent <task>` | Dispatch autonomous agent swarm |
| `/read <path>` | Load file into context |
| `/files` | List project files |
| `/context` | Show project memory |
| `/thinking` | Review AI reasoning |
| `/telemetry` | Execution statistics |
| `/goals` | World Model improvement goals |
| `/channels` | List channels with status |
| `/channel add [name platform mode]` | Create a channel (guided if no args) |
| `/channel remove <name>` | Remove a channel |
| `/channel status <name>` | Detailed channel view |
| `/channel start/stop <name>` | Control channel lifecycle |
| `/channel escalations` | View pending human reviews |
| `/jobs` | List background jobs |
| `/paste` | Multi-line input mode |
| `/spec` | Spec-Driven Development |

---

## Project Structure

```
cli-kognisant/
├── cli_kognisant/          # Source modules
│   ├── main.py             # CLI entry point (argparse)
│   ├── auth.py             # Authentication (Firebase + API key)
│   ├── chat.py             # Interactive chat loop + slash commands
│   ├── agents.py           # PERP swarm orchestration
│   ├── channels.py         # Channel system (remote AI + SMM)
│   ├── channel_daemon.py   # Daemon-side channel management
│   ├── daemon.py           # Background daemon (fork, polling, lifecycle)
│   ├── jobs.py             # Job queue, cron parser, file locking
│   ├── config.py           # Configuration, model pool, project discovery
│   ├── network.py          # API transport (retry, backoff, multi-protocol)
│   ├── tools.py            # Tool schemas + execution
│   ├── world_model.py      # Dependency graph + belief system
│   ├── adapters/           # Reference channel adapter scripts
│   └── ...
├── tests/                  # 1000+ pytest tests
├── docs/
│   ├── developer/          # Architecture, internals, extension guides
│   ├── user/               # User guides and walkthroughs
│   └── upgrade_plans/      # Feature roadmaps (channels, sync, webapp)
├── pyproject.toml          # Zero-dependency build config
└── install.sh              # One-liner installer
```

Full architecture details: [`docs/developer/architecture.md`](docs/developer/architecture.md)

---

## Documentation

### User Guides

| Document | Content |
|----------|---------|
| [Getting Started](docs/user/getting-started.md) | Installation, first setup, first chat |
| [Persistent Memory](docs/user/persistent-memory.md) | Two-layer memory system, skills |
| [Autonomous Agents](docs/user/autonomous-agents.md) | PERP swarm, /agent, monitoring |
| [Background Daemon](docs/user/background-daemon.md) | Daemon, jobs, cron scheduling |
| [Channels](docs/user/channels.md) | Remote AI access + social media management |
| [Models and Providers](docs/user/models-and-providers.md) | Multi-model support, switching |
| [User Manual](docs/user/user_manual.md) | Complete reference |

### Developer Docs

| Document | Content |
|----------|---------|
| [Architecture](docs/developer/architecture.md) | System design, module map, data flow |
| [Channels](docs/developer/channels.md) | UDS protocol, adapters, routing, encryption |
| [Execution Engine](docs/developer/execution-engine.md) | Atomic writes, recovery, schema versioning |
| [Job Lifecycle](docs/developer/job-lifecycle.md) | State machine, daemon polling, crash recovery |
| [World Model](docs/developer/world-model.md) | Dependency graph, goals, graduated autonomy |
| [Security](docs/developer/security.md) | Sandboxing, permissions, containment |
| [CLI Reference](docs/developer/cli-reference.md) | All commands with flags and exit codes |
| [Testing](docs/developer/testing.md) | Test structure, fixtures, running tests |

---

## Contributing

1. Fork the repository
2. Create a feature branch
3. Commit your changes
4. Open a Pull Request

One rule: **zero external dependencies**. Python 3.10+ standard library only.

---

## About

Built by a developer in **Mogadishu, Somalia**.

AI tooling should be accessible, portable, and private. Not locked behind subscriptions, bloated dependency trees, or proprietary ecosystems.

---

## License

Apache License 2.0
