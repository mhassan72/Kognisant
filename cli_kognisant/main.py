import argparse
import os
import sys

from .chat import chat_flow
from .colors import Colors
from .config import (
    find_project_root,
    init_project,
)
from .daemon import DaemonManager
from .jobs import CronParser, JobQueue, JOB_NAME_PATTERN, format_error, CANCELLABLE_STATES, TERMINAL_STATES


def _handle_setup():
    """Standalone provider configuration wizard (same as first-run wizard in chat)."""
    from .config import init_global_core

    init_global_core()

    from .chat import _run_first_time_setup

    print(f"\n  ⚙️  {Colors.BOLD}Kognisant Provider Setup{Colors.RESET}\n")
    result = _run_first_time_setup()
    if result and result != "mock" and isinstance(result, dict):
        print(f"  🎉 Setup complete! Run {Colors.CYAN}kognisant chat{Colors.RESET} to start.\n")
    elif result == "mock":
        print(f"  Skipped. Run {Colors.CYAN}kognisant setup{Colors.RESET} again when ready.\n")
    else:
        print(f"  Cancelled.\n")


def _handle_status():
    """Display workspace health, model connectivity, and spec status."""
    from .config import (
        GLOBAL_CORE_DIR,
        find_project_root,
        get_compiled_models,
        get_default_model,
        load_global_skills,
    )
    from .network import get_ollama_models
    from . import json_stream

    is_json = json_stream.is_active()

    if is_json:
        # Structured JSON output for status
        import os
        root = find_project_root()
        compiled_models = get_compiled_models()
        default_model = get_default_model(compiled_models) if compiled_models else None
        skills = load_global_skills()
        tools_dir = os.path.join(GLOBAL_CORE_DIR, "tools")
        tool_count = len([f for f in os.listdir(tools_dir) if f.endswith(".py")]) if os.path.exists(tools_dir) else 0

        from .channels import ChannelManager
        channels = ChannelManager().list_channels()

        json_stream.emit({
            "type": "command_result",
            "command": "status",
            "data": {
                "version": "0.1.0",
                "workspace": root,
                "global_core": os.path.exists(GLOBAL_CORE_DIR),
                "skills_count": len(skills),
                "tools_count": tool_count,
                "active_model": default_model.get("name") if default_model else None,
                "provider": default_model.get("provider") if default_model else None,
                "models": [m.get("name") for m in compiled_models],
                "channels": [{"name": ch.get("name"), "platform": ch.get("platform"),
                              "state": ch.get("state")} for ch in channels],
            },
        })
        return

    print(f"\n  {Colors.BOLD}Kognisant v0.1.0{Colors.RESET}\n")

    # Workspace status
    root = find_project_root()
    if root:
        print(f"  Workspace:    {root} (.kognisant/ {Colors.GREEN}✅{Colors.RESET})")
    else:
        print(f"  Workspace:    {Colors.YELLOW}No active workspace{Colors.RESET} (run 'kognisant init')")

    # Global Core
    import os

    if os.path.exists(GLOBAL_CORE_DIR):
        skills = load_global_skills()
        tools_dir = os.path.join(GLOBAL_CORE_DIR, "tools")
        tool_count = 0
        if os.path.exists(tools_dir):
            tool_count = len([f for f in os.listdir(tools_dir) if f.endswith(".py")])
        print(f"  Global Core:  ~/.kognisant_core/ {Colors.GREEN}✅{Colors.RESET}")
        print(f"    Skills: {len(skills)} loaded")
        print(f"    Tools:  {tool_count} registered")
    else:
        print(f"  Global Core:  {Colors.YELLOW}Not initialized{Colors.RESET}")

    # Model status
    compiled_models = get_compiled_models()
    default_model = get_default_model(compiled_models) if compiled_models else None

    print()
    if default_model:
        display_name = default_model.get("display_name", default_model.get("name", "Unknown"))
        provider = default_model.get("provider", "Unknown")

        # Quick reachability check
        status_icon = _check_model_health(default_model)
        print(f"  Active Model: {display_name} ({provider}) {status_icon}")
    else:
        print(f"  Active Model: {Colors.YELLOW}None configured{Colors.RESET}")

    # Provider summary
    print(f"\n  {Colors.BOLD}Providers:{Colors.RESET}")
    if compiled_models:
        seen_providers = {}
        for model in compiled_models:
            provider = model.get("provider", "Unknown")
            if provider not in seen_providers:
                seen_providers[provider] = []
            seen_providers[provider].append(model)

        for provider, models in seen_providers.items():
            model_names = [m.get("display_name", m.get("name", "?")) for m in models]
            api_key = models[0].get("api_key", "")
            is_local = "ollama" in provider.lower() or "llama" in provider.lower()

            if is_local:
                status = _check_model_health(models[0])
            elif api_key and "your-" not in api_key:
                status = f"{Colors.GREEN}🟢 Key set{Colors.RESET}"
            else:
                status = f"{Colors.YELLOW}🟡 Key needed{Colors.RESET}"

            print(f"    {provider}  {status}")
            for name in model_names[:3]:
                print(f"      • {name}")
    else:
        print(f"    {Colors.YELLOW}No providers configured{Colors.RESET}")

    # Spec status
    if root:
        from .sdd import get_all_specs_status

        specs = get_all_specs_status(root)
        if specs:
            print(f"\n  {Colors.BOLD}Specs:{Colors.RESET}")
            for s in specs:
                status = s["status"]
                done = s["tasks_done"]
                total = s["tasks_total"]
                if status == "DONE":
                    icon = f"{Colors.GREEN}✅{Colors.RESET}"
                elif status == "BUILD":
                    icon = f"{Colors.YELLOW}🔨{Colors.RESET}"
                else:
                    icon = f"{Colors.CYAN}📝{Colors.RESET}"
                progress = f"({done}/{total})" if total > 0 else ""
                print(f"    {icon} {s['name']}  {status} {progress}")

    # Daemon status section (Requirement 17)
    print(f"\n  {Colors.BOLD}Daemon:{Colors.RESET}")
    daemon_status = DaemonManager.status()
    if daemon_status["running"]:
        uptime_str = daemon_status.get("uptime") or "unknown"
        print(f"    State:        {Colors.GREEN}running{Colors.RESET}")
        print(f"    PID:          {daemon_status['pid']}")
        print(f"    Uptime:       {uptime_str}")
        # Count active (running) jobs
        active_jobs = sum(1 for j in JobQueue().load() if j.get("state") == "running")
        print(f"    Active Jobs:  {active_jobs}")
        # Show last poll time from daemon.log if available
        from .daemon import LOG_FILE
        if os.path.exists(LOG_FILE):
            try:
                with open(LOG_FILE, "r") as f:
                    lines = f.readlines()
                # Find last timestamp in log
                last_poll = None
                for line in reversed(lines[-50:]):
                    if line and line[0:2] == "20":
                        last_poll = line[:19]
                        break
                if last_poll:
                    print(f"    Last Poll:    {last_poll} UTC")
            except OSError:
                pass
    else:
        print(f"    State:        {Colors.RED}stopped{Colors.RESET}")
        # Show last active time from daemon.log (Requirement 17.2)
        from .daemon import LOG_FILE
        if os.path.exists(LOG_FILE):
            try:
                mtime = os.path.getmtime(LOG_FILE)
                from datetime import datetime, timezone
                last_active = datetime.fromtimestamp(mtime, tz=timezone.utc)
                print(f"    Last Active:  {last_active.strftime('%Y-%m-%dT%H:%M:%S')} UTC")
            except OSError:
                pass

    print()


def _check_model_health(model_config):
    """Quick non-blocking health check for a model endpoint. Returns status string."""
    import ssl
    import urllib.error
    import urllib.request

    provider = model_config.get("provider", "")
    api_base_url = model_config.get("api_base_url", "")
    api_key = model_config.get("api_key", "")

    if not api_base_url:
        return f"{Colors.RED}🔴 No URL{Colors.RESET}"

    # For cloud providers, just check if key is set
    is_local = "ollama" in provider.lower() or "llama" in provider.lower() or "localhost" in api_base_url
    if not is_local:
        if api_key and "your-" not in api_key:
            return f"{Colors.GREEN}🟢 Ready{Colors.RESET}"
        else:
            return f"{Colors.YELLOW}🟡 Key needed{Colors.RESET}"

    # For local endpoints, try a quick ping
    try:
        if "ollama" in provider.lower():
            url = api_base_url.rstrip("/").replace("/v1", "") + "/api/tags"
        else:
            url = api_base_url.rstrip("/") + "/health"

        context = ssl._create_unverified_context()
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=1.5, context=context) as response:
            if response.status == 200:
                return f"{Colors.GREEN}🟢 Reachable{Colors.RESET}"
    except Exception:
        pass

    return f"{Colors.RED}🔴 Unreachable{Colors.RESET}"


def _handle_daemon(args):
    """Dispatch daemon subcommands to DaemonManager methods."""
    if args.daemon_command == "start":
        try:
            DaemonManager.start()
        except RuntimeError:
            # Error already printed to stderr by DaemonManager.start()
            sys.exit(1)
    elif args.daemon_command == "stop":
        success = DaemonManager.stop()
        if not success:
            sys.exit(1)
        print("Daemon stopped.")
    elif args.daemon_command == "restart":
        was_running = DaemonManager.is_running()
        try:
            new_pid = DaemonManager.restart()
            if was_running:
                print(f"Daemon restarted with new PID {new_pid}.")
            else:
                print(f"Daemon was not previously running. Started fresh with PID {new_pid}.")
        except RuntimeError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
    elif args.daemon_command == "status":
        status = DaemonManager.status()
        if status["running"]:
            uptime_str = f" (uptime: {status['uptime']})" if status["uptime"] else ""
            print(f"Daemon is {Colors.GREEN}running{Colors.RESET} with PID {status['pid']}{uptime_str}")
        else:
            print(f"Daemon is {Colors.RED}not running{Colors.RESET}")
    elif args.daemon_command == "logs":
        output = DaemonManager.read_logs()
        print(output)
    else:
        print("Usage: kognisant daemon {start|stop|restart|status|logs}", file=sys.stderr)
        sys.exit(1)


def _handle_job(args):
    """Dispatch job subcommands with input validation."""
    if args.job_command == "add":
        _handle_job_add(args)
    elif args.job_command == "list":
        _handle_job_list()
    elif args.job_command == "cancel":
        _handle_job_cancel(args.name)
    elif args.job_command == "logs":
        _handle_job_logs(args.name, follow=getattr(args, 'follow', False))
    elif args.job_command == "remove":
        _handle_job_remove(args.name)
    elif args.job_command == "edit":
        _handle_job_edit(args)
    else:
        print("Usage: kognisant job {add|list|cancel|logs|remove|edit}", file=sys.stderr)
        sys.exit(1)


def _handle_job_add(args):
    """Handle `kognisant job add` with full validation."""
    name = args.name
    job_type = args.job_type
    script = args.script
    cron = args.cron
    task = args.task
    env_args = args.env

    # Validate job name (R7-AC10)
    if not JOB_NAME_PATTERN.match(name):
        print(
            format_error("validation",
                         f"Job name '{name}' is invalid",
                         "Use lowercase alphanumeric, hyphens, or underscores (1-64 chars)."),
            file=sys.stderr,
        )
        sys.exit(1)

    # Validate job type (R7-AC9)
    valid_types = ("scheduled", "persistent", "agent")
    if job_type not in valid_types:
        print(
            format_error("validation",
                         f"Invalid job type '{job_type}'",
                         f"Must be one of: {', '.join(valid_types)}"),
            file=sys.stderr,
        )
        sys.exit(1)

    # Type-specific validation
    if job_type == "scheduled":
        if not cron:
            print(
                format_error("validation", "--cron is required for scheduled jobs"),
                file=sys.stderr,
            )
            sys.exit(1)
        if not CronParser.validate(cron):
            print(
                format_error("validation",
                             f"Invalid cron expression '{cron}'",
                             "Expected 5 fields: minute hour day-of-month month day-of-week."),
                file=sys.stderr,
            )
            sys.exit(1)
        # Unmatchable cron warning (Requirement 34)
        if not CronParser.can_match_within_days(cron):
            print(
                format_error("validation",
                             f"Cron expression '{cron}' may never produce a match within 366 days"),
                file=sys.stderr,
            )
            confirm = input("Do you want to create this job anyway? [y/N] ").strip().lower()
            if confirm not in ("y", "yes"):
                print("Job creation cancelled.")
                sys.exit(0)
        if not script:
            print(
                format_error("validation", "--script is required for scheduled jobs"),
                file=sys.stderr,
            )
            sys.exit(1)

    elif job_type == "persistent":
        if not script:
            print(
                format_error("validation", "--script is required for persistent jobs"),
                file=sys.stderr,
            )
            sys.exit(1)

    elif job_type == "agent":
        if not task:
            print(
                format_error("validation", "--task is required for agent jobs"),
                file=sys.stderr,
            )
            sys.exit(1)

    # Verify script exists in ~/.kognisant_core/scripts/ (R7-AC1)
    if script:
        scripts_dir = os.path.expanduser("~/.kognisant_core/scripts")
        script_path = os.path.join(scripts_dir, script)
        if not os.path.exists(script_path):
            print(
                format_error("not_found",
                             f"Script '{script}' not found in {scripts_dir}/"),
                file=sys.stderr,
            )
            sys.exit(1)

    # Parse environment variables
    env_vars = {}
    if env_args:
        for env_str in env_args:
            if "=" not in env_str:
                print(
                    format_error("validation",
                                 f"Invalid env format '{env_str}'",
                                 "Expected KEY=VAL."),
                    file=sys.stderr,
                )
                sys.exit(1)
            key, val = env_str.split("=", 1)
            env_vars[key] = val

    # Load env vars from --env-file if provided (Requirement 28.4)
    env_file = getattr(args, "env_file", None)
    if env_file:
        if not os.path.exists(env_file):
            print(
                f"{Colors.RED}Error:{Colors.RESET} Env file '{env_file}' not found.",
                file=sys.stderr,
            )
            sys.exit(1)
        try:
            with open(env_file, "r") as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    # Skip empty lines and comments
                    if not line or line.startswith("#"):
                        continue
                    if "=" not in line:
                        print(
                            f"{Colors.RED}Error:{Colors.RESET} Invalid format in "
                            f"env file line {line_num}: '{line}'. Expected KEY=VAL.",
                            file=sys.stderr,
                        )
                        sys.exit(1)
                    key, val = line.split("=", 1)
                    env_vars[key.strip()] = val.strip()
        except OSError as e:
            print(
                f"{Colors.RED}Error:{Colors.RESET} Cannot read env file: {e}",
                file=sys.stderr,
            )
            sys.exit(1)

    # Unmatchable cron warning (Requirement 34)
    if cron and job_type == "scheduled":
        if not CronParser.can_match_within_days(cron):
            print(
                f"{Colors.YELLOW}Warning:{Colors.RESET} Cron expression '{cron}' may never match within 366 days.",
                file=sys.stderr,
            )
            try:
                confirm = input("Continue anyway? [y/N]: ").strip().lower()
                if confirm not in ("y", "yes"):
                    print("Aborted.")
                    sys.exit(0)
            except (EOFError, KeyboardInterrupt):
                print("\nAborted.")
                sys.exit(0)

    # Build job config and add to queue (R7-AC8: lock acquired inside JobQueue)
    job_config = {
        "name": name,
        "type": job_type,
        "script_path": script or "",
        "task": task,
        "cron_expression": cron,
        "env_vars": env_vars,
    }

    queue = JobQueue()
    try:
        result = queue.add_job(job_config)
        print(f"{Colors.GREEN}{result}{Colors.RESET}")

        # Daemon-not-running warning (Requirement 16)
        if not DaemonManager.is_running():
            print(
                f"{Colors.YELLOW}⚠️  Warning: daemon is not running, job will not execute "
                f"until you run `kognisant daemon start`{Colors.RESET}"
            )
    except ValueError as e:
        print(f"{Colors.RED}Error:{Colors.RESET} {e}", file=sys.stderr)
        sys.exit(1)


def _handle_job_list():
    """Handle `kognisant job list` — display all jobs in an enhanced table."""
    from datetime import datetime, timezone

    queue = JobQueue()
    jobs = queue.load()

    if not jobs:
        print("No jobs in the queue.")
        return

    now = datetime.now(timezone.utc)

    # Print table header
    print(f"\n  {'NAME':<20} {'TYPE':<12} {'STATE':<12} {'RUN#':<5} {'EXIT':<5} {'LAST RUN':<22} {'NEXT RUN':<28} {'PID':<8}")
    print(f"  {'─' * 20} {'─' * 12} {'─' * 12} {'─' * 5} {'─' * 5} {'─' * 22} {'─' * 28} {'─' * 8}")

    for job in jobs:
        name = job.get("name", "?")[:20]
        job_type = job.get("type", "?")
        state = job.get("state", "?")
        run_count = job.get("run_count", 0)
        last_exit_code = job.get("last_exit_code")
        last_run = job.get("last_run_at")
        pid = job.get("pid")

        # Color-code state
        if state == "running":
            state_display = f"{Colors.GREEN}{state}{Colors.RESET}"
        elif state in ("failed", "crash_loop"):
            state_display = f"{Colors.RED}{state}{Colors.RESET}"
        elif state == "cancelled":
            state_display = f"{Colors.YELLOW}{state}{Colors.RESET}"
        else:
            state_display = state

        # Format last_run with UTC suffix
        last_run_display = f"{last_run} UTC" if last_run else "—"

        # Calculate next_run_at for scheduled jobs
        next_run_display = "—"
        if job.get("type") == "scheduled" and job.get("cron_expression"):
            try:
                next_dt = CronParser.next_run(job["cron_expression"], now)
                delta = next_dt - now
                total_mins = int(delta.total_seconds() / 60)
                if total_mins >= 60:
                    hours = total_mins // 60
                    mins = total_mins % 60
                    relative = f"in {hours}h {mins}m"
                else:
                    relative = f"in {total_mins}m"
                abs_str = next_dt.strftime("%Y-%m-%dT%H:%M") + " UTC"
                next_run_display = f"{relative} ({abs_str})"
            except (ValueError, TypeError):
                next_run_display = "—"

        exit_display = str(last_exit_code) if last_exit_code is not None else "—"
        pid_display = str(pid) if pid and state == "running" else "—"

        print(f"  {name:<20} {job_type:<12} {state_display:<22} {run_count:<5} {exit_display:<5} {last_run_display:<22} {next_run_display:<28} {pid_display:<8}")

    print()


def _handle_job_cancel(name: str):
    """Handle `kognisant job cancel <name>` — cancel a job and kill subprocess."""
    queue = JobQueue()
    job = queue.get_job(name)

    if job is None:
        print(
            format_error("not_found", f"Job '{name}' does not exist",
                         "Use 'kognisant job list' to see available jobs."),
            file=sys.stderr,
        )
        sys.exit(1)

    # Cancel state validation (Requirement 31)
    current_state = job.get("state", "")
    if current_state in TERMINAL_STATES:
        print(
            format_error("state", f"Job '{name}' is in '{current_state}' state and cannot be cancelled"),
            file=sys.stderr,
        )
        sys.exit(1)

    if current_state not in CANCELLABLE_STATES:
        print(
            format_error("state", f"Job '{name}' is in '{current_state}' state and cannot be cancelled"),
            file=sys.stderr,
        )
        sys.exit(1)

    # If the job has a running subprocess, terminate it (R7-AC4,7)
    pid = job.get("pid")
    if pid and current_state == "running":
        import signal
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass  # Process already gone or not owned

    success = queue.update_status(name, "cancelled")
    if success:
        print(f"Job '{name}' cancelled.")
    else:
        print(
            format_error("io", f"Failed to update job '{name}'"),
            file=sys.stderr,
        )
        sys.exit(1)


def _handle_job_logs(name: str, follow: bool = False):
    """Handle `kognisant job logs <name>` — show last 50 lines of job log.

    With --follow: continuously display new lines (polls every 500ms).
    """
    import time as _time

    queue = JobQueue()
    job = queue.get_job(name)

    if job is None:
        print(
            format_error("not_found", f"Job '{name}' does not exist",
                         "Use 'kognisant job list' to see available jobs."),
            file=sys.stderr,
        )
        sys.exit(1)

    if not follow:
        output = queue.read_job_logs(name)
        print(output)
        return

    # --follow mode: tail the log file with 500ms polling (Requirement 24)
    log_path = queue.get_job_log_path(name)
    if not os.path.exists(log_path):
        print(f"No logs available for job '{name}'. Waiting for output...")

    try:
        # Open and seek to end
        if os.path.exists(log_path):
            with open(log_path, "r") as f:
                # Print last 10 lines as context
                f.seek(0, 2)
                file_size = f.tell()
                # Read last few KB for context
                seek_pos = max(0, file_size - 4096)
                f.seek(seek_pos)
                if seek_pos > 0:
                    f.readline()  # discard partial first line
                tail_lines = f.readlines()
                for line in tail_lines[-10:]:
                    print(line, end="")
                last_pos = f.tell()
        else:
            last_pos = 0

        # Poll for new content every 500ms
        while True:
            _time.sleep(0.5)
            if not os.path.exists(log_path):
                continue
            with open(log_path, "r") as f:
                f.seek(last_pos)
                new_content = f.read()
                if new_content:
                    print(new_content, end="", flush=True)
                last_pos = f.tell()
    except KeyboardInterrupt:
        print(f"\n{Colors.CYAN}Follow mode stopped.{Colors.RESET}")
        return


def _handle_job_remove(name: str):
    """Handle `kognisant job remove <name>` — remove job (terminates if running)."""
    import signal as sig

    queue = JobQueue()
    job = queue.get_job(name)

    if job is None:
        print(
            format_error("not_found", f"Job '{name}' does not exist",
                         "Use 'kognisant job list' to see available jobs."),
            file=sys.stderr,
        )
        sys.exit(1)

    # If running, terminate subprocess first (Requirement 21.4)
    pid = job.get("pid")
    if pid and job.get("state") == "running":
        try:
            os.kill(pid, sig.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass

    success = queue.remove_job(name)
    if success:
        print(f"Job '{name}' removed.")
    else:
        print(
            format_error("io", f"Failed to remove job '{name}'"),
            file=sys.stderr,
        )
        sys.exit(1)


def _handle_job_edit(args):
    """Handle `kognisant job edit <name>` — edit job config in place.

    Supports: --cron EXPR, --env KEY=VALUE (repeatable), --script PATH.
    """
    name = args.name
    cron = getattr(args, 'cron', None)
    env_args = getattr(args, 'env', None)
    script = getattr(args, 'script', None)

    if not cron and not env_args and not script:
        print(
            f"{Colors.RED}Error:{Colors.RESET} At least one modification flag is required "
            "(--cron, --env, --script).",
            file=sys.stderr,
        )
        sys.exit(1)

    queue = JobQueue()
    job = queue.get_job(name)

    if job is None:
        print(
            format_error("not_found", f"Job '{name}' does not exist",
                         "Use 'kognisant job list' to see available jobs."),
            file=sys.stderr,
        )
        sys.exit(1)

    # Validate cron expression if provided
    if cron:
        if not CronParser.validate(cron):
            print(
                f"{Colors.RED}Error:{Colors.RESET} Invalid cron expression '{cron}'. "
                "Expected 5 fields: minute hour day-of-month month day-of-week. "
                "Note: cron expressions are evaluated in UTC.",
                file=sys.stderr,
            )
            sys.exit(1)

    # Parse env vars
    new_env = {}
    if env_args:
        for env_str in env_args:
            if "=" not in env_str:
                print(
                    f"{Colors.RED}Error:{Colors.RESET} Invalid env format '{env_str}'. "
                    "Expected KEY=VAL.",
                    file=sys.stderr,
                )
                sys.exit(1)
            key, val = env_str.split("=", 1)
            new_env[key] = val

    # Apply edits using _locked_modify
    def _edit(jobs):
        for j in jobs:
            if j.get("name") == name:
                if cron:
                    j["cron_expression"] = cron
                if script:
                    j["script_path"] = script
                if new_env:
                    existing_env = j.get("env_vars", {})
                    existing_env.update(new_env)
                    j["env_vars"] = existing_env
                break
        return jobs

    queue._locked_modify(_edit)

    # Show warning if job is currently running (Requirement 26.3)
    if job.get("state") == "running":
        print(
            f"{Colors.YELLOW}⚠️  Warning: Job '{name}' is currently running. "
            f"Changes will take effect on the next execution cycle.{Colors.RESET}"
        )

    changes = []
    if cron:
        changes.append(f"cron_expression='{cron}'")
    if script:
        changes.append(f"script_path='{script}'")
    if new_env:
        changes.append(f"env_vars: {', '.join(f'{k}={v}' for k, v in new_env.items())}")
    print(f"Job '{name}' updated: {', '.join(changes)}")


def _handle_channel(args):
    """Dispatch channel subcommands."""
    from .channels import ChannelManager, CredentialManager, VALID_PLATFORMS, VALID_MODES
    from .colors import Colors

    manager = ChannelManager()

    if args.channel_command == "add":
        try:
            channel = manager.add_channel(
                name=args.name,
                platform=args.platform,
                mode=args.mode,
                owner_ids=args.owner_ids,
            )
            print(f"  {Colors.GREEN}✓{Colors.RESET} Channel '{args.name}' created "
                  f"(platform: {args.platform}, mode: {args.mode})")
            print()
            print("  Next steps:")
            print(f"    1. Set credentials:  kognisant channel set-credentials {args.name}")
            if args.mode in ("assistant", "hybrid"):
                print(f"    2. Set owner ID:     kognisant channel add {args.name} --owner-id <your_platform_id>")
            print(f"    3. Start:            kognisant channel start {args.name}")
        except ValueError as e:
            print(f"  {Colors.RED}Error:{Colors.RESET} {e}", file=sys.stderr)

    elif args.channel_command == "remove":
        if manager.remove_channel(args.name):
            print(f"  {Colors.GREEN}✓{Colors.RESET} Channel '{args.name}' removed")
        else:
            print(f"  {Colors.RED}Error:{Colors.RESET} Channel '{args.name}' not found", file=sys.stderr)

    elif args.channel_command == "list":
        from . import json_stream
        channels = manager.list_channels()

        if json_stream.is_active():
            json_stream.emit({
                "type": "command_result",
                "command": "channel_list",
                "data": [{"name": ch.get("name"), "platform": ch.get("platform"),
                          "mode": ch.get("mode"), "state": ch.get("state", "stopped")}
                         for ch in channels],
            })
            return

        if not channels:
            print("  No channels configured. Create one with: kognisant channel add <name> --platform <platform>")
            return

        print(f"\n  {Colors.BOLD}Channels:{Colors.RESET}\n")
        for ch in channels:
            name = ch.get("name", "?")
            platform = ch.get("platform", "?")
            mode = ch.get("mode", "?")
            state = ch.get("state", "stopped")

            # State color
            if state == "running":
                state_display = f"{Colors.GREEN}{state}{Colors.RESET}"
            elif state == "error":
                state_display = f"{Colors.RED}{state}{Colors.RESET}"
            elif state == "paused":
                state_display = f"{Colors.YELLOW}{state}{Colors.RESET}"
            else:
                state_display = state

            print(f"    {Colors.CYAN}{name}{Colors.RESET}  "
                  f"{platform} | {mode} | {state_display}")
        print()

    elif args.channel_command == "status":
        if args.name:
            ch = manager.get_channel(args.name)
            if not ch:
                print(f"  {Colors.RED}Error:{Colors.RESET} Channel '{args.name}' not found")
                return
            print(f"\n  {Colors.BOLD}Channel: {ch['name']}{Colors.RESET}")
            print(f"  Platform:  {ch.get('platform', '?')}")
            print(f"  Mode:      {ch.get('mode', '?')}")
            print(f"  State:     {ch.get('state', 'stopped')}")
            print(f"  Created:   {ch.get('created_at', '?')}")
            print(f"  Owners:    {ch.get('owner_ids', [])}")
            if ch.get("mode") in ("manager", "hybrid"):
                mc = ch.get("manager_config", {})
                persona = mc.get("persona", {})
                print(f"  Voice:     {persona.get('voice', 'not set')}")
                cg = mc.get("cost_gate", {})
                print(f"  LLM Budget: {cg.get('max_llm_calls_per_day', '?')}/day")
            print()
        else:
            # Show all statuses
            _handle_channel_list_with_status(manager)

    elif args.channel_command == "start":
        ch = manager.get_channel(args.name)
        if not ch:
            print(f"  {Colors.RED}Error:{Colors.RESET} Channel '{args.name}' not found")
            return
        # Check adapter script exists
        script_path = manager.get_adapter_script_path(args.name)
        if not script_path:
            platform = ch.get("platform", "unknown")
            print(f"  {Colors.RED}Error:{Colors.RESET} No adapter script found for platform '{platform}'")
            print(f"  Expected: ~/.kognisant_core/scripts/channel_{platform}.py")
            print(f"  Install a reference adapter or generate one with /agent")
            return
        # Check crypto backend
        backend = CredentialManager.has_crypto_backend()
        if not backend:
            print(f"  {Colors.RED}Error:{Colors.RESET} No secure credential storage available.")
            print("    Option 1: pip install cryptography")
            print("    Option 2: Configure OS keyring")
            return
        manager.update_state(args.name, "starting")
        print(f"  {Colors.GREEN}✓{Colors.RESET} Channel '{args.name}' marked for start")
        print("    The daemon will pick it up on its next poll cycle (15s).")
        print("    Check status: kognisant channel status " + args.name)

    elif args.channel_command == "stop":
        if manager.update_state(args.name, "stopped"):
            print(f"  {Colors.GREEN}✓{Colors.RESET} Channel '{args.name}' marked for stop")
        else:
            print(f"  {Colors.RED}Error:{Colors.RESET} Channel '{args.name}' not found")

    elif args.channel_command == "set-credentials":
        ch = manager.get_channel(args.name)
        if not ch:
            print(f"  {Colors.RED}Error:{Colors.RESET} Channel '{args.name}' not found")
            return

        backend = CredentialManager.has_crypto_backend()
        if not backend:
            print(f"  {Colors.RED}Error:{Colors.RESET} No secure credential storage available.")
            print("    pip install cryptography")
            return

        import getpass
        platform = ch.get("platform", "unknown")

        # Platform-specific credential prompts
        cred_prompts = {
            "telegram": [("bot_token", "Telegram Bot Token")],
            "x": [("api_key", "X API Key"), ("api_secret", "X API Secret"),
                   ("access_token", "Access Token"), ("access_secret", "Access Token Secret")],
            "discord": [("bot_token", "Discord Bot Token")],
            "reddit": [("client_id", "Reddit Client ID"), ("client_secret", "Reddit Client Secret"),
                       ("username", "Reddit Username"), ("password", "Reddit Password")],
            "webhook": [("hmac_secret", "Webhook HMAC Shared Secret")],
        }
        prompts = cred_prompts.get(platform, [("api_key", "API Key")])

        if backend == "cryptography":
            passphrase = getpass.getpass("  Master passphrase (for encryption): ")
            if not passphrase:
                print(f"  {Colors.RED}Aborted.{Colors.RESET} Passphrase required.")
                return
        else:
            passphrase = ""

        for key_name, label in prompts:
            value = getpass.getpass(f"  {label}: ")
            if value:
                try:
                    CredentialManager.store_credential(args.name, key_name, value, passphrase)
                except RuntimeError as e:
                    print(f"  {Colors.RED}Error:{Colors.RESET} {e}")
                    return

        print(f"  {Colors.GREEN}✓{Colors.RESET} Credentials encrypted and stored for '{args.name}'")

    elif args.channel_command == "lockdown":
        channels = manager.list_channels()
        stopped = 0
        for ch in channels:
            if ch.get("state") in ("running", "starting", "paused"):
                manager.update_state(ch["name"], "stopped")
                stopped += 1
        print(f"  {Colors.RED}⚠ LOCKDOWN:{Colors.RESET} {stopped} channel(s) stopped")

    elif args.channel_command == "revoke-sessions":
        # Sessions are in-memory in the daemon, so we signal via state
        manager.update_config(args.name, {"_revoke_sessions": True})
        print(f"  {Colors.GREEN}✓{Colors.RESET} Session revocation flagged for '{args.name}'")
        print("    Active sessions will be invalidated on next daemon poll.")

    elif args.channel_command == "logs":
        from .channels import LOGS_DIR
        log_path = os.path.join(LOGS_DIR, f"{args.name}.log")
        if not os.path.exists(log_path):
            print(f"  No logs found for channel '{args.name}'")
            return
        if args.follow:
            print(f"  Following {log_path} (Ctrl+C to stop)\n")
            try:
                with open(log_path, "r") as f:
                    f.seek(0, 2)  # End of file
                    while True:
                        line = f.readline()
                        if line:
                            print(line, end="")
                        else:
                            time.sleep(0.5)
            except KeyboardInterrupt:
                pass
        else:
            with open(log_path, "r") as f:
                lines = f.readlines()
            for line in lines[-50:]:
                print(line, end="")

    elif args.channel_command == "test":
        ch = manager.get_channel(args.name)
        if not ch:
            print(f"  {Colors.RED}Error:{Colors.RESET} Channel '{args.name}' not found")
            return
        if ch.get("state") != "running":
            print(f"  {Colors.YELLOW}Warning:{Colors.RESET} Channel is not running (state: {ch.get('state')})")
        # Write a test event to the socket
        sock_path = f"/tmp/kognisant_channel_{args.name}.sock"
        if os.path.exists(sock_path):
            print(f"  Socket exists: {sock_path}")
            print(f"  {Colors.GREEN}✓{Colors.RESET} Channel appears connectable")
        else:
            print(f"  {Colors.RED}✗{Colors.RESET} No socket found — channel not running")

    else:
        print("  Usage: kognisant channel <add|remove|list|status|start|stop|set-credentials|lockdown|logs|test>")


def _handle_channel_list_with_status(manager):
    """Show all channels with status."""
    from .colors import Colors
    channels = manager.list_channels()
    if not channels:
        print("  No channels configured.")
        return
    print(f"\n  {Colors.BOLD}Channels:{Colors.RESET}\n")
    for ch in channels:
        name = ch.get("name", "?")
        platform = ch.get("platform", "?")
        mode = ch.get("mode", "?")
        state = ch.get("state", "stopped")
        if state == "running":
            state_display = f"{Colors.GREEN}●{Colors.RESET} {state}"
        elif state == "error":
            state_display = f"{Colors.RED}●{Colors.RESET} {state}"
        else:
            state_display = f"○ {state}"
        print(f"    {state_display}  {Colors.CYAN}{name}{Colors.RESET} ({platform}, {mode})")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="cli-kognisant: Autonomous AI copilot with background job execution (POSIX-only daemon)."
    )
    parser.add_argument(
        "--json-stream", action="store_true", default=False,
        help="Output structured JSON events to stdout for GUI/CI consumption (protocol v1.0)"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    subparsers.add_parser("init", help="Initialize a Kognisant project directory")
    chat_parser = subparsers.add_parser(
        "chat",
        help="Start an interactive multi-turn chat session with Ollama detection",
    )
    chat_parser.add_argument(
        "--resume-session", default=None, dest="resume_session",
        help="Resume a previous session from a session file (for crash recovery)"
    )
    subparsers.add_parser(
        "status", help="Show workspace health, model connectivity, and spec status"
    )
    subparsers.add_parser(
        "setup", help="Configure AI model providers (API keys, endpoints)"
    )

    greet_parser = subparsers.add_parser("greet", help="Greet a user")
    greet_parser.add_argument(
        "-n", "--name", type=str, default="World", help="The name to greet"
    )
    greet_parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose output"
    )

    spec_parser = subparsers.add_parser(
        "spec", help="Create, resume, or manage Spec-Driven Development feature workflows"
    )
    spec_parser.add_argument(
        "name",
        nargs="?",
        default=None,
        help="Feature name to create or resume (.kognisant/specs/<name>/)",
    )
    spec_parser.add_argument(
        "-l", "--list", action="store_true", help="List all feature specs with status"
    )
    spec_parser.add_argument(
        "-r", "--resume", action="store_true", help="Resume an existing spec from where you left off"
    )
    spec_parser.add_argument(
        "-s", "--status", action="store_true", help="Show detailed status of a spec"
    )

    # --- Daemon subcommands ---
    daemon_parser = subparsers.add_parser(
        "daemon", help="Manage the background daemon process (POSIX-only: Linux, macOS)"
    )
    daemon_subparsers = daemon_parser.add_subparsers(
        dest="daemon_command", help="Daemon actions"
    )
    daemon_subparsers.add_parser("start", help="Start the background daemon (forks to background)")
    daemon_subparsers.add_parser("stop", help="Stop the running daemon (sends SIGTERM, graceful shutdown)")
    daemon_subparsers.add_parser("restart", help="Stop the running daemon and start a new one")
    daemon_subparsers.add_parser("status", help="Show daemon running state, PID, and uptime")
    daemon_subparsers.add_parser("logs", help="Show daemon log output (daemon.log)")

    # --- Job subcommands ---
    job_parser = subparsers.add_parser(
        "job", help="Manage background jobs (add, list, cancel, remove, edit, logs)"
    )
    job_subparsers = job_parser.add_subparsers(
        dest="job_command", help="Job actions"
    )

    # job add
    job_add_parser = job_subparsers.add_parser("add", help="Add a new job to the queue")
    job_add_parser.add_argument(
        "-n", "--name", required=True, help="Job name (1-64 chars, [a-z0-9_-])"
    )
    job_add_parser.add_argument(
        "-s", "--script", default=None,
        help="Script name in ~/.kognisant_core/scripts/ (required for scheduled/persistent jobs)"
    )
    job_add_parser.add_argument(
        "-t", "--type", required=True, dest="job_type",
        help="Job type: scheduled, persistent, or agent"
    )
    job_add_parser.add_argument(
        "-c", "--cron", default=None,
        help=(
            "Cron expression for scheduled jobs (5-field format: min hour dom month dow). "
            "All cron expressions are evaluated in UTC."
        )
    )
    job_add_parser.add_argument(
        "--task", default=None,
        help="Task description (required for agent jobs)"
    )
    job_add_parser.add_argument(
        "-e", "--env", action="append", default=None,
        help="Environment variable as KEY=VAL (repeatable)"
    )
    job_add_parser.add_argument(
        "--env-file", default=None, dest="env_file",
        help=(
            "Path to file containing environment variables (KEY=VAL per line). "
            "Note: env vars are stored in plaintext in jobs.json; the system is "
            "NOT a secrets manager. Use --env-file with chmod 600 files for "
            "sensitive values."
        ),
    )

    # job list
    job_subparsers.add_parser("list", help="List all jobs with state, run count, exit code, and next run time")

    # job cancel
    job_cancel_parser = job_subparsers.add_parser("cancel", help="Cancel a job (sends SIGTERM if running)")
    job_cancel_parser.add_argument("name", help="Name of the job to cancel")

    # job logs
    job_logs_parser = job_subparsers.add_parser("logs", help="Show job output logs")
    job_logs_parser.add_argument("name", help="Name of the job to show logs for")
    job_logs_parser.add_argument(
        "-f", "--follow", action="store_true", default=False,
        help="Continuously display new lines (polls every 500ms, Ctrl+C to stop)"
    )

    # job remove
    job_remove_parser = job_subparsers.add_parser("remove", help="Permanently remove a job (terminates if running)")
    job_remove_parser.add_argument("name", help="Name of the job to remove")

    # job edit
    job_edit_parser = job_subparsers.add_parser("edit", help="Edit a job's configuration in place (--cron, --env, --script)")
    job_edit_parser.add_argument("name", help="Name of the job to edit")
    job_edit_parser.add_argument(
        "-c", "--cron", default=None,
        help="New cron expression (5-field format, evaluated in UTC)"
    )
    job_edit_parser.add_argument(
        "-e", "--env", action="append", default=None,
        help="Set/update environment variable as KEY=VAL (repeatable, merges with existing)"
    )
    job_edit_parser.add_argument(
        "-s", "--script", default=None,
        help="New script path (relative to ~/.kognisant_core/scripts/)"
    )

    # --- Channel subcommands ---
    channel_parser = subparsers.add_parser(
        "channel", help="Manage channels (remote AI access + social media bots)"
    )
    channel_subparsers = channel_parser.add_subparsers(
        dest="channel_command", help="Channel actions"
    )

    # channel add
    channel_add_parser = channel_subparsers.add_parser("add", help="Create a new channel")
    channel_add_parser.add_argument(
        "name", help="Channel name (1-48 chars, [a-z0-9-])"
    )
    channel_add_parser.add_argument(
        "--platform", required=True,
        help="Target platform (telegram, x, discord, reddit, whatsapp, signal, webhook)"
    )
    channel_add_parser.add_argument(
        "--mode", default="assistant",
        help="Channel mode: assistant, manager, or hybrid (default: assistant)"
    )
    channel_add_parser.add_argument(
        "--owner-id", action="append", default=None, dest="owner_ids",
        help="Platform-native owner ID (repeatable for multiple owners)"
    )

    # channel remove
    channel_remove_parser = channel_subparsers.add_parser("remove", help="Remove a channel and its data")
    channel_remove_parser.add_argument("name", help="Name of the channel to remove")

    # channel list
    channel_subparsers.add_parser("list", help="List all channels with status")

    # channel status
    channel_status_parser = channel_subparsers.add_parser("status", help="Show detailed channel status")
    channel_status_parser.add_argument("name", nargs="?", help="Channel name (shows all if omitted)")

    # channel start
    channel_start_parser = channel_subparsers.add_parser("start", help="Start a channel")
    channel_start_parser.add_argument("name", help="Channel name to start")

    # channel stop
    channel_stop_parser = channel_subparsers.add_parser("stop", help="Stop a channel")
    channel_stop_parser.add_argument("name", help="Channel name to stop")

    # channel set-credentials
    channel_creds_parser = channel_subparsers.add_parser(
        "set-credentials", help="Set credentials for a channel (interactive)"
    )
    channel_creds_parser.add_argument("name", help="Channel name")

    # channel lockdown
    channel_subparsers.add_parser("lockdown", help="Emergency stop ALL channels")

    # channel revoke-sessions
    channel_revoke_parser = channel_subparsers.add_parser(
        "revoke-sessions", help="Revoke all active remote sessions for a channel"
    )
    channel_revoke_parser.add_argument("name", help="Channel name")

    # channel logs
    channel_logs_parser = channel_subparsers.add_parser("logs", help="Show channel logs")
    channel_logs_parser.add_argument("name", help="Channel name")
    channel_logs_parser.add_argument(
        "-f", "--follow", action="store_true", default=False, help="Follow log output"
    )

    # channel test
    channel_test_parser = channel_subparsers.add_parser("test", help="Send a test message to verify connectivity")
    channel_test_parser.add_argument("name", help="Channel name to test")

    args = parser.parse_args()

    # Activate JSON stream mode if requested
    if args.json_stream:
        from .json_stream import activate as activate_json_stream
        activate_json_stream()

    if args.command == "init":
        init_project()
    elif args.command == "status":
        _handle_status()
    elif args.command == "setup":
        _handle_setup()
    elif args.command == "chat":
        chat_flow(resume_session=getattr(args, "resume_session", None))
    elif args.command == "greet":
        if args.verbose:
            print(f"[DEBUG] CLI started with arguments: {args}", file=sys.stderr)
        print(f"Hello, {args.name}!")
    elif args.command == "spec":
        if args.list:
            root = find_project_root()
            if not root:
                print(
                    f"{Colors.RED}[Error] No active project. Run 'kognisant init' first.{Colors.RESET}"
                )
                return

            from .sdd import get_all_specs_status

            specs = get_all_specs_status(root)
            if specs:
                print(f"\n  {Colors.BOLD}Feature Specifications:{Colors.RESET}\n")
                for s in specs:
                    name = s["name"]
                    status = s["status"]
                    done = s["tasks_done"]
                    total = s["tasks_total"]

                    # Status color coding
                    if status == "DONE":
                        status_display = f"{Colors.GREEN}{status}{Colors.RESET}"
                    elif status == "BUILD":
                        status_display = f"{Colors.YELLOW}{status}{Colors.RESET}"
                    elif status in ("DEFINE", "DESIGN", "PLAN"):
                        status_display = f"{Colors.CYAN}{status}{Colors.RESET}"
                    else:
                        status_display = status

                    progress = f"({done}/{total})" if total > 0 else ""
                    print(
                        f"    {Colors.CYAN}{name}{Colors.RESET}  "
                        f"{status_display} {progress}"
                    )
                print()
            else:
                print(
                    f"{Colors.YELLOW}No feature specs found. Create one with 'kognisant spec <name>'.{Colors.RESET}"
                )
        elif args.name:
            root = find_project_root()
            if not root:
                print(
                    f"{Colors.RED}[Error] No active project. Run 'kognisant init' first.{Colors.RESET}"
                )
                return

            from .config import get_compiled_models, get_default_model, get_project_info
            from .sdd import SpecManager, spec_interactive_flow

            project_info = get_project_info()

            # Get model config for AI-assisted authoring
            compiled_models = get_compiled_models()
            model_config = None
            if compiled_models:
                model_config = get_default_model(compiled_models)
                if not model_config:
                    model_config = compiled_models[0] if compiled_models else None

            if args.status:
                # Show detailed status
                spec = SpecManager(root, args.name)
                state = spec.load()
                if not state:
                    print(f"  {Colors.RED}Spec '{args.name}' not found.{Colors.RESET}")
                    return
                done, total = spec.get_progress()
                print(f"\n  {'═' * 50}")
                print(f"  🛠️  {Colors.BOLD}Spec: {args.name}{Colors.RESET}")
                print(f"  Status: {Colors.CYAN}{state.get('status', 'UNKNOWN')}{Colors.RESET}")
                if total > 0:
                    print(f"  Progress: {done}/{total} tasks completed")
                print(f"  Created: {state.get('created_at', 'N/A')}")
                print(f"  Updated: {state.get('last_updated', 'N/A')}")
                print(f"  {'═' * 50}\n")

                tasks = spec.get_tasks()
                if tasks:
                    print(f"  {Colors.BOLD}Tasks:{Colors.RESET}")
                    for i, task in enumerate(tasks):
                        check = f"{Colors.GREEN}✓{Colors.RESET}" if task.get("status") == "done" else "□"
                        phase = f"[P{task.get('phase', '?')}]"
                        print(f"    {check} {phase} {task['description']}")
                    print()
            else:
                # Create or resume spec
                spec_interactive_flow(
                    root,
                    args.name,
                    resume=args.resume,
                    model_config=model_config,
                    project_info=project_info,
                    compiled_models=compiled_models,
                )
        else:
            spec_parser.print_help()
    elif args.command == "daemon":
        _handle_daemon(args)
    elif args.command == "job":
        _handle_job(args)
    elif args.command == "channel":
        _handle_channel(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
