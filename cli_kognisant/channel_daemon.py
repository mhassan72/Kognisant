"""
Channel daemon integration — spawns adapters, routes events, manages lifecycle.

This module is imported by the daemon's _main_loop() to manage channel
adapters as persistent subprocesses with UDS IPC.

Uses only Python standard library per Requirement 13.
"""

import json
import logging
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

from .channels import (
    CHANNELS_DIR,
    LOGS_DIR,
    SESSIONS_DIR,
    SOCKET_DIR,
    ChannelAction,
    ChannelEvent,
    ChannelManager,
    ChannelRouter,
    ChannelServer,
    CredentialManager,
    ResponseFormatter,
    RouteDecision,
    SessionAuth,
    AuditLogger,
)
from .config import GLOBAL_CORE_DIR

logger = logging.getLogger(__name__)


class ChannelDaemonService:
    """Manages all active channel adapters within the daemon process.

    Responsibilities:
    - Spawn adapter subprocesses with correct venv, env vars, socket path
    - Accept adapter connections via UDS
    - Route events through ChannelRouter → chat.py or manager_respond()
    - Send actions back to adapters
    - Monitor heartbeats, restart dead adapters
    - Handle channel state transitions (start/stop/pause)
    """

    def __init__(self):
        self.manager = ChannelManager()
        self.session_auth = SessionAuth()
        self._servers: dict[str, ChannelServer] = {}    # name → ChannelServer
        self._processes: dict[str, subprocess.Popen] = {}  # name → Popen
        self._audit_loggers: dict[str, AuditLogger] = {}
        self._last_poll: float = 0
        self._poll_interval: float = 5.0  # Check channel states every 5s

    def poll(self) -> None:
        """Called by daemon main loop every cycle. Manages channel lifecycle."""
        now = time.monotonic()
        if now - self._last_poll < self._poll_interval:
            return
        self._last_poll = now

        channels = self.manager.list_channels()

        for ch in channels:
            name = ch.get("name", "")
            state = ch.get("state", "stopped")

            if state == "starting":
                self._start_channel(ch)
            elif state == "running":
                self._check_running(ch)
            elif state == "stopped":
                self._ensure_stopped(name)

        # Process incoming events from all connected adapters
        self._process_events()

    def _start_channel(self, channel: dict) -> None:
        """Spawn adapter subprocess and set up UDS server."""
        name = channel["name"]
        platform = channel.get("platform", "")

        # Already running?
        if name in self._servers and self._servers[name].connected:
            self.manager.update_state(name, "running")
            return

        # Find adapter script
        script_path = self._resolve_adapter_script(platform)
        if not script_path:
            logger.error("Channel '%s': No adapter script for platform '%s'", name, platform)
            self.manager.update_state(name, "error")
            return

        # Find Python (venv or system)
        python_path = self._resolve_python(platform)

        # Write temp config
        try:
            config_path = self.manager.write_temp_config(name)
        except (ValueError, OSError) as e:
            logger.error("Channel '%s': Failed to write config: %s", name, e)
            self.manager.update_state(name, "error")
            return

        # Set up UDS server
        server = ChannelServer(name)
        try:
            server.start()
        except OSError as e:
            logger.error("Channel '%s': Failed to create UDS: %s", name, e)
            self.manager.update_state(name, "error")
            return

        # Build environment (core decrypts → env var injection)
        env = os.environ.copy()
        env["KOGNISANT_SOCKET"] = server.socket_path
        env["KOGNISANT_CONFIG_PATH"] = config_path

        # Decrypt and inject credentials
        passphrase = self._get_master_passphrase()
        if passphrase:
            cred_env = CredentialManager.get_env_vars(name, passphrase)
            env.update(cred_env)

        # Spawn adapter process
        log_path = os.path.join(LOGS_DIR, f"{name}.log")
        os.makedirs(LOGS_DIR, exist_ok=True)

        try:
            log_fd = open(log_path, "a")
            proc = subprocess.Popen(
                [python_path, script_path],
                env=env,
                stdout=log_fd,
                stderr=log_fd,
                stdin=subprocess.DEVNULL,
                cwd=os.path.expanduser("~"),
            )
            logger.info("Channel '%s': Adapter spawned (PID %d)", name, proc.pid)
        except OSError as e:
            logger.error("Channel '%s': Failed to spawn adapter: %s", name, e)
            server.close()
            self.manager.update_state(name, "error")
            return

        self._servers[name] = server
        self._processes[name] = proc
        self._audit_loggers[name] = AuditLogger(name)

        # Wait briefly for adapter to connect
        # (The actual handshake happens in _check_running on next poll)
        self.manager.update_state(name, "running", pid=proc.pid)

    def _check_running(self, channel: dict) -> None:
        """Check health of a running channel — accept connection, handle heartbeats."""
        name = channel["name"]
        server = self._servers.get(name)
        proc = self._processes.get(name)

        if not server or not proc:
            self.manager.update_state(name, "stopped")
            return

        # Check if process is still alive
        if proc.poll() is not None:
            logger.warning("Channel '%s': Adapter process died (exit %s)", name, proc.returncode)
            server.close()
            del self._servers[name]
            del self._processes[name]
            # Restart by setting back to "starting"
            self.manager.update_state(name, "starting")
            return

        # If not connected yet, try to accept
        if not server.connected:
            if server.accept():
                config_path = os.path.join(SOCKET_DIR, f"kognisant_cfg_{name}.json")
                if not server.handshake(config_path):
                    logger.warning("Channel '%s': Handshake failed, will retry", name)
            return

        # Send daemon heartbeat
        server.send_heartbeat()

        # Check adapter health
        if not server.check_health():
            logger.warning("Channel '%s': Adapter heartbeat timeout, restarting", name)
            self._kill_channel(name)
            self.manager.update_state(name, "starting")

    def _process_events(self) -> None:
        """Read events from all connected adapters and route them."""
        for name, server in list(self._servers.items()):
            if not server.connected:
                continue

            # Read up to 10 events per poll cycle per channel
            for _ in range(10):
                msg = server.recv_message()
                if msg is None:
                    break

                if msg.get("type") == "event":
                    self._route_event(name, msg)
                elif msg.get("type") == "action_result":
                    logger.debug("Channel '%s': Action result: %s", name, msg.get("action_id"))
                elif msg.get("type") == "error":
                    logger.warning("Channel '%s': Adapter error: %s", name, msg.get("message"))

    def _route_event(self, channel_name: str, raw_event: dict) -> None:
        """Route an event through ChannelRouter and generate response."""
        channel = self.manager.get_channel(channel_name)
        if not channel:
            return

        event = ChannelEvent.from_dict(raw_event)
        decision = ChannelRouter.route(event, channel)

        if decision == RouteDecision.DROP:
            return

        if decision == RouteDecision.ESCALATE:
            self._escalate(channel_name, event)
            return

        if decision == RouteDecision.ASSISTANT:
            self._handle_assistant(channel_name, channel, event)
        elif decision == RouteDecision.MANAGER:
            self._handle_manager(channel_name, channel, event)

    def _handle_assistant(self, channel_name: str, channel: dict, event: ChannelEvent) -> None:
        """Process event in assistant mode — full chat.py pipeline."""
        # Check session auth if required
        security = channel.get("security", {})
        if security.get("session_required"):
            if not self.session_auth.is_authenticated(channel_name):
                # Check if this message is a PIN attempt
                if event.content.strip().isdigit() and len(event.content.strip()) <= 8:
                    if self.session_auth.activate(channel_name, event.content.strip()):
                        self._send_reply(channel_name, event, "✅ Session activated.")
                    else:
                        self._send_reply(channel_name, event, "❌ Invalid PIN.")
                    return
                else:
                    self._send_reply(channel_name, event, "🔒 Session auth required. Send your PIN.")
                    return

        # Audit
        audit = self._audit_loggers.get(channel_name)
        if audit:
            audit.log(event.sender_id, "assistant_message", event.content[:200])

        # Process through chat pipeline
        response = self._run_chat_pipeline(channel, event)

        if response:
            platform = channel.get("platform", "telegram")
            chunks = ResponseFormatter.format(response, platform)
            for chunk in chunks:
                self._send_reply(channel_name, event, chunk)

    def _handle_manager(self, channel_name: str, channel: dict, event: ChannelEvent) -> None:
        """Process event in manager mode — zero-tool LLM or template."""
        # For now, basic echo response (Phase 2a will add full SMM engine)
        # This stub ensures the routing works end-to-end
        response = f"[Manager mode — Phase 2a pending] Received: {event.content[:100]}"
        self._send_reply(channel_name, event, response)

    def _send_reply(self, channel_name: str, event: ChannelEvent, content: str) -> None:
        """Send a reply action back to the adapter."""
        server = self._servers.get(channel_name)
        if not server:
            return

        action = ChannelAction(
            action_id=f"act_{int(time.time() * 1000)}",
            action_type="reply",
            target_id=event.event_id,
            content=content,
            metadata={
                "chat_id": event.context.get("chat_id"),
                "parse_mode": "Markdown",
            },
        )
        server.send_message(action.to_dict())

    def _run_chat_pipeline(self, channel: dict, event: ChannelEvent) -> str | None:
        """Run the message through chat.py's pipeline (simplified for Phase 1).

        This injects the remote message as if the user typed it in the CLI,
        processes it with full tool access, and returns the response text.
        """
        from .config import get_compiled_models, load_project_context
        from .network import query_model_api

        project_root = channel.get("assistant_config", {}).get("project_root")
        if not project_root:
            project_root = os.path.expanduser("~")

        # Build messages for the LLM
        system_prompt = (
            "You are Kognisant, an AI CLI assistant. The user is messaging you remotely "
            "via a messaging platform. Respond helpfully and concisely. "
            "You have full access to their project context and tools."
        )

        # Load project context if available
        context = ""
        if project_root and os.path.isdir(os.path.join(project_root, ".kognisant")):
            ctx = load_project_context(project_root)
            if ctx:
                context = f"\n\nProject context:\n{ctx[:2000]}"

        messages = [
            {"role": "system", "content": system_prompt + context},
            {"role": "user", "content": event.content},
        ]

        # Get model
        try:
            compiled_models = get_compiled_models()
            if not compiled_models:
                return "⚠️ No models configured. Run `kognisant setup` to add one."
            model = compiled_models[0]
        except Exception:
            return "⚠️ Failed to load model configuration."

        # Query LLM
        try:
            response = query_model_api(
                model.get("api_base_url", ""),
                model.get("api_key", ""),
                model.get("name", ""),
                messages,
                protocol=model.get("protocol", "openai"),
            )
            return response if response else "⚠️ Empty response from model."
        except Exception as e:
            logger.error("Channel '%s': LLM query failed: %s", channel.get("name"), e)
            return f"⚠️ Error: {type(e).__name__}"

    def _escalate(self, channel_name: str, event: ChannelEvent) -> None:
        """Write event to escalation queue."""
        from .channels import ESCALATIONS_FILE
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "channel": channel_name,
            "event": event.to_dict(),
            "status": "pending",
        }
        try:
            with open(ESCALATIONS_FILE, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError:
            pass

    def _ensure_stopped(self, name: str) -> None:
        """Make sure a stopped channel's adapter is actually dead."""
        if name in self._processes:
            self._kill_channel(name)

    def _kill_channel(self, name: str) -> None:
        """Kill adapter process and clean up."""
        server = self._servers.pop(name, None)
        proc = self._processes.pop(name, None)

        if server:
            server.send_shutdown(reason="channel_stopped")
            time.sleep(0.5)
            server.close()

        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    def _resolve_adapter_script(self, platform: str) -> str | None:
        """Find adapter script — check user scripts dir, then bundled adapters."""
        # Check user's scripts dir first
        user_script = os.path.join(GLOBAL_CORE_DIR, "scripts", f"channel_{platform}.py")
        if os.path.exists(user_script):
            return user_script

        # Fall back to bundled reference adapter
        bundled = os.path.join(
            os.path.dirname(__file__), "adapters", f"channel_{platform}.py"
        )
        if os.path.exists(bundled):
            # Copy to user scripts dir on first use
            os.makedirs(os.path.join(GLOBAL_CORE_DIR, "scripts"), exist_ok=True)
            shutil.copy2(bundled, user_script)
            os.chmod(user_script, 0o755)
            logger.info("Installed reference adapter: %s → %s", bundled, user_script)

            # Also copy requirements file if exists
            req_file = bundled.replace(".py", "_requirements.txt")
            if os.path.exists(req_file):
                shutil.copy2(req_file, user_script.replace(".py", "_requirements.txt"))

            return user_script

        return None

    def _resolve_python(self, platform: str) -> str:
        """Find the Python interpreter for an adapter's venv."""
        venv_python = os.path.join(
            GLOBAL_CORE_DIR, "scripts", f"channel_{platform}_venv", "bin", "python"
        )
        if os.path.exists(venv_python):
            return venv_python
        return sys.executable

    def _get_master_passphrase(self) -> str:
        """Get master passphrase for credential decryption.

        In daemon context, this should be cached from daemon start.
        For now, try environment variable.
        """
        return os.environ.get("KOGNISANT_MASTER_PASSPHRASE", "")

    def shutdown_all(self) -> None:
        """Stop all running channel adapters (called on daemon shutdown)."""
        for name in list(self._servers.keys()):
            self._kill_channel(name)
