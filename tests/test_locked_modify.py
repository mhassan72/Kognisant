"""50-thread concurrent stress test for _locked_modify serialization.

Spawns 50 threads each calling update_status on different jobs simultaneously.
Verifies: all 50 updates applied, no lost updates, valid JSON at end.

Requirements covered: 40.1, 40.2
"""

import json
import os
import threading

import pytest

from cli_kognisant.jobs import JobQueue


class TestConcurrentLockedModify:
    """Stress test: 50 threads modifying the job queue concurrently."""

    def test_50_threads_no_lost_updates(self, tmp_core_dir):
        """Spawn 50 threads each updating a different job → all 50 updates applied.

        Requirement 40.1, 40.2
        """
        queue = JobQueue(base_dir=str(tmp_core_dir))

        # Create 50 jobs
        for i in range(50):
            queue.add_job({
                "name": f"job-{i:03d}",
                "type": "persistent",
                "script_path": f"script_{i}.py",
            })

        # Verify all 50 jobs exist initially
        jobs = queue.load()
        assert len(jobs) == 50

        errors = []

        def update_job(index):
            """Update a single job's status from a separate thread."""
            try:
                queue.update_status(f"job-{index:03d}", "running", pid=1000 + index)
            except Exception as e:
                errors.append((index, str(e)))

        # Spawn 50 threads, each updating a different job
        threads = []
        for i in range(50):
            t = threading.Thread(target=update_job, args=(i,))
            threads.append(t)

        # Start all threads
        for t in threads:
            t.start()

        # Wait for all threads to complete
        for t in threads:
            t.join(timeout=30)

        # Check no errors occurred
        assert errors == [], f"Errors during concurrent updates: {errors}"

        # Verify all 50 jobs are now in "running" state
        jobs = queue.load()
        assert len(jobs) == 50

        running_count = 0
        for job in jobs:
            if job["state"] == "running":
                running_count += 1
                # Verify PID was set correctly
                idx = int(job["name"].split("-")[1])
                assert job["pid"] == 1000 + idx

        assert running_count == 50, (
            f"Expected 50 running jobs, got {running_count}. "
            f"Some updates were lost!"
        )

        # Verify the JSON file is valid
        with open(queue.queue_path, "r") as f:
            data = json.load(f)
        assert data["schema_version"] == 1
        assert len(data["jobs"]) == 50

    def test_concurrent_add_and_remove(self, tmp_core_dir):
        """Mixed add/remove operations from multiple threads → consistent state."""
        queue = JobQueue(base_dir=str(tmp_core_dir))

        # Create initial jobs
        for i in range(10):
            queue.add_job({
                "name": f"base-{i:02d}",
                "type": "agent",
                "task": f"task {i}",
            })

        errors = []
        added_names = []
        lock = threading.Lock()

        def add_job(index):
            """Add a new job from a thread."""
            try:
                name = f"added-{index:03d}"
                queue.add_job({
                    "name": name,
                    "type": "agent",
                    "task": f"Added task {index}",
                })
                with lock:
                    added_names.append(name)
            except Exception as e:
                errors.append((f"add-{index}", str(e)))

        def remove_job(index):
            """Remove an existing job from a thread."""
            try:
                queue.remove_job(f"base-{index:02d}")
            except Exception as e:
                errors.append((f"remove-{index}", str(e)))

        threads = []
        # 10 threads adding jobs
        for i in range(10):
            threads.append(threading.Thread(target=add_job, args=(i,)))
        # 5 threads removing jobs
        for i in range(5):
            threads.append(threading.Thread(target=remove_job, args=(i,)))

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert errors == [], f"Errors during concurrent ops: {errors}"

        # Final state should be: 10 - 5 (removed) + 10 (added) = 15
        jobs = queue.load()
        assert len(jobs) == 15

        # Verify JSON is valid
        with open(queue.queue_path, "r") as f:
            data = json.load(f)
        assert isinstance(data, dict)
        assert data["schema_version"] == 1
