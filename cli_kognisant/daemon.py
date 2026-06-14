"""
Daemon process management for the Autonomous Execution Engine.

Handles forking, PID file management, signal handling, and log access.
Uses only Python standard library modules per Requirement 13.
"""

import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

# Module-level constants
CORE_DIR = os.path.expanduser("~/.kognisant_core")
PID_FILE = os.path.join(CORE_DIR, "daemon.pid")
LOG_FILE = os.path.join(CORE_DIR, "daemon.log")

# Daemon state flags (set by signal handlers in the forked child)
_shutdown_flag = False
_reload_flag = False


def _sigterm_handler(signum, frame):
    """Handle SIGTERM: set graceful shutdown flag (R12-AC1)."""
    global _shutdown_flag
    _shutdown_flag = True


def _sighup_handler(signum, frame):
    """Handle SIGHUP: set reload flag to re-read job queue (R12-AC5)."""
    global _reload_flag
    _reload_flag = True


def _setup_daemon_logging():
    """Configure logging to write to LOG_FILE with ISO 8601 timestamps (R1-AC8).

    Format: YYYY-MM-DDTHH:MM:SS level message
    """
    os.makedirs(CORE_DIR, exist_ok=True)

    handler = logging.FileHandler(LOG_FILE)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(message)s",
                          datefmt="%Y-%m-%dT%H:%M:%S")
    )

    daemon_logger = logging.getLogger("kognisant.daemon")
    daemon_logger.setLevel(logging.INFO)
    # Remove any existing handlers to avoid duplicate entries
    daemon_logger.handlers.clear()
    daemon_logger.addHandler(handler)
    return daemon_logger


def _run_agent_task(task_description: str) -> dict:
    """Placeholder for PERP swarm execution.

    This function will be replaced when the swarm module is implemented.
    For now it simulates agent task execution.

    Args:
        task_description: The task to execute.

    Returns:
        Dict with keys: completed (bool), output (str), error (str|None).
    """
    # Placeholder — will be replaced by actual PERP swarm invocation
    return {
        "completed": False,
        "output": "",
        "error": f"PERP swarm not yet implemented. Task: {task_description}",
    }


class ProcessManager:
    """Manages subprocess spawning and lifecycle for job execution.

    Provides static methods to spawn script subprocesses, check liveness,
    and gracefully terminate processes.
    """

    @staticmethod
    def spawn(script_path: str, env: dict, job_context: dict) -> subprocess.Popen:
        """Spawn a script subprocess with environment and JSON stdin context.

        Executes the script using sys.executable (R10-AC5), sets env vars
        from the job definition (R10-AC2), and passes job_context as JSON
        on stdin (R10-AC1).

        Args:
            script_path: Absolute path to the Python script to execute.
            env: Environment variables to set in the subprocess.
            job_context: Dict with {job_name, job_type, env_vars, timestamp}.

        Returns:
            The subprocess.Popen object.
        """
        # Build subprocess environment: inherit current env + job env vars
        proc_env = os.environ.copy()
        proc_env.update(env)

        # Serialize job context to JSON for stdin (R10-AC1)
        stdin_data = json.dumps(job_context)

        proc = subprocess.Popen(
            [sys.executable, script_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=proc_env,
            start_new_session=True,
        )

        # Write JSON context to stdin and close it so the script can proceed
        try:
            proc.stdin.write(stdin_data.encode("utf-8"))
            proc.stdin.close()
        except (OSError, BrokenPipeError):
            pass

        return proc

    @staticmethod
    def is_alive(pid: int) -> bool:
        """Check if a process with the given PID is alive.

        Uses os.kill(pid, 0) which sends no signal but checks existence.

        Args:
            pid: The process ID to check.

        Returns:
            True if the process is alive, False otherwise.
        """
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            # Process exists but we don't own it — treat as alive
            return True

    @staticmethod
    def kill_gracefully(pid: int, timeout: int = 10) -> None:
        """Terminate a process gracefully with SIGTERM, escalating to SIGKILL.

        Sends SIGTERM first, waits up to `timeout` seconds for the process
        to exit, then sends SIGKILL if still alive (R12-AC3).

        Args:
            pid: The process ID to terminate.
            timeout: Seconds to wait after SIGTERM before sending SIGKILL.
        """
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return  # Already dead
        except PermissionError:
            return

        # Wait for process to exit
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not ProcessManager.is_alive(pid):
                return
            time.sleep(0.2)

        # Still alive — escalate to SIGKILL
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError:
            pass


def _rotate_log_if_needed(log_path: str):
    """Rotate a log file if it exceeds 10 MB (R4-AC2).

    Renames current log to {name}.log.1 and starts a fresh file.
    """
    try:
        if os.path.exists(log_path) and os.path.getsize(log_path) > 10 * 1024 * 1024:
            rotated_path = log_path + ".1"
            # Remove old rotated file if it exists
            if os.path.exists(rotated_path):
                os.remove(rotated_path)
            os.rename(log_path, rotated_path)
    except OSError:
        pass


def _append_to_log(log_path: str, content: str, prefix: str = ""):
    """Append content to a log file, optionally prefixing each line.

    Args:
        log_path: Path to the log file.
        content: Text content to append.
        prefix: Optional prefix for each line (e.g., "[ERROR] ").
    """
    if not content:
        return
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    try:
        with open(log_path, "a") as f:
            for line in content.splitlines(keepends=True):
                if prefix:
                    f.write(f"{prefix}{line}")
                else:
                    f.write(line)
            # Ensure trailing newline
            if content and not content.endswith("\n"):
                f.write("\n")
    except OSError:
        pass


def _check_missing_env_vars(job: dict, logger):
    """Warn if required env_vars from script metadata are missing (R10-AC6).

    Reads the script's .json metadata file to determine required env vars,
    then checks if they are provided in the job's env_vars.
    """
    script_path = job.get("script_path", "")
    if not script_path:
        return

    scripts_dir = os.path.join(CORE_DIR, "scripts")
    # Derive metadata path from script path
    script_name = os.path.splitext(os.path.basename(script_path))[0]
    metadata_path = os.path.join(scripts_dir, f"{script_name}.json")

    if not os.path.exists(metadata_path):
        return

    try:
        with open(metadata_path, "r") as f:
            metadata = json.load(f)
        required_vars = metadata.get("env_vars", [])
        job_env = job.get("env_vars", {})
        missing = [v for v in required_vars if v not in job_env]
        if missing:
            logger.warning(
                "Job '%s': missing required env vars: %s",
                job.get("name"), ", ".join(missing),
            )
    except (OSError, json.JSONDecodeError):
        pass


def _build_job_context(job: dict) -> dict:
    """Build the stdin JSON context for a script (R10-AC1).

    Returns dict with job_name, job_type, env_vars, and timestamp (ISO 8601 UTC).
    """
    return {
        "job_name": job.get("name", ""),
        "job_type": job.get("type", ""),
        "env_vars": job.get("env_vars", {}),
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _resolve_script_path(job: dict) -> str | None:
    """Resolve the absolute path to a job's script.

    Returns the absolute path if the script exists, None otherwise.
    """
    script_path = job.get("script_path", "")
    if not script_path:
        return None

    # If already absolute, use as-is
    if os.path.isabs(script_path):
        return script_path if os.path.exists(script_path) else None

    # Resolve relative to scripts directory
    scripts_dir = os.path.join(CORE_DIR, "scripts")
    abs_path = os.path.join(scripts_dir, script_path)
    return abs_path if os.path.exists(abs_path) else None


def _main_loop():
    """Main daemon polling loop.

    Polls the job queue every 15 seconds (R2-AC1) and manages:
    - Scheduled job execution (R3)
    - Persistent job lifecycle (R4)
    - Agent job execution (R5)
    - Graceful shutdown (R12)
    - Log rotation (R4-AC2)
    """
    global _shutdown_flag, _reload_flag

    from .jobs import JobQueue

    logger = _setup_daemon_logging()
    logger.info("Daemon started with PID %d", os.getpid())

    job_queue = JobQueue()
    logs_dir = os.path.join(CORE_DIR, "logs")
    os.makedirs(logs_dir, exist_ok=True)

    # Track running processes: {job_name: subprocess.Popen}
    running_scheduled: dict[str, subprocess.Popen] = {}
    # Track scheduled job start times for timeout: {job_name: float}
    scheduled_start_times: dict[str, float] = {}
    # Track running persistent processes: {job_name: subprocess.Popen}
    running_persistent: dict[str, subprocess.Popen] = {}
    # Track restart delay state: {job_name: float (time to restart after)}
    restart_pending: dict[str, float] = {}
    # Track agent job threads: {job_name: threading.Thread}
    agent_threads: dict[str, threading.Thread] = {}
    # Track agent job start times: {job_name: float}
    agent_start_times: dict[str, float] = {}

    while not _shutdown_flag:
        # --- (a) Check reload flag (R12-AC5) ---
        if _reload_flag:
            logger.info("Received SIGHUP, reloading job queue")
            _reload_flag = False
            # Re-instantiate job_queue to pick up changes
            job_queue = JobQueue()

        try:
            now = datetime.now(timezone.utc)

            # --- (b) Execute due scheduled jobs (R3-AC1) ---
            due_jobs = job_queue.get_due_scheduled(now)
            for job in due_jobs:
                name = job.get("name", "")

                # Skip if job is already running (R3-AC6)
                if name in running_scheduled:
                    proc = running_scheduled[name]
                    if proc.poll() is None:
                        logger.warning(
                            "Scheduled job '%s' still running, skipping overlapping execution",
                            name,
                        )
                        continue

                # Resolve script path
                abs_script = _resolve_script_path(job)
                if abs_script is None:
                    # Missing script (R3-AC7)
                    logger.error(
                        "Scheduled job '%s': script not found at '%s'",
                        name, job.get("script_path", ""),
                    )
                    job_queue.update_status(name, "failed")
                    continue

                # Warn about missing env vars (R10-AC6)
                _check_missing_env_vars(job, logger)

                # Spawn the subprocess (R3-AC1)
                context = _build_job_context(job)
                try:
                    proc = ProcessManager.spawn(
                        abs_script, job.get("env_vars", {}), context
                    )
                    running_scheduled[name] = proc
                    scheduled_start_times[name] = time.monotonic()
                    job_queue.update_status(name, "running", pid=proc.pid)
                    logger.info("Scheduled job '%s' started (PID %d)", name, proc.pid)
                except OSError as e:
                    logger.error(
                        "Scheduled job '%s': failed to spawn: %s", name, e
                    )
                    job_queue.update_status(name, "failed")

            # --- Check completed/timed-out scheduled jobs ---
            completed_scheduled = []
            for name, proc in running_scheduled.items():
                returncode = proc.poll()
                if returncode is not None:
                    # Process has exited
                    log_path = os.path.join(logs_dir, f"{name}.log")
                    stdout_data = ""
                    stderr_data = ""
                    try:
                        stdout_data = proc.stdout.read().decode("utf-8", errors="replace")
                        stderr_data = proc.stderr.read().decode("utf-8", errors="replace")
                    except (OSError, ValueError):
                        pass

                    # Capture output to log (R3-AC4)
                    _append_to_log(log_path, stdout_data)
                    _append_to_log(log_path, stderr_data, prefix="[ERROR] ")

                    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
                    if returncode == 0:
                        # Success (R3-AC2)
                        job_queue.update_status(
                            name, "scheduled", last_run_at=now_str, pid=None
                        )
                        logger.info("Scheduled job '%s' completed successfully", name)
                    else:
                        # Failure (R3-AC3)
                        last_stderr = "\n".join(stderr_data.splitlines()[-20:])
                        job_queue.update_status(
                            name, "scheduled", last_run_at=now_str, pid=None
                        )
                        logger.error(
                            "Scheduled job '%s' failed with exit code %d. "
                            "Last stderr:\n%s",
                            name, returncode, last_stderr,
                        )
                    completed_scheduled.append(name)
                else:
                    # Check timeout: 3600s (R3-AC8)
                    start_time = scheduled_start_times.get(name, 0)
                    if time.monotonic() - start_time > 3600:
                        logger.error(
                            "Scheduled job '%s' timed out after 3600s, terminating",
                            name,
                        )
                        ProcessManager.kill_gracefully(proc.pid, timeout=10)
                        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
                        job_queue.update_status(
                            name, "scheduled", last_run_at=now_str, pid=None
                        )
                        completed_scheduled.append(name)

            for name in completed_scheduled:
                running_scheduled.pop(name, None)
                scheduled_start_times.pop(name, None)

            # --- (c) Start pending persistent jobs (R4-AC1) ---
            pending_jobs = job_queue.get_pending_jobs()
            for job in pending_jobs:
                if job.get("type") != "persistent":
                    continue
                name = job.get("name", "")

                # Skip if already tracked
                if name in running_persistent:
                    continue

                abs_script = _resolve_script_path(job)
                if abs_script is None:
                    # Missing script (R2-AC8)
                    logger.error(
                        "Persistent job '%s': script not found at '%s'",
                        name, job.get("script_path", ""),
                    )
                    job_queue.update_status(name, "failed")
                    continue

                _check_missing_env_vars(job, logger)
                context = _build_job_context(job)

                try:
                    proc = ProcessManager.spawn(
                        abs_script, job.get("env_vars", {}), context
                    )
                    running_persistent[name] = proc
                    # Store PID, update state to running (R2-AC6)
                    job_queue.update_status(name, "running", pid=proc.pid)
                    logger.info(
                        "Persistent job '%s' started (PID %d)", name, proc.pid
                    )
                except OSError as e:
                    logger.error(
                        "Persistent job '%s': failed to spawn: %s", name, e
                    )
                    job_queue.update_status(name, "failed")

            # --- (d) Monitor running persistent PIDs (R4-AC3-8) ---
            persistent_to_remove = []
            for name, proc in running_persistent.items():
                returncode = proc.poll()
                if returncode is None:
                    # Still running — check if cancelled (R4-AC8)
                    current_job = job_queue.get_job(name)
                    if current_job and current_job.get("state") == "cancelled":
                        logger.info(
                            "Persistent job '%s' cancelled, terminating", name
                        )
                        ProcessManager.kill_gracefully(proc.pid, timeout=10)
                        persistent_to_remove.append(name)
                    else:
                        # Log rotation for persistent jobs (R4-AC2)
                        log_path = os.path.join(logs_dir, f"{name}.log")
                        _rotate_log_if_needed(log_path)
                    continue

                # Process has exited — collect output
                log_path = os.path.join(logs_dir, f"{name}.log")
                stderr_data = ""
                try:
                    stdout_data = proc.stdout.read().decode("utf-8", errors="replace")
                    stderr_data = proc.stderr.read().decode("utf-8", errors="replace")
                    _append_to_log(log_path, stdout_data)
                    _append_to_log(log_path, stderr_data, prefix="[ERROR] ")
                except (OSError, ValueError):
                    pass

                if returncode == 0:
                    # Clean exit (R4-AC4)
                    job_queue.update_status(name, "completed", pid=None)
                    logger.info("Persistent job '%s' exited cleanly (code 0)", name)
                    persistent_to_remove.append(name)
                else:
                    # Crashed — check if cancelled first (R4-AC8)
                    current_job = job_queue.get_job(name)
                    if current_job and current_job.get("state") == "cancelled":
                        persistent_to_remove.append(name)
                        continue

                    # Check crash loop (R4-AC5, R4-AC6)
                    restart_timestamps = (
                        current_job.get("restart_timestamps", []) if current_job else []
                    )
                    restart_count = (
                        current_job.get("restart_count", 0) if current_job else 0
                    )

                    now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    restart_timestamps.append(now_ts)
                    restart_count += 1

                    # Rolling 60s window for crash_loop detection (R4-AC6)
                    now_mono = time.time()
                    recent_restarts = []
                    for ts_str in restart_timestamps:
                        try:
                            ts_dt = datetime.fromisoformat(
                                ts_str.replace("Z", "+00:00")
                            )
                            ts_epoch = ts_dt.timestamp()
                            if now_mono - ts_epoch <= 60:
                                recent_restarts.append(ts_str)
                        except (ValueError, TypeError):
                            pass

                    if len(recent_restarts) > 5:
                        # Crash loop detected (R4-AC6, R4-AC7)
                        last_stderr_lines = "\n".join(
                            stderr_data.splitlines()[-20:]
                        )
                        logger.error(
                            "Persistent job '%s' entered crash_loop: "
                            "%d restarts, last 20 lines of stderr:\n%s",
                            name,
                            restart_count,
                            last_stderr_lines,
                        )
                        job_queue.update_status(
                            name,
                            "crash_loop",
                            pid=None,
                            restart_count=restart_count,
                            restart_timestamps=restart_timestamps,
                        )
                        persistent_to_remove.append(name)
                    else:
                        # Schedule restart after 5s delay (R4-AC3)
                        logger.info(
                            "Persistent job '%s' crashed (exit code %d), "
                            "restarting in 5s",
                            name, returncode,
                        )
                        job_queue.update_status(
                            name,
                            "running",
                            pid=None,
                            restart_count=restart_count,
                            restart_timestamps=restart_timestamps,
                        )
                        restart_pending[name] = time.monotonic() + 5.0
                        persistent_to_remove.append(name)

            for name in persistent_to_remove:
                running_persistent.pop(name, None)

            # --- Handle pending restarts (5s delay) ---
            restart_ready = []
            for name, restart_at in restart_pending.items():
                if time.monotonic() >= restart_at:
                    restart_ready.append(name)

            for name in restart_ready:
                restart_pending.pop(name)
                job_data = job_queue.get_job(name)
                if not job_data:
                    continue
                # Don't restart if state changed (e.g., cancelled, crash_loop)
                if job_data.get("state") not in ("running", "pending"):
                    continue

                abs_script = _resolve_script_path(job_data)
                if abs_script is None:
                    logger.error(
                        "Persistent job '%s': script not found for restart", name
                    )
                    job_queue.update_status(name, "failed")
                    continue

                context = _build_job_context(job_data)
                try:
                    proc = ProcessManager.spawn(
                        abs_script, job_data.get("env_vars", {}), context
                    )
                    running_persistent[name] = proc
                    job_queue.update_status(name, "running", pid=proc.pid)
                    logger.info(
                        "Persistent job '%s' restarted (PID %d)", name, proc.pid
                    )
                except OSError as e:
                    logger.error(
                        "Persistent job '%s': restart failed: %s", name, e
                    )
                    job_queue.update_status(name, "failed")

            # --- (e) Start pending agent jobs (R5-AC1) ---
            for job in pending_jobs:
                if job.get("type") != "agent":
                    continue
                name = job.get("name", "")

                # Skip if already running
                if name in agent_threads:
                    continue

                task_desc = job.get("task", "")
                if not task_desc:
                    logger.error("Agent job '%s': no task description", name)
                    job_queue.update_status(name, "failed")
                    continue

                job_queue.update_status(name, "running")
                agent_start_times[name] = time.monotonic()

                def _agent_worker(job_name: str, task: str):
                    """Run agent task on background thread (R5-AC1)."""
                    log_path = os.path.join(logs_dir, f"{job_name}.log")
                    try:
                        result = _run_agent_task(task)
                        # Capture output (R5-AC4)
                        output = result.get("output", "")
                        _append_to_log(log_path, output)

                        if result.get("completed"):
                            # Success (R5-AC2)
                            finish_ts = datetime.now(timezone.utc).strftime(
                                "%Y-%m-%dT%H:%M:%S"
                            )
                            job_queue.update_status(
                                job_name, "completed", last_run_at=finish_ts
                            )
                            logger.info(
                                "Agent job '%s' completed at %s",
                                job_name, finish_ts,
                            )
                        else:
                            # Failure (R5-AC3)
                            error_msg = result.get("error", "Unknown error")
                            _append_to_log(
                                log_path, error_msg, prefix="[ERROR] "
                            )
                            job_queue.update_status(job_name, "failed")
                            logger.error(
                                "Agent job '%s' failed: %s",
                                job_name, error_msg[:200],
                            )
                    except Exception as e:
                        # Unhandled exception (R5-AC3)
                        error_msg = f"{type(e).__name__}: {e}"
                        _append_to_log(log_path, error_msg, prefix="[ERROR] ")
                        job_queue.update_status(job_name, "failed")
                        logger.error(
                            "Agent job '%s' exception: %s",
                            job_name, error_msg[:200],
                        )

                thread = threading.Thread(
                    target=_agent_worker,
                    args=(name, task_desc),
                    daemon=True,
                )
                thread.start()
                agent_threads[name] = thread
                logger.info("Agent job '%s' started on background thread", name)

            # --- Check agent job timeouts (R5-AC5) ---
            agent_completed = []
            for name, thread in agent_threads.items():
                if not thread.is_alive():
                    agent_completed.append(name)
                    continue

                start_time = agent_start_times.get(name, 0)
                elapsed = time.monotonic() - start_time
                if elapsed > 1800:  # 30 minutes
                    logger.error(
                        "Agent job '%s' timed out after %.0f seconds",
                        name, elapsed,
                    )
                    job_queue.update_status(name, "failed")
                    log_path = os.path.join(logs_dir, f"{name}.log")
                    _append_to_log(
                        log_path,
                        f"[ERROR] Agent job timed out after {elapsed:.0f}s\n",
                    )
                    agent_completed.append(name)

            for name in agent_completed:
                agent_threads.pop(name, None)
                agent_start_times.pop(name, None)

            # --- (f) Log rotation for persistent jobs (R4-AC2) ---
            # Already handled inline during monitoring above, but also
            # check for any persistent jobs we haven't polled output from
            for name in list(running_persistent.keys()):
                log_path = os.path.join(logs_dir, f"{name}.log")
                _rotate_log_if_needed(log_path)

        except Exception as e:
            logger.error("Error in polling cycle: %s", e)

        # --- Sleep for 15s polling interval with responsive shutdown check ---
        for _ in range(30):
            if _shutdown_flag:
                break
            time.sleep(0.5)

    # --- Graceful shutdown sequence (R12-AC1,2,3,4) ---
    logger.info("Daemon shutting down gracefully (PID %d)", os.getpid())

    # Send SIGTERM to all running subprocesses (R12-AC2)
    all_pids = []
    for name, proc in running_scheduled.items():
        if proc.poll() is None:
            all_pids.append((name, proc.pid))
            try:
                os.kill(proc.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass

    for name, proc in running_persistent.items():
        if proc.poll() is None:
            all_pids.append((name, proc.pid))
            try:
                os.kill(proc.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass

    # Wait up to 10s for each, then SIGKILL (R12-AC3)
    if all_pids:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            still_alive = [
                (name, pid) for name, pid in all_pids
                if ProcessManager.is_alive(pid)
            ]
            if not still_alive:
                break
            time.sleep(0.2)
        else:
            # SIGKILL remaining
            for name, pid in still_alive:
                logger.warning("Force-killing process %d (job '%s')", pid, name)
                try:
                    os.kill(pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass

    # Wait for agent threads to finish (best effort)
    for name, thread in agent_threads.items():
        thread.join(timeout=5)

    logger.info("Daemon shutdown complete")


class DaemonManager:
    """Manages the background daemon process lifecycle.

    Provides static methods for starting, stopping, querying status,
    checking liveness, and reading daemon logs.
    """

    @staticmethod
    def start() -> int:
        """Fork a background daemon process.

        Ensures ~/.kognisant_core/ exists, checks for existing daemon,
        forks via os.fork(), and sets up the child process.

        Returns:
            The child (daemon) PID.

        Raises:
            RuntimeError: If the daemon is already running (R1-AC7).
        """
        # Ensure core directory exists (R1-AC1)
        os.makedirs(CORE_DIR, exist_ok=True)

        # Check if daemon already running (R1-AC7)
        if DaemonManager.is_running():
            existing_pid = DaemonManager._read_pid()
            print(
                f"Error: Daemon is already running with PID {existing_pid}",
                file=sys.stderr,
            )
            raise RuntimeError(
                f"Daemon already running with PID {existing_pid}"
            )

        # Fork the daemon process
        child_pid = os.fork()

        if child_pid > 0:
            # Parent process
            print(f"Daemon started with PID {child_pid}")  # R1-AC2
            return child_pid
        else:
            # Child process — become session leader
            os.setsid()

            # Write PID file
            with open(PID_FILE, "w") as f:
                f.write(str(os.getpid()))

            # Set up signal handlers (R12-AC1, R12-AC5)
            signal.signal(signal.SIGTERM, _sigterm_handler)
            signal.signal(signal.SIGHUP, _sighup_handler)

            # Run the main loop
            try:
                _main_loop()
            except Exception as e:
                # Log unexpected errors before exiting
                logger = logging.getLogger("kognisant.daemon")
                logger.error("Daemon crashed: %s", e)
            finally:
                # Clean up PID file on normal exit
                # Per R12-AC6: if daemon crashes unexpectedly, PID file
                # remains for stale detection. But on graceful shutdown
                # we clean it up (R12-AC4).
                if _shutdown_flag:
                    try:
                        os.remove(PID_FILE)
                    except OSError:
                        pass

            os._exit(0)

    @staticmethod
    def stop() -> bool:
        """Stop the running daemon by sending SIGTERM.

        Reads the PID file, sends SIGTERM to the daemon process,
        and removes the PID file.

        Returns:
            True if the daemon was successfully stopped.
        """
        # Check if PID file exists (R1-AC9)
        if not os.path.exists(PID_FILE):
            print("Error: No daemon is currently running", file=sys.stderr)
            return False

        pid = DaemonManager._read_pid()
        if pid is None:
            print("Error: No daemon is currently running", file=sys.stderr)
            return False

        try:
            os.kill(pid, signal.SIGTERM)  # R1-AC3
        except ProcessLookupError:
            # Process already dead — clean up stale PID file
            pass
        except PermissionError:
            print(
                f"Error: Permission denied sending signal to PID {pid}",
                file=sys.stderr,
            )
            return False

        # Remove PID file
        try:
            os.remove(PID_FILE)
        except OSError:
            pass

        return True

    @staticmethod
    def status() -> dict:
        """Get daemon status information.

        Returns:
            Dict with keys: running (bool), pid (int|None), uptime (str|None).
            Detects and removes stale PID files (R1-AC6).
        """
        if not os.path.exists(PID_FILE):
            return {"running": False, "pid": None, "uptime": None}

        pid = DaemonManager._read_pid()
        if pid is None:
            return {"running": False, "pid": None, "uptime": None}

        # Check if process is alive (R1-AC4)
        if DaemonManager._process_alive(pid):
            uptime = DaemonManager._get_uptime()
            return {"running": True, "pid": pid, "uptime": uptime}
        else:
            # Stale PID file — process is dead (R1-AC6)
            try:
                os.remove(PID_FILE)
            except OSError:
                pass
            return {"running": False, "pid": None, "uptime": None}

    @staticmethod
    def is_running() -> bool:
        """Check if the daemon is currently running.

        Uses PID file + os.kill(pid, 0) for verification.
        Cleans stale PID files if process is dead (R1-AC6).

        Returns:
            True if daemon is running.
        """
        if not os.path.exists(PID_FILE):
            return False

        pid = DaemonManager._read_pid()
        if pid is None:
            return False

        if DaemonManager._process_alive(pid):
            return True

        # Stale PID file — clean up (R1-AC6)
        try:
            os.remove(PID_FILE)
        except OSError:
            pass
        return False

    @staticmethod
    def read_logs(lines: int = 50) -> str:
        """Read the last N lines from the daemon log file.

        Args:
            lines: Number of lines to return (default 50, per R1-AC5).

        Returns:
            The last N lines of the log, or a message if no log exists (R1-AC10).
        """
        if not os.path.exists(LOG_FILE):
            return "No log file available. The daemon may not have been started yet."

        try:
            with open(LOG_FILE, "r") as f:
                all_lines = f.readlines()
            tail = all_lines[-lines:] if len(all_lines) > lines else all_lines
            return "".join(tail)
        except OSError:
            return "Error reading log file."

    # --- Private helpers ---

    @staticmethod
    def _read_pid() -> int | None:
        """Read and parse the PID from the PID file."""
        try:
            with open(PID_FILE, "r") as f:
                content = f.read().strip()
            return int(content)
        except (OSError, ValueError):
            return None

    @staticmethod
    def _process_alive(pid: int) -> bool:
        """Check if a process with the given PID is alive.

        Uses os.kill(pid, 0) which sends no signal but checks existence.
        """
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            # Process exists but we don't own it — treat as alive
            return True

    @staticmethod
    def _get_uptime() -> str | None:
        """Calculate daemon uptime from PID file modification time."""
        try:
            mtime = os.path.getmtime(PID_FILE)
            started_at = datetime.fromtimestamp(mtime, tz=timezone.utc)
            now = datetime.now(tz=timezone.utc)
            delta = now - started_at

            total_seconds = int(delta.total_seconds())
            hours, remainder = divmod(total_seconds, 3600)
            minutes, seconds = divmod(remainder, 60)

            if hours > 0:
                return f"{hours}h {minutes}m {seconds}s"
            elif minutes > 0:
                return f"{minutes}m {seconds}s"
            else:
                return f"{seconds}s"
        except OSError:
            return None
