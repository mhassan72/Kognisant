import argparse
import sys

from .chat import chat_flow
from .colors import Colors
from .config import (
    find_project_root,
    init_feature_spec,
    init_project,
    list_feature_specs,
)


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

    greet_parser = subparsers.add_parser("greet", help="Greet a user")
    greet_parser.add_argument(
        "-n", "--name", type=str, default="World", help="The name to greet"
    )
    greet_parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose output"
    )

    spec_parser = subparsers.add_parser(
        "spec", help="Initialize or manage Spec-Driven Development feature specs"
    )
    spec_parser.add_argument(
        "name",
        nargs="?",
        default=None,
        help="Feature name to scaffold (.kognisant/specs/<name>/)",
    )
    spec_parser.add_argument(
        "-l", "--list", action="store_true", help="List existing feature specs"
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
            specs = list_feature_specs(root)
            if specs:
                print(f"{Colors.BOLD}Feature Specifications:{Colors.RESET}")
                for s in specs:
                    print(f"  - {Colors.CYAN}{s}{Colors.RESET}")
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
            init_feature_spec(root, args.name)
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
