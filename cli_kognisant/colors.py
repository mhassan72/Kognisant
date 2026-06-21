import os
import re
import sys
import threading
import time


# Dependency-free ANSI Colors
class Colors:
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    MAGENTA = "\033[95m"
    BOLD = "\033[1m"
    RESET = "\033[0m"


class Spinner:
    """A thread-safe terminal spinner for displaying loading states."""

    def __init__(self, message=f"{Colors.CYAN}Kognisant is thinking{Colors.RESET}", show_elapsed=False, timeout=None):
        self.message = message
        self.frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        self.delay = 0.08
        self.stop_event = threading.Event()
        self.thread = None
        self.show_elapsed = show_elapsed
        self.timeout = timeout  # Optional timeout for relative hints
        self._start_time = None

    def update_message(self, new_msg: str):
        """Update the spinner message while running (thread-safe in CPython)."""
        self.message = new_msg

    def _spin(self):
        idx = 0
        self._start_time = time.monotonic()
        while not self.stop_event.is_set():
            frame = self.frames[idx % len(self.frames)]
            if self.show_elapsed:
                elapsed = time.monotonic() - self._start_time
                elapsed_str = f"{elapsed:.0f}s"
                # Timeout-relative hints when timeout is provided
                if self.timeout and self.timeout > 0:
                    threshold_80 = self.timeout * 0.8
                    threshold_50 = self.timeout * 0.5
                    if elapsed > threshold_80:
                        hint = f" — {Colors.YELLOW}Ctrl+C to cancel, /model to switch{Colors.RESET}"
                    elif elapsed > threshold_50:
                        hint = f" — {Colors.YELLOW}this may take a moment{Colors.RESET}"
                    else:
                        hint = ""
                else:
                    # Fallback to hardcoded thresholds
                    if elapsed > 120:
                        hint = f" — {Colors.YELLOW}Ctrl+C to cancel, /model to switch{Colors.RESET}"
                    elif elapsed > 60:
                        hint = f" — {Colors.YELLOW}large models may take 1-2 min{Colors.RESET}"
                    else:
                        hint = ""
                display_msg = f"{self.message} — {elapsed_str}{hint}"
            else:
                display_msg = self.message
            sys.stdout.write(f"\r\033[2K{display_msg} {Colors.CYAN}{frame}{Colors.RESET} ")
            sys.stdout.flush()
            idx += 1
            time.sleep(self.delay)

        # Clear the spinner line
        sys.stdout.write("\r\033[2K")
        sys.stdout.flush()

    def start(self):
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._spin, daemon=True)
        self.thread.start()

    def stop(self):
        if self.thread:
            self.stop_event.set()
            self.thread.join()


def print_animated_logo():
    """Renders a beautiful 2-second RGB color fade-in animation of the Kognisant ASCII logo."""
    logo = [
        r"  _  _                      _                 _   ",
        r" | |/ /___  __ _ _ _  _ _  (_)___ __ _ _ _  _| |_ ",
        r" | ' </ _ \/ _` | ' \| ' \ | (_-</ _` | ' \/ _`  _|",
        r" |_|\_\___/\__, |_||_|_||_||_/__/\__,_|_||_\__,__|",
        r"           |___/                                  ",
    ]

    is_tty = sys.stdout.isatty() and sys.stdin.isatty()

    if not is_tty:
        # Non-interactive fallback: print once instantly to prevent blocking pipelines
        print()
        for line in logo:
            sys.stdout.write(f"{Colors.CYAN}{line}{Colors.RESET}\n")
        print(f"               {Colors.BOLD}AI Core Active | v0.1.0{Colors.RESET}\n")
        return

    print()  # Opening padding

    # 1. 2-second logo color fade (20 frames, 0.1s delay per frame)
    total_duration = 2.0
    frames = 20
    delay = total_duration / frames

    for frame in range(1, frames + 1):
        ratio = frame / frames
        # RGB color fade: from dark teal (0, 15, 15) to bright Cyan (0, 255, 255)
        r = 0
        g = int(255 * ratio)
        b = int(255 * ratio)

        color_code = f"\033[38;2;{r};{g};{b}m"

        for line in logo:
            sys.stdout.write(f"{color_code}{line}{Colors.RESET}\n")
        sys.stdout.flush()

        if frame < frames:
            time.sleep(delay)
            # Move cursor up 5 lines to overwrite in-place
            sys.stdout.write("\033[5A")
            sys.stdout.flush()

    # 2. Subtitle fade-in over 0.3 seconds (5 frames)
    sub_frames = 5
    sub_delay = 0.3 / sub_frames
    sub_text = "               AI Core Active | v0.1.0"

    for frame in range(1, sub_frames + 1):
        ratio = frame / sub_frames
        # Gray fade: from dim gray (50, 50, 50) to bold white (255, 255, 255)
        r = int(50 + (205 * ratio))
        g = int(50 + (205 * ratio))
        b = int(50 + (205 * ratio))

        sub_color = f"\033[1;38;2;{r};{g};{b}m"
        sys.stdout.write(f"\r{sub_color}{sub_text}{Colors.RESET}")
        sys.stdout.flush()
        time.sleep(sub_delay)

    print("\n\n")  # Final spacing
    time.sleep(0.1)


def get_box_width():
    try:
        columns = os.get_terminal_size().columns
        return min(max(40, columns - 4), 80)
    except (OSError, ValueError):
        return 70


def prompt_boxed_input():
    """Prompts the user for input inside a box, supporting robust multi-line paste mode."""
    width = get_box_width()

    top_border_text = "┌── Your Message " + "─" * (width - 17) + "┐"
    bottom_border_text = "└" + "─" * (width - 2) + "┘"

    top_border = f"{Colors.GREEN}{top_border_text}{Colors.RESET}"
    bottom_border = f"{Colors.GREEN}{bottom_border_text}{Colors.RESET}"

    is_tty = sys.stdout.isatty() and sys.stdin.isatty()

    if is_tty:
        print(top_border)
    try:
        prompt_str = f"{Colors.GREEN}│ You > {Colors.RESET}" if is_tty else "You > "
        user_input = input(prompt_str)

        # Intercept and transition to multi-line Paste Mode
        cleaned_inp = user_input.strip().lower()
        if cleaned_inp == "/paste" or cleaned_inp == "/p":
            print(
                f"  📋 {Colors.BOLD}Paste Mode Active:{Colors.RESET} Paste your text below."
            )
            print(
                f"     Type {Colors.CYAN}/end{Colors.RESET} on a new line and press Enter to submit.\n"
            )
            sys.stdout.flush()

            lines = []
            while True:
                try:
                    line = input(
                        f"{Colors.GREEN}│ ... {Colors.RESET}" if is_tty else "... "
                    )
                    if line.strip() == "/end":
                        break
                    lines.append(line)
                except (EOFError, KeyboardInterrupt):
                    break

            user_input = "\n".join(lines)

            # Collapse the multi-line paste block cleanly on the terminal screen
            if is_tty:
                sys.stdout.write(
                    f"\r  {Colors.GREEN}✓ [Paste]{Colors.RESET} Received {len(lines)} lines of text.\n"
                )
                sys.stdout.flush()

    except (EOFError, KeyboardInterrupt) as e:
        if is_tty:
            print(bottom_border)
        raise e

    if is_tty and cleaned_inp != "/paste" and cleaned_inp != "/p":
        # Draw the right-side border on the input line
        sys.stdout.write(f"\033[A\033[{width}G{Colors.GREEN}│{Colors.RESET}\n")
        sys.stdout.write(bottom_border + "\n")
        sys.stdout.flush()

        cleaned = user_input.strip()
        if not cleaned:
            # Erase all 3 lines completely
            sys.stdout.write("\033[3A")
            sys.stdout.write("\033[2K\n")
            sys.stdout.write("\033[2K\n")
            sys.stdout.write("\033[2K")
            sys.stdout.write("\033[2A")
            sys.stdout.flush()
        else:
            # Replace box with a single clean line
            sys.stdout.write("\033[3A")
            sys.stdout.write(
                "\033[2K" + f"{Colors.GREEN}You >{Colors.RESET} {cleaned}\n"
            )
            sys.stdout.write("\033[2K\n")
            sys.stdout.write("\033[2K")
            sys.stdout.write("\033[A")
            sys.stdout.flush()

    return user_input


def highlight_code(code_text, language="python"):
    """Applies beautiful ANSI syntax highlighting to code snippets based on the language."""
    language = language.lower().strip()

    if language in ["python", "py"]:
        # Keyword regex
        keywords = r"\b(def|class|import|from|return|if|elif|else|while|try|except|finally|for|in|with|as|print|self|None|True|False|and|or|not|is|pass|lambda|global|nonlocal|raise|assert|yield)\b"
        # Function calls
        functions = r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*(?=\()"
        # Numbers
        numbers = r"\b(\d+)\b"

        lines = []
        for line in code_text.splitlines():
            # Separate comment to keep it clean
            comment_match = re.search(r"(#[^\n]*)$", line)
            comment_text = ""
            if comment_match:
                comment_text = comment_match.group(1)
                line = line[: comment_match.start()]

            # Highlight double-quoted strings
            line = re.sub(r"(\"[^\"]*\")", f"{Colors.GREEN}\\1{Colors.RESET}", line)
            # Highlight single-quoted strings
            line = re.sub(r"('[^']*')", f"{Colors.GREEN}\\1{Colors.RESET}", line)
            # Highlight keywords
            line = re.compile(keywords).sub(f"{Colors.MAGENTA}\\1{Colors.RESET}", line)
            # Highlight functions
            line = re.compile(functions).sub(f"{Colors.CYAN}\\1{Colors.RESET}", line)
            # Highlight numbers
            line = re.sub(numbers, f"{Colors.YELLOW}\\1{Colors.RESET}", line)

            # Re-append comments in gray
            if comment_text:
                line += f"\033[90m{comment_text}{Colors.RESET}"

            lines.append(line)
        return "\n".join(lines)

    elif language in ["rust", "rs"]:
        # Rust keywords
        keywords = r"\b(fn|let|mut|match|use|mod|struct|enum|impl|trait|pub|crate|self|Self|return|if|else|loop|while|for|in|as|async|await|dyn|type|const|static|where|move|unsafe|ref)\b"
        # Macros
        macros = r"\b([a-zA-Z_][a-zA-Z0-9_]*!)\b"
        # Function calls
        functions = r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*(?=\()"
        # Numbers
        numbers = r"\b(\d+)\b"

        lines = []
        for line in code_text.splitlines():
            comment_match = re.search(r"(//[^\n]*)$", line)
            comment_text = ""
            if comment_match:
                comment_text = comment_match.group(1)
                line = line[: comment_match.start()]

            # Highlight double-quoted strings
            line = re.sub(r"(\"[^\"]*\")", f"{Colors.GREEN}\\1{Colors.RESET}", line)
            # Highlight single-quoted characters
            line = re.sub(r"('[^']')", f"{Colors.GREEN}\\1{Colors.RESET}", line)
            # Highlight keywords
            line = re.compile(keywords).sub(f"{Colors.MAGENTA}\\1{Colors.RESET}", line)
            # Highlight macros in Cyan
            line = re.compile(macros).sub(f"{Colors.CYAN}\\1{Colors.RESET}", line)
            # Highlight functions
            line = re.compile(functions).sub(f"{Colors.CYAN}\\1{Colors.RESET}", line)
            # Highlight numbers
            line = re.sub(numbers, f"{Colors.YELLOW}\\1{Colors.RESET}", line)

            # Re-append comments in gray
            if comment_text:
                line += f"\033[90m{comment_text}{Colors.RESET}"

            lines.append(line)
        return "\n".join(lines)

    elif language in ["json", "js"]:
        lines = []
        for line in code_text.splitlines():
            # Highlight JSON keys in Cyan
            line = re.sub(
                r"(\"[^\"]*\")\s*(?=:)", f"{Colors.CYAN}\\1{Colors.RESET}", line
            )
            # Highlight JSON string values in Green
            line = re.sub(
                r"(?<=:)\s*(\"[^\"]*\")", f" {Colors.GREEN}\\1{Colors.RESET}", line
            )
            # Highlight keywords (true/false/null) in Magenta
            line = re.sub(
                r"\b(true|false|null)\b", f"{Colors.MAGENTA}\\1{Colors.RESET}", line
            )
            # Highlight numbers in Yellow
            line = re.sub(r"\b(\d+)\b", f"{Colors.YELLOW}\\1{Colors.RESET}", line)
            lines.append(line)
        return "\n".join(lines)

    elif language in ["bash", "sh", "shell", "zsh"]:
        lines = []
        for line in code_text.splitlines():
            if line.strip().startswith("#"):
                line = f"\033[90m{line}{Colors.RESET}"
            else:
                # Highlight flags: -f, --flag
                line = re.sub(
                    r"\s(\-[a-zA-Z0-9_\-]+)", f" {Colors.YELLOW}\\1{Colors.RESET}", line
                )
                # Highlight strings
                line = re.sub(
                    r"(\"[^\"]*\"|'[^']*')", f"{Colors.GREEN}\\1{Colors.RESET}", line
                )
            lines.append(line)
        return "\n".join(lines)

    return code_text


def parse_inline_markdown(text):
    """Parses inline markdown formatters like **bold**, *italic*, and `inline code`."""
    # Bold: **text** (non-greedy)
    text = re.sub(r"\*\*(.*?)\*\*", f"{Colors.BOLD}\\1{Colors.RESET}", text)
    # Italic: *text* (non-greedy)
    text = re.sub(r"\*(.*?)\*", "\033[3m\\1\033[0m", text)
    # Inline code: `code`
    text = re.sub(r"`([^`]+)`", f"{Colors.YELLOW}\\1{Colors.RESET}", text)
    return text


def render_markdown(markdown_text):
    """Parses and renders markdown text using clean, beautiful ANSI colors and code framing."""
    if not markdown_text:
        return ""
    if not isinstance(markdown_text, str):
        markdown_text = str(markdown_text)

    is_tty = sys.stdout.isatty() and sys.stdin.isatty()
    if not is_tty:
        return markdown_text

    rendered_lines = []
    in_code_block = False
    code_lines = []
    code_lang = ""

    for line in markdown_text.splitlines():
        # Code block boundary
        if line.strip().startswith("```"):
            if in_code_block:
                code_content = "\n".join(code_lines)
                highlighted = highlight_code(code_content, code_lang)

                # Draw a gorgeous, clean terminal boundary frame matching terminal columns
                width = get_box_width()
                border = "\033[90m" + "─" * (width - 4) + f"{Colors.RESET}"
                rendered_lines.append(border)
                for cl in highlighted.splitlines():
                    rendered_lines.append(f"  {cl}")
                rendered_lines.append(border)

                in_code_block = False
                code_lines = []
            else:
                in_code_block = True
                code_lang = line.replace("```", "").strip()
            continue

        if in_code_block:
            code_lines.append(line)
            continue

        # Render Markdown elements
        line_stripped = line.strip()

        # Headers (#, ##, ###) in BOLD CYAN
        if line_stripped.startswith("#"):
            level = len(line_stripped) - len(line_stripped.lstrip("#"))
            header_text = line_stripped.lstrip("# ").strip()
            # Dynamic header size colors
            if level == 1:
                color = Colors.MAGENTA  # Pink H1
            elif level == 2:
                color = Colors.CYAN  # Cyan H2
            elif level == 3:
                color = Colors.YELLOW  # Gold H3
            else:
                color = Colors.GREEN  # Green H4-H6
            rendered_lines.append(
                f"\n{Colors.BOLD}{color}{'#' * level} {header_text}{Colors.RESET}"
            )
            continue

        # Blockquotes (>) in gray with side margin
        if line_stripped.startswith(">"):
            quote_text = line_stripped.lstrip("> ").strip()
            rendered_lines.append(f"  \033[90m│\033[0m  \033[3m{quote_text}\033[0m")
            continue

        # Bullets (- or *) swapped with a clean Cyan bullet symbol
        if line_stripped.startswith("- ") or line_stripped.startswith("* "):
            bullet_text = line_stripped.lstrip("-* ").strip()
            bullet_text = parse_inline_markdown(bullet_text)
            rendered_lines.append(f"  {Colors.CYAN}•{Colors.RESET} {bullet_text}")
            continue

        # Standard lines
        rendered_lines.append(parse_inline_markdown(line))

    return "\n".join(rendered_lines)
