# Sync Module — CLI-Side Implementation

## Core Principle: Sync Is Always Optional

**The CLI works 100% without sync.** No account needed. No API calls unless the user explicitly opts in via `kognisant sync login`. If the user never links a device, no sync code is ever executed, no network requests are made, and no import of `sync.py` occurs in the main code path.

- `kognisant sync *` commands gracefully inform and return if not linked (no errors, no exceptions)
- API unreachable → warning message + normal CLI operation continues
- `cryptography` package not installed → sync commands inform user and return (doesn't block CLI startup)
- Daemon heartbeat skips silently if not linked (zero log noise)

---

## Purpose

Adds **optional** device sync capabilities to the Kognisant CLI. This module handles:

- Authenticating with the web app (Firebase token exchange)
- Linking/unlinking the local machine to a user account
- Packaging, encrypting, and uploading `.kognisant_core` to the sync portal
- Downloading, decrypting, and unpacking from the sync portal
- Key pair management (RSA-4096 for E2E encryption, derived from recovery phrase)
- Selective component sync (choose what to transfer)
- Conflict resolution when target already has data (field-level, timestamp-based)

---

## New Module: `cli_kognisant/sync.py`

### Overview

```python
# New CLI commands:
kognisant sync login <token>     # Link device to web account
kognisant sync logout            # Unlink device
kognisant sync status            # Show link status, last sync
kognisant sync push              # Encrypt + upload to cloud
kognisant sync pull              # Download + decrypt from cloud
kognisant sync devices           # List linked devices
```

### Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  sync.py (new module)                                             │
├──────────────────────────────────────────────────────────────────┤
│                                                                    │
│  SyncClient            KeyManager           PackageBuilder         │
│  ──────────            ──────────           ──────────────         │
│  login(token)          generate_keypair()   pack(components[])     │
│  logout()              get_public_key()     unpack(archive, dest)  │
│  push(components)      encrypt_for(pubkey)  list_components()      │
│  pull(job_id)          decrypt(blob)        diff(local, remote)    │
│  list_devices()        rotate_keys()                               │
│  heartbeat()                                                       │
│                                                                    │
└──────────────────────────────────┬───────────────────────────────┘
                                   │ HTTPS (urllib.request)
                                   ▼
                        ┌──────────────────────┐
                        │  Web App API          │
                        │  /api/sync/*          │
                        │  /api/devices/*       │
                        │  /api/auth/*          │
                        └──────────────────────┘
```

---

## Data Flow: Push (Upload)

```
User runs: kognisant sync push

1. SyncClient reads local config:
   - ~/.kognisant_core/sync_config.json (device_id, auth token, API URL)

2. SyncClient calls API: POST /api/sync/initiate
   - Body: { from_device_id, to_device_id: null, components: [...] }
   - Response: { job_id, upload_url }

3. PackageBuilder packs selected components:
   - Reads ~/.kognisant_core/ selectively
   - Creates tar.gz archive in memory/tempfile
   - Excludes: daemon.pid, *.lock, __pycache__, venvs, logs

4. KeyManager encrypts:
   - Generate random 256-bit AES key
   - Encrypt archive with AES-256-GCM
   - If target device known: encrypt AES key with target's RSA public key
   - If target unknown (push-to-cloud): encrypt AES key with user's own key
     (re-encrypt for specific device on pull)

5. SyncClient uploads encrypted blob:
   - PUT upload_url (presigned Firebase Storage URL)
   - Reports size + hash

6. SyncClient confirms:
   - POST /api/sync/{job_id}/complete { status: "uploaded" }

7. Done. Blob sits encrypted in cloud until pulled.
```

## Data Flow: Pull (Download)

```
User runs: kognisant sync pull

1. SyncClient calls API: GET /api/sync/history?status=uploaded
   - Gets available sync jobs waiting for this device

2. User selects which sync to pull (or auto-picks latest)

3. SyncClient calls: GET /api/sync/{job_id}/download
   - Response: { download_url, blob_size, components, from_device }

4. SyncClient downloads encrypted blob from presigned URL

5. KeyManager decrypts:
   - Decrypt AES key with local RSA private key
   - Decrypt archive with AES-256-GCM
   - Verify integrity (GCM tag)

6. PackageBuilder unpacks:
   - Checks for conflicts (files that differ from current local state)
   - Applies merge strategy:
     - Model pool: merge (union of models)
     - Context.md: replace (latest wins)
     - Skills: merge (union)
     - Jobs: merge (skip duplicates by name)
     - Channel configs: merge (skip existing by name)
     - Session history: append
     - Credentials: skip (must be re-entered on new device)
   - Backs up current state before applying

7. SyncClient confirms:
   - POST /api/sync/{job_id}/complete { success: true }
   - Server deletes blob from storage

8. Done. Local .kognisant_core updated with pulled data.
```

---

## Component Selection

Not everything should sync. Users choose:

| Component | Path | Default | Notes |
|-----------|------|---------|-------|
| Project memory | `.kognisant/context.md`, `memory-guidelines.md` | ✅ | Per-project (syncs current project) |
| Model pool | `models_pool.json`, `providers.json` | ✅ | Credentials stripped, just config |
| Skills | `skills/` | ✅ | Transferable knowledge |
| Global tools | `tools/` | ✅ | Custom tool schemas + scripts |
| Session history | Per-project `.kognisant/history/` | ☐ | Can be large, optional |
| Job configs | `jobs.json` | ✅ | Job definitions (not state/PIDs) |
| Channel configs | `channels/channels.json` | ✅ | Configs only, not credentials |
| Channel templates | `channels/templates/` | ✅ | Template banks |
| Scripts | `scripts/*.py` + `*.json` | ✅ | User scripts (not venvs) |
| World model | `.kognisant/world_model/` | ☐ | Large, project-specific |
| Telemetry | `telemetry.jsonl` | ☐ | Machine-specific |
| Self-model | `self_model.json` | ☐ | Machine-specific reliability data |
| Auth config | `auth_config.json` | ❌ | Never sync (contains secrets) |
| Credentials | `channels/credentials/` | ❌ | Never sync (device-specific encryption) |
| Daemon state | `daemon.pid`, `daemon.log` | ❌ | Runtime, not portable |
| Venvs | `scripts/*_venv/` | ❌ | OS/arch specific |

---

## Key Management

### Key Pair Generation

On first `kognisant sync login`:

```python
from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import serialization, hashes

# Generate RSA-4096 keypair
private_key = rsa.generate_private_key(public_exponent=65537, key_size=4096)

# Store private key locally (encrypted with user's master passphrase)
# ~/.kognisant_core/sync_keys/private.pem (0o600, encrypted PEM)

# Upload public key to server (stored in Firestore device doc)
public_key_pem = private_key.public_key().public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo,
)
# → POST /api/devices/link { ..., public_key: public_key_pem }
```

### Encryption Flow

```python
# Pack: encrypt archive for transfer
def encrypt_for_transfer(archive_bytes: bytes, target_public_key_pem: bytes) -> bytes:
    # 1. Generate random AES-256 key
    aes_key = os.urandom(32)
    nonce = os.urandom(12)
    
    # 2. Encrypt archive with AES-256-GCM
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    aesgcm = AESGCM(aes_key)
    ciphertext = aesgcm.encrypt(nonce, archive_bytes, None)
    
    # 3. Encrypt AES key with target's RSA public key
    target_pubkey = serialization.load_pem_public_key(target_public_key_pem)
    encrypted_aes_key = target_pubkey.encrypt(
        aes_key,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    
    # 4. Package: [version][encrypted_key_len][encrypted_key][nonce][ciphertext]
    blob = (
        b"\x01"  # version
        + struct.pack(">H", len(encrypted_aes_key))
        + encrypted_aes_key
        + nonce
        + ciphertext
    )
    return blob
```

---

## File System Additions

```
~/.kognisant_core/
├── sync_config.json        # Sync state (device_id, user_id, api_url, auth_token_ref)
├── sync_keys/
│   ├── private.pem         # RSA-4096 private key (encrypted with master passphrase, 0o600)
│   └── public.pem          # RSA-4096 public key (also uploaded to server)
├── sync_backups/           # Pre-sync backup (auto-created before pull)
│   └── backup_2026-06-24.tar.gz
└── ... (existing structure)
```

### `sync_config.json`

```json
{
  "device_id": "dev_a1b2c3d4",
  "device_name": "MacBook Pro",
  "user_id": "firebase_uid_xyz",
  "plan": "standard",
  "api_url": "https://kognisant.dev/api",
  "linked_at": "2026-06-24T10:00:00Z",
  "last_sync": "2026-06-24T15:30:00Z",
  "last_heartbeat": "2026-06-24T16:00:00Z",
  "limits": {
    "max_devices": 5,
    "max_blob_bytes": 1073741824,
    "syncs_per_month": -1,
    "backup_slots": 1,
    "history_retention_days": 30
  }
}
```

---

## CLI Commands

### `kognisant sync login <token>`

```bash
$ kognisant sync login A7X2K9

  Verifying token with kognisant.dev...
  ✓ Authenticated as user@example.com
  
  Generating device keypair...
  ✓ RSA-4096 keypair created
  
  Linking device...
  ✓ Device "MacBook Pro" linked (device_id: dev_a1b2c3d4)
  
  You can now:
    kognisant sync push      Upload your assistant to cloud (encrypted)
    kognisant sync pull      Download from another device
    kognisant sync devices   List your linked machines
```

### `kognisant sync push`

```bash
$ kognisant sync push

  Plan: Standard ($1.99/mo) | 5 devices | 1 GB max per sync

  Components to sync:
    ✓ Model pool configuration
    ✓ Skills (3 files)
    ✓ Global tools (2 tools)
    ✓ Job configurations (5 jobs)
    ✓ Channel configs (2 channels)
    ✓ Scripts (4 scripts)
    ─ Session history (skipped)
    ─ World model (skipped)

  Packaging... 847 KB (within 1 GB limit ✓)
  Encrypting (AES-256-GCM + RSA-4096)...
  Uploading to kognisant.dev...
  ✓ Sync uploaded successfully (job_id: sync_x7y8z9)
  
  Available for pull on any linked device for 24 hours.
```

If the user exceeds their plan limits:
```bash
$ kognisant sync push

  ⚠ Sync limit reached (5/5 syncs this month on Free plan).
  Upgrade to Standard ($1.99/mo) for unlimited syncs:
    https://kognisant.dev/dashboard/settings

  Or wait until next month (resets in 12 days).
```

### `kognisant sync pull`

```bash
$ kognisant sync pull

  Available syncs:
    [1] From "MacBook Pro" (2h ago, 847 KB)
        Components: models, skills, tools, jobs, channels, scripts
    [2] From "MacBook Pro" (3d ago, 612 KB)
        Components: models, skills

  Pull which? [1]: 1

  Downloading... 847 KB
  Decrypting...
  
  Conflict resolution:
    models_pool.json: 2 new models merged (kept existing + added new)
    skills/coding_standards.md: replaced (remote is newer)
    jobs.json: 1 new job added, 4 existing unchanged
  
  Backing up current state → ~/.kognisant_core/sync_backups/backup_2026-06-24.tar.gz
  Applying changes...
  ✓ Sync complete. Run `kognisant status` to verify.
```

### `kognisant sync status`

```bash
$ kognisant sync status

  Device: MacBook Pro (dev_a1b2c3d4)
  Account: user@example.com
  Plan: Standard ($1.99/mo)
  Linked: 2026-06-20
  Last sync: 2h ago (push to cloud)
  Last heartbeat: 2m ago
  
  Usage this month:
    Syncs: 12 / unlimited
    Devices: 3 / 5
    Backup: 1 / 1 slot used
  Other devices:
    ● Ubuntu Server (active, last sync: 1d ago)
    ○ Old Laptop (offline, 7d)
```

### `kognisant sync logout`

```bash
$ kognisant sync logout

  ⚠ This will unlink this device from your account.
  Your local .kognisant_core data is NOT affected.
  Continue? [y/N]: y
  
  ✓ Device unlinked. Keypair removed.
```

### `kognisant sync devices`

```bash
$ kognisant sync devices

  Linked Devices:
    ● MacBook Pro (this device, active)
    ● Ubuntu Server (active, last seen: 5m ago)
    ○ Old Laptop (offline, last seen: 7d ago)
```

---

## Merge Strategy (Conflict Resolution)

When pulling into a device that already has data:

| Component | Strategy | Rationale |
|-----------|----------|-----------|
| `models_pool.json` | Union merge (add new models, keep existing) | User may have device-specific models |
| `providers.json` | Union merge (add new providers) | API keys may differ per device |
| `context.md` | Replace if remote is newer (by timestamp) | Single source of truth for project memory |
| `memory-guidelines.md` | Replace if remote is newer | Same |
| `skills/` | Union (add new files, replace if remote newer) | Skills are transferable |
| `tools/` | Union (add new, replace if remote newer) | Tools are transferable |
| `jobs.json` | Merge by name (add new jobs, skip existing) | Existing jobs may have device-specific state |
| `channels/channels.json` | Merge by name (add new channels, skip existing) | Channel creds are device-specific |
| `channels/templates/` | Union (add new, replace if newer) | Templates are portable |
| `scripts/*.py` | Union (add new, replace if newer) | Scripts are portable (venvs are not) |
| `history/` | Append (don't overwrite existing sessions) | History is additive |

**Credentials are NEVER synced.** After pulling channel configs to a new device, user must run `kognisant channel set-credentials <name>` to set up new credentials locally.

---

## Heartbeat (Background)

The daemon periodically pings the web API to keep device status current:

```python
# In daemon's _main_loop, every 5 minutes:
def _sync_heartbeat():
    config_path = os.path.join(CORE_DIR, "sync_config.json")
    if not os.path.exists(config_path):
        return  # Not linked, skip
    
    with open(config_path) as f:
        config = json.load(f)
    
    # PATCH /api/devices/{device_id}/heartbeat
    url = f"{config['api_url']}/devices/{config['device_id']}/heartbeat"
    data = json.dumps({
        "last_active": datetime.now(timezone.utc).isoformat(),
        "kognisant_version": __version__,
    }).encode()
    
    req = urllib.request.Request(url, data=data, method="PATCH")
    req.add_header("Authorization", f"Bearer {_get_auth_token()}")
    req.add_header("Content-Type", "application/json")
    
    try:
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass  # Best-effort, non-blocking
```

---

## Dependencies

### Required (for sync only)

```
cryptography>=41.0    # RSA-4096 keypair + AES-256-GCM
```

Same dependency as channels feature credential encryption. If user has channels working, sync works too.

### stdlib used

- `urllib.request` — HTTPS API calls to web app
- `tarfile` — Packing/unpacking .kognisant_core components (**with path traversal protection — see below**)
- `json` — Config and API communication
- `os`, `struct` — File operations, binary formatting
- `hashlib` — Integrity verification (SHA-256 of blobs)
- `tempfile` — Secure temp storage during encrypt/decrypt

### tarfile Extraction Safety

Python's `tarfile.extractall()` is vulnerable to path traversal (CVE-2007-4559). All extraction MUST use safe filtering:

```python
import tarfile
import os

def safe_extract(tar_path: str, dest_dir: str):
    """Extract tar archive with path traversal protection."""
    dest_dir = os.path.realpath(dest_dir)
    
    with tarfile.open(tar_path, "r:gz") as tar:
        for member in tar.getmembers():
            # Reject absolute paths
            if member.name.startswith("/") or member.name.startswith("\\"):
                raise ValueError(f"Absolute path in archive: {member.name}")
            # Reject path traversal
            if ".." in member.name.split("/"):
                raise ValueError(f"Path traversal in archive: {member.name}")
            # Reject symlinks pointing outside dest
            if member.issym() or member.islnk():
                link_target = os.path.realpath(os.path.join(dest_dir, member.linkname))
                if not link_target.startswith(dest_dir):
                    raise ValueError(f"Symlink escape: {member.name} → {member.linkname}")
            # Verify resolved path stays within dest
            resolved = os.path.realpath(os.path.join(dest_dir, member.name))
            if not resolved.startswith(dest_dir):
                raise ValueError(f"Path escape: {member.name}")
        
        # Python 3.12+: use data_filter if available
        if hasattr(tarfile, "data_filter"):
            tar.extractall(dest_dir, filter="data")
        else:
            tar.extractall(dest_dir)
```

This runs BEFORE extraction and rejects any archive member that would write outside the target directory.

---

## Security Considerations

1. **Private key recovery** — RSA-4096 private key is derived from a 24-word seed phrase (shown once at setup). Stored locally encrypted with master passphrase (0o600). If device is lost, user can regenerate the exact keypair on a new device using the recovery phrase.

2. **Server is zero-knowledge** — Web app stores encrypted blobs it cannot decrypt. No decryption key is ever transmitted to the server.

3. **Blobs are ephemeral** — Auto-deleted after 24h or on successful pull (whichever first). Source device is notified if blob expires unpulled.

4. **Auth tokens are short-lived** — Firebase ID tokens expire in 1 hour. Refresh token stored locally in encrypted `sync_auth.json`. Refresh handled automatically via `TokenManager` class (see Auth Token Refresh section).

5. **Device tokens are one-time** — 6-char linking tokens expire in 10 minutes, single-use.

6. **Pre-sync backup** — Every pull creates a backup first. User can roll back with `kognisant sync restore`.

7. **Credentials excluded** — API keys, tokens, encrypted credential files are NEVER included in sync packages. Must be re-entered per device.

8. **No code execution from synced data** — Pulled scripts are stored but not auto-executed. Jobs pulled from another device start in `pending` state, not `running`.

9. **CLI independence** — Sync is always optional. No auth required for local operation. API failures never affect CLI functionality. If not linked, sync commands gracefully inform and return.

---

## Implementation Phases

### Phase 1: Login + Device Linking — 2 weeks

- [ ] `sync.py` module: `SyncClient`, `KeyManager`, `TokenManager`, `PackageManifest`
- [ ] `kognisant sync login <token>` — verify token, generate keypair from seed, show recovery phrase, link device
- [ ] `kognisant sync logout` — unlink, delete local keys + auth tokens
- [ ] `kognisant sync status` — show link status, plan, usage
- [ ] `kognisant sync devices` — list linked devices
- [ ] `kognisant sync recover` — regenerate keypair from 24-word recovery phrase
- [ ] RSA-4096 keypair generation from BIP39-style seed phrase
- [ ] `TokenManager` — Firebase ID token refresh via `securetoken.googleapis.com`
- [ ] `sync_auth.json` — encrypted refresh token storage (0o600)
- [ ] Daemon heartbeat integration (5-minute interval, best-effort, non-blocking)
- [ ] CLI subcommand registration in main.py
- [ ] Graceful no-op when not linked (all sync commands inform + return, never error)
- [ ] API unreachable handling (warning message, no effect on local operation)

### Phase 2: Push/Pull — 3 weeks

- [ ] `PackageBuilder` — selective component packing (tar.gz) with `_manifest.json`
- [ ] Package versioning: `package_version` field, forward-compatibility skip for unknown components
- [ ] `_sync_modified_at` timestamps on syncable entries (models, skills, etc.)
- [ ] `encrypt_for_transfer()` — AES-256-GCM + RSA envelope
- [ ] `kognisant sync push` — package + encrypt + upload + plan limit enforcement
- [ ] `kognisant sync pull` — download + decrypt + unpack
- [ ] `kognisant sync pull --latest --auto` — non-interactive mode for daemon/scripted use
- [ ] `--strategy` flag: remote-wins-if-newer, remote-wins, local-wins, skip-conflicts
- [ ] Field-level merge with timestamp comparison (last-write-wins per entry)
- [ ] Interactive conflict resolution UI (L/R/M/S choices)
- [ ] Conflict logging to `sync_conflicts.log` (for --auto mode review)
- [ ] Pre-sync backup creation
- [ ] `kognisant sync restore` — roll back to pre-pull backup
- [ ] Progress reporting (upload/download percentage)
- [ ] Blob expiry notification handling (daemon heartbeat response)

### Phase 3: Polish — 1 week

- [ ] `kognisant sync push --components "models,skills"` — selective push
- [ ] `kognisant sync push --exclude "history,world_model"` — exclusion
- [ ] Auto-push on specific events (optional: post-agent completion)
- [ ] Auto-repush on expiry (configurable in sync_config.json)
- [ ] Sync notifications in chat (`/sync status`)
- [ ] Error recovery (resume interrupted uploads, retry failed downloads)
- [ ] Rate limit handling (429 backoff with Retry-After)
- [ ] Bandwidth cap enforcement messaging ("8.2/10 GB used this month")

---

## Chat Slash Commands

```
/sync status     Show sync link status and last sync time
/sync push       Quick push (default components)
/sync pull       Quick pull (interactive selection)
/sync devices    List linked devices
```

---

## Integration Points

| Existing System | How Sync Uses It |
|-----------------|-----------------|
| `config.py` (GLOBAL_CORE_DIR) | Source directory for packaging |
| `daemon.py` (_main_loop) | Heartbeat integration (5-min interval) |
| `channels.py` (CredentialManager) | Same `cryptography` dependency, same key patterns |
| `main.py` | New `sync` subcommand registration |
| `chat.py` | `/sync` slash commands |
| `jobs.py` (FileLock) | Lock during pack/unpack to prevent corruption |

---

## API Is Always Optional (CLI Independence)

**Critical design constraint**: The sync module must NEVER interfere with local CLI operation.

```python
# Every sync function must gracefully no-op when not linked:

def sync_push(components=None):
    config = _load_sync_config()
    if config is None:
        print("  Sync not configured. All local features work without it.")
        print("  To set up: kognisant sync login <token>")
        print("  Get a token at: https://kognisant.dev/dashboard/devices")
        return  # No error, no exception, just inform and return

# Every API call must handle failure without affecting local operation:

def _api_call(url, data=None, method="GET"):
    try:
        response = urllib.request.urlopen(req, timeout=10)
        return json.loads(response.read())
    except (urllib.error.URLError, OSError, TimeoutError):
        logger.debug("Sync API unreachable: %s", url)
        return None  # Caller handles None gracefully

# The daemon heartbeat is best-effort:
def _sync_heartbeat():
    if not _is_linked():
        return  # Skip silently, no log spam
    # ... attempt heartbeat, ignore failures
```

**Rules:**
- No sync import should fail if `cryptography` is not installed (lazy import only when sync commands are invoked)
- No daemon startup should fail because sync API is unreachable
- No chat session should be affected by sync state
- `kognisant status` shows sync as a separate optional section, not mixed with core health

---

## Auth Token Refresh (Firebase)

Firebase ID tokens expire after 1 hour. The CLI must refresh them without user interaction.

### Token Storage

```
~/.kognisant_core/
├── sync_config.json          # Contains device_id, user_id, api_url
└── sync_auth.json            # Contains refresh_token (0o600, encrypted)
```

`sync_auth.json` is encrypted with the same mechanism as channel credentials (AES-256-GCM via `cryptography`, keyed from master passphrase). If `cryptography` is not available, sync login refuses to proceed.

### Refresh Flow

```python
import urllib.request
import json

FIREBASE_REFRESH_URL = "https://securetoken.googleapis.com/v1/token"

def _refresh_id_token(api_key: str, refresh_token: str) -> tuple[str, str]:
    """Exchange refresh token for a new ID token.
    
    Returns: (new_id_token, new_refresh_token)
    Raises: SyncAuthError if refresh fails (token revoked, account deleted)
    """
    data = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }).encode()
    
    url = f"{FIREBASE_REFRESH_URL}?key={api_key}"
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    
    response = urllib.request.urlopen(req, timeout=10)
    result = json.loads(response.read())
    
    return result["id_token"], result["refresh_token"]


class TokenManager:
    """Manages Firebase auth tokens with automatic refresh."""
    
    def __init__(self):
        self._id_token: str | None = None
        self._id_token_expires: float = 0
        self._refresh_token: str | None = None
        self._load_from_disk()
    
    def get_token(self) -> str | None:
        """Get a valid ID token, refreshing if expired. Returns None if not linked."""
        if not self._refresh_token:
            return None
        
        if time.time() < self._id_token_expires - 300:  # 5-min buffer
            return self._id_token
        
        # Refresh needed
        try:
            new_id, new_refresh = _refresh_id_token(FIREBASE_API_KEY, self._refresh_token)
            self._id_token = new_id
            self._id_token_expires = time.time() + 3600  # 1 hour
            self._refresh_token = new_refresh
            self._save_to_disk()
            return self._id_token
        except Exception as e:
            logger.warning("Token refresh failed: %s", e)
            return None  # Caller handles gracefully (sync unavailable)
    
    def _load_from_disk(self):
        """Load encrypted refresh token from sync_auth.json."""
        # ... decrypt with master passphrase (same as CredentialManager)
        pass
    
    def _save_to_disk(self):
        """Save encrypted refresh token to sync_auth.json."""
        # ... encrypt with master passphrase
        pass
```

### Token Revocation Handling

If the refresh token is revoked (user deleted account via web, or manually revoked):
```
$ kognisant sync push

  ⚠ Authentication expired. Your sync session is no longer valid.
  This may mean your web account was modified or deleted.
  
  To re-link: kognisant sync login <token>
  Get a new token at: https://kognisant.dev/dashboard/devices
  
  All local features continue to work normally.
```

The CLI auto-clears `sync_auth.json` and `sync_config.json`, reverting to "not linked" state. No other files are touched.

---

## Conflict Resolution (Field-Level Merge)

### The Problem

Simple "union merge" breaks when both devices edit the same entry:

```
Device A: changes model "gemma4" api_base_url to "http://newserver:11434"
Device B: changes model "gemma4" capabilities.reasoning to true

Naive union: one overwrites the other. Data lost.
```

### Solution: Sidecar Metadata Files + Last-Write-Wins

Sync metadata is stored in **separate sidecar files** (never polluting the user-facing configs). This preserves the "sync is optional" principle — if sync is never used, these files don't exist.

```
~/.kognisant_core/
├── models_pool.json              # User-facing config (NEVER modified by sync logic)
├── .sync_meta/                   # Only exists if sync is active
│   ├── models_pool.meta.json     # Per-entry timestamps for conflict detection
│   ├── skills.meta.json
│   └── jobs.meta.json
```

Sidecar format:
```json
// .sync_meta/models_pool.meta.json
{
  "entries": {
    "gemma4": {"modified_at": "2026-06-24T15:30:00Z", "device": "dev_a1b2c3d4"},
    "claude-3": {"modified_at": "2026-06-23T10:00:00Z", "device": "dev_e5f6g7h8"}
  }
}
```

**How it works:**
- When the user edits `models_pool.json`, the CLI detects the change (file mtime) and updates the sidecar entry timestamp on next sync push
- On pull, merge compares sidecar timestamps between source and local
- The actual config files are never modified with sync-internal fields

**Merge rules:**

1. **New entry on remote, not on local** → Add it (union)
2. **Entry exists on both, remote sidecar timestamp is newer** → Replace local with remote
3. **Entry exists on both, local sidecar is newer** → Keep local (don't overwrite)
4. **Entry exists on both, same timestamp** → Keep local (tie-break: local wins)
5. **Entry exists on local, not on remote** → Keep local (never delete)

**Never delete locally unless user explicitly requests.**

### Interactive Conflict UI

For entries where both sides modified after the last sync (true conflict):

```bash
$ kognisant sync pull

  Conflict: models_pool.json → "gemma4"
    Local (modified 2h ago):  api_base_url = "http://newserver:11434"
    Remote (modified 1h ago): capabilities.reasoning = true
  
  [L] Keep local  [R] Use remote  [M] Merge both  [S] Skip
  Choice: M
  
  ✓ Merged: kept local api_base_url + remote capabilities.reasoning
```

For `--auto` mode (non-interactive): use "remote wins if newer, local wins on tie" — log conflicts to `~/.kognisant_core/sync_conflicts.log` for later review.

---

## Non-Interactive Pull Mode

For daemon auto-sync and scripted usage:

```bash
# Pull latest available sync without prompting
kognisant sync pull --latest --auto

# Pull specific job ID
kognisant sync pull --job sync_x7y8z9 --auto

# Pull with specific merge strategy
kognisant sync pull --latest --auto --strategy remote-wins
```

**`--auto` flag behavior:**
- No interactive prompts
- Conflict resolution: `--strategy` flag (default: `remote-wins-if-newer`)
- If multiple syncs available: picks most recent
- If no syncs available: exits 0 with message to stderr
- If download fails: exits 1, does not modify local state

**`--strategy` options:**
- `remote-wins-if-newer` (default) — Remote entry replaces local if timestamp is newer
- `remote-wins` — Remote always overwrites local
- `local-wins` — Only add new entries from remote, never overwrite existing
- `skip-conflicts` — Add new entries, skip any entry that exists on both sides

---

## Key Recovery

### The Problem

RSA-4096 private key stays on device. If device is lost → key is gone → encrypted blobs for that device are unrecoverable.

### Solution: Seed Phrase Encrypts the Private Key

Generate RSA normally (cryptographically random). Then encrypt the private key PEM with a symmetric key derived from a 24-word seed phrase. The seed phrase is a "backup decryption passphrase" — not a key derivation source.

**Why not derive RSA from seed?** Standard RSA generation requires a CSRNG. Deterministic RSA from a seed has no well-audited implementation and is cryptographically fragile. Instead, we use the seed phrase as a portable encryption key for the standard private key.

```python
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import os

def generate_keypair_with_recovery():
    """Generate RSA-4096 keypair + recovery phrase for portable backup."""
    # 1. Generate standard RSA keypair (cryptographically random)
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
    
    # 2. Generate 24-word seed phrase (from wordlist, 256 bits of entropy)
    seed_phrase = _generate_seed_phrase(24)  # BIP39-style wordlist
    
    # 3. Derive symmetric key from seed phrase
    seed_bytes = seed_phrase.encode("utf-8")
    salt = b"kognisant-recovery-v1"  # Fixed salt (phrase is the entropy source)
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=600_000)
    recovery_key = kdf.derive(seed_bytes)
    
    # 4. Encrypt private key PEM with the recovery key (AES-256-GCM)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    nonce = os.urandom(12)
    aesgcm = AESGCM(recovery_key)
    encrypted_pem = aesgcm.encrypt(nonce, private_pem, None)
    
    # 5. Store encrypted PEM as recovery blob (can be stored anywhere safely)
    recovery_blob = nonce + encrypted_pem  # 12 bytes nonce + ciphertext
    
    return private_key, seed_phrase, recovery_blob
```

### Recovery Flow (lost device)

```bash
$ kognisant sync recover

  Enter your 24-word recovery phrase:
  > abandon bicycle catalog diamond ...
  
  Deriving decryption key...
  Decrypting private key...
  ✓ Private key recovered successfully
  
  Link this as a new device? Enter device token: B3M7Q2
  ✓ Device "New MacBook" linked with recovered keys.
```

**Server-side verification**: The public key fingerprint (SHA-256) is stored in Firestore. On recovery, client re-derives the public key from the decrypted private key and server verifies it matches the stored fingerprint.

---

## Blob Expiry and Re-Push

### The Problem

Blobs auto-delete after 24h. If target device is offline for 25h, the sync fails silently.

### Solution: Notification + Re-Push

**Server-side (Cloud Function):**
```
Every hour, check sync_jobs where:
  status = "uploaded" AND created_at < (now - 20h)

For each:
  1. Send push notification to source device (via heartbeat response):
     {"type": "sync_expiring", "job_id": "...", "hours_remaining": 4}
  2. If blob expires without pull:
     - Update job status to "expired"
     - Delete blob from Storage
     - Notify source: {"type": "sync_expired", "job_id": "..."}
```

**CLI-side (daemon heartbeat response):**
```python
# When daemon receives heartbeat response with sync_expiring notification:
def _handle_heartbeat_response(response):
    notifications = response.get("notifications", [])
    for n in notifications:
        if n["type"] == "sync_expiring":
            logger.info("Sync %s expiring in %dh — target device may be offline",
                       n["job_id"], n["hours_remaining"])
        elif n["type"] == "sync_expired":
            logger.warning("Sync %s expired (target never pulled). Re-push if needed.",
                          n["job_id"])
```

**User sees (next time they run `kognisant sync status`):**
```bash
$ kognisant sync status
  
  ⚠ Last push expired (target device "Ubuntu Server" was offline for 24h+)
  Re-push with: kognisant sync push
```

**Optional: auto re-push (configurable):**
```json
// sync_config.json
"auto_repush": true  // Re-push automatically if blob expires and source device is online
```

---

## Sync Package Versioning

### The Problem

Future Kognisant versions may add new component types. An older CLI pulling a newer package won't know what to do with unknown components.

### Solution: Package Manifest with Version

Every sync archive includes a `_manifest.json` at the root:

```json
{
  "package_version": "1.0",
  "kognisant_version": "0.2.0",
  "created_at": "2026-06-24T15:30:00Z",
  "source_device": "dev_a1b2c3d4",
  "components": [
    {"name": "models_pool", "path": "models_pool.json", "version": "1"},
    {"name": "skills", "path": "skills/", "version": "1"},
    {"name": "channel_templates", "path": "channels/templates/", "version": "1"},
    {"name": "custom_new_thing", "path": "new_thing/", "version": "1"}
  ],
  "checksum": "sha256:abc123..."
}
```

**Forward compatibility rules:**

1. CLI reads `_manifest.json` first
2. If `package_version` major > supported → reject with message: "This sync package requires Kognisant v0.3.0+. Please upgrade: pip install --upgrade kognisant"
3. If an individual component has unknown `name` → skip it with warning: "Skipping unknown component 'custom_new_thing' (requires newer Kognisant)"
4. Known components with higher `version` than supported → skip with warning
5. Known components with supported version → process normally

This ensures old CLIs never corrupt new data formats, and users get clear upgrade instructions.
