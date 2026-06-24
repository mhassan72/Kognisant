#!/usr/bin/env python3
"""
Kognisant Channel Adapter: Telegram
Protocol: v1.0 (UDS, length-prefixed binary)

Dependencies: python-telegram-bot>=21.0
Requirements file: channel_telegram_requirements.txt

This is the reference adapter. It ships with Kognisant and gets copied
to ~/.kognisant_core/scripts/ on first use.
"""

import asyncio
import json
import logging
import os
import signal
import socket
import struct
import sys
import time
from datetime import datetime, timezone

# ─── Configuration ─────────────────────────────────────────────────────────────

PROTOCOL_VERSION = "1.0"
ADAPTER_VERSION = "0.1.0"
HEARTBEAT_INTERVAL = 30  # seconds

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("channel_telegram")

# ─── Protocol Helpers ──────────────────────────────────────────────────────────


def proto_send(sock: socket.socket, message: dict) -> None:
    """Send length-prefixed JSON message over UDS."""
    data = json.dumps(message).encode("utf-8")
    header = struct.pack(">I", len(data))
    sock.sendall(header + data)


def proto_recv(sock: socket.socket, timeout: float = 5.0) -> dict | None:
    """Receive length-prefixed JSON message. Returns None on timeout/disconnect."""
    sock.settimeout(timeout)
    try:
        header = _recv_exact(sock, 4)
        if header is None:
            return None
        length = struct.unpack(">I", header)[0]
        if length > 10 * 1024 * 1024:
            raise ValueError(f"Message too large: {length}")
        data = _recv_exact(sock, length)
        if data is None:
            return None
        return json.loads(data.decode("utf-8"))
    except (socket.timeout, OSError, ValueError) as e:
        logger.debug("proto_recv error: %s", e)
        return None


def _recv_exact(sock: socket.socket, n: int) -> bytes | None:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


# ─── Main Adapter ─────────────────────────────────────────────────────────────

_shutdown = False


def _sigterm_handler(sig, frame):
    global _shutdown
    _shutdown = True


signal.signal(signal.SIGTERM, _sigterm_handler)
signal.signal(signal.SIGINT, _sigterm_handler)


def connect_to_daemon():
    """Connect to the daemon UDS and perform protocol handshake."""
    socket_path = os.environ.get("KOGNISANT_SOCKET")
    if not socket_path:
        logger.error("KOGNISANT_SOCKET env var not set")
        sys.exit(1)

    logger.info("Connecting to daemon: %s", socket_path)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(socket_path)

    # Send hello
    proto_send(sock, {
        "type": "hello",
        "protocol_version": PROTOCOL_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "platform": "telegram",
    })

    # Receive hello_ack
    ack = proto_recv(sock, timeout=10.0)
    if not ack or ack.get("type") != "hello_ack":
        logger.error("Handshake failed: expected hello_ack, got %s", ack)
        sys.exit(1)

    logger.info("Handshake complete (daemon v%s)", ack.get("daemon_version", "?"))

    # Load config
    config_path = ack.get("config_path") or os.environ.get("KOGNISANT_CONFIG_PATH")
    config = {}
    if config_path and os.path.exists(config_path):
        with open(config_path, "r") as f:
            config = json.load(f)
        logger.info("Config loaded from %s", config_path)

    # Send ready
    proto_send(sock, {
        "type": "ready",
        "capabilities": ["message", "dm", "reply", "like"],
    })

    return sock, config


async def run_adapter():
    """Main adapter loop: Telegram bot + daemon IPC."""
    from telegram import Update
    from telegram.ext import Application, MessageHandler, filters, ContextTypes

    # Connect to daemon
    daemon_sock, config = connect_to_daemon()

    # Get bot token from env (injected by core after decryption)
    bot_token = os.environ.get("KOGNISANT_BOT_TOKEN") or os.environ.get("KOGNISANT_TELEGRAM_TOKEN")
    if not bot_token:
        logger.error("No bot token. Set KOGNISANT_BOT_TOKEN or KOGNISANT_TELEGRAM_TOKEN env var.")
        sys.exit(1)

    # Build Telegram application
    app = Application.builder().token(bot_token).build()

    # Queues for async message passing
    event_queue: asyncio.Queue = asyncio.Queue()

    async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Any incoming text message → emit event to daemon."""
        if not update.message or not update.message.text:
            return
        msg = update.message
        user = msg.from_user
        event = {
            "type": "event",
            "event_id": str(msg.message_id),
            "platform": "telegram",
            "event_type": "dm" if msg.chat.type == "private" else "message",
            "sender_id": f"tg:{user.id}",
            "sender_name": user.first_name or user.username or str(user.id),
            "content": msg.text,
            "timestamp": msg.date.isoformat() if msg.date else datetime.now(timezone.utc).isoformat(),
            "context": {
                "chat_id": msg.chat_id,
                "chat_type": msg.chat.type,
                "message_id": msg.message_id,
            },
            "reply_to": str(msg.reply_to_message.message_id) if msg.reply_to_message else None,
            "attachments": [],
        }
        await event_queue.put(event)

    # Handle both regular text and /commands
    app.add_handler(MessageHandler(filters.TEXT, on_message))

    # ─── Background Tasks ──────────────────────────────────────────────────

    async def event_sender():
        """Drain event queue → send to daemon over UDS."""
        while not _shutdown:
            try:
                event = await asyncio.wait_for(event_queue.get(), timeout=1.0)
                proto_send(daemon_sock, event)
            except asyncio.TimeoutError:
                continue
            except (OSError, BrokenPipeError):
                logger.error("Daemon connection lost (event_sender)")
                return

    async def action_reader():
        """Read actions from daemon → execute on Telegram."""
        global _shutdown
        loop = asyncio.get_event_loop()

        while not _shutdown:
            msg = await loop.run_in_executor(None, _blocking_recv, daemon_sock)
            if msg is None:
                await asyncio.sleep(0.1)
                continue

            msg_type = msg.get("type")
            if msg_type == "action":
                await _execute_action(msg, app.bot, daemon_sock)
            elif msg_type == "heartbeat":
                proto_send(daemon_sock, {
                    "type": "heartbeat_ack",
                    "seq": msg.get("seq", 0),
                    "ts": datetime.now(timezone.utc).isoformat(),
                })
            elif msg_type == "heartbeat_ack":
                pass
            elif msg_type == "shutdown":
                logger.info("Shutdown from daemon: %s", msg.get("reason", ""))
                _shutdown = True
                return
            elif msg_type == "config_reload":
                path = msg.get("config_path")
                if path and os.path.exists(path):
                    with open(path) as f:
                        config.update(json.load(f))
                    logger.info("Config reloaded")

    async def heartbeat_sender():
        """Send periodic heartbeats to daemon."""
        seq = 0
        while not _shutdown:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            seq += 1
            try:
                proto_send(daemon_sock, {
                    "type": "heartbeat",
                    "seq": seq,
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "status": "healthy",
                })
            except (OSError, BrokenPipeError):
                logger.error("Heartbeat failed — daemon gone")
                return

    # ─── Run Everything ────────────────────────────────────────────────────

    async with app:
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        logger.info("Telegram adapter running")

        tasks = [
            asyncio.create_task(event_sender()),
            asyncio.create_task(action_reader()),
            asyncio.create_task(heartbeat_sender()),
        ]

        while not _shutdown:
            await asyncio.sleep(1.0)

        for t in tasks:
            t.cancel()
        await app.updater.stop()
        await app.stop()

    daemon_sock.close()
    logger.info("Adapter shutdown complete")


async def _execute_action(action: dict, bot, daemon_sock: socket.socket):
    """Execute a single action on Telegram."""
    action_type = action.get("action_type")
    action_id = action.get("action_id", "?")
    content = action.get("content", "")
    metadata = action.get("metadata", {})
    chat_id = metadata.get("chat_id")
    target_id = action.get("target_id")

    success = False
    platform_id = None

    try:
        if action_type in ("reply", "post") and chat_id:
            chunks = _split_message(content, 4096)
            for chunk in chunks:
                kwargs = {"chat_id": chat_id, "text": chunk}
                parse_mode = metadata.get("parse_mode")
                if parse_mode:
                    kwargs["parse_mode"] = parse_mode
                if action_type == "reply" and target_id:
                    kwargs["reply_to_message_id"] = int(target_id)
                sent = await bot.send_message(**kwargs)
                platform_id = str(sent.message_id)
            success = True
        else:
            logger.warning("Unknown action_type: %s", action_type)
    except Exception as e:
        logger.error("Action %s (%s) failed: %s", action_id, action_type, e)

    try:
        proto_send(daemon_sock, {
            "type": "action_result",
            "action_id": action_id,
            "success": success,
            "platform_id": platform_id,
        })
    except OSError:
        pass


def _blocking_recv(sock: socket.socket) -> dict | None:
    """Blocking recv with 0.5s timeout — runs in executor."""
    sock.setblocking(True)
    sock.settimeout(0.5)
    try:
        header = _recv_exact(sock, 4)
        if header is None:
            return None
        length = struct.unpack(">I", header)[0]
        data = _recv_exact(sock, length)
        if data is None:
            return None
        return json.loads(data.decode("utf-8"))
    except (socket.timeout, OSError):
        return None


def _split_message(text: str, limit: int) -> list[str]:
    """Split text into chunks within platform character limit."""
    if len(text) <= limit:
        return [text]
    chunks = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        cut = remaining.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = remaining.rfind(" ", 0, limit)
        if cut < 1:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    return chunks


if __name__ == "__main__":
    asyncio.run(run_adapter())
