import json
import os
import sys
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
    load_global_skills,
    load_project_context,
    load_project_memory_guidelines,
    load_providers_and_pool,
    save_chat_session,
    save_providers_and_pool,
    set_default_model,
)
from .network import KognisantAPIError, query_model_api_raw
from .tools import execute_tool, get_active_tools


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
        prompt += "Additionally, you possess the following universal, transferable software engineering skills:\n"
        for skill in global_skills:
            prompt += f"### Global Skill: {skill['name']}\n```markdown\n{skill['content']}\n```\n\n"
        prompt += "Apply these global standard skills to any coding solutions, refactoring, or designs you produce.\n\n"

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
        width = 64
        border = f"{Colors.CYAN}" + "─" * width + f"{Colors.RESET}"

        print(f"\n  ✨ {Colors.BOLD}KOGNISANT HELP MENU & SLASH COMMANDS{Colors.RESET}")
        print(f"  {border}\n")

        print(f"  {Colors.BOLD}📁 Workspace & Memory:{Colors.RESET}")
        print(
            f"    {Colors.CYAN}/context{Colors.RESET}      - Display local project build context & active tasks (Membrain)"
        )
        print(
            f"    {Colors.CYAN}/skills{Colors.RESET}       - List loaded global transferable guidelines (Core Memory)"
        )
        print(
            f"    {Colors.CYAN}/files{Colors.RESET}        - List all files currently indexed in your project workspace"
        )
        print(
            f"    {Colors.CYAN}/read <path>{Colors.RESET}  - Load a specific project file directly into conversational memory\n"
        )

        print(f"  {Colors.BOLD}🤖 Model & Provider Configuration:{Colors.RESET}")
        print(
            f"    {Colors.CYAN}/model{Colors.RESET}        - Open the Model Pool Wizard to switch active models or register cloud endpoints"
        )
        print(
            f"    {Colors.CYAN}/providers{Colors.RESET}    - Inspect all configured AI providers, keys, and model pools\n"
        )

        print(f"  {Colors.BOLD}🚀 Autonomous Multi-Agent Swarms:{Colors.RESET}")
        print(
            f"    {Colors.CYAN}/agent <task>{Colors.RESET} - Deploy a concurrent, self-evaluating PERP swarm (Plan, Execute, Reflect, Persist)\n"
        )

        print(f"  {Colors.BOLD}📋 Multi-Line Typing & Paste Mode:{Colors.RESET}")
        print(
            f"    {Colors.CYAN}/paste{Colors.RESET} (or {Colors.CYAN}/p{Colors.RESET}) - Open secure Paste Mode to paste large logs, stack traces, or code files."
        )
        print(
            f"                    Type {Colors.CYAN}/end{Colors.RESET} on a new line and press Enter to submit.\n"
        )

        print(f"  {Colors.BOLD}🧹 Utilities & Sessions:{Colors.RESET}")
        print(
            f"    {Colors.CYAN}/clear{Colors.RESET}        - Flush active conversational logs (fresh context) while preserving system contracts"
        )
        print(
            f"    {Colors.CYAN}/help{Colors.RESET}         - Display this beautiful, spacious help matrix"
        )
        print(
            f"    {Colors.BOLD}exit{Colors.RESET} (or {Colors.BOLD}quit{Colors.RESET})  - Terminate active session and exit cleanly\n"
        )
        print(f"  {border}\n")
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

        # Interactive inline model selection menu
        print(f"\n  📦 {Colors.BOLD}Kognisant Model Pool Wizard:{Colors.RESET}\n")
        for idx, m in enumerate(compiled_models, 1):
            provider_name = m.get("provider", "Unknown")
            display_name = m.get("display_name", m["name"])
            is_active = (
                f" {Colors.GREEN}[Active]{Colors.RESET}"
                if active_model_config
                and m["name"] == active_model_config["name"]
                and m["provider"] == active_model_config["provider"]
                else ""
            )
            print(
                f"    [{Colors.CYAN}{idx}{Colors.RESET}] {display_name} ({Colors.MAGENTA}{provider_name}{Colors.RESET}){is_active}"
            )
        print(f"    [{Colors.GREEN}a{Colors.RESET}] Add custom OpenAI-compatible model")
        print("    [Enter] Cancel and resume chat\n")

        try:
            choice = input(f"  👉 {Colors.BOLD}Enter selection: {Colors.RESET}").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return True

        if not choice:
            print("     Resuming session...\n")
            return True

        if choice.lower() == "a":
            # Add new model on the fly
            new_model = select_model(compiled_models)
            if new_model and isinstance(new_model, dict) and active_model_config:
                active_model_config.clear()
                active_model_config.update(new_model)
                set_default_model(new_model)
                print(
                    f"  🔄 {Colors.GREEN}Model Switched:{Colors.RESET} Active model is now '{new_model['display_name']}' ({new_model['provider']}).\n"
                )
            return True

        try:
            index = int(choice) - 1
            if 0 <= index < len(compiled_models):
                selected = compiled_models[index]

                # Check for required API keys
                provider_name = selected.get("provider", "")
                api_key = selected.get("api_key", "")

                if provider_name != "Ollama (Local)" and (
                    not api_key or "your-" in api_key
                ):
                    print(
                        f"\n  🔑 {Colors.YELLOW}The provider '{provider_name}' requires an API Key.{Colors.RESET}"
                    )
                    try:
                        new_key = input(
                            f"     Please enter your {provider_name} API Key: "
                        ).strip()
                    except (EOFError, KeyboardInterrupt):
                        return True

                    if not new_key:
                        print(
                            f"     {Colors.RED}No key entered. Selection aborted.{Colors.RESET}\n"
                        )
                        return True

                    # Save key inside the nested structures of models_pool.json
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

                # In-place state swap using dict reference
                if active_model_config:
                    active_model_config.clear()
                    active_model_config.update(selected)
                    set_default_model(selected)
                    print(
                        f"  🔄 {Colors.GREEN}Model Switched:{Colors.RESET} Active model is now '{selected['display_name']}' ({selected['provider']}).\n"
                    )
                return True
        except ValueError:
            pass
        print(f"     {Colors.RED}Invalid selection. Resuming chat...{Colors.RESET}\n")
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
        except Exception as ex:
            print(f"{Colors.RED}[!] Error reading file: {ex}{Colors.RESET}\n")
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

    return False


def run_mock_chat(project_info=None):
    print(f"\n{Colors.BOLD}--- Starting Mock Chat (Offline) ---{Colors.RESET}")
    if project_info:
        print(
            f"📁 {Colors.CYAN}Project Mode Active:{Colors.RESET} {project_info['name']}"
        )
        print(
            f"  🧠 {Colors.GREEN}Membrain Active:{Colors.RESET} Loaded '.kognisant/context.md' and '.kognisant/memory-guidlines.md' steering rules into conversation context."
        )
    print(f"Type {Colors.CYAN}/help{Colors.RESET} to see available commands.")
    print("Press Ctrl+D (Cmd+D) or Ctrl+C to exit.\n")

    history = []
    session_file = (
        f"session_{time.strftime('%Y%m%d_%H%M%S')}.json" if project_info else None
    )

    while True:
        try:
            user_input = prompt_boxed_input()
        except (EOFError, KeyboardInterrupt):
            print(f"\n{Colors.YELLOW}Goodbye! Thanks for chatting.{Colors.RESET}")
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
        time.sleep(1.0)
        spinner.stop()

        turn_count = len(history)
        if turn_count == 1:
            response = f"Hello! It's nice to meet you. You said: '{cleaned_input}'."
        else:
            response = f"Understood. We are on turn {turn_count} of our chat. You previously mentioned: '{history[-2]}'. What's next?"

        print(f"{Colors.CYAN}Kognisant >{Colors.RESET}\n{render_markdown(response)}\n")


def run_api_chat(model_config, project_info=None):
    """Active multi-turn LLM chat loop powered by standard compatible APIs with tool execution and self-healing fallback."""
    model_name = model_config["name"]
    provider_name = model_config["provider"]
    display_name = model_config.get("display_name", model_name)

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
    print("Press Ctrl+D (Cmd+D) or Ctrl+C to exit.\n")

    messages = []
    session_file = (
        f"session_{time.strftime('%Y%m%d_%H%M%S')}.json" if project_info else None
    )

    if project_info:
        messages.append(build_system_prompt(project_info))
        save_chat_session(project_info, messages, session_file)

    while True:
        try:
            user_input = prompt_boxed_input()
        except (EOFError, KeyboardInterrupt):
            print(f"\n{Colors.YELLOW}Goodbye! Thanks for chatting.{Colors.RESET}")
            break

        cleaned_input = user_input.strip()
        if not cleaned_input:
            continue

        if cleaned_input.lower() in ["exit", "quit"]:
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

        # Checkpoint-Based Conversation Rollback: Save exact length of messages list before the turn
        checkpoint_idx = len(messages)

        messages.append({"role": "user", "content": cleaned_input})
        save_chat_session(project_info, messages, session_file)

        spinner = Spinner()
        spinner.start()
        try:
            success = False
            while True:
                payload = {
                    "model": model_config["name"],
                    "messages": messages,
                    "stream": False,
                }

                # Check tool calling support dynamically
                supports_tools = model_config.get("capabilities", {}).get(
                    "tool_calling", True
                )
                if supports_tools:
                    payload["tools"] = get_active_tools()

                resp_data = query_model_api_raw(
                    model_config["api_base_url"], model_config["api_key"], payload
                )

                if not resp_data or "choices" not in resp_data:
                    raise KognisantAPIError(
                        "Empty or malformed JSON returned from the model API."
                    )

                choice = resp_data["choices"][0]
                assistant_message = choice["message"]
                tool_calls = assistant_message.get("tool_calls")

                if tool_calls and supports_tools:
                    messages.append(assistant_message)
                    save_chat_session(project_info, messages, session_file)

                    spinner.stop()

                    # 1. PLAN
                    print(f"\n{Colors.BOLD}{Colors.CYAN}PLAN{Colors.RESET}")
                    print("\033[90m────────────────────────────────────────────\033[0m")
                    for tc in tool_calls:
                        func_name = tc["function"]["name"]
                        func_args = tc["function"]["arguments"]
                        desc = get_tool_call_description(func_name, func_args)
                        print(f"  • {desc}")
                    print()

                    # 2. EXECUTION
                    print(f"{Colors.BOLD}{Colors.YELLOW}EXECUTION{Colors.RESET}")
                    print("\033[90m────────────────────────────────────────────\033[0m")
                    sys.stdout.flush()

                    any_failed = False

                    for tc in tool_calls:
                        call_id = tc.get("id")
                        func_name = tc["function"]["name"]
                        func_args = tc["function"]["arguments"]
                        desc = get_tool_call_description(func_name, func_args)

                        # Write loading status
                        sys.stdout.write(f"  {Colors.CYAN}◓{Colors.RESET} {desc} ...")
                        sys.stdout.flush()

                        try:
                            result = execute_tool(func_name, func_args, project_info)
                            is_err = isinstance(result, str) and result.startswith(
                                "[Error]"
                            )
                        except Exception as ex:
                            result = f"[Error] {ex}"
                            is_err = True

                        if is_err:
                            any_failed = True
                            err_msg = result.replace("[Error]", "").strip()
                            if len(err_msg) > 60:
                                err_msg = err_msg[:57] + "..."
                            sys.stdout.write(
                                f"\r  {Colors.RED}✗{Colors.RESET} {desc} {Colors.RED}failed{Colors.RESET} ({err_msg})\n"
                            )
                        else:
                            sys.stdout.write(
                                f"\r  {Colors.GREEN}✓{Colors.RESET} {desc}\n"
                            )
                        sys.stdout.flush()

                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": call_id,
                                "name": func_name,
                                "content": result,
                            }
                        )
                        save_chat_session(project_info, messages, session_file)

                    # 3. RESULT
                    print(f"\n{Colors.BOLD}{Colors.GREEN}RESULT{Colors.RESET}")
                    print("\033[90m────────────────────────────────────────────\033[0m")
                    if any_failed:
                        print(
                            f"  {Colors.RED}Some tool executions encountered issues.{Colors.RESET}"
                        )
                    else:
                        print(
                            f"  {Colors.GREEN}All tools executed successfully.{Colors.RESET}"
                        )
                    print()

                    spinner = Spinner("Kognisant is thinking")
                    spinner.start()
                    continue
                else:
                    response = assistant_message.get("content") or ""
                    messages.append(assistant_message)
                    save_chat_session(project_info, messages, session_file)
                    success = True
                    break

        except Exception as e:
            # Self-healing handler: If error is because model doesn't support tools, disable tools and retry the prompt!
            err_msg = str(e).lower()
            if (
                "does not support tools" in err_msg
                or "tool_calling" in err_msg
                or ("http error 400" in err_msg and "tool" in err_msg)
            ):
                if model_config.get("capabilities", {}).get("tool_calling", True):
                    spinner.stop()
                    print(
                        f"  ⚠️  {Colors.YELLOW}Note:{Colors.RESET} '{display_name}' does not support tool calling. Resuming cleanly in standard chat mode..."
                    )
                    if "capabilities" not in model_config:
                        model_config["capabilities"] = {}
                    model_config["capabilities"]["tool_calling"] = False

                    # Rollback checkpoint just in case
                    while len(messages) > checkpoint_idx + 1:  # keep user prompt
                        messages.pop()

                    # Restart spinner and retry the completions query cleanly
                    spinner = Spinner()
                    spinner.start()
                    continue

            # Bulletproof rollback: Reset history exactly to checkpoint
            while len(messages) > checkpoint_idx:
                messages.pop()
            save_chat_session(project_info, messages, session_file)

            # Format and surface friendly, human-readable error messages
            if isinstance(e, KognisantAPIError):
                response = (
                    f"{Colors.RED}[Error] API Transport Failure: {e}{Colors.RESET}"
                )
            else:
                response = f"{Colors.RED}[Error] Failed to get response from model: {e}\nPlease ensure the API endpoint is fully reachable.{Colors.RESET}"
            success = False
        finally:
            spinner.stop()

        if success:
            print(
                f"{Colors.CYAN}Kognisant >{Colors.RESET}\n{render_markdown(response)}\n"
            )
        else:
            print(f"\n{response}\n")


def select_model(models):
    print(
        f"  📦 {Colors.BOLD}Select an AI Model to power this session:{Colors.RESET}\n"
    )
    for idx, model in enumerate(models, 1):
        provider_name = model.get("provider", "Unknown")
        display_name = model.get("display_name", model["name"])
        print(
            f"    [{Colors.CYAN}{idx}{Colors.RESET}] {display_name} ({Colors.MAGENTA}{provider_name}{Colors.RESET})"
        )
    print(f"    [{Colors.GREEN}a{Colors.RESET}] Add custom OpenAI-compatible model")
    print("    [Enter] Cancel and resume chat\n")

    while True:
        try:
            choice = input(
                f"  👉 {Colors.BOLD}Enter model number, 'a', or 'm': {Colors.RESET}"
            ).strip()
        except (EOFError, KeyboardInterrupt):
            return None

        if choice.lower() == "m":
            return "mock"

        if choice.lower() == "a":
            # Add custom model interactively
            print(
                f"\n  ➕ {Colors.BOLD}Add a Custom OpenAI-Compatible Model{Colors.RESET}\n"
            )
            try:
                provider_name = input(
                    "     1. Enter Provider Name (e.g. OpenRouter, Groq): "
                ).strip()
                if not provider_name:
                    print(
                        f"     {Colors.RED}Provider name cannot be empty.{Colors.RESET}\n"
                    )
                    continue

                model_name = input(
                    "     2. Enter Model Name (e.g. Kimi-K2.6): "
                ).strip()
                if not model_name:
                    print(
                        f"     {Colors.RED}Model name cannot be empty.{Colors.RESET}\n"
                    )
                    continue

                model_id = input(
                    "     3. Enter Model ID (e.g. moonshotai/Kimi-K2.6): "
                ).strip()
                if not model_id:
                    print(f"     {Colors.RED}Model ID cannot be empty.{Colors.RESET}\n")
                    continue

                api_base_url = input(
                    "     4. Enter Base URL (e.g. https://api.openai.com/v1): "
                ).strip()
                if not api_base_url:
                    print(f"     {Colors.RED}Base URL cannot be empty.{Colors.RESET}\n")
                    continue

                api_key = input("     5. Enter API Key (press Enter if none): ").strip()
            except (EOFError, KeyboardInterrupt):
                return None

            new_model = {
                "name": model_id,
                "display_name": model_name,
                "provider": provider_name,
                "api_base_url": api_base_url,
                "api_key": api_key,
                "capabilities": {"tool_calling": True, "reasoning": True},
            }

            # Save newly added model globally inside hierarchical models_pool.json
            providers, pool = load_providers_and_pool()
            selected_models = []
            if isinstance(pool, dict):
                selected_models = pool.get("selected_models", [])
                if not isinstance(selected_models, list):
                    selected_models = []

            # Find if provider already exists in selected_models
            provider_group = None
            for group in selected_models:
                if group.get("provider") == provider_name:
                    provider_group = group
                    break

            new_m_dict = {
                "vendor": provider_name,
                "name": model_name,
                "model_id": model_id,
                "api_base_url": api_base_url,
                "capabilities": {"tool_calling": True, "reasoning": True},
            }

            if not provider_group:
                provider_group = {
                    "provider": provider_name,
                    "api_key": api_key,
                    "models": [new_m_dict],
                }
                selected_models.append(provider_group)
            else:
                if api_key:
                    provider_group["api_key"] = api_key
                provider_group["models"].append(new_m_dict)

            pool["selected_models"] = selected_models
            save_providers_and_pool(providers, pool)

            print(
                f"\n  ✅ {Colors.GREEN}Model '{model_name}' successfully added and saved globally!{Colors.RESET}\n"
            )
            return new_model

        try:
            index = int(choice) - 1
            if 0 <= index < len(models):
                selected = models[index]
                provider_name = selected.get("provider", "")
                api_key = selected.get("api_key", "")

                if provider_name != "Ollama (Local)" and (
                    not api_key or "your-" in api_key
                ):
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
            f"  📁 {Colors.BOLD}Workspace:{Colors.RESET} {project_info['root']} ({Colors.GREEN}Active{Colors.RESET})\n"
        )
    else:
        print(
            f"  📂 {Colors.BOLD}Workspace:{Colors.RESET} {Colors.YELLOW}No active workspace.{Colors.RESET} (Run 'kognisant init' to enable persistent build context)\n"
        )

    # Compile the explicit models pool list (no dynamic local-tag injection)
    compiled_models = get_compiled_models()

    if not compiled_models:
        print(
            f"  ⚠️  {Colors.YELLOW}No AI models are currently configured or available.{Colors.RESET}"
        )
        print("     Starting offline Mock Chat mode...\n")
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

            if provider_name != "Ollama (Local)" and (
                not api_key or "your-" in api_key
            ):
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
