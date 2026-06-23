# Getting Started with Kognisant

Welcome to Kognisant, a terminal-native AI assistant that remembers your projects, runs autonomous agents, and works with any LLM. This guide walks you through installation, first-time setup, and your first chat session.

---

## Why Kognisant?

Most AI coding tools forget everything the moment you close the session. Kognisant is different: it builds persistent memory per project, carries global skills across all your work, and gets smarter over time. You explain your architecture once, and it remembers.

---

## Installation

There are three ways to install Kognisant. Pick whichever fits your workflow.

### Method 1: One-liner install script (recommended)

```bash
curl -fsSL https://raw.githubusercontent.com/mhassan72/Kognisant/main/install.sh | sh
```

This downloads and installs the latest version using pip under the hood.

### Method 2: Install from Git directly

```bash
pip install git+https://github.com/mhassan72/Kognisant.git
```

### Method 3: Clone and install in development mode

```bash
git clone https://github.com/mhassan72/Kognisant.git
cd Kognisant
pip install -e .
```

This is ideal if you want to contribute or inspect the source.

### Requirements

- Python 3.10 or newer (3.12 recommended)
- macOS or Linux (POSIX-compliant)
- No external dependencies required (zero-dep, standard library only)
- Optional: Chrome or Brave Browser for headless web browsing features

Verify the installation:

```bash
kognisant --help
```

You should see the available subcommands: `init`, `chat`, `setup`, `status`, `spec`, `daemon`, `job`.

---

## First Project Initialization

Navigate to any project directory and initialize Kognisant's local memory:

```bash
cd ~/projects/my-app
kognisant init
```

This creates the `.kognisant/` directory in your project root containing:

| File/Folder | Purpose |
|:---|:---|
| `config.json` | Workspace-specific settings and file exclusion patterns |
| `context.md` | The project "brain" - milestones, active tasks, architecture notes |
| `memory-guidlines.md` | Rules governing how agents update memory |
| `history/` | Session transcripts for continuity |
| `specs/` | Feature specification documents (for Spec-Driven Development) |

Your project is also registered in the global registry (`~/.kognisant_core/projects.json`) so Kognisant always knows your active workspaces.

---

## Choosing and Configuring a Model

Before chatting, you need at least one AI model configured. Run the setup wizard:

```bash
kognisant setup
```

You will see an interactive menu:

```
How would you like to connect?

  [1] 🏠 Local Model (Ollama)        - Free, private
  [2] 🏠 Local Model (Llama.cpp)     - Free, point to a running server
  [3] ☁️  Cloud API (OpenAI)          - Requires API key
  [4] ☁️  Cloud API (Groq)            - Requires API key, fast inference
  [5] ☁️  Cloud API (DeepSeek)        - Requires API key, affordable
  [6] ☁️  Cloud API (Custom endpoint) - Any OpenAI-compatible server
  [7] 🔌 Skip - I'll configure later
```

**For local models (Ollama):** Make sure Ollama is running (`ollama serve`) and has at least one model pulled (`ollama pull gemma3:1b`). Kognisant auto-detects available models.

**For cloud providers:** Enter your API key when prompted. Kognisant tests the connection before confirming.

Your model configuration is saved to `~/.kognisant_core/models_pool.json` and persists across all sessions.

If you skip setup now, the wizard will appear automatically when you first run `kognisant chat`.

---

## Your First Chat Session

Start a session:

```bash
kognisant chat
```

On startup, you will see:

```
⚡ gemma3:1b | valence: +0 | 3 skills, 4 tools
```

This status line tells you:
- Which model is active
- The current valence score (system mood, from -100 to +100)
- How many skills and tools are loaded

Type a message and press Enter:

```
You > what files are in this project?
```

Kognisant responds with awareness of your project structure because it loaded `context.md` and your file listing on startup.

### Try these first commands:

```
/help          Show all available commands
/files         List project files
/context       View the project memory
/read src/app.py   Load a specific file into the conversation
```

---

## Understanding the Output

Every response goes through a 5-phase pipeline. Here is what the emoji status lines mean:

```
⚡ gemma4:latest | valence: +22 | 8 skills, 4 tools
```
**Bootstrap phase** - Model selected, capabilities loaded, system healthy.

```
📋 COMPLEX → ~2,100 tokens input (sys: 800 + tools: 600 + hist: 400 + msg: 300)
```
**Plan phase** - Message classified. The classification determines how much context and which tools are included:
- `SIMPLE` - Quick factual answers, minimal context
- `CONTEXT` - Needs project awareness, loads memory
- `COMPLEX` - Needs tools and full project context
- `AUTONOMOUS` - Delegated to the multi-agent swarm

```
💭 Thinking...
```
**Execute phase** - The model is generating a response. If it supports reasoning, you will see numbered thinking steps.

```
🔍 18.2s | 2,100 in > 420 out | valence: +27 (+5) | 3 tool(s)
```
**Reflect phase** - Execution complete. Shows timing, token usage, valence change, and tool call count.

---

## Next Steps

Now that you are set up, here are the features to explore:

| Want to... | Go to |
|:---|:---|
| Understand the memory system | [Persistent Memory](persistent-memory.md) |
| Configure multiple models | [Models and Providers](models-and-providers.md) |
| Run autonomous multi-step tasks | [Autonomous Agents](autonomous-agents.md) |
| Schedule background jobs | [Background Daemon](background-daemon.md) |
| Learn all slash commands | [Slash Commands](slash-commands.md) |
| Plan features methodically | [Spec-Driven Development](spec-driven-development.md) |

---

## Quick Health Check

At any time, verify your setup is working:

```bash
kognisant status
```

This shows workspace state, model connectivity, provider health, active specs, and daemon status all in one view.

```
  Kognisant v0.1.0

  Workspace:    ./my-app (.kognisant/ ✅)
  Global Core:  ~/.kognisant_core/ ✅
    Skills: 3 loaded
    Tools:  4 registered

  Active Model: gemma3:1b (Ollama) 🟢 Reachable

  Providers:
    Ollama     🟢 Reachable
      • gemma3:1b

  Daemon:
    State:        stopped
```

If any provider shows 🟡 or 🔴, check your API key or that the local server is running.
