"""
Unit tests for cli_kognisant/daemon.py - DaemonManager class.

Tests daemon lifecycle methods, signal handler setup, PID file management,
stale PID detection, and log reading.
"""

import os
import signal
import sys
import tempfile
import time
from unittest.mock import patch, mock_open

import pytest

from cli_kognisant.daemon import (
    CORE_DIR,
    PID_FILE,
    LOG_FILE,
    DaemonManager,
    _sigterm_handler,
    _sighup_handler,
)


@pytest.fixture
def tmp_core_dir(tmp_path, monkeypatch):
    """Override CORE_DIR, PID_FILE, LOG_FILE to use a temp directory."""
    core_dir = str(tmp_path / "kognisant_core")
    pid_file = os.path.join(core_dir, "daemon.pid")
    log_file = os.path.join(core_dir, "daemon.log")

    monkeypatch.setattr("cli_kognisant.daemon.CORE_DIR", core_dir)
    monkeypatch.setattr("cli_kognisant.daemon.PID_FILE", pid_file)
    monkeypatch.setattr("cli_kognisant.daemon.LOG_FILE", log_file)

    os.makedirs(core_dir, exist_ok=True)
    return core_dir, pid_file, log_file


class TestSignalHandlers:
    """Test signal handler flag behavior."""

    def test_sigterm_handler_sets_shutdown_flag(self, monkeypatch):
        """SIGTERM handler sets _shutdown_flag to True (R12-AC1)."""
        import cli_kognisant.daemon as daemon_mod
        monkeypatch.setattr(daemon_mod, "_shutdown_flag", False)
        _sigterm_handler(signal.SIGTERM, None)
        assert daemon_mod._shutdown_flag is True

    def test_sighup_handler_sets_reload_flag(self, monkeypatch):
        """SIGHUP handler sets _reload_flag to True (R12-AC5)."""
        import cli_kognisant.daemon as daemon_mod
        monkeypatch.setattr(daemon_mod, "_reload_flag", False)
        _sighup_handler(signal.SIGHUP, None)
        assert daemon_mod._reload_flag is True


class TestDaemonManagerIsRunning:
    """Test is_running() with PID file and process checks."""

    def test_is_running_no_pid_file(self, tmp_core_dir):
        """Returns False when PID file doesn't exist."""
        assert DaemonManager.is_running() is False

    def test_is_running_with_active_process(self, tmp_core_dir):
        """Returns True when PID file exists and process is alive."""
        _, pid_file, _ = tmp_core_dir
        # Write our own PID — we know we're alive
        with open(pid_file, "w") as f:
            f.write(str(os.getpid()))
        assert DaemonManager.is_running() is True

    def test_is_running_stale_pid_cleaned(self, tmp_core_dir):
        """Removes stale PID file when process is dead (R1-AC6)."""
        _, pid_file, _ = tmp_core_dir
        # Write a PID that doesn't exist (very high number)
        with open(pid_file, "w") as f:
            f.write("9999999")
        assert DaemonManager.is_running() is False
        # PID file should be cleaned up
        assert not os.path.exists(pid_file)

    def test_is_running_invalid_pid_content(self, tmp_core_dir):
        """Returns False when PID file contains non-numeric content."""
        _, pid_file, _ = tmp_core_dir
        with open(pid_file, "w") as f:
            f.write("not-a-pid")
        assert DaemonManager.is_running() is False


class TestDaemonManagerStatus:
    """Test status() method."""

    def test_status_not_running(self, tmp_core_dir):
        """Status reports not running when no PID file."""
        result = DaemonManager.status()
        assert result == {"running": False, "pid": None, "uptime": None}

    def test_status_running(self, tmp_core_dir):
        """Status reports running with PID and uptime."""
        _, pid_file, _ = tmp_core_dir
        with open(pid_file, "w") as f:
            f.write(str(os.getpid()))
        result = DaemonManager.status()
        assert result["running"] is True
        assert result["pid"] == os.getpid()
        assert result["uptime"] is not None

    def test_status_stale_pid_cleaned(self, tmp_core_dir):
        """Status removes stale PID and reports not running (R1-AC6)."""
        _, pid_file, _ = tmp_core_dir
        with open(pid_file, "w") as f:
            f.write("9999999")
        result = DaemonManager.status()
        assert result["running"] is False
        assert result["pid"] is None
        assert not os.path.exists(pid_file)


class TestDaemonManagerStop:
    """Test stop() method."""

    def test_stop_no_pid_file(self, tmp_core_dir, capsys):
        """Stop prints error when no PID file exists (R1-AC9)."""
        result = DaemonManager.stop()
        assert result is False
        captured = capsys.readouterr()
        assert "No daemon is currently running" in captured.err

    def test_stop_sends_sigterm(self, tmp_core_dir):
        """Stop sends SIGTERM to the daemon process (R1-AC3)."""
        _, pid_file, _ = tmp_core_dir
        # Use a mock to verify SIGTERM is sent
        with open(pid_file, "w") as f:
            f.write(str(os.getpid()))

        with patch("os.kill") as mock_kill:
            result = DaemonManager.stop()
            mock_kill.assert_called_once_with(os.getpid(), signal.SIGTERM)
            assert result is True

    def test_stop_removes_pid_file(self, tmp_core_dir):
        """Stop removes PID file after sending signal."""
        _, pid_file, _ = tmp_core_dir
        with open(pid_file, "w") as f:
            f.write(str(os.getpid()))

        with patch("os.kill"):
            DaemonManager.stop()
        assert not os.path.exists(pid_file)

    def test_stop_dead_process_cleans_up(self, tmp_core_dir):
        """Stop handles dead process gracefully."""
        _, pid_file, _ = tmp_core_dir
        with open(pid_file, "w") as f:
            f.write("9999999")
        result = DaemonManager.stop()
        assert result is True
        assert not os.path.exists(pid_file)


class TestDaemonManagerStart:
    """Test start() method."""

    def test_start_already_running(self, tmp_core_dir, capsys):
        """Start raises error if daemon already running (R1-AC7)."""
        _, pid_file, _ = tmp_core_dir
        with open(pid_file, "w") as f:
            f.write(str(os.getpid()))

        with pytest.raises(RuntimeError, match="already running"):
            DaemonManager.start()
        captured = capsys.readouterr()
        assert "already running" in captured.err
        assert str(os.getpid()) in captured.err

    def test_start_creates_core_dir(self, tmp_path, monkeypatch):
        """Start creates CORE_DIR if it doesn't exist (R1-AC1)."""
        core_dir = str(tmp_path / "new_core_dir")
        pid_file = os.path.join(core_dir, "daemon.pid")
        log_file = os.path.join(core_dir, "daemon.log")

        monkeypatch.setattr("cli_kognisant.daemon.CORE_DIR", core_dir)
        monkeypatch.setattr("cli_kognisant.daemon.PID_FILE", pid_file)
        monkeypatch.setattr("cli_kognisant.daemon.LOG_FILE", log_file)

        # Mock fork to return a child PID (simulating parent path)
        with patch("os.fork", return_value=12345):
            pid = DaemonManager.start()
            assert pid == 12345
        assert os.path.exists(core_dir)

    def test_start_returns_child_pid(self, tmp_core_dir, capsys):
        """Start returns and prints child PID (R1-AC2)."""
        with patch("os.fork", return_value=42):
            pid = DaemonManager.start()
        assert pid == 42
        captured = capsys.readouterr()
        assert "42" in captured.out
        assert "Daemon started" in captured.out


class TestDaemonManagerReadLogs:
    """Test read_logs() method."""

    def test_read_logs_no_file(self, tmp_core_dir):
        """Returns message when log file doesn't exist (R1-AC10)."""
        result = DaemonManager.read_logs()
        assert "No log file available" in result

    def test_read_logs_returns_last_n_lines(self, tmp_core_dir):
        """Returns last N lines from daemon log (R1-AC5)."""
        _, _, log_file = tmp_core_dir
        lines = [f"2024-01-01T00:00:{i:02d} INFO Line {i}\n" for i in range(100)]
        with open(log_file, "w") as f:
            f.writelines(lines)

        result = DaemonManager.read_logs(lines=10)
        result_lines = result.strip().split("\n")
        assert len(result_lines) == 10
        assert "Line 90" in result_lines[0]
        assert "Line 99" in result_lines[-1]

    def test_read_logs_default_50_lines(self, tmp_core_dir):
        """Default reads last 50 lines."""
        _, _, log_file = tmp_core_dir
        lines = [f"2024-01-01T00:00:00 INFO Line {i}\n" for i in range(100)]
        with open(log_file, "w") as f:
            f.writelines(lines)

        result = DaemonManager.read_logs()
        result_lines = result.strip().split("\n")
        assert len(result_lines) == 50

    def test_read_logs_fewer_than_requested(self, tmp_core_dir):
        """Returns all lines when fewer than requested exist."""
        _, _, log_file = tmp_core_dir
        lines = [f"2024-01-01T00:00:00 INFO Line {i}\n" for i in range(5)]
        with open(log_file, "w") as f:
            f.writelines(lines)

        result = DaemonManager.read_logs(lines=50)
        result_lines = result.strip().split("\n")
        assert len(result_lines) == 5


class TestModuleConstants:
    """Test module-level constants are correctly defined."""

    def test_core_dir_points_to_home(self):
        """CORE_DIR expands to ~/.kognisant_core."""
        assert CORE_DIR == os.path.expanduser("~/.kognisant_core")

    def test_pid_file_in_core_dir(self):
        """PID_FILE is inside CORE_DIR."""
        assert PID_FILE == os.path.join(CORE_DIR, "daemon.pid")

    def test_log_file_in_core_dir(self):
        """LOG_FILE is inside CORE_DIR."""
        assert LOG_FILE == os.path.join(CORE_DIR, "daemon.log")
