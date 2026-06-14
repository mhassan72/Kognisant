"""Tests for the new daemon/job slash commands in chat.py."""

import os
import json
import tempfile
import unittest
from io import StringIO
from unittest.mock import patch, MagicMock

from cli_kognisant.chat import process_slash_commands


class TestJobsSlashCommand(unittest.TestCase):
    """Tests for /jobs slash command (R8-AC1)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.project_info = {"root": self.tmpdir, "files": []}
        self.history = []

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @patch("cli_kognisant.jobs.JobQueue.load")
    def test_jobs_empty_queue(self, mock_load):
        """Display message when no jobs exist."""
        mock_load.return_value = []

        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            result = process_slash_commands("/jobs", self.project_info, self.history)

        self.assertTrue(result)
        self.assertIn("No jobs", mock_out.getvalue())

    @patch("cli_kognisant.jobs.JobQueue.load")
    def test_jobs_displays_all_jobs(self, mock_load):
        """Display all jobs with name, type, and state."""
        mock_load.return_value = [
            {"name": "test-bot", "type": "persistent", "state": "running"},
            {"name": "nightly-tests", "type": "scheduled", "state": "pending"},
        ]

        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            result = process_slash_commands("/jobs", self.project_info, self.history)

        self.assertTrue(result)
        output = mock_out.getvalue()
        self.assertIn("test-bot", output)
        self.assertIn("persistent", output)
        self.assertIn("nightly-tests", output)
        self.assertIn("scheduled", output)


class TestJobStopSlashCommand(unittest.TestCase):
    """Tests for /job stop <name> slash command (R8-AC2)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.project_info = {"root": self.tmpdir, "files": []}
        self.history = []

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @patch("cli_kognisant.jobs.JobQueue.get_job")
    def test_job_stop_nonexistent(self, mock_get_job):
        """Error when stopping a non-existent job (R8-AC7)."""
        mock_get_job.return_value = None

        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            result = process_slash_commands("/job stop ghost-job", self.project_info, self.history)

        self.assertTrue(result)
        self.assertIn("does not exist", mock_out.getvalue())

    @patch("cli_kognisant.jobs.JobQueue.update_status")
    @patch("cli_kognisant.jobs.JobQueue.get_job")
    def test_job_stop_success(self, mock_get_job, mock_update):
        """Successfully stop a running job."""
        mock_get_job.return_value = {
            "name": "my-bot", "type": "persistent", "state": "running", "pid": None,
        }
        mock_update.return_value = True

        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            result = process_slash_commands("/job stop my-bot", self.project_info, self.history)

        self.assertTrue(result)
        self.assertIn("stopped", mock_out.getvalue())
        mock_update.assert_called_once_with("my-bot", "cancelled", pid=None)


class TestJobLogsSlashCommand(unittest.TestCase):
    """Tests for /job logs <name> slash command (R8-AC3)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.project_info = {"root": self.tmpdir, "files": []}
        self.history = []

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @patch("cli_kognisant.jobs.JobQueue.get_job")
    def test_job_logs_nonexistent(self, mock_get_job):
        """Error when viewing logs for a non-existent job (R8-AC7)."""
        mock_get_job.return_value = None

        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            result = process_slash_commands("/job logs ghost-job", self.project_info, self.history)

        self.assertTrue(result)
        self.assertIn("does not exist", mock_out.getvalue())

    @patch("cli_kognisant.jobs.JobQueue.read_job_logs")
    @patch("cli_kognisant.jobs.JobQueue.get_job")
    def test_job_logs_success(self, mock_get_job, mock_read_logs):
        """Display last 30 lines of job log."""
        mock_get_job.return_value = {
            "name": "my-bot", "type": "persistent", "state": "running", "pid": None,
        }
        mock_read_logs.return_value = "line 1\nline 2\nline 3\n"

        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            result = process_slash_commands("/job logs my-bot", self.project_info, self.history)

        self.assertTrue(result)
        mock_read_logs.assert_called_once_with("my-bot", lines=30)
        output = mock_out.getvalue()
        self.assertIn("line 1", output)


class TestJobRestartSlashCommand(unittest.TestCase):
    """Tests for /job restart <name> slash command (R8-AC4)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.project_info = {"root": self.tmpdir, "files": []}
        self.history = []

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @patch("cli_kognisant.jobs.JobQueue.get_job")
    def test_job_restart_nonexistent(self, mock_get_job):
        """Error when restarting a non-existent job (R8-AC7)."""
        mock_get_job.return_value = None

        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            result = process_slash_commands("/job restart ghost-job", self.project_info, self.history)

        self.assertTrue(result)
        self.assertIn("does not exist", mock_out.getvalue())

    @patch("cli_kognisant.jobs.JobQueue.update_status")
    @patch("cli_kognisant.jobs.JobQueue.get_job")
    def test_job_restart_persistent_crash_loop(self, mock_get_job, mock_update):
        """Restart a crash-looped persistent job resets state."""
        mock_get_job.return_value = {
            "name": "my-bot", "type": "persistent", "state": "crash_loop",
            "pid": None, "restart_count": 6,
        }
        mock_update.return_value = True

        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            result = process_slash_commands("/job restart my-bot", self.project_info, self.history)

        self.assertTrue(result)
        self.assertIn("restarted", mock_out.getvalue())
        mock_update.assert_called_once_with(
            "my-bot", "pending",
            restart_count=0,
            restart_timestamps=[],
        )

    @patch("cli_kognisant.jobs.JobQueue.get_job")
    def test_job_restart_non_persistent_rejected(self, mock_get_job):
        """Restart rejected for non-persistent jobs."""
        mock_get_job.return_value = {
            "name": "cron-job", "type": "scheduled", "state": "failed",
            "pid": None, "restart_count": 0,
        }

        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            result = process_slash_commands("/job restart cron-job", self.project_info, self.history)

        self.assertTrue(result)
        self.assertIn("Only persistent", mock_out.getvalue())


class TestDaemonSlashCommand(unittest.TestCase):
    """Tests for /daemon status|start slash commands (R8-AC5,6)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.project_info = {"root": self.tmpdir, "files": []}
        self.history = []

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @patch("cli_kognisant.daemon.DaemonManager.status")
    def test_daemon_status_running(self, mock_status):
        """Display daemon running status with PID and uptime."""
        mock_status.return_value = {
            "running": True, "pid": 12345, "uptime": "2h 15m 30s",
        }

        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            result = process_slash_commands("/daemon status", self.project_info, self.history)

        self.assertTrue(result)
        output = mock_out.getvalue()
        self.assertIn("12345", output)
        self.assertIn("2h 15m 30s", output)

    @patch("cli_kognisant.daemon.DaemonManager.status")
    def test_daemon_status_not_running(self, mock_status):
        """Display daemon not running status."""
        mock_status.return_value = {
            "running": False, "pid": None, "uptime": None,
        }

        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            result = process_slash_commands("/daemon status", self.project_info, self.history)

        self.assertTrue(result)
        output = mock_out.getvalue()
        self.assertIn("No", output)

    @patch("cli_kognisant.daemon.DaemonManager.start")
    def test_daemon_start_success(self, mock_start):
        """Start daemon and display confirmation."""
        mock_start.return_value = 99999

        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            result = process_slash_commands("/daemon start", self.project_info, self.history)

        self.assertTrue(result)
        output = mock_out.getvalue()
        self.assertIn("99999", output)
        self.assertIn("started", output)

    @patch("cli_kognisant.daemon.DaemonManager.start")
    def test_daemon_start_already_running(self, mock_start):
        """Error when daemon already running."""
        mock_start.side_effect = RuntimeError("Daemon already running with PID 12345")

        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            result = process_slash_commands("/daemon start", self.project_info, self.history)

        self.assertTrue(result)
        output = mock_out.getvalue()
        self.assertIn("already running", output)


class TestHelpUpdated(unittest.TestCase):
    """Tests that /help now includes daemon/job commands."""

    def setUp(self):
        self.project_info = {"root": "/tmp", "files": []}
        self.history = []

    def test_help_overview_includes_daemon_jobs(self):
        """Help overview shows Daemon & Jobs section."""
        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            result = process_slash_commands("/help", self.project_info, self.history)

        self.assertTrue(result)
        output = mock_out.getvalue()
        self.assertIn("Daemon", output)
        self.assertIn("Jobs", output)
        self.assertIn("/daemon", output)
        self.assertIn("/jobs", output)

    def test_help_daemon_detailed(self):
        """Detailed help for /daemon."""
        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            result = process_slash_commands("/help daemon", self.project_info, self.history)

        self.assertTrue(result)
        output = mock_out.getvalue()
        self.assertIn("status", output)
        self.assertIn("start", output)

    def test_help_jobs_detailed(self):
        """Detailed help for /jobs."""
        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            result = process_slash_commands("/help jobs", self.project_info, self.history)

        self.assertTrue(result)
        output = mock_out.getvalue()
        self.assertIn("List all jobs", output)

    def test_help_job_detailed(self):
        """Detailed help for /job."""
        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            result = process_slash_commands("/help job", self.project_info, self.history)

        self.assertTrue(result)
        output = mock_out.getvalue()
        self.assertIn("stop", output)
        self.assertIn("logs", output)
        self.assertIn("restart", output)


class TestJobSlashCommandUsage(unittest.TestCase):
    """Tests for /job with missing or invalid arguments."""

    def setUp(self):
        self.project_info = {"root": "/tmp", "files": []}
        self.history = []

    def test_job_no_args_shows_usage(self):
        """Show usage when /job has no subcommand."""
        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            result = process_slash_commands("/job", self.project_info, self.history)

        self.assertTrue(result)
        self.assertIn("Usage", mock_out.getvalue())

    def test_job_stop_no_name_shows_usage(self):
        """Show usage when /job stop has no job name."""
        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            result = process_slash_commands("/job stop", self.project_info, self.history)

        self.assertTrue(result)
        self.assertIn("Usage", mock_out.getvalue())

    def test_daemon_no_args_shows_usage(self):
        """Show usage when /daemon has no subcommand."""
        with patch("sys.stdout", new_callable=StringIO) as mock_out:
            result = process_slash_commands("/daemon", self.project_info, self.history)

        self.assertTrue(result)
        self.assertIn("Usage", mock_out.getvalue())


if __name__ == "__main__":
    unittest.main()
