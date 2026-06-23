# Background Daemon

Kognisant includes a built-in background daemon that runs scripts, cron jobs, persistent services, and one-shot AI tasks without keeping a terminal open. Think of it as your personal task runner that operates 24/7.

---

## Why Use the Daemon?

Sometimes you need things running in the background:

- A Telegram bot that should stay alive permanently
- A nightly test suite that runs at 2 AM
- A monitoring script that checks API health every 5 minutes
- A one-shot AI task that researches something while you sleep

The daemon handles all of this. It forks to the background, polls a job queue every 15 seconds, and manages process lifecycles including crash recovery.

---

## Platform Requirements

The daemon requires a POSIX-compliant operating system:

- Linux (all major distributions)
- macOS (10.15 Catalina and later)
- Windows is NOT supported (the daemon uses `os.fork()`, `os.setsid()`, and `fcntl.flock()`)

The interactive chat and all non-daemon features work on any platform.

---

## Starting, Stopping, and Restarting

### Start the daemon

```bash
kognisant daemon start
```

The daemon forks to the background and prints its PID. You can close the terminal afterward.

### Check status

```bash
kognisant daemon status
```

Output:
```
Daemon is running with PID 48291 (uptime: 2h 15m)
```

### Stop gracefully

```bash
kognisant daemon stop
```

This sends SIGTERM to the daemon. It then:
1. Stops accepting new job executions
2. Sends SIGTERM to all running job subprocesses
3. Waits up to 10 seconds per process for graceful shutdown
4. Sends SIGKILL to any process that does not exit
5. Cleans up the PID file

### Restart

```bash
kognisant daemon restart
```

Equivalent to stop + start in one command. If the daemon was not running, it starts fresh:

```
Daemon was not previously running. Started fresh with PID 55123.
```

### View logs

```bash
kognisant daemon logs
```

Shows the daemon's operational log (polling events, job starts/stops, errors).

---

## Job Types

There are three types of jobs:

### Scheduled Jobs

Run a script on a cron schedule. The daemon evaluates the cron expression every poll cycle (15 seconds) and executes when the time matches.

```bash
kognisant job add --name nightly-tests --script run-tests.py --type scheduled --cron "0 2 * * *"
```

### Persistent Jobs

Long-running services that auto-restart on crash. If the process exits with a non-zero code, the daemon restarts it after a 5-second delay.

```bash
kognisant job add --name telegram-bot --script bot.py --type persistent
```

Exit behavior:
- `exit(0)` = intentional completion, daemon does NOT restart
- `exit(non-zero)` = crash, daemon auto-restarts after 5 seconds

### Agent Jobs

One-shot AI tasks. The daemon spawns a PERP agent swarm to complete a goal, then the job terminates.

```bash
kognisant job add --name research-auth --type agent --task "Research OAuth2 PKCE flow and write a summary"
```

---

## Adding Jobs

### From the CLI

```bash
kognisant job add \
  --name health-check \
  --script monitor.py \
  --type scheduled \
  --cron "*/5 * * * *"
```

Required flags:
- `--name` - Unique job name (1-64 chars, lowercase alphanumeric, hyphens, underscores)
- `--type` - One of: `scheduled`, `persistent`, `agent`

Type-specific flags:
- `--script` - Script name in `~/.kognisant_core/scripts/` (required for scheduled and persistent)
- `--cron` - Cron expression (required for scheduled)
- `--task` - Task description (required for agent)

Optional flags:
- `--env KEY=VALUE` - Environment variable (repeatable)
- `--env-file PATH` - Load env vars from a file

### From chat

Inside a `kognisant chat` session, the AI can create jobs using its `schedule_job` tool. Just ask naturally:

```
Build a health monitoring script and run it every 5 minutes
```

The AI will:
1. Create the script in `~/.kognisant_core/scripts/`
2. Register it as a scheduled job
3. Confirm the schedule

### Daemon-not-running warning

If you add a job while the daemon is stopped, you will see:

```
⚠️  Warning: daemon is not running, job will not execute until you run `kognisant daemon start`
```

---

## Cron Expressions

All cron expressions use 5-field UTC format:

```
┌───────────── minute (0-59)
│ ┌───────────── hour (0-23)
│ │ ┌───────────── day of month (1-31)
│ │ │ ┌───────────── month (1-12)
│ │ │ │ ┌───────────── day of week (0-6, Sunday=0)
│ │ │ │ │
* * * * *
```

### Supported syntax

| Symbol | Meaning | Example |
|:---|:---|:---|
| `*` | Every value | `* * * * *` (every minute) |
| `,` | List | `0,30 * * * *` (at :00 and :30) |
| `-` | Range | `9-17 * * * *` (hours 9 through 17) |
| `/` | Step | `*/5 * * * *` (every 5 minutes) |

### Common examples

```bash
# Every day at 2:00 AM UTC
--cron "0 2 * * *"

# Every Monday at 9:00 AM UTC
--cron "0 9 * * 1"

# Every 15 minutes
--cron "*/15 * * * *"

# First of every month at midnight
--cron "0 0 1 * *"

# Weekdays at 8:30 AM UTC
--cron "30 8 * * 1-5"
```

### Unmatchable expression warning

If your cron expression cannot produce a valid next execution within 366 days (e.g., February 31st), Kognisant warns you:

```
Error: validation - Cron expression '0 0 31 2 *' may never produce a match within 366 days
Do you want to create this job anyway? [y/N]
```

---

## Monitoring Jobs

### List all jobs

```bash
kognisant job list
```

Output (table format):

```
  NAME                 TYPE         STATE        RUN#  EXIT  LAST RUN               NEXT RUN                     PID
  telegram-bot         persistent   running      3     -     2025-06-15T10:30 UTC   -                            48291
  nightly-tests        scheduled    scheduled    12    0     2025-06-15T02:00 UTC   in 8h 30m (2025-06-16T02:00 UTC)  -
  health-check         scheduled    scheduled    48    0     2025-06-15T14:30 UTC   in 2m (2025-06-15T14:35 UTC)      -
```

### View job logs

```bash
# Last 50 lines of output
kognisant job logs telegram-bot

# Live tail (updates every 500ms)
kognisant job logs telegram-bot --follow
```

Press Ctrl+C to stop following.

### From inside chat

```
/jobs                    List all jobs
/job logs telegram-bot   View last 30 lines
/job stop telegram-bot   Cancel a running job
/job restart telegram-bot   Restart a stopped job
/job remove telegram-bot    Remove permanently
```

---

## Crash Recovery and Restart Behavior

### Persistent job crashes

When a persistent job crashes (non-zero exit):

1. The daemon detects the exit on its next poll (within 15 seconds)
2. Waits 5 seconds (backoff delay)
3. Restarts the job
4. Increments the run counter

### Crash loop detection

If a persistent job crashes repeatedly (multiple restarts in a short window), the daemon may put it in `crash_loop` state to prevent infinite restart cycles. You can inspect and restart manually:

```bash
kognisant job logs problematic-bot    # See what's failing
kognisant job restart problematic-bot  # Try again after fixing the issue
```

### File corruption recovery

The job queue (`~/.kognisant_core/jobs.json`) uses atomic writes and backup files:

- Every write creates a `.bak` backup first
- If `jobs.json` is corrupted, the daemon restores from `.bak`
- If both are lost, a fresh empty queue is created
- All writes use temp file + fsync + rename to prevent partial writes

---

## Editing Jobs

Change a job's configuration without removing and recreating it:

```bash
# Change schedule
kognisant job edit nightly-tests --cron "0 3 * * *"

# Update environment variables (merges with existing)
kognisant job edit my-bot --env API_KEY=new-key --env TIMEOUT=30

# Change the script
kognisant job edit my-bot --script new-bot.py
```

If the job is currently running, changes apply on the next execution cycle:

```
⚠️  Warning: Job 'my-bot' is currently running. Changes will take effect on the next execution cycle.
```

---

## Environment Variables

Pass environment variables to job scripts:

```bash
# Inline
kognisant job add --name my-bot --script bot.py --type persistent --env API_KEY=sk-abc123 --env PORT=8080

# From a file
kognisant job add --name my-bot --script bot.py --type persistent --env-file ~/.secrets/bot.env
```

The env file format:
```
# Comments supported
API_KEY=sk-abc123
DATABASE_URL=postgres://localhost/mydb
DEBUG=false
```

**Security note:** Env vars are stored in `jobs.json` which has `chmod 600` permissions (owner-only). For highly sensitive credentials on shared machines, use a dedicated secrets manager. Kognisant is NOT a secrets vault.

---

## Clock Jump Handling

When your machine suspends (laptop closed) and wakes up later, the daemon detects the time gap:

- A "clock jump" is detected when elapsed time between polls exceeds 30 seconds (2x the 15-second interval)
- Default behavior (`skip` policy): missed cron jobs are silently skipped
- Alternative (`catchup_once` policy): each missed job fires exactly once

```bash
# Set catchup behavior for a critical sync job
kognisant job edit critical-sync --scheduler-policy catchup_once
```

---

## Managing from Chat

All daemon and job commands are available as slash commands inside `kognisant chat`:

| Command | Effect |
|:---|:---|
| `/daemon status` | Show PID, uptime, running state |
| `/daemon start` | Start the daemon |
| `/daemon stop` | Stop the daemon |
| `/daemon restart` | Restart the daemon |
| `/jobs` | List all jobs with full details |
| `/job stop <name>` | Cancel a running job |
| `/job logs <name>` | View recent output |
| `/job restart <name>` | Restart a stopped/crashed job |
| `/job remove <name>` | Permanently remove a job |

---

## Script Location

All scripts must be placed in `~/.kognisant_core/scripts/`. The daemon only executes scripts from this directory (security boundary).

When the AI creates scripts for you (via the `create_script` tool), they are automatically placed in this directory. You can also copy scripts there manually:

```bash
cp my-bot.py ~/.kognisant_core/scripts/my-bot.py
```
