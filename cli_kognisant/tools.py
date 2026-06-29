import json
import os
import re
import signal
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

from .scripts import create_script, read_script, edit_script, delete_script, list_scripts
from .jobs import JobQueue, CronParser, format_error, CANCELLABLE_STATES, TERMINAL_STATES

# Standard tools specification available to Kognisant models
# Contains local workspace capabilities, standard headless browser, native browser launcher, headless search, and active console monitor
TOOLS_SPEC = [
    {
        "type": "function",
        "function": {
            "name": "read_project_file",
            "description": "Read the contents of a specific file in the active project workspace root.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "The project-relative path of the file to read (e.g. 'cli_kognisant/main.py' or '.kognisant/context.md').",
                    }
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_project_file",
            "description": "Create a brand new file inside the project workspace root directory with specified content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "The project-relative path of the new file to create (e.g. 'tests/test_chat.py').",
                    },
                    "content": {
                        "type": "string",
                        "description": "The initial text content for the new file.",
                    },
                },
                "required": ["file_path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_project_directory",
            "description": "Create a new directory (and any necessary parent directories) inside the project workspace root.",
            "parameters": {
                "type": "object",
                "properties": {
                    "directory_path": {
                        "type": "string",
                        "description": "The project-relative path of the directory to create (e.g. 'tests/mock_data').",
                    }
                },
                "required": ["directory_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_project_path",
            "description": "Delete a file or directory recursively inside the project workspace root. Be extremely careful when using this.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "The project-relative path of the file or directory to delete.",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_project_file",
            "description": "Edit an existing file in the project. Provide the project-relative file path and a list of edits. Each edit must contain the exact 'old_text' to find in the file and the 'new_text' to replace it with.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "The project-relative path of the file to edit.",
                    },
                    "edits": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "old_text": {
                                    "type": "string",
                                    "description": "The exact block of code to find in the file. Be minimal and precise to ensure matching.",
                                },
                                "new_text": {
                                    "type": "string",
                                    "description": "The new block of code to replace it with.",
                                },
                            },
                            "required": ["old_text", "new_text"],
                        },
                        "description": "List of find-and-replace edits to apply sequentially.",
                    },
                },
                "required": ["file_path", "edits"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_project_files",
            "description": "List all files indexed recursively inside the active project workspace root.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browse_web_page",
            "description": "Fetch a public webpage URL, clean it of HTML markup, and return the plain readable text content in the background. Captures and appends any JavaScript console error logs autonomously.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The absolute URL to browse (e.g. 'https://docs.pytest.org/en/stable/').",
                    }
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_in_native_browser",
            "description": "Open a search query or a direct URL in the user's default native desktop web browser (e.g. Chrome/Safari) VISUALLY. Only use this when the user explicitly asks to open a webpage or run a search inside their desktop browser application.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query_or_url": {
                        "type": "string",
                        "description": "The search query (e.g. 'how to mock with pytest') or direct URL (e.g. 'https://github.com') to open in the native browser application.",
                    }
                },
                "required": ["query_or_url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Perform a headless background web search using DuckDuckGo and return the text search results (titles, URLs, and snippets). Always use this when the user asks to look up latest news, find current events, or search the web, to retrieve the results directly in the chat.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query (e.g. 'latest news on X about Iran war').",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "capture_active_browser_console",
            "description": "Capture the active browser developer console logs (console.log, console.error, runtime crashes) directly from the user's active desktop Brave or Chrome browser. Use this whenever the user asks you to inspect or debug their browser console.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_global_file",
            "description": "Read the contents of a specific global skill or tool file inside the global core directory (~/.kognisant_core/tools/ or ~/.kognisant_core/skills/).",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "The path to the global file to read (e.g., '~/.kognisant_core/tools/my_tool.py' or '~/.kognisant_core/skills/my_skill.md').",
                    }
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_global_file",
            "description": "Create a brand new global file inside the global core skills or tools directories (~/.kognisant_core/tools/ or ~/.kognisant_core/skills/).",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "The path to the global file to create (e.g., '~/.kognisant_core/tools/my_tool.json' or '~/.kognisant_core/skills/my_skill.md').",
                    },
                    "content": {
                        "type": "string",
                        "description": "The initial text content for the new global file.",
                    },
                },
                "required": ["file_path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_global_file",
            "description": "Edit an existing global file inside the global core skills or tools directories (~/.kognisant_core/tools/ or ~/.kognisant_core/skills/). Provide a list of sequential find-and-replace edits.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "The path to the global file to edit (e.g., '~/.kognisant_core/tools/my_tool.py').",
                    },
                    "edits": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "old_text": {
                                    "type": "string",
                                    "description": "The exact block of old text to find.",
                                },
                                "new_text": {
                                    "type": "string",
                                    "description": "The new text block to replace it with.",
                                },
                            },
                            "required": ["old_text", "new_text"],
                        },
                        "description": "A list of sequential edits to perform on the file.",
                    },
                },
                "required": ["file_path", "edits"],
            },
        },
    },
    # --- Script Management Tools (R6) ---
    {
        "type": "function",
        "function": {
            "name": "create_script",
            "description": "Create a new Python script in the global scripts folder (~/.kognisant_core/scripts/) with accompanying metadata.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Script name (lowercase alphanumeric, hyphens, underscores, 1-64 chars).",
                    },
                    "content": {
                        "type": "string",
                        "description": "The Python script content to write.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Human-readable description of what the script does.",
                    },
                    "env_vars": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of required environment variable names for this script.",
                    },
                },
                "required": ["name", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_script",
            "description": "Read the content of a Python script from the global scripts folder (~/.kognisant_core/scripts/).",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The name of the script to read.",
                    }
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_script",
            "description": "Apply sequential find-and-replace edits to an existing script in the global scripts folder. All edits are rolled back if any old_text is not found.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The name of the script to edit.",
                    },
                    "edits": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "old_text": {
                                    "type": "string",
                                    "description": "The exact text to find in the script.",
                                },
                                "new_text": {
                                    "type": "string",
                                    "description": "The replacement text.",
                                },
                            },
                            "required": ["old_text", "new_text"],
                        },
                        "description": "List of sequential find-and-replace edits.",
                    },
                },
                "required": ["name", "edits"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_script",
            "description": "Delete a Python script and its metadata from the global scripts folder (~/.kognisant_core/scripts/).",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The name of the script to delete.",
                    }
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_scripts",
            "description": "List all scripts in the global scripts folder (~/.kognisant_core/scripts/) with their names, descriptions, and required environment variables.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    # --- Job Management Tools (R9) ---
    {
        "type": "function",
        "function": {
            "name": "schedule_job",
            "description": "Create a new job in the job queue. For 'scheduled' type, a valid cron expression is required. For 'persistent' or 'scheduled' types, the referenced script must exist in ~/.kognisant_core/scripts/.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Job name (lowercase alphanumeric, hyphens, underscores, 1-64 chars).",
                    },
                    "script_path": {
                        "type": "string",
                        "description": "Path to the script file (relative to ~/.kognisant_core/scripts/). Required for persistent and scheduled job types.",
                    },
                    "job_type": {
                        "type": "string",
                        "enum": ["scheduled", "persistent", "agent"],
                        "description": "The type of job: 'scheduled' (cron-based), 'persistent' (always-on), or 'agent' (one-shot AI task).",
                    },
                    "cron_expression": {
                        "type": "string",
                        "description": "A 5-field cron expression (e.g., '*/15 * * * *'). Required when job_type is 'scheduled'.",
                    },
                    "task": {
                        "type": "string",
                        "description": "Task description for agent-type jobs. Required when job_type is 'agent'.",
                    },
                    "env_vars": {
                        "type": "object",
                        "description": "Environment variables to pass to the job (key-value pairs).",
                    },
                },
                "required": ["name", "job_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_job",
            "description": "Cancel a job by updating its state to 'cancelled' and terminating its subprocess if running.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The name of the job to cancel.",
                    }
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_jobs",
            "description": "List all jobs in the job queue with their name, type, current state, and last execution timestamp.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "job_logs",
            "description": "Return the last N lines from a job's log file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The name of the job to get logs for.",
                    },
                    "lines": {
                        "type": "integer",
                        "description": "Number of log lines to return (default: 50).",
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_job",
            "description": "Permanently remove a job from the queue. If the job is currently running, its subprocess will be terminated first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The name of the job to remove.",
                    }
                },
                "required": ["name"],
            },
        },
    },
]


def clean_html(html_content):
    """Clean HTML content by removing scripts, styles, and returning readable text."""
    # Remove script, style, and head tags recursively
    html_content = re.sub(
        r"<(script|style|head)[^>]*>([\s\S]*?)</\1>",
        "",
        html_content,
        flags=re.IGNORECASE,
    )
    # Remove HTML comments
    html_content = re.sub(r"<!--([\s\S]*?)-->", "", html_content)
    # Replace block-level element tags with single newlines
    html_content = re.sub(
        r"</?(div|p|h[1-6]|li|ul|ol|tr|td)[^>]*>",
        "\n",
        html_content,
        flags=re.IGNORECASE,
    )
    # Strip remaining HTML tags
    text = re.sub(r"<[^>]+>", "", html_content)
    # Compress multi-newlines
    text = re.sub(r"\n\s*\n", "\n\n", text)
    return text.strip()


def find_chrome_or_brave():
    """Locates the Brave Browser or Google Chrome executable on the desktop system."""
    if sys.platform == "darwin":  # macOS
        paths = [
            "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        ]
        for path in paths:
            if os.path.exists(path):
                return path
    elif sys.platform == "win32":  # Windows
        paths = [
            r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
            r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]
        for path in paths:
            if os.path.exists(path):
                return path
    else:  # Linux / Unix
        for cmd in ["brave-browser", "brave", "google-chrome", "chrome"]:
            path = shutil.which(cmd)
            if path:
                return path

    return None


def browse_web_page(url):
    """Fetches a webpage, cleans it of HTML, and returns the readable text.
    Uses Brave/Chrome headless DOM dump if available to render JavaScript,
    with a graceful urllib fallback if no browser is installed.
    """
    browser_path = find_chrome_or_brave()
    html = None
    stderr_output = ""
    used_headless = False

    if browser_path:
        try:
            # Launch browser headlessly to execute client-side JS and dump fully rendered DOM
            # Enable logging (--enable-logging --v=1) to capture console messages on stderr
            cmd = [
                browser_path,
                "--headless=new",
                "--enable-logging",
                "--v=1",
                "--dump-dom",
                url,
            ]
            # 15.0 second timeout to prevent hangs
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15.0)
            if result.returncode == 0:
                html = result.stdout
                stderr_output = result.stderr
                used_headless = True
        except Exception:
            pass  # Fall back to urllib silently on any browser invocation failure

    if not html:
        # Graceful fallback to urllib
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                },
            )
            import ssl

            context = ssl._create_unverified_context()
            with urllib.request.urlopen(req, timeout=15.0, context=context) as response:
                if response.status == 200:
                    html = response.read().decode("utf-8", errors="ignore")
                else:
                    return (
                        f"[Error] Failed to fetch URL. HTTP Status: {response.status}"
                    )
        except Exception as e:
            return f"[Error] Failed to fetch webpage: {e}"

    cleaned_text = clean_html(html)
    prefix = (
        "[Headless Browser Mode] Rendered page using local desktop browser engine.\n\n"
        if used_headless
        else ""
    )

    # Extract console error logs from headless stderr
    console_logs = []
    if used_headless and stderr_output:
        for line in stderr_output.splitlines():
            line_lower = line.lower()
            if (
                "console" in line_lower
                or "error" in line_lower
                or "exception" in line_lower
            ):
                console_logs.append(line.strip())

    suffix = ""
    if console_logs:
        suffix = "\n\n--- Headless Browser Console Logs (Captured) ---\n" + "\n".join(
            console_logs[:20]
        )

    # Cap the output length to 12,000 characters to prevent context-overflow in LLM
    if len(cleaned_text) > 12000:
        return (
            prefix
            + cleaned_text[:12000]
            + "\n\n[Content truncated due to length...]"
            + suffix
        )
    return prefix + cleaned_text + suffix


def search_web(query):
    """Performs a headless background search on DuckDuckGo and returns the plain text results."""
    encoded_query = urllib.parse.quote_plus(query)
    # html.duckduckgo.com is a JS-free, lightweight HTML search endpoint
    url = f"https://html.duckduckgo.com/html/?q={encoded_query}"

    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            },
        )
        import ssl

        context = ssl._create_unverified_context()
        with urllib.request.urlopen(req, timeout=15.0, context=context) as response:
            if response.status == 200:
                html = response.read().decode("utf-8", errors="ignore")
                cleaned_text = clean_html(html)

                # Truncate search results to first 8000 characters
                if len(cleaned_text) > 8000:
                    return cleaned_text[:8000] + "\n\n[Search results truncated...]"
                return cleaned_text
            else:
                return f"[Error] Failed to search. HTTP Status: {response.status}"
    except Exception as e:
        return f"[Error] Failed to execute web search: {e}"


def open_in_native_browser(query_or_url):
    """Opens a URL or search query in the user's native default web browser."""
    target = query_or_url.strip()
    if not (target.startswith("http://") or target.startswith("https://")):
        # Treat as search query and format Google search URL
        encoded_query = urllib.parse.quote_plus(target)
        target = f"https://www.google.com/search?q={encoded_query}"

    try:
        # Open in default native browser
        webbrowser.open(target)
        return f"[Success] Opened '{target}' in the user's default native web browser."
    except Exception as e:
        return f"[Error] Failed to open native browser: {e}"


def capture_active_browser_console():
    """Locates and reads the native browser's active debug log file containing console.log and error streams."""
    paths = []
    if sys.platform == "darwin":  # macOS
        paths = [
            os.path.expanduser(
                "~/Library/Application Support/BraveSoftware/Brave-Browser/chrome_debug.log"
            ),
            os.path.expanduser(
                "~/Library/Application Support/Google/Chrome/chrome_debug.log"
            ),
        ]
    elif sys.platform == "win32":  # Windows
        paths = [
            os.path.expanduser(
                "~/AppData/Local/BraveSoftware/Brave-Browser/User Data/chrome_debug.log"
            ),
            os.path.expanduser(
                "~/AppData/Local/Google/Chrome/User Data/chrome_debug.log"
            ),
        ]
    else:  # Linux / Unix
        paths = [
            os.path.expanduser("~/.config/brave-browser/chrome_debug.log"),
            os.path.expanduser("~/.config/google-chrome/chrome_debug.log"),
        ]

    for path in paths:
        if os.path.exists(path):
            try:
                # Read the last 60 lines of the browser debug log to capture recent js errors
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()
                    recent_logs = lines[-60:]
                return (
                    f"[Success] Captured last {len(recent_logs)} lines from '{path}':\n"
                    + "\n".join([line.strip() for line in recent_logs])
                )
            except Exception as e:
                return (
                    f"[Error] Failed to read active browser debug log at '{path}': {e}"
                )

    return (
        "[Warning] No active browser debug log found.\n"
        "To enable automatic console logging on your device, start your Brave or Chrome browser with logging enabled:\n"
        "  Brave Browser: open -a 'Brave Browser' --args --enable-logging --v=1\n"
        "  Google Chrome: open -a 'Google Chrome' --args --enable-logging --v=1"
    )


def resolve_safe_path(file_path, project_root):
    """Safely resolves file_path, allowing paths in project_root or ~/.kognisant_core/tools, ~/.kognisant_core/skills, or ~/.kognisant_core/scripts.

    Returns the absolute real path if valid, otherwise raises a PermissionError with a descriptive message.
    """
    if file_path.startswith("~"):
        full_path = os.path.expanduser(file_path)
    elif os.path.isabs(file_path):
        full_path = file_path
    else:
        full_path = os.path.join(project_root, file_path)

    real_target = os.path.realpath(full_path)
    real_root = os.path.realpath(project_root)

    global_tools_dir = os.path.realpath(os.path.expanduser("~/.kognisant_core/tools"))
    global_skills_dir = os.path.realpath(os.path.expanduser("~/.kognisant_core/skills"))
    global_scripts_dir = os.path.realpath(os.path.expanduser("~/.kognisant_core/scripts"))

    is_in_project = real_target.startswith(real_root)
    is_in_global_tools = real_target.startswith(global_tools_dir)
    is_in_global_skills = real_target.startswith(global_skills_dir)
    is_in_global_scripts = real_target.startswith(global_scripts_dir)

    if not (is_in_project or is_in_global_tools or is_in_global_skills or is_in_global_scripts):
        raise PermissionError(
            "Access denied: Cannot access paths outside the project root or global tools/skills/scripts directories."
        )

    return real_target


def is_strictly_global_path(real_path):
    """Verifies that the absolute path resides strictly under ~/.kognisant_core/tools/, ~/.kognisant_core/skills/, or ~/.kognisant_core/scripts/."""
    global_tools_dir = os.path.realpath(os.path.expanduser("~/.kognisant_core/tools"))
    global_skills_dir = os.path.realpath(os.path.expanduser("~/.kognisant_core/skills"))
    global_scripts_dir = os.path.realpath(os.path.expanduser("~/.kognisant_core/scripts"))
    return (
        real_path.startswith(global_tools_dir)
        or real_path.startswith(global_skills_dir)
        or real_path.startswith(global_scripts_dir)
    )


def create_project_file(file_path, content, project_info):
    """Creates a brand new file inside the project workspace root or global tools/skills directories with boundary protection."""
    # Strict memory protection: block sub-agents from overwriting context files
    if (
        ".kognisant/context.md" in file_path
        or ".kognisant/memory-guidlines.md" in file_path
        or "spec.json" in file_path
    ):
        return "[Error] Access denied: Local memory assets (.kognisant/context.md, .kognisant/memory-guidlines.md, spec.json) are strictly read-only for subtask agents."

    try:
        full_path = resolve_safe_path(file_path, project_info["root"])
    except PermissionError as e:
        return f"[Error] {e}"

    if os.path.exists(full_path):
        return f"[Error] File '{file_path}' already exists. Use 'edit_project_file' to modify it instead."

    try:
        # Ensure parent directories exist
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
        from . import json_stream
        if json_stream.is_active():
            json_stream.emit_file_created(file_path, os.path.getsize(full_path))
        return f"[Success] Created brand new file '{file_path}' with {len(content)} characters."
    except Exception as e:
        return f"[Error] Failed to create file: {e}"


def create_project_directory(directory_path, project_info):
    """Creates a new directory structure inside the project workspace root or global tools/skills directories."""
    try:
        full_path = resolve_safe_path(directory_path, project_info["root"])
    except PermissionError as e:
        return f"[Error] {e}"

    if os.path.exists(full_path):
        return f"[Success] Directory '{directory_path}' already exists."

    try:
        os.makedirs(full_path, exist_ok=True)
        return f"[Success] Created directory '{directory_path}'."
    except Exception as e:
        return f"[Error] Failed to create directory: {e}"


def delete_project_path(target_path, project_info):
    """Deletes a file or directory recursively inside the project workspace root or global tools/skills directories."""
    # Block deleting local memories or the core .kognisant configuration
    if ".kognisant" in target_path or "spec.json" in target_path:
        return "[Error] Access denied: Local project memory configurations (.kognisant/) are immutable and cannot be deleted."

    try:
        full_path = resolve_safe_path(target_path, project_info["root"])
    except PermissionError as e:
        return f"[Error] {e}"

    if not os.path.exists(full_path):
        return f"[Error] Path '{target_path}' not found."

    try:
        if os.path.isdir(full_path):
            shutil.rmtree(full_path)
            from . import json_stream
            if json_stream.is_active():
                json_stream.emit_file_deleted(target_path)
            return f"[Success] Recursively deleted directory '{target_path}'."
        else:
            os.remove(full_path)
            from . import json_stream
            if json_stream.is_active():
                json_stream.emit_file_deleted(target_path)
            return f"[Success] Deleted file '{target_path}'."
    except Exception as e:
        return f"[Error] Failed to delete path: {e}"


def load_global_tools():
    """Crawls and compiles all globally transferable JSON tool schemas from ~/.kognisant_core/tools/."""
    global_dir = os.path.expanduser("~/.kognisant_core/tools")
    global_specs = []
    if os.path.exists(global_dir):
        try:
            for file in os.listdir(global_dir):
                if file.endswith(".json"):
                    path = os.path.join(global_dir, file)
                    with open(path, "r", encoding="utf-8") as f:
                        spec = json.load(f)
                        global_specs.append(spec)
        except Exception:
            pass
    return global_specs


def get_active_tools():
    """Merges native Kognisant tools with dynamically loaded global transferable tools on launch."""
    return TOOLS_SPEC + load_global_tools()


def execute_tool(name, arguments, project_info):
    """Executes a local workspace, browser, system web-launcher, or search tool requested by the LLM."""
    try:
        args = json.loads(arguments) if isinstance(arguments, str) else arguments
    except Exception as e:
        return f"[Error] Failed to parse tool arguments: {e}"

    if name == "read_project_file":
        if not project_info:
            return "[Error] This tool is only available inside an active Kognisant workspace. Run 'kognisant init' first."
        file_path = args.get("file_path", "").strip()
        if not file_path:
            return "[Error] file_path is required."

        try:
            full_path = resolve_safe_path(file_path, project_info["root"])
        except PermissionError as e:
            return f"[Error] {e}"

        if not os.path.exists(full_path) or not os.path.isfile(full_path):
            return f"[Error] File '{file_path}' not found."

        try:
            with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            return content
        except Exception as e:
            return f"[Error] Failed to read file: {e}"

    elif name == "edit_project_file":
        if not project_info:
            return "[Error] This tool is only available inside an active Kognisant workspace. Run 'kognisant init' first."
        file_path = args.get("file_path", "").strip()
        edits = args.get("edits", [])

        if not file_path:
            return "[Error] file_path is required."
        if not edits:
            return "[Error] edits array is required."

        # Strict memory steering boundary check: prevent parallel sub-agents from overwriting context files
        if (
            ".kognisant/context.md" in file_path
            or ".kognisant/memory-guidlines.md" in file_path
            or "spec.json" in file_path
        ):
            return "[Error] Access denied: Local project memory assets (.kognisant/context.md, .kognisant/memory-guidlines.md, spec.json) are strictly read-only for subtask agents. Only the Reflection/Persistence Stage on the orchestrator's main thread may write to them."

        try:
            full_path = resolve_safe_path(file_path, project_info["root"])
        except PermissionError as e:
            return f"[Error] {e}"

        if not os.path.exists(full_path) or not os.path.isfile(full_path):
            return f"[Error] File '{file_path}' not found."

        try:
            with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            for edit in edits:
                old_text = edit.get("old_text", "")
                new_text = edit.get("new_text", "")

                if old_text not in content:
                    return f"[Error] Could not find old_text block precisely inside '{file_path}'. Ensure formatting is exact."

                content = content.replace(old_text, new_text, 1)

            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)

            from . import json_stream
            if json_stream.is_active():
                json_stream.emit_file_modified(file_path, len(edits))

            return f"[Success] Sequentially applied {len(edits)} find-and-replace edits inside '{file_path}'."
        except Exception as e:
            return f"[Error] Failed to edit file: {e}"

    elif name == "list_project_files":
        if not project_info:
            return "[Error] This tool is only available inside an active Kognisant workspace. Run 'kognisant init' first."
        return json.dumps(project_info["files"])

    elif name == "create_project_file":
        content = args.get("content", "")
        file_path = args.get("file_path", "").strip()
        return create_project_file(file_path, content, project_info)

    elif name == "create_project_directory":
        directory_path = args.get("directory_path", "").strip()
        return create_project_directory(directory_path, project_info)

    elif name == "delete_project_path":
        target_path = args.get("path", "").strip()
        return delete_project_path(target_path, project_info)

    elif name == "browse_web_page":
        url = args.get("url", "").strip()
        if not url:
            return "[Error] url is required."
        return browse_web_page(url)

    elif name == "open_in_native_browser":
        query_or_url = args.get("query_or_url", "").strip()
        if not query_or_url:
            return "[Error] query_or_url is required."
        return open_in_native_browser(query_or_url)

    elif name == "search_web":
        query = args.get("query", "").strip()
        if not query:
            return "[Error] query is required."
        return search_web(query)

    elif name == "capture_active_browser_console":
        return capture_active_browser_console()

    elif name == "read_global_file":
        file_path = args.get("file_path", "").strip()
        if not file_path:
            return "[Error] file_path is required."

        try:
            full_path = resolve_safe_path(
                file_path, project_info["root"] if project_info else ""
            )
            if not is_strictly_global_path(full_path):
                return "[Error] Access denied: This tool can only read files inside global tools and skills folders (~/.kognisant_core/tools/ or ~/.kognisant_core/skills/)."
        except PermissionError as e:
            return f"[Error] {e}"

        if not os.path.exists(full_path) or not os.path.isfile(full_path):
            return f"[Error] File '{file_path}' not found."

        try:
            with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            return content
        except Exception as e:
            return f"[Error] Failed to read file: {e}"

    elif name == "create_global_file":
        file_path = args.get("file_path", "").strip()
        content = args.get("content", "")
        if not file_path:
            return "[Error] file_path is required."

        try:
            full_path = resolve_safe_path(
                file_path, project_info["root"] if project_info else ""
            )
            if not is_strictly_global_path(full_path):
                return "[Error] Access denied: This tool can only create files inside global tools and skills folders (~/.kognisant_core/tools/ or ~/.kognisant_core/skills/)."
        except PermissionError as e:
            return f"[Error] {e}"

        if os.path.exists(full_path):
            return f"[Error] File '{file_path}' already exists. Use 'edit_global_file' to modify it."

        try:
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)
            return f"[Success] Created brand new global file '{file_path}'."
        except Exception as e:
            return f"[Error] Failed to create global file: {e}"

    elif name == "edit_global_file":
        file_path = args.get("file_path", "").strip()
        edits = args.get("edits", [])
        if not file_path:
            return "[Error] file_path is required."
        if not edits:
            return "[Error] edits array is required."

        try:
            full_path = resolve_safe_path(
                file_path, project_info["root"] if project_info else ""
            )
            if not is_strictly_global_path(full_path):
                return "[Error] Access denied: This tool can only edit files inside global tools and skills folders (~/.kognisant_core/tools/ or ~/.kognisant_core/skills/)."
        except PermissionError as e:
            return f"[Error] {e}"

        if not os.path.exists(full_path) or not os.path.isfile(full_path):
            return f"[Error] File '{file_path}' not found."

        try:
            with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            for edit in edits:
                old_text = edit.get("old_text", "")
                new_text = edit.get("new_text", "")

                if old_text not in content:
                    return f"[Error] Could not find old_text block precisely inside '{file_path}'."

                content = content.replace(old_text, new_text, 1)

            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)

            return f"[Success] Sequentially applied {len(edits)} find-and-replace edits inside global file '{file_path}'."
        except Exception as e:
            return f"[Error] Failed to edit global file: {e}"

    # --- Script Management Tool Handlers (R6) ---

    elif name == "create_script":
        script_name = args.get("name", "").strip()
        content = args.get("content", "")
        description = args.get("description", "")
        env_vars = args.get("env_vars", None)
        if not script_name:
            return "[Error] name is required."
        if not content:
            return "[Error] content is required."
        return create_script(script_name, content, description, env_vars)

    elif name == "read_script":
        script_name = args.get("name", "").strip()
        if not script_name:
            return "[Error] name is required."
        return read_script(script_name)

    elif name == "edit_script":
        script_name = args.get("name", "").strip()
        edits = args.get("edits", [])
        if not script_name:
            return "[Error] name is required."
        if not edits:
            return "[Error] edits array is required."
        return edit_script(script_name, edits)

    elif name == "delete_script":
        script_name = args.get("name", "").strip()
        if not script_name:
            return "[Error] name is required."
        return delete_script(script_name)

    elif name == "list_scripts":
        return list_scripts()

    # --- Job Management Tool Handlers (R9) ---

    elif name == "schedule_job":
        job_name = args.get("name", "").strip()
        job_type = args.get("job_type", "").strip()
        script_path = args.get("script_path", "").strip()
        cron_expression = args.get("cron_expression", "").strip() if args.get("cron_expression") else None
        task = args.get("task", "").strip() if args.get("task") else None
        env_vars = args.get("env_vars", {})

        if not job_name:
            return format_error("validation", "name is required")
        if not job_type:
            return format_error("validation", "job_type is required")

        # R9-AC5: scheduled type requires cron_expression
        if job_type == "scheduled" and not cron_expression:
            return format_error("validation", "cron_expression is required for scheduled job type")

        # Validate cron expression syntax if provided
        if cron_expression and not CronParser.validate(cron_expression):
            return format_error("validation", f"Invalid cron expression: '{cron_expression}'")

        # Unmatchable cron warning (Requirement 34)
        if cron_expression and CronParser.validate(cron_expression):
            if not CronParser.can_match_within_days(cron_expression):
                return format_error(
                    "validation",
                    f"Cron expression '{cron_expression}' may never produce a match within 366 days",
                    "Verify the expression is correct before scheduling."
                )

        # R9-AC6: persistent/scheduled types require script to exist
        if job_type in ("persistent", "scheduled"):
            if not script_path:
                return format_error("validation", "script_path is required for persistent and scheduled job types")
            scripts_dir = os.path.expanduser("~/.kognisant_core/scripts")
            # Handle both bare name and name with .py extension
            check_path = script_path if script_path.endswith(".py") else f"{script_path}.py"
            full_script_path = os.path.join(scripts_dir, check_path)
            if not os.path.exists(full_script_path):
                return format_error(
                    "not_found",
                    f"Script '{script_path}' not found in ~/.kognisant_core/scripts/"
                )

        # Agent type requires a task description
        if job_type == "agent" and not task:
            return format_error("validation", "task is required for agent job type")

        # Build job config and delegate to JobQueue
        job_queue = JobQueue()
        job_config = {
            "name": job_name,
            "type": job_type,
            "script_path": script_path,
            "cron_expression": cron_expression,
            "task": task,
            "env_vars": env_vars,
        }

        try:
            result = job_queue.add_job(job_config)
            return result
        except ValueError as e:
            return format_error("validation", str(e))

    elif name == "cancel_job":
        job_name = args.get("name", "").strip()
        if not job_name:
            return format_error("validation", "name is required")

        job_queue = JobQueue()
        job = job_queue.get_job(job_name)

        # R9-AC8: error if job not found
        if job is None:
            return format_error(
                "not_found",
                f"Job '{job_name}' does not exist",
                "Use 'kognisant job list' to see available jobs."
            )

        # Cancel state validation (Requirement 31)
        current_state = job.get("state", "")
        if current_state in TERMINAL_STATES:
            return format_error(
                "state",
                f"Job '{job_name}' is in '{current_state}' state and cannot be cancelled"
            )

        if current_state not in CANCELLABLE_STATES:
            return format_error(
                "state",
                f"Job '{job_name}' is in '{current_state}' state and cannot be cancelled"
            )

        # Terminate subprocess if running
        pid = job.get("pid")
        if pid and current_state == "running":
            try:
                os.kill(pid, signal.SIGTERM)
            except (OSError, ProcessLookupError):
                pass  # Process already gone

        # Update state to cancelled
        job_queue.update_status(job_name, "cancelled", pid=None)
        return f"Job '{job_name}' cancelled successfully"

    elif name == "list_jobs":
        job_queue = JobQueue()
        jobs = job_queue.load()

        if not jobs:
            return "No jobs found"

        lines = []
        for job in jobs:
            entry = f"  {job.get('name', '?')} [{job.get('type', '?')}] - {job.get('state', '?')}"
            last_run = job.get("last_run_at")
            if last_run:
                entry += f" (last run: {last_run})"
            lines.append(entry)

        return "Jobs:\n" + "\n".join(lines)

    elif name == "job_logs":
        job_name = args.get("name", "").strip()
        lines_count = args.get("lines", 50)
        if not job_name:
            return format_error("validation", "name is required")

        job_queue = JobQueue()
        job = job_queue.get_job(job_name)

        # R9-AC8: error if job not found
        if job is None:
            return format_error(
                "not_found",
                f"Job '{job_name}' does not exist",
                "Use 'kognisant job list' to see available jobs."
            )

        return job_queue.read_job_logs(job_name, lines=lines_count)

    elif name == "remove_job":
        job_name = args.get("name", "").strip()
        if not job_name:
            return format_error("validation", "name is required")

        job_queue = JobQueue()
        job = job_queue.get_job(job_name)

        if job is None:
            return format_error(
                "not_found",
                f"Job '{job_name}' does not exist",
                "Use 'list_jobs' to see available jobs."
            )

        # If running, terminate subprocess first
        pid = job.get("pid")
        if pid and job.get("state") == "running":
            try:
                os.kill(pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass

        success = job_queue.remove_job(job_name)
        if success:
            return f"Job '{job_name}' removed successfully"
        else:
            return format_error("io", f"Failed to remove job '{job_name}'")

    # Dynamic Global Transferable Tool Execution (Subprocess Sandbox)
    global_dir = os.path.expanduser("~/.kognisant_core/tools")
    tool_script = os.path.join(global_dir, f"{name}.py")

    if os.path.exists(tool_script):
        try:
            # Safely invoke the external Python tool script in an isolated background process, passing JSON arguments
            cmd = [sys.executable, tool_script, json.dumps(arguments)]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30.0)
            if result.returncode == 0:
                return result.stdout.strip()
            else:
                return f"[Error] Global tool '{name}' failed: {result.stderr.strip()}"
        except Exception as e:
            return f"[Error] Failed to execute global tool '{name}': {e}"

    return f"[Error] Tool '{name}' not found."
