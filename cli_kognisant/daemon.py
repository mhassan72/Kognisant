"""
Daemon process management for the Autonomous Execution Engine.

Handles forking, PID file management, signal handling, and log access.
Uses only Python standard library modules per Requirement 13.
"""

import errno
import json
import logging
import logging.handlers
import os
import platform
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

# --- Platform guard (Requirement 1) ---
if sys.version_info < (3, 10):
    raise RuntimeError(
        "Kognisant requires Python 3.10 or later. "
        f"Current version: {sys.version_info.major}.{sys.version_info.minor}"
    )

try:
    import fcntl  # noqa: F401 — import to verify POSIX availability
except ImportError:
    raise RuntimeError(
        "Kognisant requires a POSIX-compatible platform with fcntl support. "
        "Windows is not supported."
    )

# Module-level constants
CORE_DIR = os.path.expanduser("~/.kognisant_core")
PID_FILE = os.path.join(CORE_DIR, "daemon.pid")
LOG_FILE = os.path.join(CORE_DIR, "daemon.log")

# Daemon state flags (set by signal handlers in the forked child)
_shutdown_flag = False
_reload_flag = False

# --- World Model job type constants (Requirement 18) ---
WM_JOB_OBSERVE = "wm_observe"
WM_JOB_DECAY_TICK = "wm_decay_tick"
WM_JOB_STATIC_ANALYSIS = "wm_static_analysis"
WM_JOB_GENERATE_GOALS = "wm_generate_goals"

# Intervals for world model jobs
_WM_DECAY_TICK_INTERVAL = 3600  # 60 minutes in seconds
_WM_GIT_POLL_INTERVAL = 300  # 5 minutes in seconds
_WM_RETRY_DELAY = 300  # 5 minutes in seconds

# ─── World Model In-Memory Cache (Performance Optimization) ───────────────
# Avoids redundant load→deserialize→serialize→save cycles within the same
# daemon poll when decay_tick triggers generate_goals in sequence.

class _WMGraphCache:
    """Per-project in-memory cache for world model graph state.

    Holds the deserialized DependencyGraph between sequential WM jobs within
    a single poll cycle so that decay_tick → generate_goals doesn't require
    a second full disk round-trip.

    Cache is invalidated at the start of each daemon poll cycle.
    """

    _cache: dict[str, tuple] = {}  # project_root -> (graph, store, timestamp)
    _change_cache: dict[str, dict] = {}  # project_root -> change detection result

    @classmethod
    def invalidate_all(cls) -> None:
        """Clear all cached state — called at start of each poll cycle."""
        cls._cache.clear()
        cls._change_cache.clear()

    @classmethod
    def get_graph(cls, project_root: str, store):
        """Get or build the DependencyGraph for a project.

        Returns (graph, beliefs, contracts, gaps) tuple.
        Caches the result for reuse within the same poll cycle.
        """
        if project_root in cls._cache:
            return cls._cache[project_root]

        from .models import Edge, Node
        from .world_model import (
            BeliefSystem, ContractRegistry, DependencyGraph,
            EpistemicGapTracker,
        )

        graph_data = store.load_graph()
        graph = DependencyGraph()
        for node_dict in graph_data.get("nodes", []):
            graph.add_node(Node.from_dict(node_dict))
        for edge_dict in graph_data.get("edges", []):
            graph.add_edge(Edge.from_dict(edge_dict))

        beliefs = BeliefSystem()
        contracts = ContractRegistry()
        gaps = EpistemicGapTracker()

        result = (graph, beliefs, contracts, gaps)
        cls._cache[project_root] = result
        return result

    @classmethod
    def save_graph(cls, project_root: str, graph, store) -> None:
        """Serialize and save graph back to store. Updates cache in-place."""
        nodes_out = [{"id": n.id, "node_type": n.node_type, "module": n.module,
                      "file_path": n.file_path,
                      "tags": n.tags, "line_start": n.line_start,
                      "line_end": n.line_end, "last_modified": n.last_modified}
                     for n in graph._nodes.values()]
        edges_out = [e.to_dict() for e in graph._edges.values()]
        store.save_graph({"nodes": nodes_out, "edges": edges_out})

    @classmethod
    def get_changes(cls, project_root: str, store) -> dict:
        """Get or compute change detection results. Cached per poll cycle."""
        if project_root in cls._change_cache:
            return cls._change_cache[project_root]

        from .observer import ChangeDetector
        change_detector = ChangeDetector(project_root, store)
        changes = change_detector.detect_changes()
        cls._change_cache[project_root] = changes
        return changes


def _sigterm_handler(signum, frame):
    """Handle SIGTERM: set graceful shutdown flag (R12-AC1)."""
    global _shutdown_flag
    _shutdown_flag = True


def _sighup_handler(signum, frame):
    """Handle SIGHUP: set reload flag to re-read job queue (R12-AC5)."""
    global _reload_flag
    _reload_flag = True


def _setup_daemon_logging():
    """Configure logging to write to LOG_FILE with RotatingFileHandler.

    Uses logging.handlers.RotatingFileHandler with maxBytes=10MB, backupCount=3
    per Requirement 15.2.
    Format: YYYY-MM-DDTHH:MM:SS level message
    """
    os.makedirs(CORE_DIR, exist_ok=True)

    handler = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=3
    )
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


def _run_agent_task(task_description: str, project_root: str | None = None) -> dict:
    """Execute an agent task via the PERP orchestration pipeline.

    Invokes perp_orchestrate's internal worker synchronously (since we are
    already on a background thread in the daemon) with the task description
    and project context derived from project_root.

    Args:
        task_description: The task to execute.
        project_root: Working directory for the agent. Defaults to $HOME if None/empty.

    Returns:
        Dict with keys: completed (bool), output (str), error (str|None).
    """
    from .agents import _orchestrate_worker, SwarmController
    from .config import get_compiled_models

    # Resolve project_root: null/empty → home directory
    if not project_root:
        project_root = os.path.expanduser("~")

    # Build project_info from project_root (minimal structure for agent context)
    project_info = None
    if os.path.isdir(project_root):
        # Gather basic file listing for the project
        files = []
        try:
            for entry in os.listdir(project_root):
                entry_path = os.path.join(project_root, entry)
                files.append({
                    "name": entry,
                    "is_dir": os.path.isdir(entry_path),
                })
        except OSError:
            pass
        project_info = {"root": project_root, "files": files}

    # Load compiled models from config
    try:
        compiled_models = get_compiled_models()
    except Exception:
        compiled_models = []

    # Reset swarm controller state for this invocation
    SwarmController.stop_event.clear()
    SwarmController.resume_event.set()
    SwarmController.is_active = True
    SwarmController.is_paused = False
    SwarmController.active_task_description = task_description

    try:
        # Run the orchestration worker synchronously on this thread
        _orchestrate_worker(task_description, project_info, compiled_models)

        # If we reach here without exception, the orchestration completed
        return {
            "completed": True,
            "output": f"PERP orchestration completed for task: {task_description}",
            "error": None,
        }
    except Exception as e:
        return {
            "completed": False,
            "output": "",
            "error": f"PERP orchestration failed: {type(e).__name__}: {e}",
        }
    finally:
        SwarmController.is_active = False


class StreamReader(threading.Thread):
    """Daemon thread that reads subprocess pipe line-by-line.

    Uses iter(proc.stdout.readline, b"") pattern for reliable line reading.
    Appends to job log file. Stderr lines are prefixed with "[ERROR] ".
    Created with daemon=True to avoid blocking shutdown.
    """

    def __init__(self, pipe, log_path: str, prefix: str = "",
                 on_broken_pipe=None):
        """Initialize StreamReader thread.

        Args:
            pipe: The subprocess pipe (stdout or stderr) to read from.
            log_path: Path to the log file to write output to.
            prefix: Prefix to prepend to each line (e.g. "[ERROR] ").
            on_broken_pipe: Optional callback invoked on BrokenPipeError/EPIPE.
        """
        super().__init__(daemon=True)
        self.pipe = pipe
        self.log_path = log_path
        self.prefix = prefix
        self._on_broken_pipe = on_broken_pipe

    def run(self) -> None:
        """Read lines until EOF or BrokenPipeError.

        On BrokenPipeError/IOError(EPIPE): calls _on_broken_pipe callback
        which marks the job as failed.
        """
        try:
            for line in iter(self.pipe.readline, b""):
                decoded = line.decode("utf-8", errors="replace")
                self._append_line(decoded)
        except (BrokenPipeError, IOError) as e:
            if isinstance(e, IOError) and getattr(e, 'errno', None) != errno.EPIPE:
                # Not an EPIPE error, just stop reading
                return
            if self._on_broken_pipe:
                self._on_broken_pipe(str(e))
        except (OSError, ValueError):
            # Pipe closed or invalid - stop reading silently
            pass

    def _append_line(self, line: str) -> None:
        """Append a single line to log file with optional prefix."""
        try:
            os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
            with open(self.log_path, "a") as f:
                if self.prefix:
                    f.write(f"{self.prefix}{line}")
                else:
                    f.write(line)
                # Ensure trailing newline
                if line and not line.endswith("\n"):
                    f.write("\n")
        except OSError:
            pass


class ProcessManager:
    """Manages subprocess spawning and lifecycle for job execution.

    Provides static methods to spawn script subprocesses, check liveness,
    and gracefully terminate processes.
    """

    @staticmethod
    def spawn(script_path: str, env: dict, job_context: dict,
              log_path: str | None = None,
              on_broken_pipe=None) -> subprocess.Popen:
        """Spawn a script subprocess with environment and JSON stdin context.

        Creates StreamReader threads for stdout and stderr, starts them,
        and stores references on the Popen object.

        Args:
            script_path: Absolute path to the Python script to execute.
            env: Environment variables to set in the subprocess.
            job_context: Dict with {job_name, job_type, env_vars, timestamp}.
            log_path: Path to log file for StreamReader output.
            on_broken_pipe: Callback for broken pipe detection.

        Returns:
            The subprocess.Popen object with .stdout_reader and .stderr_reader attributes.
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

        # Create and start StreamReader threads if log_path is provided
        if log_path:
            stdout_reader = StreamReader(
                proc.stdout, log_path, prefix="",
                on_broken_pipe=on_broken_pipe,
            )
            stderr_reader = StreamReader(
                proc.stderr, log_path, prefix="[ERROR] ",
                on_broken_pipe=on_broken_pipe,
            )
            stdout_reader.start()
            stderr_reader.start()
            proc.stdout_reader = stdout_reader
            proc.stderr_reader = stderr_reader
        else:
            proc.stdout_reader = None
            proc.stderr_reader = None

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

    @staticmethod
    def get_start_time(pid: int) -> str | None:
        """Get OS-reported process creation time for PID reuse detection.

        macOS: subprocess ps -o lstart= -p {pid}
        Linux: reads /proc/{pid}/stat field 22 (starttime)

        Returns:
            String representation of process start time, or None if process not found.
        """
        try:
            if platform.system() == "Darwin":
                # macOS: use ps -o lstart=
                result = subprocess.run(
                    ["ps", "-o", "lstart=", "-p", str(pid)],
                    capture_output=True, text=True, timeout=5,
                )
                if result.returncode == 0 and result.stdout.strip():
                    return result.stdout.strip()
                return None
            else:
                # Linux: read /proc/{pid}/stat field 22 (starttime in clock ticks)
                stat_path = f"/proc/{pid}/stat"
                if not os.path.exists(stat_path):
                    return None
                with open(stat_path, "r") as f:
                    stat_content = f.read()
                # Fields after the command name (in parentheses)
                # Find the last ')' to handle command names with spaces/parens
                close_paren = stat_content.rfind(")")
                if close_paren == -1:
                    return None
                fields_after = stat_content[close_paren + 2:].split()
                # Field 22 in stat is starttime, but index is 0-based from field 3
                # so it's index 19 in fields_after (field 22 - 3 = 19)
                if len(fields_after) > 19:
                    return fields_after[19]
                return None
        except (OSError, subprocess.TimeoutExpired, ValueError, IndexError):
            return None

    @staticmethod
    def check_symlink(script_path: str, scripts_dir: str) -> bool:
        """Verify os.path.realpath(script_path) is within scripts_dir.

        Returns True if safe (path is contained), False if path escapes
        the allowed directory.

        Args:
            script_path: The path to check.
            scripts_dir: The allowed base directory.

        Returns:
            True if the resolved path is within scripts_dir.
        """
        real_script = os.path.realpath(script_path)
        real_scripts_dir = os.path.realpath(scripts_dir)
        # Ensure the resolved path starts with the scripts directory
        # Add trailing separator to prevent prefix matches like /scripts2/
        return real_script.startswith(real_scripts_dir + os.sep) or real_script == real_scripts_dir


def _rotate_log_if_needed(log_path: str):
    """Rotate a job log file if it exceeds 10 MB (Requirement 15.1).

    Uses rename-then-open strategy: rename current log to {name}.log.1,
    then open a new file for subsequent writes. No open handles across
    rotation boundaries.
    """
    try:
        if os.path.exists(log_path) and os.path.getsize(log_path) > 10 * 1024 * 1024:
            rotated_path = log_path + ".1"
            # Remove old rotated file if it exists
            if os.path.exists(rotated_path):
                os.remove(rotated_path)
            # Rename current log — no open handles since StreamReaders
            # open/close per line write
            os.rename(log_path, rotated_path)
            # New log file will be created on next write
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

    Returns dict with job_name, job_type, env_vars, project_root,
    and timestamp (ISO 8601 UTC).
    """
    return {
        "job_name": job.get("name", ""),
        "job_type": job.get("type", ""),
        "env_vars": job.get("env_vars", {}),
        "project_root": job.get("project_root", None),
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _resolve_script_path(job: dict) -> str | None:
    """Resolve the absolute path to a job's script.

    Returns the absolute path if the script exists and passes symlink
    containment check, None otherwise. If symlink escape is detected,
    logs a security warning.
    """
    script_path = job.get("script_path", "")
    if not script_path:
        return None

    scripts_dir = os.path.join(CORE_DIR, "scripts")

    # If already absolute, use as-is
    if os.path.isabs(script_path):
        abs_path = script_path
    else:
        # Resolve relative to scripts directory
        abs_path = os.path.join(scripts_dir, script_path)

    if not os.path.exists(abs_path):
        return None

    # Symlink containment check (Requirement 27.1, 27.2)
    if not ProcessManager.check_symlink(abs_path, scripts_dir):
        logger = logging.getLogger("kognisant.daemon")
        real_path = os.path.realpath(abs_path)
        logger.warning(
            "Security: script path '%s' resolves to '%s' which is outside "
            "allowed scripts directory '%s'. Execution refused.",
            abs_path, real_path, scripts_dir,
        )
        return None

    return abs_path


# ─── World Model Daemon Job Helpers (Requirement 18) ───────────────────────


def _get_registered_projects() -> list[str]:
    """Return list of registered project root paths from projects.json."""
    projects_file = os.path.join(CORE_DIR, "projects.json")
    try:
        with open(projects_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return list(data.get("projects", {}).keys())
    except (OSError, json.JSONDecodeError, ValueError):
        return []


def _has_recent_file_modifications(project_root: str, within_minutes: int = 60) -> bool:
    """Check if any file modifications occurred in the project within the given minutes.

    Uses git log to check for recent commits. Falls back to checking file
    mtimes in the project root if git is unavailable.
    """
    import subprocess as _sp

    try:
        result = _sp.run(
            ["git", "log", "--oneline", f"--since={within_minutes} minutes ago", "--max-count=1"],
            capture_output=True,
            text=True,
            cwd=project_root,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return True
    except (FileNotFoundError, OSError):
        pass

    # Fallback: check mtime of files in project directory
    cutoff = time.time() - (within_minutes * 60)
    try:
        for entry in os.scandir(project_root):
            if entry.name.startswith("."):
                continue
            try:
                if entry.stat().st_mtime > cutoff:
                    return True
            except OSError:
                continue
    except OSError:
        pass

    return False


def _get_current_git_head(project_root: str) -> str | None:
    """Return the current git HEAD hash for a project, or None if unavailable."""
    import subprocess as _sp

    try:
        result = _sp.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            cwd=project_root,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, OSError):
        pass
    return None


def _run_wm_decay_tick(project_root: str, logger) -> bool:
    """Execute a decay_tick job for the given project.

    Uses _WMGraphCache to avoid redundant deserialization when
    generate_goals follows immediately after.

    Returns True on success, False on failure.
    """
    from .config import is_world_model_enabled, load_world_model

    if not is_world_model_enabled(project_root):
        return True  # Not enabled — consider success (no-op)

    try:
        store = load_world_model(project_root)
        graph, beliefs, contracts, gaps = _WMGraphCache.get_graph(project_root, store)

        from .world_model import GraphMaintenanceEngine

        # Use cached change detection (avoids duplicate git subprocess calls)
        changes = _WMGraphCache.get_changes(project_root, store)
        modified_nodes = changes.get("modified_functions", [])

        # Skip decay if no modifications (fast path)
        if not modified_nodes:
            logger.info("World model decay_tick skipped (no modifications) for '%s'", project_root)
            return True

        # Run decay tick
        engine = GraphMaintenanceEngine(graph, beliefs, contracts, gaps)
        engine.decay_tick(modified_nodes)

        # Save graph back
        _WMGraphCache.save_graph(project_root, graph, store)

        logger.info("World model decay_tick completed for '%s'", project_root)
        return True
    except Exception as e:
        logger.error("World model decay_tick failed for '%s': %s", project_root, e)
        return False


def _run_wm_static_analysis(project_root: str, logger) -> bool:
    """Execute a static_analysis job for the given project.

    Uses cached change detection to avoid duplicate git subprocess calls.
    Skips pytest collection (moved to separate low-priority job).

    Returns True on success, False on failure.
    """
    from .config import is_world_model_enabled, load_world_model

    if not is_world_model_enabled(project_root):
        return True  # Not enabled — consider success (no-op)

    try:
        store = load_world_model(project_root)

        from .observer import StaticAnalyzer

        # Use cached change detection (shared with decay_tick if both trigger)
        changes = _WMGraphCache.get_changes(project_root, store)

        added = changes.get("added_files", [])
        modified_funcs = changes.get("modified_functions", [])

        # Fast path: skip if no changes
        if not added and not modified_funcs:
            logger.info("World model static_analysis skipped (no changes) for '%s'", project_root)
            return True

        if added:
            analyzer = StaticAnalyzer(project_root, scope_config={"max_files": 1000})
            # Batch analyze: only .py files
            py_files = [
                os.path.join(project_root, fp) for fp in added
                if fp.endswith(".py") and os.path.exists(os.path.join(project_root, fp))
            ]
            for abs_path in py_files:
                try:
                    analyzer.analyze_file(abs_path)
                except Exception:
                    pass  # Individual file failures are non-fatal

        logger.info("World model static_analysis completed for '%s'", project_root)
        return True
    except Exception as e:
        logger.error("World model static_analysis failed for '%s': %s", project_root, e)
        return False


def _run_wm_generate_goals(project_root: str, logger) -> bool:
    """Execute a generate_goals job for the given project.

    Uses _WMGraphCache to reuse the already-loaded graph from decay_tick
    rather than loading it again from disk.

    Returns True on success, False on failure.
    """
    from .config import is_world_model_enabled, load_world_model

    if not is_world_model_enabled(project_root):
        return True  # Not enabled — consider success (no-op)

    try:
        store = load_world_model(project_root)
        graph, beliefs, contracts, gaps = _WMGraphCache.get_graph(project_root, store)

        from .goal_engine import GoalGenerator

        generator = GoalGenerator(graph, contracts, gaps, beliefs, store)
        generator.generate_goals()

        logger.info("World model generate_goals completed for '%s'", project_root)
        return True
    except Exception as e:
        logger.error("World model generate_goals failed for '%s': %s", project_root, e)
        return False


# ─── World Model Public API (Requirement 18) ──────────────────────────────


# Job registry: maps project_root to set of registered job types
_wm_job_registry: dict[str, set[str]] = {}


def register_world_model_jobs(project_root: str) -> None:
    """Register world model scheduled jobs for a project in the daemon's job registry.

    Adds decay_tick, static_analysis, and generate_goals jobs for the given
    project root. These jobs will be picked up by the daemon's main loop
    scheduling logic.

    Args:
        project_root: Absolute path to the project root directory.
    """
    try:
        _wm_job_registry[project_root] = {
            WM_JOB_DECAY_TICK,
            WM_JOB_STATIC_ANALYSIS,
            WM_JOB_GENERATE_GOALS,
        }
    except Exception:
        pass


def execute_world_model_job(job_type: str, project_root: str) -> bool:
    """Execute a world model maintenance job for a project.

    Checks if world model is enabled, loads the store, and dispatches
    to the appropriate handler based on job_type.

    Args:
        job_type: One of WM_JOB_DECAY_TICK, WM_JOB_STATIC_ANALYSIS, WM_JOB_GENERATE_GOALS.
        project_root: Absolute path to the project root directory.

    Returns:
        True on success, False on failure.
    """
    from .config import is_world_model_enabled, load_world_model

    logger = logging.getLogger("kognisant.daemon")

    try:
        if not is_world_model_enabled(project_root):
            logger.info(
                "World model not enabled for '%s', skipping %s",
                project_root, job_type,
            )
            return False

        store = load_world_model(project_root)

        if job_type == WM_JOB_DECAY_TICK:
            return _execute_wm_decay_tick(store, project_root, logger)
        elif job_type == WM_JOB_STATIC_ANALYSIS:
            return _execute_wm_static_analysis(store, project_root, logger)
        elif job_type == WM_JOB_GENERATE_GOALS:
            return _execute_wm_generate_goals(store, project_root, logger)
        else:
            logger.error("Unknown world model job type: %s", job_type)
            return False
    except Exception as e:
        logger.error(
            "World model job %s failed for '%s': %s",
            job_type, project_root, e,
        )
        return False


def _execute_wm_decay_tick(store, project_root: str, logger) -> bool:
    """Execute decay_tick via store: load graph, run maintenance, save back."""
    try:
        graph_data = store.load_graph()

        from .models import Edge, Node
        from .world_model import (
            BeliefSystem, ContractRegistry, DependencyGraph,
            EpistemicGapTracker, GraphMaintenanceEngine,
        )
        from .observer import ChangeDetector

        # Reconstruct graph from stored data
        graph = DependencyGraph()
        for node_dict in graph_data.get("nodes", []):
            graph.add_node(Node.from_dict(node_dict))
        for edge_dict in graph_data.get("edges", []):
            graph.add_edge(Edge.from_dict(edge_dict))

        # Load beliefs, contracts, gaps
        beliefs = BeliefSystem()
        contracts = ContractRegistry()
        gaps = EpistemicGapTracker()

        # Detect changes to find modified nodes
        change_detector = ChangeDetector(project_root, store)
        changes = change_detector.detect_changes()
        modified_nodes = changes.get("modified_functions", [])

        # Run decay tick
        engine = GraphMaintenanceEngine(graph, beliefs, contracts, gaps)
        engine.decay_tick(modified_nodes)

        # Save graph back
        nodes_out = [{"id": n.id, "node_type": n.node_type, "module": n.module,
                      "file_path": n.file_path, "confidence": n.confidence,
                      "tags": n.tags, "line_start": n.line_start,
                      "line_end": n.line_end}
                     for n in graph._nodes.values()]
        edges_out = [{"id": e.id, "source": e.source, "target": e.target,
                      "edge_type": e.edge_type, "confidence": e.confidence,
                      "provenance": e.provenance, "conditional": e.conditional,
                      "version": e.version}
                     for e in graph._edges.values()]
        store.save_graph({"nodes": nodes_out, "edges": edges_out})

        logger.info("execute_world_model_job decay_tick completed for '%s'", project_root)
        return True
    except Exception as e:
        logger.error("execute_world_model_job decay_tick failed for '%s': %s", project_root, e)
        return False


def _execute_wm_static_analysis(store, project_root: str, logger) -> bool:
    """Execute static_analysis via store: detect changes, run analyzer."""
    try:
        from .observer import ChangeDetector, StaticAnalyzer

        change_detector = ChangeDetector(project_root, store)
        changes = change_detector.detect_changes()

        added = changes.get("added_files", [])
        modified_funcs = changes.get("modified_functions", [])

        if added or modified_funcs:
            analyzer = StaticAnalyzer(project_root, scope_config={"max_files": 1000})
            for file_path in added:
                abs_path = os.path.join(project_root, file_path)
                if os.path.exists(abs_path) and abs_path.endswith(".py"):
                    try:
                        analyzer.analyze_file(abs_path)
                    except Exception:
                        pass  # Individual file failures are non-fatal

        logger.info("execute_world_model_job static_analysis completed for '%s'", project_root)
        return True
    except Exception as e:
        logger.error("execute_world_model_job static_analysis failed for '%s': %s", project_root, e)
        return False


def _execute_wm_generate_goals(store, project_root: str, logger) -> bool:
    """Execute generate_goals via store: build graph, run goal generator."""
    try:
        graph_data = store.load_graph()

        from .models import Edge, Node
        from .world_model import (
            BeliefSystem, ContractRegistry, DependencyGraph,
            EpistemicGapTracker,
        )
        from .goal_engine import GoalGenerator

        # Reconstruct graph
        graph = DependencyGraph()
        for node_dict in graph_data.get("nodes", []):
            graph.add_node(Node.from_dict(node_dict))
        for edge_dict in graph_data.get("edges", []):
            graph.add_edge(Edge.from_dict(edge_dict))

        beliefs = BeliefSystem()
        contracts = ContractRegistry()
        gaps = EpistemicGapTracker()

        generator = GoalGenerator(graph, contracts, gaps, beliefs, store)
        generator.generate_goals()

        logger.info("execute_world_model_job generate_goals completed for '%s'", project_root)
        return True
    except Exception as e:
        logger.error("execute_world_model_job generate_goals failed for '%s': %s", project_root, e)
        return False


def _main_loop():
    """Main daemon polling loop.

    Polls the job queue every 15 seconds (R2-AC1) and manages:
    - Orphan cleanup on startup (R9)
    - Clock jump detection (R11)
    - Scheduled job execution (R3)
    - Persistent job lifecycle (R4)
    - Agent job execution (R5)
    - Graceful shutdown (R12)
    - SIGHUP responsiveness at 500ms (R12)
    - Broken pipe detection (R13)
    - Deletion guard / backup recovery (R14)
    - Log rotation (R15)
    """
    global _shutdown_flag, _reload_flag

    from .jobs import JobQueue

    logger = _setup_daemon_logging()
    logger.info("Daemon started with PID %d", os.getpid())

    # Root privilege warning (Requirement 29)
    if os.geteuid() == 0:
        logger.warning(
            "Daemon running with root privileges. "
            "Recommend running under a non-root user."
        )

    job_queue = JobQueue()
    logs_dir = os.path.join(CORE_DIR, "logs")
    os.makedirs(logs_dir, exist_ok=True)

    # --- Orphan cleanup on startup (Requirement 9) ---
    try:
        jobs = job_queue.load()
        for job in jobs:
            if job.get("state") != "running":
                continue
            pid = job.get("pid")
            name = job.get("name", "")
            if not pid:
                # No PID stored but state is running — mark as orphaned
                job_queue.update_status(
                    name, "failed", pid=None,
                    pid_started_at=None,
                )
                logger.warning(
                    "Orphan cleanup: job '%s' in running state with no PID, marked failed",
                    name,
                )
                continue

            if not ProcessManager.is_alive(pid):
                # PID not alive → orphaned process not found (R9-AC4)
                job_queue.update_status(
                    name, "failed", pid=None,
                    pid_started_at=None,
                )
                logger.warning(
                    "Orphan cleanup: job '%s' PID %d not alive, "
                    "marked failed (orphaned process not found)",
                    name, pid,
                )
            else:
                # PID alive — check creation time match (R9-AC2, R9-AC3)
                stored_start_time = job.get("pid_started_at")
                current_start_time = ProcessManager.get_start_time(pid)
                if stored_start_time and current_start_time:
                    if stored_start_time != current_start_time:
                        # PID reused by another process (R9-AC3)
                        job_queue.update_status(
                            name, "failed", pid=None,
                            pid_started_at=None,
                        )
                        logger.warning(
                            "Orphan cleanup: job '%s' PID %d reused by another process "
                            "(expected start_time='%s', got='%s'), marked failed without signal",
                            name, pid, stored_start_time, current_start_time,
                        )
                    # else: PID alive and start time matches — process is legitimate
                elif not stored_start_time:
                    # No stored start time — can't verify, leave as is
                    logger.info(
                        "Orphan cleanup: job '%s' PID %d alive but no stored start time, "
                        "assuming legitimate",
                        name, pid,
                    )
    except Exception as e:
        logger.error("Error during orphan cleanup: %s", e)

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

    # --- World Model job state (Requirement 18) ---
    # Per-project tracking: {project_root: state_dict}
    wm_state: dict[str, dict] = {}
    # Initialize WM state for all registered projects
    for _proj_root in _get_registered_projects():
        wm_state[_proj_root] = {
            "last_decay_tick": 0.0,       # monotonic time of last decay_tick
            "last_git_poll": 0.0,         # monotonic time of last git HEAD poll
            "last_git_head": None,        # last known git HEAD hash
            "decay_tick_failures": 0,     # failure counter
            "static_analysis_failures": 0,
            "generate_goals_failures": 0,
            "decay_tick_retry_at": None,  # monotonic time for retry (or None)
            "static_analysis_retry_at": None,
            "generate_goals_retry_at": None,
            "decay_tick_failed": False,   # marked failed until next trigger
            "static_analysis_failed": False,
            "generate_goals_failed": False,
        }
    logger.info("World model jobs registered for %d project(s) (R18.1)", len(wm_state))

    # --- Channel service initialization ---
    channel_service = None
    try:
        from .channel_daemon import ChannelDaemonService
        channel_service = ChannelDaemonService()
        logger.info("Channel service initialized")
    except Exception as e:
        logger.warning("Channel service failed to initialize: %s", e)

    # --- Batch queue initialization ---
    batch_queue = None
    try:
        from .batch import get_batch_queue
        batch_queue = get_batch_queue()
        if batch_queue:
            logger.info("Batch queue initialized (enabled)")
        else:
            logger.info("Batch queue disabled (batch_config.json: enabled=false)")
    except Exception as e:
        logger.warning("Batch queue failed to initialize: %s", e)

    # Clock jump detection (Requirement 11): use monotonic clock
    POLL_INTERVAL = 15  # seconds
    _last_tick = time.monotonic()

    while not _shutdown_flag:
        # --- Invalidate WM graph cache at start of each poll cycle ---
        _WMGraphCache.invalidate_all()

        # --- (a) Check reload flag (R12-AC5) ---
        if _reload_flag:
            logger.info("Received SIGHUP, reloading job queue")
            _reload_flag = False
            # Re-instantiate job_queue to pick up changes
            job_queue = JobQueue()

        try:
            now = datetime.now(timezone.utc)

            # --- Clock jump detection (Requirement 11) ---
            current_tick = time.monotonic()
            elapsed = current_tick - _last_tick
            clock_jump_detected = elapsed > (2 * POLL_INTERVAL)  # > 30s

            if clock_jump_detected:
                logger.warning(
                    "Clock jump detected: %.1fs elapsed (expected ~%ds)",
                    elapsed, POLL_INTERVAL,
                )

            _last_tick = current_tick

            # --- Deletion guard (Requirement 14) ---
            # Ensure job queue file is accessible; recover if needed
            # This is handled internally by _load_raw() and _recover_from_backup()
            # but we do an explicit check here for the polling cycle
            if not os.path.exists(job_queue.queue_path):
                logger.warning(
                    "jobs.json missing during poll, attempting recovery"
                )
                try:
                    job_queue._load_raw()  # Will trigger recovery logic
                except Exception as e:
                    logger.error("Recovery failed: %s", e)

            # --- (b) Execute due scheduled jobs (R3-AC1) ---
            if clock_jump_detected:
                # Handle scheduled jobs according to scheduler_policy (R11)
                due_jobs = job_queue.get_due_scheduled(now)
                for job in due_jobs:
                    name = job.get("name", "")
                    policy = job.get("scheduler_policy", "skip")

                    if policy == "skip":
                        # Discard missed executions (R11-AC3)
                        logger.info(
                            "Clock jump: skipping job '%s' (scheduler_policy=skip)",
                            name,
                        )
                        continue
                    elif policy == "catchup_once":
                        # Execute each missed job exactly once (R11-AC4)
                        logger.info(
                            "Clock jump: catching up job '%s' (scheduler_policy=catchup_once)",
                            name,
                        )
                        # Fall through to normal execution below
                        pass

                    # Execute job (catchup_once path)
                    if name in running_scheduled:
                        proc = running_scheduled[name]
                        if proc.poll() is None:
                            continue

                    abs_script = _resolve_script_path(job)
                    if abs_script is None:
                        logger.error(
                            "Scheduled job '%s': script not found at '%s'",
                            name, job.get("script_path", ""),
                        )
                        job_queue.update_status(name, "failed")
                        continue

                    _check_missing_env_vars(job, logger)
                    context = _build_job_context(job)
                    log_path = os.path.join(logs_dir, f"{name}.log")

                    def _make_broken_pipe_cb(job_name, job_pid_holder):
                        def _on_broken_pipe(error_msg):
                            logger.error(
                                "Broken pipe for job '%s': %s", job_name, error_msg
                            )
                            job_queue.update_status(
                                job_name, "failed", pid=None,
                            )
                            if job_pid_holder[0]:
                                ProcessManager.kill_gracefully(job_pid_holder[0])
                        return _on_broken_pipe

                    pid_holder = [None]
                    try:
                        proc = ProcessManager.spawn(
                            abs_script, job.get("env_vars", {}), context,
                            log_path=log_path,
                            on_broken_pipe=_make_broken_pipe_cb(name, pid_holder),
                        )
                        pid_holder[0] = proc.pid
                        running_scheduled[name] = proc
                        scheduled_start_times[name] = time.monotonic()
                        # Store pid_started_at (R9 - for PID reuse protection)
                        pid_start = ProcessManager.get_start_time(proc.pid)
                        job_queue.update_status(
                            name, "running", pid=proc.pid,
                            pid_started_at=pid_start,
                        )
                        logger.info("Scheduled job '%s' started (PID %d)", name, proc.pid)
                    except OSError as e:
                        logger.error(
                            "Scheduled job '%s': failed to spawn: %s", name, e
                        )
                        job_queue.update_status(name, "failed")
            else:
                # Normal scheduling (no clock jump)
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
                    log_path = os.path.join(logs_dir, f"{name}.log")

                    def _make_broken_pipe_cb(job_name, job_pid_holder):
                        def _on_broken_pipe(error_msg):
                            logger.error(
                                "Broken pipe for job '%s': %s", job_name, error_msg
                            )
                            job_queue.update_status(
                                job_name, "failed", pid=None,
                            )
                            if job_pid_holder[0]:
                                ProcessManager.kill_gracefully(job_pid_holder[0])
                        return _on_broken_pipe

                    pid_holder = [None]
                    try:
                        proc = ProcessManager.spawn(
                            abs_script, job.get("env_vars", {}), context,
                            log_path=log_path,
                            on_broken_pipe=_make_broken_pipe_cb(name, pid_holder),
                        )
                        pid_holder[0] = proc.pid
                        running_scheduled[name] = proc
                        scheduled_start_times[name] = time.monotonic()
                        # Store pid_started_at (R9 - for PID reuse protection)
                        pid_start = ProcessManager.get_start_time(proc.pid)
                        job_queue.update_status(
                            name, "running", pid=proc.pid,
                            pid_started_at=pid_start,
                        )
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
                    # Process has exited — join StreamReader threads (Req 5.4)
                    if getattr(proc, 'stdout_reader', None):
                        proc.stdout_reader.join(timeout=2)
                    if getattr(proc, 'stderr_reader', None):
                        proc.stderr_reader.join(timeout=2)

                    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
                    # Increment run_count and record last_exit_code (R50.2, R50.3)
                    current_job = job_queue.get_job(name)
                    current_run_count = (
                        current_job.get("run_count", 0) if current_job else 0
                    )
                    if returncode == 0:
                        # Success (R3-AC2)
                        job_queue.update_status(
                            name, "scheduled", last_run_at=now_str, pid=None,
                            pid_started_at=None,
                            last_exit_code=returncode,
                            run_count=current_run_count + 1,
                        )
                        logger.info("Scheduled job '%s' completed successfully", name)
                    else:
                        # Failure (R3-AC3)
                        job_queue.update_status(
                            name, "scheduled", last_run_at=now_str, pid=None,
                            pid_started_at=None,
                            last_exit_code=returncode,
                            run_count=current_run_count + 1,
                        )
                        logger.error(
                            "Scheduled job '%s' failed with exit code %d",
                            name, returncode,
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
                        current_job = job_queue.get_job(name)
                        current_run_count = (
                            current_job.get("run_count", 0) if current_job else 0
                        )
                        job_queue.update_status(
                            name, "scheduled", last_run_at=now_str, pid=None,
                            pid_started_at=None,
                            run_count=current_run_count + 1,
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
                log_path = os.path.join(logs_dir, f"{name}.log")

                def _make_broken_pipe_cb(job_name, job_pid_holder):
                    def _on_broken_pipe(error_msg):
                        logger.error(
                            "Broken pipe for job '%s': %s", job_name, error_msg
                        )
                        job_queue.update_status(
                            job_name, "failed", pid=None,
                        )
                        if job_pid_holder[0]:
                            ProcessManager.kill_gracefully(job_pid_holder[0])
                    return _on_broken_pipe

                pid_holder = [None]
                try:
                    proc = ProcessManager.spawn(
                        abs_script, job.get("env_vars", {}), context,
                        log_path=log_path,
                        on_broken_pipe=_make_broken_pipe_cb(name, pid_holder),
                    )
                    pid_holder[0] = proc.pid
                    running_persistent[name] = proc
                    # Store PID and pid_started_at, update state to running (R2-AC6)
                    pid_start = ProcessManager.get_start_time(proc.pid)
                    job_queue.update_status(
                        name, "running", pid=proc.pid,
                        pid_started_at=pid_start,
                    )
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
                        # Log rotation for persistent jobs (R15.1)
                        log_path = os.path.join(logs_dir, f"{name}.log")
                        _rotate_log_if_needed(log_path)
                    continue

                # Process has exited — collect output
                # Join StreamReader threads (Req 5.4)
                if getattr(proc, 'stdout_reader', None):
                    proc.stdout_reader.join(timeout=2)
                if getattr(proc, 'stderr_reader', None):
                    proc.stderr_reader.join(timeout=2)

                # Increment run_count and record last_exit_code (R50.2, R50.3)
                current_job = job_queue.get_job(name)
                current_run_count = (
                    current_job.get("run_count", 0) if current_job else 0
                )

                if returncode == 0:
                    # Clean exit (R4-AC4)
                    job_queue.update_status(
                        name, "completed", pid=None,
                        pid_started_at=None,
                        last_exit_code=returncode,
                        run_count=current_run_count + 1,
                    )
                    logger.info("Persistent job '%s' exited cleanly (code 0)", name)
                    persistent_to_remove.append(name)
                else:
                    # Crashed — check if cancelled first (R4-AC8)
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
                        logger.error(
                            "Persistent job '%s' entered crash_loop: "
                            "%d restarts in 60s window",
                            name,
                            restart_count,
                        )
                        job_queue.update_status(
                            name,
                            "crash_loop",
                            pid=None,
                            pid_started_at=None,
                            restart_count=restart_count,
                            restart_timestamps=restart_timestamps,
                            last_exit_code=returncode,
                            run_count=current_run_count + 1,
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
                            pid_started_at=None,
                            restart_count=restart_count,
                            restart_timestamps=restart_timestamps,
                            last_exit_code=returncode,
                            run_count=current_run_count + 1,
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
                log_path = os.path.join(logs_dir, f"{name}.log")

                def _make_broken_pipe_cb(job_name, job_pid_holder):
                    def _on_broken_pipe(error_msg):
                        logger.error(
                            "Broken pipe for job '%s': %s", job_name, error_msg
                        )
                        job_queue.update_status(
                            job_name, "failed", pid=None,
                        )
                        if job_pid_holder[0]:
                            ProcessManager.kill_gracefully(job_pid_holder[0])
                    return _on_broken_pipe

                pid_holder = [None]
                try:
                    proc = ProcessManager.spawn(
                        abs_script, job_data.get("env_vars", {}), context,
                        log_path=log_path,
                        on_broken_pipe=_make_broken_pipe_cb(name, pid_holder),
                    )
                    pid_holder[0] = proc.pid
                    running_persistent[name] = proc
                    pid_start = ProcessManager.get_start_time(proc.pid)
                    job_queue.update_status(
                        name, "running", pid=proc.pid,
                        pid_started_at=pid_start,
                    )
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
                    # Resolve project_root from job data
                    current_job = job_queue.get_job(job_name)
                    project_root = current_job.get("project_root") if current_job else None
                    try:
                        result = _run_agent_task(task, project_root=project_root)
                        # Capture output (R5-AC4)
                        output = result.get("output", "")
                        _append_to_log(log_path, output)

                        if result.get("completed"):
                            # Success (R8-AC4): set state "completed", record timestamp,
                            # increment run_count, set last_exit_code = 0
                            finish_ts = datetime.now(timezone.utc).strftime(
                                "%Y-%m-%dT%H:%M:%S"
                            )
                            # Get current run_count to increment
                            current_job = job_queue.get_job(job_name)
                            current_run_count = (
                                current_job.get("run_count", 0) if current_job else 0
                            )
                            job_queue.update_status(
                                job_name, "completed",
                                last_run_at=finish_ts,
                                run_count=current_run_count + 1,
                                last_exit_code=0,
                            )
                            logger.info(
                                "Agent job '%s' completed at %s",
                                job_name, finish_ts,
                            )
                        else:
                            # Failure (R8 failure path): set state "failed",
                            # record error, set last_exit_code = 1
                            error_msg = result.get("error", "Unknown error")
                            _append_to_log(
                                log_path, error_msg, prefix="[ERROR] "
                            )
                            current_job = job_queue.get_job(job_name)
                            current_run_count = (
                                current_job.get("run_count", 0) if current_job else 0
                            )
                            finish_ts = datetime.now(timezone.utc).strftime(
                                "%Y-%m-%dT%H:%M:%S"
                            )
                            job_queue.update_status(
                                job_name, "failed",
                                last_run_at=finish_ts,
                                run_count=current_run_count + 1,
                                last_exit_code=1,
                            )
                            logger.error(
                                "Agent job '%s' failed: %s",
                                job_name, error_msg[:200],
                            )
                    except Exception as e:
                        # Unhandled exception (R5-AC3)
                        error_msg = f"{type(e).__name__}: {e}"
                        _append_to_log(log_path, error_msg, prefix="[ERROR] ")
                        current_job = job_queue.get_job(job_name)
                        current_run_count = (
                            current_job.get("run_count", 0) if current_job else 0
                        )
                        finish_ts = datetime.now(timezone.utc).strftime(
                            "%Y-%m-%dT%H:%M:%S"
                        )
                        job_queue.update_status(
                            job_name, "failed",
                            last_run_at=finish_ts,
                            run_count=current_run_count + 1,
                            last_exit_code=1,
                        )
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

            # --- (g) World Model scheduled jobs (Requirement 18) ---
            current_mono = time.monotonic()
            for proj_root, wm in wm_state.items():
                # --- R18.5/R18.6: Handle retries for failed jobs ---
                for job_key in ("decay_tick", "static_analysis", "generate_goals"):
                    retry_at = wm.get(f"{job_key}_retry_at")
                    if retry_at is not None and current_mono >= retry_at:
                        wm[f"{job_key}_retry_at"] = None
                        # Execute retry
                        if job_key == "decay_tick":
                            success = _run_wm_decay_tick(proj_root, logger)
                        elif job_key == "static_analysis":
                            success = _run_wm_static_analysis(proj_root, logger)
                        else:
                            success = _run_wm_generate_goals(proj_root, logger)

                        if success:
                            wm[f"{job_key}_failures"] = 0
                            wm[f"{job_key}_failed"] = False
                            logger.info(
                                "World model %s retry succeeded for '%s'",
                                job_key, proj_root,
                            )
                            # R18.4: trigger generate_goals after successful retry
                            if job_key in ("decay_tick", "static_analysis"):
                                goal_success = _run_wm_generate_goals(proj_root, logger)
                                if not goal_success:
                                    wm["generate_goals_failures"] += 1
                                    wm["generate_goals_retry_at"] = current_mono + _WM_RETRY_DELAY
                                    logger.error(
                                        "World model generate_goals failed after %s retry for '%s'",
                                        job_key, proj_root,
                                    )
                        else:
                            # R18.6: Second failure — mark failed, skip until next trigger
                            wm[f"{job_key}_failed"] = True
                            logger.error(
                                "World model %s retry failed for '%s', "
                                "marking failed until next trigger (R18.6)",
                                job_key, proj_root,
                            )

                # --- R18.2: decay_tick every 60 min when activity detected ---
                if not wm["decay_tick_failed"]:
                    time_since_decay = current_mono - wm["last_decay_tick"]
                    if time_since_decay >= _WM_DECAY_TICK_INTERVAL:
                        if _has_recent_file_modifications(proj_root, within_minutes=60):
                            wm["last_decay_tick"] = current_mono
                            success = _run_wm_decay_tick(proj_root, logger)
                            if success:
                                wm["decay_tick_failures"] = 0
                                # R18.4: trigger generate_goals after successful decay_tick
                                goal_success = _run_wm_generate_goals(proj_root, logger)
                                if not goal_success:
                                    wm["generate_goals_failures"] += 1
                                    wm["generate_goals_retry_at"] = current_mono + _WM_RETRY_DELAY
                                    logger.error(
                                        "World model generate_goals failed after decay_tick for '%s'",
                                        proj_root,
                                    )
                            else:
                                # R18.5: first failure — log, increment, retry after 5 min
                                wm["decay_tick_failures"] += 1
                                wm["decay_tick_retry_at"] = current_mono + _WM_RETRY_DELAY
                                logger.error(
                                    "World model decay_tick failed for '%s', "
                                    "scheduling retry in 5 min (R18.5)",
                                    proj_root,
                                )

                # --- R18.3: static_analysis when git HEAD changes (poll every 5 min) ---
                if not wm["static_analysis_failed"]:
                    time_since_git_poll = current_mono - wm["last_git_poll"]
                    if time_since_git_poll >= _WM_GIT_POLL_INTERVAL:
                        wm["last_git_poll"] = current_mono
                        current_head = _get_current_git_head(proj_root)
                        if current_head is not None:
                            if wm["last_git_head"] is None:
                                # First poll — just record HEAD, don't run
                                wm["last_git_head"] = current_head
                            elif current_head != wm["last_git_head"]:
                                # HEAD changed — run static_analysis
                                wm["last_git_head"] = current_head
                                success = _run_wm_static_analysis(proj_root, logger)
                                if success:
                                    wm["static_analysis_failures"] = 0
                                    # R18.4: trigger generate_goals after successful static_analysis
                                    goal_success = _run_wm_generate_goals(proj_root, logger)
                                    if not goal_success:
                                        wm["generate_goals_failures"] += 1
                                        wm["generate_goals_retry_at"] = current_mono + _WM_RETRY_DELAY
                                        logger.error(
                                            "World model generate_goals failed after static_analysis for '%s'",
                                            proj_root,
                                        )
                                else:
                                    # R18.5: first failure — log, increment, retry after 5 min
                                    wm["static_analysis_failures"] += 1
                                    wm["static_analysis_retry_at"] = current_mono + _WM_RETRY_DELAY
                                    logger.error(
                                        "World model static_analysis failed for '%s', "
                                        "scheduling retry in 5 min (R18.5)",
                                        proj_root,
                                    )

        except Exception as e:
            logger.error("Error in polling cycle: %s", e)

        # --- Channel service poll (manage adapters, route events) ---
        if channel_service:
            try:
                channel_service.poll()
            except Exception as e:
                logger.error("Channel service poll error: %s", e)

        # --- Batch queue poll (check job statuses, flush aged requests) ---
        if batch_queue:
            try:
                batch_queue.poll()
            except Exception as e:
                logger.error("Batch queue poll error: %s", e)

        # --- Sleep for 15s polling interval with responsive shutdown/SIGHUP check ---
        # Check _shutdown_flag and _reload_flag every 500ms (Requirement 12)
        for _ in range(30):
            if _shutdown_flag:
                break
            if _reload_flag:
                break  # Break immediately to begin new poll cycle
            time.sleep(0.5)

    # --- Graceful shutdown sequence (R12-AC1,2,3,4) ---
    logger.info("Daemon shutting down gracefully (PID %d)", os.getpid())

    # Shutdown channel adapters first
    if channel_service:
        try:
            channel_service.shutdown_all()
            logger.info("Channel adapters shut down")
        except Exception as e:
            logger.error("Channel shutdown error: %s", e)

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
        forks via os.fork(), and sets up the child process with:
        1. os.setsid() — new session
        2. os.closerange(3, SC_OPEN_MAX) — close inherited FDs
        3. Redirect stdin/stdout/stderr to /dev/null

        Uses O_CREAT|O_EXCL for race-free PID file creation (Requirement 10).

        Returns:
            The child (daemon) PID.

        Raises:
            RuntimeError: If the daemon is already running or another
                          instance is starting (R1-AC7, R10-AC4).
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

        # PID file race prevention (Requirement 10):
        # If PID file exists but PID is dead → remove stale file (R10-AC1, R10-AC2)
        if os.path.exists(PID_FILE):
            stale_pid = DaemonManager._read_pid()
            if stale_pid is not None and not DaemonManager._process_alive(stale_pid):
                try:
                    os.remove(PID_FILE)
                except OSError:
                    pass
            elif stale_pid is not None:
                # PID file exists and process is alive
                raise RuntimeError(
                    f"Daemon already running with PID {stale_pid}"
                )

        # Root privilege warning (Requirement 29)
        if os.geteuid() == 0:
            print(
                "WARNING: Starting daemon with root privileges. "
                "Recommend running under a non-root user.",
                file=sys.stderr,
            )

        # Atomically create PID file with O_CREAT|O_EXCL (Requirement 10.3)
        # This prevents race between two simultaneous start attempts
        try:
            pid_fd = os.open(PID_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise RuntimeError("Another daemon instance is starting")

        # Close the PID fd for now — child will write its own PID
        os.close(pid_fd)
        # Remove the placeholder PID file — child will create the real one
        try:
            os.remove(PID_FILE)
        except OSError:
            pass

        # Fork the daemon process
        child_pid = os.fork()

        if child_pid > 0:
            # Parent process
            print(f"Daemon started with PID {child_pid}")  # R1-AC2
            return child_pid
        else:
            # Child process — become session leader
            os.setsid()

            # Close all inherited file descriptors above stderr (Requirement 6)
            try:
                max_fd = os.sysconf("SC_OPEN_MAX")
            except (ValueError, OSError):
                max_fd = 1024
            os.closerange(3, max_fd)

            # Redirect stdin/stdout/stderr to /dev/null (Requirement 6)
            devnull_fd = os.open(os.devnull, os.O_RDWR)
            os.dup2(devnull_fd, 0)  # stdin
            os.dup2(devnull_fd, 1)  # stdout
            os.dup2(devnull_fd, 2)  # stderr
            if devnull_fd > 2:
                os.close(devnull_fd)

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
                if _shutdown_flag:
                    try:
                        os.remove(PID_FILE)
                    except OSError:
                        pass

            os._exit(0)

    @staticmethod
    def restart() -> int:
        """Stop existing daemon (if running), then start new one.

        Returns new daemon PID. If daemon was not running, starts fresh.
        """
        was_running = DaemonManager.is_running()
        if was_running:
            DaemonManager.stop()
            # Wait briefly for shutdown to complete
            for _ in range(20):  # up to 2 seconds
                if not DaemonManager.is_running():
                    break
                time.sleep(0.1)

        return DaemonManager.start()

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
