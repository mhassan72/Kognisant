# CLI Authentication

How the Kognisant CLI authenticates against the inference API at `https://inference.kognisant.xyz/v1/`.

The inference API accepts two auth methods (see API docs):
1. **API Key** — prefixed `kog_live_*` or `kog_test_*`
2. **Firebase ID Token** — obtained via Firebase Auth

The CLI supports both. Firebase login is the primary path (browser-based, zero-config). API keys are the secondary path (for headless/CI environments or users who prefer static keys).

---

## Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    CLI Authentication Methods                      │
├─────────────────────────────────────────────────────────────────┤
│  Primary:   Firebase Auth (browser login → refresh token)        │
│  Secondary: API Key (manual entry → stored encrypted)            │
└─────────────────────────────────────────────────────────────────┘
```

---

## 1. Firebase Auth (Primary)

### Login Flow

```
kognisant login
```

1. CLI generates a random `session_code` (32 chars, cryptographically secure)
2. CLI starts a temporary local HTTP server on `localhost:<random_port>/callback`
3. CLI opens the browser to:
   ```
   https://kognisant.xyz/login?cli=true&code=<session_code>&redirect=http://localhost:<port>/callback
   ```
4. User authenticates normally on the web (Google, email/password, etc.)
5. Web app verifies the session code, then redirects to the CLI callback with:
   ```
   http://localhost:<port>/callback?refresh_token=<token>&firebase_api_key=<key>&uid=<uid>&email=<email>
   ```
6. CLI receives the callback, stores credentials, kills the local server
7. CLI prints success:
   ```
   ✓ Logged in as user@example.com
     Kognisant Cloud models are now available.
   ```

### Alternative: Polling-based (if loopback redirect fails)

Some environments block localhost redirects (WSL, remote SSH, corporate firewalls). Fallback:

1. CLI opens browser to `https://kognisant.xyz/login?cli=true&code=<session_code>`
2. CLI polls `https://kognisant.xyz/api/cli/poll?code=<session_code>` every 2 seconds
3. Web app posts credentials to that endpoint after user authenticates
4. CLI receives credentials from poll response

The CLI tries loopback first. If the local server can't bind or doesn't receive a callback within 10 seconds, it switches to polling automatically.

---

## 2. Token Lifecycle

### Storage

Credentials are stored at `~/.kognisant_core/auth.json` with `0o600` permissions:

```json
{
  "method": "firebase",
  "uid": "abc123",
  "email": "user@example.com",
  "refresh_token": "AMf-vBx...",
  "id_token": "eyJhbGciOiJSUzI1NiIs...",
  "expires_at": 1785103096,
  "firebase_api_key": "AIzaSy...",
  "logged_in_at": "2026-08-01T12:00:00Z"
}
```

| Field | Purpose |
|-------|---------|
| `method` | `"firebase"` or `"api_key"` — determines how `get_id_token()` works |
| `uid` | Firebase user ID |
| `email` | Display only (shown in status) |
| `refresh_token` | Long-lived token for obtaining fresh ID tokens |
| `id_token` | Current Firebase ID token (JWT, ~1 hour lifetime) |
| `expires_at` | Unix timestamp when `id_token` expires |
| `firebase_api_key` | Firebase project API key (not secret — identifies the project) |
| `logged_in_at` | ISO timestamp of login (informational) |

### Token Refresh

Firebase ID tokens expire after 1 hour. The CLI auto-refreshes transparently:

```python
def get_id_token(force_refresh: bool = False) -> str | None:
    """Get a valid Firebase ID token, refreshing if expired.

    Args:
        force_refresh: If True, refresh even if token appears valid (for 401 retry).

    Returns:
        Valid ID token string, or None if not logged in or refresh fails.
    """
    auth = _load_auth()
    if not auth:
        return None

    if auth["method"] == "api_key":
        return auth["api_key"]  # Static key, no refresh needed

    # Check if current token is still valid (5-minute buffer)
    if not force_refresh and auth.get("id_token") and time.time() < auth.get("expires_at", 0) - 300:
        return auth["id_token"]

    # Refresh via Firebase token exchange
    refresh_token = auth.get("refresh_token")
    api_key = auth.get("firebase_api_key")
    if not refresh_token or not api_key:
        return None

    try:
        new_id_token, new_refresh_token = _refresh_firebase_token(api_key, refresh_token)
        auth["id_token"] = new_id_token
        auth["refresh_token"] = new_refresh_token
        auth["expires_at"] = time.time() + 3600  # Firebase tokens live 1 hour
        _save_auth(auth)
        return new_id_token
    except Exception:
        return None


def _refresh_firebase_token(api_key: str, refresh_token: str) -> tuple[str, str]:
    """Exchange refresh token for a new ID token via Firebase."""
    data = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }).encode()

    url = f"https://securetoken.googleapis.com/v1/token?key={api_key}"
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")

    ctx = ssl._create_unverified_context()
    with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
        result = json.loads(resp.read().decode())

    return result["id_token"], result["refresh_token"]
```

### Pre-flight Refresh for Streaming

Before starting a streaming request, check token expiry:

```python
def _ensure_token_valid_for_stream():
    """Refresh token if it will expire during a long stream."""
    auth = _load_auth()
    if auth and auth["method"] == "firebase":
        # If token expires within 2 minutes, refresh now
        if time.time() > auth.get("expires_at", 0) - 120:
            get_id_token(force_refresh=True)
```

---

## 3. API Key Auth (Secondary)

For headless environments (CI/CD, remote servers, Docker containers) where a browser login isn't possible.

### Setup

```
kognisant login --api-key
```

1. Prompts user to paste their API key
2. Validates format (must start with `kog_`)
3. Stores in `auth.json`:

```json
{
  "method": "api_key",
  "api_key": "kog_live_c7470d7e9e607506106f940ef455e0f4",
  "logged_in_at": "2026-08-01T12:00:00Z"
}
```

### How to Get an API Key

API keys are managed at `kognisant.xyz/console/api-keys`. The CLI prints this during `--api-key` setup:

```
  Paste your API key (get one at kognisant.xyz/console/api-keys):
  > kog_live_****
  ✓ API key stored. Kognisant Cloud models are now available.
```

### Behavior Differences

| Aspect | Firebase Login | API Key |
|--------|---------------|---------|
| Requires browser | Yes | No |
| Token refresh | Automatic (hourly) | Not needed (static) |
| Expiry | Refresh token long-lived; ID token hourly | Set at key creation (default 30 days) |
| Revocable | Via Firebase (sign out) | Via web console |
| Best for | Interactive CLI use | CI/CD, scripts, headless |

---

## 4. Auth Detection in Network Layer

The inference API auto-detects token type by prefix. The CLI just sends whatever it has:

```python
def _build_auth_header() -> str | None:
    """Get the auth header value for Kognisant Cloud requests."""
    from .auth import get_id_token
    token = get_id_token()
    # token is either:
    #   - Firebase ID token (JWT string) → API treats as Firebase auth
    #   - API key (starts with "kog_") → API treats as API key auth
    # Both go in the same header format
    return f"Bearer {token}" if token else None
```

No branching needed in `network.py` — the API handles detection server-side.

---

## 5. Public Functions (`auth.py` API)

```python
def is_logged_in() -> bool:
    """Check if user has valid credentials stored (Firebase or API key)."""

def get_id_token(force_refresh: bool = False) -> str | None:
    """Get a valid token for API requests. Auto-refreshes Firebase tokens."""

def get_user_email() -> str | None:
    """Get logged-in user's email (for display in status). None if API key auth."""

def login(api_key_mode: bool = False) -> None:
    """Run the interactive login flow (browser or API key paste)."""

def logout() -> None:
    """Clear all stored credentials. Removes auth.json."""
```

---

## 6. Logout

```
kognisant logout
```

1. Removes `~/.kognisant_core/auth.json`
2. Clears in-memory token cache
3. Prints confirmation:
   ```
   ✓ Logged out. Kognisant Cloud models will no longer be available.
     Local and external models are unaffected.
   ```

Does NOT revoke the refresh token server-side (Firebase refresh tokens can't be revoked from the client). If the user wants to revoke all sessions, they do it from the web console.

---

## 7. Migration from `sync_auth.json`

Existing users who ran `kognisant sync login` already have Firebase credentials in `~/.kognisant_core/sync_auth.json`. On first access:

```python
def _migrate_from_sync_auth():
    """One-time migration: copy Firebase credentials from sync_auth.json to auth.json."""
    if os.path.exists(AUTH_PATH):
        return  # Already migrated or logged in independently

    sync_auth_path = os.path.join(GLOBAL_CORE_DIR, "sync_auth.json")
    if not os.path.exists(sync_auth_path):
        return

    try:
        with open(sync_auth_path, "r") as f:
            sync_data = json.load(f)

        if sync_data.get("refresh_token"):
            auth_data = {
                "method": "firebase",
                "uid": "",  # Not stored in sync_auth
                "email": "",
                "refresh_token": sync_data["refresh_token"],
                "id_token": sync_data.get("id_token"),
                "expires_at": sync_data.get("expires_at", 0),
                "firebase_api_key": sync_data.get("firebase_api_key", ""),
                "logged_in_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            _save_auth(auth_data)
    except (json.JSONDecodeError, OSError, KeyError):
        pass  # Don't block on migration failure
```

After migration, `sync.py` imports `get_id_token` from `auth.py` instead of managing its own token logic.

---

## 8. Error Messages

| Situation | Message |
|-----------|---------|
| Not logged in, trying to use cloud | `"Run 'kognisant login' to use Kognisant Cloud models."` |
| Token refresh fails (network) | `"Could not refresh session. Check your internet connection."` |
| Token refresh fails (revoked) | `"Session expired. Run 'kognisant login' to re-authenticate."` |
| API key invalid/revoked | `"API key rejected. Generate a new one at kognisant.xyz/console/api-keys"` |
| 402 insufficient balance | `"Insufficient balance. Top up at: kognisant.xyz/console/billing"` |
| General auth issue | `"If the problem persists, contact support@kognisant.xyz"` |

---

## 9. Security Considerations

| Concern | Mitigation |
|---------|------------|
| `auth.json` readable by other users | File created with `0o600` (owner-only) |
| Refresh token in plaintext | Same security model as `~/.aws/credentials`, `~/.config/gcloud/`. Acceptable for CLI tools. |
| API key in plaintext | Same as above. `0o600` prevents other users from reading. |
| Firebase API key is public | By design — it only identifies the Firebase project, not a secret |
| Token in process memory | Unavoidable for any auth system. Token is short-lived (1 hour). |
| Man-in-the-middle | HTTPS for all token exchange calls |

For environments requiring stronger protection, users can use OS keyring integration (future enhancement) or manage credentials via environment variables:

```bash
export KOGNISANT_API_KEY="kog_live_..."
```

If `KOGNISANT_API_KEY` env var is set, it takes priority over `auth.json`. This allows:
- CI/CD pipelines to inject keys without touching disk
- Docker containers with secrets mounted as env vars
- Users who don't want credentials on disk

---

## 10. CLI Commands

```
kognisant login            Browser-based Firebase login (default)
kognisant login --api-key  Paste an API key for headless environments
kognisant logout           Clear stored credentials
kognisant status           Shows auth state (logged in as X, method, token health)
```

### `kognisant status` auth section

```
  Account:  user@example.com (Firebase)
  Token:    valid (expires in 47 min)
  Cloud:    8 models available
```

Or for API key:

```
  Account:  API key (kog_live_...e0f4)
  Cloud:    8 models available
```

Or not logged in:

```
  Account:  not logged in
  Cloud:    run 'kognisant login' to enable cloud models
```

---

## 11. Environment Variable Override

For CI/CD and automation:

```bash
# Static API key via env var (highest priority)
export KOGNISANT_API_KEY="kog_live_c7470d7e9e607506106f940ef455e0f4"
```

Priority order for auth resolution:
1. `KOGNISANT_API_KEY` env var (if set)
2. `~/.kognisant_core/auth.json` (if exists)
3. Not authenticated (cloud models unavailable)

```python
def get_id_token(force_refresh: bool = False) -> str | None:
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

    # ... rest of token logic ...
```

---

## 12. Implementation Checklist

| Component | Description |
|-----------|-------------|
| `cli_kognisant/auth.py` | New module: `is_logged_in()`, `get_id_token()`, `login()`, `logout()`, `get_user_email()` |
| `cli_kognisant/main.py` | Add `login` and `logout` subcommands |
| `cli_kognisant/main.py` | Update `_handle_status()` to show auth state |
| `cli_kognisant/network.py` | Import `get_id_token` from `auth.py` for cloud requests |
| `cli_kognisant/sync.py` | Replace internal token management with `from .auth import get_id_token` |
| Web app | Add `/login?cli=true` page that handles CLI redirect/poll flow |
| Web app | Add `/api/cli/poll?code=X` endpoint for polling fallback |

---

## 13. Relationship to Sync

```
┌─────────────────────────────────────────┐
│  auth.py                                │
│  "Am I logged in? Give me a token."     │
│  Supports: Firebase login + API key     │
└──────────────┬──────────────────────────┘
               │ provides token
       ┌───────┴───────┐
       │               │
 ┌─────▼─────┐  ┌─────▼──────┐
 │ network.py│  │  sync.py   │
 │ inference │  │ device link│
 │ API calls │  │ push/pull  │
 └───────────┘  └────────────┘
```

- `auth.py` is the single source of truth for "is this user authenticated?"
- `sync.py` uses `auth.py` for its API calls (device registration, push/pull)
- `sync login` becomes: verify `is_logged_in()` → then do device keypair + registration
- A user can be logged in without having sync enabled (inference-only use case)
