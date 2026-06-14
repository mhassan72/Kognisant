# Kognisant 🧠

**Your AI remembers everything. Across every project. Forever.**

[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
![Project Status](https://img.shields.io/badge/status-active-success.svg)
![Dependencies](https://img.shields.io/badge/dependencies-0-brightgreen.svg)

Kognisant is a terminal-native AI assistant with persistent memory, self-building tools, and autonomous background execution. It connects to any LLM — local or cloud — and gets smarter the more you use it.

Most AI CLI tools give you a stateless chat session. You explain your project structure every time. You re-teach patterns. You lose context between sessions. Kognisant solves this with a two-layer memory architecture that gives the AI a continuous understanding of your environment — a world model that persists, compounds, and transfers across everything you work on.

It's not limited to code. Research, writing, planning, automation, web browsing — Kognisant handles any structured work from your terminal.

---

## Why Kognisant?

### It remembers

Every project gets a local memory layer (`.kognisant/context.md`) that tracks architecture decisions, milestones, and build context. A global memory layer (`~/.kognisant_core/`) carries transferable knowledge — coding standards, learned patterns, custom tools — across all your projects. The AI reads both on startup. No re-explaining.

### It builds its own tools

When Kognisant encounters a task it can't handle with its built-in toolkit, it can create new tools on the spot — writing both the schema and implementation — and store them globally. Next time it (or you) needs that capability, it's already there. Tools compound. Skills compound. The system gets more capable over time without you installing anything.

### It works while you sleep

A production-hardened background daemon runs scripts, cron jobs, monitoring tasks, and AI agent work autonomously. Crash recovery, atomic writes, and schema versioning keep it reliable. Schedule a nightly test suite, a persistent bot, or a one-shot AI research task — Kognisant executes it without an open terminal.

### It's not just for code

Web search, headless page browsing, browser console capture, desktop browser control — Kognisant's toolkit makes it useful for research, documentation, planning, and general knowledge work. The PERP agent (Plan → Execute → Reflect → Persist) can break down and complete any structured task, not just programming.

### Zero dependencies. Zero lock-in.

Built entirely on the Python 3.10+ standard library. No `pip install` dependency tree. No vendor lock-in — switch between Ollama, Llama.cpp, OpenAI, DeepSeek, Groq, or any OpenAI-compatible endpoint mid-session. Your data stays local.

---

## 📑 Table of Contents

- [How It Works](#how-it-works)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Memory Architecture](#memory-architecture)
- [Self-Building Tools & Skills](#self-building-tools--skills)
- [Autonomous Agent (PERP Swarm)](#autonomous-agent-perp-swarm)
- [Background Daemon & Job Scheduling](#background-daemon--job-scheduling)
- [Slash Commands](#slash-commands)
- [Built-in Toolkit](#built-in-toolkit)
- [Project Structure](#project-structure)
- [Security](#security)
- [Troubleshooting](#troubleshooting)
- [Developer Documentation](#developer-documentation)
- [Contributing](#contributing)
- [About](#about)
- [License](#license)

---

## How It Works

```
┌─────────────────────────────────────────────────────────────┐
│  YOU (terminal)                                              │
│  ├── kognisant chat     → Interactive AI session            │
│  ├── kognisant init     → Initialize project memory         │
│  ├── kognisant spec     → Structured feature planning       │
│  ├── kognisant daemon   → Background execution engine       │
│  └── kognisant job      → Schedule & manage autonomous work │
└──────────────────────────────┬──────────────────────────────┘
                               │
              ┌────────────────▼────────────────┐
              │  KOGNISANT RUNTIME               │
              │  ├── Any LLM (local or cloud)    │
              │  ├── Tool execution sandbox      │
              │  ├── PERP agent swarm            │
              │  └── Background daemon           │
              └────────────────┬────────────────┘
                               │
         ┌─────────────────────▼─────────────────────┐
         │  MEMORY (Membrain)                         │
         │  ├── Local: .kognisant/context.md          │
         │  │   (project architecture, decisions)     │
         │  └── Global: ~/.kognisant_core/            │
         │      ├── skills/  (transferable knowledge) │
         │      ├── tools/   (self-built capabilities)│
         │      └── scripts/ (autonomous executables) │
         └───────────────────────────────────────────┘
```

On every session start, Kognisant loads your project's local memory and all global skills into context. The AI knows where it is, what it's working on, and what it's learned before — without you saying a word.

---

## Installation

Requires **Python 3.10+**. Nothing else.

### One-liner (recommended)

```bash
curl -fsSL https://raw.githubusercontent.com/mhassan72/Kognisant/main/install.sh | sh
```

The script checks your Python version, clones the repo, and installs the `kognisant` command. Run it again to update.

### Manual install

```bash
pip install git+https://github.com/mhassan72/Kognisant.git
```

### From source (for contributors)

```bash
git clone https://github.com/mhassan72/Kognisant.git
cd Kognisant
pip install -e .
```

> **Platform note:** The background daemon requires POSIX (Linux or macOS). Chat and all other features work on any Python 3.10+ platform.

---

## Quick Start

### 1. Initialize a project

```bash
cd your-project
kognisant init
```

Creates `.kognisant/` with a `context.md` memory file, config, and session history folder. Registers the project in your global registry.

### 2. Start chatting

```bash
kognisant chat
```

Select a model (or let it auto-detect Ollama locally), and start working. The AI has full context of your project files and memory from the first message.

### 3. Let the agent handle it

Inside chat:

```
/agent refactor the authentication module to use JWT tokens
```

The PERP swarm plans the work, dispatches parallel execution agents, reflects on the results, and persists learnings back to your project memory.

### 4. Plan a feature with specs

```bash
kognisant spec user-authentication
```

Generates structured requirements, design, and task documents. The agent can then execute against the spec with full traceability.

---

## Memory Architecture

Kognisant's memory is what makes it fundamentally different from a stateless chat session.

### Local Memory (per-project)

```
.kognisant/
├── config.json      # Workspace name, file exclusions
├── context.md       # Persistent build memory (the AI reads and writes this)
├── history/         # Session logs for continuity
└── specs/           # Feature specifications (SDD)
```

The `context.md` file is your project's living knowledge base. The AI reads it on startup and updates it after significant work (via the PERP persist phase). It contains architecture decisions, implementation notes, and anything the AI learns about your project.

### Global Memory (cross-project)

```
~/.kognisant_core/
├── projects.json       # Registry of all your workspaces
├── models_pool.json    # Configured providers & sticky default model
├── skills/             # Transferable markdown knowledge files
│   ├── coding_standards.md
│   ├── web_browser_steering.md
│   └── global_tool_development.md
├── tools/              # Self-built tool schemas + implementations
│   ├── search_web.json + .py
│   ├── browse_web_page.json + .py
│   └── (any tools the AI creates for you)
├── scripts/            # Autonomous executable scripts + metadata
└── logs/               # Daemon execution logs
```

Global skills are injected into every session. When the AI learns a pattern — how you like your tests structured, how to handle your API conventions, how to use a tool it built — that knowledge persists globally and improves every future session across all projects.

---

## Self-Building Tools & Skills

This is where Kognisant diverges from conventional AI assistants.

### Dynamic Tool Creation

When you ask Kognisant to do something beyond its built-in toolkit, it can build a new tool:

1. Creates a JSON schema file (`~/.kognisant_core/tools/tool_name.json`) defining the tool's interface
2. Creates a Python implementation (`~/.kognisant_core/tools/tool_name.py`) that runs as an isolated subprocess
3. The tool is immediately available in the current and all future sessions

The AI follows a strict development contract: tools must use only Python stdlib, accept arguments via `sys.argv[1]` as JSON, and output results to stdout. This ensures portability and safety.

### Transferable Skills

Skills are markdown documents in `~/.kognisant_core/skills/` that steer the AI's behavior. They're loaded into every session's system prompt. Examples that ship by default:

- **coding_standards.md** — Your code style preferences and conventions
- **web_browser_steering.md** — When and how to use browser-based tools
- **global_tool_development.md** — The contract for building new tools

You can create new skills manually, or the AI will create them when it learns something worth remembering. They're your AI's long-term memory.

### Script Factory

Scripts (`~/.kognisant_core/scripts/`) are executable Python files with metadata that can be scheduled as daemon jobs. The AI can:

- Create scripts with `create_script` (atomic two-phase write with rollback)
- Edit them with `edit_script` (sequential find-replace with full rollback on failure)
- Schedule them as persistent services, cron jobs, or one-shot agent tasks

The pipeline: identify a need → write a script → schedule it → it runs autonomously. All from a chat conversation.

---

## Autonomous Agent (PERP Swarm)

The `/agent <task>` command triggers a four-stage autonomous pipeline:

| Stage | What Happens |
|-------|-------------|
| **Plan** | A planning model analyzes the task, project context, and memory. Produces a phased execution strategy with parallelizable subtasks. |
| **Execute** | Subtask agents run in parallel threads (grouped by phase). Each agent has tool access: read, write, create, delete files, browse web. |
| **Reflect** | A reflection model evaluates outcomes against the original intent. If goals aren't met, it generates corrective adjustments and loops back (up to 2 correction cycles). |
| **Persist** | Successful outcomes and learnings are written back to `context.md`. The project's memory grows. |

The swarm features:
- Dynamic capability routing (cloud models for planning, local models for tasks)
- CPU-aware concurrency throttling for local models
- Thread-safe pause/resume/stop controls
- Spec-Driven Development integration (executes against formal specs when available)
- Tool calls are sandboxed to project root with symlink protection

---

## Background Daemon & Job Scheduling

A forked POSIX daemon that runs work without an open terminal.

### Daemon Control

```bash
kognisant daemon start      # Fork to background
kognisant daemon stop       # Graceful shutdown (SIGTERM → 10s → SIGKILL)
kognisant daemon restart    # Stop + start
kognisant daemon status     # PID, uptime, active jobs
```

### Job Types

| Type | Behavior |
|------|----------|
| `persistent` | Always-on service. Restarts on crash. `exit(0)` = intentional stop. |
| `scheduled` | Cron-based execution (UTC, 5-field format). Supports `*/15 * * * *`, ranges, steps. |
| `agent` | One-shot AI task dispatched to the PERP swarm. |

### Job Management

```bash
kognisant job add --name my-bot --script bot.py --type persistent
kognisant job add --name nightly --script tests.py --type scheduled --cron "0 2 * * *"
kognisant job list
kognisant job logs <name> --follow
kognisant job edit <name> --cron "0 3 * * *"
kognisant job remove <name>
```

### Reliability

- Atomic write sequence: tempfile → fsync → rename → backup
- Auto-recovery from `jobs.json.bak` on corruption
- PID reuse detection prevents killing unrelated processes
- Schema versioning with forward migration
- Scheduler policies: `skip` (default) or `catchup_once` for clock jumps

---

## Slash Commands

Available during `kognisant chat`:

| Command | Description |
|---------|-------------|
| `/help` | Show all commands |
| `/clear` | Reset conversation (preserves system prompt) |
| `/context` | Display project memory |
| `/skills` | List loaded global skills |
| `/model` | Switch model or add a new endpoint |
| `/providers` | Inspect configured providers and API key status |
| `/files` | List workspace files |
| `/read <path>` | Load a file into conversation context |
| `/agent <task>` | Dispatch the PERP swarm |
| `/daemon stop` | Stop the background daemon |
| `/daemon restart` | Restart the daemon |
| `/job stop` | Stop a running job |
| `/job remove` | Remove a job |
| `/job restart` | Restart a job |

---

## Built-in Toolkit

Tools available to the AI during chat and agent execution:

### Workspace Operations
- `read_project_file` — Read any project file (sandboxed to project root)
- `create_project_file` — Create new files
- `create_project_directory` — Create directories
- `edit_project_file` — Precise find-and-replace edits
- `delete_project_path` — Remove files/directories
- `list_project_files` — Full workspace file tree

### Web & Research
- `search_web` — Headless DuckDuckGo search (results returned in-chat)
- `browse_web_page` — Fetch and clean any URL (headless Chrome/Brave with JS rendering, or urllib fallback)
- `open_in_native_browser` — Open URLs in your desktop browser
- `capture_active_browser_console` — Read Chrome/Brave developer console logs

### Global Assets
- `read_global_file` / `create_global_file` / `edit_global_file` — Manage skills and tools in `~/.kognisant_core/`
- `create_script` / `read_script` / `edit_script` / `delete_script` / `list_scripts` — Script CRUD with atomic writes

### Job Management
- `schedule_job` / `cancel_job` / `remove_job` / `list_jobs` / `job_logs` — Full job lifecycle from within chat

---

## Project Structure

```text
cli-kognisant/
├── pyproject.toml                 # Build system & metadata
├── README.md
├── docs/
│   ├── developer/                 # Technical docs for contributors
│   │   ├── architecture.md
│   │   ├── execution-engine.md
│   │   ├── job-lifecycle.md
│   │   ├── security.md
│   │   ├── testing.md
│   │   ├── cli-reference.md
│   │   └── cron-scheduling.md
│   └── user/                      # End-user documentation
│       ├── user_manual.md
│       └── user_journeys.md
├── cli_kognisant/
│   ├── main.py                    # CLI entry point (argparse)
│   ├── config.py                  # Memory, providers, global core init
│   ├── chat.py                    # Interactive loop, slash commands, rollback
│   ├── agents.py                  # PERP swarm orchestration
│   ├── network.py                 # API client with retry & backoff
│   ├── tools.py                   # Tool specs & execution sandbox
│   ├── daemon.py                  # Background daemon engine
│   ├── jobs.py                    # Job queue, cron parser, atomic writes
│   ├── scripts.py                 # Script CRUD with symlink containment
│   ├── sdd.py                     # Spec-Driven Development
│   └── colors.py                  # ANSI rendering, spinners, logo
└── tests/                         # pytest suite
```

---

## Security

- **Sandbox enforcement** — All file operations resolve through `os.path.realpath` and are verified against the project root. No directory traversal, no symlink escapes.
- **No temp/staging files** — Agents edit target files directly. No orphaned drafts.
- **API key isolation** — Keys stored locally in `~/.kognisant_core/`, never hardcoded or leaked.
- **Atomic writes** — Job state, scripts, and config use tempfile → rename patterns to prevent corruption.
- **File permissions** — `jobs.json` and sensitive files protected with `chmod 600`.
- **Subprocess isolation** — Dynamic tools run as isolated subprocesses with captured stdout.

---

## Troubleshooting

| Symptom | Resolution |
|---------|-----------|
| `Connection refused` on Ollama model | Start Ollama: `ollama serve` (default: `http://localhost:11434`) |
| `API HTTP Error 401` | Replace placeholder API key via `/model` in chat |
| `No active project detected` | Run `kognisant init` in your project root |
| Rollback / "API Transport Failure" | Transient network error. Session auto-reverts to checkpoint. Retry. |
| Python `SyntaxError` on launch | Requires Python 3.10+ |
| Daemon won't start | POSIX only (Linux/macOS). Check `kognisant daemon status`. |

---

## Developer Documentation

In-depth technical documentation lives in [`docs/developer/`](docs/developer/):

- **[Architecture](docs/developer/architecture.md)** — System design, module responsibilities, threading model
- **[Execution Engine](docs/developer/execution-engine.md)** — Atomic writes, recovery, schema versioning, clock jump handling
- **[Job Lifecycle](docs/developer/job-lifecycle.md)** — State machine, execution flows, graceful shutdown
- **[Security](docs/developer/security.md)** — Containment, permissions, traversal protection
- **[Testing](docs/developer/testing.md)** — Test structure, fixtures, coverage strategy
- **[CLI Reference](docs/developer/cli-reference.md)** — Complete command reference with flags and exit codes
- **[Cron Scheduling](docs/developer/cron-scheduling.md)** — Parser internals, UTC evaluation, clock jump handling

---

## Contributing

Contributions, issues, and feature requests are welcome.

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/your-feature`)
3. Commit your changes
4. Push and open a Pull Request

One rule: stay within the **Python 3.10+ standard library**. Zero external dependencies is a core design principle.

---

## About

Kognisant is a free, open-source project built by a developer in **Mogadishu, Somalia**. It exists because AI tooling should be accessible, portable, and private — not locked behind subscriptions, bloated dependency trees, or proprietary ecosystems.

The goal is simple: give every developer a capable AI partner that learns, adapts, and works autonomously — right from their terminal.

---

## License

MIT License. See `pyproject.toml`.
