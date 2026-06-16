# CLI Reference

Complete command reference for all Kognisant CLI commands, chat slash commands, and agent tools.

## CLI Commands

### Global Options

```bash
kognisant --help     # Show all available subcommands
kognisant --version  # Show version (if implemented)
```

---

### `kognisant init`

Initialize a project workspace.

```bash
kognisant init
```

**Creates:**
- `.kognisant/config.json` - Workspace configuration
- `.kognisant/context.md` - Persistent Membrain template
- `.kognisant/history/` - Session log directory
- Registers project in `~/.kognisant_core/projects.json`

**Behavior:**
- If `.kognisant/` already exists: prints warning, no changes made
- If `~/.kognisant_core/` doesn't exist: creates it first

---

### `kognisant chat`

Start an interactive AI chat session.

```bash
kognisant chat
```

**Behavior:**
- Loads project context if in an initialized workspace
- Prompts for model selection if none configured
- Falls back to Mock Chat mode if no APIs available
- Sticky default model restored from `~/.kognisant_core/models_pool.json`

---

### `kognisant status`

Display system health overview.

```bash
kognisant status
```

**Output includes:**
- Workspace detection and Membrain state
- Global Core directory status
- Active model and provider health
- Daemon section: running/stopped, PID, uptime, active jobs, last poll time
- Spec progress (if any specs exist)

---

### `kognisant spec`

Manage Spec-Driven Development documents.

```bash
kognisant spec <name>      # Create or resume a spec
kognisant spec --list      # List all specs with status
kognisant spec -l          # Short form
```

---

### `kognisant daemon start`

Start the background daemon process.

```bash
kognisant daemon start
```

**Output:**
```
Daemon started with PID 12345.
```

**Errors:**
```
Error: state - Daemon is already running (PID 12345).
Error: state - Another daemon instance is starting. Aborting.
```

**Warning (if running as root):**
```
Warning: Daemon running with root privileges. Recommend running under a non-root user.
```

---

### `kognisant daemon stop`

Stop the running daemon gracefully.

```bash
kognisant daemon stop
```

Sends SIGTERM to the daemon. The daemon then:
1. Signals all running job subprocesses with SIGTERM
2. Waits up to 10 seconds per process
3. Sends SIGKILL to any that didn't exit
4. Removes PID file and exits

**Output:**
```
Daemon stopped.
```

**Errors:**
```
Error: state - Daemon is not running.
```

---

### `kognisant daemon restart`

Stop and restart the daemon in one command.

```bash
kognisant daemon restart
```

**Output:**
```
Daemon restarted with new PID 54321.
```

**If daemon was not running:**
```
Daemon was not previously running. Started fresh with PID 54321.
```

---

### `kognisant daemon status`

Check daemon health.

```bash
kognisant daemon status
```

**Output (running):**
```
Daemon: running (PID 12345)
Uptime: 2h 15m
Active jobs: 3
Last poll: 2025-06-15T14:30:05 UTC
```

**Output (stopped):**
```
Daemon: stopped
Last active: 2025-06-15T12:00:00 UTC (from daemon.log)
```

---

### `kognisant daemon logs`

View daemon operational log.

```bash
kognisant daemon logs
```

Displays the last 50 lines of `~/.kognisant_core/daemon.log`.

---

### `kognisant job add`

Add a new job to the queue.

```bash
kognisant job add --name <name> --script <script> --type <type> [options]
```

**Required flags:**

| Flag | Description |
|------|-------------|
| `--name NAME` | Job name (1-64 chars, `[a-z0-9_-]`) |
| `--script SCRIPT` | Script filename (relative to `~/.kognisant_core/scripts/`) |
| `--type TYPE` | One of: `scheduled`, `persistent`, `agent` |

**Optional flags:**

| Flag | Description |
|------|-------------|
| `--cron EXPR` | Cron expression (required for `scheduled` type). Evaluated in UTC. |
| `--env KEY=VALUE` | Set environment variable (repeatable) |
| `--env-file PATH` | Load env vars from file (KEY=VALUE per line, # for comments) |
| `--task TASK` | Task description (for `agent` type) |
| `--project-root PATH` | Working directory for agent jobs |

**Examples:**
```bash
# Scheduled job running at 2 AM UTC daily
kognisant job add --name nightly-tests --script run-tests.py --type scheduled --cron "0 2 * * *"

# Persistent bot with env vars from file
kognisant job add --name telegram-bot --script bot.py --type persistent --env-file ~/.secrets/bot.env

# Persistent job with inline env vars
kognisant job add --name monitor --script health-check.py --type persistent --env API_KEY=abc123
```

**Warnings:**
```
# If daemon is not running:
Warning: Daemon is not running. Job will not execute until daemon is started.
Start with: kognisant daemon start

# If cron expression may never match:
Warning: Cron expression '0 0 31 2 *' may never produce a match within 366 days.
Do you want to create this job anyway? [y/N]
```

**Errors:**
```
Error: validation - Job name 'AB!' is invalid. Use lowercase alphanumeric, hyphens, or underscores (1-64 chars).
Error: validation - Cron expression required for scheduled job type.
Error: not_found - Script 'nonexistent.py' not found in scripts directory.
```

---

### `kognisant job list`

List all jobs with details.

```bash
kognisant job list
```

**Output format:**
```
Jobs:
  nightly-tests   scheduled   scheduled  Run# 12  Exit 0    2025-06-15T02:00 UTC   in 8h (2025-06-16T02:00 UTC)
  telegram-bot    persistent  running    Run# 45  Exit -    (PID 48291)
  data-sync       scheduled   failed     Run# 3   Exit 1    2025-06-15T03:00 UTC   in 7h (2025-06-16T03:00 UTC)
```

**Columns:** name, type, state, run count, last exit code, last run / PID, next run (scheduled only)

---

### `kognisant job cancel <name>`

Cancel a pending or running job.

```bash
kognisant job cancel my-job
```

**Behavior:**
- If running: sends SIGTERM to subprocess, state → "cancelled"
- If pending/scheduled: state → "cancelled" (no process to kill)
- If in terminal state: error

**Errors:**
```
Error: state - Job 'my-job' is in 'completed' state and cannot be cancelled.
Error: not_found - Job 'my-job' does not exist. Use 'kognisant job list' to see available jobs.
```

---

### `kognisant job remove <name>`

Permanently remove a job from the queue.

```bash
kognisant job remove old-bot
```

**Behavior:**
- If running: terminates subprocess first, then removes
- Removes job entry from `jobs.json`
- Does NOT delete the script file or log files

**Output:**
```
Job 'old-bot' removed.
```

**Errors:**
```
Error: not_found - Job 'old-bot' does not exist. Use 'kognisant job list' to see available jobs.
```

---

### `kognisant job edit <name>`

Edit a job's configuration in place.

```bash
kognisant job edit <name> [--cron EXPR] [--env KEY=VALUE] [--script PATH]
```

**Flags:**

| Flag | Description |
|------|-------------|
| `--cron EXPR` | New cron expression (evaluated in UTC) |
| `--env KEY=VALUE` | Set/update environment variable (repeatable, merges with existing) |
| `--script PATH` | New script path (relative to scripts/) |
| `--scheduler-policy POLICY` | Set scheduler policy (`skip` or `catchup_once`) |

**Examples:**
```bash
kognisant job edit nightly-tests --cron "0 3 * * *"
kognisant job edit my-bot --env API_KEY=new-key --env TIMEOUT=30
kognisant job edit my-bot --script new-bot.py
kognisant job edit backup --scheduler-policy catchup_once
```

**Output:**
```
Job 'nightly-tests' updated: cron_expression='0 3 * * *'
```

**Warnings:**
```
Warning: Job 'my-bot' is currently running. Changes will take effect on the next execution cycle.
```

**Errors:**
```
Error: not_found - Job 'nonexistent' does not exist. Use 'kognisant job list' to see available jobs.
Error: validation - Invalid cron expression: '* * * *' (expected 5 fields).
```

---

### `kognisant job logs <name>`

View job output logs.

```bash
kognisant job logs <name>           # Last 50 lines
kognisant job logs <name> --follow  # Tail mode (Ctrl+C to stop)
kognisant job logs <name> -f        # Short form
```

**Follow mode:**
- Checks for new content every 500ms
- Displays new lines as they appear
- Press Ctrl+C to stop:
  ```
  Follow mode stopped.
  ```

**Errors:**
```
Error: not_found - Job 'nonexistent' does not exist. Use 'kognisant job list' to see available jobs.
Error: not_found - No log file found for job 'my-job'. Job may not have been executed yet.
```

---

## Chat Slash Commands

### Daemon Commands

| Command | Description |
|---------|-------------|
| `/daemon start` | Start the background daemon |
| `/daemon stop` | Send SIGTERM to running daemon |
| `/daemon restart` | Stop + start in one step |
| `/daemon status` | Show daemon PID, uptime, state |

### Job Commands

| Command | Description |
|---------|-------------|
| `/jobs` | List all jobs (name, type, state, run count, exit code, next run, PID) |
| `/job stop <name>` | Send SIGTERM to running subprocess, state → "cancelled" |
| `/job logs <name>` | View last 30 lines of job output |
| `/job restart <name>` | Restart a stopped/crash-looped persistent job |
| `/job remove <name>` | Permanently remove job (terminates if running) |

### General Commands

| Command | Description |
|---------|-------------|
| `/help` | Show available commands |
| `/clear` | Clear conversation history (preserves system prompt) |
| `/context` | Display local Membrain (context.md) |
| `/skills` | List loaded global skills |
| `/model` | Switch active model or add custom endpoint |
| `/providers` | Inspect provider configs and API key status |
| `/files` | List indexed workspace files |
| `/read <path>` | Load file into conversation context |
| `/agent <task>` | Deploy autonomous PERP swarm |
| `/tool <subcommand>` | Global tool management (list, register, delete) |
| `/goals` | List active World Model goals (requires world_model_enabled) |
| `/goals accept <id>` | Accept a goal for execution |
| `/goals dismiss <id>` | Dismiss a goal (records negative feedback) |
| `/paste` or `/p` | Enter paste mode (submit with `/end`) |
| `exit` or `quit` | End session, save history |

### Job Command Details

**`/job stop <name>`**

Sends SIGTERM to the job's subprocess and updates state to "cancelled".

Errors:
- Job not found: `Error: not_found - Job 'foo' does not exist.`
- Job not running: `Error: state - Job 'foo' is not currently running.`

**`/job restart <name>`**

Restarts a stopped or crash-looped persistent job by resetting state to "pending".

Warnings:
- If daemon not running: `Warning: Daemon is not running. Job will remain in pending state until daemon starts.`

**`/job remove <name>`**

Terminates subprocess if running, then removes job entry from queue.

Output: `Job 'foo' removed from queue.`

---

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | General error (validation, not found, state conflict) |
| 2 | Argument parsing error (argparse) |

## Error Format

All user-facing errors follow a consistent format:

```
Error: [category] - [description]. [suggestion]
```

### Error Categories

| Category | When Used |
|----------|-----------|
| `validation` | Invalid input (bad name, bad cron expression, missing required field) |
| `not_found` | Referenced resource doesn't exist (job name, script path) |
| `state` | Operation invalid in current state (cancel completed job, stop stopped daemon) |
| `permission` | Access denied (symlink escape, file permission issue) |
| `timeout` | Operation timed out (lock acquisition, job execution) |
| `io` | I/O failure (broken pipe, disk error, file read/write failure) |

### Examples

```
Error: validation - Job name 'AB!' is invalid. Use lowercase alphanumeric, hyphens, or underscores (1-64 chars).
Error: not_found - Job 'foo' does not exist. Use 'kognisant job list' to see available jobs.
Error: state - Job 'bar' is in 'completed' state and cannot be cancelled.
Error: permission - Script path resolves outside allowed directory. Execution refused.
Error: timeout - Could not acquire file lock within 5 seconds. Another process may be holding the lock.
Error: io - Broken pipe detected for job 'baz'. Process terminated.
```

## Agent Tools

These tools are available to the LLM via the OpenAI function-calling interface:

| Tool | Description |
|------|-------------|
| `schedule_job` | Create a new job in the queue |
| `cancel_job` | Cancel a pending/running job |
| `remove_job` | Remove a job from the queue |
| `list_jobs` | List all jobs with status |
| `job_logs` | View recent job output |
| `create_script` | Create a new script atomically |
| `read_script` | Read script content |
| `edit_script` | Modify an existing script |
| `delete_script` | Delete a script and its metadata |
| `list_scripts` | List available scripts |

## Cross-References

- [Architecture](architecture.md) - Module that implements each command
- [Job Lifecycle](job-lifecycle.md) - State machine and execution semantics
- [Cron Scheduling](cron-scheduling.md) - Cron expression syntax and validation
- [Security](security.md) - Error handling for security violations
