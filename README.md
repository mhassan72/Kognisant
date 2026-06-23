# Kognisant

An open-source AI CLI assistant that remembers your projects, runs autonomous agents, and works with any LLM.

[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
![Dependencies](https://img.shields.io/badge/dependencies-0-brightgreen.svg)

```
Requirements
  ✓ Python 3.10+
  ✓ No Docker
  ✓ No Node.js
  ✓ No external dependencies
  ✓ Works with any OpenAI-compatible API or local model (Ollama, llama.cpp)
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
| Locked into one provider | Switch between any LLM mid-session. Local or cloud. |
| Tools are hardcoded and limited | AI builds its own tools when it encounters new tasks. |
| No visibility into what the AI is doing | Every phase is transparent: classification, tokens, timing, reasoning. |
| Background tasks require separate tooling | Built-in daemon with cron scheduling and persistent services. |

---

## How It Compares

| Feature | Kognisant | Claude Code | Codex CLI | Gemini CLI | Aider |
|---------|-----------|-------------|-----------|------------|-------|
| Persistent memory across sessions | ✅ | ❌ | ❌ | ❌ | Partial |
| Works with any LLM (local or cloud) | ✅ | Claude only | OpenAI only | Gemini only | Multi-model |
| Multi-agent swarm execution | ✅ | ❌ | ❌ | ❌ | ❌ |
| Auto-detects when to use agents | ✅ | ❌ | ❌ | ❌ | ❌ |
| Background job scheduling (cron, persistent services) | ✅ | ❌ | ❌ | ❌ | ❌ |
| AI creates and schedules executable scripts | ✅ | ❌ | ❌ | ❌ | ❌ |
| Self-building tools (creates new capabilities on demand) | ✅ | ❌ | ❌ | ❌ | ❌ |
| Codebase world model with dependency graph | ✅ | ❌ | ❌ | ❌ | ❌ |
| Goal engine (auto-generates improvement suggestions) | ✅ | ❌ | ❌ | ❌ | ❌ |
| Graduated autonomy (learns what to do automatically) | ✅ | ❌ | ❌ | ❌ | ❌ |
| Spec-Driven Development (requirements > design > tasks) | ✅ | ❌ | ❌ | ❌ | ❌ |
| Self-healing model selection (circuit breakers, auto-switch) | ✅ | ❌ | ❌ | ❌ | ❌ |
| Execution telemetry and observability | ✅ | ❌ | ❌ | ❌ | ❌ |
| Zero external dependencies | ✅ | npm | pip | gcloud | pip |
| Fully open source | ✅ | ❌ | ✅ | ❌ | ✅ |

---

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/mhassan72/Kognisant/main/install.sh | sh
```

Or manually:

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

It auto-detects Ollama locally. Or use `/model` to add any OpenAI-compatible endpoint.

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

---

## Core Features

### Persistent Memory
Every project gets a `.kognisant/context.md` file that the AI reads on startup and updates after significant work. Global skills in `~/.kognisant_core/skills/` carry knowledge across all projects. Teach it once, reuse forever.

### Multi-Model Support
Ollama, llama.cpp, OpenAI, DeepSeek, Groq, NVidia, Kimi, Nebius, or any OpenAI-compatible endpoint. Switch mid-session with `/model`. The system tracks per-model reliability and auto-switches on failures.

### Autonomous Agents
The `/agent` command dispatches a multi-agent swarm that plans, executes in parallel, reflects on outcomes, and persists learnings. Complex tasks like "research X and write Y" are auto-detected and delegated to the swarm without manual intervention.

### Background Daemon
A POSIX daemon (Linux/macOS) runs persistent services, cron jobs, and one-shot AI tasks without an open terminal. Crash recovery, atomic writes, and log rotation included.

### Self-Building Tools
When the AI encounters a task beyond its built-in toolkit, it creates new tools (JSON schema + Python implementation) stored globally. Available in all future sessions automatically.

### Reasoning Display
Models that support reasoning (gemma4, deepseek-r1, qwen3) stream their thinking in real-time. You see exactly how the AI is working through your request.

---

## Advanced Features

These are documented in detail in [`docs/`](docs/):

- **World Model** - Living dependency graph of your codebase with confidence-tracked knowledge, goal generation, and graduated autonomy
- **Reflection Engine** - HOT (every turn), WARM (every 3rd), COLD (every 20th) health assessment with valence tracking
- **Circuit Breakers** - Per-model failure detection (5 failures in 30s opens the breaker, 30s cooldown)
- **Token Calibration** - Per-model correction factors that improve accuracy over time
- **Dynamic Escalation** - Automatic detection of multi-step tasks and delegation to the agent swarm
- **Spec-Driven Development** - Structured requirements, design, and task documents that agents execute against
- **Telemetry** - Per-execution recording with `/telemetry` command for stats

---

## Commands

### CLI

```bash
kognisant init          # Initialize project memory
kognisant chat          # Start interactive session
kognisant setup         # Configure model providers
kognisant status        # Workspace health check
kognisant spec <name>   # Feature specification workflow
kognisant daemon start  # Start background daemon
kognisant job add       # Schedule a job
```

### Chat Slash Commands

| Command | What It Does |
|---------|-------------|
| `/help` | All commands |
| `/model` | Switch or add models |
| `/agent <task>` | Dispatch autonomous agent swarm |
| `/read <path>` | Load file into context |
| `/files` | List project files |
| `/context` | Show project memory |
| `/thinking` | Review AI reasoning |
| `/telemetry` | Execution statistics |
| `/goals` | World Model improvement goals |
| `/paste` | Multi-line input mode |
| `/spec` | Spec-Driven Development |

---

## Project Structure

```
cli-kognisant/
├── cli_kognisant/     # Source modules
├── tests/             # 1000+ pytest tests
├── docs/              # Technical documentation
├── pyproject.toml     # Zero-dependency build config
└── install.sh         # One-liner installer
```

Full architecture details: [`docs/developer/architecture.md`](docs/developer/architecture.md)

---

## Documentation

| Document | Content |
|----------|---------|
| [User Manual](docs/user/user_manual.md) | Complete usage guide |
| [Architecture](docs/developer/architecture.md) | System design and module responsibilities |
| [Execution Engine](docs/developer/execution-engine.md) | Atomic writes, recovery, schema versioning |
| [Security](docs/developer/security.md) | Sandboxing, permissions, containment |
| [CLI Reference](docs/developer/cli-reference.md) | All commands with flags and exit codes |
| [World Model](docs/developer/world-model.md) | Dependency graph, goals, graduated autonomy |
| [Job Lifecycle](docs/developer/job-lifecycle.md) | Daemon, scheduling, crash recovery |

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

MIT
