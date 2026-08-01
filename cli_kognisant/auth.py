"""
Kognisant account authentication.

Handles Firebase login/token lifecycle and API key auth independently of sync.
Used by: inference API (network.py), sync module, any future cloud features.

Supports two auth methods:
  1. Firebase Auth (browser login → refresh token → auto-refreshing ID tokens)
  2. API Key (static kog_* key for headless/CI environments)

Uses only Python stdlib (no external dependencies).
"""

import json
import logging
import os
import secrets
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

from .colors import Colors
from .config import GLOBAL_CORE_DIR

logger = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────

AUTH_PATH = os.path.join(GLOBAL_CORE_DIR, "auth.json")
SYNC_AUTH_PATH = os.path.join(GLOBAL_CORE_DIR, "sync_auth.json")  # For migration

FIREBASE_REFRESH_URL = "https://securetoken.googleapis.com/v1/token"
LOGIN_URL_BASE = "https://kognisant.xyz/login"
CLI_POLL_URL = "https://kognisant.xyz/api/cli/poll"

# ─── In-memory token cache ────────────────────────────────────────────────────

_token_cache: dict | None = None
_token_cache_lock = threading.Lock()


# ─── Public API ───────────────────────────────────────────────────────────────


def is_logged_in() -> bool:
    """Check if user has valid credentials stored (Firebase or API key).

    Also checks KOGNISANT_API_KEY env var.
    """
    if os.environ.get("KOGNISANT_API_KEY"):
        return True
    auth = _load_auth()
    return auth is not None


def get_id_token(force_refresh: bool = False) -> str | None:
    """Get a valid token for API requests. Auto-refreshes Firebase tokens.

    Priority:
      1. KOGNISANT_API_KEY env var
      2. Stored credentials (auth.json)
      3. Migrated from sync_auth.json (one-time)

    Args:
        force_refresh: If True, refresh even if token appears valid (for 401 retry).

    Returns:
        Valid token string (Firebase ID token or API key), or None if not authenticated.
    """
    # Priority 1: Environment variable
    env_key = os.environ.get("KOGNISANT_API_KEY")
    if env_key:
        return env_key

    # Priority 2: Stored credentials
    auth = _load_auth()
    if not auth:
        _migrate_from_sync_auth()
        auth = _load_auth()
    if not auth:
        return None

    # API key mode — no refresh needed
    if auth.get("method") == "api_key":
        return auth.get("api_key")

    # Firebase mode — check expiry and refresh if needed
    if not force_refresh and auth.get("id_token") and time.time() < auth.get("expires_at", 0) - 300:
        return auth["id_token"]

    # Refresh needed
    refresh_token = auth.get("refresh_token")
    api_key = auth.get("firebase_api_key")
    if not refresh_token or not api_key:
        return None

    try:
        new_id_token, new_refresh_token = _refresh_firebase_token(api_key, refresh_token)
        auth["id_token"] = new_id_token
        auth["refresh_token"] = new_refresh_token
        auth["expires_at"] = time.time() + 3600
        _save_auth(auth)
        return new_id_token
    except Exception as e:
        logger.debug("Token refresh failed: %s", e)
        return None


def get_user_email() -> str | None:
    """Get logged-in user's email (for display in status). None if API key auth or not logged in."""
    auth = _load_auth()
    if not auth:
        return None
    return auth.get("email") or None


def get_auth_method() -> str | None:
    """Get the current auth method: 'firebase', 'api_key', 'env_var', or None."""
    if os.environ.get("KOGNISANT_API_KEY"):
        return "env_var"
    auth = _load_auth()
    if not auth:
        return None
    return auth.get("method")


def login(api_key_mode: bool = False) -> None:
    """Run the interactive login flow.

    Args:
        api_key_mode: If True, prompt for API key paste instead of browser login.
    """
    if api_key_mode:
        _login_api_key()
    else:
        _login_browser()


def logout() -> None:
    """Clear all stored credentials."""
    global _token_cache

    if not os.path.exists(AUTH_PATH):
        print(f"\n  Not logged in. Nothing to do.\n")
        return

    try:
        os.unlink(AUTH_PATH)
    except OSError:
        pass

    with _token_cache_lock:
        _token_cache = None

    print(f"\n  {Colors.GREEN}✓ Logged out.{Colors.RESET} Kognisant Cloud models will no longer be available.")
    print(f"    Local and external models are unaffected.\n")


# ─── Login Flows ──────────────────────────────────────────────────────────────


def _login_browser() -> None:
    """Browser-based Firebase login with polling (same pattern as sync login)."""
    auth = _load_auth()
    if auth:
        email = auth.get("email", "unknown")
        method = auth.get("method", "unknown")
        print(f"\n  Already logged in as {Colors.GREEN}{email}{Colors.RESET} ({method})")
        print(f"  To log out: {Colors.CYAN}kognisant logout{Colors.RESET}\n")
        return

    print(f"\n  {Colors.BOLD}Logging in to Kognisant...{Colors.RESET}\n")

    # Generate session code
    session_code = secrets.token_urlsafe(32)

    login_url = f"{LOGIN_URL_BASE}?cli=true&code={session_code}"

    # Open browser
    print(f"  Opening browser to log in...")
    print(f"  → {Colors.CYAN}{login_url}{Colors.RESET}\n")
    try:
        webbrowser.open(login_url)
    except Exception:
        print(f"  Can't open browser. Visit the URL above manually.")

    # Poll for approval
    print(f"  Waiting for approval (expires in 2 min)...")
    print(f"  Press Ctrl+C to cancel.\n")

    result = _poll_for_auth(session_code)

    if not result:
        print(f"\n  {Colors.RED}Login failed or timed out.{Colors.RESET} Try again.\n")
        return

    # Store credentials
    auth_data = {
        "method": "firebase",
        "uid": result.get("uid", ""),
        "email": result.get("email", ""),
        "refresh_token": result.get("refresh_token", ""),
        "id_token": None,
        "expires_at": 0,
        "firebase_api_key": result.get("firebase_api_key", ""),
        "logged_in_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _save_auth(auth_data)

    # Try to get an initial ID token
    get_id_token(force_refresh=True)

    print(f"  {Colors.GREEN}✓ Logged in as {result.get('email', 'unknown')}{Colors.RESET}")
    print(f"    Kognisant Cloud models are now available.\n")


def _login_api_key() -> None:
    """API key login — paste a kog_* key."""
    auth = _load_auth()
    if auth:
        email = auth.get("email", auth.get("api_key", "")[:20] + "...")
        print(f"\n  Already logged in as {Colors.GREEN}{email}{Colors.RESET}")
        print(f"  To log out: {Colors.CYAN}kognisant logout{Colors.RESET}\n")
        return

    print(f"\n  {Colors.BOLD}API Key Login{Colors.RESET}")
    print(f"  Get a key at: {Colors.CYAN}kognisant.xyz/console/api-keys{Colors.RESET}\n")

    try:
        key = input("  Paste your API key: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n  Cancelled.\n")
        return

    if not key:
        print(f"  {Colors.RED}No key entered.{Colors.RESET}\n")
        return

    if not key.startswith("kog_"):
        print(f"  {Colors.RED}Invalid key format.{Colors.RESET} Keys start with 'kog_'.\n")
        return

    auth_data = {
        "method": "api_key",
        "api_key": key,
        "logged_in_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _save_auth(auth_data)

    print(f"\n  {Colors.GREEN}✓ API key stored.{Colors.RESET} Kognisant Cloud models are now available.\n")


# ─── Polling ──────────────────────────────────────────────────────────────────


def _poll_for_auth(session_code: str, timeout: int = 120, interval: float = 2.0) -> dict | None:
    """Poll the server for login approval.

    Returns credentials dict or None on timeout/failure.
    """
    deadline = time.time() + timeout
    ctx = ssl._create_unverified_context()

    try:
        while time.time() < deadline:
            time.sleep(interval)

            url = f"{CLI_POLL_URL}?code={urllib.parse.quote(session_code)}"
            req = urllib.request.Request(url, method="GET")

            try:
                with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
                    result = json.loads(resp.read().decode())
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
                sys.stdout.write(".")
                sys.stdout.flush()
                continue

            status = result.get("status")
            if status == "pending":
                sys.stdout.write(".")
                sys.stdout.flush()
                continue
            elif status == "approved":
                sys.stdout.write("\n")
                return result
            elif status in ("expired", "denied"):
                print(f"\n  {Colors.RED}Login {status}.{Colors.RESET}")
                return None
    except KeyboardInterrupt:
        print(f"\n\n  Cancelled.\n")
        return None

    return None


# ─── Token Refresh ────────────────────────────────────────────────────────────


def _refresh_firebase_token(api_key: str, refresh_token: str) -> tuple[str, str]:
    """Exchange refresh token for a new ID token via Firebase.

    Returns (new_id_token, new_refresh_token).
    """
    data = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }).encode()

    url = f"{FIREBASE_REFRESH_URL}?key={api_key}"
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    ctx = ssl._create_unverified_context()
    with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
        result = json.loads(resp.read().decode())

    return result["id_token"], result["refresh_token"]


# ─── Storage ──────────────────────────────────────────────────────────────────


def _load_auth() -> dict | None:
    """Load auth credentials from disk. Returns None if not found."""
    global _token_cache

    with _token_cache_lock:
        if _token_cache is not None:
            return _token_cache

    if not os.path.exists(AUTH_PATH):
        return None

    try:
        with open(AUTH_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        with _token_cache_lock:
            _token_cache = data
        return data
    except (json.JSONDecodeError, OSError):
        return None


def _save_auth(auth_data: dict) -> None:
    """Save auth credentials to disk with 0o600 permissions."""
    global _token_cache

    os.makedirs(GLOBAL_CORE_DIR, exist_ok=True)
    tmp = AUTH_PATH + ".tmp"
    fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(auth_data, f, indent=2)
    os.rename(tmp, AUTH_PATH)

    with _token_cache_lock:
        _token_cache = auth_data


# ─── Migration ────────────────────────────────────────────────────────────────


def _migrate_from_sync_auth() -> None:
    """One-time migration: copy Firebase credentials from sync_auth.json to auth.json."""
    if os.path.exists(AUTH_PATH):
        return  # Already migrated or logged in independently

    if not os.path.exists(SYNC_AUTH_PATH):
        return

    try:
        with open(SYNC_AUTH_PATH, "r", encoding="utf-8") as f:
            sync_data = json.load(f)

        if sync_data.get("refresh_token"):
            auth_data = {
                "method": "firebase",
                "uid": "",
                "email": "",
                "refresh_token": sync_data["refresh_token"],
                "id_token": sync_data.get("id_token"),
                "expires_at": sync_data.get("expires_at", 0),
                "firebase_api_key": sync_data.get("firebase_api_key", ""),
                "logged_in_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            _save_auth(auth_data)
            logger.debug("Migrated credentials from sync_auth.json to auth.json")
    except (json.JSONDecodeError, OSError, KeyError):
        pass  # Don't block on migration failure
