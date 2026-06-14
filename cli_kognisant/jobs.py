"""
Job queue state management, cron parsing, and file locking.

Uses only Python standard library modules per Requirement 13.
"""

import fcntl
import json
import logging
import os
import re
import shutil
import tempfile
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# Schema versioning constant
CURRENT_SCHEMA_VERSION = 1

# Valid error categories for standardized error format
VALID_ERROR_CATEGORIES = {"validation", "not_found", "state", "permission", "timeout", "io"}


def format_error(category: str, description: str, suggestion: str | None = None) -> str:
    """Format a standardized error message.

    Args:
        category: One of: validation, not_found, state, permission, timeout, io
        description: Human-readable error description.
        suggestion: Optional recovery action.

    Returns:
        "Error: [category] - [description]. [suggestion]" or
        "Error: [category] - [description]." if no suggestion.
    """
    if suggestion:
        return f"Error: [{category}] - {description}. {suggestion}"
    return f"Error: [{category}] - {description}."


class MigrationRegistry:
    """Registry of forward-migration functions keyed by source version.

    Each migration transforms data from version N to N+1.
    Migrations use the same _atomic_save path to ensure:
    - Pre-migration state is preserved in .bak
    - Migration result is crash-safe
    """

    _migrations: dict[int, Callable[[dict], dict]] = {}

    @classmethod
    def register(cls, from_version: int):
        """Decorator to register a migration function from from_version to from_version+1."""
        def decorator(fn: Callable[[dict], dict]) -> Callable[[dict], dict]:
            cls._migrations[from_version] = fn
            return fn
        return decorator

    @classmethod
    def apply_pending(cls, data: dict, save_fn: Callable[[dict], None]) -> dict:
        """Apply all pending migrations from data's schema_version to CURRENT_SCHEMA_VERSION.

        Each step:
        1. Call migration function
        2. save_fn(result) — uses atomic_save path, creating .bak of prior version

        Args:
            data: Current file data with schema_version.
            save_fn: Atomic save function for durability.

        Returns:
            Fully migrated data structure.

        Raises:
            ValueError: If a required migration is missing from registry.
        """
        current = data.get("schema_version", 1)
        while current < CURRENT_SCHEMA_VERSION:
            if current not in cls._migrations:
                raise ValueError(
                    f"No migration registered for version {current} → {current + 1}"
                )
            data = cls._migrations[current](data)
            save_fn(data)
            current = data["schema_version"]
        return data


class FileLock:
    """Advisory file lock using fcntl.flock() with timeout support.

    Implements context manager protocol for safe lock acquisition/release.
    Per R11-AC1,2,4: exclusive lock via LOCK_EX, release via LOCK_UN,
    5-second timeout with one retry after 1-second delay.
    """

    def __init__(self, lock_path: str, timeout: float = 5.0):
        self.lock_path = lock_path
        self.timeout = timeout
        self._fd = None

    def acquire(self) -> bool:
        """Acquire the advisory lock with timeout.

        Returns True if lock acquired, False if timed out after retry.
        Per R11-AC4: If lock not acquired within 5s, log timeout warning
        and retry once after 1s delay.
        """
        if self._try_acquire():
            return True

        # First attempt timed out - log warning and retry once after 1s
        logger.warning(
            "Lock acquisition timed out after %.1fs for %s, retrying in 1s",
            self.timeout,
            self.lock_path,
        )
        time.sleep(1.0)

        if self._try_acquire():
            return True

        logger.warning(
            "Lock acquisition failed after retry for %s", self.lock_path
        )
        return False

    def _try_acquire(self) -> bool:
        """Attempt to acquire the lock within the timeout period."""
        # Ensure the lock file's directory exists
        lock_dir = os.path.dirname(self.lock_path)
        if lock_dir:
            os.makedirs(lock_dir, exist_ok=True)

        self._fd = os.open(
            self.lock_path, os.O_CREAT | os.O_RDWR
        )

        deadline = time.monotonic() + self.timeout
        while True:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except (OSError, IOError):
                if time.monotonic() >= deadline:
                    os.close(self._fd)
                    self._fd = None
                    return False
                time.sleep(0.05)

    def release(self) -> None:
        """Release the advisory lock via fcntl.flock(fd, LOCK_UN)."""
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = None

    def __enter__(self):
        if not self.acquire():
            raise TimeoutError(
                f"Could not acquire lock on {self.lock_path} "
                f"within {self.timeout}s (including retry)"
            )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()
        return False


class CronParser:
    """Parser and evaluator for 5-field cron expressions.

    Supports: minute, hour, day-of-month, month, day-of-week
    Syntax per field: integers, wildcards (*), step values (*/N),
    ranges (N-M), comma-separated lists (A,B,C).

    Only uses Python standard library (R13-AC4).
    """

    # Valid ranges for each cron field
    FIELD_RANGES = [
        (0, 59),   # minute
        (0, 23),   # hour
        (1, 31),   # day of month
        (1, 12),   # month
        (0, 6),    # day of week (0=Sunday)
    ]

    FIELD_NAMES = ["minute", "hour", "day-of-month", "month", "day-of-week"]

    @staticmethod
    def validate(expression: str) -> bool:
        """Validate a 5-field cron expression.

        Returns True if the expression is syntactically valid.
        """
        try:
            fields = expression.strip().split()
            if len(fields) != 5:
                return False

            for i, field in enumerate(fields):
                min_val, max_val = CronParser.FIELD_RANGES[i]
                if not CronParser._validate_field(field, min_val, max_val):
                    return False
            return True
        except (ValueError, IndexError):
            return False

    @staticmethod
    def _validate_field(field: str, min_val: int, max_val: int) -> bool:
        """Validate a single cron field."""
        # Handle comma-separated lists
        parts = field.split(",")
        for part in parts:
            if not CronParser._validate_part(part, min_val, max_val):
                return False
        return True

    @staticmethod
    def _validate_part(part: str, min_val: int, max_val: int) -> bool:
        """Validate a single part of a cron field (no commas)."""
        # Wildcard
        if part == "*":
            return True

        # Step value: */N or N-M/S
        if "/" in part:
            base, step_str = part.split("/", 1)
            try:
                step = int(step_str)
                if step < 1:
                    return False
            except ValueError:
                return False

            if base == "*":
                return True
            # Range with step: N-M/S
            if "-" in base:
                return CronParser._validate_range(base, min_val, max_val)
            # Single value with step doesn't make much sense but some
            # implementations allow it - we'll reject it for clarity
            return False

        # Range: N-M
        if "-" in part:
            return CronParser._validate_range(part, min_val, max_val)

        # Single integer
        try:
            val = int(part)
            return min_val <= val <= max_val
        except ValueError:
            return False

    @staticmethod
    def _validate_range(range_str: str, min_val: int, max_val: int) -> bool:
        """Validate a range expression N-M."""
        parts = range_str.split("-", 1)
        if len(parts) != 2:
            return False
        try:
            low = int(parts[0])
            high = int(parts[1])
            return min_val <= low <= max_val and min_val <= high <= max_val and low <= high
        except ValueError:
            return False

    @staticmethod
    def matches(expression: str, dt: datetime) -> bool:
        """Check if a datetime matches a 5-field cron expression.

        Args:
            expression: A 5-field cron expression string.
            dt: The datetime to check against.

        Returns:
            True if the datetime matches the cron expression.
        """
        fields = expression.strip().split()
        if len(fields) != 5:
            raise ValueError(f"Invalid cron expression: expected 5 fields, got {len(fields)}")

        # Extract datetime components
        values = [
            dt.minute,
            dt.hour,
            dt.day,
            dt.month,
            dt.weekday(),  # Python: 0=Monday; cron: 0=Sunday
        ]
        # Convert Python weekday (0=Mon) to cron weekday (0=Sun)
        # Python: Mon=0, Tue=1, ..., Sun=6
        # Cron:   Sun=0, Mon=1, ..., Sat=6
        values[4] = (dt.weekday() + 1) % 7

        for i, (field, value) in enumerate(zip(fields, values)):
            min_val, max_val = CronParser.FIELD_RANGES[i]
            if not CronParser._field_matches(field, value, min_val, max_val):
                return False
        return True

    @staticmethod
    def _field_matches(field: str, value: int, min_val: int, max_val: int) -> bool:
        """Check if a value matches a cron field expression."""
        allowed = CronParser._expand_field(field, min_val, max_val)
        return value in allowed

    @staticmethod
    def _expand_field(field: str, min_val: int, max_val: int) -> set:
        """Expand a cron field expression into the set of matching values."""
        result = set()
        parts = field.split(",")
        for part in parts:
            result.update(CronParser._expand_part(part, min_val, max_val))
        return result

    @staticmethod
    def _expand_part(part: str, min_val: int, max_val: int) -> set:
        """Expand a single part (no commas) into matching values."""
        # Step value
        if "/" in part:
            base, step_str = part.split("/", 1)
            step = int(step_str)

            if base == "*":
                start = min_val
                end = max_val
            elif "-" in base:
                range_parts = base.split("-", 1)
                start = int(range_parts[0])
                end = int(range_parts[1])
            else:
                start = int(base)
                end = max_val

            return set(range(start, end + 1, step))

        # Wildcard
        if part == "*":
            return set(range(min_val, max_val + 1))

        # Range
        if "-" in part:
            range_parts = part.split("-", 1)
            low = int(range_parts[0])
            high = int(range_parts[1])
            return set(range(low, high + 1))

        # Single value
        return {int(part)}

    @staticmethod
    def next_run(expression: str, after: datetime) -> datetime:
        """Calculate the next execution time after a given datetime.

        Args:
            expression: A 5-field cron expression string.
            after: Find the next match strictly after this datetime.

        Returns:
            The next datetime that matches the cron expression.

        Raises:
            ValueError: If no match found within 4 years (safety limit).
        """
        if not CronParser.validate(expression):
            raise ValueError(f"Invalid cron expression: {expression}")

        # Start from the next minute (cron has minute-level granularity)
        candidate = after.replace(second=0, microsecond=0) + timedelta(minutes=1)

        # Safety limit: don't search more than 4 years ahead
        max_date = after + timedelta(days=366 * 4)

        while candidate <= max_date:
            if CronParser.matches(expression, candidate):
                return candidate
            candidate += timedelta(minutes=1)

        raise ValueError(
            f"No matching time found for '{expression}' within 4 years after {after}"
        )

    @staticmethod
    def can_match_within_days(expression: str, days: int = 366) -> bool:
        """Return True if expression produces at least one match within N days.

        Tries to find a next_run within the given number of days from now.
        Returns False if no match found (expression may never fire).

        Args:
            expression: A 5-field cron expression string.
            days: Number of days to search forward (default 366).

        Returns:
            True if at least one match exists within the window.
        """
        if not CronParser.validate(expression):
            return False

        now = datetime.now(timezone.utc)
        candidate = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
        max_date = now + timedelta(days=days)

        while candidate <= max_date:
            if CronParser.matches(expression, candidate):
                return True
            candidate += timedelta(minutes=1)

        return False


# Valid job name pattern: 1-64 chars, lowercase alphanumeric, hyphens, underscores
JOB_NAME_PATTERN = re.compile(r"^[a-z0-9_-]{1,64}$")

# Valid job types and states
VALID_JOB_TYPES = {"scheduled", "persistent", "agent"}
VALID_JOB_STATES = {
    "pending", "scheduled", "running", "completed",
    "failed", "cancelled", "crash_loop",
}

# Cancel state machine
CANCELLABLE_STATES = {"pending", "scheduled", "running"}
TERMINAL_STATES = {"completed", "failed", "cancelled", "crash_loop"}


class JobQueue:
    """Manages the job queue stored in ~/.kognisant_core/jobs.json.

    Provides CRUD operations on jobs with advisory file locking for
    concurrent access safety. Writes are atomic via temp file + fsync + rename.
    Storage format: {"schema_version": 1, "jobs": [...]}

    Per R2-AC1,3,4 | R7-AC1,6,8,10 | R11-AC1,2,3,4,5.
    """

    def __init__(self, base_dir: str | None = None):
        """Initialize JobQueue with configurable base directory.

        Args:
            base_dir: Override base directory (useful for testing).
                      Defaults to ~/.kognisant_core/
        """
        if base_dir is None:
            base_dir = os.path.expanduser("~/.kognisant_core")
        self.base_dir = base_dir
        self.queue_path = os.path.join(base_dir, "jobs.json")
        self.backup_path = os.path.join(base_dir, "jobs.json.bak")
        self.lock_path = os.path.join(base_dir, "jobs.lock")
        self.logs_dir = os.path.join(base_dir, "logs")

    @property
    def BACKUP_PATH(self) -> str:
        """Path to the backup file."""
        return self.backup_path

    def _atomic_save(self, data: dict) -> None:
        """Atomic write with fsync and backup.

        Sequence: tempfile.mkstemp → json.dump → flush() → os.fsync(fd)
        → os.chmod(tmp, 0o600) → os.rename(tmp, QUEUE_PATH) → os.fsync(dirfd)
        → shutil.copy2(QUEUE_PATH, BACKUP_PATH) → os.chmod(BACKUP_PATH, 0o600)
        → os.fsync(bak_fd).

        On rename failure: os.unlink(tmp), leave existing unchanged.
        Sets file permissions to 0o600 on both primary and backup.
        """
        os.makedirs(self.base_dir, exist_ok=True)

        fd, tmp_path = tempfile.mkstemp(
            dir=self.base_dir, suffix=".tmp", prefix="jobs_"
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())

            os.chmod(tmp_path, 0o600)

            # Atomic rename
            os.rename(tmp_path, self.queue_path)
        except OSError:
            # On rename failure: remove temp, leave existing unchanged
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

        # fsync directory to make rename durable
        try:
            dirfd = os.open(self.base_dir, os.O_RDONLY)
            try:
                os.fsync(dirfd)
            finally:
                os.close(dirfd)
        except OSError:
            pass

        # Create backup
        try:
            shutil.copy2(self.queue_path, self.backup_path)
            os.chmod(self.backup_path, 0o600)
            bak_fd = os.open(self.backup_path, os.O_RDONLY)
            try:
                os.fsync(bak_fd)
            finally:
                os.close(bak_fd)
        except OSError:
            pass

    def _load_raw(self) -> dict:
        """Load raw JSON from queue file with recovery fallback.

        Decision tree:
        1. Primary exists + valid JSON + recognized schema_version → return data
        2. Primary exists + valid JSON + unrecognized schema_version → raise ValueError
        3. Primary exists + invalid JSON → try .bak, log warning
        4. Primary missing + .bak exists → restore from .bak, log warning
        5. Both missing/corrupted → return {"schema_version": 1, "jobs": []}
        6. Primary exists + bare array (legacy) → migrate to versioned format
        """
        os.makedirs(self.base_dir, exist_ok=True)

        if os.path.exists(self.queue_path):
            try:
                with open(self.queue_path, "r") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError):
                # Primary corrupted → try .bak
                logger.warning(
                    "Primary jobs.json corrupted, attempting backup recovery"
                )
                return self._recover_from_backup()

            # Valid JSON — check structure
            if isinstance(data, list):
                # Legacy bare array format → wrap and migrate
                wrapped = {"schema_version": CURRENT_SCHEMA_VERSION, "jobs": data}
                self._atomic_save(wrapped)
                logger.info("Migrated legacy bare-array format to versioned schema")
                return wrapped

            if isinstance(data, dict):
                if "schema_version" in data:
                    version = data["schema_version"]
                    if isinstance(version, int) and version <= CURRENT_SCHEMA_VERSION:
                        return data
                    else:
                        raise ValueError(
                            f"Unknown schema version {version}. "
                            f"Refusing to process. This file may be from "
                            f"a newer version of Kognisant."
                        )
                else:
                    # Dict without schema_version → treat as corrupted
                    logger.warning(
                        "jobs.json is a dict without schema_version, "
                        "attempting backup recovery"
                    )
                    return self._recover_from_backup()

            # Not a dict or list → treat as corrupted
            logger.warning(
                "jobs.json contains unexpected type, attempting backup recovery"
            )
            return self._recover_from_backup()
        else:
            # Primary missing
            if os.path.exists(self.backup_path):
                logger.warning(
                    "Primary jobs.json missing, restoring from backup"
                )
                return self._recover_from_backup()
            else:
                # Both missing → init empty
                empty = {"schema_version": CURRENT_SCHEMA_VERSION, "jobs": []}
                self._atomic_save(empty)
                return empty

    def _recover_from_backup(self) -> dict:
        """Attempt recovery from .bak file. Returns data or empty schema."""
        if os.path.exists(self.backup_path):
            try:
                with open(self.backup_path, "r") as f:
                    data = json.load(f)
                if isinstance(data, dict) and "schema_version" in data:
                    version = data["schema_version"]
                    if isinstance(version, int) and version <= CURRENT_SCHEMA_VERSION:
                        logger.warning(
                            "Recovered from backup file jobs.json.bak"
                        )
                        self._atomic_save(data)
                        return data
            except (json.JSONDecodeError, OSError):
                pass

        # Both missing or corrupted
        logger.error(
            "Both primary and backup missing/corrupted. Data loss. "
            "Initializing empty job queue."
        )
        empty = {"schema_version": CURRENT_SCHEMA_VERSION, "jobs": []}
        self._atomic_save(empty)
        return empty

    def _migrate_if_needed(self, data: dict) -> dict:
        """Apply pending migrations via MigrationRegistry.apply_pending().

        Args:
            data: Current versioned data structure.

        Returns:
            Fully migrated data structure.
        """
        return MigrationRegistry.apply_pending(data, self._atomic_save)

    def _locked_modify(self, fn: Callable[[list[dict]], list[dict]]) -> None:
        """Hold Advisory_Lock across: load → fn(jobs) → atomic_save.

        The lock is held for the entire read-modify-write cycle.
        Raises TimeoutError if lock not acquired within 5 seconds.
        """
        with FileLock(self.lock_path):
            data = self._load_raw()
            data = self._migrate_if_needed(data)
            jobs = data.get("jobs", [])
            jobs = fn(jobs)
            data["jobs"] = jobs
            self._atomic_save(data)

    def load(self) -> list[dict]:
        """Load jobs from the queue file with advisory lock.

        Returns an empty list if the file does not exist or contains
        malformed JSON (logs error in the latter case).
        """
        os.makedirs(self.base_dir, exist_ok=True)

        try:
            with FileLock(self.lock_path):
                data = self._load_raw()
                data = self._migrate_if_needed(data)
                return data.get("jobs", [])
        except (ValueError, TimeoutError) as e:
            logger.error("Failed to load job queue: %s", e)
            return []

    def save(self, jobs: list[dict]) -> None:
        """Atomically save jobs to the queue file in versioned format.

        Wraps the jobs list into the versioned schema and saves atomically.
        Lock is acquired before writing.
        """
        os.makedirs(self.base_dir, exist_ok=True)

        with FileLock(self.lock_path):
            data = {"schema_version": CURRENT_SCHEMA_VERSION, "jobs": jobs}
            self._atomic_save(data)

    def add_job(self, job_config: dict) -> str:
        """Add a new job to the queue.

        Validates the job name and checks for duplicates before adding.
        Sets initial state to "pending" and created_at to current UTC time.

        Args:
            job_config: Dict with at minimum "name" and "type" keys.
                        Optional: script_path, task, cron_expression, env_vars,
                        project_root.

        Returns:
            Success message string.

        Raises:
            ValueError: If name is invalid, type is invalid, or name already exists.
        """
        name = job_config.get("name", "")
        if not JOB_NAME_PATTERN.match(name):
            raise ValueError(
                f"Invalid job name '{name}': must be 1-64 characters, "
                "lowercase alphanumeric, hyphens, or underscores only"
            )

        job_type = job_config.get("type", "")
        if job_type not in VALID_JOB_TYPES:
            raise ValueError(
                f"Invalid job type '{job_type}': must be one of "
                f"{sorted(VALID_JOB_TYPES)}"
            )

        # Build the full job entry
        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        job_entry = {
            "name": name,
            "type": job_type,
            "state": "pending",
            "script_path": job_config.get("script_path", ""),
            "task": job_config.get("task", None),
            "project_root": job_config.get("project_root", None),
            "cron_expression": job_config.get("cron_expression", None),
            "env_vars": job_config.get("env_vars", {}),
            "scheduler_policy": job_config.get("scheduler_policy", "skip"),
            "created_at": now_utc,
            "last_run_at": None,
            "last_exit_code": None,
            "run_count": 0,
            "pid": None,
            "pid_started_at": None,
            "restart_count": 0,
            "restart_timestamps": [],
        }

        result_msg = ""

        def _add(jobs: list[dict]) -> list[dict]:
            nonlocal result_msg
            # Check for duplicates
            for existing in jobs:
                if existing.get("name") == name:
                    raise ValueError(
                        f"A job with name '{name}' already exists"
                    )
            jobs.append(job_entry)
            result_msg = f"Job '{name}' added successfully"
            return jobs

        self._locked_modify(_add)
        return result_msg

    def remove_job(self, name: str) -> bool:
        """Remove a job from the queue by name.

        Args:
            name: The job name to remove.

        Returns:
            True if the job was found and removed, False otherwise.
        """
        removed = [False]

        def _remove(jobs: list[dict]) -> list[dict]:
            original_count = len(jobs)
            new_jobs = [j for j in jobs if j.get("name") != name]
            removed[0] = len(new_jobs) < original_count
            return new_jobs

        self._locked_modify(_remove)
        return removed[0]

    def update_status(self, name: str, state: str, **kwargs) -> bool:
        """Update a job's state and additional fields.

        Args:
            name: The job name to update.
            state: The new state value.
            **kwargs: Additional fields to update (pid, last_run_at,
                      restart_count, restart_timestamps, etc.)

        Returns:
            True if the job was found and updated, False otherwise.
        """
        found = [False]

        def _update(jobs: list[dict]) -> list[dict]:
            for job in jobs:
                if job.get("name") == name:
                    job["state"] = state
                    for key, value in kwargs.items():
                        job[key] = value
                    found[0] = True
                    break
            return jobs

        self._locked_modify(_update)
        return found[0]

    def cancel_job(self, name: str) -> str:
        """Cancel a job with state validation.

        Verifies the job is in a cancellable state (pending, scheduled, running)
        before allowing the cancel. Terminal states cannot be cancelled.

        Args:
            name: The job name to cancel.

        Returns:
            Success message or error message (using format_error).
        """
        job = self.get_job(name)
        if job is None:
            return format_error(
                "not_found",
                f"Job '{name}' does not exist",
                "Use 'kognisant job list' to see available jobs."
            )

        current_state = job.get("state", "")
        if current_state in TERMINAL_STATES:
            return format_error(
                "state",
                f"Job '{name}' is in '{current_state}' state and cannot be cancelled"
            )

        if current_state not in CANCELLABLE_STATES:
            return format_error(
                "state",
                f"Job '{name}' is in '{current_state}' state and cannot be cancelled"
            )

        self.update_status(name, "cancelled", pid=None)
        return f"Job '{name}' cancelled successfully"

    def get_job(self, name: str) -> dict | None:
        """Get a single job by name.

        Args:
            name: The job name to look up.

        Returns:
            The job dict if found, None otherwise.
        """
        jobs = self.load()
        for job in jobs:
            if job.get("name") == name:
                return job
        return None

    def get_pending_jobs(self) -> list[dict]:
        """Return all jobs with state 'pending'.

        Per R2-AC1: daemon polls for pending jobs.
        """
        jobs = self.load()
        return [j for j in jobs if j.get("state") == "pending"]

    def get_due_scheduled(self, now: datetime) -> list[dict]:
        """Return scheduled jobs whose cron expression matches the given time.

        Args:
            now: The current datetime to check cron expressions against.

        Returns:
            List of job dicts that are due for execution.
        """
        jobs = self.load()
        due = []
        for job in jobs:
            if job.get("type") != "scheduled":
                continue
            if job.get("state") not in ("pending", "scheduled"):
                continue
            cron_expr = job.get("cron_expression")
            if not cron_expr:
                continue
            try:
                if CronParser.matches(cron_expr, now):
                    due.append(job)
            except (ValueError, TypeError):
                logger.warning(
                    "Invalid cron expression for job '%s': %s",
                    job.get("name"), cron_expr,
                )
        return due

    def get_persistent_jobs(self) -> list[dict]:
        """Return all persistent type jobs."""
        jobs = self.load()
        return [j for j in jobs if j.get("type") == "persistent"]

    def get_job_log_path(self, name: str) -> str:
        """Return the path to a job's log file."""
        return os.path.join(self.logs_dir, f"{name}.log")

    def read_job_logs(self, name: str, lines: int = 50) -> str:
        """Read the last N lines of a job's log file.

        Args:
            name: The job name.
            lines: Number of lines to return (default 50).

        Returns:
            String with the last N lines, or an error message if
            the log file doesn't exist.
        """
        log_path = self.get_job_log_path(name)

        if not os.path.exists(log_path):
            return f"No logs available for job '{name}'"

        try:
            with open(log_path, "r") as f:
                all_lines = f.readlines()
            tail = all_lines[-lines:] if len(all_lines) > lines else all_lines
            return "".join(tail)
        except OSError as e:
            return f"Error reading logs for job '{name}': {e}"
