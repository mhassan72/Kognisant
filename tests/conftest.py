"""Shared test fixtures for execution engine hardening tests.

Provides opt-in fixtures for isolated filesystem testing:
- tmp_core_dir: Creates a temporary CORE_DIR with scripts/ and logs/ subdirs
- patch_paths: Patches module-level path constants to use tmp_core_dir
- job_queue: Pre-configured JobQueue instance using temp directory

These fixtures are NOT autouse — tests must explicitly request them
to avoid breaking existing tests that manage their own setup.
"""

import os
import tempfile

import pytest

from cli_kognisant.jobs import JobQueue


@pytest.fixture
def tmp_core_dir(tmp_path):
    """Create a temporary CORE_DIR with scripts/ and logs/ subdirectories.

    Returns the path to the temp core directory.
    Cleanup is handled automatically by pytest's tmp_path.
    """
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    return tmp_path


@pytest.fixture
def patch_paths(tmp_core_dir, monkeypatch):
    """Patch module-level path constants across jobs, daemon, and scripts modules.

    Patches:
    - cli_kognisant.daemon: CORE_DIR, PID_FILE, LOG_FILE
    - cli_kognisant.scripts: SCRIPTS_DIR
    - os.path.expanduser: redirects ~/.kognisant_core to tmp_core_dir

    This fixture is opt-in (not autouse) to avoid breaking existing tests.
    """
    core_dir = str(tmp_core_dir)
    scripts_dir = str(tmp_core_dir / "scripts")

    # Patch daemon module-level constants
    monkeypatch.setattr("cli_kognisant.daemon.CORE_DIR", core_dir)
    monkeypatch.setattr(
        "cli_kognisant.daemon.PID_FILE",
        os.path.join(core_dir, "daemon.pid"),
    )
    monkeypatch.setattr(
        "cli_kognisant.daemon.LOG_FILE",
        os.path.join(core_dir, "daemon.log"),
    )

    # Patch scripts module-level constant
    monkeypatch.setattr("cli_kognisant.scripts.SCRIPTS_DIR", scripts_dir)

    # Capture the real expanded path BEFORE we patch expanduser
    _real_expanduser = os.path.expanduser
    _real_core_path = _real_expanduser("~/.kognisant_core")

    def _patched_expanduser(path):
        # Redirect any path that references ~/.kognisant_core
        expanded = _real_expanduser(path)
        if expanded.startswith(_real_core_path):
            return expanded.replace(_real_core_path, core_dir, 1)
        return expanded

    monkeypatch.setattr("os.path.expanduser", _patched_expanduser)

    return core_dir


@pytest.fixture
def job_queue(tmp_core_dir):
    """Create a JobQueue instance using the temporary core directory.

    Returns a fully functional JobQueue with isolated filesystem.
    """
    return JobQueue(base_dir=str(tmp_core_dir))
