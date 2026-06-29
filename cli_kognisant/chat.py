import json
import os
import time

from .colors import (
    Colors,
    Spinner,
    print_animated_logo,
    prompt_boxed_input,
    render_markdown,
)
from .config import (
    get_compiled_models,
    get_default_model,
    get_project_info,
    is_world_model_enabled,
    load_global_skills,
    load_project_context,
    load_project_memory_guidelines,
    load_providers_and_pool,
    save_chat_session,
    save_providers_and_pool,
    set_default_model,
)
from .runtime import execute_message


def get_tool_call_description(func_name, func_args):
    """Generates a clean, human-readable description for any native or dynamic tool call."""
    try:
        args = json.loads(func_args) if isinstance(func_args, str) else func_args
    except Exception:
        args = {}

    if func_name in ("read_project_file", "read_global_file"):
        file_path = args.get("file_path", "file")
        return f"Read file: '{file_path}'"
    elif func_name in ("create_project_file", "create_global_file"):
        file_path = args.get("file_path", "file")
        return f"Create file: '{file_path}'"
    elif func_name in ("edit_project_file", "edit_global_file"):
        file_path = args.get("file_path", "file")
        return f"Edit file: '{file_path}'"
    elif func_name == "create_project_directory":
        directory_path = args.get("directory_path", "directory")
        return f"Create directory: '{directory_path}'"
    elif func_name == "delete_project_path":
        target_path = args.get("path", "path")
        return f"Delete path: '{target_path}'"
    elif func_name == "search_web":
        query = args.get("query", "")
        return f"Search web for: '{query}'"
    elif func_name == "browse_web_page":
        url = args.get("url", "")
        return f"Browse webpage: '{url}'"
    elif func_name == "open_in_native_browser":
        query_or_url = args.get("query_or_url", "")
        return f"Open native browser for: '{query_or_url}'"
    elif func_name == "capture_active_browser_console":
        return "Capture active browser console logs"
    elif func_name == "shell_execution":
        command = args.get("command", "")
        return f"Execute command: '{command}'"
    else:
        desc = f"Execute tool: {func_name}"
        if args:
            args_summary = ", ".join([f"{k}={v}" for k, v in args.items()])
            desc += f" with arguments ({args_summary})"
        return desc


def requires_api_key(provider_name, api_key):
    """Determines if a model provider requires an API key prompt (Ollama is exempt)."""
    if "ollama" in provider_name.lower():
        return False
    # Standard requirement: key is missing or is the default 'your-...' placeholder
    return not api_key or "your-" in api_key


def build_system_prompt(project_info):
    """Builds the initial system prompt containing project context, local Membrain, and global skills."""
    files_str = "\n".join([f"- {f}" for f in project_info["files"][:100]])
    if len(project_info["files"]) > 100:
        files_str += f"\n- ... and {len(project_info['files']) - 100} more files"

    context_content = load_project_context(project_info["root"])
    guidelines_content = load_project_memory_guidelines(project_info["root"])
    global_skills = load_global_skills()

    prompt = (
        f"You are Kognisant, an advanced software engineering assistant for the project '{project_info['name']}'.\n"
        f"The project files are:\n{files_str}\n\n"
    )

    if context_content:
        prompt += (
            f"You also have access to the project's persistent build memory (.kognisant/context.md):\n"
            f"```markdown\n{context_content}\n```\n\n"
        )

    if guidelines_content:
        prompt += (
            f"You must strictly adhere to the project's persistent memory guidelines (.kognisant/memory-guidlines.md):\n"
            f"```markdown\n{guidelines_content}\n```\n\n"
            f"IMPORTANT: Only the Reflection and Persistence stages are authorized to write or edit '.kognisant/context.md' and '.kognisant/memory-guidlines.md'.\n\n"
        )

    if global_skills:
        prompt += "You possess the following universal software engineering skills (names only; apply them to any coding work):\n"
        for skill in global_skills:
            prompt += f"  • {skill['name']}\n"
        prompt += "\n"

    prompt += "You can refer to these files. If you need to view a file's content or list the files, you MUST use the provided tools (like 'read_project_file' or 'list_project_files') to retrieve the information directly instead of asking the user to read or paste them."

    return {"role": "system", "content": prompt}


def process_slash_commands(
    cleaned_input,
    project_info,
    messages_or_history,
    active_model_config=None,
    is_mock=False,
):
    """Processes in-chat slash commands. Returns True if a command was handled, False otherwise."""
    parts = cleaned_input.split(" ", 1)
    cmd = parts[0].lower()

    if cmd == "/help":
        # Tiered help: /help shows compact overview, /help <cmd> shows detailed info
        help_topic = parts[1].strip().lower().lstrip("/") if len(parts) > 1 else ""

        if help_topic:
            # Detailed help for specific command
            detailed_help = {
                "agent": (
                    f"  {Colors.BOLD}/agent <task>{Colors.RESET}\n\n"
                    f"  Deploys an autonomous PERP swarm to solve complex tasks:\n"
                    f"    Plan → Execute → Reflect → Persist\n\n"
                    f"  Examples:\n"
                    f"    /agent Write tests for the auth module\n"
                    f"    /agent Refactor network.py to support streaming\n"
                    f"    /agent Add input validation to all API endpoints\n\n"
                    f"  The swarm reads/writes files, runs tools, and updates\n"
                    f"  your project context automatically.\n"
                ),
                "spec": (
                    f"  {Colors.BOLD}/spec <subcommand>{Colors.RESET}\n\n"
                    f"  Manage Spec-Driven Development workflows inside chat.\n\n"
                    f"  Subcommands:\n"
                    f"    /spec list              Show all specs with status\n"
                    f"    /spec <name>            Load spec context into chat\n"
                    f"    /spec <name> run        Execute next pending task\n"
                    f"    /spec <name> run all    Execute all remaining tasks\n"
                    f"    /spec <name> done       Mark current task complete\n"
                    f"    /spec <name> status     Show detailed progress\n\n"
                    f"  Create specs from terminal: kognisant spec <name>\n"
                ),
                "model": (
                    f"  {Colors.BOLD}/model{Colors.RESET}\n\n"
                    f"  Opens the Model Pool Wizard to switch active models,\n"
                    f"  add new providers, or update API keys mid-session.\n\n"
                    f"  Your selection is saved as the sticky default for\n"
                    f"  future sessions.\n"
                ),
                "tool": (
                    f"  {Colors.BOLD}/tool <subcommand>{Colors.RESET}\n\n"
                    f"  Manage globally registered custom tools.\n\n"
                    f"  Subcommands:\n"
                    f"    /tool list                       List all active tools\n"
                    f"    /tool register <name> <py> [json] Register a new tool\n"
                    f"    /tool delete <name>              Remove a global tool\n\n"
                    f"  Tools are stored in ~/.kognisant_core/tools/ and are\n"
                    f"  available across all projects.\n"
                ),
                "read": (
                    f"  {Colors.BOLD}/read <file_path>{Colors.RESET}\n\n"
                    f"  Loads a project file directly into conversation memory\n"
                    f"  so the AI can see its full content.\n\n"
                    f"  Example: /read cli_kognisant/network.py\n"
                ),
                "paste": (
                    f"  {Colors.BOLD}/paste{Colors.RESET} (or {Colors.BOLD}/p{Colors.RESET})\n\n"
                    f"  Opens multi-line paste mode for large content like\n"
                    f"  logs, stack traces, or code blocks.\n\n"
                    f"  Type /end on a blank line to submit.\n"
                ),
                "context": (
                    f"  {Colors.BOLD}/context{Colors.RESET}\n\n"
                    f"  Displays your project's persistent build memory\n"
                    f"  (.kognisant/context.md). Shows current phases,\n"
                    f"  tracked tasks, and architectural decisions.\n"
                ),
                "clear": (
                    f"  {Colors.BOLD}/clear{Colors.RESET}\n\n"
                    f"  Flushes conversation history while preserving the\n"
                    f"  system prompt. Gives you a fresh context window.\n"
                ),
                "daemon": (
                    f"  {Colors.BOLD}/daemon <subcommand>{Colors.RESET}\n\n"
                    f"  Control the background daemon process from chat.\n\n"
                    f"  Subcommands:\n"
                    f"    /daemon status      Show if daemon is running, PID, uptime\n"
                    f"    /daemon start       Start the daemon process\n"
                    f"    /daemon stop        Stop the running daemon (SIGTERM)\n"
                    f"    /daemon restart     Stop + start cycle\n\n"
                    f"  The daemon polls the job queue every 15 seconds and\n"
                    f"  executes scheduled, persistent, and agent jobs.\n\n"
                    f"  Platform:  POSIX-only (Linux, macOS). No Windows support.\n"
                    f"  Requires:  fcntl.flock() and os.fork()\n"
                    f"  Cron:      All cron expressions are evaluated in UTC.\n"
                    f"  Timeouts:  Scheduled 3600s, agent 1800s, shutdown 10s/process.\n"
                ),
                "jobs": (
                    f"  {Colors.BOLD}/jobs{Colors.RESET}\n\n"
                    f"  List all jobs in the queue with their:\n"
                    f"    • Name, type, and state\n"
                    f"    • Run count and last exit code\n"
                    f"    • Last run time (UTC)\n"
                    f"    • Next run time (for scheduled jobs, UTC)\n"
                    f"    • PID (if currently running)\n"
                ),
                "job": (
                    f"  {Colors.BOLD}/job <subcommand> <name>{Colors.RESET}\n\n"
                    f"  Manage a specific job by name.\n\n"
                    f"  Subcommands:\n"
                    f"    /job stop <name>      Send SIGTERM to running subprocess, set cancelled\n"
                    f"    /job logs <name>      Show last 30 lines of job log\n"
                    f"    /job restart <name>   Restart a stopped/crash-looped job\n"
                    f"    /job remove <name>    Permanently remove job from queue\n\n"
                    f"  Restart resets state to 'pending' and clears the\n"
                    f"  restart counter (persistent jobs only).\n\n"
                    f"  Note: If the daemon is not running, restarted jobs\n"
                    f"  will remain pending until the daemon starts.\n"
                ),
                "goals": (
                    f"  {Colors.BOLD}/goals [subcommand]{Colors.RESET}\n\n"
                    f"  View and manage AI-generated goals for your project.\n\n"
                    f"  Subcommands:\n"
                    f"    /goals                List all active goals with priority\n"
                    f"    /goals accept <id>    Accept a goal (integrates into workflow)\n"
                    f"    /goals dismiss <id>   Dismiss a goal (hides from suggestions)\n\n"
                    f"  Goals are generated from your project's world model\n"
                    f"  and shown at session start. Requires world model to be enabled.\n"
                ),
            }

            if help_topic in detailed_help:
                print(f"\n{detailed_help[help_topic]}")
            else:
                print(f"  {Colors.YELLOW}No detailed help for '{help_topic}'. Available: agent, spec, model, tool, read, paste, context, clear, daemon, jobs, job, goals{Colors.RESET}\n")
            return True

        # Compact help overview
        print(f"\n  📖 {Colors.BOLD}Commands:{Colors.RESET}\n")
        print(f"  {Colors.BOLD}Basics{Colors.RESET}        /files  /read <path>  /context  /clear")
        print(f"  {Colors.BOLD}AI Config{Colors.RESET}     /model  /providers")
        print(f"  {Colors.BOLD}Power Tools{Colors.RESET}   /agent <task>  /spec  /tool")
        print(f"  {Colors.BOLD}Daemon{Colors.RESET}        /daemon status|start|stop|restart")
        print(f"  {Colors.BOLD}Jobs{Colors.RESET}          /jobs  /job stop|logs|restart|remove <name>")
        print(f"  {Colors.BOLD}Channels{Colors.RESET}      /channels  /channel status|start|stop|pause|escalations <name>")
        print(f"  {Colors.BOLD}Goals{Colors.RESET}         /goals  /goals accept|dismiss <id>")
        print(f"  {Colors.BOLD}World Model{Colors.RESET}   /worldmodel [enable|disable|status]")
        print(f"  {Colors.BOLD}Telemetry{Colors.RESET}     /telemetry [model_name]")
        print(f"  {Colors.BOLD}Input{Colors.RESET}         /paste (multi-line mode)")
        print(f"  {Colors.BOLD}Session{Colors.RESET}       exit / quit\n")
        print(f"  {Colors.YELLOW}Note:{Colors.RESET} Daemon & jobs require POSIX (Linux/macOS). Cron times are in UTC.")
        print(f"  Type {Colors.CYAN}/help <command>{Colors.RESET} for details (e.g. /help agent)\n")
        return True

    elif cmd == "/clear":
        if (
            messages_or_history
            and isinstance(messages_or_history[0], dict)
            and messages_or_history[0].get("role") == "system"
        ):
            system_prompt = messages_or_history[0]
            messages_or_history.clear()
            messages_or_history.append(system_prompt)
        else:
            messages_or_history.clear()
        print(f"{Colors.MAGENTA}[*] Conversation history cleared.{Colors.RESET}\n")
        return True

    elif cmd == "/context":
        if not project_info:
            print(
                f"{Colors.YELLOW}[!] No active project detected. Run 'kognisant init' first.{Colors.RESET}\n"
            )
            return True
        content = load_project_context(project_info["root"])
        if not content:
            print(
                f"{Colors.YELLOW}[!] context.md not found or empty in .kognisant/.{Colors.RESET}\n"
            )
            return True
        print(
            f"\n{Colors.BOLD}--- .kognisant/context.md (Persistent Memory) ---{Colors.RESET}"
        )
        print(content.strip())
        print(
            f"{Colors.BOLD}───────────────────────────────────────────────────{Colors.RESET}\n"
        )
        return True

    elif cmd == "/skills":
        skills = load_global_skills()
        if not skills:
            print(
                f"{Colors.YELLOW}[!] No global transferable skills found in ~/.kognisant_core/skills/{Colors.RESET}\n"
            )
            return True
        print(
            f"\n{Colors.BOLD}--- Global Transferable Skills (Core Memory) ---{Colors.RESET}"
        )
        for skill in skills:
            print(f"  - {Colors.CYAN}{skill['name']}{Colors.RESET}")
        print(
            f"{Colors.BOLD}────────────────────────────────────────────────{Colors.RESET}\n"
        )
        return True

    elif cmd == "/model":
        if is_mock:
            print(
                "  ⚠️  [Warning] Model switching is disabled in offline Mock Chat mode."
            )
            return True

        compiled_models = get_compiled_models()
        if not compiled_models:
            print("  ⚠️  [Warning] No AI models are currently configured.")
            return True

        # Use the unified select_model wizard directly
        selected = select_model(
            compiled_models,
            label="Kognisant Model Pool Wizard",
            active_model_config=active_model_config,
        )

        if selected and isinstance(selected, dict) and active_model_config:
            active_model_config.clear()
            active_model_config.update(selected)
            set_default_model(selected)
            print(
                f"  🔄 {Colors.GREEN}Model Switched:{Colors.RESET} Active model is now '{selected['display_name']}' ({selected['provider']}).\n"
            )
        return True

    elif cmd == "/providers":
        providers, pool = load_providers_and_pool()

        selected_models = []
        if isinstance(pool, dict):
            selected_models = pool.get("selected_models", [])

        if not selected_models:
            print(
                f"  ⚠️  {Colors.YELLOW}[Warning] No providers are currently configured.{Colors.RESET}\n"
            )
            return True

        print(f"\n{Colors.BOLD}--- Configured AI Providers ---{Colors.RESET}")
        for group in selected_models:
            pname = group.get("provider", "Unknown")
            api_key = group.get("api_key", "")
            key_status = (
                f"{Colors.GREEN}Set{Colors.RESET}"
                if api_key and "your-" not in api_key
                else f"{Colors.YELLOW}Not Set / Placeholder{Colors.RESET}"
            )
            models_list = group.get("models", [])
            models_names = (
                [m.get("name", "Unknown") for m in models_list]
                if isinstance(models_list, list)
                else []
            )
            models_str = ", ".join(models_names) if models_names else "None"

            # Use base url of first model if available
            base_url = "N/A"
            if isinstance(models_list, list) and models_list:
                base_url = models_list[0].get("api_base_url", "N/A")

            print(f"\n  {Colors.CYAN}{pname}{Colors.RESET}")
            print(f"    Base URL: {base_url}")
            print(f"    API Key:  {key_status}")
            print(f"    Models:   {models_str}")

        compiled = get_compiled_models()
        print(
            f"\n  {Colors.BOLD}Active Model Pool:{Colors.RESET} {len(compiled)} model(s) available."
        )
        print(f"{Colors.BOLD}────────────────────────────{Colors.RESET}\n")
        return True

    elif cmd == "/files":
        if not project_info:
            print(
                f"{Colors.YELLOW}[!] No active project detected. Run 'kognisant init' first.{Colors.RESET}\n"
            )
            return True
        print(f"{Colors.BOLD}Project files:{Colors.RESET}")
        for f in project_info["files"]:
            print(f"  - {f}")
        print()
        return True

    elif cmd == "/read":
        if not project_info:
            print(
                f"{Colors.YELLOW}[!] No active project detected. Run 'kognisant init' first.{Colors.RESET}\n"
            )
            return True
        if len(parts) < 2:
            print(f"{Colors.YELLOW}[!] Usage: /read <file_path>{Colors.RESET}\n")
            return True

        target_file = parts[1].strip()
        full_path = os.path.join(project_info["root"], target_file)

        real_root = os.path.realpath(project_info["root"])
        real_target = os.path.realpath(full_path)

        if not real_target.startswith(real_root):
            print(
                f"{Colors.RED}[!] Error: Access denied. Cannot read files outside the project directory.{Colors.RESET}\n"
            )
            return True

        if not os.path.exists(full_path) or not os.path.isfile(full_path):
            print(
                f"{Colors.RED}[!] Error: File '{target_file}' not found.{Colors.RESET}\n"
            )
            return True

        try:
            with open(full_path, "r", encoding="utf-8", errors="ignore") as f_obj:
                content = f_obj.read()

            if isinstance(messages_or_history, list) and (
                not messages_or_history or isinstance(messages_or_history[0], dict)
            ):
                messages_or_history.append(
                    {
                        "role": "system",
                        "content": f"[User loaded file '{target_file}'. File contents:\n```\n{content}\n```]",
                    }
                )

            print(
                f"{Colors.MAGENTA}[+] Loaded '{target_file}' into conversation context ({len(content)} chars).{Colors.RESET}\n"
            )

            # Inline contextual suggestion for loaded file
            if project_info and is_world_model_enabled(project_info["root"]):
                try:
                    from .goal_engine import ProposalInterface

                    proposal = ProposalInterface(project_root=project_info["root"])
                    suggestion = proposal.get_inline_suggestion(full_path)
                    if suggestion:
                        print(suggestion)
                        print()
                except Exception:
                    pass
        except Exception as ex:
            print(f"{Colors.RED}[!] Error reading file: {ex}{Colors.RESET}\n")
        return True

    elif cmd == "/spec":
        if not project_info:
            print(
                f"{Colors.YELLOW}[!] No active project detected. Run 'kognisant init' first.{Colors.RESET}\n"
            )
            return True

        subparts = parts[1].split() if len(parts) > 1 else []
        subcmd = subparts[0].lower() if subparts else "help"

        if subcmd == "help":
            print(f"\n  📋 {Colors.BOLD}KOGNISANT SPEC COMMANDS{Colors.RESET}")
            print(
                f"  {Colors.CYAN}────────────────────────────────────────────────{Colors.RESET}"
            )
            print(
                f"    {Colors.BOLD}/spec list{Colors.RESET}              - Show all specs with status"
            )
            print(
                f"    {Colors.BOLD}/spec <name>{Colors.RESET}            - Load spec context into chat"
            )
            print(
                f"    {Colors.BOLD}/spec <name> run{Colors.RESET}        - Execute next pending task"
            )
            print(
                f"    {Colors.BOLD}/spec <name> run all{Colors.RESET}    - Execute all remaining tasks"
            )
            print(
                f"    {Colors.BOLD}/spec <name> done{Colors.RESET}       - Mark current task complete"
            )
            print(
                f"    {Colors.BOLD}/spec <name> status{Colors.RESET}     - Show detailed progress"
            )
            print(
                f"  {Colors.CYAN}────────────────────────────────────────────────{Colors.RESET}\n"
            )
            return True

        elif subcmd == "list":
            from .sdd import get_all_specs_status

            specs = get_all_specs_status(project_info["root"])
            if not specs:
                print(
                    f"  {Colors.YELLOW}No specs found. Create one with 'kognisant spec <name>' from terminal.{Colors.RESET}\n"
                )
                return True
            print(f"\n  {Colors.BOLD}Feature Specifications:{Colors.RESET}\n")
            for s in specs:
                name = s["name"]
                status = s["status"]
                done = s["tasks_done"]
                total = s["tasks_total"]
                if status == "DONE":
                    status_display = f"{Colors.GREEN}{status}{Colors.RESET}"
                elif status == "BUILD":
                    status_display = f"{Colors.YELLOW}{status}{Colors.RESET}"
                else:
                    status_display = f"{Colors.CYAN}{status}{Colors.RESET}"
                progress = f"({done}/{total})" if total > 0 else ""
                print(f"    {Colors.CYAN}{name}{Colors.RESET}  {status_display} {progress}")
            print()
            return True

        else:
            # /spec <name> [action]
            spec_name = subcmd
            action = subparts[1].lower() if len(subparts) > 1 else "load"
            run_all = len(subparts) > 2 and subparts[2].lower() == "all"

            from .sdd import SpecManager, run_build_next_task

            spec = SpecManager(project_info["root"], spec_name)
            state = spec.load()

            if not state:
                print(
                    f"  {Colors.YELLOW}Spec '{spec_name}' not found. Create it with 'kognisant spec {spec_name}' from terminal.{Colors.RESET}\n"
                )
                return True

            if action == "status":
                done, total = spec.get_progress()
                status = state.get("status", "UNKNOWN")
                print(f"\n  🛠️  {Colors.BOLD}Spec: {spec_name}{Colors.RESET}")
                print(f"  Status: {Colors.CYAN}{status}{Colors.RESET} ({done}/{total} tasks)")
                tasks = spec.get_tasks()
                if tasks:
                    for task in tasks:
                        check = f"{Colors.GREEN}✓{Colors.RESET}" if task.get("status") == "done" else "□"
                        print(f"    {check} {task['description']}")
                print()
                return True

            elif action == "run":
                compiled_models = get_compiled_models()
                if run_all:
                    # Execute all remaining tasks
                    while True:
                        idx, task = spec.get_next_task()
                        if idx is None:
                            print(f"  ✅ {Colors.GREEN}All spec tasks completed!{Colors.RESET}\n")
                            spec.advance_status("VERIFY")
                            break
                        run_build_next_task(
                            spec,
                            model_config=active_model_config,
                            project_info=project_info,
                            compiled_models=compiled_models,
                        )
                else:
                    run_build_next_task(
                        spec,
                        model_config=active_model_config,
                        project_info=project_info,
                        compiled_models=compiled_models,
                    )
                return True

            elif action == "done":
                idx, task = spec.get_next_task()
                if idx is not None:
                    spec.mark_task_done(idx)
                    done, total = spec.get_progress()
                    print(f"  ✅ Marked task done: '{task['description']}'")
                    print(f"  📊 Progress: {done}/{total}\n")
                else:
                    print(f"  {Colors.GREEN}All tasks already completed!{Colors.RESET}\n")
                return True

            else:
                # Default: load spec context into chat session
                spec_context = spec.get_spec_context_for_agent()
                if isinstance(messages_or_history, list) and (
                    not messages_or_history or isinstance(messages_or_history[0], dict)
                ):
                    messages_or_history.append(
                        {
                            "role": "system",
                            "content": f"[Loaded spec context for feature '{spec_name}']\n{spec_context}",
                        }
                    )
                done, total = spec.get_progress()
                print(
                    f"  📋 {Colors.GREEN}Spec '{spec_name}' loaded into context.{Colors.RESET} "
                    f"({state.get('status', '?')} — {done}/{total} tasks)\n"
                )
                return True

    elif cmd == "/agent":
        if not project_info:
            print(
                f"{Colors.YELLOW}[!] No active project detected. Run 'kognisant init' first.{Colors.RESET}\n"
            )
            return True
        if len(parts) < 2:
            print(
                f"{Colors.YELLOW}[!] Usage: /agent <task_to_orchestrate>{Colors.RESET}\n"
            )
            return True
        task = parts[1].strip()

        from .agents import perp_orchestrate

        # Execute the full autonomous Plan, Execute, Reflect, Persist loop
        perp_orchestrate(task, project_info, get_compiled_models(), force_mock=is_mock)

        # Convo Persistence context link: Read newly updated context.md and inject directly into main session memory
        updated_context = load_project_context(project_info["root"])
        if isinstance(messages_or_history, list) and (
            not messages_or_history or isinstance(messages_or_history[0], dict)
        ):
            messages_or_history.append(
                {
                    "role": "system",
                    "content": (
                        f"[Autonomous agent swarm successfully executed your task: '{task}'. "
                        f"The local build memory context.md has been self-updated to reflect this. "
                        f"Current updated .kognisant/context.md contents:\n```markdown\n{updated_context}\n```]"
                    ),
                }
            )
            print(
                f"  🧠 {Colors.CYAN}Session Memory Synchronized:{Colors.RESET} Injected updated Membrain context into chat context."
            )
        elif isinstance(messages_or_history, list):
            messages_or_history.append(
                f"[Autonomous agent swarm completed task: '{task}'. Local workspace .kognisant/context.md has been self-modified.]"
            )

        return True

    elif cmd == "/tool":
        # Global Tool Management Wizard
        subparts = parts[1].split() if len(parts) > 1 else []
        subcmd = subparts[0].lower() if subparts else "help"

        global_tools_dir = os.path.expanduser("~/.kognisant_core/tools")

        if subcmd == "help":
            print(f"\n  ⚙️  {Colors.BOLD}KOGNISANT GLOBAL TOOL UTILITY{Colors.RESET}")
            print(
                f"  {Colors.CYAN}────────────────────────────────────────────────{Colors.RESET}"
            )
            print(
                f"    {Colors.BOLD}/tool list{Colors.RESET}                   - List all active global tools and schemas"
            )
            print(
                f"    {Colors.BOLD}/tool register <name> <py_path> [json_path]{Colors.RESET}"
            )
            print(
                "                                 - Register/copy a local Python script and schema to core"
            )
            print(
                f"    {Colors.BOLD}/tool delete <name>{Colors.RESET}         - Permanently delete a global tool and its schema"
            )
            print(
                f"  {Colors.CYAN}────────────────────────────────────────────────{Colors.RESET}\n"
            )
            return True

        elif subcmd == "list":
            if not os.path.exists(global_tools_dir):
                print(
                    f"  [!] Global tools directory '{global_tools_dir}' does not exist yet.\n"
                )
                return True
            files = os.listdir(global_tools_dir)
            py_files = sorted([f[:-3] for f in files if f.endswith(".py")])
            if not py_files:
                print(
                    f"  {Colors.YELLOW}[!] No global tools registered yet.{Colors.RESET}\n"
                )
                return True
            print(
                f"\n{Colors.BOLD}--- Globally Registered Active Tools ---{Colors.RESET}"
            )
            for tool in py_files:
                has_json = "Yes" if f"{tool}.json" in files else "No (Missing Schema)"
                status_color = Colors.GREEN if has_json == "Yes" else Colors.RED
                print(
                    f"  - {Colors.CYAN}{tool}{Colors.RESET} (Schema: {status_color}{has_json}{Colors.RESET})"
                )
            print(
                f"{Colors.BOLD}─────────────────────────────────────────{Colors.RESET}\n"
            )
            return True

        elif subcmd == "register":
            if len(subparts) < 3:
                print(
                    f"  {Colors.RED}[Error] Usage: /tool register <name> <local_py_path> [local_json_path]{Colors.RESET}\n"
                )
                return True
            name = subparts[1].lower()
            local_py = subparts[2]
            local_json = subparts[3] if len(subparts) > 3 else None

            # Resolve local paths
            root = project_info["root"] if project_info else os.getcwd()
            full_py = (
                local_py if os.path.isabs(local_py) else os.path.join(root, local_py)
            )

            if not os.path.exists(full_py) or not os.path.isfile(full_py):
                print(
                    f"  {Colors.RED}[Error] Local Python file '{local_py}' not found.{Colors.RESET}\n"
                )
                return True

            # Ensure global tools folder exists
            os.makedirs(global_tools_dir, exist_ok=True)

            import shutil

            # Copy py
            dst_py = os.path.join(global_tools_dir, f"{name}.py")
            shutil.copy2(full_py, dst_py)
            print(f"  ✅ {Colors.GREEN}Copied Python script to {dst_py}{Colors.RESET}")

            # Copy or Scaffold JSON schema
            dst_json = os.path.join(global_tools_dir, f"{name}.json")
            if local_json:
                full_json = (
                    local_json
                    if os.path.isabs(local_json)
                    else os.path.join(root, local_json)
                )
                if os.path.exists(full_json) and os.path.isfile(full_json):
                    shutil.copy2(full_json, dst_json)
                    print(
                        f"  ✅ {Colors.GREEN}Copied JSON schema to {dst_json}{Colors.RESET}"
                    )
                else:
                    print(
                        f"  ⚠️  {Colors.YELLOW}Local schema '{local_json}' not found. Scaffolding default...{Colors.RESET}"
                    )
                    local_json = None

            if not local_json:
                # Scaffold a standard schema template
                default_schema = {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": f"Custom registered {name} tool.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "args_str": {
                                    "type": "string",
                                    "description": "Standard input argument string for the tool.",
                                }
                            },
                            "required": [],
                        },
                    },
                }
                with open(dst_json, "w", encoding="utf-8") as f:
                    json.dump(default_schema, f, indent=2)
                print(
                    f"  ✅ {Colors.GREEN}Scaffolded default JSON schema at {dst_json}{Colors.RESET}"
                )

            print(
                f"  🎉 {Colors.BOLD}{Colors.GREEN}Global tool '{name}' successfully registered and activated!{Colors.RESET}\n"
            )
            return True

        elif subcmd == "delete":
            if len(subparts) < 2:
                print(
                    f"  {Colors.RED}[Error] Usage: /tool delete <name>{Colors.RESET}\n"
                )
                return True
            name = subparts[1].lower()
            dst_py = os.path.join(global_tools_dir, f"{name}.py")
            dst_json = os.path.join(global_tools_dir, f"{name}.json")

            deleted_any = False
            if os.path.exists(dst_py):
                os.remove(dst_py)
                print(
                    f"  🗑️  {Colors.YELLOW}Deleted global script {dst_py}{Colors.RESET}"
                )
                deleted_any = True
            if os.path.exists(dst_json):
                os.remove(dst_json)
                print(
                    f"  🗑️  {Colors.YELLOW}Deleted global schema {dst_json}{Colors.RESET}"
                )
                deleted_any = True

            if deleted_any:
                print(
                    f"  ✅ {Colors.GREEN}Global tool '{name}' successfully removed.{Colors.RESET}\n"
                )
            else:
                print(
                    f"  ⚠️  {Colors.YELLOW}Global tool '{name}' was not found in directory.{Colors.RESET}\n"
                )
            return True

    elif cmd == "/jobs":
        # Display all jobs from the Job Queue (R8-AC1)
        from .jobs import JobQueue, CronParser
        from datetime import datetime, timezone

        queue = JobQueue()
        jobs = queue.load()

        if not jobs:
            print(f"  {Colors.YELLOW}No jobs in the queue.{Colors.RESET}\n")
            return True

        now = datetime.now(timezone.utc)

        print(f"\n  {Colors.BOLD}{'Name':<20} {'Type':<12} {'State':<10} {'Run#':<5} {'Exit':<5} {'Last Run':<22} {'Next Run':<24} {'PID':<8}{Colors.RESET}")
        print(f"  {'─' * 106}")
        for job in jobs:
            name = job.get("name", "?")
            jtype = job.get("type", "?")
            state = job.get("state", "?")
            run_count = job.get("run_count", 0)
            last_exit_code = job.get("last_exit_code")
            last_run = job.get("last_run_at")
            pid = job.get("pid")

            # Color-code state
            if state == "running":
                state_display = f"{Colors.GREEN}{state}{Colors.RESET}"
            elif state in ("failed", "crash_loop", "cancelled"):
                state_display = f"{Colors.RED}{state}{Colors.RESET}"
            elif state == "completed":
                state_display = f"{Colors.CYAN}{state}{Colors.RESET}"
            else:
                state_display = f"{Colors.YELLOW}{state}{Colors.RESET}"

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

            print(f"  {name:<20} {jtype:<12} {state_display:<20} {run_count:<5} {exit_display:<5} {last_run_display:<22} {next_run_display:<24} {pid_display:<8}")
        print()
        return True

    elif cmd == "/job":
        # Job management: /job stop|logs|restart|remove <name>
        from .jobs import JobQueue, format_error, CANCELLABLE_STATES, TERMINAL_STATES
        from .daemon import DaemonManager

        if len(parts) < 2:
            print(f"  {Colors.YELLOW}Usage: /job stop|logs|restart|remove <name>{Colors.RESET}\n")
            return True

        subparts = parts[1].split(None, 1)
        subcmd = subparts[0].lower() if subparts else ""
        job_name = subparts[1].strip() if len(subparts) > 1 else ""

        if not job_name:
            print(f"  {Colors.YELLOW}Usage: /job {subcmd} <name>{Colors.RESET}\n")
            return True

        queue = JobQueue()
        job = queue.get_job(job_name)

        if job is None:
            err_msg = format_error('not_found', f"Job '{job_name}' does not exist", "Use '/jobs' to see available jobs.")
            print(f"  {Colors.RED}{err_msg}{Colors.RESET}\n")
            return True

        if subcmd == "stop":
            # Cancel state validation (Requirement 31)
            current_state = job.get("state", "")
            if current_state in TERMINAL_STATES:
                err_msg = format_error('state', f"Job '{job_name}' is in '{current_state}' state and cannot be cancelled")
                print(f"  {Colors.RED}{err_msg}{Colors.RESET}\n")
                return True

            if current_state not in CANCELLABLE_STATES:
                err_msg = format_error('state', f"Job '{job_name}' is in '{current_state}' state and cannot be cancelled")
                print(f"  {Colors.RED}{err_msg}{Colors.RESET}\n")
                return True

            # R20: send SIGTERM to job subprocess if running
            pid = job.get("pid")
            if pid and current_state == "running":
                import signal as sig
                try:
                    os.kill(pid, sig.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    pass

            queue.update_status(job_name, "cancelled", pid=None)
            print(f"  {Colors.GREEN}Job '{job_name}' stopped (state → cancelled).{Colors.RESET}\n")
            return True

        elif subcmd == "remove":
            # R21: if running, terminate first; then remove from queue
            pid = job.get("pid")
            if pid and job.get("state") == "running":
                import signal as sig
                try:
                    os.kill(pid, sig.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    pass

            success = queue.remove_job(job_name)
            if success:
                print(f"  {Colors.GREEN}Job '{job_name}' removed from queue.{Colors.RESET}\n")
            else:
                err_msg = format_error("io", f"Failed to remove job '{job_name}'")
                print(f"  {Colors.RED}{err_msg}{Colors.RESET}\n")
            return True

        elif subcmd == "logs":
            # Display last 30 lines of job log
            log_output = queue.read_job_logs(job_name, lines=30)
            print(f"\n  {Colors.BOLD}--- Logs: {job_name} (last 30 lines) ---{Colors.RESET}")
            print(log_output)
            print(f"  {Colors.BOLD}{'─' * 40}{Colors.RESET}\n")
            return True

        elif subcmd == "restart":
            # R18: warn if daemon not running
            if job.get("type") != "persistent":
                print(f"  {Colors.YELLOW}Only persistent jobs can be restarted.{Colors.RESET}\n")
                return True
            if job.get("state") not in ("cancelled", "failed", "crash_loop", "completed"):
                print(f"  {Colors.YELLOW}Job '{job_name}' is in state '{job.get('state')}' and cannot be restarted.{Colors.RESET}\n")
                return True

            queue.update_status(
                job_name, "pending",
                restart_count=0,
                restart_timestamps=[],
            )
            print(f"  {Colors.GREEN}Job '{job_name}' restarted (state reset to pending).{Colors.RESET}")

            # Warn if daemon not running (Requirement 18)
            if not DaemonManager.is_running():
                print(f"  {Colors.YELLOW}⚠️  Warning: daemon is not running, job will remain pending until you run `kognisant daemon start`{Colors.RESET}")
            print()
            return True

        else:
            print(f"  {Colors.YELLOW}Unknown subcommand '{subcmd}'. Usage: /job stop|logs|restart|remove <name>{Colors.RESET}\n")
            return True

    elif cmd == "/channels":
        # List all channels with status
        from .channels import ChannelManager
        manager = ChannelManager()
        channels = manager.list_channels()

        if not channels:
            print(f"  {Colors.YELLOW}No channels configured.{Colors.RESET}")
            print(f"  Create one: kognisant channel add <name> --platform <platform>\n")
            return True

        print(f"\n  {Colors.BOLD}{'Name':<20} {'Platform':<12} {'Mode':<10} {'State':<10}{Colors.RESET}")
        print(f"  {'─' * 52}")
        for ch in channels:
            name = ch.get("name", "?")
            platform = ch.get("platform", "?")
            mode = ch.get("mode", "?")
            state = ch.get("state", "stopped")

            if state == "running":
                state_display = f"{Colors.GREEN}{state}{Colors.RESET}"
            elif state == "error":
                state_display = f"{Colors.RED}{state}{Colors.RESET}"
            elif state == "paused":
                state_display = f"{Colors.YELLOW}{state}{Colors.RESET}"
            else:
                state_display = state

            print(f"  {name:<20} {platform:<12} {mode:<10} {state_display}")
        print()
        return True

    elif cmd == "/channel":
        # Channel management: /channel add|remove|status|start|stop|pause|metrics|escalations
        from .channels import ChannelManager, VALID_PLATFORMS, VALID_MODES
        manager = ChannelManager()
        parts = user_input.split()  # /channel <subcmd> [args...]

        if len(parts) < 2:
            print(f"  {Colors.YELLOW}Usage: /channel add|remove|status|start|stop|pause|escalations <name>{Colors.RESET}\n")
            return True

        subcmd = parts[1].lower()
        ch_name = parts[2].strip() if len(parts) > 2 else None

        if subcmd == "add":
            # Interactive channel creation: /channel add <name> <platform> [mode]
            # Or just /channel add for guided prompts
            if len(parts) >= 4:
                name = parts[2]
                platform = parts[3]
                mode = parts[4] if len(parts) > 4 else "assistant"
            else:
                # Guided flow
                print(f"\n  {Colors.BOLD}Create a new channel{Colors.RESET}\n")
                try:
                    name = input(f"  Channel name: ").strip()
                    if not name:
                        print(f"  {Colors.YELLOW}Cancelled.{Colors.RESET}\n")
                        return True
                    print(f"  Platforms: {', '.join(sorted(VALID_PLATFORMS))}")
                    platform = input(f"  Platform: ").strip().lower()
                    print(f"  Modes: assistant (remote AI), manager (social bot), hybrid (both)")
                    mode = input(f"  Mode [assistant]: ").strip().lower() or "assistant"
                except (KeyboardInterrupt, EOFError):
                    print(f"\n  {Colors.YELLOW}Cancelled.{Colors.RESET}\n")
                    return True

            # Optional owner ID
            owner_ids = []
            if mode in ("assistant", "hybrid"):
                try:
                    owner_id = input(f"  Owner ID (e.g. tg:123456, leave blank to set later): ").strip()
                    if owner_id:
                        owner_ids = [owner_id]
                except (KeyboardInterrupt, EOFError):
                    pass

            try:
                channel = manager.add_channel(name=name, platform=platform, mode=mode, owner_ids=owner_ids)
                print(f"\n  {Colors.GREEN}✓{Colors.RESET} Channel '{name}' created ({platform}, {mode})")
                print(f"  Next: set credentials with `kognisant channel set-credentials {name}`")
                print(f"  Then: `/channel start {name}`\n")
            except ValueError as e:
                print(f"  {Colors.RED}Error:{Colors.RESET} {e}\n")
            return True

        elif subcmd == "remove" and ch_name:
            if manager.remove_channel(ch_name):
                print(f"  {Colors.GREEN}✓{Colors.RESET} Channel '{ch_name}' removed.\n")
            else:
                print(f"  {Colors.RED}Channel '{ch_name}' not found.{Colors.RESET}\n")
            return True

        elif subcmd == "status":
            if not ch_name:
                # Show all
                channels = manager.list_channels()
                for ch in channels:
                    state = ch.get("state", "stopped")
                    state_icon = "●" if state == "running" else "○"
                    color = Colors.GREEN if state == "running" else Colors.RESET
                    print(f"  {color}{state_icon}{Colors.RESET} {ch.get('name')} ({ch.get('platform')}, {ch.get('mode')}) — {state}")
                print()
            else:
                ch = manager.get_channel(ch_name)
                if not ch:
                    print(f"  {Colors.RED}Channel '{ch_name}' not found.{Colors.RESET}\n")
                else:
                    print(f"\n  {Colors.BOLD}Channel: {ch['name']}{Colors.RESET}")
                    print(f"  Platform:  {ch.get('platform')}")
                    print(f"  Mode:      {ch.get('mode')}")
                    print(f"  State:     {ch.get('state', 'stopped')}")
                    print(f"  Owners:    {ch.get('owner_ids', [])}")
                    print(f"  Created:   {ch.get('created_at', '?')}")
                    if ch.get("mode") in ("manager", "hybrid"):
                        mc = ch.get("manager_config", {})
                        cg = mc.get("cost_gate", {})
                        print(f"  LLM Budget: {cg.get('max_llm_calls_per_day', '?')}/day")
                    print()
            return True

        elif subcmd == "start" and ch_name:
            if manager.update_state(ch_name, "starting"):
                print(f"  {Colors.GREEN}✓{Colors.RESET} Channel '{ch_name}' queued for start.\n")
            else:
                print(f"  {Colors.RED}Channel '{ch_name}' not found.{Colors.RESET}\n")
            return True

        elif subcmd == "stop" and ch_name:
            if manager.update_state(ch_name, "stopped"):
                print(f"  {Colors.GREEN}✓{Colors.RESET} Channel '{ch_name}' marked for stop.\n")
            else:
                print(f"  {Colors.RED}Channel '{ch_name}' not found.{Colors.RESET}\n")
            return True

        elif subcmd == "pause" and ch_name:
            if manager.update_state(ch_name, "paused"):
                print(f"  {Colors.GREEN}✓{Colors.RESET} Channel '{ch_name}' paused.\n")
            else:
                print(f"  {Colors.RED}Channel '{ch_name}' not found.{Colors.RESET}\n")
            return True

        elif subcmd == "escalations":
            from .channels import ESCALATIONS_FILE
            if not os.path.exists(ESCALATIONS_FILE):
                print(f"  {Colors.GREEN}No pending escalations.{Colors.RESET}\n")
                return True
            try:
                with open(ESCALATIONS_FILE, "r") as f:
                    lines = f.readlines()
                pending = [json.loads(l) for l in lines if l.strip()]
                pending = [e for e in pending if e.get("status") == "pending"]
                if not pending:
                    print(f"  {Colors.GREEN}No pending escalations.{Colors.RESET}\n")
                else:
                    print(f"\n  {Colors.BOLD}Pending Escalations ({len(pending)}):{Colors.RESET}\n")
                    for i, esc in enumerate(pending[-10:], 1):
                        ev = esc.get("event", {})
                        print(f"  [{i}] {esc.get('channel', '?')} | @{ev.get('sender_name', '?')}: {ev.get('content', '')[:60]}")
                        print(f"      {esc.get('ts', '')}")
                    print()
            except (OSError, json.JSONDecodeError):
                print(f"  {Colors.RED}Failed to read escalations.{Colors.RESET}\n")
            return True

        elif subcmd == "metrics" and ch_name:
            # Phase 2a — placeholder
            print(f"  {Colors.YELLOW}Metrics for '{ch_name}' — available in Phase 2a.{Colors.RESET}\n")
            return True

        else:
            print(f"  {Colors.YELLOW}Usage: /channel add|remove|status|start|stop|pause|escalations|metrics <name>{Colors.RESET}\n")
            return True

    elif cmd == "/daemon":
        # Daemon control: /daemon status|start|stop|restart
        from .daemon import DaemonManager
        from .jobs import format_error

        if len(parts) < 2:
            print(f"  {Colors.YELLOW}Usage: /daemon status|start|stop|restart{Colors.RESET}\n")
            return True

        subcmd = parts[1].strip().lower()

        if subcmd == "status":
            # Display daemon status, PID, uptime
            status_info = DaemonManager.status()
            if status_info["running"]:
                print(f"\n  {Colors.BOLD}Daemon Status:{Colors.RESET}")
                print(f"    Running:  {Colors.GREEN}Yes{Colors.RESET}")
                print(f"    PID:      {status_info['pid']}")
                print(f"    Uptime:   {status_info['uptime'] or 'unknown'}")
            else:
                print(f"\n  {Colors.BOLD}Daemon Status:{Colors.RESET}")
                print(f"    Running:  {Colors.RED}No{Colors.RESET}")
            print()
            return True

        elif subcmd == "start":
            # Start daemon and display confirmation
            try:
                pid = DaemonManager.start()
                print(f"  {Colors.GREEN}Daemon started with PID {pid}.{Colors.RESET}\n")
            except RuntimeError as e:
                print(f"  {Colors.RED}Error: {e}{Colors.RESET}\n")
            return True

        elif subcmd == "stop":
            # R19: send SIGTERM to daemon, display confirmation
            if not DaemonManager.is_running():
                err_msg = format_error("state", "No daemon is currently running")
                print(f"  {Colors.RED}{err_msg}{Colors.RESET}\n")
                return True

            success = DaemonManager.stop()
            if success:
                print(f"  {Colors.GREEN}Daemon stopped.{Colors.RESET}\n")
            else:
                err_msg = format_error("io", "Failed to stop daemon")
                print(f"  {Colors.RED}{err_msg}{Colors.RESET}\n")
            return True

        elif subcmd == "restart":
            # R22: stop + start cycle
            was_running = DaemonManager.is_running()
            try:
                new_pid = DaemonManager.restart()
                if was_running:
                    print(f"  {Colors.GREEN}Daemon restarted with new PID {new_pid}.{Colors.RESET}\n")
                else:
                    print(f"  {Colors.GREEN}Daemon was not previously running. Started fresh with PID {new_pid}.{Colors.RESET}\n")
            except RuntimeError as e:
                print(f"  {Colors.RED}Error: {e}{Colors.RESET}\n")
            return True

        else:
            print(f"  {Colors.YELLOW}Unknown subcommand '{subcmd}'. Usage: /daemon status|start|stop|restart{Colors.RESET}\n")
            return True

    elif cmd == "/worldmodel":
        if project_info:
            parts = cleaned_input.split()[1:]
            subcommand = parts[0].lower() if parts else "status"
            if subcommand == "enable":
                try:
                    config_path = os.path.join(project_info["root"], ".kognisant", "config.json")
                    with open(config_path, "r", encoding="utf-8") as f:
                        config_data = json.load(f)
                    config_data["world_model_enabled"] = True
                    with open(config_path, "w", encoding="utf-8") as f:
                        json.dump(config_data, f, indent=2)
                    from .config import init_world_model
                    init_world_model(project_info["root"])
                    print(f"{Colors.GREEN}World model enabled and initialized.{Colors.RESET}")
                except Exception as ex:
                    print(f"{Colors.RED}[!] Error enabling world model: {ex}{Colors.RESET}")
            elif subcommand == "disable":
                try:
                    config_path = os.path.join(project_info["root"], ".kognisant", "config.json")
                    with open(config_path, "r", encoding="utf-8") as f:
                        config_data = json.load(f)
                    config_data["world_model_enabled"] = False
                    with open(config_path, "w", encoding="utf-8") as f:
                        json.dump(config_data, f, indent=2)
                    print(f"{Colors.GREEN}World model disabled.{Colors.RESET}")
                except Exception as ex:
                    print(f"{Colors.RED}[!] Error disabling world model: {ex}{Colors.RESET}")
            elif subcommand == "status":
                enabled = is_world_model_enabled(project_info["root"]) if project_info else False
                status_str = f"{Colors.GREEN}enabled{Colors.RESET}" if enabled else f"{Colors.YELLOW}disabled{Colors.RESET}"
                print(f"  World Model: {status_str}")
                if enabled:
                    wm_dir = os.path.join(project_info["root"], ".kognisant", "world_model")
                    if os.path.isdir(wm_dir):
                        print(f"  Storage: {wm_dir}")
                    else:
                        print(f"  Storage: {Colors.YELLOW}not initialized{Colors.RESET} (run /worldmodel enable)")
            else:
                print(f"  Usage: /worldmodel [enable|disable|status]")
        else:
            print("No project detected. Run 'kognisant init' first.")
        return True

    elif cmd == "/goals":
        if project_info and is_world_model_enabled(project_info["root"]):
            try:
                from .goal_engine import ProposalInterface

                proposal = ProposalInterface(project_root=project_info["root"])
                parts = cleaned_input.split()[1:]  # get subcommand parts
                output = proposal.handle_command(parts)
                print(output)
            except Exception as ex:
                print(f"{Colors.RED}[!] Error processing goals: {ex}{Colors.RESET}\n")
        else:
            print("World model not enabled. Enable it in .kognisant/config.json")
        return True

    elif cmd == "/telemetry":
        from .telemetry import format_telemetry_summary, format_model_telemetry, load_recent_telemetry
        records = load_recent_telemetry(50)
        if len(parts) > 1:
            print(format_model_telemetry(records, parts[1]))
        else:
            print(format_telemetry_summary(records))
        return True

    elif cmd == "/thinking":
        _handle_thinking_command(parts, project_info, session_file if 'session_file' in dir() else None)
        return True

    return False


def _handle_thinking_command(parts: list, project_info: dict | None, session_file: str | None):
    """Handle /thinking, /thinking N, /thinking list commands."""
    import json as _json
    import os as _os
    import glob as _glob

    # Find the most recent thinking file
    if project_info:
        history_dir = _os.path.join(project_info.get("root", ""), ".kognisant", "history")
    else:
        history_dir = _os.path.expanduser("~/.kognisant_core/history")

    # Find thinking files
    thinking_files = sorted(_glob.glob(_os.path.join(history_dir, "*_thinking.json")))
    if not thinking_files:
        print(f"  {Colors.YELLOW}No reasoning data for this session.{Colors.RESET}")
        return

    # Use the most recent one
    thinking_path = thinking_files[-1]
    try:
        with open(thinking_path, "r", encoding="utf-8") as f:
            entries = _json.load(f)
    except (IOError, _json.JSONDecodeError):
        print(f"  {Colors.YELLOW}Could not read reasoning data.{Colors.RESET}")
        return

    if not entries:
        print(f"  {Colors.YELLOW}No reasoning entries recorded yet.{Colors.RESET}")
        return

    sub_cmd = parts[1] if len(parts) > 1 else None

    if sub_cmd == "list":
        # Show summary: turn numbers + first 50 chars
        print(f"\n  {Colors.BOLD}Reasoning history:{Colors.RESET}")
        for entry in entries:
            turn = entry.get("turn", "?")
            reasoning = entry.get("reasoning", [])
            preview = reasoning[0][:50] if reasoning else "(empty)"
            duration = entry.get("thinking_duration_ms", 0) / 1000
            print(f"    Turn {turn} ({duration:.1f}s): \"{preview}...\"")
        print()
        return

    if sub_cmd and sub_cmd.isdigit():
        # Show specific turn
        turn_num = int(sub_cmd)
        entry = next((e for e in entries if e.get("turn") == turn_num), None)
        if not entry:
            print(f"  {Colors.YELLOW}No reasoning recorded for turn {turn_num}.{Colors.RESET}")
            return
    else:
        # Show last turn
        entry = entries[-1]

    # Display the entry
    turn = entry.get("turn", "?")
    model = entry.get("model", "unknown")
    duration_ms = entry.get("thinking_duration_ms", 0)
    reasoning = entry.get("reasoning", [])

    print(f"\n  {Colors.BOLD}Turn {turn}{Colors.RESET} - Thought for {duration_ms/1000:.1f}s ({model})")
    if len(reasoning) > 1:
        for i, step in enumerate(reasoning, 1):
            print(f"    {i}. {step}")
    elif reasoning:
        print(f"    {reasoning[0]}")
    else:
        print(f"    (no reasoning steps recorded)")
    print()


def run_mock_chat(project_info=None):
    print(f"\n{Colors.BOLD}--- Offline Mode (No AI Model Connected) ---{Colors.RESET}")
    if project_info:
        print(
            f"📁 {Colors.CYAN}Project Mode Active:{Colors.RESET} {project_info['name']}"
        )
    print(
        f"  {Colors.YELLOW}ℹ️  Responses are simulated. Slash commands still work.{Colors.RESET}"
    )
    print(
        f"  To connect a model: type {Colors.CYAN}/model{Colors.RESET} or run {Colors.CYAN}kognisant setup{Colors.RESET}\n"
    )
    print(f"  Quick commands: {Colors.CYAN}/help{Colors.RESET}  {Colors.CYAN}/files{Colors.RESET}  {Colors.CYAN}/context{Colors.RESET}  {Colors.CYAN}/model{Colors.RESET}")
    print("  Press Ctrl+D or Ctrl+C to exit.\n")

    history = []
    session_file = (
        f"session_{time.strftime('%Y%m%d_%H%M%S')}.json" if project_info else None
    )

    while True:
        try:
            user_input = prompt_boxed_input()
        except (EOFError, KeyboardInterrupt):
            print(f"\n{Colors.YELLOW}Goodbye!{Colors.RESET}")
            break

        cleaned_input = user_input.strip()
        if not cleaned_input:
            continue

        if cleaned_input.lower() in ["exit", "quit"]:
            print(f"{Colors.CYAN}Kognisant >{Colors.RESET} Goodbye!")
            break

        if cleaned_input.startswith("/"):
            if process_slash_commands(
                cleaned_input, project_info, history, is_mock=True
            ):
                save_chat_session(project_info, history, session_file)
                continue

        history.append(cleaned_input)
        save_chat_session(project_info, history, session_file)

        spinner = Spinner()
        spinner.start()
        time.sleep(0.6)
        spinner.stop()

        turn_count = len(history)
        if turn_count == 1:
            response = (
                f"I'm in offline mode right now, so I can't actually process your request.\n\n"
                f"To get AI-powered responses, connect a model:\n"
                f"  • Type `/model` to configure one now\n"
                f"  • Or run `kognisant setup` from your terminal\n\n"
                f"In the meantime, slash commands like `/files`, `/context`, and `/spec list` still work!"
            )
        else:
            response = (
                f"Still in offline mode. Your message was logged (turn {turn_count}).\n\n"
                f"Type `/model` to connect an AI model, or `/help` for available commands."
            )

        print(f"{Colors.CYAN}Kognisant >{Colors.RESET}\n{render_markdown(response)}\n")


# ───────────────────────────────────────────────────────────
def run_api_chat(model_config, project_info=None):
    """Active multi-turn LLM chat loop powered by standard compatible APIs with tool execution and self-healing fallback."""
    from . import json_stream

    model_name = model_config["name"]
    provider_name = model_config["provider"]
    display_name = model_config.get("display_name", model_name)
    is_json = json_stream.is_active()

    if is_json:
        # Emit session start and wait for handshake
        from . import __version__
        json_stream.emit_session_start(
            cli_version=__version__,
            project=project_info["root"] if project_info else None,
            model=model_name,
            provider=provider_name,
            valence=0,
        )
        json_stream.wait_for_hello(timeout=5.0)
    else:
        print(
            f"\n{Colors.BOLD}--- Starting Chat with '{display_name}' ({provider_name}) ---{Colors.RESET}"
        )
    if project_info:
        print(
            f"📁 {Colors.CYAN}Project Mode Active:{Colors.RESET} {project_info['name']}"
        )
        print(
            f"  🧠 {Colors.GREEN}Membrain Active:{Colors.RESET} Loaded '.kognisant/context.md' and '.kognisant/memory-guidlines.md' steering rules into conversation context."
        )
    print(f"Type {Colors.CYAN}/help{Colors.RESET} to see available commands.")
    print(
        f"  💡 Quick: {Colors.CYAN}/files{Colors.RESET}  {Colors.CYAN}/read <path>{Colors.RESET}  "
        f"{Colors.CYAN}/agent <task>{Colors.RESET}  {Colors.CYAN}/spec list{Colors.RESET}  {Colors.CYAN}/model{Colors.RESET}"
    )
    print("Press Ctrl+D (Cmd+D) or Ctrl+C to exit.\n")

    messages = []
    session_file = (
        f"session_{time.strftime('%Y%m%d_%H%M%S')}.json" if project_info else None
    )

    if project_info:
        messages.append(build_system_prompt(project_info))
        save_chat_session(project_info, messages, session_file)

    # Display session-start goals if world model is enabled
    if project_info and is_world_model_enabled(project_info["root"]):
        try:
            from .goal_engine import ProposalInterface

            proposal = ProposalInterface(project_root=project_info["root"])
            goals_display = proposal.display_session_start_goals()
            if goals_display:
                print(goals_display)
                print()
        except Exception:
            pass  # Never break chat startup

    while True:
        try:
            if is_json:
                # JSON stream mode: read commands from stdin reader
                cmd = json_stream.poll_command()
                if cmd is None:
                    # No command yet — wait briefly (non-busy)
                    import time as _time
                    _time.sleep(0.05)
                    continue
                cmd_type = cmd.get("type", "")
                if cmd_type == "message":
                    user_input = cmd.get("content", "")
                elif cmd_type == "command":
                    user_input = cmd.get("slash", "") + " " + cmd.get("args", "")
                elif cmd_type == "exit" or cmd_type == "_eof":
                    json_stream.emit_session_end("user_exit")
                    break
                elif cmd_type == "ping":
                    json_stream.emit_pong()
                    continue
                else:
                    continue
            else:
                user_input = prompt_boxed_input()
        except (EOFError, KeyboardInterrupt):
            if is_json:
                json_stream.emit_session_end("frontend_disconnect")
            else:
                print(f"\n{Colors.YELLOW}Goodbye! Thanks for chatting.{Colors.RESET}")
            break

        cleaned_input = user_input.strip()
        if not cleaned_input:
            continue

        if cleaned_input.lower() in ["exit", "quit"]:
            if is_json:
                json_stream.emit_session_end("user_exit")
            else:
                print(f"{Colors.CYAN}Kognisant >{Colors.RESET} Goodbye!")
            break

        if cleaned_input.startswith("/"):
            if process_slash_commands(
                cleaned_input,
                project_info,
                messages,
                active_model_config=model_config,
                is_mock=False,
            ):
                save_chat_session(project_info, messages, session_file)
                continue

        # Emit user message event
        if is_json:
            json_stream.emit_user_message(cleaned_input)

        # Delegate to runtime orchestrator (5-phase lifecycle)
        result = execute_message(
            user_message=cleaned_input,
            messages=messages,
            model_config=model_config,
            project_info=project_info,
            session_file=session_file,
        )

        if result.success and not result.streamed:
            print(f"{Colors.CYAN}Kognisant >{Colors.RESET}\n{render_markdown(result.response)}\n")
        elif result.error:
            print(f"\n{result.error}\n")
        # cancelled/streamed cases already handled inside runtime


def select_model(
    models, label="Select an AI Model to power this session", active_model_config=None
):
    print(f"\n  📦 {Colors.BOLD}{label}:{Colors.RESET}\n")
    for idx, model in enumerate(models, 1):
        provider_name = model.get("provider", "Unknown")
        display_name = model.get("display_name", model["name"])
        api_key = model.get("api_key", "")
        is_active = (
            f" {Colors.GREEN}[Active]{Colors.RESET}"
            if active_model_config
            and model["name"] == active_model_config["name"]
            and model["provider"] == active_model_config["provider"]
            else ""
        )

        # Health status indicator
        is_local = "ollama" in provider_name.lower() or "llama" in provider_name.lower()
        if is_local:
            health = f"{Colors.GREEN}🟢{Colors.RESET}"
        elif api_key and "your-" not in api_key:
            health = f"{Colors.GREEN}🟢{Colors.RESET}"
        else:
            health = f"{Colors.YELLOW}🟡{Colors.RESET}"

        print(
            f"    [{Colors.CYAN}{idx}{Colors.RESET}] {display_name} ({Colors.MAGENTA}{provider_name}{Colors.RESET}) {health}{is_active}"
        )
    print(f"    [{Colors.GREEN}a{Colors.RESET}] Add custom provider / model")
    print(f"    [{Colors.RED}r{Colors.RESET}] Remove a model from pool")
    print("    [Enter] Cancel and resume chat\n")

    while True:
        try:
            choice = input(
                f"  👉 {Colors.BOLD}Enter selection (number, 'a', or 'm'): {Colors.RESET}"
            ).strip()
        except (EOFError, KeyboardInterrupt):
            return None

        if not choice:
            return None

        if choice.lower() == "m":
            return "mock"

        if choice.lower() == "r":
            # Remove a model from the pool
            print(f"\n  🗑️  {Colors.BOLD}Remove a model:{Colors.RESET}\n")
            for idx, model in enumerate(models, 1):
                display_name = model.get("display_name", model["name"])
                provider_name = model.get("provider", "Unknown")
                is_active = (
                    f" {Colors.RED}[Active - cannot remove]{Colors.RESET}"
                    if active_model_config
                    and model["name"] == active_model_config["name"]
                    and model["provider"] == active_model_config["provider"]
                    else ""
                )
                print(f"    [{Colors.CYAN}{idx}{Colors.RESET}] {display_name} ({provider_name}){is_active}")
            print(f"    [Enter] Cancel\n")

            try:
                rm_choice = input(f"  👉 {Colors.BOLD}Enter number to remove: {Colors.RESET}").strip()
            except (EOFError, KeyboardInterrupt):
                return None

            if not rm_choice:
                continue

            try:
                rm_idx = int(rm_choice) - 1
                if 0 <= rm_idx < len(models):
                    target = models[rm_idx]
                    # Prevent removing the active model
                    if (
                        active_model_config
                        and target["name"] == active_model_config["name"]
                        and target["provider"] == active_model_config["provider"]
                    ):
                        print(f"  {Colors.RED}Cannot remove the active model. Switch first with /model.{Colors.RESET}\n")
                        continue

                    display_name = target.get("display_name", target["name"])
                    provider_name = target.get("provider", "Unknown")

                    # Confirm
                    try:
                        confirm = input(f"  Remove '{display_name}' ({provider_name})? [y/N]: ").strip().lower()
                    except (EOFError, KeyboardInterrupt):
                        return None

                    if confirm == "y":
                        models.pop(rm_idx)
                        # Save updated pool
                        from .config import save_providers_and_pool
                        providers_data = load_providers_and_pool()
                        providers_data["models"] = models
                        save_providers_and_pool(providers_data)
                        print(f"  ✅ {Colors.GREEN}'{display_name}' removed from model pool.{Colors.RESET}\n")
                    else:
                        print(f"  Cancelled.\n")
                else:
                    print(f"  {Colors.RED}Invalid selection.{Colors.RESET}\n")
            except ValueError:
                print(f"  {Colors.RED}Invalid input.{Colors.RESET}\n")
            continue

        if choice.lower() == "a":
            # Template-based provider addition
            print(
                f"\n  ➕ {Colors.BOLD}Add a Provider:{Colors.RESET}\n"
            )
            print(f"    [{Colors.CYAN}1{Colors.RESET}] Ollama (local, auto-detect)")
            print(f"    [{Colors.CYAN}2{Colors.RESET}] OpenAI")
            print(f"    [{Colors.CYAN}3{Colors.RESET}] Groq")
            print(f"    [{Colors.CYAN}4{Colors.RESET}] DeepSeek")
            print(f"    [{Colors.CYAN}5{Colors.RESET}] OpenRouter")
            print(f"    [{Colors.CYAN}6{Colors.RESET}] Custom endpoint\n")

            try:
                provider_choice = input(f"     Select [1-6]: ").strip()
            except (EOFError, KeyboardInterrupt):
                return None

            # Provider templates: (name, base_url, protocol, needs_key, default_model)
            provider_templates = {
                "1": ("Ollama (Local)", "http://localhost:11434", "ollama", False, None),
                "2": ("OpenAI", "https://api.openai.com/v1", "openai", True, "gpt-4o-mini"),
                "3": ("Groq", "https://api.groq.com/openai/v1", "openai", True, "llama-3.3-70b-versatile"),
                "4": ("DeepSeek", "https://api.deepseek.com/v1", "openai", True, "deepseek-chat"),
                "5": ("OpenRouter", "https://openrouter.ai/api/v1", "openai", True, "meta-llama/llama-3.3-70b-instruct"),
                "6": None,
            }

            if provider_choice not in provider_templates:
                print(f"     {Colors.RED}Invalid choice.{Colors.RESET}\n")
                continue

            template = provider_templates[provider_choice]

            if provider_choice == "1":
                # Ollama auto-detect
                from .network import OLLAMA_HOST, get_ollama_models

                local_tags = get_ollama_models()
                if local_tags:
                    print(f"\n     {Colors.GREEN}Detected {len(local_tags)} model(s):{Colors.RESET}")
                    for i, tag in enumerate(local_tags[:10], 1):
                        print(f"       [{Colors.CYAN}{i}{Colors.RESET}] {tag}")
                    try:
                        sel = input(f"\n     Select model: ").strip()
                        s_idx = int(sel) - 1
                        tag = local_tags[s_idx] if 0 <= s_idx < len(local_tags) else local_tags[0]
                    except (ValueError, EOFError, KeyboardInterrupt):
                        tag = local_tags[0]

                    new_model = {
                        "name": tag,
                        "display_name": tag,
                        "provider": "Ollama (Local)",
                        "protocol": "ollama",
                        "api_base_url": OLLAMA_HOST,
                        "api_key": "",
                        "capabilities": {"tool_calling": True, "reasoning": True},
                    }
                else:
                    print(f"     {Colors.RED}Ollama not reachable at localhost:11434.{Colors.RESET}\n")
                    continue

            elif provider_choice == "6":
                # Custom endpoint
                try:
                    provider_name = input("     Provider name: ").strip()
                    api_base_url = input("     Base URL: ").strip()
                    model_id = input("     Model ID: ").strip()
                    api_key = input("     API Key (Enter if none): ").strip()
                except (EOFError, KeyboardInterrupt):
                    return None

                if not api_base_url or not model_id:
                    print(f"     {Colors.RED}URL and Model ID required.{Colors.RESET}\n")
                    continue

                new_model = {
                    "name": model_id,
                    "display_name": model_id,
                    "provider": provider_name or "Custom",
                    "protocol": "openai",
                    "api_base_url": api_base_url,
                    "api_key": api_key,
                    "capabilities": {"tool_calling": True, "reasoning": True},
                }
            else:
                # Template-based cloud provider (just need API key)
                prov_name, base_url, protocol, _, default_model = template
                try:
                    api_key = input(f"     🔑 {prov_name} API Key: ").strip()
                except (EOFError, KeyboardInterrupt):
                    return None

                if not api_key:
                    print(f"     {Colors.RED}No key provided.{Colors.RESET}\n")
                    continue

                new_model = {
                    "name": default_model,
                    "display_name": default_model,
                    "provider": prov_name,
                    "protocol": protocol,
                    "api_base_url": base_url,
                    "api_key": api_key,
                    "capabilities": {"tool_calling": True, "reasoning": True},
                }

            # Save the model
            _save_setup_model(new_model, new_model["provider"], new_model.get("api_key", ""))
            print(
                f"\n  ✅ {Colors.GREEN}'{new_model['display_name']}' ({new_model['provider']}) added and saved!{Colors.RESET}\n"
            )
            return new_model

        try:
            index = int(choice) - 1
            if 0 <= index < len(models):
                selected = models[index]
                provider_name = selected.get("provider", "")
                api_key = selected.get("api_key", "")

                if requires_api_key(provider_name, api_key):
                    print(
                        f"\n  🔑 {Colors.YELLOW}The provider '{provider_name}' requires an API Key.{Colors.RESET}"
                    )
                    try:
                        new_key = input(
                            f"     Please enter your {provider_name} API Key: "
                        ).strip()
                    except (EOFError, KeyboardInterrupt):
                        return None

                    if not new_key:
                        print(
                            f"     {Colors.RED}No key entered. Selection aborted.{Colors.RESET}\n"
                        )
                        continue

                    # Save credentials within the hierarchical group inside models_pool.json
                    providers, pool = load_providers_and_pool()
                    if isinstance(pool, dict):
                        for group in pool.get("selected_models", []):
                            if group.get("provider") == provider_name:
                                group["api_key"] = new_key

                    save_providers_and_pool(providers, pool)
                    selected["api_key"] = new_key
                    print(
                        f"  ✅ {Colors.GREEN}API key saved successfully!{Colors.RESET}\n"
                    )

                return selected
        except ValueError:
            pass
        print(
            f"     {Colors.RED}Invalid selection. Please enter a valid number, 'a', or 'm'.{Colors.RESET}\n"
        )


def _has_any_valid_provider():
    """Check if at least one provider has a usable key or is a local endpoint."""
    compiled_models = get_compiled_models()
    for model in compiled_models:
        provider = model.get("provider", "")
        api_key = model.get("api_key", "")
        # Local models don't need keys
        if "ollama" in provider.lower() or "llama" in provider.lower():
            return True
        # Cloud models need a real key
        if api_key and "your-" not in api_key:
            return True
    return False


def _run_first_time_setup():
    """Interactive first-run setup wizard for configuring a provider."""
    print(f"  ╭{'─' * 48}╮")
    print(f"  │  👋 {Colors.BOLD}Welcome to Kognisant!{Colors.RESET}                       │")
    print(f"  │  Let's get you connected to an AI model.      │")
    print(f"  ╰{'─' * 48}╯\n")

    print(f"  How would you like to connect?\n")
    print(f"    [{Colors.CYAN}1{Colors.RESET}] 🏠 Local Model (Ollama)        — Free, private, runs on your machine")
    print(f"    [{Colors.CYAN}2{Colors.RESET}] 🏠 Local Model (Llama.cpp)     — Free, point to a running server")
    print(f"    [{Colors.CYAN}3{Colors.RESET}] ☁️  Cloud API (OpenAI)          — Requires API key")
    print(f"    [{Colors.CYAN}4{Colors.RESET}] ☁️  Cloud API (Groq)            — Requires API key, fast inference")
    print(f"    [{Colors.CYAN}5{Colors.RESET}] ☁️  Cloud API (DeepSeek)        — Requires API key, affordable")
    print(f"    [{Colors.CYAN}6{Colors.RESET}] ☁️  Cloud API (Custom endpoint) — Any OpenAI-compatible server")
    print(f"    [{Colors.CYAN}7{Colors.RESET}] 🔌 Skip — I'll configure later (offline mock mode)\n")

    try:
        choice = input(f"  👉 {Colors.BOLD}Select [1-7]:{Colors.RESET} ").strip()
    except (EOFError, KeyboardInterrupt):
        return None

    if choice == "7" or not choice:
        return "mock"

    # Provider templates: (provider_name, api_base_url, protocol, needs_key, default_model_name, default_model_id)
    templates = {
        "1": ("Ollama (Local)", "http://localhost:11434", "ollama", False, None, None),
        "2": ("Llama.cpp (Local)", "http://localhost:8080", "llama_cpp", False, "local-model", "local-model"),
        "3": ("OpenAI", "https://api.openai.com/v1", "openai", True, "gpt-4o-mini", "gpt-4o-mini"),
        "4": ("Groq", "https://api.groq.com/openai/v1", "openai", True, "llama-3.3-70b-versatile", "llama-3.3-70b-versatile"),
        "5": ("DeepSeek", "https://api.deepseek.com/v1", "openai", True, "deepseek-chat", "deepseek-chat"),
        "6": None,  # Custom
    }

    if choice not in templates:
        print(f"  {Colors.YELLOW}Invalid selection. Starting in offline mode.{Colors.RESET}\n")
        return "mock"

    template = templates[choice]

    if choice == "6":
        # Custom endpoint flow
        try:
            print(f"\n  ➕ {Colors.BOLD}Custom OpenAI-Compatible Endpoint{Colors.RESET}\n")
            api_base_url = input("     Base URL (e.g. https://api.example.com/v1): ").strip()
            if not api_base_url:
                print(f"  {Colors.RED}URL required.{Colors.RESET}\n")
                return "mock"
            model_id = input("     Model ID (e.g. my-model-name): ").strip()
            if not model_id:
                print(f"  {Colors.RED}Model ID required.{Colors.RESET}\n")
                return "mock"
            api_key = input("     API Key (Enter if none): ").strip()
            provider_name = input("     Provider name (e.g. MyServer) [Custom]: ").strip() or "Custom"
        except (EOFError, KeyboardInterrupt):
            return None

        new_model = {
            "name": model_id,
            "display_name": model_id,
            "provider": provider_name,
            "protocol": "openai",
            "api_base_url": api_base_url,
            "api_key": api_key,
            "capabilities": {"tool_calling": True, "reasoning": True},
        }
        _save_setup_model(new_model, provider_name, api_key)
        return new_model

    provider_name, api_base_url, protocol, needs_key, default_name, default_id = template

    # Ollama: auto-detect models
    if choice == "1":
        from .network import get_ollama_models

        print(f"\n  ⏳ Detecting Ollama models at localhost:11434...")
        models = get_ollama_models()
        if models:
            print(f"  {Colors.GREEN}✅ Ollama detected! {len(models)} model(s) available:{Colors.RESET}\n")
            for i, m in enumerate(models[:10], 1):
                print(f"    [{Colors.CYAN}{i}{Colors.RESET}] {m}")
            print()
            try:
                sel = input(f"  👉 Select model [1-{min(len(models), 10)}]: ").strip()
                idx = int(sel) - 1
                if 0 <= idx < len(models):
                    selected_model_name = models[idx]
                else:
                    selected_model_name = models[0]
            except (ValueError, EOFError, KeyboardInterrupt):
                selected_model_name = models[0]

            new_model = {
                "name": selected_model_name,
                "display_name": selected_model_name,
                "provider": "Ollama (Local)",
                "protocol": "ollama",
                "api_base_url": "http://localhost:11434",
                "api_key": "",
                "capabilities": {"tool_calling": True, "reasoning": True},
            }
            _save_setup_model(new_model, "Ollama (Local)", "")
            print(f"  ✅ {Colors.GREEN}Saved! Ollama ({selected_model_name}) is now your active model.{Colors.RESET}\n")
            return new_model
        else:
            print(f"  {Colors.RED}❌ Could not reach Ollama at localhost:11434.{Colors.RESET}")
            print(f"     Make sure Ollama is running: {Colors.CYAN}ollama serve{Colors.RESET}")
            print(f"     Then restart: {Colors.CYAN}kognisant chat{Colors.RESET}\n")
            return "mock"

    # Llama.cpp: ask for URL
    if choice == "2":
        try:
            print(f"\n  🏠 {Colors.BOLD}Llama.cpp Server{Colors.RESET}\n")
            url = input(f"     Server URL [{api_base_url}]: ").strip() or api_base_url
            model_name = input(f"     Model name [local-model]: ").strip() or "local-model"
        except (EOFError, KeyboardInterrupt):
            return None

        new_model = {
            "name": model_name,
            "display_name": model_name,
            "provider": "Llama.cpp (Local)",
            "protocol": "llama_cpp",
            "api_base_url": url,
            "api_key": "",
            "capabilities": {"tool_calling": False, "reasoning": True},
        }
        _save_setup_model(new_model, "Llama.cpp (Local)", "")
        print(f"  ✅ {Colors.GREEN}Saved! Llama.cpp ({model_name}) is now your active model.{Colors.RESET}\n")
        return new_model

    # Cloud providers: ask for API key
    try:
        print(f"\n  ☁️  {Colors.BOLD}{provider_name} Setup{Colors.RESET}\n")
        api_key = input(f"     🔑 Enter your {provider_name} API key: ").strip()
    except (EOFError, KeyboardInterrupt):
        return None

    if not api_key:
        print(f"  {Colors.YELLOW}No key provided. Starting in offline mode.{Colors.RESET}\n")
        return "mock"

    # Validate connection
    print(f"  ⏳ Testing connection to {provider_name}...")
    import ssl
    import urllib.error
    import urllib.request

    try:
        test_url = api_base_url.rstrip("/") + "/models"
        req = urllib.request.Request(test_url)
        req.add_header("Authorization", f"Bearer {api_key}")
        context = ssl._create_unverified_context()
        with urllib.request.urlopen(req, timeout=5.0, context=context) as resp:
            if resp.status == 200:
                print(f"  {Colors.GREEN}✅ Connected successfully!{Colors.RESET}\n")
            else:
                print(f"  {Colors.YELLOW}⚠️  Got HTTP {resp.status}. Saving anyway.{Colors.RESET}\n")
    except urllib.error.HTTPError as e:
        if e.code == 401:
            print(f"  {Colors.RED}❌ Invalid API key (HTTP 401). Please check your key.{Colors.RESET}")
            retry = input(f"     Save anyway? [y/n]: ").strip().lower()
            if retry != "y":
                return "mock"
        else:
            print(f"  {Colors.YELLOW}⚠️  HTTP {e.code}. Saving config anyway.{Colors.RESET}\n")
    except Exception as e:
        print(f"  {Colors.YELLOW}⚠️  Connection test failed: {e}. Saving config anyway.{Colors.RESET}\n")

    new_model = {
        "name": default_id,
        "display_name": default_name,
        "provider": provider_name,
        "protocol": protocol,
        "api_base_url": api_base_url,
        "api_key": api_key,
        "capabilities": {"tool_calling": True, "reasoning": True},
    }
    _save_setup_model(new_model, provider_name, api_key)
    print(f"  ✅ {Colors.GREEN}Saved! {provider_name} ({default_name}) is now your active model.{Colors.RESET}\n")
    return new_model


def _save_setup_model(model_config, provider_name, api_key):
    """Persist a model from setup wizard into the global models_pool.json."""
    providers, pool = load_providers_and_pool()
    if not isinstance(pool, dict):
        pool = {"selected_models": []}

    selected_models = pool.get("selected_models", [])
    if not isinstance(selected_models, list):
        selected_models = []

    # Find existing provider group or create new one
    provider_group = None
    for group in selected_models:
        if group.get("provider") == provider_name:
            provider_group = group
            break

    new_model_entry = {
        "vendor": provider_name,
        "name": model_config.get("display_name", model_config["name"]),
        "model_id": model_config["name"],
        "protocol": model_config.get("protocol", "openai"),
        "api_base_url": model_config["api_base_url"],
        "capabilities": model_config.get("capabilities", {"tool_calling": True, "reasoning": True}),
    }

    if not provider_group:
        provider_group = {
            "provider": provider_name,
            "api_key": api_key,
            "models": [new_model_entry],
        }
        selected_models.append(provider_group)
    else:
        if api_key:
            provider_group["api_key"] = api_key
        # Don't duplicate if model already exists
        existing_ids = [m.get("model_id") for m in provider_group.get("models", [])]
        if model_config["name"] not in existing_ids:
            provider_group["models"].append(new_model_entry)

    pool["selected_models"] = selected_models
    save_providers_and_pool(providers, pool)

    # Set as default
    set_default_model(model_config)


def chat_flow():
    # Initialize global registry folder dynamically
    from .config import init_global_core

    init_global_core()

    # Play the ASCII animated logo!
    print_animated_logo()

    print(f"  ✨ {Colors.BOLD}WELCOME TO KOGNISANT CHAT{Colors.RESET}")
    print(
        f"  {Colors.CYAN}──────────────────────────────────────────────────{Colors.RESET}\n"
    )

    project_info = get_project_info()
    if project_info:
        print(
            f"  📁 {Colors.BOLD}Workspace:{Colors.RESET} {project_info['root']} ({Colors.GREEN}Active{Colors.RESET})"
        )
        # Session continuity cues
        context_path = os.path.join(project_info["root"], ".kognisant", "context.md")
        history_dir = os.path.join(project_info["root"], ".kognisant", "history")
        if os.path.exists(context_path):
            try:
                with open(context_path, "r", encoding="utf-8") as f:
                    ctx = f.read()
                # Count tracked phases/tasks
                task_count = ctx.count("- [x]") + ctx.count("- [ ]")
                done_count = ctx.count("- [x]")
                if task_count > 0:
                    print(f"  🧠 Membrain loaded (context.md: {done_count}/{task_count} tasks tracked)")
                else:
                    print(f"  🧠 Membrain loaded")
            except Exception:
                pass

        if os.path.exists(history_dir):
            try:
                sessions = sorted(
                    [f for f in os.listdir(history_dir) if f.endswith(".json")],
                    reverse=True,
                )
                if sessions:
                    latest = sessions[0]
                    latest_path = os.path.join(history_dir, latest)
                    mtime = os.path.getmtime(latest_path)
                    elapsed = time.time() - mtime
                    if elapsed < 3600:
                        ago = f"{int(elapsed // 60)} minutes ago"
                    elif elapsed < 86400:
                        ago = f"{int(elapsed // 3600)} hours ago"
                    else:
                        ago = f"{int(elapsed // 86400)} days ago"
                    print(f"  🕐 Last session: {ago}")
            except Exception:
                pass
        print()
    else:
        print(
            f"  📂 {Colors.BOLD}Workspace:{Colors.RESET} {Colors.YELLOW}No active workspace.{Colors.RESET} (Run 'kognisant init' to enable persistent build context)\n"
        )

    # Check if this is a first-run scenario (no valid providers configured)
    compiled_models = get_compiled_models()
    if not _has_any_valid_provider():
        # First-run setup wizard
        result = _run_first_time_setup()
        if result is None:
            print(f"\n  {Colors.YELLOW}Goodbye!{Colors.RESET}\n")
            return
        elif result == "mock":
            run_mock_chat(project_info)
            return
        else:
            # Refresh compiled models after setup
            compiled_models = get_compiled_models()
            run_api_chat(result, project_info)
            return

    if not compiled_models:
        print(
            f"  ℹ️  {Colors.YELLOW}No configured models are currently reachable.{Colors.RESET}\n"
        )
        print(f"  Options:")
        print(f"    • Start Ollama locally: {Colors.CYAN}ollama serve{Colors.RESET}")
        print(f"    • Add a cloud provider: type {Colors.CYAN}/model{Colors.RESET} inside chat")
        print(f"    • Continue in offline mode (responses are simulated)\n")
        print(f"  Continuing in offline mode...\n")
        run_mock_chat(project_info)
    else:
        # Retrieve the sticky default model from pool
        selected = get_default_model(compiled_models)

        if not selected:
            # Fallback to interactive select if no default found
            selected = select_model(compiled_models)

        if selected is None:
            print(f"\n  {Colors.YELLOW}Goodbye!{Colors.RESET}\n")
            return

        # Check if selected model requires a key but is unconfigured
        if isinstance(selected, dict):
            provider_name = selected.get("provider", "")
            api_key = selected.get("api_key", "")

            if requires_api_key(provider_name, api_key):
                # Prompt them cleanly by passing it through the select_model flow
                # (which has the interactive key prompter built-in)
                selected = select_model([selected])

        if selected is None:
            print(f"\n  {Colors.YELLOW}Goodbye!{Colors.RESET}\n")
            return
        elif selected == "mock":
            run_mock_chat(project_info)
        else:
            # Go straight into chat with the default/active model!
            run_api_chat(selected, project_info)
