"""Unit tests for FileLock and CronParser classes in cli_kognisant/jobs.py."""

import fcntl
import os
import tempfile
import threading
import time
import unittest
from datetime import datetime

from cli_kognisant.jobs import CronParser, FileLock


class TestFileLock(unittest.TestCase):
    """Tests for FileLock class — R2-AC2,10 | R11-AC1,2,4."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.lock_path = os.path.join(self.tmpdir, "test.lock")

    def tearDown(self):
        # Clean up temp files
        if os.path.exists(self.lock_path):
            os.unlink(self.lock_path)
        os.rmdir(self.tmpdir)

    def test_acquire_and_release(self):
        """Lock can be acquired and released successfully."""
        lock = FileLock(self.lock_path)
        self.assertTrue(lock.acquire())
        lock.release()
        # Verify the lock file was created
        self.assertTrue(os.path.exists(self.lock_path))

    def test_context_manager_acquires_and_releases(self):
        """Context manager acquires on entry and releases on exit."""
        with FileLock(self.lock_path) as lock:
            # Lock should be held — verify fd is set
            self.assertIsNotNone(lock._fd)
        # After exit, fd should be None
        self.assertIsNone(lock._fd)

    def test_context_manager_releases_on_exception(self):
        """Context manager releases lock even when exception occurs."""
        lock = FileLock(self.lock_path)
        try:
            with lock:
                raise RuntimeError("test error")
        except RuntimeError:
            pass
        self.assertIsNone(lock._fd)

    def test_timeout_when_lock_held_by_another(self):
        """Lock acquisition times out when another process holds it."""
        # Acquire the lock in the current thread
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX)

        try:
            # Try to acquire from another FileLock instance with short timeout
            lock = FileLock(self.lock_path, timeout=0.2)
            # This should fail because we hold the lock and won't release it
            # within the timeout + retry window
            result = lock.acquire()
            self.assertFalse(result)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def test_lock_reacquire_after_release(self):
        """Lock can be acquired again after being released."""
        lock = FileLock(self.lock_path)
        self.assertTrue(lock.acquire())
        lock.release()
        self.assertTrue(lock.acquire())
        lock.release()

    def test_context_manager_timeout_raises(self):
        """Context manager raises TimeoutError when lock cannot be acquired."""
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX)

        try:
            with self.assertRaises(TimeoutError):
                with FileLock(self.lock_path, timeout=0.2):
                    pass  # Should not reach here
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def test_concurrent_lock_serializes_access(self):
        """Two threads accessing a shared resource via FileLock are serialized."""
        results = []

        def worker(worker_id):
            with FileLock(self.lock_path, timeout=5.0):
                results.append(f"start-{worker_id}")
                time.sleep(0.1)
                results.append(f"end-{worker_id}")

        t1 = threading.Thread(target=worker, args=(1,))
        t2 = threading.Thread(target=worker, args=(2,))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Verify no interleaving: starts and ends should be paired
        self.assertEqual(len(results), 4)
        # Either [start-1, end-1, start-2, end-2] or [start-2, end-2, start-1, end-1]
        if results[0] == "start-1":
            self.assertEqual(results[1], "end-1")
            self.assertEqual(results[2], "start-2")
            self.assertEqual(results[3], "end-2")
        else:
            self.assertEqual(results[0], "start-2")
            self.assertEqual(results[1], "end-2")
            self.assertEqual(results[2], "start-1")
            self.assertEqual(results[3], "end-1")


class TestCronParserValidate(unittest.TestCase):
    """Tests for CronParser.validate() — R3-AC5."""

    def test_valid_all_wildcards(self):
        self.assertTrue(CronParser.validate("* * * * *"))

    def test_valid_specific_values(self):
        self.assertTrue(CronParser.validate("0 12 1 6 3"))

    def test_valid_step_values(self):
        self.assertTrue(CronParser.validate("*/15 * * * *"))
        self.assertTrue(CronParser.validate("* */2 * * *"))

    def test_valid_ranges(self):
        self.assertTrue(CronParser.validate("0-30 * * * *"))
        self.assertTrue(CronParser.validate("* 9-17 * * *"))

    def test_valid_lists(self):
        self.assertTrue(CronParser.validate("0,15,30,45 * * * *"))
        self.assertTrue(CronParser.validate("* * * 1,6,12 *"))

    def test_valid_range_with_step(self):
        self.assertTrue(CronParser.validate("1-30/5 * * * *"))

    def test_valid_combined(self):
        self.assertTrue(CronParser.validate("*/5 9-17 * 1,6 0-4"))

    def test_invalid_too_few_fields(self):
        self.assertFalse(CronParser.validate("* * * *"))

    def test_invalid_too_many_fields(self):
        self.assertFalse(CronParser.validate("* * * * * *"))

    def test_invalid_out_of_range_minute(self):
        self.assertFalse(CronParser.validate("60 * * * *"))

    def test_invalid_out_of_range_hour(self):
        self.assertFalse(CronParser.validate("* 24 * * *"))

    def test_invalid_out_of_range_day(self):
        self.assertFalse(CronParser.validate("* * 32 * *"))

    def test_invalid_out_of_range_month(self):
        self.assertFalse(CronParser.validate("* * * 13 *"))

    def test_invalid_out_of_range_dow(self):
        self.assertFalse(CronParser.validate("* * * * 7"))

    def test_invalid_non_numeric(self):
        self.assertFalse(CronParser.validate("abc * * * *"))

    def test_invalid_empty_string(self):
        self.assertFalse(CronParser.validate(""))

    def test_invalid_negative_step(self):
        self.assertFalse(CronParser.validate("*/0 * * * *"))

    def test_invalid_reversed_range(self):
        self.assertFalse(CronParser.validate("30-10 * * * *"))


class TestCronParserMatches(unittest.TestCase):
    """Tests for CronParser.matches() — R3-AC5."""

    def test_all_wildcards_matches_any_time(self):
        dt = datetime(2025, 6, 15, 14, 30)
        self.assertTrue(CronParser.matches("* * * * *", dt))

    def test_specific_minute(self):
        dt = datetime(2025, 6, 15, 14, 30)
        self.assertTrue(CronParser.matches("30 * * * *", dt))
        self.assertFalse(CronParser.matches("31 * * * *", dt))

    def test_specific_hour(self):
        dt = datetime(2025, 6, 15, 14, 30)
        self.assertTrue(CronParser.matches("* 14 * * *", dt))
        self.assertFalse(CronParser.matches("* 15 * * *", dt))

    def test_specific_day_of_month(self):
        dt = datetime(2025, 6, 15, 14, 30)
        self.assertTrue(CronParser.matches("* * 15 * *", dt))
        self.assertFalse(CronParser.matches("* * 16 * *", dt))

    def test_specific_month(self):
        dt = datetime(2025, 6, 15, 14, 30)
        self.assertTrue(CronParser.matches("* * * 6 *", dt))
        self.assertFalse(CronParser.matches("* * * 7 *", dt))

    def test_day_of_week_sunday(self):
        # 2025-06-15 is a Sunday
        dt = datetime(2025, 6, 15, 14, 30)
        self.assertTrue(CronParser.matches("* * * * 0", dt))  # 0 = Sunday
        self.assertFalse(CronParser.matches("* * * * 1", dt))

    def test_day_of_week_monday(self):
        # 2025-06-16 is a Monday
        dt = datetime(2025, 6, 16, 14, 30)
        self.assertTrue(CronParser.matches("* * * * 1", dt))  # 1 = Monday

    def test_step_values(self):
        # */15 should match minutes 0, 15, 30, 45
        self.assertTrue(CronParser.matches("*/15 * * * *", datetime(2025, 1, 1, 0, 0)))
        self.assertTrue(CronParser.matches("*/15 * * * *", datetime(2025, 1, 1, 0, 15)))
        self.assertTrue(CronParser.matches("*/15 * * * *", datetime(2025, 1, 1, 0, 30)))
        self.assertTrue(CronParser.matches("*/15 * * * *", datetime(2025, 1, 1, 0, 45)))
        self.assertFalse(CronParser.matches("*/15 * * * *", datetime(2025, 1, 1, 0, 7)))

    def test_range(self):
        # 9-17 should match hours 9 through 17
        self.assertTrue(CronParser.matches("* 9-17 * * *", datetime(2025, 1, 1, 9, 0)))
        self.assertTrue(CronParser.matches("* 9-17 * * *", datetime(2025, 1, 1, 17, 0)))
        self.assertFalse(CronParser.matches("* 9-17 * * *", datetime(2025, 1, 1, 8, 0)))
        self.assertFalse(CronParser.matches("* 9-17 * * *", datetime(2025, 1, 1, 18, 0)))

    def test_list(self):
        # 1,15 should match day 1 and day 15
        self.assertTrue(CronParser.matches("* * 1,15 * *", datetime(2025, 1, 1, 0, 0)))
        self.assertTrue(CronParser.matches("* * 1,15 * *", datetime(2025, 1, 15, 0, 0)))
        self.assertFalse(CronParser.matches("* * 1,15 * *", datetime(2025, 1, 10, 0, 0)))

    def test_complex_expression(self):
        # "0 2 * * *" = at 02:00 every day
        self.assertTrue(CronParser.matches("0 2 * * *", datetime(2025, 3, 10, 2, 0)))
        self.assertFalse(CronParser.matches("0 2 * * *", datetime(2025, 3, 10, 2, 1)))
        self.assertFalse(CronParser.matches("0 2 * * *", datetime(2025, 3, 10, 3, 0)))

    def test_range_with_step(self):
        # 1-30/10 should match minutes 1, 11, 21
        self.assertTrue(CronParser.matches("1-30/10 * * * *", datetime(2025, 1, 1, 0, 1)))
        self.assertTrue(CronParser.matches("1-30/10 * * * *", datetime(2025, 1, 1, 0, 11)))
        self.assertTrue(CronParser.matches("1-30/10 * * * *", datetime(2025, 1, 1, 0, 21)))
        self.assertFalse(CronParser.matches("1-30/10 * * * *", datetime(2025, 1, 1, 0, 31)))

    def test_invalid_expression_raises(self):
        with self.assertRaises(ValueError):
            CronParser.matches("invalid", datetime(2025, 1, 1, 0, 0))


class TestCronParserNextRun(unittest.TestCase):
    """Tests for CronParser.next_run() — R3-AC5."""

    def test_next_minute(self):
        # "* * * * *" — every minute, next run should be the next minute
        after = datetime(2025, 6, 15, 14, 30, 45)
        result = CronParser.next_run("* * * * *", after)
        self.assertEqual(result, datetime(2025, 6, 15, 14, 31, 0))

    def test_specific_time_today(self):
        # "0 15 * * *" — at 15:00, asking after 14:30
        after = datetime(2025, 6, 15, 14, 30)
        result = CronParser.next_run("0 15 * * *", after)
        self.assertEqual(result, datetime(2025, 6, 15, 15, 0, 0))

    def test_specific_time_next_day(self):
        # "0 2 * * *" — at 02:00, asking after 03:00
        after = datetime(2025, 6, 15, 3, 0)
        result = CronParser.next_run("0 2 * * *", after)
        self.assertEqual(result, datetime(2025, 6, 16, 2, 0, 0))

    def test_every_15_minutes(self):
        # "*/15 * * * *" — every 15 min, asking after 14:07
        after = datetime(2025, 6, 15, 14, 7)
        result = CronParser.next_run("*/15 * * * *", after)
        self.assertEqual(result, datetime(2025, 6, 15, 14, 15, 0))

    def test_specific_day_of_month(self):
        # "0 0 1 * *" — midnight on the 1st of each month
        after = datetime(2025, 6, 15, 0, 0)
        result = CronParser.next_run("0 0 1 * *", after)
        self.assertEqual(result, datetime(2025, 7, 1, 0, 0, 0))

    def test_invalid_expression_raises(self):
        with self.assertRaises(ValueError):
            CronParser.next_run("bad expr", datetime(2025, 1, 1))

    def test_next_run_skips_current_minute(self):
        # Even if current time matches, next_run returns the *next* match
        after = datetime(2025, 6, 15, 14, 0, 0)  # exactly 14:00
        result = CronParser.next_run("0 14 * * *", after)
        # Should return tomorrow at 14:00, not today
        self.assertEqual(result, datetime(2025, 6, 16, 14, 0, 0))


# --------------------------------------------------------------------------
# Tests for JobQueue class — Task 2
# --------------------------------------------------------------------------

import json
import shutil

from cli_kognisant.jobs import JobQueue, JOB_NAME_PATTERN


class TestJobQueue(unittest.TestCase):
    """Tests for JobQueue CRUD operations — R2, R7, R11."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.queue = JobQueue(base_dir=self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # --- add_job tests ---

    def test_add_job_creates_entry(self):
        """add_job creates a job with correct default fields."""
        result = self.queue.add_job({
            "name": "my-job",
            "type": "persistent",
            "script_path": "my_script.py",
        })
        self.assertIn("added successfully", result)

        job = self.queue.get_job("my-job")
        self.assertIsNotNone(job)
        self.assertEqual(job["name"], "my-job")
        self.assertEqual(job["type"], "persistent")
        self.assertEqual(job["state"], "pending")
        self.assertEqual(job["script_path"], "my_script.py")
        self.assertIsNone(job["task"])
        self.assertIsNone(job["cron_expression"])
        self.assertEqual(job["env_vars"], {})
        self.assertIsNotNone(job["created_at"])
        self.assertIsNone(job["last_run_at"])
        self.assertIsNone(job["pid"])
        self.assertEqual(job["restart_count"], 0)
        self.assertEqual(job["restart_timestamps"], [])

    def test_add_job_scheduled_with_cron(self):
        """add_job stores cron_expression for scheduled jobs."""
        self.queue.add_job({
            "name": "cron-job",
            "type": "scheduled",
            "script_path": "cron_script.py",
            "cron_expression": "*/15 * * * *",
        })
        job = self.queue.get_job("cron-job")
        self.assertEqual(job["cron_expression"], "*/15 * * * *")

    def test_add_job_with_env_vars(self):
        """add_job stores environment variables."""
        self.queue.add_job({
            "name": "env-job",
            "type": "persistent",
            "script_path": "bot.py",
            "env_vars": {"API_KEY": "abc123", "DEBUG": "true"},
        })
        job = self.queue.get_job("env-job")
        self.assertEqual(job["env_vars"], {"API_KEY": "abc123", "DEBUG": "true"})

    def test_add_job_agent_with_task(self):
        """add_job stores task description for agent jobs."""
        self.queue.add_job({
            "name": "agent-task",
            "type": "agent",
            "task": "Refactor the utils module",
        })
        job = self.queue.get_job("agent-task")
        self.assertEqual(job["task"], "Refactor the utils module")
        self.assertEqual(job["type"], "agent")

    # --- Name validation tests ---

    def test_add_job_invalid_name_empty(self):
        """add_job rejects empty name."""
        with self.assertRaises(ValueError) as ctx:
            self.queue.add_job({"name": "", "type": "persistent"})
        self.assertIn("Invalid job name", str(ctx.exception))

    def test_add_job_invalid_name_uppercase(self):
        """add_job rejects uppercase characters."""
        with self.assertRaises(ValueError):
            self.queue.add_job({"name": "MyJob", "type": "persistent"})

    def test_add_job_invalid_name_spaces(self):
        """add_job rejects spaces."""
        with self.assertRaises(ValueError):
            self.queue.add_job({"name": "my job", "type": "persistent"})

    def test_add_job_invalid_name_special_chars(self):
        """add_job rejects special characters."""
        with self.assertRaises(ValueError):
            self.queue.add_job({"name": "job@name", "type": "persistent"})

    def test_add_job_invalid_name_too_long(self):
        """add_job rejects names longer than 64 chars."""
        with self.assertRaises(ValueError):
            self.queue.add_job({"name": "a" * 65, "type": "persistent"})

    def test_add_job_valid_name_max_length(self):
        """add_job accepts names at exactly 64 chars."""
        name = "a" * 64
        self.queue.add_job({"name": name, "type": "persistent"})
        job = self.queue.get_job(name)
        self.assertIsNotNone(job)

    def test_add_job_valid_name_with_hyphens_underscores(self):
        """add_job accepts names with hyphens and underscores."""
        self.queue.add_job({"name": "my-job_123", "type": "persistent"})
        job = self.queue.get_job("my-job_123")
        self.assertIsNotNone(job)

    # --- Duplicate detection tests ---

    def test_add_job_duplicate_name_raises(self):
        """add_job raises ValueError for duplicate name."""
        self.queue.add_job({"name": "dup-job", "type": "persistent"})
        with self.assertRaises(ValueError) as ctx:
            self.queue.add_job({"name": "dup-job", "type": "scheduled", "cron_expression": "* * * * *"})
        self.assertIn("already exists", str(ctx.exception))

    # --- Invalid type tests ---

    def test_add_job_invalid_type_raises(self):
        """add_job raises ValueError for invalid job type."""
        with self.assertRaises(ValueError) as ctx:
            self.queue.add_job({"name": "bad-type", "type": "invalid"})
        self.assertIn("Invalid job type", str(ctx.exception))

    # --- remove_job tests ---

    def test_remove_job_existing(self):
        """remove_job returns True and removes the job."""
        self.queue.add_job({"name": "to-remove", "type": "persistent"})
        result = self.queue.remove_job("to-remove")
        self.assertTrue(result)
        self.assertIsNone(self.queue.get_job("to-remove"))

    def test_remove_job_nonexistent(self):
        """remove_job returns False for non-existent job."""
        result = self.queue.remove_job("ghost-job")
        self.assertFalse(result)

    # --- update_status tests ---

    def test_update_status_changes_state(self):
        """update_status changes the job state."""
        self.queue.add_job({"name": "status-job", "type": "persistent"})
        result = self.queue.update_status("status-job", "running")
        self.assertTrue(result)
        job = self.queue.get_job("status-job")
        self.assertEqual(job["state"], "running")

    def test_update_status_with_kwargs(self):
        """update_status sets additional fields from kwargs."""
        self.queue.add_job({"name": "running-job", "type": "persistent"})
        self.queue.update_status(
            "running-job", "running",
            pid=12345, last_run_at="2025-06-15T02:00:00",
        )
        job = self.queue.get_job("running-job")
        self.assertEqual(job["state"], "running")
        self.assertEqual(job["pid"], 12345)
        self.assertEqual(job["last_run_at"], "2025-06-15T02:00:00")

    def test_update_status_nonexistent_returns_false(self):
        """update_status returns False for non-existent job."""
        result = self.queue.update_status("ghost", "running")
        self.assertFalse(result)

    def test_update_status_restart_count(self):
        """update_status can increment restart_count."""
        self.queue.add_job({"name": "restart-job", "type": "persistent"})
        self.queue.update_status("restart-job", "running", restart_count=3)
        job = self.queue.get_job("restart-job")
        self.assertEqual(job["restart_count"], 3)

    # --- get_job tests ---

    def test_get_job_existing(self):
        """get_job returns the job dict for existing job."""
        self.queue.add_job({"name": "find-me", "type": "agent", "task": "do stuff"})
        job = self.queue.get_job("find-me")
        self.assertIsNotNone(job)
        self.assertEqual(job["name"], "find-me")

    def test_get_job_nonexistent(self):
        """get_job returns None for non-existent job."""
        self.assertIsNone(self.queue.get_job("nope"))

    # --- get_pending_jobs tests ---

    def test_get_pending_jobs(self):
        """get_pending_jobs returns only pending jobs."""
        self.queue.add_job({"name": "pending1", "type": "persistent"})
        self.queue.add_job({"name": "pending2", "type": "agent", "task": "x"})
        self.queue.update_status("pending2", "running")

        pending = self.queue.get_pending_jobs()
        names = [j["name"] for j in pending]
        self.assertIn("pending1", names)
        self.assertNotIn("pending2", names)

    # --- get_due_scheduled tests ---

    def test_get_due_scheduled_matching(self):
        """get_due_scheduled returns jobs whose cron matches now."""
        self.queue.add_job({
            "name": "every-min",
            "type": "scheduled",
            "script_path": "script.py",
            "cron_expression": "* * * * *",  # matches every minute
        })
        now = datetime(2025, 6, 15, 14, 30)
        due = self.queue.get_due_scheduled(now)
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0]["name"], "every-min")

    def test_get_due_scheduled_not_matching(self):
        """get_due_scheduled skips jobs whose cron doesn't match."""
        self.queue.add_job({
            "name": "hourly",
            "type": "scheduled",
            "script_path": "script.py",
            "cron_expression": "0 * * * *",  # only at minute 0
        })
        now = datetime(2025, 6, 15, 14, 30)  # minute 30
        due = self.queue.get_due_scheduled(now)
        self.assertEqual(len(due), 0)

    def test_get_due_scheduled_ignores_non_scheduled_types(self):
        """get_due_scheduled only considers scheduled type jobs."""
        self.queue.add_job({
            "name": "persistent-job",
            "type": "persistent",
            "script_path": "bot.py",
        })
        now = datetime(2025, 6, 15, 14, 30)
        due = self.queue.get_due_scheduled(now)
        self.assertEqual(len(due), 0)

    def test_get_due_scheduled_ignores_running_jobs(self):
        """get_due_scheduled skips scheduled jobs that are already running."""
        self.queue.add_job({
            "name": "running-sched",
            "type": "scheduled",
            "script_path": "script.py",
            "cron_expression": "* * * * *",
        })
        self.queue.update_status("running-sched", "running")
        now = datetime(2025, 6, 15, 14, 30)
        due = self.queue.get_due_scheduled(now)
        self.assertEqual(len(due), 0)

    # --- get_persistent_jobs tests ---

    def test_get_persistent_jobs(self):
        """get_persistent_jobs returns only persistent type jobs."""
        self.queue.add_job({"name": "bot1", "type": "persistent", "script_path": "b.py"})
        self.queue.add_job({"name": "cron1", "type": "scheduled", "script_path": "c.py", "cron_expression": "* * * * *"})
        self.queue.add_job({"name": "bot2", "type": "persistent", "script_path": "b2.py"})

        persistent = self.queue.get_persistent_jobs()
        names = [j["name"] for j in persistent]
        self.assertIn("bot1", names)
        self.assertIn("bot2", names)
        self.assertNotIn("cron1", names)

    # --- Atomic save tests ---

    def test_save_is_atomic_file_exists(self):
        """save() writes a valid JSON file that can be loaded back."""
        jobs = [
            {"name": "job1", "type": "persistent", "state": "pending"},
            {"name": "job2", "type": "scheduled", "state": "running"},
        ]
        self.queue.save(jobs)

        # Verify the file content directly
        with open(self.queue.queue_path, "r") as f:
            loaded = json.load(f)
        self.assertEqual(len(loaded), 2)
        self.assertEqual(loaded[0]["name"], "job1")

    def test_load_missing_file_returns_empty(self):
        """load() returns empty list when jobs.json doesn't exist."""
        jobs = self.queue.load()
        self.assertEqual(jobs, [])

    def test_load_malformed_json_returns_empty(self):
        """load() returns empty list for malformed JSON and logs error."""
        os.makedirs(self.tmpdir, exist_ok=True)
        with open(self.queue.queue_path, "w") as f:
            f.write("{not valid json!!!")

        jobs = self.queue.load()
        self.assertEqual(jobs, [])

    def test_load_non_array_json_returns_empty(self):
        """load() returns empty list if file contains non-array JSON."""
        os.makedirs(self.tmpdir, exist_ok=True)
        with open(self.queue.queue_path, "w") as f:
            json.dump({"not": "an array"}, f)

        jobs = self.queue.load()
        self.assertEqual(jobs, [])

    # --- read_job_logs tests ---

    def test_read_job_logs_existing_file(self):
        """read_job_logs returns last N lines of the log file."""
        os.makedirs(self.queue.logs_dir, exist_ok=True)
        log_path = self.queue.get_job_log_path("my-job")
        with open(log_path, "w") as f:
            for i in range(100):
                f.write(f"Line {i}\n")

        result = self.queue.read_job_logs("my-job", lines=5)
        lines = result.strip().split("\n")
        self.assertEqual(len(lines), 5)
        self.assertEqual(lines[0], "Line 95")
        self.assertEqual(lines[4], "Line 99")

    def test_read_job_logs_missing_file(self):
        """read_job_logs returns error message for missing log file."""
        result = self.queue.read_job_logs("no-such-job")
        self.assertIn("No logs available", result)

    def test_read_job_logs_fewer_lines_than_requested(self):
        """read_job_logs returns all lines when file has fewer than N lines."""
        os.makedirs(self.queue.logs_dir, exist_ok=True)
        log_path = self.queue.get_job_log_path("short-log")
        with open(log_path, "w") as f:
            f.write("Line 1\nLine 2\nLine 3\n")

        result = self.queue.read_job_logs("short-log", lines=50)
        lines = result.strip().split("\n")
        self.assertEqual(len(lines), 3)

    # --- get_job_log_path tests ---

    def test_get_job_log_path(self):
        """get_job_log_path returns correct path."""
        path = self.queue.get_job_log_path("my-bot")
        expected = os.path.join(self.tmpdir, "logs", "my-bot.log")
        self.assertEqual(path, expected)


class TestJobNamePattern(unittest.TestCase):
    """Tests for JOB_NAME_PATTERN regex — R7-AC10."""

    def test_valid_names(self):
        valid = ["a", "my-job", "job_1", "a" * 64, "test-123", "abc_def-ghi"]
        for name in valid:
            self.assertIsNotNone(
                JOB_NAME_PATTERN.match(name),
                f"Expected '{name}' to be valid"
            )

    def test_invalid_names(self):
        invalid = [
            "", "A", "MyJob", "job name", "job@name",
            "a" * 65, "../etc", "job/path", "job.name",
        ]
        for name in invalid:
            self.assertIsNone(
                JOB_NAME_PATTERN.match(name),
                f"Expected '{name}' to be invalid"
            )


if __name__ == "__main__":
    unittest.main()
