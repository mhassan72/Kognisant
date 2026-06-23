# Kognisant

**Your AI remembers everything. Across every project. Forever.**

[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
![Project Status](https://img.shields.io/badge/status-active-success.svg)
![Dependencies](https://img.shields.io/badge/dependencies-0-brightgreen.svg)

Kognisant is a terminal-native AI assistant with persistent memory, a 5-phase cognitive runtime, self-building tools, and autonomous background execution. It connects to any LLM (local or cloud) and gets smarter the more you use it.

Most AI CLI tools give you a stateless chat session. You explain your project structure every time. You re-teach patterns. You lose context between sessions. Kognisant solves this with a two-layer memory architecture that gives the AI a continuous understanding of your environment: a world model that persists, compounds, and transfers across everything you work on.

It is not limited to code. Research, writing, planning, automation, web browsing - Kognisant handles any structured work from your terminal.

---

## Why Kognisant?

### It thinks transparently

Every execution runs through a 5-phase cognitive lifecycle: Bootstrap, Plan, Execute, Reflect, Persist. You see exactly what the system is doing at every step: which model is active, how it classified your message, how many tokens it used, and what it learned. Reasoning models (gemma4, deepseek-r1, qwen3) stream their thinking process in real-time so you can watch the AI reason through your request.

### It remembers

Every project gets a local memory layer (`.kognisant/context.md`) that tracks architecture decisions, milestones, and build context. A global memory layer (`~/.kognisant_core/`) carries transferable knowledge (coding standards, learned patterns, custom tools) across all your projects. The AI reads both on startup. No re-explaining.

### It builds its own tools

When Kognisant encounters a task it cannot handle with its built-in toolkit, it can create new tools on the spot, writing both the schema and implementation, and store them globally. Next time it (or you) needs that capability, it is already there. Tools compound. Skills compound. The system gets more capable over time without you installing anything.

### It works while you sleep

A production-hardened background daemon runs scripts, cron jobs, monitoring tasks, and AI agent work autonomously. Crash recovery, atomic writes, and schema versioning keep it reliable. Schedule a nightly test suite, a persistent bot, or a one-shot AI research task. Kognisant executes it without an open terminal.

### It understands your codebase

The World Model builds a living dependency graph of your project. It tracks function calls, imports, class hierarchies, and test outcomes with confidence scores. When code changes, it detects what went stale. When patterns break, it suggests fixes. When coverage gaps grow, it flags them. The system learns from your responses (accept or dismiss) and calibrates its suggestions over time, graduating from "ask every time" to "handle it automatically" as trust builds.

### It escalates intelligently

Simple greetings get a fast 30-second response. Complex tasks with tools get a 2-minute window. Multi-step autonomous work (research + analysis + writing) is automatically detected and delegated to the PERP agent swarm with no manual intervention. The system classifies every message and routes it to the right execution mode.

### Zero dependencies. Zero lock-in.

Built entirely on the Python 3.10+ standard library. No bloated dependency tree. No vendor lock-in. Switch between Ollama, Llama.cpp, OpenAI, DeepSeek, Groq, NVidia, Kimi, or any OpenAI-compatible endpoint mid-session. Your data stays local.

---

## Table of Contents

- [How It Works](#how-it-works)
- [The 5-Phase Runtime](#the-5-phase-runtime)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Memory Architecture](#memory-architecture)
- [Self-Building Tools and Skills](#self-building-tools-and-skills)
- [Autonomous Agent (PERP Swarm)](#autonomous-agent-perp-swarm)
- [Dynamic Agent Escalation](#dynamic-agent-escalation)
- [Background Daemon and Job Scheduling](#background-daemon-and-job-scheduling)
- [World Model](#world-model)
- [Model Pool and Selection](#model-pool-and-selection)
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
│  ├── kognisant chat     > Interactive AI session            │
│  ├── kognisant init     > Initialize project memory         │
│  ├── kognisant spec     > Structured feature planning       │
│  ├── kognisant daemon   > Background execution engine       │
│  └── kognisant job      > Schedule & manage autonomous work │
└──────────────────────────────┬──────────────────────────────┘
                               │
              ┌────────────────▼────────────────┐
              │  KOGNISANT RUNTIME               │
              │  ├── 5-Phase Lifecycle           │
              │  ├── FastPath Classifier         │
              │  ├── Any LLM (local or cloud)    │
              │  ├── Tool execution sandbox      │
              │  ├── PERP agent swarm            │
              │  ├── SelfModel (cognitive state) │
              │  └── Background daemon           │
              └────────────────┬────────────────┘
                               │
         ┌─────────────────────▼─────────────────────┐
         │  MEMORY (Membrain)                         │
         │  ├── Local: .kognisant/context.md          │
         │  │   (project architecture, decisions)     │
         │  ├── Global: ~/.kognisant_core/            │
         │  │   ├── skills/  (transferable knowledge) │
         │  │   ├── tools/   (self-built capabilities)│
         │  │   ├── scripts/ (autonomous executables) │
         │  │   ├── self_model.json (cognitive state) │
         │  │   └── telemetry.jsonl (execution log)   │
         │  └── Thinking: session_*_thinking.json     │
         │      (reasoning traces per session)        │
         └───────────────────────────────────────────┘
```

On every session start, Kognisant loads your project's local memory and all global skills into context. The AI knows where it is, what it's working on, and what it has learned before. You do not have to say a word.

---

## The 5-Phase Runtime

Every non-slash user message passes through exactly 5 phases:

```
User presses Enter
    │
    ▼
⚡ BOOTSTRAP - Load cognitive state, select model, check circuit breakers
    │           Print: model name, valence, capabilities
    ▼
📋 PLAN     - Classify message (SIMPLE/CONTEXT/COMPLEX/AUTONOMOUS)
    │           Build system prompt, set timeout, estimate tokens
    ▼
⚙️  EXECUTE  - Stream LLM response with sub-state tracking
    │           Handle tool calls (animated boxes), retry on failure
    ▼
🔍 REFLECT  - Update valence, model reliability, tool reliability
    │           HOT (every turn), WARM (every 3rd), COLD (every 20th)
    ▼
💾 PERSIST  - Atomic save of cognitive state
```

### Classification Tiers

| Tier | Criteria | Behavior |
|------|----------|----------|
| SIMPLE | Short greeting, no tools needed | Minimal prompt, 30s timeout (local: 120s) |
| CONTEXT | Question about project, explanation | Project context included, 60s timeout (local: 180s) |
| COMPLEX | Action verb + file/code, 1-3 tool calls | Full prompt + tools, 120s timeout (local: 300s) |
| AUTONOMOUS | Multi-step task (research + write) | Auto-escalates to PERP agent swarm |

### Reasoning Display

Models that support reasoning (gemma4, deepseek-r1, qwen3) stream their thinking process in real-time:

```
💭 Thinking...
  1. Analyze the request: The user wants to refactor auth.
  2. I need to read the current auth module first.
  3. The JWT library is already imported but unused.
💭 Thought for 8.3s

Kognisant >
Here is the refactored authentication module...
```

Thinking is saved per-session in a separate file and never pollutes the chat context window. Use `/thinking` to review past reasoning.

### SelfModel (Cognitive State)

The SelfModel persists between sessions and tracks:
- **Valence** (-100 to +100): overall system health score
- **Model reliability**: Bayesian confidence per model (successes/failures)
- **Tool reliability**: per-tool success rates
- **Circuit breakers**: per-model (5 failures in 30s opens the breaker)
- **Token calibration**: per-model correction factor for estimates
- **Capabilities**: reasoning, tool_calling (learned dynamically)

### Retry Strategy

Every execution gets up to 3 attempts before reporting failure:
1. Normal streaming request with classification timeout
2. Retry with 2x extended timeout
3. Final retry with maximum timeout (300s local, 120s remote)

Retries only trigger for timeouts and empty responses. Auth errors (401, 402, 403, 429) fail immediately.

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
├── history/         # Session logs + thinking traces
└── specs/           # Feature specifications (SDD)
```

The `context.md` file is your project's living knowledge base. The AI reads it on startup and updates it after significant work (via the PERP persist phase). It contains architecture decisions, implementation notes, and anything the AI learns about your project.

### Global Memory (cross-project)

```
~/.kognisant_core/
├── projects.json       # Registry of all your workspaces
├── models_pool.json    # Configured providers with capabilities
├── self_model.json     # Cognitive state (valence, reliability, circuit breakers)
├── telemetry.jsonl     # Per-execution log (append-only, rotates at 5MB)
├── skills/             # Transferable markdown knowledge files
├── tools/              # Self-built tool schemas + implementations
├── scripts/            # Autonomous executable scripts + metadata
└── logs/               # Daemon execution logs
```

Global skills are injected into every session. When the AI learns a pattern, that knowledge persists globally and improves every future session across all projects.

---

## Self-Building Tools and Skills

### Dynamic Tool Creation

When you ask Kognisant to do something beyond its built-in toolkit, it can build a new tool:

1. Creates a JSON schema file (`~/.kognisant_core/tools/tool_name.json`) defining the tool's interface
2. Creates a Python implementation (`~/.kognisant_core/tools/tool_name.py`) that runs as an isolated subprocess
3. The tool is immediately available in the current and all future sessions

The AI follows a strict development contract: tools must use only Python stdlib, accept arguments via `sys.argv[1]` as JSON, and output results to stdout.

### Transferable Skills

Skills are markdown documents in `~/.kognisant_core/skills/` that steer the AI's behavior. They are loaded into every session's system prompt. You can create new skills manually, or the AI will create them when it learns something worth remembering.

### Script Factory

Scripts (`~/.kognisant_core/scripts/`) are executable Python files with metadata that can be scheduled as daemon jobs. The AI can create scripts, edit them, and schedule them as persistent services, cron jobs, or one-shot agent tasks. All from a chat conversation.

---

## Autonomous Agent (PERP Swarm)

The `/agent <task>` command triggers a four-stage autonomous pipeline:

| Stage | What Happens |
|-------|-------------|
| **Plan** | A reasoning-capable model analyzes the task. Produces a phased execution strategy with parallelizable subtasks. |
| **Execute** | Subtask agents run in parallel threads (grouped by phase). Each agent has full tool access. |
| **Reflect** | A reflection model evaluates outcomes. If goals are not met, it generates corrective adjustments (up to 2 correction cycles). |
| **Persist** | Successful outcomes and learnings are written back to `context.md`. |

The swarm features:
- Dynamic capability-based model selection (reasoning models for planning, any model for tasks)
- Cascading fallback on model failure (402/403/429 tries the next model, never gives up)
- Per-worker token tracking with swarm completion summary
- CPU-aware concurrency throttling for local models
- Thread-safe pause/resume/stop controls
- Spec-Driven Development integration
- Artifact tracking (lists all created/modified files on completion)

---

## Dynamic Agent Escalation

The runtime automatically detects when a task is too complex for single-model chat and escalates to the PERP swarm without user intervention.

**Detection triggers:**
- Multiple distinct phases: research + analysis + creation
- URL + creation intent (browse X then write Y)
- Multi-output markers ("write an article", "create a report")
- Post-exhaustion: 3+ tool rounds used with no content produced

```
You > write an article comparing Kognisant to other AI systems
⚡ gemma4:latest | valence: +12 | 15 skills, 6 tools
📋 AUTONOMOUS -> delegating to agent swarm
  Multi-phase task detected: research + analysis + creation

  🐝 Delegating to agent swarm...
  [Running in background - /status to monitor, /stop to cancel]
```

The user stays at the chat prompt and can continue chatting while the swarm works in the background. On completion, artifacts are listed with their file paths.

---

## Background Daemon and Job Scheduling

A forked POSIX daemon that runs work without an open terminal.

### Daemon Control

```bash
kognisant daemon start      # Fork to background
kognisant daemon stop       # Graceful shutdown (SIGTERM, wait 10s, then SIGKILL)
kognisant daemon restart    # Stop + start
kognisant daemon status     # PID, uptime, active jobs
```

### Job Types

| Type | Behavior |
|------|----------|
| `persistent` | Always-on service. Restarts on crash. `exit(0)` means intentional stop. |
| `scheduled` | Cron-based execution (UTC, 5-field format). |
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

- Atomic write sequence: tempfile, fsync, rename, backup
- Auto-recovery from `jobs.json.bak` on corruption
- PID reuse detection prevents killing unrelated processes
- Schema versioning with forward migration
- Crash loop detection (5 restarts in 60s triggers cooldown)

---

## World Model

The World Model is an opt-in subsystem that gives Kognisant deep understanding of your codebase structure and health.

### Enable It

```bash
# From chat:
/worldmodel enable
```

### What It Does

- Traces every PERP execution (tool calls, file ops, LLM calls)
- Builds a dependency graph via AST analysis (functions, classes, imports)
- Tracks confidence scores on every piece of knowledge with provenance
- Detects code changes via git and invalidates stale graph edges
- Monitors test health (rolling history, instability detection)
- Generates improvement goals from 6 strategies
- Learns from your feedback to calibrate future suggestions
- Graduates autonomy from "ask every time" to "auto-execute"

### Goal Types

| Type | What It Detects |
|------|----------------|
| Contract Violation | Function call arguments don't match expected signature |
| Coverage Gap | Module has 4+ untested branches |
| Decay Alert | Knowledge about a module is going stale |
| Complexity | Function too complex with high churn or no tests |
| Stale Artifact | File unmodified 90+ days with low-confidence nodes |
| Pattern Detection | Same error repeated 3+ times in recent executions |

---

## Model Pool and Selection

Kognisant maintains a pool of configured models with dynamically-learned capabilities:

```json
{
  "name": "gemma4:latest",
  "protocol": "ollama",
  "api_base_url": "http://localhost:11434",
  "capabilities": {
    "tool_calling": true,
    "reasoning": true
  }
}
```

### Selection Logic

- **Chat runtime**: Uses the user's active model. Circuit breakers auto-switch on repeated failures.
- **Agent swarm planner**: Selects based on proven reasoning capability (true > unknown > false), sorted by reliability.
- **Agent swarm workers**: Selects based on tool_calling capability.
- **Cascading fallback**: If a model fails with 401/402/403/429/timeout, it is marked unreachable for the session and the next candidate is tried. The system never gives up until all models are exhausted.

### Capability Detection

Capabilities are discovered dynamically:
- First use of a model probes for reasoning (sends `think: true` for Ollama)
- If thinking tokens arrive, `reasoning: true` is persisted
- If no thinking tokens, `reasoning: false` is persisted
- Subsequent requests skip probing and use the known capability

---

## Slash Commands

Available during `kognisant chat`:

| Command | Description |
|---------|-------------|
| `/help` | Show all commands |
| `/clear` | Reset conversation (preserves system prompt) |
| `/context` | Display project memory |
| `/files` | List workspace files |
| `/read <path>` | Load a file into conversation context |
| `/model` | Switch model or add a new endpoint |
| `/agent <task>` | Dispatch the PERP swarm |
| `/telemetry` | Show execution stats (last 50 runs) |
| `/telemetry <model>` | Per-model deep dive stats |
| `/thinking` | Show reasoning for last turn |
| `/thinking N` | Show reasoning for turn N |
| `/thinking list` | Summary of all reasoning in this session |
| `/goals` | List active World Model improvement goals |
| `/goals accept <id>` | Accept a goal for automatic PERP execution |
| `/goals dismiss <id>` | Dismiss a goal (records feedback for learning) |
| `/daemon status` | Check daemon state |
| `/jobs` | List all jobs |
| `/job stop <name>` | Stop a running job |
| `/worldmodel enable` | Enable the World Model |
| `/paste` | Multi-line paste mode |
| `/spec` | Manage Spec-Driven Development workflows |

---

## Built-in Toolkit

Tools available to the AI during chat and agent execution:

### Workspace Operations
- `read_project_file` - Read any project file (sandboxed to project root)
- `create_project_file` - Create new files
- `create_project_directory` - Create directories
- `edit_project_file` - Precise find-and-replace edits
- `delete_project_path` - Remove files/directories
- `list_project_files` - Full workspace file tree

### Web and Research
- `search_web` - Headless DuckDuckGo search
- `browse_web_page` - Fetch and clean any URL (headless Chrome/Brave with JS rendering)
- `open_in_native_browser` - Open URLs in your desktop browser
- `capture_active_browser_console` - Read Chrome/Brave developer console logs

### Global Assets
- `read_global_file` / `create_global_file` / `edit_global_file` - Manage skills and tools
- `create_script` / `read_script` / `edit_script` / `delete_script` / `list_scripts` - Script CRUD

### Job Management
- `schedule_job` / `cancel_job` / `remove_job` / `list_jobs` / `job_logs` - Full job lifecycle

---

## Project Structure

```text
cli-kognisant/
├── pyproject.toml                 # Build system and metadata (zero dependencies)
├── README.md
├── install.sh                     # One-liner installer script
├── docs/
│   ├── developer/                 # Technical docs for contributors
│   └── user/                      # End-user documentation
├── cli_kognisant/
│   ├── main.py                    # CLI entry point (argparse)
│   ├── config.py                  # Memory, providers, global core init
│   ├── chat.py                    # Interactive loop, slash commands
│   ├── runtime.py                 # 5-phase execution lifecycle orchestrator
│   ├── fast_path_classifier.py    # Rule-based SIMPLE/CONTEXT/COMPLEX/AUTONOMOUS
│   ├── self_model_engine.py       # Cognitive state, Bayesian reliability, circuit breakers
│   ├── reflect_engine.py          # HOT/WARM/COLD reflection logic
│   ├── telemetry.py               # Per-execution recording, rotation, /telemetry
│   ├── agents.py                  # PERP swarm orchestration with cascading fallback
│   ├── network.py                 # Streaming API client (thinking tokens, stall detection)
│   ├── tools.py                   # Tool specs and execution sandbox
│   ├── models.py                  # Shared dataclasses (Node, Edge, Goal, etc.)
│   ├── observer.py                # Trace collection, static analysis, git detection
│   ├── world_model.py             # Dependency graph, beliefs, contracts, maintenance
│   ├── world_model_store.py       # JSON-sharded persistence with snapshots
│   ├── goal_engine.py             # Goal generation, ranking, execution, learning
│   ├── daemon.py                  # Background daemon engine
│   ├── jobs.py                    # Job queue, cron parser, atomic writes
│   ├── scripts.py                 # Script CRUD with symlink containment
│   ├── sdd.py                     # Spec-Driven Development
│   └── colors.py                  # ANSI rendering, spinners, markdown rendering
└── tests/                         # pytest suite (1000+ tests)
```

---

## Security

- **Sandbox enforcement** - All file operations resolve through `os.path.realpath` and are verified against the project root. No directory traversal, no symlink escapes.
- **No temp/staging files** - Agents edit target files directly. No orphaned drafts.
- **API key isolation** - Keys stored locally in `~/.kognisant_core/`, never hardcoded or leaked.
- **Atomic writes** - Job state, scripts, and config use tempfile-then-rename patterns.
- **File permissions** - Sensitive files protected with `chmod 600`.
- **Subprocess isolation** - Dynamic tools run as isolated subprocesses with captured stdout.
- **Model config immutability** - The runtime never mutates the user's model configuration dict.

---

## Troubleshooting

| Symptom | Resolution |
|---------|-----------|
| `Connection refused` on Ollama | Start Ollama: `ollama serve` |
| `API HTTP Error 401/402/403` | Replace API key via `/model`, or use a local model |
| `No active project detected` | Run `kognisant init` in your project root |
| Timeout on local model | Model is loading into memory (first request). Wait or use a smaller model. |
| Empty response | Runtime retries 3 times automatically. If all fail, try `/model` to switch. |
| Agent swarm planning failed | All cloud models exhausted. Ensure at least one local model is running. |
| Python `SyntaxError` on launch | Requires Python 3.10+ |
| Daemon won't start | POSIX only (Linux/macOS). Check `kognisant daemon status`. |

---

## Developer Documentation

In-depth technical documentation lives in [`docs/developer/`](docs/developer/):

- [Architecture](docs/developer/architecture.md) - System design, module responsibilities, threading model
- [Execution Engine](docs/developer/execution-engine.md) - Atomic writes, recovery, schema versioning
- [Job Lifecycle](docs/developer/job-lifecycle.md) - State machine, execution flows, graceful shutdown
- [Security](docs/developer/security.md) - Containment, permissions, traversal protection
- [Testing](docs/developer/testing.md) - Test structure, fixtures, coverage strategy
- [CLI Reference](docs/developer/cli-reference.md) - Complete command reference
- [Cron Scheduling](docs/developer/cron-scheduling.md) - Parser internals, UTC evaluation
- [World Model](docs/developer/world-model.md) - Dependency graph, goal engine, graduated autonomy

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

Kognisant is a free, open-source project built by a developer in **Mogadishu, Somalia**.

It exists because AI tooling should be accessible, portable, and private. Not locked behind subscriptions, bloated dependency trees, or proprietary ecosystems.

The goal is simple: give every developer a capable AI partner that learns, adapts, and works autonomously, right from their terminal.

---

## License

MIT License. See `pyproject.toml`.
