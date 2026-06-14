# Kognisant 🧠 — User Journey Scenarios

This guide provides simple, step-by-step stories (journeys) to help you understand exactly how to use Kognisant. Whether you are a professional coder or just starting out, these scenarios show you how to talk to your AI assistant and get things done.

---

## Scenario 1: First-Time Setup
**Goal**: You just installed Kognisant and want to connect it to an AI model.

1.  **Open your Terminal** and run:
    ```bash
    kognisant setup
    ```
2.  **Choose your provider** from the interactive menu:
    ```
    How would you like to connect?

      [1] 🏠 Local Model (Ollama)        — Free, private
      [2] 🏠 Local Model (Llama.cpp)     — Free, point to a running server
      [3] ☁️  Cloud API (OpenAI)          — Requires API key
      [4] ☁️  Cloud API (Groq)            — Requires API key, fast inference
      [5] ☁️  Cloud API (DeepSeek)        — Requires API key, affordable
      [6] ☁️  Cloud API (Custom endpoint) — Any OpenAI-compatible server
      [7] 🔌 Skip — I'll configure later
    ```
3.  **Enter your API key** (for cloud providers) or **select a local model** (for Ollama).
4.  **Kognisant tests the connection** and confirms you're ready.
5.  **You're done!** Your model is saved as the default for all future sessions.

*Note: If you skip setup, Kognisant will trigger this wizard automatically the first time you run `kognisant chat`.*

---

## Scenario 2: The "New Project" Setup
**Goal**: You have a folder of code and want Kognisant to learn it.

1.  **Navigate to your project folder**:
    ```bash
    cd path/to/your/project
    ```
2.  **Initialize Kognisant**:
    ```bash
    kognisant init
    ```
    *What happens?* Kognisant creates a `.kognisant/` folder (its "brain") and shows you next steps:
    ```
    Next steps:
      → kognisant chat       Start an AI conversation about this project
      → kognisant spec <name> Plan a feature with requirements → design → tasks
      → kognisant status     Check your workspace health
    ```
3.  **Start chatting**:
    ```bash
    kognisant chat
    ```
    Kognisant loads your project context automatically and shows session continuity info:
    ```
    📁 Workspace: ./my-app (Active)
    🧠 Membrain loaded (context.md: 5/12 tasks tracked)
    🕐 Last session: 2 hours ago
    ```

---

## Scenario 3: The "Daily Helper" Workflow
**Goal**: You want help understanding or fixing code.

1.  **Start Chat**: `kognisant chat`
2.  **Load a file into context**:
    ```
    /read login.py
    ```
    Kognisant reads the file so it can see the actual code.
3.  **Ask your question**:
    "Can you explain how the password validation works?"
4.  **Ask for a fix**:
    "I'm getting an error on line 10. Can you fix it?"
    You'll see the **PLAN → EXECUTION → RESULT** flow as Kognisant works.

---

## Scenario 4: Planning a Feature with Specs
**Goal**: You want to build a new feature methodically using Spec-Driven Development.

1.  **Create a spec**:
    ```bash
    kognisant spec auth_module
    ```
2.  **Kognisant walks you through the stages interactively**:
    - **DEFINE**: "Describe what you want to build" → AI generates requirements
    - **DESIGN**: AI proposes architecture → you review/approve
    - **PLAN**: AI generates phased implementation tasks
    - **BUILD**: Execute tasks one-by-one or all at once
    - **VERIFY**: Validate against requirements

3.  **Pause and resume anytime**:
    ```bash
    kognisant spec auth_module --resume
    ```
    Kognisant remembers where you left off:
    ```
    🛠️  Spec: auth_module
    Status: BUILD (3/8 tasks done)

    [c] Continue building (auto)
    [n] Execute next task only
    [s] Show full spec
    [q] Save and quit
    ```

4.  **Or manage specs from inside chat**:
    ```
    /spec list                — See all specs with status
    /spec auth_module         — Load context into chat
    /spec auth_module run     — Execute next task
    /spec auth_module run all — Execute all remaining
    /spec auth_module done    — Mark current task complete
    ```

---

## Scenario 5: The Autonomous Agent Swarm
**Goal**: You have a big task and want Kognisant to handle it autonomously.

1.  **Inside chat, use the `/agent` command**:
    ```
    /agent Write a complete test suite for the auth module
    ```
2.  **Watch the PERP swarm work**:
    - **PLAN**: Breaks your request into phases and subtasks
    - **EXECUTE**: Runs tasks in parallel, reading/writing files
    - **REFLECT**: Checks its own work, fixes issues automatically
    - **PERSIST**: Updates your project memory with what was done

3.  **Check what changed**:
    ```
    /context    — See updated project state
    /files      — See new files created
    ```

---

## Scenario 6: Checking System Health
**Goal**: Quickly verify everything is configured correctly.

```bash
kognisant status
```

Output:
```
  Kognisant v0.1.0

  Workspace:    ./my-app (.kognisant/ ✅)
  Global Core:  ~/.kognisant_core/ ✅
    Skills: 3 loaded
    Tools:  4 registered

  Active Model: llama-3.3-70b-versatile (Groq) 🟢 Ready

  Providers:
    Ollama     🟢 Reachable
      • gemma3:1b
    Groq       🟢 Key set
      • llama-3.3-70b-versatile
    OpenAI     🟡 Key needed
      • gpt-4o-mini

  Specs:
    🔨 auth_module  BUILD (3/8)
    ✅ logging      DONE (5/5)
```

---

## Scenario 7: Switching Models Mid-Session
**Goal**: You want to try a different model without restarting.

1.  **Inside chat, type**:
    ```
    /model
    ```
2.  **You see the model pool with health indicators**:
    ```
    📦 Select an AI Model:

      [1] gemma3:1b (Ollama) 🟢
      [2] llama-3.3-70b-versatile (Groq) 🟢 [Active]
      [3] gpt-4o-mini (OpenAI) 🟡
      [a] Add custom provider / model
    ```
3.  **Select a number** to switch instantly, or **press 'a'** to add a new provider with templates (just pick a provider and enter your key — no manual URL entry needed).

---

## Scenario 8: Global Tools & Skills
**Goal**: Register a custom script as a tool usable in any project.

1.  **Inside chat, register your script**:
    ```
    /tool register shrink scripts/shrink.py
    ```
2.  **Kognisant copies it globally** and creates a schema for it.
3.  **Use it anywhere**: Next week, in a different project:
    "Kognisant, use the shrink tool on logo.png."
    It works instantly across all projects.

---

## Quick Reference: Key Commands

| Where | Command | What it does |
|-------|---------|-------------|
| Terminal | `kognisant init` | Initialize project workspace |
| Terminal | `kognisant chat` | Start AI chat session |
| Terminal | `kognisant setup` | Configure AI providers |
| Terminal | `kognisant status` | Check system health |
| Terminal | `kognisant spec <name>` | Create/resume a feature spec |
| Terminal | `kognisant spec --list` | List all specs |
| In Chat | `/help` | Compact command overview |
| In Chat | `/help <cmd>` | Detailed help for a command |
| In Chat | `/files` | List project files |
| In Chat | `/read <path>` | Load file into context |
| In Chat | `/model` | Switch AI models |
| In Chat | `/agent <task>` | Deploy autonomous swarm |
| In Chat | `/spec <name> run` | Execute next spec task |
| In Chat | `/context` | View project memory |
| In Chat | `/clear` | Reset conversation |
| In Chat | `exit` | End session |

---

## Tips

-   **Health indicators** (🟢🟡🔴) show you at a glance which models are ready.
-   **Sticky defaults**: Once you pick a model, it's remembered for next time.
-   **Checkpoint rollback**: If an API call fails mid-conversation, your chat history rolls back automatically.
-   **Spec lifecycle**: Specs remember their state. You can leave and come back days later.
-   **Safety first**: Kognisant never touches files outside your project root or `~/.kognisant_core/`.

---
*Kognisant is built to be your partner. Talk to it naturally — it handles the technical details.*
