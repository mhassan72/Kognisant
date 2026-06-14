"""
Job queue state management, cron parsing, and file locking.

Uses only Python standard library modules per Requirement 13.
"""

import fcntl
import json
import logging
import os
import re
import tempfile
import time
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


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


# Valid job name pattern: 1-64 chars, lowercase alphanumeric, hyphens, underscores
JOB_NAME_PATTERN = re.compile(r"^[a-z0-9_-]{1,64}$")

# Valid job types and states
VALID_JOB_TYPES = {"scheduled", "persistent", "agent"}
VALID_JOB_STATES = {
    "pending", "scheduled", "running", "completed",
    "failed", "cancelled", "crash_loop",
}


class JobQueue:
    """Manages the job queue stored in ~/.kognisant_core/jobs.json.

    Provides CRUD operations on jobs with advisory file locking for
    concurrent access safety. Writes are atomic via temp file + rename.

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
        self.lock_path = os.path.join(base_dir, "jobs.lock")
        self.logs_dir = os.path.join(base_dir, "logs")

    def load(self) -> list[dict]:
        """Load jobs from the queue file with advisory lock.

        Returns an empty list if the file does not exist or contains
        malformed JSON (logs error in the latter case).

        Per R2-AC3 (missing file), R2-AC4 (malformed JSON).
        """
        os.makedirs(self.base_dir, exist_ok=True)

        if not os.path.exists(self.queue_path):
            return []

        try:
            with FileLock(self.lock_path):
                with open(self.queue_path, "r") as f:
                    data = json.load(f)
                if not isinstance(data, list):
                    logger.error(
                        "Job queue file %s does not contain a JSON array",
                        self.queue_path,
                    )
                    return []
                return data
        except json.JSONDecodeError as e:
            logger.error(
                "Malformed JSON in job queue file %s: %s",
                self.queue_path, e,
            )
            return []
        except TimeoutError:
            logger.error(
                "Could not acquire lock to read job queue at %s",
                self.queue_path,
            )
            return []

    def save(self, jobs: list[dict]) -> None:
        """Atomically save jobs to the queue file.

        Writes to a temporary file first, then renames to the target path
        to prevent partial writes. Lock is acquired before writing.

        Per R11-AC3 (atomic write via temp+rename).
        """
        os.makedirs(self.base_dir, exist_ok=True)

        with FileLock(self.lock_path):
            # Write to temp file in the same directory (same filesystem for rename)
            fd, tmp_path = tempfile.mkstemp(
                dir=self.base_dir, suffix=".tmp", prefix="jobs_"
            )
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(jobs, f, indent=2)
                os.rename(tmp_path, self.queue_path)
            except Exception:
                # Clean up temp file on failure
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
                raise

    def add_job(self, job_config: dict) -> str:
        """Add a new job to the queue.

        Validates the job name and checks for duplicates before adding.
        Sets initial state to "pending" and created_at to current UTC time.

        Args:
            job_config: Dict with at minimum "name" and "type" keys.
                        Optional: script_path, task, cron_expression, env_vars.

        Returns:
            Success message string.

        Raises:
            ValueError: If name is invalid, type is invalid, or name already exists.

        Per R7-AC10 (name validation), R7-AC6 (duplicate detection).
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

        jobs = self.load()

        # Check for duplicates
        for existing in jobs:
            if existing.get("name") == name:
                raise ValueError(
                    f"A job with name '{name}' already exists"
                )

        # Build the full job entry per R11-AC5
        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        job_entry = {
            "name": name,
            "type": job_type,
            "state": "pending",
            "script_path": job_config.get("script_path", ""),
            "task": job_config.get("task", None),
            "cron_expression": job_config.get("cron_expression", None),
            "env_vars": job_config.get("env_vars", {}),
            "created_at": now_utc,
            "last_run_at": None,
            "pid": None,
            "restart_count": 0,
            "restart_timestamps": [],
        }

        jobs.append(job_entry)
        self.save(jobs)
        return f"Job '{name}' added successfully"

    def remove_job(self, name: str) -> bool:
        """Remove a job from the queue by name.

        Args:
            name: The job name to remove.

        Returns:
            True if the job was found and removed, False otherwise.
        """
        jobs = self.load()
        original_count = len(jobs)
        jobs = [j for j in jobs if j.get("name") != name]

        if len(jobs) == original_count:
            return False

        self.save(jobs)
        return True

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
        jobs = self.load()

        for job in jobs:
            if job.get("name") == name:
                job["state"] = state
                for key, value in kwargs.items():
                    job[key] = value
                self.save(jobs)
                return True

        return False

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

        Per R2-AC5: daemon executes scheduled jobs matching current time.
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
        """Return all persistent type jobs.

        Per R2-AC6: daemon manages persistent jobs.
        """
        jobs = self.load()
        return [j for j in jobs if j.get("type") == "persistent"]

    def get_job_log_path(self, name: str) -> str:
        """Return the path to a job's log file.

        Args:
            name: The job name.

        Returns:
            Absolute path to ~/.kognisant_core/logs/{job_name}.log
        """
        return os.path.join(self.logs_dir, f"{name}.log")

    def read_job_logs(self, name: str, lines: int = 50) -> str:
        """Read the last N lines of a job's log file.

        Args:
            name: The job name.
            lines: Number of lines to return (default 50).

        Returns:
            String with the last N lines, or an error message if
            the log file doesn't exist.

        Per R7-AC11: error if log file doesn't exist.
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
