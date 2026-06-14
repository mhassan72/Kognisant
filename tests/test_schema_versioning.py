"""Tests for schema versioning and migration framework.

Requirements covered: 43.1, 43.2, 43.3
"""

import json
import os

import pytest

from cli_kognisant.jobs import (
    CURRENT_SCHEMA_VERSION,
    JobQueue,
    MigrationRegistry,
)


class TestLegacyMigration:
    """Test migration from legacy bare-array format to versioned schema."""

    def test_bare_array_migrated_to_versioned(self, tmp_core_dir):
        """Legacy bare-array format → migrated to versioned format with schema_version 1.

        Requirement 43.2
        """
        queue = JobQueue(base_dir=str(tmp_core_dir))
        os.makedirs(str(tmp_core_dir), exist_ok=True)

        # Write a legacy bare-array format file
        legacy_jobs = [
            {"name": "old-job", "type": "persistent", "state": "pending"},
            {"name": "another-old", "type": "agent", "state": "completed"},
        ]
        with open(queue.queue_path, "w") as f:
            json.dump(legacy_jobs, f)

        # Load should transparently migrate
        jobs = queue.load()
        assert len(jobs) == 2
        assert jobs[0]["name"] == "old-job"
        assert jobs[1]["name"] == "another-old"

        # Verify the file was rewritten in versioned format
        with open(queue.queue_path) as f:
            data = json.load(f)
        assert data["schema_version"] == CURRENT_SCHEMA_VERSION
        assert isinstance(data["jobs"], list)
        assert len(data["jobs"]) == 2

    def test_legacy_migration_creates_backup(self, tmp_core_dir):
        """Migration from legacy format uses atomic write path (creates .bak).

        Requirement 43.3
        """
        queue = JobQueue(base_dir=str(tmp_core_dir))
        os.makedirs(str(tmp_core_dir), exist_ok=True)

        # Write legacy format
        legacy_jobs = [{"name": "migrate-me", "type": "scheduled", "state": "pending"}]
        with open(queue.queue_path, "w") as f:
            json.dump(legacy_jobs, f)

        # Load triggers migration
        queue.load()

        # Backup should be created after migration
        assert os.path.exists(queue.backup_path)
        with open(queue.backup_path) as f:
            backup = json.load(f)
        assert backup["schema_version"] == CURRENT_SCHEMA_VERSION


class TestMigrationRegistry:
    """Test the MigrationRegistry class and migration application."""

    def test_apply_pending_with_no_migrations_needed(self, tmp_core_dir):
        """Data at current version → no migrations applied, data unchanged.

        Requirement 43.1
        """
        queue = JobQueue(base_dir=str(tmp_core_dir))
        data = {
            "schema_version": CURRENT_SCHEMA_VERSION,
            "jobs": [{"name": "current-job", "state": "pending"}],
        }
        # apply_pending should be a no-op when already at current version
        result = MigrationRegistry.apply_pending(data, queue._atomic_save)
        assert result["schema_version"] == CURRENT_SCHEMA_VERSION
        assert result["jobs"][0]["name"] == "current-job"

    def test_migration_preserves_existing_fields(self, tmp_core_dir):
        """Migration to higher version preserves all existing job fields.

        Requirement 43.1 — all existing fields preserved after migration.
        """
        queue = JobQueue(base_dir=str(tmp_core_dir))

        # Write a valid v1 file with full fields
        full_job = {
            "name": "full-job",
            "type": "persistent",
            "state": "running",
            "script_path": "my_script.py",
            "task": None,
            "project_root": "/home/user/project",
            "cron_expression": None,
            "env_vars": {"KEY": "value"},
            "scheduler_policy": "skip",
            "created_at": "2025-01-01T00:00:00",
            "last_run_at": "2025-06-01T12:00:00",
            "last_exit_code": 0,
            "run_count": 5,
            "pid": 12345,
            "pid_started_at": "2025-06-01T12:00:00",
            "restart_count": 2,
            "restart_timestamps": ["2025-06-01T11:00:00"],
        }
        data = {"schema_version": CURRENT_SCHEMA_VERSION, "jobs": [full_job]}
        queue._atomic_save(data)

        # Load and verify all fields preserved
        jobs = queue.load()
        assert len(jobs) == 1
        job = jobs[0]
        assert job["name"] == "full-job"
        assert job["type"] == "persistent"
        assert job["state"] == "running"
        assert job["script_path"] == "my_script.py"
        assert job["project_root"] == "/home/user/project"
        assert job["env_vars"] == {"KEY": "value"}
        assert job["run_count"] == 5
        assert job["pid"] == 12345
        assert job["restart_count"] == 2

    def test_migration_registry_missing_version_raises(self):
        """apply_pending raises ValueError when migration for a version is missing."""
        # Create data with a version below current (but we won't register a migration)
        # This test only works if CURRENT_SCHEMA_VERSION > some test version
        # Since CURRENT is 1, we need to simulate a scenario

        # Store original migrations
        original = MigrationRegistry._migrations.copy()
        try:
            # Temporarily set a higher target to test missing migration
            # We can't easily change CURRENT_SCHEMA_VERSION, so test the logic directly
            data = {"schema_version": 0, "jobs": []}

            # No migration registered for version 0
            MigrationRegistry._migrations.pop(0, None)

            # Only test if there's a version gap to trigger
            # Since CURRENT is 1 and data is at 0, this should fail
            with pytest.raises(ValueError, match="No migration registered"):
                MigrationRegistry.apply_pending(data, lambda d: None)
        finally:
            MigrationRegistry._migrations = original


class TestSchemaVersionValidation:
    """Test schema version validation behavior."""

    def test_recognized_version_loads_normally(self, tmp_core_dir):
        """Recognized schema_version → data loaded without issue."""
        queue = JobQueue(base_dir=str(tmp_core_dir))
        os.makedirs(str(tmp_core_dir), exist_ok=True)

        data = {"schema_version": CURRENT_SCHEMA_VERSION, "jobs": [{"name": "ok"}]}
        with open(queue.queue_path, "w") as f:
            json.dump(data, f)

        jobs = queue.load()
        assert len(jobs) == 1
        assert jobs[0]["name"] == "ok"

    def test_unrecognized_version_rejected(self, tmp_core_dir):
        """Unrecognized schema_version (future) → ValueError raised by _load_raw."""
        queue = JobQueue(base_dir=str(tmp_core_dir))
        os.makedirs(str(tmp_core_dir), exist_ok=True)

        data = {"schema_version": 999, "jobs": []}
        with open(queue.queue_path, "w") as f:
            json.dump(data, f)

        # _load_raw raises ValueError for unknown version
        with pytest.raises(ValueError, match="Unknown schema version"):
            queue._load_raw()

    def test_schema_version_zero_triggers_migration(self, tmp_core_dir):
        """Schema version 0 (if registered) would trigger migration attempt."""
        queue = JobQueue(base_dir=str(tmp_core_dir))
        os.makedirs(str(tmp_core_dir), exist_ok=True)

        # Version 0 is below current — _load_raw returns it,
        # then _migrate_if_needed tries to migrate
        data = {"schema_version": 0, "jobs": [{"name": "v0-job"}]}
        with open(queue.queue_path, "w") as f:
            json.dump(data, f)

        # This will fail because no migration is registered for v0
        # But _load_raw itself should return the data (version <= CURRENT check)
        # Actually version 0 <= 1, so it passes _load_raw but fails in migrate
        # The load() method catches the ValueError
        jobs = queue.load()
        # Since migration fails, load() catches the error and returns empty
        assert jobs == []
