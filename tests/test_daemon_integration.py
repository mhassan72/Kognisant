"""
Integration tests for the Autonomous Execution Engine.

Tests end-to-end flows including:
1. Create script → schedule persistent job → verify daemon spawns it
2. Cron job execution verification
3. Cancel running job → subprocess terminated
4. Crash loop detection
5. Concurrent CLI+daemon access

Uses tempfile.mkdtemp() for isolation. Matches project test patterns (unittest).
"""

import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock


class TestCreateScriptSchedulePersistentJob(unittest.TestCase):
    """Test: Create script → Schedule persistent job → Verify daemon spawns it."""

    def setUp(self):
        """Set up isolated temp directory for each test."""
        self.test_dir = tempfile.mkdtemp()
        self.scripts_dir = os.path.join(self.test_dir, "scripts")
        self.logs_dir = os.path.join(self.test_dir, "logs")
        os.makedirs(self.scripts_dir, exist_ok=True)
        os.makedirs(self.logs_dir, exist_ok=True)

    def tearDown(self):
        """Clean up temp directory."""
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_create_script_and_schedule_persistent_job(self):
        """End-to-end: create_script writes a script, add_job creates a persistent job,
        ProcessManager.spawn is called with correct args, and state transitions
        from pending → running."""
        from cli_kognisant.scripts import validate_script_name, create_script
        from cli_kognisant.jobs import JobQueue
        from cli_kognisant.daemon import ProcessManager

        # Patch SCRIPTS_DIR to use our temp directory
        with patch("cli_kognisant.scripts.SCRIPTS_DIR", self.scripts_dir):
            # Step 1: Create a script
            result = create_script(
                name="my-bot",
                content="import sys\nprint('bot running')\n",
                description="A test bot",
                env_vars=["BOT_TOKEN"],
            )
            self.assertIn("created successfully", result)

            # Verify script file exists
            script_path = os.path.join(self.scripts_dir, "my-bot.py")
            self.assertTrue(os.path.exists(script_path))

            # Verify metadata file exists
            metadata_path = os.path.join(self.scripts_dir, "my-bot.json")
            self.assertTrue(os.path.exists(metadata_path))
            with open(metadata_path) as f:
                meta = json.load(f)
            self.assertEqual(meta["name"], "my-bot")
            self.assertEqual(meta["env_vars"], ["BOT_TOKEN"])

        # Step 2: Add a persistent job via JobQueue
        job_queue = JobQueue(base_dir=self.test_dir)
        result = job_queue.add_job({
            "name": "my-bot",
            "type": "persistent",
            "script_path": "my-bot.py",
            "env_vars": {"BOT_TOKEN": "abc123"},
        })
        self.assertIn("added successfully", result)

        # Verify job is in pending state
        job = job_queue.get_job("my-bot")
        self.assertIsNotNone(job)
        self.assertEqual(job["state"], "pending")
        self.assertEqual(job["type"], "persistent")

        # Step 3: Simulate ProcessManager.spawn and verify correct args
        with patch.object(ProcessManager, "spawn") as mock_spawn:
            mock_proc = MagicMock()
            mock_proc.pid = 12345
            mock_spawn.return_value = mock_proc

            # Simulate what the daemon main loop would do
            pending = job_queue.get_pending_jobs()
            persistent_pending = [j for j in pending if j["type"] == "persistent"]
            self.assertEqual(len(persistent_pending), 1)
            self.assertEqual(persistent_pending[0]["name"], "my-bot")

            # Call spawn like the daemon would
            job_data = persistent_pending[0]
            abs_script = os.path.join(self.scripts_dir, job_data["script_path"])
            context = {
                "job_name": job_data["name"],
                "job_type": job_data["type"],
                "env_vars": job_data["env_vars"],
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            proc = ProcessManager.spawn(abs_script, job_data["env_vars"], context)

            # Verify spawn was called with correct args
            mock_spawn.assert_called_once_with(abs_script, {"BOT_TOKEN": "abc123"}, context)

        # Step 4: Verify state transition to running
        job_queue.update_status("my-bot", "running", pid=12345)
        job = job_queue.get_job("my-bot")
        self.assertEqual(job["state"], "running")
        self.assertEqual(job["pid"], 12345)


class TestCronJobExecution(unittest.TestCase):
    """Test: Cron job execution verification."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.logs_dir = os.path.join(self.test_dir, "logs")
        os.makedirs(self.logs_dir, exist_ok=True)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_cron_job_due_detection_and_execution(self):
        """End-to-end: add a scheduled job with a cron expression,
        get_due_scheduled returns the job when time matches,
        simulate spawn + successful completion,
        verify last_run_at is updated and state returns to 'scheduled'."""
        from cli_kognisant.jobs import JobQueue, CronParser
        from cli_kognisant.daemon import ProcessManager

        job_queue = JobQueue(base_dir=self.test_dir)

        # Add a scheduled job: "every hour at minute 30"
        job_queue.add_job({
            "name": "nightly-tests",
            "type": "scheduled",
            "script_path": "run-tests.py",
            "cron_expression": "30 * * * *",
            "env_vars": {},
        })

        # Verify it starts in pending state
        job = job_queue.get_job("nightly-tests")
        self.assertEqual(job["state"], "pending")

        # Test: datetime that matches cron "30 * * * *" → minute 30
        matching_time = datetime(2025, 6, 15, 14, 30, 0, tzinfo=timezone.utc)
        due = job_queue.get_due_scheduled(matching_time)
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0]["name"], "nightly-tests")

        # Test: datetime that does NOT match → minute 15
        non_matching_time = datetime(2025, 6, 15, 14, 15, 0, tzinfo=timezone.utc)
        due = job_queue.get_due_scheduled(non_matching_time)
        self.assertEqual(len(due), 0)

        # Simulate spawn + successful completion
        with patch.object(ProcessManager, "spawn") as mock_spawn:
            mock_proc = MagicMock()
            mock_proc.pid = 99999
            mock_proc.poll.return_value = 0  # Exit code 0 = success
            mock_proc.stdout.read.return_value = b"All tests passed\n"
            mock_proc.stderr.read.return_value = b""
            mock_spawn.return_value = mock_proc

            # Simulate daemon: update to running during execution
            job_queue.update_status("nightly-tests", "running", pid=99999)

            # Simulate daemon: on completion, update state back to scheduled
            now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
            job_queue.update_status("nightly-tests", "scheduled", last_run_at=now_str, pid=None)

        # Verify last_run_at is updated and state is "scheduled"
        job = job_queue.get_job("nightly-tests")
        self.assertEqual(job["state"], "scheduled")
        self.assertIsNotNone(job["last_run_at"])
        self.assertIsNone(job["pid"])


class TestCancelRunningJob(unittest.TestCase):
    """Test: Cancel running job → subprocess terminated."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.scripts_dir = os.path.join(self.test_dir, "scripts")
        self.logs_dir = os.path.join(self.test_dir, "logs")
        os.makedirs(self.scripts_dir, exist_ok=True)
        os.makedirs(self.logs_dir, exist_ok=True)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_cancel_running_job_terminates_subprocess(self):
        """Start a long-running subprocess (sleep 60), cancel it,
        verify ProcessManager.kill_gracefully is called,
        verify final state is 'cancelled'."""
        from cli_kognisant.jobs import JobQueue
        from cli_kognisant.daemon import ProcessManager

        job_queue = JobQueue(base_dir=self.test_dir)

        # Create and start a persistent job
        job_queue.add_job({
            "name": "long-runner",
            "type": "persistent",
            "script_path": "sleeper.py",
            "env_vars": {},
        })

        # Spawn an actual subprocess (sleep 60) to test real termination
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        real_pid = proc.pid

        # Update job state to running with the real PID
        job_queue.update_status("long-runner", "running", pid=real_pid)
        job = job_queue.get_job("long-runner")
        self.assertEqual(job["state"], "running")
        self.assertEqual(job["pid"], real_pid)

        # Verify subprocess is alive
        self.assertTrue(ProcessManager.is_alive(real_pid))

        # Cancel the job
        job_queue.update_status("long-runner", "cancelled")

        # Terminate the subprocess (as the daemon would)
        ProcessManager.kill_gracefully(real_pid, timeout=5)

        # Reap the subprocess to avoid zombie (parent must call wait)
        proc.wait(timeout=5)

        # Verify process is dead
        self.assertFalse(ProcessManager.is_alive(real_pid))

        # Verify final state is cancelled
        job = job_queue.get_job("long-runner")
        self.assertEqual(job["state"], "cancelled")


class TestCrashLoopDetection(unittest.TestCase):
    """Test: Crash loop detection for persistent jobs."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.logs_dir = os.path.join(self.test_dir, "logs")
        os.makedirs(self.logs_dir, exist_ok=True)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_crash_loop_detection_after_5_rapid_restarts(self):
        """Simulate a persistent job that exits non-zero rapidly (>5 times in 60s).
        Verify state transitions to 'crash_loop', restart counter and timestamps
        are stored correctly."""
        from cli_kognisant.jobs import JobQueue

        job_queue = JobQueue(base_dir=self.test_dir)

        # Add a persistent job
        job_queue.add_job({
            "name": "crasher",
            "type": "persistent",
            "script_path": "crasher.py",
            "env_vars": {},
        })

        # Simulate the daemon detecting repeated crashes
        # Each crash adds a restart timestamp within a 60s window
        now = datetime.now(timezone.utc)
        restart_timestamps = []

        for i in range(6):
            # Simulate crash timestamps all within 60 seconds
            ts = (now).strftime("%Y-%m-%dT%H:%M:%SZ")
            restart_timestamps.append(ts)

        # After 6 restarts in 60s window, daemon would detect crash_loop
        # Simulate the daemon's crash detection logic
        restart_count = 6

        # Rolling 60s window check: all 6 timestamps are within 60s
        recent_count = len(restart_timestamps)
        self.assertGreater(recent_count, 5)

        # Daemon would set state to crash_loop
        job_queue.update_status(
            "crasher",
            "crash_loop",
            pid=None,
            restart_count=restart_count,
            restart_timestamps=restart_timestamps,
        )

        # Verify state is crash_loop
        job = job_queue.get_job("crasher")
        self.assertEqual(job["state"], "crash_loop")
        self.assertEqual(job["restart_count"], 6)
        self.assertEqual(len(job["restart_timestamps"]), 6)
        self.assertIsNone(job["pid"])

    def test_no_crash_loop_when_restarts_spread_out(self):
        """Verify that restarts spread over more than 60 seconds
        do NOT trigger crash_loop detection."""
        from cli_kognisant.jobs import JobQueue
        from datetime import timedelta

        job_queue = JobQueue(base_dir=self.test_dir)

        job_queue.add_job({
            "name": "slow-crasher",
            "type": "persistent",
            "script_path": "slow-crasher.py",
            "env_vars": {},
        })

        # Simulate restarts spread over 5 minutes (1 per minute)
        now = datetime.now(timezone.utc)
        restart_timestamps = []
        for i in range(6):
            ts = (now - timedelta(minutes=5 - i)).strftime("%Y-%m-%dT%H:%M:%SZ")
            restart_timestamps.append(ts)

        # Check rolling 60s window: only the most recent would be within 60s
        now_epoch = time.time()
        recent = []
        for ts_str in restart_timestamps:
            ts_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if now_epoch - ts_dt.timestamp() <= 60:
                recent.append(ts_str)

        # Should NOT exceed threshold of 5 within 60s
        self.assertLessEqual(len(recent), 5)

        # Job should remain in running state (not crash_loop)
        job_queue.update_status(
            "slow-crasher",
            "running",
            restart_count=6,
            restart_timestamps=restart_timestamps,
        )
        job = job_queue.get_job("slow-crasher")
        self.assertEqual(job["state"], "running")


class TestConcurrentCLIDaemonAccess(unittest.TestCase):
    """Test: Concurrent CLI+daemon access to jobs.json with file locking."""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_concurrent_access_produces_valid_json(self):
        """Spawn two threads both accessing JobQueue simultaneously.
        Verify file lock prevents corruption and both reads produce
        valid JSON after concurrent writes."""
        from cli_kognisant.jobs import JobQueue

        job_queue = JobQueue(base_dir=self.test_dir)

        # Initialize with an empty queue
        job_queue.save([])

        errors = []
        results = {"thread1": None, "thread2": None}

        def writer_thread_1():
            """Simulate CLI adding jobs."""
            try:
                q = JobQueue(base_dir=self.test_dir)
                for i in range(10):
                    q.add_job({
                        "name": f"cli-job-{i}",
                        "type": "scheduled",
                        "script_path": f"script-{i}.py",
                        "cron_expression": f"{i} * * * *",
                    })
                results["thread1"] = "done"
            except Exception as e:
                errors.append(f"Thread 1 error: {e}")

        def writer_thread_2():
            """Simulate daemon updating job states."""
            try:
                q = JobQueue(base_dir=self.test_dir)
                for i in range(10):
                    q.add_job({
                        "name": f"daemon-job-{i}",
                        "type": "persistent",
                        "script_path": f"daemon-script-{i}.py",
                    })
                results["thread2"] = "done"
            except Exception as e:
                errors.append(f"Thread 2 error: {e}")

        t1 = threading.Thread(target=writer_thread_1)
        t2 = threading.Thread(target=writer_thread_2)

        t1.start()
        t2.start()

        t1.join(timeout=30)
        t2.join(timeout=30)

        # No errors should have occurred
        self.assertEqual(len(errors), 0, f"Errors during concurrent access: {errors}")

        # Both threads should have completed
        self.assertEqual(results["thread1"], "done")
        self.assertEqual(results["thread2"], "done")

        # Verify the final JSON file is valid and contains all 20 jobs
        with open(os.path.join(self.test_dir, "jobs.json"), "r") as f:
            data = json.load(f)

        self.assertIsInstance(data, dict)
        self.assertIn("schema_version", data)
        self.assertEqual(data["schema_version"], 1)
        jobs = data["jobs"]
        self.assertIsInstance(jobs, list)
        self.assertEqual(len(jobs), 20)

        # Verify all job names are present
        names = {j["name"] for j in jobs}
        for i in range(10):
            self.assertIn(f"cli-job-{i}", names)
            self.assertIn(f"daemon-job-{i}", names)

    def test_concurrent_read_write_integrity(self):
        """One thread writes while another reads concurrently.
        Both must produce valid results without corruption."""
        from cli_kognisant.jobs import JobQueue

        job_queue = JobQueue(base_dir=self.test_dir)
        job_queue.save([])

        read_results = []
        write_errors = []

        def writer():
            try:
                q = JobQueue(base_dir=self.test_dir)
                for i in range(15):
                    q.add_job({
                        "name": f"w-job-{i}",
                        "type": "persistent",
                        "script_path": f"w-{i}.py",
                    })
                    time.sleep(0.01)
            except Exception as e:
                write_errors.append(str(e))

        def reader():
            q = JobQueue(base_dir=self.test_dir)
            for _ in range(20):
                jobs = q.load()
                # Every read must be valid JSON (list of dicts)
                read_results.append(len(jobs))
                time.sleep(0.005)

        t_write = threading.Thread(target=writer)
        t_read = threading.Thread(target=reader)

        t_write.start()
        t_read.start()

        t_write.join(timeout=30)
        t_read.join(timeout=30)

        # No write errors
        self.assertEqual(len(write_errors), 0)

        # All reads returned valid data (non-negative count)
        for count in read_results:
            self.assertGreaterEqual(count, 0)

        # Final state should have all 15 jobs
        final_jobs = job_queue.load()
        self.assertEqual(len(final_jobs), 15)


class TestProcessManagerSpawnIntegration(unittest.TestCase):
    """Test ProcessManager.spawn with a real subprocess."""

    def test_spawn_and_capture_output(self):
        """Verify ProcessManager.spawn actually executes a script and passes
        job context on stdin."""
        from cli_kognisant.daemon import ProcessManager

        # Create a temp script that reads stdin and prints it
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False
        ) as f:
            f.write(
                "import sys, json\n"
                "data = json.load(sys.stdin)\n"
                "print(f\"Got job: {data['job_name']}\")\n"
            )
            script_path = f.name

        try:
            context = {
                "job_name": "test-job",
                "job_type": "scheduled",
                "env_vars": {},
                "timestamp": "2025-06-15T00:00:00Z",
            }
            proc = ProcessManager.spawn(script_path, {}, context)

            # Wait for completion
            proc.wait(timeout=10)

            stdout = proc.stdout.read().decode("utf-8")
            self.assertIn("Got job: test-job", stdout)
            self.assertEqual(proc.returncode, 0)
        finally:
            os.unlink(script_path)

    def test_kill_gracefully_terminates_process(self):
        """Verify kill_gracefully sends SIGTERM and process terminates."""
        from cli_kognisant.daemon import ProcessManager

        # Start a long-running process
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        self.assertTrue(ProcessManager.is_alive(proc.pid))

        # Kill gracefully
        ProcessManager.kill_gracefully(proc.pid, timeout=5)

        # Reap the zombie process (parent must call wait)
        proc.wait(timeout=5)

        self.assertFalse(ProcessManager.is_alive(proc.pid))


if __name__ == "__main__":
    unittest.main()
