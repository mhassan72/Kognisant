"""Tests for crash recovery and backup restoration.

Requirements covered: 38.1, 38.2, 41.1, 41.2
"""

import json
import os

import pytest

from cli_kognisant.jobs import JobQueue, CURRENT_SCHEMA_VERSION


class TestCrashRecovery:
    """Test recovery when jobs.json is removed or corrupted."""

    def test_recovery_from_backup_after_primary_removed(self, tmp_core_dir):
        """Write valid data, remove jobs.json, verify _load_raw() recovers from .bak.

        Simulates a crash by removing the primary file while .bak exists.
        Requirement 38.1
        """
        queue = JobQueue(base_dir=str(tmp_core_dir))

        # Write valid data (creates both primary and .bak)
        queue.add_job({"name": "recover-me", "type": "agent", "task": "test"})

        # Verify primary exists and .bak was created
        assert os.path.exists(queue.queue_path)
        assert os.path.exists(queue.backup_path)

        # Simulate crash: remove primary
        os.unlink(queue.queue_path)
        assert not os.path.exists(queue.queue_path)

        # Load should recover from backup
        jobs = queue.load()
        assert len(jobs) == 1
        assert jobs[0]["name"] == "recover-me"

        # Primary should be restored
        assert os.path.exists(queue.queue_path)

    def test_recovery_from_backup_after_corruption(self, tmp_core_dir):
        """Write valid data, corrupt jobs.json, verify recovery from .bak.

        Simulates corruption by writing invalid JSON to the primary file.
        Requirement 38.2
        """
        queue = JobQueue(base_dir=str(tmp_core_dir))

        # Write valid data
        queue.add_job({"name": "safe-job", "type": "persistent", "script_path": "x.py"})

        # Verify .bak exists
        assert os.path.exists(queue.backup_path)

        # Simulate corruption: overwrite primary with invalid JSON
        with open(queue.queue_path, "w") as f:
            f.write("{this is not valid json!!!")

        # Load should recover from backup
        jobs = queue.load()
        assert len(jobs) == 1
        assert jobs[0]["name"] == "safe-job"

    def test_both_files_missing_initializes_empty(self, tmp_core_dir):
        """When both primary and backup are missing, initialize empty queue."""
        queue = JobQueue(base_dir=str(tmp_core_dir))

        # Neither file exists yet — load should return empty
        jobs = queue.load()
        assert jobs == []

        # Primary should now exist with empty schema
        assert os.path.exists(queue.queue_path)
        with open(queue.queue_path) as f:
            data = json.load(f)
        assert data["schema_version"] == CURRENT_SCHEMA_VERSION
        assert data["jobs"] == []


class TestBackupRecoveryVerification:
    """Tests verifying backup file behavior.

    Requirements covered: 41.1, 41.2
    """

    def test_valid_backup_restores_when_primary_missing(self, tmp_core_dir):
        """Valid .bak + missing primary → load() returns .bak data.

        Requirement 41.1
        """
        queue = JobQueue(base_dir=str(tmp_core_dir))

        # Write valid backup directly
        backup_data = {
            "schema_version": CURRENT_SCHEMA_VERSION,
            "jobs": [
                {
                    "name": "backup-job",
                    "type": "agent",
                    "state": "pending",
                    "script_path": "",
                    "task": "Backed up task",
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
        os.makedirs(str(tmp_core_dir), exist_ok=True)
        with open(queue.backup_path, "w") as f:
            json.dump(backup_data, f)

        # Primary doesn't exist — should restore from backup
        jobs = queue.load()
        assert len(jobs) == 1
        assert jobs[0]["name"] == "backup-job"
        assert jobs[0]["task"] == "Backed up task"

    def test_backup_updated_after_successful_save(self, tmp_core_dir):
        """Verify .bak is updated after every successful _atomic_save.

        Requirement 41.2
        """
        queue = JobQueue(base_dir=str(tmp_core_dir))

        # Add first job
        queue.add_job({"name": "job-one", "type": "agent", "task": "first"})

        # Check .bak contains the first job
        with open(queue.backup_path) as f:
            bak_data = json.load(f)
        assert len(bak_data["jobs"]) == 1
        assert bak_data["jobs"][0]["name"] == "job-one"

        # Add second job
        queue.add_job({"name": "job-two", "type": "agent", "task": "second"})

        # Check .bak now contains both jobs
        with open(queue.backup_path) as f:
            bak_data = json.load(f)
        assert len(bak_data["jobs"]) == 2
        names = {j["name"] for j in bak_data["jobs"]}
        assert "job-one" in names
        assert "job-two" in names

    def test_corrupted_backup_falls_through_to_empty(self, tmp_core_dir):
        """When both primary is corrupted and backup is also corrupted, init empty."""
        queue = JobQueue(base_dir=str(tmp_core_dir))
        os.makedirs(str(tmp_core_dir), exist_ok=True)

        # Write corrupted primary
        with open(queue.queue_path, "w") as f:
            f.write("not json")

        # Write corrupted backup
        with open(queue.backup_path, "w") as f:
            f.write("also not json")

        # Should fall through to empty initialization
        jobs = queue.load()
        assert jobs == []
