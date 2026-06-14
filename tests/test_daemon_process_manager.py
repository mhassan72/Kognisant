"""Unit tests for ProcessManager and daemon helper functions."""

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from cli_kognisant.daemon import (
    ProcessManager,
    _append_to_log,
    _build_job_context,
    _resolve_script_path,
    _rotate_log_if_needed,
    CORE_DIR,
)


class TestProcessManagerSpawn(unittest.TestCase):
    """Tests for ProcessManager.spawn()."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        # Create a simple test script that reads stdin and prints it
        self.script_path = os.path.join(self.tmpdir, "test_script.py")
        with open(self.script_path, "w") as f:
            f.write(
                "import sys, json\n"
                "data = json.load(sys.stdin)\n"
                "print(json.dumps(data))\n"
            )

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_spawn_returns_popen(self):
        """spawn() returns a subprocess.Popen object."""
        context = {"job_name": "test", "job_type": "scheduled",
                   "env_vars": {}, "timestamp": "2025-01-01T00:00:00Z"}
        proc = ProcessManager.spawn(self.script_path, {}, context)
        self.assertIsInstance(proc, subprocess.Popen)
        proc.wait(timeout=5)

    def test_spawn_passes_json_on_stdin(self):
        """spawn() passes job_context as JSON on stdin (R10-AC1)."""
        context = {"job_name": "test-job", "job_type": "persistent",
                   "env_vars": {"KEY": "VAL"}, "timestamp": "2025-01-01T00:00:00Z"}
        proc = ProcessManager.spawn(self.script_path, {}, context)
        proc.wait(timeout=5)
        stdout = proc.stdout.read().decode("utf-8")
        result = json.loads(stdout)
        self.assertEqual(result["job_name"], "test-job")
        self.assertEqual(result["env_vars"]["KEY"], "VAL")

    def test_spawn_uses_sys_executable(self):
        """spawn() uses sys.executable to run the script (R10-AC5)."""
        # Create a script that prints which python is running it
        script = os.path.join(self.tmpdir, "which_python.py")
        with open(script, "w") as f:
            f.write("import sys, json\njson.load(sys.stdin)\nprint(sys.executable)\n")

        context = {"job_name": "t", "job_type": "scheduled",
                   "env_vars": {}, "timestamp": "now"}
        proc = ProcessManager.spawn(script, {}, context)
        proc.wait(timeout=5)
        stdout = proc.stdout.read().decode("utf-8")
        # The script should be run by the same python
        self.assertEqual(stdout.strip(), sys.executable)

    def test_spawn_sets_env_vars(self):
        """spawn() sets environment variables from env dict (R10-AC2)."""
        script = os.path.join(self.tmpdir, "check_env.py")
        with open(script, "w") as f:
            f.write(
                "import sys, os, json\n"
                "json.load(sys.stdin)\n"
                "print(os.environ.get('TEST_VAR_123', 'NOT_SET'))\n"
            )

        context = {"job_name": "t", "job_type": "scheduled",
                   "env_vars": {}, "timestamp": "now"}
        proc = ProcessManager.spawn(
            script, {"TEST_VAR_123": "hello_world"}, context
        )
        proc.wait(timeout=5)
        stdout = proc.stdout.read().decode("utf-8")
        self.assertEqual(stdout.strip(), "hello_world")


class TestProcessManagerIsAlive(unittest.TestCase):
    """Tests for ProcessManager.is_alive()."""

    def test_is_alive_current_process(self):
        """Current process PID should be alive."""
        self.assertTrue(ProcessManager.is_alive(os.getpid()))

    def test_is_alive_dead_process(self):
        """A PID that doesn't exist should return False."""
        # Use a very large PID that's unlikely to exist
        self.assertFalse(ProcessManager.is_alive(999999999))


class TestProcessManagerKillGracefully(unittest.TestCase):
    """Tests for ProcessManager.kill_gracefully()."""

    def test_kill_gracefully_terminates_process(self):
        """kill_gracefully terminates a running subprocess."""
        # Start a long-running process
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            start_new_session=True,
        )
        self.assertTrue(ProcessManager.is_alive(proc.pid))

        ProcessManager.kill_gracefully(proc.pid, timeout=3)

        # Reap the child so the OS removes the process entry
        proc.wait(timeout=5)
        self.assertFalse(ProcessManager.is_alive(proc.pid))

    def test_kill_gracefully_already_dead(self):
        """kill_gracefully handles already-dead processes."""
        # This should not raise
        ProcessManager.kill_gracefully(999999999, timeout=1)


class TestRotateLogIfNeeded(unittest.TestCase):
    """Tests for _rotate_log_if_needed()."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_no_rotation_small_file(self):
        """No rotation when file is under 10MB."""
        log_path = os.path.join(self.tmpdir, "test.log")
        with open(log_path, "w") as f:
            f.write("small content")

        _rotate_log_if_needed(log_path)
        self.assertTrue(os.path.exists(log_path))
        self.assertFalse(os.path.exists(log_path + ".1"))

    def test_rotation_large_file(self):
        """Rotation occurs when file exceeds 10MB."""
        log_path = os.path.join(self.tmpdir, "test.log")
        # Write > 10MB
        with open(log_path, "w") as f:
            f.write("x" * (10 * 1024 * 1024 + 1))

        _rotate_log_if_needed(log_path)
        self.assertFalse(os.path.exists(log_path))
        self.assertTrue(os.path.exists(log_path + ".1"))

    def test_rotation_nonexistent_file(self):
        """No error when file doesn't exist."""
        _rotate_log_if_needed(os.path.join(self.tmpdir, "nonexistent.log"))


class TestAppendToLog(unittest.TestCase):
    """Tests for _append_to_log()."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_append_basic(self):
        """Appends content to log file."""
        log_path = os.path.join(self.tmpdir, "test.log")
        _append_to_log(log_path, "hello world\n")
        with open(log_path) as f:
            self.assertEqual(f.read(), "hello world\n")

    def test_append_with_prefix(self):
        """Appends content with prefix on each line (R10-AC4)."""
        log_path = os.path.join(self.tmpdir, "test.log")
        _append_to_log(log_path, "line1\nline2\n", prefix="[ERROR] ")
        with open(log_path) as f:
            content = f.read()
        self.assertIn("[ERROR] line1\n", content)
        self.assertIn("[ERROR] line2\n", content)

    def test_append_empty_does_nothing(self):
        """Empty content does not create file."""
        log_path = os.path.join(self.tmpdir, "test.log")
        _append_to_log(log_path, "")
        self.assertFalse(os.path.exists(log_path))

    def test_append_creates_directory(self):
        """Creates parent directory if needed."""
        log_path = os.path.join(self.tmpdir, "subdir", "test.log")
        _append_to_log(log_path, "data\n")
        self.assertTrue(os.path.exists(log_path))


class TestBuildJobContext(unittest.TestCase):
    """Tests for _build_job_context()."""

    def test_builds_correct_context(self):
        """Builds context dict with expected fields (R10-AC1)."""
        job = {
            "name": "my-job",
            "type": "scheduled",
            "env_vars": {"KEY": "VALUE"},
        }
        ctx = _build_job_context(job)
        self.assertEqual(ctx["job_name"], "my-job")
        self.assertEqual(ctx["job_type"], "scheduled")
        self.assertEqual(ctx["env_vars"], {"KEY": "VALUE"})
        self.assertIn("T", ctx["timestamp"])  # ISO 8601 format
        self.assertTrue(ctx["timestamp"].endswith("Z"))


class TestResolveScriptPath(unittest.TestCase):
    """Tests for _resolve_script_path()."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_resolves_absolute_path(self):
        """Resolves absolute path when file exists."""
        script = os.path.join(self.tmpdir, "test.py")
        with open(script, "w") as f:
            f.write("pass")

        job = {"script_path": script}
        self.assertEqual(_resolve_script_path(job), script)

    def test_returns_none_for_missing_absolute(self):
        """Returns None when absolute path doesn't exist."""
        job = {"script_path": "/nonexistent/path/script.py"}
        self.assertIsNone(_resolve_script_path(job))

    def test_returns_none_for_empty_path(self):
        """Returns None for empty script_path."""
        job = {"script_path": ""}
        self.assertIsNone(_resolve_script_path(job))


if __name__ == "__main__":
    unittest.main()
