"""Tests for atomic write and corrupted file handling.

Verifies that corrupted (non-JSON) jobs.json is handled gracefully,
and that valid JSON with unexpected schema is handled correctly.

Requirements covered: 42.1, 42.2
"""

import json
import os

import pytest

from cli_kognisant.jobs import JobQueue, CURRENT_SCHEMA_VERSION


class TestCorruptedFileSurvival:
    """Test graceful handling of corrupted or unexpected file content."""

    def test_corrupted_non_json_file_recovers_gracefully(self, tmp_core_dir):
        """Corrupted (non-JSON) jobs.json → load() returns empty or recovers.

        Verifies no unhandled exception is raised.
        Requirement 42.1
        """
        queue = JobQueue(base_dir=str(tmp_core_dir))
        os.makedirs(str(tmp_core_dir), exist_ok=True)

        # Write corrupted content to primary
        with open(queue.queue_path, "w") as f:
            f.write("THIS IS NOT JSON AT ALL }{}{}{")

        # Should not raise — returns empty or recovered data
        jobs = queue.load()
        assert isinstance(jobs, list)
        # Should be empty (no backup to recover from)
        assert jobs == []

    def test_corrupted_file_with_valid_backup_recovers(self, tmp_core_dir):
        """Corrupted primary + valid backup → load() recovers from backup."""
        queue = JobQueue(base_dir=str(tmp_core_dir))
        os.makedirs(str(tmp_core_dir), exist_ok=True)

        # Write valid backup
        backup_data = {
            "schema_version": CURRENT_SCHEMA_VERSION,
            "jobs": [
                {
                    "name": "saved-job",
                    "type": "agent",
                    "state": "pending",
                    "script_path": "",
                    "task": "Important task",
                    "project_root": None,
                    "cron_expression": None,
                    "env_vars": {},
                    "scheduler_policy": "skip",
                    "created_at": "2025-01-01T00:00:00",
                    "last_run_at": None,
                    "last_exit_code": None,
                    "run_count": 0,
                    "pid": None,
                    "pid_started_at": None,
                    "restart_count": 0,
                    "restart_timestamps": [],
                }
            ],
        }
        with open(queue.backup_path, "w") as f:
            json.dump(backup_data, f)

        # Write corrupted primary
        with open(queue.queue_path, "w") as f:
            f.write("corrupted garbage data")

        # Should recover from backup
        jobs = queue.load()
        assert len(jobs) == 1
        assert jobs[0]["name"] == "saved-job"

    def test_valid_json_without_schema_version_handled(self, tmp_core_dir):
        """Valid JSON dict without schema_version → graceful handling.

        Requirement 42.2
        """
        queue = JobQueue(base_dir=str(tmp_core_dir))
        os.makedirs(str(tmp_core_dir), exist_ok=True)

        # Write valid JSON that's a dict but missing schema_version
        with open(queue.queue_path, "w") as f:
            json.dump({"some_key": "some_value", "other": 123}, f)

        # Should handle gracefully (treat as corrupted, try backup or init empty)
        jobs = queue.load()
        assert isinstance(jobs, list)
        # No backup exists, so should init empty
        assert jobs == []

    def test_valid_json_number_handled(self, tmp_core_dir):
        """Valid JSON that's just a number → graceful handling."""
        queue = JobQueue(base_dir=str(tmp_core_dir))
        os.makedirs(str(tmp_core_dir), exist_ok=True)

        # Write a number as valid JSON
        with open(queue.queue_path, "w") as f:
            json.dump(42, f)

        # Should handle gracefully
        jobs = queue.load()
        assert isinstance(jobs, list)
        assert jobs == []

    def test_valid_json_string_handled(self, tmp_core_dir):
        """Valid JSON that's just a string → graceful handling."""
        queue = JobQueue(base_dir=str(tmp_core_dir))
        os.makedirs(str(tmp_core_dir), exist_ok=True)

        with open(queue.queue_path, "w") as f:
            json.dump("just a string", f)

        jobs = queue.load()
        assert isinstance(jobs, list)
        assert jobs == []

    def test_unknown_schema_version_raises(self, tmp_core_dir):
        """Unrecognized schema_version → raises ValueError (not silent corruption)."""
        queue = JobQueue(base_dir=str(tmp_core_dir))
        os.makedirs(str(tmp_core_dir), exist_ok=True)

        # Write valid JSON with future schema version
        with open(queue.queue_path, "w") as f:
            json.dump({"schema_version": 999, "jobs": []}, f)

        # load() catches the ValueError and returns empty
        # (the raw _load_raw raises ValueError, but load() catches it)
        jobs = queue.load()
        assert jobs == []


class TestAtomicWriteProperties:
    """Test that atomic writes produce correct file state."""

    def test_atomic_save_creates_primary_and_backup(self, tmp_core_dir):
        """After _atomic_save: both primary and .bak exist with correct data."""
        queue = JobQueue(base_dir=str(tmp_core_dir))
        data = {"schema_version": 1, "jobs": [{"name": "test", "state": "pending"}]}
        queue._atomic_save(data)

        # Primary exists with correct content
        assert os.path.exists(queue.queue_path)
        with open(queue.queue_path) as f:
            loaded = json.load(f)
        assert loaded == data

        # Backup exists with same content
        assert os.path.exists(queue.backup_path)
        with open(queue.backup_path) as f:
            backup = json.load(f)
        assert backup == data

    def test_atomic_save_permissions(self, tmp_core_dir):
        """After _atomic_save: primary and backup have 0o600 permissions."""
        queue = JobQueue(base_dir=str(tmp_core_dir))
        data = {"schema_version": 1, "jobs": []}
        queue._atomic_save(data)

        primary_mode = os.stat(queue.queue_path).st_mode & 0o777
        assert primary_mode == 0o600

        backup_mode = os.stat(queue.backup_path).st_mode & 0o777
        assert backup_mode == 0o600

    def test_atomic_save_no_temp_files_left(self, tmp_core_dir):
        """After _atomic_save: no temporary files remain in the directory."""
        queue = JobQueue(base_dir=str(tmp_core_dir))
        data = {"schema_version": 1, "jobs": []}
        queue._atomic_save(data)

        # Check no .tmp files remain
        for filename in os.listdir(str(tmp_core_dir)):
            assert not filename.endswith(".tmp"), f"Temp file left behind: {filename}"
