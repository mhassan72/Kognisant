"""Tests for daemon and job CLI subcommands in main.py."""

import json
import os
import sys
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from cli_kognisant.main import (
    _handle_daemon,
    _handle_job,
    _handle_job_add,
    _handle_job_cancel,
    _handle_job_list,
    _handle_job_logs,
)


# --- Daemon subcommand tests ---


class TestHandleDaemon:
    """Tests for _handle_daemon dispatch."""

    def test_daemon_status_not_running(self, capsys):
        """daemon status reports not running when no daemon exists."""
        args = MagicMock()
        args.daemon_command = "status"

        with patch(
            "cli_kognisant.main.DaemonManager.status",
            return_value={"running": False, "pid": None, "uptime": None},
        ):
            _handle_daemon(args)

        output = capsys.readouterr().out
        assert "not running" in output

    def test_daemon_status_running(self, capsys):
        """daemon status reports running with PID and uptime."""
        args = MagicMock()
        args.daemon_command = "status"

        with patch(
            "cli_kognisant.main.DaemonManager.status",
            return_value={"running": True, "pid": 12345, "uptime": "5m 30s"},
        ):
            _handle_daemon(args)

        output = capsys.readouterr().out
        assert "running" in output
        assert "12345" in output
        assert "5m 30s" in output

    def test_daemon_stop_success(self, capsys):
        """daemon stop prints success message."""
        args = MagicMock()
        args.daemon_command = "stop"

        with patch(
            "cli_kognisant.main.DaemonManager.stop", return_value=True
        ):
            _handle_daemon(args)

        output = capsys.readouterr().out
        assert "Daemon stopped" in output

    def test_daemon_stop_failure(self):
        """daemon stop exits with code 1 on failure."""
        args = MagicMock()
        args.daemon_command = "stop"

        with patch(
            "cli_kognisant.main.DaemonManager.stop", return_value=False
        ):
            with pytest.raises(SystemExit) as exc_info:
                _handle_daemon(args)
            assert exc_info.value.code == 1

    def test_daemon_start_already_running(self):
        """daemon start exits with code 1 if already running."""
        args = MagicMock()
        args.daemon_command = "start"

        with patch(
            "cli_kognisant.main.DaemonManager.start",
            side_effect=RuntimeError("Daemon already running with PID 999"),
        ):
            with pytest.raises(SystemExit) as exc_info:
                _handle_daemon(args)
            assert exc_info.value.code == 1

    def test_daemon_logs(self, capsys):
        """daemon logs prints log output."""
        args = MagicMock()
        args.daemon_command = "logs"

        with patch(
            "cli_kognisant.main.DaemonManager.read_logs",
            return_value="2025-01-01T00:00:00 INFO Daemon started\n",
        ):
            _handle_daemon(args)

        output = capsys.readouterr().out
        assert "Daemon started" in output

    def test_daemon_no_subcommand(self, capsys):
        """daemon with no subcommand shows usage."""
        args = MagicMock()
        args.daemon_command = None

        with pytest.raises(SystemExit) as exc_info:
            _handle_daemon(args)
        assert exc_info.value.code == 1


# --- Job subcommand tests ---


class TestHandleJobAdd:
    """Tests for _handle_job_add validation."""

    def test_invalid_name_uppercase(self):
        """Job add rejects uppercase letters in name."""
        args = MagicMock()
        args.name = "InvalidName"
        args.job_type = "agent"
        args.script = None
        args.cron = None
        args.task = "some task"
        args.env = None

        with pytest.raises(SystemExit) as exc_info:
            _handle_job_add(args)
        assert exc_info.value.code == 1

    def test_invalid_name_too_long(self):
        """Job add rejects names over 64 chars."""
        args = MagicMock()
        args.name = "a" * 65
        args.job_type = "agent"
        args.script = None
        args.cron = None
        args.task = "some task"
        args.env = None

        with pytest.raises(SystemExit) as exc_info:
            _handle_job_add(args)
        assert exc_info.value.code == 1

    def test_invalid_type(self):
        """Job add rejects invalid job types."""
        args = MagicMock()
        args.name = "valid-name"
        args.job_type = "unknown"
        args.script = None
        args.cron = None
        args.task = None
        args.env = None

        with pytest.raises(SystemExit) as exc_info:
            _handle_job_add(args)
        assert exc_info.value.code == 1

    def test_scheduled_missing_cron(self):
        """Job add rejects scheduled type without --cron."""
        args = MagicMock()
        args.name = "valid-name"
        args.job_type = "scheduled"
        args.script = "test.py"
        args.cron = None
        args.task = None
        args.env = None

        with pytest.raises(SystemExit) as exc_info:
            _handle_job_add(args)
        assert exc_info.value.code == 1

    def test_scheduled_invalid_cron(self):
        """Job add rejects invalid cron expression."""
        args = MagicMock()
        args.name = "valid-name"
        args.job_type = "scheduled"
        args.script = "test.py"
        args.cron = "bad expression"
        args.task = None
        args.env = None

        with pytest.raises(SystemExit) as exc_info:
            _handle_job_add(args)
        assert exc_info.value.code == 1

    def test_scheduled_missing_script(self):
        """Job add rejects scheduled type without --script."""
        args = MagicMock()
        args.name = "valid-name"
        args.job_type = "scheduled"
        args.script = None
        args.cron = "*/5 * * * *"
        args.task = None
        args.env = None

        with pytest.raises(SystemExit) as exc_info:
            _handle_job_add(args)
        assert exc_info.value.code == 1

    def test_persistent_missing_script(self):
        """Job add rejects persistent type without --script."""
        args = MagicMock()
        args.name = "valid-name"
        args.job_type = "persistent"
        args.script = None
        args.cron = None
        args.task = None
        args.env = None

        with pytest.raises(SystemExit) as exc_info:
            _handle_job_add(args)
        assert exc_info.value.code == 1

    def test_agent_missing_task(self):
        """Job add rejects agent type without --task."""
        args = MagicMock()
        args.name = "valid-name"
        args.job_type = "agent"
        args.script = None
        args.cron = None
        args.task = None
        args.env = None

        with pytest.raises(SystemExit) as exc_info:
            _handle_job_add(args)
        assert exc_info.value.code == 1

    def test_script_not_found(self):
        """Job add rejects if script doesn't exist."""
        args = MagicMock()
        args.name = "valid-name"
        args.job_type = "persistent"
        args.script = "nonexistent-script.py"
        args.cron = None
        args.task = None
        args.env = None

        with pytest.raises(SystemExit) as exc_info:
            _handle_job_add(args)
        assert exc_info.value.code == 1

    def test_invalid_env_format(self):
        """Job add rejects env args not in KEY=VAL format."""
        args = MagicMock()
        args.name = "valid-name"
        args.job_type = "agent"
        args.script = None
        args.cron = None
        args.task = "some task"
        args.env = ["NO_EQUALS_SIGN"]

        with pytest.raises(SystemExit) as exc_info:
            _handle_job_add(args)
        assert exc_info.value.code == 1

    def test_successful_agent_job_add(self, capsys, tmp_path):
        """Job add succeeds for agent job with valid args."""
        args = MagicMock()
        args.name = "my-agent-job"
        args.job_type = "agent"
        args.script = None
        args.cron = None
        args.task = "Refactor utils"
        args.env = ["KEY=value"]

        with patch("cli_kognisant.main.JobQueue") as MockQueue:
            mock_queue = MagicMock()
            mock_queue.add_job.return_value = "Job 'my-agent-job' added successfully"
            MockQueue.return_value = mock_queue
            _handle_job_add(args)

        output = capsys.readouterr().out
        assert "added successfully" in output
        mock_queue.add_job.assert_called_once()
        call_config = mock_queue.add_job.call_args[0][0]
        assert call_config["name"] == "my-agent-job"
        assert call_config["type"] == "agent"
        assert call_config["task"] == "Refactor utils"
        assert call_config["env_vars"] == {"KEY": "value"}

    def test_successful_scheduled_job_add(self, capsys, tmp_path):
        """Job add succeeds for scheduled job with valid script."""
        # Create a temp script file
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        script_file = scripts_dir / "my-script.py"
        script_file.write_text("print('hello')")

        args = MagicMock()
        args.name = "my-cron-job"
        args.job_type = "scheduled"
        args.script = "my-script.py"
        args.cron = "0 2 * * *"
        args.task = None
        args.env = None

        with patch(
            "cli_kognisant.main.os.path.expanduser",
            return_value=str(scripts_dir),
        ):
            with patch("cli_kognisant.main.JobQueue") as MockQueue:
                mock_queue = MagicMock()
                mock_queue.add_job.return_value = "Job 'my-cron-job' added successfully"
                MockQueue.return_value = mock_queue
                _handle_job_add(args)

        output = capsys.readouterr().out
        assert "added successfully" in output

    def test_duplicate_name_error(self, capsys):
        """Job add reports error for duplicate name."""
        args = MagicMock()
        args.name = "dup-job"
        args.job_type = "agent"
        args.script = None
        args.cron = None
        args.task = "some task"
        args.env = None

        with patch("cli_kognisant.main.JobQueue") as MockQueue:
            mock_queue = MagicMock()
            mock_queue.add_job.side_effect = ValueError(
                "A job with name 'dup-job' already exists"
            )
            MockQueue.return_value = mock_queue

            with pytest.raises(SystemExit) as exc_info:
                _handle_job_add(args)
            assert exc_info.value.code == 1


class TestHandleJobList:
    """Tests for _handle_job_list display."""

    def test_empty_queue(self, capsys):
        """Job list shows message when no jobs exist."""
        with patch("cli_kognisant.main.JobQueue") as MockQueue:
            mock_queue = MagicMock()
            mock_queue.load.return_value = []
            MockQueue.return_value = mock_queue
            _handle_job_list()

        output = capsys.readouterr().out
        assert "No jobs" in output

    def test_lists_jobs_with_table(self, capsys):
        """Job list displays jobs in table format."""
        jobs = [
            {
                "name": "my-bot",
                "type": "persistent",
                "state": "running",
                "last_run_at": "2025-01-01T00:00:00",
            },
            {
                "name": "nightly-test",
                "type": "scheduled",
                "state": "pending",
                "last_run_at": None,
            },
        ]

        with patch("cli_kognisant.main.JobQueue") as MockQueue:
            mock_queue = MagicMock()
            mock_queue.load.return_value = jobs
            MockQueue.return_value = mock_queue
            _handle_job_list()

        output = capsys.readouterr().out
        assert "my-bot" in output
        assert "persistent" in output
        assert "nightly-test" in output
        assert "scheduled" in output


class TestHandleJobCancel:
    """Tests for _handle_job_cancel."""

    def test_cancel_nonexistent_job(self):
        """Job cancel exits with error for unknown job."""
        with patch("cli_kognisant.main.JobQueue") as MockQueue:
            mock_queue = MagicMock()
            mock_queue.get_job.return_value = None
            MockQueue.return_value = mock_queue

            with pytest.raises(SystemExit) as exc_info:
                _handle_job_cancel("nonexistent")
            assert exc_info.value.code == 1

    def test_cancel_running_job_sends_sigterm(self, capsys):
        """Job cancel sends SIGTERM to running subprocess."""
        job = {"name": "my-job", "state": "running", "pid": 99999}

        with patch("cli_kognisant.main.JobQueue") as MockQueue:
            mock_queue = MagicMock()
            mock_queue.get_job.return_value = job
            mock_queue.update_status.return_value = True
            MockQueue.return_value = mock_queue

            with patch("cli_kognisant.main.os.kill") as mock_kill:
                _handle_job_cancel("my-job")
                mock_kill.assert_called_once()

        output = capsys.readouterr().out
        assert "cancelled" in output

    def test_cancel_pending_job(self, capsys):
        """Job cancel works for pending job without subprocess."""
        job = {"name": "my-job", "state": "pending", "pid": None}

        with patch("cli_kognisant.main.JobQueue") as MockQueue:
            mock_queue = MagicMock()
            mock_queue.get_job.return_value = job
            mock_queue.update_status.return_value = True
            MockQueue.return_value = mock_queue
            _handle_job_cancel("my-job")

        output = capsys.readouterr().out
        assert "cancelled" in output


class TestHandleJobLogs:
    """Tests for _handle_job_logs."""

    def test_logs_nonexistent_job(self):
        """Job logs exits with error for unknown job."""
        with patch("cli_kognisant.main.JobQueue") as MockQueue:
            mock_queue = MagicMock()
            mock_queue.get_job.return_value = None
            MockQueue.return_value = mock_queue

            with pytest.raises(SystemExit) as exc_info:
                _handle_job_logs("nonexistent")
            assert exc_info.value.code == 1

    def test_logs_existing_job(self, capsys):
        """Job logs prints log output for existing job."""
        job = {"name": "my-job", "state": "running", "pid": 123}

        with patch("cli_kognisant.main.JobQueue") as MockQueue:
            mock_queue = MagicMock()
            mock_queue.get_job.return_value = job
            mock_queue.read_job_logs.return_value = "Line 1\nLine 2\n"
            MockQueue.return_value = mock_queue
            _handle_job_logs("my-job")

        output = capsys.readouterr().out
        assert "Line 1" in output
        assert "Line 2" in output
