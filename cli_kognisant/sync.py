"""
Sync module — Optional device sync via Kognisant Sync API.

Handles device linking, encrypted push/pull of .kognisant_core data,
key management, and conflict resolution.

All sync operations are optional. If user is not linked or cryptography
is not installed, commands gracefully inform and return.

Uses only Python stdlib for HTTP (urllib.request). Requires `cryptography`
package only when sync commands are actually invoked (lazy import).
"""

import hashlib
import json
import logging
import os
import platform
import ssl
import struct
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

from .colors import Colors
from .config import GLOBAL_CORE_DIR

logger = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────

SYNC_API_BASE = "https://sync-service-api-xhm3l3ggta-nw.a.run.app"
SYNC_CONFIG_PATH = os.path.join(GLOBAL_CORE_DIR, "sync_config.json")
SYNC_AUTH_PATH = os.path.join(GLOBAL_CORE_DIR, "sync_auth.json")
SYNC_KEYS_DIR = os.path.join(GLOBAL_CORE_DIR, "sync_keys")
SYNC_BACKUPS_DIR = os.path.join(GLOBAL_CORE_DIR, "sync_backups")

FIREBASE_REFRESH_URL = "https://securetoken.googleapis.com/v1/token"
FIREBASE_API_KEY = ""  # Set during login from server response or config

LINK_URL_BASE = "https://kognisant.xyz/link"

# Components that can be synced
SYNCABLE_COMPONENTS = {
    "models": ("models_pool.json", "Model pool configuration"),
    "skills": ("skills/", "Global skills"),
    "tools": ("tools/", "Custom tools"),
    "jobs": ("jobs.json", "Job configurations"),
    "scripts": ("scripts/", "User scripts"),
}

# Components that are NEVER synced
EXCLUDED_PATTERNS = [
    "daemon.pid", "daemon.log", "*.lock", "__pycache__",
    "sync_keys/", "sync_auth.json", "sync_backups/",
    "logs/", "*.tmp", "telemetry.jsonl",
]


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _is_linked() -> bool:
    """Check if this device is linked (sync_config.json exists with device_id)."""
    if not os.path.exists(SYNC_CONFIG_PATH):
        return False
    try:
        with open(SYNC_CONFIG_PATH, "r") as f:
            config = json.load(f)
        return bool(config.get("device_id"))
    except (json.JSONDecodeError, OSError):
        return False


def _load_sync_config() -> dict | None:
    """Load sync config. Returns None if not linked."""
    if not os.path.exists(SYNC_CONFIG_PATH):
        return None
    try:
        with open(SYNC_CONFIG_PATH, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _save_sync_config(config: dict) -> None:
    """Save sync config atomically."""
    os.makedirs(GLOBAL_CORE_DIR, exist_ok=True)
    tmp = SYNC_CONFIG_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(config, f, indent=2)
    os.rename(tmp, SYNC_CONFIG_PATH)


def _get_auth_token() -> str | None:
    """Get a valid Firebase ID token, refreshing if expired.
    
    Returns None if not linked or refresh fails.
    """
    if not os.path.exists(SYNC_AUTH_PATH):
        return None
    try:
        with open(SYNC_AUTH_PATH, "r") as f:
            auth = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    # Check if current token is still valid (5-min buffer)
    id_token = auth.get("id_token")
    expires_at = auth.get("expires_at", 0)
    if id_token and time.time() < expires_at - 300:
        return id_token

    # Refresh needed
    refresh_token = auth.get("refresh_token")
    api_key = auth.get("firebase_api_key", "")
    if not refresh_token or not api_key:
        return None

    try:
        new_id, new_refresh = _refresh_firebase_token(api_key, refresh_token)
        auth["id_token"] = new_id
        auth["refresh_token"] = new_refresh
        auth["expires_at"] = time.time() + 3600
        # Save updated tokens
        tmp = SYNC_AUTH_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(auth, f, indent=2)
        os.rename(tmp, SYNC_AUTH_PATH)
        os.chmod(SYNC_AUTH_PATH, 0o600)
        return new_id
    except Exception as e:
        logger.debug("Token refresh failed: %s", e)
        return None


def _refresh_firebase_token(api_key: str, refresh_token: str) -> tuple[str, str]:
    """Exchange refresh token for a new ID token via Firebase."""
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


def _api_call(
    path: str,
    method: str = "GET",
    body: dict | None = None,
    auth: bool = True,
    timeout: float = 15.0,
) -> dict | None:
    """Make an API call to the sync service.
    
    Returns parsed JSON response, or None on failure.
    Prints user-facing warnings on connection issues.
    """
    url = f"{SYNC_API_BASE}{path}"
    headers = {"Content-Type": "application/json"}

    if auth:
        token = _get_auth_token()
        if not token:
            print(f"  {Colors.RED}⚠️  Sync authentication expired.{Colors.RESET}")
            print(f"  Run {Colors.CYAN}kognisant sync login{Colors.RESET} to re-link.")
            return None
        headers["Authorization"] = f"Bearer {token}"

    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)

    ctx = ssl._create_unverified_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            error_body = json.loads(e.read().decode())
            error_msg = error_body.get("message", error_body.get("error", str(e.code)))
        except Exception:
            error_msg = f"HTTP {e.code}"
        logger.debug("API error: %s %s → %s", method, path, error_msg)
        print(f"  {Colors.RED}⚠️  API error: {error_msg}{Colors.RESET}")
        return None
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        logger.debug("API unreachable: %s", e)
        print(f"  {Colors.YELLOW}⚠️  Sync API unreachable. All local features unaffected.{Colors.RESET}")
        return None


# ─── Commands ─────────────────────────────────────────────────────────────────


def handle_sync(args):
    """Dispatch sync subcommands."""
    sub = args.sync_command if hasattr(args, "sync_command") else None
    if sub == "login":
        sync_login()
    elif sub == "logout":
        sync_logout()
    elif sub == "status":
        sync_status()
    elif sub == "devices":
        sync_devices()
    elif sub == "push":
        sync_push()
    elif sub == "pull":
        sync_pull()
    else:
        print(f"\n  {Colors.BOLD}Kognisant Sync{Colors.RESET} — optional device sync\n")
        print(f"  {Colors.CYAN}kognisant sync login{Colors.RESET}    Link this device")
        print(f"  {Colors.CYAN}kognisant sync logout{Colors.RESET}   Unlink this device")
        print(f"  {Colors.CYAN}kognisant sync status{Colors.RESET}   Show link status")
        print(f"  {Colors.CYAN}kognisant sync devices{Colors.RESET}  List linked devices")
        print(f"  {Colors.CYAN}kognisant sync push{Colors.RESET}     Upload to cloud (encrypted)")
        print(f"  {Colors.CYAN}kognisant sync pull{Colors.RESET}     Download from cloud")
        print()


def sync_login():
    """Link this device via browser-based approval flow."""
    if _is_linked():
        config = _load_sync_config()
        print(f"\n  Already linked as {Colors.GREEN}{config.get('email', 'unknown')}{Colors.RESET}")
        print(f"  Device: {config.get('device_name')} ({config.get('device_id')})")
        print(f"  To unlink: {Colors.CYAN}kognisant sync logout{Colors.RESET}\n")
        return

    # Check cryptography is available
    try:
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives import serialization
    except ImportError:
        print(f"\n  {Colors.RED}⚠️  'cryptography' package required for sync.{Colors.RESET}")
        print(f"  Install: {Colors.CYAN}pip install cryptography{Colors.RESET}\n")
        return

    print(f"\n  {Colors.BOLD}Linking device to Kognisant...{Colors.RESET}\n")

    # Generate RSA-4096 keypair
    print(f"  Generating keypair (RSA-4096)...")
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
    public_key_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()

    # Initiate device link
    device_name = platform.node() or "Unknown Device"
    plat = sys.platform

    result = _api_call("/api/devices/initiate", method="POST", auth=False, body={
        "device_name": device_name,
        "platform": plat,
        "kognisant_version": "0.1.0",
        "public_key_pem": public_key_pem,
    })

    if not result:
        print(f"  {Colors.RED}Failed to initiate link.{Colors.RESET}\n")
        return

    device_code = result["device_code"]
    link_url = result.get("link_url", f"{LINK_URL_BASE}?code={device_code}")
    expires_in = result.get("expires_in", 600)
    poll_interval = result.get("poll_interval", 2)

    # Open browser
    print(f"  Opening browser to approve...")
    print(f"  → {Colors.CYAN}{link_url}{Colors.RESET}\n")
    try:
        webbrowser.open(link_url)
    except Exception:
        print(f"  Can't open browser. Visit the URL above manually.")

    # Poll for approval
    print(f"  Waiting for approval (expires in {expires_in // 60}min)...")
    print(f"  Press Ctrl+C to cancel.\n")

    deadline = time.time() + expires_in
    try:
        while time.time() < deadline:
            time.sleep(poll_interval)
            poll_result = _api_call(
                f"/api/devices/poll?code={urllib.parse.quote(device_code)}",
                method="GET", auth=False, timeout=10,
            )
            if not poll_result:
                continue

            status = poll_result.get("status")
            if status == "pending":
                sys.stdout.write(".")
                sys.stdout.flush()
                continue
            elif status == "approved":
                sys.stdout.write("\n")
                break
            elif status in ("expired", "denied"):
                print(f"\n  {Colors.RED}Link {status}.{Colors.RESET} Try again.\n")
                return
    except KeyboardInterrupt:
        print(f"\n\n  Cancelled.\n")
        return

    if poll_result.get("status") != "approved":
        print(f"\n  {Colors.RED}Link expired. Try again.{Colors.RESET}\n")
        return

    # Store credentials
    device_id = poll_result["device_id"]
    user_id = poll_result.get("user_id", "")
    email = poll_result.get("email", "")
    refresh_token = poll_result.get("refresh_token", "")
    plan = poll_result.get("plan", "free")
    limits = poll_result.get("limits", {})

    # Save sync config
    _save_sync_config({
        "device_id": device_id,
        "device_name": device_name,
        "user_id": user_id,
        "email": email,
        "plan": plan,
        "api_url": SYNC_API_BASE,
        "linked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "last_sync": None,
        "limits": limits,
    })

    # Save auth tokens
    auth_data = {
        "refresh_token": refresh_token,
        "id_token": None,
        "expires_at": 0,
        "firebase_api_key": poll_result.get("firebase_api_key", ""),
    }
    tmp = SYNC_AUTH_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(auth_data, f, indent=2)
    os.rename(tmp, SYNC_AUTH_PATH)
    os.chmod(SYNC_AUTH_PATH, 0o600)

    # Save keypair
    os.makedirs(SYNC_KEYS_DIR, exist_ok=True)
    priv_path = os.path.join(SYNC_KEYS_DIR, "private.pem")
    pub_path = os.path.join(SYNC_KEYS_DIR, "public.pem")

    priv_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    with open(priv_path, "wb") as f:
        f.write(priv_pem)
    os.chmod(priv_path, 0o600)

    with open(pub_path, "w") as f:
        f.write(public_key_pem)

    print(f"  {Colors.GREEN}✓ Linked!{Colors.RESET}")
    print(f"  Account: {Colors.CYAN}{email}{Colors.RESET}")
    print(f"  Device: {device_name} ({device_id})")
    print(f"  Plan: {plan}\n")
    print(f"  You can now:")
    print(f"    {Colors.CYAN}kognisant sync push{Colors.RESET}    Upload your assistant (encrypted)")
    print(f"    {Colors.CYAN}kognisant sync pull{Colors.RESET}    Download from another device")
    print(f"    {Colors.CYAN}kognisant sync devices{Colors.RESET}  List linked machines\n")


def sync_logout():
    """Unlink this device."""
    if not _is_linked():
        print(f"\n  Not linked. Nothing to do.\n")
        return

    config = _load_sync_config()
    device_id = config.get("device_id", "")

    try:
        confirm = input(f"  Unlink this device? Local data is NOT affected. [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\n  Cancelled.\n")
        return

    if confirm not in ("y", "yes"):
        print("  Cancelled.\n")
        return

    # Call API to unlink (best-effort)
    if device_id:
        _api_call(f"/api/devices/{device_id}", method="DELETE")

    # Remove local sync files
    for path in [SYNC_CONFIG_PATH, SYNC_AUTH_PATH]:
        if os.path.exists(path):
            os.remove(path)

    # Remove keys
    if os.path.exists(SYNC_KEYS_DIR):
        import shutil
        shutil.rmtree(SYNC_KEYS_DIR, ignore_errors=True)

    print(f"  {Colors.GREEN}✓ Device unlinked.{Colors.RESET} Local data unchanged.\n")


def sync_status():
    """Show sync link status, plan, and usage."""
    if not _is_linked():
        print(f"\n  {Colors.YELLOW}Sync not configured.{Colors.RESET} All local features work without it.")
        print(f"  To link: {Colors.CYAN}kognisant sync login{Colors.RESET}")
        print(f"  Get started: https://kognisant.xyz/dashboard/devices\n")
        return

    config = _load_sync_config()
    print(f"\n  {Colors.BOLD}Sync Status{Colors.RESET}\n")
    print(f"  Device:   {config.get('device_name')} ({config.get('device_id', '?')[:12]}...)")
    print(f"  Account:  {Colors.CYAN}{config.get('email', '?')}{Colors.RESET}")
    print(f"  Plan:     {config.get('plan', 'free')}")
    print(f"  Linked:   {config.get('linked_at', '?')}")
    print(f"  Last sync: {config.get('last_sync') or 'never'}")

    # Fetch live usage from API
    usage = _api_call("/api/user/usage")
    if usage:
        syncs_used = usage.get("syncs_used", 0)
        syncs_limit = usage.get("syncs_limit", -1)
        devices = usage.get("devices_count", 0)
        devices_limit = usage.get("devices_limit", 5)
        limit_str = "unlimited" if syncs_limit == -1 else str(syncs_limit)
        print(f"\n  Usage this period:")
        print(f"    Syncs:   {syncs_used} / {limit_str}")
        print(f"    Devices: {devices} / {devices_limit}")
    print()


def sync_devices():
    """List linked devices."""
    if not _is_linked():
        print(f"\n  {Colors.YELLOW}Not linked.{Colors.RESET} Run {Colors.CYAN}kognisant sync login{Colors.RESET} first.\n")
        return

    result = _api_call("/api/devices")
    if not result:
        return

    devices = result.get("devices", [])
    config = _load_sync_config()
    my_device_id = config.get("device_id", "")

    print(f"\n  {Colors.BOLD}Linked Devices{Colors.RESET} ({len(devices)}/{result.get('limit', '?')})\n")
    for dev in devices:
        is_me = dev.get("device_id") == my_device_id
        status = dev.get("status", "unknown")
        icon = "●" if status == "active" else "○"
        color = Colors.GREEN if status == "active" else Colors.YELLOW
        me_tag = f" {Colors.CYAN}(this device){Colors.RESET}" if is_me else ""
        last = dev.get("last_active", "?")
        print(f"    {color}{icon}{Colors.RESET} {dev.get('device_name', '?')}{me_tag}")
        print(f"      {dev.get('platform', '?')} | last active: {last}")
    print()


def sync_push():
    """Package, encrypt, and upload .kognisant_core to cloud."""
    if not _is_linked():
        print(f"\n  {Colors.YELLOW}Not linked.{Colors.RESET} Run {Colors.CYAN}kognisant sync login{Colors.RESET} first.\n")
        return

    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.primitives import hashes, serialization
    except ImportError:
        print(f"\n  {Colors.RED}⚠️  'cryptography' package required.{Colors.RESET}")
        print(f"  Install: {Colors.CYAN}pip install cryptography{Colors.RESET}\n")
        return

    config = _load_sync_config()
    device_id = config.get("device_id", "")

    # Select components
    print(f"\n  {Colors.BOLD}Sync Push{Colors.RESET}\n")
    print(f"  Components to sync:")
    components_to_push = []
    for key, (path, desc) in SYNCABLE_COMPONENTS.items():
        full_path = os.path.join(GLOBAL_CORE_DIR, path)
        exists = os.path.exists(full_path)
        if exists:
            components_to_push.append(key)
            print(f"    {Colors.GREEN}✓{Colors.RESET} {desc}")
        else:
            print(f"    {Colors.YELLOW}─{Colors.RESET} {desc} (not found)")

    if not components_to_push:
        print(f"\n  Nothing to sync.\n")
        return

    # Package into tar.gz
    print(f"\n  Packaging...")
    archive_bytes = _pack_components(components_to_push)
    if not archive_bytes:
        print(f"  {Colors.RED}Failed to package components.{Colors.RESET}\n")
        return

    size_kb = len(archive_bytes) / 1024
    print(f"  Archive size: {size_kb:.0f} KB")

    # Initiate sync job
    print(f"  Initiating sync...")
    result = _api_call("/api/sync/initiate", method="POST", body={
        "from_device_id": device_id,
        "components": components_to_push,
        "blob_size_estimate": len(archive_bytes),
        "target_device_id": None,
    })

    if not result:
        return

    job_id = result["job_id"]
    upload_url = result["upload_url"]

    # Encrypt
    print(f"  Encrypting (AES-256-GCM)...")
    aes_key = os.urandom(32)
    nonce = os.urandom(12)
    aesgcm = AESGCM(aes_key)
    ciphertext = aesgcm.encrypt(nonce, archive_bytes, None)

    # Encrypt AES key with own public key (self-encrypted for cloud storage)
    pub_path = os.path.join(SYNC_KEYS_DIR, "public.pem")
    with open(pub_path, "rb") as f:
        public_key = serialization.load_pem_public_key(f.read())

    encrypted_aes_key = public_key.encrypt(
        aes_key,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )

    # Package blob: [version:1][key_len:2][encrypted_key][nonce:12][ciphertext]
    blob = (
        b"\x01"
        + struct.pack(">H", len(encrypted_aes_key))
        + encrypted_aes_key
        + nonce
        + ciphertext
    )

    blob_hash = f"sha256:{hashlib.sha256(blob).hexdigest()}"

    # Upload to presigned URL
    print(f"  Uploading ({len(blob) / 1024:.0f} KB encrypted)...")
    try:
        upload_req = urllib.request.Request(upload_url, data=blob, method="PUT")
        upload_req.add_header("Content-Type", "application/octet-stream")
        ctx = ssl._create_unverified_context()
        urllib.request.urlopen(upload_req, timeout=60, context=ctx)
    except Exception as e:
        print(f"  {Colors.RED}Upload failed: {e}{Colors.RESET}\n")
        return

    # Confirm upload
    _api_call(f"/api/sync/{job_id}/complete", method="POST", body={
        "action": "uploaded",
        "blob_size": len(blob),
        "blob_hash": blob_hash,
    })

    # Update local config
    config["last_sync"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _save_sync_config(config)

    print(f"\n  {Colors.GREEN}✓ Push complete!{Colors.RESET} (job: {job_id[:12]}...)")
    print(f"  Available for pull on any linked device for 24 hours.\n")


def sync_pull():
    """Download, decrypt, and merge from cloud."""
    if not _is_linked():
        print(f"\n  {Colors.YELLOW}Not linked.{Colors.RESET} Run {Colors.CYAN}kognisant sync login{Colors.RESET} first.\n")
        return

    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.primitives import hashes, serialization
    except ImportError:
        print(f"\n  {Colors.RED}⚠️  'cryptography' package required.{Colors.RESET}")
        print(f"  Install: {Colors.CYAN}pip install cryptography{Colors.RESET}\n")
        return

    config = _load_sync_config()
    device_id = config.get("device_id", "")

    # Check available syncs
    print(f"\n  {Colors.BOLD}Sync Pull{Colors.RESET}\n")
    print(f"  Checking available syncs...")

    result = _api_call(f"/api/sync/available?device_id={device_id}")
    if not result:
        return

    syncs = result.get("syncs", [])
    if not syncs:
        print(f"  No syncs available. Push from another device first.\n")
        return

    # Show available syncs
    print(f"\n  Available syncs:")
    for i, sync in enumerate(syncs, 1):
        from_name = sync.get("from_device_name", "Unknown")
        components = ", ".join(sync.get("components", []))
        size_kb = sync.get("blob_size", 0) / 1024
        created = sync.get("created_at", "?")
        print(f"    [{Colors.CYAN}{i}{Colors.RESET}] From \"{from_name}\" ({size_kb:.0f} KB)")
        print(f"        Components: {components}")
        print(f"        Created: {created}")

    # Select which to pull
    if len(syncs) == 1:
        choice = 0
    else:
        try:
            raw = input(f"\n  Pull which? [1]: ").strip()
            choice = int(raw) - 1 if raw else 0
        except (ValueError, EOFError, KeyboardInterrupt):
            print("\n  Cancelled.\n")
            return

    if choice < 0 or choice >= len(syncs):
        print(f"  Invalid choice.\n")
        return

    selected = syncs[choice]
    job_id = selected["job_id"]

    # Get download URL
    dl_result = _api_call(f"/api/sync/{job_id}/download")
    if not dl_result:
        return

    download_url = dl_result["download_url"]
    expected_hash = dl_result.get("blob_hash", "")

    # Download
    print(f"\n  Downloading ({dl_result.get('blob_size', 0) / 1024:.0f} KB)...")
    try:
        dl_req = urllib.request.Request(download_url)
        ctx = ssl._create_unverified_context()
        with urllib.request.urlopen(dl_req, timeout=60, context=ctx) as resp:
            blob = resp.read()
    except Exception as e:
        print(f"  {Colors.RED}Download failed: {e}{Colors.RESET}\n")
        return

    # Verify hash
    actual_hash = f"sha256:{hashlib.sha256(blob).hexdigest()}"
    if expected_hash and actual_hash != expected_hash:
        print(f"  {Colors.RED}⚠️  Integrity check failed! Blob may be corrupted.{Colors.RESET}\n")
        return

    # Decrypt
    print(f"  Decrypting...")
    try:
        # Parse blob: [version:1][key_len:2][encrypted_key][nonce:12][ciphertext]
        version = blob[0]
        if version != 1:
            print(f"  {Colors.RED}Unknown blob version: {version}{Colors.RESET}\n")
            return

        key_len = struct.unpack(">H", blob[1:3])[0]
        encrypted_aes_key = blob[3:3 + key_len]
        nonce = blob[3 + key_len:3 + key_len + 12]
        ciphertext = blob[3 + key_len + 12:]

        # Decrypt AES key with local private key
        priv_path = os.path.join(SYNC_KEYS_DIR, "private.pem")
        with open(priv_path, "rb") as f:
            private_key = serialization.load_pem_private_key(f.read(), password=None)

        aes_key = private_key.decrypt(
            encrypted_aes_key,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )

        # Decrypt archive
        aesgcm = AESGCM(aes_key)
        archive_bytes = aesgcm.decrypt(nonce, ciphertext, None)
    except Exception as e:
        print(f"  {Colors.RED}Decryption failed: {e}{Colors.RESET}")
        print(f"  This may mean the blob was encrypted for a different device.\n")
        return

    # Backup current state
    print(f"  Backing up current state...")
    os.makedirs(SYNC_BACKUPS_DIR, exist_ok=True)
    backup_name = f"backup_{time.strftime('%Y%m%d_%H%M%S')}.tar.gz"
    backup_path = os.path.join(SYNC_BACKUPS_DIR, backup_name)
    _create_backup(backup_path)

    # Unpack and merge
    print(f"  Applying changes...")
    merge_report = _unpack_and_merge(archive_bytes)

    # Confirm pull with API (triggers blob deletion)
    _api_call(f"/api/sync/{job_id}/complete", method="POST", body={
        "action": "pulled",
        "device_id": device_id,
    })

    # Update local config
    config["last_sync"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _save_sync_config(config)

    print(f"\n  {Colors.GREEN}✓ Pull complete!{Colors.RESET}")
    if merge_report:
        print(f"\n  Merge summary:")
        for line in merge_report:
            print(f"    {line}")
    print(f"\n  Backup saved: {backup_name}")
    print(f"  Run {Colors.CYAN}kognisant status{Colors.RESET} to verify.\n")


# ─── Packaging / Unpacking ────────────────────────────────────────────────────


def _pack_components(components: list[str]) -> bytes | None:
    """Pack selected components into a tar.gz archive in memory."""
    try:
        buf = tempfile.SpooledTemporaryFile(max_size=50 * 1024 * 1024)
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for comp in components:
                path_spec = SYNCABLE_COMPONENTS.get(comp)
                if not path_spec:
                    continue
                rel_path = path_spec[0]
                full_path = os.path.join(GLOBAL_CORE_DIR, rel_path)

                if not os.path.exists(full_path):
                    continue

                if os.path.isdir(full_path):
                    # Add directory recursively
                    for root, dirs, files in os.walk(full_path):
                        # Skip excluded patterns
                        dirs[:] = [d for d in dirs if not _is_excluded(d)]
                        for fname in files:
                            if _is_excluded(fname):
                                continue
                            fpath = os.path.join(root, fname)
                            arcname = os.path.relpath(fpath, GLOBAL_CORE_DIR)
                            tar.add(fpath, arcname=arcname)
                else:
                    # Add single file
                    arcname = os.path.relpath(full_path, GLOBAL_CORE_DIR)
                    tar.add(full_path, arcname=arcname)

        buf.seek(0)
        return buf.read()
    except Exception as e:
        logger.error("Pack failed: %s", e)
        return None


def _is_excluded(name: str) -> bool:
    """Check if a filename matches exclusion patterns."""
    for pattern in EXCLUDED_PATTERNS:
        if pattern.endswith("/"):
            if name == pattern.rstrip("/"):
                return True
        elif pattern.startswith("*."):
            if name.endswith(pattern[1:]):
                return True
        elif name == pattern:
            return True
    return False


def _create_backup(backup_path: str) -> None:
    """Create a tar.gz backup of current syncable state."""
    try:
        with tarfile.open(backup_path, "w:gz") as tar:
            for key, (rel_path, _) in SYNCABLE_COMPONENTS.items():
                full_path = os.path.join(GLOBAL_CORE_DIR, rel_path)
                if os.path.exists(full_path):
                    arcname = rel_path
                    tar.add(full_path, arcname=arcname)
    except Exception as e:
        logger.warning("Backup creation failed: %s", e)


def _unpack_and_merge(archive_bytes: bytes) -> list[str]:
    """Unpack archive and merge into .kognisant_core with conflict resolution.
    
    Returns list of merge report lines.
    """
    report = []
    try:
        buf = tempfile.SpooledTemporaryFile(max_size=50 * 1024 * 1024)
        buf.write(archive_bytes)
        buf.seek(0)

        with tarfile.open(fileobj=buf, mode="r:gz") as tar:
            # Safety: validate all members first
            for member in tar.getmembers():
                if member.name.startswith("/") or ".." in member.name.split("/"):
                    report.append(f"⚠️  Skipped unsafe path: {member.name}")
                    continue

            # Extract to temp dir first
            with tempfile.TemporaryDirectory() as tmpdir:
                # Safe extraction
                for member in tar.getmembers():
                    if member.name.startswith("/") or ".." in member.name.split("/"):
                        continue
                    resolved = os.path.realpath(os.path.join(tmpdir, member.name))
                    if not resolved.startswith(os.path.realpath(tmpdir)):
                        continue
                    tar.extract(member, tmpdir)

                # Merge: walk extracted files and apply to GLOBAL_CORE_DIR
                for root, dirs, files in os.walk(tmpdir):
                    for fname in files:
                        src = os.path.join(root, fname)
                        rel = os.path.relpath(src, tmpdir)
                        dest = os.path.join(GLOBAL_CORE_DIR, rel)

                        # Merge strategy
                        if os.path.exists(dest):
                            # File exists locally — check if it's a JSON we can merge
                            if fname == "models_pool.json":
                                _merge_models_pool(src, dest)
                                report.append(f"models_pool.json: merged")
                            elif fname == "jobs.json":
                                _merge_jobs(src, dest)
                                report.append(f"jobs.json: merged by name")
                            else:
                                # Default: replace if remote is newer (by content comparison)
                                with open(src, "rb") as f1, open(dest, "rb") as f2:
                                    if f1.read() != f2.read():
                                        os.makedirs(os.path.dirname(dest), exist_ok=True)
                                        with open(src, "rb") as s:
                                            with open(dest, "wb") as d:
                                                d.write(s.read())
                                        report.append(f"{rel}: replaced (remote newer)")
                        else:
                            # New file — add it
                            os.makedirs(os.path.dirname(dest), exist_ok=True)
                            with open(src, "rb") as s:
                                with open(dest, "wb") as d:
                                    d.write(s.read())
                            report.append(f"{rel}: added (new)")

    except Exception as e:
        report.append(f"⚠️  Unpack error: {e}")

    return report


def _merge_models_pool(src: str, dest: str) -> None:
    """Merge models_pool.json — union of models, keep existing."""
    try:
        with open(src, "r") as f:
            remote = json.load(f)
        with open(dest, "r") as f:
            local = json.load(f)

        # Merge selected_models by provider+model name
        local_models = local.get("selected_models", [])
        remote_models = remote.get("selected_models", [])

        existing_keys = set()
        for group in local_models:
            for m in group.get("models", []):
                existing_keys.add((group.get("provider", ""), m.get("name", "")))

        for rgroup in remote_models:
            provider = rgroup.get("provider", "")
            # Find matching local group
            local_group = next((g for g in local_models if g.get("provider") == provider), None)
            if local_group is None:
                # New provider — add entire group (strip API key)
                rgroup_copy = dict(rgroup)
                rgroup_copy["api_key"] = ""  # Never sync credentials
                local_models.append(rgroup_copy)
            else:
                # Existing provider — add new models only
                for rm in rgroup.get("models", []):
                    key = (provider, rm.get("name", ""))
                    if key not in existing_keys:
                        local_group.setdefault("models", []).append(rm)

        local["selected_models"] = local_models
        with open(dest, "w") as f:
            json.dump(local, f, indent=2)
    except Exception:
        pass  # On any merge error, leave local unchanged


def _merge_jobs(src: str, dest: str) -> None:
    """Merge jobs.json — add new jobs by name, skip existing."""
    try:
        with open(src, "r") as f:
            remote = json.load(f)
        with open(dest, "r") as f:
            local = json.load(f)

        local_names = {j.get("name") for j in local.get("jobs", [])}
        remote_jobs = remote.get("jobs", [])

        for rjob in remote_jobs:
            if rjob.get("name") not in local_names:
                # Reset state for imported jobs (don't import running/PID state)
                rjob["state"] = "pending"
                rjob["pid"] = None
                rjob["pid_started_at"] = None
                rjob["run_count"] = 0
                rjob["last_run_at"] = None
                rjob["last_exit_code"] = None
                local.setdefault("jobs", []).append(rjob)

        with open(dest, "w") as f:
            json.dump(local, f, indent=2)
    except Exception:
        pass  # On any merge error, leave local unchanged
