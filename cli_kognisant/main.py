import argparse
import sys

from .chat import chat_flow
from .colors import Colors
from .config import (
    find_project_root,
    init_project,
)


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


def main():
    parser = argparse.ArgumentParser(
        description="cli-kognisant: A Python CLI application."
    )
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    subparsers.add_parser("init", help="Initialize a Kognisant project directory")
    subparsers.add_parser(
        "chat",
        help="Start an interactive multi-turn chat session with Ollama detection",
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

    awesome_parser = subparsers.add_parser(
        "awesome_feature", help="Trigger the awesome feature"
    )
    awesome_parser.add_argument(
        "-l", "--level", type=int, default=1, help="Awesome level (1-10)"
    )

    args = parser.parse_args()

    if args.command == "init":
        init_project()
    elif args.command == "status":
        _handle_status()
    elif args.command == "setup":
        _handle_setup()
    elif args.command == "chat":
        chat_flow()
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
    elif args.command == "awesome_feature":
        level = max(1, min(10, args.level))
        print(
            f"{Colors.BOLD}{Colors.MAGENTA}Awesome feature engaged at level {level}!{Colors.RESET}"
        )
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
