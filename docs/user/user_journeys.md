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
| In Chat | `/jobs` | List all background jobs (name, type, state, run#, exit code, next run) |
| In Chat | `/job stop <name>` | Send SIGTERM, set state to cancelled |
| In Chat | `/job logs <name>` | View job output logs |
| In Chat | `/job restart <name>` | Restart a stopped/crashed job |
| In Chat | `/job remove <name>` | Permanently remove job from queue |
| In Chat | `/daemon status` | Check daemon health |
| In Chat | `/daemon start` | Start the background daemon |
| In Chat | `/daemon stop` | Stop the running daemon |
| In Chat | `/daemon restart` | Restart the daemon (stop + start) |
| In Chat | `exit` | End session |
| Terminal | `kognisant daemon start` | Start background daemon |
| Terminal | `kognisant daemon stop` | Stop the daemon gracefully |
| Terminal | `kognisant daemon restart` | Stop and restart the daemon |
| Terminal | `kognisant daemon status` | Check daemon status |
| Terminal | `kognisant daemon logs` | View daemon log |
| Terminal | `kognisant job add` | Add a new job to the queue |
| Terminal | `kognisant job add --env-file` | Add job with env vars from file |
| Terminal | `kognisant job list` | List all jobs with details |
| Terminal | `kognisant job cancel <name>` | Cancel a job |
| Terminal | `kognisant job remove <name>` | Remove a job permanently |
| Terminal | `kognisant job edit <name>` | Edit job config (--cron, --env, --script) |
| Terminal | `kognisant job logs <name>` | View job logs |
| Terminal | `kognisant job logs <name> -f` | Tail job logs in real-time |

---

## Scenario 9: Running a Background Bot
**Goal**: You want the AI to build a Telegram bot and keep it running 24/7.

1.  **Start Chat**: `kognisant chat`
2.  **Ask the AI to build your bot**:
    ```
    Build a Telegram bot that answers FAQ questions from our knowledge base
    ```
3.  **The AI uses `create_script` to write the bot** to `~/.kognisant_core/scripts/telegram-bot.py` with accompanying metadata.
4.  **The AI uses `schedule_job` to start it as a persistent job**:
    ```
    → Job 'telegram-bot' created as persistent (auto-restarts on crash)
    ```
5.  **Check status any time** using the chat command:
    ```
    /jobs
    ```
    Output:
    ```
    Jobs:
      telegram-bot  persistent  running  (PID 48291)
    ```
6.  **Bot crashes → daemon auto-restarts** it after 5 seconds. The restart counter increments.
7.  **Check logs** to see what happened:
    ```
    /job logs telegram-bot
    ```
    Output shows stdout and `[ERROR]` prefixed stderr from the bot script.

---

## Scenario 10: Scheduling a Nightly Task
**Goal**: Run your test suite automatically at 2 AM every night.

1.  **From the CLI, add a scheduled job**:
    ```bash
    kognisant job add --name nightly-tests --script run-tests.py --type scheduled --cron "0 2 * * *"
    ```
    *Kognisant validates the script exists and the cron syntax is correct.*
2.  **The daemon picks it up** at 2:00 AM each night and executes `~/.kognisant_core/scripts/run-tests.py`.
3.  **Check results the next morning**:
    ```bash
    kognisant job logs nightly-tests
    ```
    Shows the last 50 lines of output from the most recent run.
4.  **Check the schedule**:
    ```bash
    kognisant job list
    ```
    Output:
    ```
    Jobs:
      nightly-tests  scheduled  scheduled  last_run: 2025-06-15T02:00:05
    ```

---

## Scenario 11: Editing a Running Job
**Goal**: You need to change the schedule of a running cron job without recreating it.

1.  **Check current jobs**:
    ```bash
    kognisant job list
    ```
    Output:
    ```
      nightly-tests   scheduled  scheduled  Run# 12  Exit 0  2025-06-15T02:00 UTC   in 8h (2025-06-16T02:00 UTC)
    ```
2.  **Edit the cron schedule** to run at 3 AM instead:
    ```bash
    kognisant job edit nightly-tests --cron "0 3 * * *"
    ```
    Output:
    ```
    Job 'nightly-tests' updated: cron_expression='0 3 * * *'
    ```
3.  **If the job were currently running**, you'd see:
    ```
    ⚠️  Warning: Job 'nightly-tests' is currently running. Changes will take effect on the next execution cycle.
    ```
4.  **Verify the change**:
    ```bash
    kognisant job list
    ```
    Now shows `in 9h (2025-06-16T03:00 UTC)` for next run.

---

## Scenario 12: Removing a Job
**Goal**: You no longer need a background bot and want to clean it up.

1.  **Check if the job is running**:
    ```bash
    kognisant job list
    ```
    Shows `telegram-bot  persistent  running  (PID 48291)`
2.  **Remove it** (terminates the process automatically):
    ```bash
    kognisant job remove telegram-bot
    ```
    Output:
    ```
    Job 'telegram-bot' removed.
    ```
3.  **Or from chat**:
    ```
    /job remove telegram-bot
    ```
    Output:
    ```
    Job 'telegram-bot' removed from queue.
    ```
4.  **Confirm it's gone**:
    ```
    /jobs
    ```
    The job no longer appears in the listing.

---

## Scenario 13: Restarting the Daemon
**Goal**: The daemon seems sluggish or you updated configuration and want a clean restart.

1.  **From CLI** (single command):
    ```bash
    kognisant daemon restart
    ```
    Output:
    ```
    Daemon restarted with new PID 55123.
    ```
2.  **From chat**:
    ```
    /daemon restart
    ```
    Output:
    ```
    Daemon restarted with new PID 55123.
    ```
3.  **If daemon wasn't running**, both methods start it fresh:
    ```
    Daemon was not previously running. Started fresh with PID 55123.
    ```

---

## Scenario 14: Tailing Live Logs
**Goal**: You want to watch a bot's output in real-time as it processes requests.

1.  **Start following the log**:
    ```bash
    kognisant job logs telegram-bot --follow
    ```
2.  **You see recent output** and then live updates as they arrive:
    ```
    [2025-06-15T14:30:01] Bot started successfully
    [2025-06-15T14:30:02] Listening for messages...
    [2025-06-15T14:31:15] Received message from user_42
    [2025-06-15T14:31:15] Processing FAQ query...
    [2025-06-15T14:31:16] Reply sent: "Here's how to reset your password..."
    ```
3.  **Press Ctrl+C** to stop following:
    ```
    Follow mode stopped.
    ```

The log is polled every 500ms for new content.

---

## Scenario 15: Recovering from Corruption
**Goal**: Something went wrong and `jobs.json` got corrupted or deleted.

### If jobs.json is deleted while daemon is running:

1.  The daemon detects the missing file on its next poll cycle
2.  If `jobs.json.bak` exists, it automatically restores from the backup
3.  You see a warning in daemon.log:
    ```
    WARNING: Primary missing, restored from backup
    ```
4.  All your jobs continue running as if nothing happened

### If jobs.json contains invalid JSON:

1.  The daemon (or any CLI command) detects the corruption on load
2.  If `jobs.json.bak` is valid, it restores from the backup automatically
3.  The corrupted file is replaced with the last known good state
4.  A warning is logged indicating recovery was performed

### If both files are gone:

1.  The system initializes a fresh empty job queue: `{"schema_version": 1, "jobs": []}`
2.  An error is logged: "Both primary and backup missing/corrupted. Data loss."
3.  You'll need to recreate your jobs

### Prevention:

- Every successful write to `jobs.json` also creates `jobs.json.bak`
- Both files are written atomically (temp → fsync → rename) to prevent partial writes
- The system never modifies `jobs.json` in-place

---

## Scenario 16: Handling Clock Jumps
**Goal**: Your laptop was suspended overnight, and you want to understand what happens to missed cron jobs.

1.  **With the default `skip` policy** (most jobs):
    - Laptop suspends at 11 PM, wakes at 7 AM
    - Your 2 AM cron job that was missed is simply skipped
    - The daemon logs: `"Clock jump detected. Skipping missed executions for: nightly-tests"`
    - The next scheduled run happens at 2 AM the following night

2.  **With `catchup_once` policy** (critical jobs):
    ```bash
    kognisant job edit critical-sync --scheduler-policy catchup_once
    ```
    - Laptop suspends at 11 PM, wakes at 7 AM
    - The daemon detects the clock jump
    - Your `critical-sync` job fires exactly once (even though it missed multiple intervals)
    - The daemon logs: `"Clock jump detected. Catchup execution for: critical-sync"`

3.  **The 30-second threshold**: A clock jump is detected when the elapsed time between daemon poll cycles exceeds 30 seconds (2× the 15-second polling interval).

---

## Scenario 17: Using --env-file for Secrets
**Goal**: You want to run a bot with API keys without storing them directly in the job queue.

1.  **Create a secrets file**:
    ```bash
    mkdir -p ~/.secrets
    cat > ~/.secrets/my-bot.env << 'EOF'
    # Bot credentials
    TELEGRAM_TOKEN=123456:ABC-DEF
    DATABASE_URL=postgres://user:pass@localhost/db
    API_SECRET=sk-very-secret-key
    EOF
    chmod 600 ~/.secrets/my-bot.env
    ```

2.  **Create the job using the env file**:
    ```bash
    kognisant job add --name my-bot --script telegram-bot.py --type persistent --env-file ~/.secrets/my-bot.env
    ```

3.  **What happens internally**:
    - Kognisant reads the KEY=VALUE pairs from your file
    - The values are stored in `jobs.json` (which has `chmod 600`)
    - The daemon passes them as environment variables to the subprocess

4.  **Security considerations**:
    - `jobs.json` is protected with `0o600` permissions (owner-only)
    - But any process running as your user can still read it
    - For maximum security, consider using a dedicated service account
    - The system is NOT a secrets manager — for high-security environments, use a proper vault

---

## Tips

-   **Health indicators** (🟢🟡🔴) show you at a glance which models are ready.
-   **Sticky defaults**: Once you pick a model, it's remembered for next time.
-   **Checkpoint rollback**: If an API call fails mid-conversation, your chat history rolls back automatically.
-   **Spec lifecycle**: Specs remember their state. You can leave and come back days later.
-   **Safety first**: Kognisant never touches files outside your project root or `~/.kognisant_core/`.

---
*Kognisant is built to be your partner. Talk to it naturally — it handles the technical details.*
