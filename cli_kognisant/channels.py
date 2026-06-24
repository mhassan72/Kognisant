"""
Channel system for remote AI access and developer marketing engine.

Provides:
- ChannelManager: CRUD for channel configs, lifecycle management
- ChannelRouter: Routes events to assistant (chat.py) or manager mode
- ChannelProtocol: UDS IPC with length-prefixed binary framing
- ChannelServer: Per-channel Unix domain socket server with poll() multiplexing

Uses only Python standard library per Requirement 13.
"""

import enum
import fcntl
import json
import logging
import os
import select
import shutil
import socket
import struct
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .config import GLOBAL_CORE_DIR

logger = logging.getLogger(__name__)

# ─── Constants ─────────────────────────────────────────────────────────────────

CHANNELS_DIR = os.path.join(GLOBAL_CORE_DIR, "channels")
CHANNELS_FILE = os.path.join(CHANNELS_DIR, "channels.json")
CHANNELS_LOCK = os.path.join(CHANNELS_DIR, "channels.lock")
CREDENTIALS_DIR = os.path.join(CHANNELS_DIR, "credentials")
SESSIONS_DIR = os.path.join(CHANNELS_DIR, "sessions")
LOGS_DIR = os.path.join(CHANNELS_DIR, "logs")
TEMPLATES_DIR = os.path.join(CHANNELS_DIR, "templates")
ESCALATIONS_FILE = os.path.join(CHANNELS_DIR, "escalations.jsonl")

PROTOCOL_VERSION = "1.0"
HEARTBEAT_INTERVAL = 30  # seconds
HEARTBEAT_MISS_LIMIT = 3  # 3 misses = 90s = dead
SOCKET_DIR = "/tmp"

VALID_PLATFORMS = {"telegram", "x", "discord", "reddit", "whatsapp", "signal", "webhook"}
VALID_MODES = {"assistant", "manager", "hybrid"}
VALID_STATES = {"stopped", "starting", "running", "paused", "error"}

# Channel name: 1-48 chars, lowercase alphanumeric + hyphens
CHANNEL_NAME_PATTERN = __import__("re").compile(r"^[a-z0-9][a-z0-9\-]{0,47}$")


# ─── Data Models ───────────────────────────────────────────────────────────────

class RouteDecision(enum.Enum):
    """Routing decision for an incoming channel event."""
    ASSISTANT = "assistant"
    MANAGER = "manager"
    ESCALATE = "escalate"
    DROP = "drop"


@dataclass
class ChannelEvent:
    """Normalized event from any platform adapter."""
    event_id: str
    platform: str
    event_type: str        # message, mention, dm, comment, reaction
    sender_id: str         # Platform-native unique ID
    sender_name: str
    content: str
    timestamp: str         # ISO 8601
    context: dict = field(default_factory=dict)
    reply_to: str | None = None
    attachments: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "platform": self.platform,
            "event_type": self.event_type,
            "sender_id": self.sender_id,
            "sender_name": self.sender_name,
            "content": self.content,
            "timestamp": self.timestamp,
            "context": self.context,
            "reply_to": self.reply_to,
            "attachments": self.attachments,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ChannelEvent":
        return cls(
            event_id=d.get("event_id", ""),
            platform=d.get("platform", ""),
            event_type=d.get("event_type", ""),
            sender_id=d.get("sender_id", ""),
            sender_name=d.get("sender_name", ""),
            content=d.get("content", ""),
            timestamp=d.get("timestamp", ""),
            context=d.get("context", {}),
            reply_to=d.get("reply_to"),
            attachments=d.get("attachments", []),
        )


@dataclass
class ChannelAction:
    """Action to send back to the adapter for execution on the platform."""
    action_id: str
    action_type: str       # reply, post, like, delete, mute, dm
    target_id: str | None = None
    content: str | None = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "type": "action",
            "action_id": self.action_id,
            "action_type": self.action_type,
            "target_id": self.target_id,
            "content": self.content,
            "metadata": self.metadata,
        }


# ─── Channel Protocol (UDS IPC) ───────────────────────────────────────────────

class ChannelProtocol:
    """Length-prefixed binary framing over Unix domain sockets.

    Wire format:
        [4 bytes big-endian length][JSON payload UTF-8]

    All messages are JSON objects with a "type" field.
    """

    MAX_MESSAGE_SIZE = 10 * 1024 * 1024  # 10MB safety limit

    @staticmethod
    def send(sock: socket.socket, message: dict) -> None:
        """Send a length-prefixed JSON message over the socket."""
        data = json.dumps(message).encode("utf-8")
        header = struct.pack(">I", len(data))
        sock.sendall(header + data)

    @staticmethod
    def recv(sock: socket.socket) -> dict | None:
        """Receive a length-prefixed JSON message. Returns None on connection close."""
        header = ChannelProtocol._recv_exact(sock, 4)
        if header is None:
            return None
        length = struct.unpack(">I", header)[0]
        if length > ChannelProtocol.MAX_MESSAGE_SIZE:
            raise ValueError(f"Message too large: {length} bytes (max {ChannelProtocol.MAX_MESSAGE_SIZE})")
        data = ChannelProtocol._recv_exact(sock, length)
        if data is None:
            return None
        return json.loads(data.decode("utf-8"))

    @staticmethod
    def _recv_exact(sock: socket.socket, nbytes: int) -> bytes | None:
        """Receive exactly nbytes from socket. Returns None if connection closed."""
        buf = bytearray()
        while len(buf) < nbytes:
            try:
                chunk = sock.recv(nbytes - len(buf))
            except (ConnectionError, OSError):
                return None
            if not chunk:
                return None
            buf.extend(chunk)
        return bytes(buf)


# ─── Channel Server (per-adapter UDS) ─────────────────────────────────────────

class ChannelServer:
    """Unix domain socket server for a single channel adapter.

    Creates a UDS at /tmp/kognisant_channel_{name}.sock, listens for
    one adapter connection, handles the protocol v1.0 handshake, and
    provides send/recv methods for the established connection.
    """

    def __init__(self, channel_name: str):
        self.channel_name = channel_name
        self.socket_path = os.path.join(SOCKET_DIR, f"kognisant_channel_{channel_name}.sock")
        self._server_sock: socket.socket | None = None
        self._client_sock: socket.socket | None = None
        self._adapter_version: str | None = None
        self._adapter_platform: str | None = None
        self._heartbeat_seq: int = 0
        self._last_heartbeat_recv: float = 0.0
        self._last_heartbeat_sent: float = 0.0

    def start(self) -> None:
        """Create and bind the UDS server socket."""
        # Remove stale socket file
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)

        self._server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_sock.bind(self.socket_path)
        self._server_sock.listen(1)
        self._server_sock.setblocking(False)
        os.chmod(self.socket_path, 0o600)
        logger.info("Channel server listening: %s", self.socket_path)

    def accept(self) -> bool:
        """Accept a pending adapter connection (non-blocking). Returns True if connected."""
        if self._client_sock is not None:
            return True
        try:
            conn, _ = self._server_sock.accept()
            conn.setblocking(True)
            conn.settimeout(10.0)  # Handshake timeout
            self._client_sock = conn
            self._last_heartbeat_recv = time.monotonic()
            return True
        except (BlockingIOError, OSError):
            return False

    def handshake(self, config_path: str) -> bool:
        """Perform protocol v1.0 handshake with the connected adapter.

        Expects: {"type": "hello", "protocol_version": "1.0", ...}
        Sends:   {"type": "hello_ack", "protocol_version": "1.0", "config_path": ...}
        Waits for: {"type": "ready", "capabilities": [...]}

        Returns True if handshake succeeded.
        """
        if self._client_sock is None:
            return False

        try:
            # Receive hello
            hello = ChannelProtocol.recv(self._client_sock)
            if not hello or hello.get("type") != "hello":
                logger.error("Channel %s: Expected 'hello', got: %s", self.channel_name, hello)
                self._close_client()
                return False

            # Version check
            their_version = hello.get("protocol_version", "")
            their_major = their_version.split(".")[0] if their_version else ""
            our_major = PROTOCOL_VERSION.split(".")[0]
            if their_major != our_major:
                logger.error(
                    "Channel %s: Protocol version mismatch (ours=%s, theirs=%s)",
                    self.channel_name, PROTOCOL_VERSION, their_version
                )
                self._close_client()
                return False

            self._adapter_version = hello.get("adapter_version", "unknown")
            self._adapter_platform = hello.get("platform", "unknown")

            # Send hello_ack
            from . import __version__ as daemon_version
            ChannelProtocol.send(self._client_sock, {
                "type": "hello_ack",
                "protocol_version": PROTOCOL_VERSION,
                "daemon_version": daemon_version,
                "config_path": config_path,
            })

            # Wait for ready
            ready = ChannelProtocol.recv(self._client_sock)
            if not ready or ready.get("type") != "ready":
                logger.error("Channel %s: Expected 'ready', got: %s", self.channel_name, ready)
                self._close_client()
                return False

            # Switch to non-blocking for normal operation
            self._client_sock.setblocking(False)
            logger.info(
                "Channel %s: Adapter connected (platform=%s, version=%s, capabilities=%s)",
                self.channel_name, self._adapter_platform,
                self._adapter_version, ready.get("capabilities", [])
            )
            return True

        except (OSError, ValueError, json.JSONDecodeError) as e:
            logger.error("Channel %s: Handshake failed: %s", self.channel_name, e)
            self._close_client()
            return False

    def send_message(self, message: dict) -> bool:
        """Send a message to the adapter. Returns False if connection lost."""
        if self._client_sock is None:
            return False
        try:
            self._client_sock.setblocking(True)
            self._client_sock.settimeout(5.0)
            ChannelProtocol.send(self._client_sock, message)
            self._client_sock.setblocking(False)
            return True
        except (OSError, BrokenPipeError):
            logger.warning("Channel %s: Send failed, connection lost", self.channel_name)
            self._close_client()
            return False

    def recv_message(self) -> dict | None:
        """Non-blocking receive from adapter. Returns None if no data or disconnected."""
        if self._client_sock is None:
            return None
        try:
            # Use poll to check if data available
            poller = select.poll()
            poller.register(self._client_sock.fileno(), select.POLLIN)
            events = poller.poll(0)  # Non-blocking
            if not events:
                return None

            self._client_sock.setblocking(True)
            self._client_sock.settimeout(2.0)
            msg = ChannelProtocol.recv(self._client_sock)
            self._client_sock.setblocking(False)

            if msg is None:
                self._close_client()
                return None

            # Track heartbeats
            if msg.get("type") == "heartbeat":
                self._last_heartbeat_recv = time.monotonic()
                # Send ack
                self.send_message({
                    "type": "heartbeat_ack",
                    "seq": msg.get("seq", 0),
                    "ts": datetime.now(timezone.utc).isoformat(),
                })
                return None  # Don't propagate heartbeats as events

            return msg

        except (OSError, ValueError, json.JSONDecodeError):
            return None

    def send_heartbeat(self) -> bool:
        """Send daemon-side heartbeat to adapter."""
        now = time.monotonic()
        if now - self._last_heartbeat_sent < HEARTBEAT_INTERVAL:
            return True

        self._heartbeat_seq += 1
        self._last_heartbeat_sent = now
        return self.send_message({
            "type": "heartbeat",
            "seq": self._heartbeat_seq,
            "ts": datetime.now(timezone.utc).isoformat(),
        })

    def check_health(self) -> bool:
        """Check if adapter is alive based on heartbeat timing."""
        if self._client_sock is None:
            return False
        elapsed = time.monotonic() - self._last_heartbeat_recv
        return elapsed < (HEARTBEAT_INTERVAL * HEARTBEAT_MISS_LIMIT)

    @property
    def connected(self) -> bool:
        return self._client_sock is not None

    def send_shutdown(self, reason: str = "user_requested", grace_seconds: int = 5) -> None:
        """Send shutdown command to adapter."""
        self.send_message({
            "type": "shutdown",
            "reason": reason,
            "grace_seconds": grace_seconds,
        })

    def send_config_reload(self, config_path: str) -> None:
        """Notify adapter to reload config from the given path."""
        self.send_message({
            "type": "config_reload",
            "config_path": config_path,
        })

    def close(self) -> None:
        """Close all sockets and clean up."""
        self._close_client()
        if self._server_sock:
            try:
                self._server_sock.close()
            except OSError:
                pass
            self._server_sock = None
        if os.path.exists(self.socket_path):
            try:
                os.unlink(self.socket_path)
            except OSError:
                pass

    def _close_client(self) -> None:
        if self._client_sock:
            try:
                self._client_sock.close()
            except OSError:
                pass
            self._client_sock = None

    @property
    def fileno(self) -> int | None:
        """Return the server socket fd for external poll() integration."""
        if self._server_sock:
            return self._server_sock.fileno()
        return None


# ─── Channel Router ────────────────────────────────────────────────────────────

class ChannelRouter:
    """Routes incoming channel events to assistant mode or manager mode.

    In hybrid mode, routing depends on both sender identity and context:
    - Owner DM → assistant (full tools via chat.py)
    - Owner public with / prefix → assistant (remote command)
    - Owner public without / → manager (stay in brand voice)
    - Everyone else → manager
    """

    @staticmethod
    def route(event: ChannelEvent, channel_config: dict) -> RouteDecision:
        """Determine routing for an incoming event."""
        mode = channel_config.get("mode", "assistant")
        owner_ids = channel_config.get("owner_ids", [])

        if mode == "assistant":
            if ChannelRouter._is_owner(event.sender_id, owner_ids):
                return RouteDecision.ASSISTANT
            return RouteDecision.DROP  # Assistant-only channels ignore non-owners

        if mode == "manager":
            return RouteDecision.MANAGER

        # Hybrid mode
        if ChannelRouter._is_owner(event.sender_id, owner_ids):
            if event.event_type == "dm":
                return RouteDecision.ASSISTANT
            if event.content.startswith("/"):
                return RouteDecision.ASSISTANT
            return RouteDecision.MANAGER

        return RouteDecision.MANAGER

    @staticmethod
    def _is_owner(sender_id: str, owner_ids: list[str]) -> bool:
        """Check if sender matches any configured owner ID."""
        return sender_id in owner_ids


# ─── Channel Manager ──────────────────────────────────────────────────────────

class ChannelManager:
    """Manages channel configurations with file locking and atomic writes.

    Provides CRUD operations on channels stored in
    ~/.kognisant_core/channels/channels.json.
    """

    def __init__(self):
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        """Create channel directory structure if not exists."""
        for d in (CHANNELS_DIR, CREDENTIALS_DIR, SESSIONS_DIR, LOGS_DIR, TEMPLATES_DIR):
            os.makedirs(d, exist_ok=True)

    def _lock(self):
        """Return a FileLock context manager for channels.json."""
        from .jobs import FileLock
        return FileLock(CHANNELS_LOCK, timeout=5.0)

    def _load(self) -> list[dict]:
        """Load channel configs from disk."""
        if not os.path.exists(CHANNELS_FILE):
            return []
        try:
            with open(CHANNELS_FILE, "r") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
            return data.get("channels", [])
        except (json.JSONDecodeError, OSError):
            logger.warning("channels.json corrupted or unreadable, returning empty")
            return []

    def _save(self, channels: list[dict]) -> None:
        """Atomic write channels to disk (tempfile + rename)."""
        os.makedirs(CHANNELS_DIR, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=CHANNELS_DIR, suffix=".tmp", prefix="channels_")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump({"channels": channels}, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp_path, 0o600)
            os.rename(tmp_path, CHANNELS_FILE)
        except OSError:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def list_channels(self) -> list[dict]:
        """List all channel configurations."""
        with self._lock():
            return self._load()

    def get_channel(self, name: str) -> dict | None:
        """Get a single channel config by name."""
        with self._lock():
            channels = self._load()
            for ch in channels:
                if ch.get("name") == name:
                    return ch
            return None

    def add_channel(
        self,
        name: str,
        platform: str,
        mode: str = "assistant",
        owner_ids: list[str] | None = None,
    ) -> dict:
        """Create a new channel configuration.

        Args:
            name: Channel name (1-48 chars, [a-z0-9-]).
            platform: Target platform (telegram, x, discord, reddit, etc).
            mode: Channel mode (assistant, manager, hybrid).
            owner_ids: List of platform-native owner IDs for auth.

        Returns:
            The created channel config dict.

        Raises:
            ValueError: If name, platform, or mode is invalid, or name exists.
        """
        # Validate
        if not CHANNEL_NAME_PATTERN.match(name):
            raise ValueError(
                f"Invalid channel name '{name}'. Must be 1-48 chars, "
                "lowercase alphanumeric and hyphens, starting with alphanumeric."
            )
        if platform not in VALID_PLATFORMS:
            raise ValueError(f"Invalid platform '{platform}'. Valid: {sorted(VALID_PLATFORMS)}")
        if mode not in VALID_MODES:
            raise ValueError(f"Invalid mode '{mode}'. Valid: {sorted(VALID_MODES)}")

        channel = {
            "name": name,
            "platform": platform,
            "mode": mode,
            "state": "stopped",
            "owner_ids": owner_ids or [],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "credentials_ref": f"{platform}_{name}",
            "assistant_config": {
                "full_tool_access": True,
                "allow_agent_swarm": True,
                "allow_file_ops": True,
                "allow_shell": False,
                "project_root": None,
                "max_response_length": 4096,
                "confirmation_required": [
                    "delete_project_path",
                    "shell_exec",
                    "create_script",
                    "schedule_job",
                ],
            },
            "security": {
                "session_required": False,
                "session_timeout_hours": 8,
                "anomaly_detection": {
                    "enabled": False,
                    "typical_hours": "07:00-01:00",
                    "max_requests_per_minute": 5,
                    "on_anomaly": "require_reauth",
                },
            },
        }

        # Add manager config if applicable
        if mode in ("manager", "hybrid"):
            channel["manager_config"] = {
                "persona": {
                    "voice": "friendly, technical, concise",
                    "knowledge_base": [],
                    "language": "en",
                },
                "rate_limits": {
                    "inbound_per_user_per_minute": 5,
                    "inbound_per_user_per_hour": 30,
                    "inbound_global_per_minute": 50,
                    "outbound_replies_per_hour": 30,
                    "outbound_posts_per_day": 10,
                },
                "cost_gate": {
                    "max_llm_calls_per_hour": 10,
                    "max_llm_calls_per_day": 50,
                    "on_exceeded": "template_only",
                },
                "content_calendar": {
                    "enabled": False,
                    "cron": None,
                    "topics_source": None,
                    "require_approval": True,
                },
                "competitive_tracking": {
                    "competitors": [],
                    "sentiment_gate": True,
                },
                "group_behavior": {
                    "designated_channels": [],
                    "reply_mode": "mentions_only",
                    "ignore_channels": [],
                },
            }

        with self._lock():
            channels = self._load()
            # Check uniqueness
            if any(ch.get("name") == name for ch in channels):
                raise ValueError(f"Channel '{name}' already exists")
            channels.append(channel)
            self._save(channels)

        # Create session directory
        session_dir = os.path.join(SESSIONS_DIR, name)
        os.makedirs(session_dir, exist_ok=True)

        logger.info("Channel '%s' created (platform=%s, mode=%s)", name, platform, mode)
        return channel

    def remove_channel(self, name: str) -> bool:
        """Remove a channel config and its associated data.

        Returns True if removed, False if not found.
        """
        with self._lock():
            channels = self._load()
            original_len = len(channels)
            channels = [ch for ch in channels if ch.get("name") != name]
            if len(channels) == original_len:
                return False
            self._save(channels)

        # Clean up session data
        session_dir = os.path.join(SESSIONS_DIR, name)
        if os.path.isdir(session_dir):
            shutil.rmtree(session_dir, ignore_errors=True)

        # Clean up credential file
        cred_file = os.path.join(CREDENTIALS_DIR, f"{name}.enc")
        if os.path.exists(cred_file):
            os.unlink(cred_file)

        # Clean up socket
        sock_path = os.path.join(SOCKET_DIR, f"kognisant_channel_{name}.sock")
        if os.path.exists(sock_path):
            os.unlink(sock_path)

        logger.info("Channel '%s' removed", name)
        return True

    def update_state(self, name: str, state: str, **kwargs) -> bool:
        """Update a channel's state and optional extra fields.

        Returns True if updated, False if channel not found.
        """
        if state not in VALID_STATES:
            raise ValueError(f"Invalid state '{state}'. Valid: {sorted(VALID_STATES)}")

        with self._lock():
            channels = self._load()
            for ch in channels:
                if ch.get("name") == name:
                    ch["state"] = state
                    for k, v in kwargs.items():
                        ch[k] = v
                    self._save(channels)
                    return True
            return False

    def update_config(self, name: str, updates: dict) -> bool:
        """Merge updates into a channel's configuration.

        Returns True if updated, False if channel not found.
        """
        with self._lock():
            channels = self._load()
            for ch in channels:
                if ch.get("name") == name:
                    self._deep_merge(ch, updates)
                    self._save(channels)
                    return True
            return False

    @staticmethod
    def _deep_merge(base: dict, updates: dict) -> None:
        """Recursively merge updates into base dict."""
        for key, value in updates.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                ChannelManager._deep_merge(base[key], value)
            else:
                base[key] = value

    def write_temp_config(self, name: str) -> str:
        """Write channel config to a temporary file for adapter consumption.

        Returns the path to the temp config file (0o600 permissions).
        """
        channel = self.get_channel(name)
        if channel is None:
            raise ValueError(f"Channel '{name}' not found")

        config_path = os.path.join(SOCKET_DIR, f"kognisant_cfg_{name}.json")
        fd = os.open(config_path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(channel, f, indent=2)
        return config_path

    def get_adapter_script_path(self, name: str) -> str | None:
        """Get the adapter script path for a channel.

        Looks for: ~/.kognisant_core/scripts/channel_{platform}.py
        """
        channel = self.get_channel(name)
        if not channel:
            return None
        platform = channel.get("platform", "")
        script_name = f"channel_{platform}.py"
        script_path = os.path.join(GLOBAL_CORE_DIR, "scripts", script_name)
        if os.path.exists(script_path):
            return script_path
        return None

    def get_adapter_venv_python(self, name: str) -> str | None:
        """Get the venv Python path for a channel's adapter.

        Looks for: ~/.kognisant_core/scripts/channel_{platform}_venv/bin/python
        Falls back to sys.executable if no venv exists.
        """
        channel = self.get_channel(name)
        if not channel:
            return None
        platform = channel.get("platform", "")
        venv_python = os.path.join(
            GLOBAL_CORE_DIR, "scripts", f"channel_{platform}_venv", "bin", "python"
        )
        if os.path.exists(venv_python):
            return venv_python
        return None  # Caller should fall back to sys.executable


# ─── Credential Manager ────────────────────────────────────────────────────────

class CredentialManager:
    """Manages encrypted credential storage for channel adapters.

    Tiered strategy:
      Priority 1: cryptography package (AES-256-GCM via PBKDF2)
      Priority 2: OS keyring (macOS: security CLI, Linux: secret-tool)
      Priority 3: REFUSE — hard failure, no obfuscation

    Credentials are decrypted by core and passed to adapters via env vars.
    Adapters never touch encrypted files.
    """

    @staticmethod
    def has_crypto_backend() -> str | None:
        """Check available crypto backend.

        Returns:
            "cryptography" if the package is available,
            "keyring" if OS keyring is available,
            None if no secure storage is available.
        """
        # Try cryptography package
        try:
            import cryptography  # noqa: F401
            return "cryptography"
        except ImportError:
            pass

        # Try OS keyring (macOS)
        import platform as plat
        if plat.system() == "Darwin":
            # macOS security CLI
            if shutil.which("security"):
                return "keyring"

        # Try Linux secret-tool
        if plat.system() == "Linux":
            if shutil.which("secret-tool"):
                return "keyring"

        return None

    @staticmethod
    def store_credential(channel_name: str, key_name: str, value: str, passphrase: str) -> None:
        """Store a credential securely.

        Args:
            channel_name: Channel this credential belongs to.
            key_name: Credential key (e.g., "bot_token", "api_key").
            value: The secret value to store.
            passphrase: Master passphrase for encryption.

        Raises:
            RuntimeError: If no secure storage backend is available.
        """
        backend = CredentialManager.has_crypto_backend()

        if backend == "cryptography":
            CredentialManager._store_with_cryptography(channel_name, key_name, value, passphrase)
        elif backend == "keyring":
            CredentialManager._store_with_keyring(channel_name, key_name, value)
        else:
            raise RuntimeError(
                "No secure credential storage available.\n"
                "  Option 1: pip install cryptography\n"
                "  Option 2: Configure OS keyring (macOS Keychain / GNOME Keyring)\n"
                "\n"
                "Channel setup aborted. Credentials NOT stored."
            )

    @staticmethod
    def load_credential(channel_name: str, key_name: str, passphrase: str) -> str | None:
        """Load and decrypt a stored credential.

        Returns the plaintext value, or None if not found.
        """
        backend = CredentialManager.has_crypto_backend()

        if backend == "cryptography":
            return CredentialManager._load_with_cryptography(channel_name, key_name, passphrase)
        elif backend == "keyring":
            return CredentialManager._load_with_keyring(channel_name, key_name)
        return None

    @staticmethod
    def _store_with_cryptography(channel_name: str, key_name: str, value: str, passphrase: str) -> None:
        """Encrypt and store using cryptography package (AES-256-GCM + PBKDF2)."""
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        from cryptography.hazmat.primitives import hashes

        # Derive key from passphrase
        salt = os.urandom(16)
        kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=600_000)
        key = kdf.derive(passphrase.encode("utf-8"))

        # Encrypt
        nonce = os.urandom(12)
        aesgcm = AESGCM(key)
        plaintext = json.dumps({key_name: value}).encode("utf-8")
        ciphertext = aesgcm.encrypt(nonce, plaintext, None)

        # Store: version + salt + nonce + ciphertext
        cred_path = os.path.join(CREDENTIALS_DIR, f"{channel_name}.enc")
        os.makedirs(CREDENTIALS_DIR, exist_ok=True)

        # Load existing credentials for this channel (multi-key support)
        existing = {}
        if os.path.exists(cred_path):
            try:
                existing_value = CredentialManager._load_with_cryptography(
                    channel_name, None, passphrase
                )
                if existing_value and isinstance(existing_value, dict):
                    existing = existing_value
            except Exception:
                pass  # Fresh start if existing can't be decrypted

        existing[key_name] = value
        plaintext = json.dumps(existing).encode("utf-8")
        ciphertext = aesgcm.encrypt(nonce, plaintext, None)

        payload = b"\x01" + salt + nonce + ciphertext  # Version 1
        fd = os.open(cred_path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(payload)

    @staticmethod
    def _load_with_cryptography(channel_name: str, key_name: str | None, passphrase: str) -> str | dict | None:
        """Decrypt credential using cryptography package."""
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        from cryptography.hazmat.primitives import hashes

        cred_path = os.path.join(CREDENTIALS_DIR, f"{channel_name}.enc")
        if not os.path.exists(cred_path):
            return None

        with open(cred_path, "rb") as f:
            payload = f.read()

        if len(payload) < 29 or payload[0] != 0x01:  # version + salt(16) + nonce(12)
            return None

        salt = payload[1:17]
        nonce = payload[17:29]
        ciphertext = payload[29:]

        kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=600_000)
        key = kdf.derive(passphrase.encode("utf-8"))

        aesgcm = AESGCM(key)
        try:
            plaintext = aesgcm.decrypt(nonce, ciphertext, None)
        except Exception:
            return None

        data = json.loads(plaintext.decode("utf-8"))

        if key_name is None:
            return data  # Return all credentials
        return data.get(key_name)

    @staticmethod
    def _store_with_keyring(channel_name: str, key_name: str, value: str) -> None:
        """Store credential using OS keyring."""
        import subprocess
        import platform as plat

        service = f"kognisant-channel-{channel_name}"

        if plat.system() == "Darwin":
            # macOS Keychain
            subprocess.run(
                ["security", "add-generic-password",
                 "-a", key_name, "-s", service, "-w", value, "-U"],
                check=True, capture_output=True,
            )
        elif plat.system() == "Linux":
            # GNOME Keyring via secret-tool
            subprocess.run(
                ["secret-tool", "store",
                 "--label", f"Kognisant {channel_name} {key_name}",
                 "service", service, "key", key_name],
                input=value.encode(), check=True, capture_output=True,
            )

    @staticmethod
    def _load_with_keyring(channel_name: str, key_name: str) -> str | None:
        """Load credential from OS keyring."""
        import subprocess
        import platform as plat

        service = f"kognisant-channel-{channel_name}"

        try:
            if plat.system() == "Darwin":
                result = subprocess.run(
                    ["security", "find-generic-password",
                     "-a", key_name, "-s", service, "-w"],
                    capture_output=True, text=True, check=True,
                )
                return result.stdout.strip()
            elif plat.system() == "Linux":
                result = subprocess.run(
                    ["secret-tool", "lookup", "service", service, "key", key_name],
                    capture_output=True, text=True, check=True,
                )
                return result.stdout.strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            return None
        return None

    @staticmethod
    def get_env_vars(channel_name: str, passphrase: str) -> dict[str, str]:
        """Decrypt all credentials for a channel and return as env var dict.

        Returns dict like {"KOGNISANT_TELEGRAM_TOKEN": "abc123", ...}
        """
        backend = CredentialManager.has_crypto_backend()
        if not backend:
            return {}

        if backend == "cryptography":
            data = CredentialManager._load_with_cryptography(channel_name, None, passphrase)
            if isinstance(data, dict):
                # Convert to env vars: key_name → KOGNISANT_{PLATFORM}_{KEY_NAME}
                env = {}
                for k, v in data.items():
                    env_key = f"KOGNISANT_{k.upper()}"
                    env[env_key] = str(v)
                return env
        return {}


# ─── Session Auth ──────────────────────────────────────────────────────────────

class SessionAuth:
    """Manages session authentication for remote assistant mode.

    Sessions are time-limited auth tokens that verify the owner beyond
    platform identity. Stored in memory (ephemeral — die with daemon).
    """

    def __init__(self):
        self._sessions: dict[str, dict] = {}  # channel_name -> {pin, expires_at, active}

    def create_session(self, channel_name: str, pin: str, timeout_hours: float = 8.0) -> None:
        """Create a new session for a channel."""
        self._sessions[channel_name] = {
            "pin": pin,
            "expires_at": time.time() + (timeout_hours * 3600),
            "active": False,
            "last_activity": time.time(),
        }

    def activate(self, channel_name: str, pin_attempt: str) -> bool:
        """Attempt to activate a session with a PIN. Returns True if successful."""
        session = self._sessions.get(channel_name)
        if not session:
            return False
        if time.time() > session["expires_at"]:
            del self._sessions[channel_name]
            return False
        if pin_attempt == session["pin"]:
            session["active"] = True
            session["last_activity"] = time.time()
            return True
        return False

    def is_authenticated(self, channel_name: str) -> bool:
        """Check if there's an active, non-expired session."""
        session = self._sessions.get(channel_name)
        if not session or not session["active"]:
            return False
        if time.time() > session["expires_at"]:
            del self._sessions[channel_name]
            return False
        # Inactivity timeout (2 hours)
        if time.time() - session["last_activity"] > 7200:
            session["active"] = False
            return False
        session["last_activity"] = time.time()
        return True

    def revoke(self, channel_name: str) -> None:
        """Revoke session for a channel."""
        self._sessions.pop(channel_name, None)

    def revoke_all(self) -> None:
        """Revoke all sessions (lockdown)."""
        self._sessions.clear()

    def session_required(self, channel_config: dict) -> bool:
        """Check if session auth is required for this channel."""
        return channel_config.get("security", {}).get("session_required", False)


# ─── Audit Logger ──────────────────────────────────────────────────────────────

class AuditLogger:
    """Append-only audit log for channel actions.

    Every remote assistant action is logged with:
    who, when, what, channel, result.
    """

    def __init__(self, channel_name: str):
        self.log_path = os.path.join(SESSIONS_DIR, channel_name, "audit.jsonl")
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)

    def log(self, sender_id: str, action: str, details: str = "", result: str = "ok") -> None:
        """Append an audit entry."""
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "sender": sender_id,
            "action": action,
            "details": details[:500],  # Truncate long details
            "result": result,
        }
        try:
            with open(self.log_path, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError:
            logger.warning("Failed to write audit log for channel %s", self.log_path)

    def recent(self, n: int = 20) -> list[dict]:
        """Read the last N audit entries."""
        if not os.path.exists(self.log_path):
            return []
        entries = []
        try:
            with open(self.log_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        entries.append(json.loads(line))
            return entries[-n:]
        except (OSError, json.JSONDecodeError):
            return []


# ─── Response Formatter ────────────────────────────────────────────────────────

class ResponseFormatter:
    """Format Kognisant responses for platform-specific constraints."""

    PLATFORM_LIMITS = {
        "telegram": 4096,
        "x": 280,
        "discord": 2000,
        "reddit": 10000,
        "whatsapp": 65536,
        "signal": 65536,
        "webhook": 1_000_000,
    }

    @staticmethod
    def format(response: str, platform: str) -> list[str]:
        """Split response into platform-appropriate chunks.

        Returns a list of message strings (usually 1, may be multiple for long responses).
        """
        limit = ResponseFormatter.PLATFORM_LIMITS.get(platform, 4096)

        if len(response) <= limit:
            return [response]

        # Split at paragraph boundaries first, then sentence, then hard cut
        chunks = []
        remaining = response

        while remaining:
            if len(remaining) <= limit:
                chunks.append(remaining)
                break

            # Try to find a paragraph break within limit
            cut_point = remaining.rfind("\n\n", 0, limit)
            if cut_point == -1 or cut_point < limit // 2:
                # Try single newline
                cut_point = remaining.rfind("\n", 0, limit)
            if cut_point == -1 or cut_point < limit // 2:
                # Try sentence boundary
                cut_point = remaining.rfind(". ", 0, limit)
                if cut_point != -1:
                    cut_point += 1  # Include the period
            if cut_point == -1 or cut_point < limit // 4:
                # Hard cut at space
                cut_point = remaining.rfind(" ", 0, limit)
            if cut_point == -1:
                # Absolute hard cut
                cut_point = limit

            chunks.append(remaining[:cut_point].rstrip())
            remaining = remaining[cut_point:].lstrip()

        return chunks
