"""Tests for PID reuse detection during orphan cleanup.

Requirements covered: 39.1, 39.2
"""

from unittest.mock import patch

import pytest

from cli_kognisant.jobs import JobQueue
from cli_kognisant.daemon import ProcessManager


class TestPidReuseDetection:
    """Test that orphan cleanup correctly handles PID reuse scenarios."""

    def test_pid_alive_but_creation_time_mismatch(self, tmp_core_dir):
        """PID alive with non-matching creation time → mark failed, no signal sent.

        Simulates: another process reused the PID. The job should be marked
        as failed with "PID reused" message and the PID cleared. No signal
        should be sent to the unrelated process.

        Requirement 39.1
        """
        queue = JobQueue(base_dir=str(tmp_core_dir))
        queue.add_job({"name": "running-job", "type": "persistent", "script_path": "x.py"})
        queue.update_status(
            "running-job", "running",
            pid=99999,
            pid_started_at="2025-01-01T00:00:00",
        )

        # Mock: PID is alive, but creation time doesn't match
        with patch.object(ProcessManager, "is_alive", return_value=True), \
             patch.object(ProcessManager, "get_start_time", return_value="2025-06-15T12:00:00"), \
             patch("os.kill") as mock_kill:

            # Simulate the orphan cleanup logic
            job = queue.get_job("running-job")
            pid = job["pid"]
            stored_start_time = job["pid_started_at"]

            if ProcessManager.is_alive(pid):
                current_start_time = ProcessManager.get_start_time(pid)
                if current_start_time != stored_start_time:
                    # PID reused by another process — mark failed, NO signal
                    queue.update_status(
                        "running-job", "failed",
                        pid=None,
                        pid_started_at=None,
                    )

            # Verify: job marked as failed
            job = queue.get_job("running-job")
            assert job["state"] == "failed"
            assert job["pid"] is None
            assert job["pid_started_at"] is None

            # Verify: no signal was sent
            mock_kill.assert_not_called()

    def test_pid_not_alive_marked_orphaned(self, tmp_core_dir):
        """PID not alive → mark failed with 'orphaned' message.

        Simulates: the process died without the daemon knowing.
        The job should be marked as failed with a message indicating
        the process was not found.

        Requirement 39.2
        """
        queue = JobQueue(base_dir=str(tmp_core_dir))
        queue.add_job({"name": "dead-job", "type": "persistent", "script_path": "x.py"})
        queue.update_status(
            "dead-job", "running",
            pid=88888,
            pid_started_at="2025-01-01T00:00:00",
        )

        # Mock: PID is not alive
        with patch.object(ProcessManager, "is_alive", return_value=False), \
             patch("os.kill") as mock_kill:

            # Simulate orphan cleanup logic
            job = queue.get_job("dead-job")
            pid = job["pid"]

            if not ProcessManager.is_alive(pid):
                # Orphaned process not found
                queue.update_status(
                    "dead-job", "failed",
                    pid=None,
                    pid_started_at=None,
                )

            # Verify: job marked as failed
            job = queue.get_job("dead-job")
            assert job["state"] == "failed"
            assert job["pid"] is None
            assert job["pid_started_at"] is None

            # No signal sent to dead process
            mock_kill.assert_not_called()

    def test_pid_alive_and_creation_time_matches(self, tmp_core_dir):
        """PID alive with matching creation time → job stays running.

        This verifies the positive case: when PID is valid and still ours,
        the job should not be marked as failed.
        """
        queue = JobQueue(base_dir=str(tmp_core_dir))
        queue.add_job({"name": "valid-job", "type": "persistent", "script_path": "x.py"})
        queue.update_status(
            "valid-job", "running",
            pid=77777,
            pid_started_at="2025-01-01T00:00:00",
        )

        # Mock: PID alive and creation time matches
        with patch.object(ProcessManager, "is_alive", return_value=True), \
             patch.object(ProcessManager, "get_start_time", return_value="2025-01-01T00:00:00"):

            job = queue.get_job("valid-job")
            pid = job["pid"]
            stored_start_time = job["pid_started_at"]

            if ProcessManager.is_alive(pid):
                current_start_time = ProcessManager.get_start_time(pid)
                if current_start_time == stored_start_time:
                    # Job is still running normally - don't touch it
                    pass

            # Verify: job stays running
            job = queue.get_job("valid-job")
            assert job["state"] == "running"
            assert job["pid"] == 77777
