import json
import os
import sys
import time

from .colors import Colors

GLOBAL_CORE_DIR = os.path.expanduser("~/.kognisant_core")

# ───────────────────────────────────────────────────────────
# Auth & Security Configuration
# ───────────────────────────────────────────────────────────

# Default token lifetimes (seconds)
DEFAULT_ACCESS_TOKEN_EXPIRY = 900  # 15 minutes
DEFAULT_REFRESH_TOKEN_EXPIRY = 604800  # 7 days
DEFAULT_SESSION_TOKEN_EXPIRY = 3600  # 1 hour
DEFAULT_API_KEY_EXPIRY = 2592000  # 30 days (for long-lived API keys)

# Secret key lengths
ACCESS_SECRET_KEY_LENGTH = 32
REFRESH_SECRET_KEY_LENGTH = 64
API_SECRET_KEY_LENGTH = 48

# Files for storing auth-related data
AUTH_CONFIG_FILE = os.path.join(GLOBAL_CORE_DIR, "auth_config.json")
AUTH_TOKENS_FILE = os.path.join(GLOBAL_CORE_DIR, "tokens.json")


class AuthConfig:
    """In-memory representation of global auth/secrets configuration."""

    __slots__ = (
        "access_token_expiry",
        "refresh_token_expiry",
        "session_token_expiry",
        "api_key_expiry",
        "access_secret_key",
        "refresh_secret_key",
        "api_secret_key",
    )

    def __init__(
        self,
        access_token_expiry=DEFAULT_ACCESS_TOKEN_EXPIRY,
        refresh_token_expiry=DEFAULT_REFRESH_TOKEN_EXPIRY,
        session_token_expiry=DEFAULT_SESSION_TOKEN_EXPIRY,
        api_key_expiry=DEFAULT_API_KEY_EXPIRY,
        access_secret_key=None,
        refresh_secret_key=None,
        api_secret_key=None,
    ):
        self.access_token_expiry = access_token_expiry
        self.refresh_token_expiry = refresh_token_expiry
        self.session_token_expiry = session_token_expiry
        self.api_key_expiry = api_key_expiry
        self.access_secret_key = access_secret_key or _generate_secret_key(
            ACCESS_SECRET_KEY_LENGTH
        )
        self.refresh_secret_key = refresh_secret_key or _generate_secret_key(
            REFRESH_SECRET_KEY_LENGTH
        )
        self.api_secret_key = api_secret_key or _generate_secret_key(
            API_SECRET_KEY_LENGTH
        )

    def to_dict(self):
        return {
            "access_token_expiry_seconds": self.access_token_expiry,
            "refresh_token_expiry_seconds": self.refresh_token_expiry,
            "session_token_expiry_seconds": self.session_token_expiry,
            "api_key_expiry_seconds": self.api_key_expiry,
            "access_secret_key": self.access_secret_key,
            "refresh_secret_key": self.refresh_secret_key,
            "api_secret_key": self.api_secret_key,
        }

    @classmethod
    def from_dict(cls, d: dict):
        return cls(
            access_token_expiry=d.get(
                "access_token_expiry_seconds", DEFAULT_ACCESS_TOKEN_EXPIRY
            ),
            refresh_token_expiry=d.get(
                "refresh_token_expiry_seconds", DEFAULT_REFRESH_TOKEN_EXPIRY
            ),
            session_token_expiry=d.get(
                "session_token_expiry_seconds", DEFAULT_SESSION_TOKEN_EXPIRY
            ),
            api_key_expiry=d.get("api_key_expiry_seconds", DEFAULT_API_KEY_EXPIRY),
            access_secret_key=d.get("access_secret_key"),
            refresh_secret_key=d.get("refresh_secret_key"),
            api_secret_key=d.get("api_secret_key"),
        )


def _generate_secret_key(length: int = ACCESS_SECRET_KEY_LENGTH) -> str:
    """Generate a cryptographically secure random secret key of specified length."""
    import secrets
    import string

    alphabet = string.ascii_letters + string.digits + "-_"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def load_auth_config() -> AuthConfig:
    """Load auth configuration from disk, generating defaults if absent."""
    if not os.path.exists(AUTH_CONFIG_FILE):
        config = AuthConfig()
        save_auth_config(config)
        return config

    try:
        with open(AUTH_CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return AuthConfig.from_dict(data)
    except Exception as e:
        print(
            f"{Colors.YELLOW}[Warning] Failed to load auth config: {e}. "
            f"Regenerating defaults.{Colors.RESET}",
            file=sys.stderr,
        )
        config = AuthConfig()
        save_auth_config(config)
        return config


def save_auth_config(config: AuthConfig) -> bool:
    """Persist auth configuration to disk."""
    init_global_core()
    try:
        with open(AUTH_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config.to_dict(), f, indent=2)
        return True
    except Exception as e:
        print(
            f"{Colors.RED}[Error] Failed to save auth config: {e}{Colors.RESET}",
            file=sys.stderr,
        )
        return False


# ───────────────────────────────────────────────────────────
# Spec-Driven Development (SDD) Templates & Data Structures
# ───────────────────────────────────────────────────────────

SPEC_TEMPLATE_REQUIREMENTS = """# Feature Requirements: {feature_name}

## Overview
Describe what this feature does and why it is needed.

## Functional Requirements
- [ ] Core behavior requirement 1
- [ ] Core behavior requirement 2
- [ ] Error handling and edge cases

## Non-Functional Requirements
- [ ] Performance constraints
- [ ] Security considerations
- [ ] Compatibility requirements

## Success Criteria
How do we know this feature is complete?
"""

SPEC_TEMPLATE_DESIGN = """# Design Document: {feature_name}

## Architecture
Describe the high-level architecture and component interactions.

## Data Structures
Define the core data structures, schemas, or object models required.

## Behavior & Logic
Describe the algorithms, state machines, or workflows.

## Interface Contract
Document public APIs, CLI arguments, or message formats introduced.

## Testing Strategy
Outline how the feature will be validated.
"""

SPEC_TEMPLATE_TASKS = """# Implementation Tasks: {feature_name}

## Phase 1 — Discovery & Scaffolding
- [ ] Identify implementation files
- [ ] Add core data structures

## Phase 2 — Core Logic
- [ ] Implement primary behavior
- [ ] Add error handling

## Phase 3 — Integration & Polish
- [ ] Wire into CLI or chat slash commands
- [ ] Update documentation and memory
"""


class FeatureSpec:
    """Data structure representing a Spec-Driven Development feature package."""

    __slots__ = ("feature_name", "requirements", "design", "tasks", "root")

    def __init__(self, feature_name, requirements="", design="", tasks="", root=""):
        self.feature_name = feature_name
        self.requirements = requirements
        self.design = design
        self.tasks = tasks
        self.root = root

    def to_dict(self):
        return {
            "feature": self.feature_name,
            "requirements": self.requirements,
            "design": self.design,
            "tasks": self.tasks,
            "root": self.root,
        }

    def __repr__(self):
        return f"<FeatureSpec '{self.feature_name}' root={self.root}>"


class User:
    """Data model representing a user with authentication-specific fields."""

    __slots__ = (
        "username",
        "email",
        "password_hash",
        "email_verified",
        "verification_token",
        "reset_token",
        "reset_token_expires_at",
        "auth_token",
        "auth_token_expires_at",
    )

    def __init__(
        self,
        username="",
        email="",
        password_hash="",
        email_verified=False,
        verification_token="",
        reset_token="",
        reset_token_expires_at=None,
        auth_token="",
        auth_token_expires_at=None,
    ):
        self.username = username
        self.email = email
        self.password_hash = password_hash
        self.email_verified = email_verified
        self.verification_token = verification_token
        self.reset_token = reset_token
        self.reset_token_expires_at = reset_token_expires_at
        self.auth_token = auth_token
        self.auth_token_expires_at = auth_token_expires_at

    def to_dict(self):
        return {
            "username": self.username,
            "email": self.email,
            "password_hash": self.password_hash,
            "email_verified": self.email_verified,
            "verification_token": self.verification_token,
            "reset_token": self.reset_token,
            "reset_token_expires_at": self.reset_token_expires_at,
            "auth_token": self.auth_token,
            "auth_token_expires_at": self.auth_token_expires_at,
        }

    def __repr__(self):
        return f"<User '{self.username}' email_verified={self.email_verified}>"


def init_feature_spec(project_root, feature_name):
    """Initializes a Spec-Driven Development directory for a new feature."""
    spec_dir = os.path.join(project_root, ".kognisant", "specs", feature_name)
    if os.path.exists(spec_dir):
        print(
            f"{Colors.YELLOW}[!] Spec '{feature_name}' already exists at {spec_dir}{Colors.RESET}"
        )
        return False

    try:
        os.makedirs(spec_dir, exist_ok=True)

        files = {
            "requirements.md": SPEC_TEMPLATE_REQUIREMENTS.format(
                feature_name=feature_name
            ),
            "design.md": SPEC_TEMPLATE_DESIGN.format(feature_name=feature_name),
            "tasks.md": SPEC_TEMPLATE_TASKS.format(feature_name=feature_name),
        }

        for filename, content in files.items():
            filepath = os.path.join(spec_dir, filename)
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)

        print(
            f"{Colors.GREEN}[+] Initialized feature spec '{feature_name}' in: {spec_dir}{Colors.RESET}"
        )
        print("    Created requirements.md, design.md, and tasks.md templates.")
        return True
    except Exception as e:
        print(
            f"{Colors.RED}[Error] Failed to initialize feature spec: {e}{Colors.RESET}"
        )
        return False


def list_feature_specs(project_root):
    """Lists all feature specs under .kognisant/specs/."""
    specs_dir = os.path.join(project_root, ".kognisant", "specs")
    if not os.path.exists(specs_dir):
        return []
    try:
        return sorted(
            [
                name
                for name in os.listdir(specs_dir)
                if os.path.isdir(os.path.join(specs_dir, name))
            ]
        )
    except Exception:
        return []


def init_global_core():
    """Initializes the global ~/.kognisant_core registry, skills, tools, and the rich nested models pool."""
    try:
        os.makedirs(GLOBAL_CORE_DIR, exist_ok=True)
        os.makedirs(os.path.join(GLOBAL_CORE_DIR, "skills"), exist_ok=True)
        os.makedirs(os.path.join(GLOBAL_CORE_DIR, "tools"), exist_ok=True)
        os.makedirs(os.path.join(GLOBAL_CORE_DIR, "scripts"), exist_ok=True)
        os.makedirs(os.path.join(GLOBAL_CORE_DIR, "logs"), exist_ok=True)

        projects_file = os.path.join(GLOBAL_CORE_DIR, "projects.json")
        if not os.path.exists(projects_file):
            with open(projects_file, "w", encoding="utf-8") as f:
                json.dump({"projects": {}}, f, indent=2)

        # Create nested models_pool.json default configuration matching user's rich schema
        pool_path = os.path.join(GLOBAL_CORE_DIR, "models_pool.json")
        if not os.path.exists(pool_path):
            default_pool = {
                "selected_models": [
                    {
                        "provider": "Ollama (Local)",
                        "api_key": "",
                        "models": [
                            {
                                "vendor": "Google",
                                "name": "gemma3:1b",
                                "model_id": "gemma3:1b",
                                "api_base_url": "http://localhost:11434/v1",
                                "context_window": 131072,
                                "modality": "text-to-text",
                                "capabilities": {
                                    "tool_calling": True,
                                    "reasoning": True,
                                },
                            }
                        ],
                    },
                    {
                        "provider": "OpenAI",
                        "api_key": "your-openai-api-key-here",
                        "models": [
                            {
                                "vendor": "OpenAI",
                                "name": "gpt-4o-mini",
                                "model_id": "gpt-4o-mini",
                                "api_base_url": "https://api.openai.com/v1",
                                "pricing": {
                                    "input_per_1m_tokens_usd": 0.15,
                                    "output_per_1m_tokens_usd": 0.60,
                                },
                                "context_window": 128000,
                                "modality": "text-to-text",
                                "capabilities": {
                                    "tool_calling": True,
                                    "reasoning": False,
                                },
                            }
                        ],
                    },
                    {
                        "provider": "DeepSeek",
                        "api_key": "your-deepseek-api-key-here",
                        "models": [
                            {
                                "vendor": "DeepSeek",
                                "name": "deepseek-chat",
                                "model_id": "deepseek-chat",
                                "api_base_url": "https://api.deepseek.com/v1",
                                "pricing": {
                                    "input_per_1m_tokens_usd": 0.14,
                                    "output_per_1m_tokens_usd": 0.28,
                                },
                                "context_window": 64000,
                                "modality": "text-to-text",
                                "capabilities": {
                                    "tool_calling": True,
                                    "reasoning": True,
                                },
                            }
                        ],
                    },
                    {
                        "provider": "Groq",
                        "api_key": "your-groq-api-key-here",
                        "models": [
                            {
                                "vendor": "Groq",
                                "name": "llama-3.3-70b-versatile",
                                "model_id": "llama-3.3-70b-versatile",
                                "api_base_url": "https://api.groq.com/openai/v1",
                                "pricing": {
                                    "input_per_1m_tokens_usd": 0.59,
                                    "output_per_1m_tokens_usd": 0.79,
                                },
                                "context_window": 128000,
                                "modality": "text-to-text",
                                "capabilities": {
                                    "tool_calling": True,
                                    "reasoning": True,
                                },
                            }
                        ],
                    },
                    {
                        "provider": "Llama.cpp (Local)",
                        "api_key": "",
                        "models": [
                            {
                                "vendor": "Local",
                                "name": "llama-3-8b",
                                "model_id": "llama-3-8b",
                                "protocol": "llama_cpp",
                                "api_base_url": "http://localhost:8080",
                                "context_window": 8192,
                                "modality": "text-to-text",
                                "capabilities": {
                                    "tool_calling": False,
                                    "reasoning": True,
                                },
                            }
                        ],
                    },
                ]
            }
            with open(pool_path, "w", encoding="utf-8") as f:
                json.dump(default_pool, f, indent=2)

        # 1. Scaffold globally transferable Web & Browser tools autonomously on boot
        tools_dir = os.path.join(GLOBAL_CORE_DIR, "tools")

        # A. search_web
        sw_json = os.path.join(tools_dir, "search_web.json")
        if not os.path.exists(sw_json):
            schema = {
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
            }
            with open(sw_json, "w", encoding="utf-8") as f:
                json.dump(schema, f, indent=2)

        sw_py = os.path.join(tools_dir, "search_web.py")
        if not os.path.exists(sw_py):
            code = (
                "import sys\n"
                "import json\n"
                "import re\n"
                "import ssl\n"
                "import urllib.parse\n"
                "import urllib.request\n\n"
                "def clean_html(html_content):\n"
                "    html_content = re.sub(r'<(script|style|head)[^>]*>([\\s\\S]*?)</\\1>', '', html_content, flags=re.IGNORECASE)\n"
                "    html_content = re.sub(r'<!--([\\s\\S]*?)-->', '', html_content)\n"
                "    html_content = re.sub(r'</?(div|p|h[1-6]|li|ul|ol|tr|td)[^>]*>', '\\n', html_content, flags=re.IGNORECASE)\n"
                "    text = re.sub(r'<[^>]+>', '', html_content)\n"
                "    text = re.sub(r'\\n\\s*\\n', '\\n\\n', text)\n"
                "    return text.strip()\n\n"
                "def main():\n"
                "    try:\n"
                "        args = json.loads(sys.argv[1])\n"
                "        query = args.get('query', '').strip()\n"
                "    except Exception:\n"
                "        print('[Error] Failed to parse arguments.')\n"
                "        sys.exit(1)\n\n"
                "    encoded_query = urllib.parse.quote_plus(query)\n"
                "    url = f'https://html.duckduckgo.com/html/?q={encoded_query}'\n"
                "    try:\n"
                "        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})\n"
                "        context = ssl._create_unverified_context()\n"
                "        with urllib.request.urlopen(req, timeout=15.0, context=context) as resp:\n"
                "            if resp.status == 200:\n"
                "                cleaned = clean_html(resp.read().decode('utf-8', errors='ignore'))\n"
                "                print(cleaned[:8000])\n"
                "            else:\n"
                "                print(f'[Error] Search failed with HTTP {resp.status}')\n"
                "    except Exception as e:\n"
                "        print(f'[Error] Search failed: {e}')\n\n"
                "if __name__ == '__main__':\n"
                "    main()\n"
            )
            with open(sw_py, "w", encoding="utf-8") as f:
                f.write(code)

        # B. browse_web_page
        bwp_json = os.path.join(tools_dir, "browse_web_page.json")
        if not os.path.exists(bwp_json):
            schema = {
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
            }
            with open(bwp_json, "w", encoding="utf-8") as f:
                json.dump(schema, f, indent=2)

        bwp_py = os.path.join(tools_dir, "browse_web_page.py")
        if not os.path.exists(bwp_py):
            code = (
                "import sys\n"
                "import os\n"
                "import json\n"
                "import re\n"
                "import ssl\n"
                "import shutil\n"
                "import subprocess\n"
                "import urllib.request\n\n"
                "def clean_html(html_content):\n"
                "    html_content = re.sub(r'<(script|style|head)[^>]*>([\\s\\S]*?)</\\1>', '', html_content, flags=re.IGNORECASE)\n"
                "    html_content = re.sub(r'<!--([\\s\\S]*?)-->', '', html_content)\n"
                "    html_content = re.sub(r'</?(div|p|h[1-6]|li|ul|ol|tr|td)[^>]*>', '\\n', html_content, flags=re.IGNORECASE)\n"
                "    text = re.sub(r'<[^>]+>', '', html_content)\n"
                "    text = re.sub(r'\\n\\s*\\n', '\\n\\n', text)\n"
                "    return text.strip()\n\n"
                "def find_chrome_or_brave():\n"
                "    if sys.platform == 'darwin':\n"
                "        paths = ['/Applications/Brave Browser.app/Contents/MacOS/Brave Browser', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome']\n"
                "        for p in paths:\n"
                "            if os.path.exists(p): return p\n"
                "    return None\n\n"
                "def main():\n"
                "    try:\n"
                "        args = json.loads(sys.argv[1])\n"
                "        url = args.get('url', '').strip()\n"
                "    except Exception:\n"
                "        print('[Error] Failed to parse arguments.')\n"
                "        sys.exit(1)\n\n"
                "    browser_path = find_chrome_or_brave()\n"
                "    html = None\n"
                "    stderr_output = ''\n"
                "    used_headless = False\n"
                "    if browser_path:\n"
                "        try:\n"
                "            cmd = [browser_path, '--headless=new', '--enable-logging', '--v=1', '--dump-dom', url]\n"
                "            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15.0)\n"
                "            if result.returncode == 0:\n"
                "                html = result.stdout\n"
                "                stderr_output = result.stderr\n"
                "                used_headless = True\n"
                "        except Exception:\n"
                "            pass\n\n"
                "    if not html:\n"
                "        try:\n"
                "            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})\n"
                "            context = ssl._create_unverified_context()\n"
                "            with urllib.request.urlopen(req, timeout=15.0, context=context) as resp:\n"
                "                if resp.status == 200:\n"
                "                    html = resp.read().decode('utf-8', errors='ignore')\n"
                "        except Exception as e:\n"
                "            print(f'[Error] Failed to fetch: {e}')\n"
                "            sys.exit(1)\n\n"
                "    cleaned = clean_html(html)\n"
                "    prefix = '[Headless Browser Mode] Rendered page dynamically.\\n\\n' if used_headless else ''\n"
                "    console_logs = []\n"
                "    if used_headless and stderr_output:\n"
                "        for line in stderr_output.splitlines():\n"
                "            if any(k in line.lower() for k in ['console', 'error', 'exception']):\n"
                "                console_logs.append(line.strip())\n"
                "    suffix = '\\n\\n--- Headless Browser Console Logs ---\\n' + '\\n'.join(console_logs[:20]) if console_logs else ''\n"
                "    print(prefix + cleaned[:12000] + suffix)\n\n"
                "if __name__ == '__main__':\n"
                "    main()\n"
            )
            with open(bwp_py, "w", encoding="utf-8") as f:
                f.write(code)

        # C. open_in_native_browser
        onb_json = os.path.join(tools_dir, "open_in_native_browser.json")
        if not os.path.exists(onb_json):
            schema = {
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
            }
            with open(onb_json, "w", encoding="utf-8") as f:
                json.dump(schema, f, indent=2)

        onb_py = os.path.join(tools_dir, "open_in_native_browser.py")
        if not os.path.exists(onb_py):
            code = (
                "import sys\n"
                "import json\n"
                "import urllib.parse\n"
                "import webbrowser\n\n"
                "def main():\n"
                "    try:\n"
                "        args = json.loads(sys.argv[1])\n"
                "        query_or_url = args.get('query_or_url', '').strip()\n"
                "    except Exception:\n"
                "        print('[Error] Failed to parse arguments.')\n"
                "        sys.exit(1)\n\n"
                "    target = query_or_url\n"
                "    if not (target.startswith('http://') or target.startswith('https://')):\n"
                "        encoded = urllib.parse.quote_plus(target)\n"
                "        target = f'https://www.google.com/search?q={encoded}'\n"
                "    try:\n"
                "        webbrowser.open(target)\n"
                "        print(f'[Success] Opened {target} in native browser.')\n"
                "    except Exception as e:\n"
                "        print(f'[Error] Failed to open: {e}')\n\n"
                "if __name__ == '__main__':\n"
                "    main()\n"
            )
            with open(onb_py, "w", encoding="utf-8") as f:
                f.write(code)

        # D. capture_active_browser_console
        cbc_json = os.path.join(tools_dir, "capture_active_browser_console.json")
        if not os.path.exists(cbc_json):
            schema = {
                "type": "function",
                "function": {
                    "name": "capture_active_browser_console",
                    "description": "Capture the active browser developer console logs (console.log, console.error, runtime crashes) directly from the user's active desktop Brave or Chrome browser. Use this whenever the user asks you to inspect or debug their browser console.",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
            with open(cbc_json, "w", encoding="utf-8") as f:
                json.dump(schema, f, indent=2)

        cbc_py = os.path.join(tools_dir, "capture_active_browser_console.py")
        if not os.path.exists(cbc_py):
            code = (
                "import sys\n"
                "import os\n"
                "def main():\n"
                "    paths = []\n"
                "    if sys.platform == 'darwin':\n"
                "        paths = [\n"
                "            os.path.expanduser('~/Library/Application Support/BraveSoftware/Brave-Browser/chrome_debug.log'),\n"
                "            os.path.expanduser('~/Library/Application Support/Google/Chrome/chrome_debug.log')\n"
                "        ]\n"
                "    for p in paths:\n"
                "        if os.path.exists(p):\n"
                "            try:\n"
                "                with open(p, 'r', encoding='utf-8', errors='ignore') as f:\n"
                "                    recent = f.readlines()[-60:]\n"
                "                print(f'[Success] Captured console from {p}:\\n' + '\\n'.join([l.strip() for l in recent]))\n"
                "                return\n"
                "            except Exception as e:\n"
                "                print(f'[Error] Failed to read {p}: {e}')\n"
                "                sys.exit(1)\n"
                "    print('[Warning] No active debug log found. Enable logging in Chrome/Brave args first.')\n\n"
                "if __name__ == '__main__':\n"
                "    main()\n"
            )
            with open(cbc_py, "w", encoding="utf-8") as f:
                f.write(code)

        # 2. Create the unified web_browser_steering.md Steering Skill
        skills_dir = os.path.join(GLOBAL_CORE_DIR, "skills")
        wbs_md = os.path.join(skills_dir, "web_browser_steering.md")
        if not os.path.exists(wbs_md):
            skills_content = (
                "# Global Transferable Skill — Web & Browser Steering\n\n"
                "## Overview\n"
                "This skill teaches you how and when to deploy Kognisant's custom, globally transferable Web & Browser tools located inside `~/.kognisant_core/tools/`.\n\n"
                "## Active Tools Map\n"
                "- **`search_web`** (`~/.kognisant_core/tools/search_web.py`): Performs a background, headless DuckDuckGo search.\n"
                "- **`browse_web_page`** (`~/.kognisant_core/tools/browse_web_page.py`): Fetches a URL, executes JavaScript via your local headless browser engine, cleans the HTML, and captures JavaScript console error logs.\n"
                "- **`open_in_native_browser`** (`~/.kognisant_core/tools/open_in_native_browser.py`): Opens a search query or a direct URL visually on the user's desktop browser.\n"
                "- **`capture_active_browser_console`** (`~/.kognisant_core/tools/capture_active_browser_console.py`): Captures active console log/error streams directly from the user's active desktop browser session.\n\n"
                "## Decision Matrix: Silent vs Visual\n"
                "- **Always use `search_web` first** when the user asks you to look up facts, find news, or search the web. This retrieves the results silently inside the console without interrupting the user.\n"
                "- **Use `browse_web_page`** once you have search result links and need to read the full contents of an article or documentation page headlessly in the background.\n"
                '- Only use `open_in_native_browser` if the user explicitly asks you to open a page on their screen (e.g. "open this link in Safari").\n\n'
                "## Debugging Console Errors\n"
                "If the user reports a frontend bug or build error:\n"
                "1. Call `capture_active_browser_console` to autonomously inspect their browser's active `console.log` and error streams.\n"
                "2. Read the console logs to trace and repair any unhandled JavaScript exceptions, network blockages, or Pydantic validation crashes.\n"
            )
            with open(wbs_md, "w", encoding="utf-8") as f:
                f.write(skills_content)

        # Create coding_standards transferable skill
        default_skill_path = os.path.join(
            GLOBAL_CORE_DIR, "skills", "coding_standards.md"
        )
        if not os.path.exists(default_skill_path):
            default_skill = (
                "# Global Transferable Skill — Coding Standards\n\n"
                "## General Principles\n"
                "- Write clear, self-documenting code with meaningful variable and function names.\n"
                "- Handle errors gracefully; always catch specific exceptions rather than broad ones.\n"
                "- Keep functions modular and focused on a single responsibility.\n\n"
                "## Documentation & Comments\n"
                "- Document non-obvious algorithms, edge cases, and architectural constraints.\n"
                "- Avoid comments that simply restate what the code is doing.\n"
            )
            with open(default_skill_path, "w", encoding="utf-8") as f:
                f.write(default_skill)

        # Create global_tool_development transferable skill
        tool_dev_skill_path = os.path.join(
            GLOBAL_CORE_DIR, "skills", "global_tool_development.md"
        )
        if not os.path.exists(tool_dev_skill_path):
            tool_dev_skill = (
                "# Global Transferable Skill — Dynamic Tool Development\n\n"
                "## Overview\n"
                "This skill defines the precise guidelines, requirements, and contracts you must follow when creating, modifying, or managing globally transferable tools inside Kognisant.\n\n"
                "## Directory Boundaries & File Names\n"
                "All global tools must be saved directly in `~/.kognisant_core/tools/`. Each tool requires exactly two files named identically (lowercase, underscore-separated):\n"
                "1. **JSON Schema File**: `~/.kognisant_core/tools/<tool_name>.json` (defines the tool calling schema).\n"
                "2. **Python Executable File**: `~/.kognisant_core/tools/<tool_name>.py` (implements the tool logic in Python).\n\n"
                "## Execution Contract & Arguments Parsing\n"
                "- **Language Requirement**: All dynamic global tool implementations **MUST** be written in standard Python 3.\n"
                "- **Subprocess Calling Signature**: The Kognisant runtime executes global tools via an isolated subprocess: `python <tool_name>.py '<json_arguments_string>'.`\n"
                "- **Arguments Ingestion**: Your Python script must read the JSON-formatted arguments string directly from `sys.argv[1]` and decode it. Do not prompt for interactive input.\n"
                "  ```python\n"
                "  import sys\n"
                "  import json\n\n"
                "  def main():\n"
                "      try:\n"
                "          args = json.loads(sys.argv[1])\n"
                "      except Exception:\n"
                "          print('[Error] Failed to parse input arguments.')\n"
                "          sys.exit(1)\n\n"
                "      # Implement your tool logic here...\n"
                "  ```\n"
                "- **Returning Outputs**: Your script must write all output/results directly to standard output (`sys.stdout` or `print()`). The runtime captures `stdout` and returns it back to the assistant.\n"
                "- **Portability**: Rely primarily on Python's built-in standard libraries to guarantee zero-dependency execution across different user environments.\n\n"
                "## Schema Format Standard\n"
                "The JSON schema file must be a standard OpenAI tool/function block:\n"
                "```json\n"
                "{\n"
                '  "type": "function",\n'
                '  "function": {\n'
                '    "name": "tool_name",\n'
                '    "description": "Describe exactly what the tool does and when to use it.",\n'
                '    "parameters": {\n'
                '      "type": "object",\n'
                '      "properties": {\n'
                '        "param_name": {\n'
                '          "type": "string",\n'
                '          "description": "Parameter description..."\n'
                "        }\n"
                "      },\n"
                '      "required": ["param_name"]\n'
                "    }\n"
                "  }\n"
                "}\n"
                "```\n\n"
                "## CRUD Workflow for Tools\n"
                "When requested to create or update a tool:\n"
                "1. Use `create_project_file` to create `~/.kognisant_core/tools/<tool_name>.json` with the schema.\n"
                "2. Use `create_project_file` to create `~/.kognisant_core/tools/<tool_name>.py` with the implementation.\n"
                "3. Use `edit_project_file` to make edits to any existing tool files inside `~/.kognisant_core/tools/`.\n\n"
                "## Strict Global Only - No Workspace Shortcuts Allowed\n"
                "- **No Local Fallbacks**: You must **never** create any tool files or skill files inside the local project folder (e.g. `.kognisant_core/`, `.kognisant/tools/`, etc.).\n"
                "- **Direct Global Writing**: You have full read and write permissions on `~/.kognisant_core/tools/` and `~/.kognisant_core/skills/` via `create_project_file`, `edit_project_file`, `read_project_file`, and `delete_project_path`. You must write to them directly.\n"
                "- **Why?**: The project folder can be deleted by the user at their whim. Saving skills or tools locally in the workspace is forbidden as tools are universally transferable assets.\n"
            )
            with open(tool_dev_skill_path, "w", encoding="utf-8") as f:
                f.write(tool_dev_skill)
    except Exception as e:
        print(
            f"{Colors.YELLOW}[Warning] Failed to initialize global core memory: {e}{Colors.RESET}",
            file=sys.stderr,
        )


def register_project_globally(project_root, project_name):
    """Registers or updates a project's metadata globally to prevent context leakage."""
    init_global_core()
    projects_file = os.path.join(GLOBAL_CORE_DIR, "projects.json")
    try:
        if os.path.exists(projects_file):
            with open(projects_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = {"projects": {}}

        projects = data.get("projects", {})
        abs_path = os.path.abspath(project_root)
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        if abs_path not in projects:
            projects[abs_path] = {
                "name": project_name,
                "initialized_at": timestamp,
                "last_accessed": timestamp,
            }
        else:
            projects[abs_path]["last_accessed"] = timestamp
            projects[abs_path]["name"] = project_name

        data["projects"] = projects
        with open(projects_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(
            f"{Colors.RED}[Error] Failed to register project globally: {e}{Colors.RESET}",
            file=sys.stderr,
        )


def load_providers_and_pool():
    """Loads the rich models pool from local files, warning the user on data corruption."""
    init_global_core()
    pool_path = os.path.join(GLOBAL_CORE_DIR, "models_pool.json")

    try:
        with open(pool_path, "r", encoding="utf-8") as f:
            pool = json.load(f)
        return {}, pool
    except Exception as e:
        print(
            f"  ⚠️  {Colors.YELLOW}[Warning] Failed to load models pool from disk: {e}. Falling back to default settings.{Colors.RESET}",
            file=sys.stderr,
        )
        return {}, {"selected_models": []}


def save_providers_and_pool(providers, pool):
    """Saves the models pool configuration back to disk, returning a success flag."""
    pool_path = os.path.join(GLOBAL_CORE_DIR, "models_pool.json")
    try:
        with open(pool_path, "w", encoding="utf-8") as f:
            json.dump(pool, f, indent=2)
        return True
    except Exception as e:
        print(
            f"  ⚠️  {Colors.RED}[Error] Failed to save models pool changes to disk: {e}{Colors.RESET}",
            file=sys.stderr,
        )
        return False


def get_compiled_models():
    """Loads the explicit nested model pool configuration and flat-compiles it for the app context."""
    providers, pool = load_providers_and_pool()
    compiled_models = []

    pool_dict: dict = pool
    if isinstance(pool_dict, dict):
        selected_groups = pool_dict.get("selected_models", [])
        if isinstance(selected_groups, list):
            for group in selected_groups:
                provider_name = group.get("provider", "Unknown")
                api_key = group.get("api_key", "")
                models_list = group.get("models", [])

                if isinstance(models_list, list):
                    for m in models_list:
                        # Determine protocol based on provider name if not explicitly set
                        protocol = m.get("protocol")
                        if not protocol:
                            p_lower = provider_name.lower()
                            if "ollama" in p_lower:
                                protocol = "ollama"
                            elif "llama" in p_lower or "cpp" in p_lower:
                                protocol = "llama_cpp"
                            else:
                                protocol = "openai"

                        flat_model = {
                            "name": m.get(
                                "model_id", m.get("name")
                            ),  # actual ID string used in API calls
                            "display_name": m.get(
                                "name", "Unknown"
                            ),  # friendly display name in CLI selector
                            "provider": provider_name,
                            "protocol": protocol,
                            "api_base_url": m.get("api_base_url", ""),
                            "api_key": api_key,
                            "capabilities": m.get(
                                "capabilities",
                                {"tool_calling": True, "reasoning": True},
                            ),
                        }
                        compiled_models.append(flat_model)

                # Dynamic Ollama Auto-Discovery
                if provider_name == "Ollama (Local)":
                    try:
                        from .network import OLLAMA_HOST, get_ollama_models

                        local_tags = get_ollama_models()
                        if local_tags:
                            # Avoid duplicates: track what IDs are already in the list for this provider
                            existing_ids = {
                                m.get("model_id", m.get("name"))
                                for m in models_list
                                if isinstance(m, dict)
                            }
                            for tag in local_tags:
                                if tag not in existing_ids:
                                    # Dynamically inject newly discovered local model into the active pool
                                    dynamic_model = {
                                        "name": tag,
                                        "display_name": tag,
                                        "provider": provider_name,
                                        "protocol": "ollama",
                                        "api_base_url": f"{OLLAMA_HOST}",  # Use base host for native API
                                        "api_key": "",
                                        "capabilities": {
                                            "tool_calling": True,
                                            "reasoning": True,
                                        },
                                    }
                                    compiled_models.append(dynamic_model)
                    except Exception:
                        pass

    return compiled_models


def get_default_model(compiled_models):
    """Retrieves the sticky default model from the pool configuration."""
    providers, pool_dict = load_providers_and_pool()
    pool_dict: dict = pool_dict
    if isinstance(pool_dict, dict):
        default_ref = pool_dict.get("default_model")

        if default_ref and isinstance(default_ref, dict):
            name = default_ref.get("name")
            provider = default_ref.get("provider")
            for m in compiled_models:
                if m.get("name") == name and m.get("provider") == provider:
                    return m

    # Fallback to the first available compiled model
    if compiled_models:
        return compiled_models[0]

    return None


def set_default_model(model):
    """Sets the sticky default model globally in models_pool.json."""
    providers, pool_dict = load_providers_and_pool()
    pool_dict: dict = pool_dict
    if isinstance(pool_dict, dict) and isinstance(model, dict):
        pool_dict["default_model"] = {
            "name": model["name"],
            "provider": model["provider"],
        }
        save_providers_and_pool(providers, pool_dict)


def init_project():
    project_dir = os.getcwd()
    kognisant_dir = os.path.join(project_dir, ".kognisant")

    if os.path.exists(kognisant_dir):
        print(
            f"{Colors.YELLOW}[!] Kognisant is already initialized here: {kognisant_dir}{Colors.RESET}"
        )
        return

    try:
        # Create .kognisant/ directory
        os.makedirs(kognisant_dir, exist_ok=True)

        # Create config.json
        config_path = os.path.join(kognisant_dir, "config.json")
        project_name = os.path.basename(project_dir)
        default_config = {
            "project_name": project_name,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "exclude_patterns": [
                ".git",
                "node_modules",
                "__pycache__",
                ".kognisant",
                ".venv",
                "venv",
                "dist",
                "build",
            ],
        }
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(default_config, f, indent=2)

        # Create history/ subdirectory
        history_dir = os.path.join(kognisant_dir, "history")
        os.makedirs(history_dir, exist_ok=True)

        # Create memory-guidlines.md pre-populated with steering rules
        guidelines_path = os.path.join(kognisant_dir, "memory-guidlines.md")
        guidelines_markdown = (
            "# Membrain Steering — Kognisant's Memory Guidelines\n\n"
            "## What It Is\n"
            "This file defines Kognisant's persistent build memory rules and guidelines. It serves "
            "as the project steering mechanism to ensure context is retained cleanly across sessions.\n\n"
            "## Purpose\n"
            "- Enforce memory consistency and prevent context drift.\n"
            "- Define explicit rules on when and how to read/write persistent build memories.\n"
            "- Map the boundary between global core expertise and project-specific execution logs.\n\n"
            "## Rules for Kognisant\n\n"
            "### Reading\n"
            "- At the start of any non-trivial task, read both `.kognisant/context.md` and `.kognisant/memory-guidlines.md` to align with the active project state.\n"
            "- Use this context to avoid re-asking the user redundant questions or rebuilding existing modules.\n"
            "- Strictly adhere to these guidelines during strategizing and execution.\n\n"
            "### Writing (Enforced Boundaries)\n"
            "- **Only the Reflection and Persistence stages are authorized to write or edit `.kognisant/context.md` and `.kognisant/memory-guidlines.md`.**\n"
            "- Execution sub-agents running in parallel are strictly read-only relative to these memory files to prevent resource race conditions.\n"
            "- Update `.kognisant/context.md` after any of these events:\n"
            "  - A feature, phase, or milestone is completed.\n"
            "  - A new architectural decision is made.\n"
            "  - A task status changes (not started -> in progress -> complete).\n\n"
            "### How to Update\n"
            "- Keep entries concise, objective, and factual.\n"
            "- Use checkbox lists (`- [x]` / `- [ ]`) for task tracking.\n"
            "- Never leave conflicting or ambiguous information.\n\n"
            "## Format Conventions\n"
            "- Use H2 (`##`) for major sections.\n"
            "- Use H3 (`###`) for sub-sections.\n"
            "- Keep the documents highly scannable (grok project state under 60 seconds).\n"
        )
        with open(guidelines_path, "w", encoding="utf-8") as f:
            f.write(guidelines_markdown)

        # Create context.md pre-populated with Membrain structure & rules
        context_path = os.path.join(kognisant_dir, "context.md")
        context_markdown = (
            "# Membrain — Kognisant's Persistent Build Memory\n\n"
            "## What It Is\n"
            "The `.kognisant/` folder is Kognisant's long-term memory. It holds structured context "
            "files that persist knowledge across sessions so nothing gets lost between conversations.\n\n"
            "## Purpose\n"
            "- Remember what's been built, what's in progress, and what's next\n"
            "- Retain key architectural decisions and their rationale\n"
            "- Maintain an accurate map of the codebase state\n"
            "- Avoid redundant work or contradictory changes across sessions\n\n"
            "## Folder Structure\n\n"
            "```\n"
            ".kognisant/\n"
            "├── context.md          # Primary build context — project state, phases, decisions\n"
            "├── config.json         # Workspace configurations and exclude patterns\n"
            "└── history/            # Saved multi-turn conversation logs\n"
            "```\n\n"
            "## Rules for Kognisant\n\n"
            "### Reading\n"
            "- At the start of any non-trivial task, read `.kognisant/context.md` to understand current project state\n"
            "- Use it to avoid re-asking the user questions or rebuilding things that already exist\n"
            "- Reference it when making decisions that depend on prior architectural choices\n\n"
            "### Writing — When to Update\n"
            "Update `.kognisant/context.md` after any of these events:\n"
            "- A feature, phase, or milestone is completed\n"
            "- A new architectural decision is made\n"
            "- A file or module is added, renamed, or removed\n"
            "- A task status changes (not started → in progress → complete)\n"
            "- A key dependency or integration is introduced\n"
            "- Something breaks or gets rolled back\n\n"
            "### Writing — How to Update\n"
            "- Keep entries concise and factual — no prose, no filler\n"
            "- Use checkbox lists (`- [x]` / `- [ ]`) for task tracking\n"
            "- Update the `Last updated` date at the top\n"
            '- Move completed phases above the "next" section, don\'t delete them\n'
            "- If a section becomes stale or contradictory, fix it immediately\n"
            "- Never leave conflicting information — accuracy is the whole point\n\n"
            "### What NOT to Put in Membrain\n"
            "- Code snippets (that's what the source files are for)\n"
            "- Temporary debugging notes\n"
            "- Speculative ideas or unconfirmed plans\n"
            "- Anything that belongs in `docs/` (user-facing documentation)\n\n"
            "## Format Conventions\n"
            "- Use H2 (`##`) for major sections\n"
            "- Use H3 (`###`) for sub-sections (phases, feature groups)\n"
            "- Use `- [x]` for done, `- [ ]` for pending\n"
            "- Use inline code for file paths and class names\n"
            "- Keep the file scannable — the goal is to grok project state in 60 seconds\n"
        )
        with open(context_path, "w", encoding="utf-8") as f:
            f.write(context_markdown)

        # Register globally inside ~/.kognisant_core
        register_project_globally(project_dir, project_name)

        print(
            f"{Colors.GREEN}[+] Initialized Kognisant project (Membrain) in: {kognisant_dir}{Colors.RESET}"
        )
        print("    Created default configuration: .kognisant/config.json")
        print("    Created persistent build context: .kognisant/context.md")
        print("    Created project memory guidelines: .kognisant/memory-guidlines.md")
        print("    Registered project inside global core registry (~/.kognisant_core/)")
        print()
        print(f"  {Colors.BOLD}Next steps:{Colors.RESET}")
        print(f"    → {Colors.CYAN}kognisant chat{Colors.RESET}       Start an AI conversation about this project")
        print(f"    → {Colors.CYAN}kognisant spec <name>{Colors.RESET} Plan a feature with requirements → design → tasks")
        print(f"    → {Colors.CYAN}kognisant status{Colors.RESET}     Check your workspace health")
        print()
    except Exception as e:
        print(
            f"{Colors.RED}[Error] Failed to initialize project directory: {e}{Colors.RESET}"
        )


def find_project_root():
    """Traverses directories upward to find a .kognisant directory."""
    current = os.getcwd()
    while True:
        if os.path.isdir(os.path.join(current, ".kognisant")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return None


def scan_project_files(project_root, exclude_patterns):
    project_files = []

    def is_excluded(path):
        parts = path.split(os.sep)
        for part in parts:
            if part in exclude_patterns:
                return True
        return False

    for root, dirs, files in os.walk(project_root):
        dirs[:] = [d for d in dirs if d not in exclude_patterns]
        for file in files:
            full_path = os.path.join(root, file)
            rel_path = os.path.relpath(full_path, project_root)
            if not is_excluded(rel_path):
                project_files.append(rel_path)

    return project_files


def get_project_info():
    root = find_project_root()
    if not root:
        return None

    config_path = os.path.join(root, ".kognisant", "config.json")
    exclude_patterns = [".git", "node_modules", "__pycache__", ".kognisant"]
    project_name = os.path.basename(root)

    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
                exclude_patterns = config.get("exclude_patterns", exclude_patterns)
                project_name = config.get("project_name", project_name)
        except Exception:
            pass

    # Touch global reference registry update
    register_project_globally(root, project_name)

    files = scan_project_files(root, exclude_patterns)
    return {"root": root, "name": project_name, "files": sorted(files)}


def load_project_context(project_root):
    """Loads the content of context.md if it exists."""
    context_path = os.path.join(project_root, ".kognisant", "context.md")
    if os.path.exists(context_path):
        try:
            with open(context_path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            pass
    return None


def load_project_memory_guidelines(project_root):
    """Loads the content of memory-guidlines.md if it exists."""
    path = os.path.join(project_root, ".kognisant", "memory-guidlines.md")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception:
            pass
    return None


def load_global_skills():
    """Loads all markdown files in ~/.kognisant_core/skills/ as global skills."""
    skills_dir = os.path.join(GLOBAL_CORE_DIR, "skills")
    skills = []
    if os.path.exists(skills_dir):
        try:
            for file in os.listdir(skills_dir):
                if file.endswith(".md"):
                    path = os.path.join(skills_dir, file)
                    with open(path, "r", encoding="utf-8", errors="ignore") as f:
                        skills.append(
                            {
                                "name": file[:-3],  # remove .md extension
                                "content": f.read(),
                            }
                        )
        except Exception:
            pass
    return skills


def load_spec_info(project_root, feature_name):
    """Locates and loads Spec-Driven Development files inside .kognisant/specs/<feature_name>/."""
    spec_dir = os.path.join(project_root, ".kognisant", "specs", feature_name)
    if not os.path.exists(spec_dir) or not os.path.isdir(spec_dir):
        return None

    req_path = os.path.join(spec_dir, "requirements.md")
    design_path = os.path.join(spec_dir, "design.md")
    tasks_path = os.path.join(spec_dir, "tasks.md")

    spec_info = {"feature": feature_name, "root": spec_dir}

    try:
        if os.path.exists(req_path):
            with open(req_path, "r", encoding="utf-8") as f:
                spec_info["requirements"] = f.read()
        if os.path.exists(design_path):
            with open(design_path, "r", encoding="utf-8") as f:
                spec_info["design"] = f.read()
        if os.path.exists(tasks_path):
            with open(tasks_path, "r", encoding="utf-8") as f:
                spec_info["tasks"] = f.read()
    except Exception:
        pass

    return spec_info


def save_chat_session(project_info, messages_or_history, session_file):
    """Saves conversation history to .kognisant/history/ for crash resilience & history."""
    if not project_info or not session_file:
        return
    history_dir = os.path.join(project_info["root"], ".kognisant", "history")
    os.makedirs(history_dir, exist_ok=True)

    filepath = os.path.join(history_dir, session_file)
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(messages_or_history, f, indent=2)
        return True
    except Exception as e:
        print(
            f"  ⚠️  {Colors.RED}[Error] Failed to auto-save conversation log: {e}{Colors.RESET}",
            file=sys.stderr,
        )
        return False
