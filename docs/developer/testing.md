# Testing

Documentation of the test infrastructure, fixtures, categories, and guidelines for the Kognisant test suite.

## Test Structure

All tests live in the `tests/` directory. The project uses **pytest** as its test runner (with tests written using both pytest-style and unittest-style patterns).

```
tests/
├── __init__.py                      # Package marker
├── conftest.py                      # Shared fixtures: tmp_core_dir, patch_paths, job_queue
├── test_agents.py                   # PERP swarm orchestration tests
├── test_atomic_write.py             # Atomic write sequence & backup verification
├── test_chat_slash_commands.py      # Slash command handler tests
├── test_config.py                   # Configuration & global core tests
├── test_daemon.py                   # DaemonManager unit tests
├── test_daemon_integration.py       # Daemon start/stop/restart integration tests
├── test_daemon_process_manager.py   # ProcessManager spawn & kill tests
├── test_jobs.py                     # JobQueue CRUD & cron parsing tests
├── test_locked_modify.py            # Concurrent access & file locking stress tests
├── test_main_daemon_job.py          # CLI daemon/job subcommand tests
├── test_pid_reuse.py                # PID reuse detection & orphan cleanup tests
├── test_recovery.py                 # Backup recovery & corruption handling tests
├── test_schema_versioning.py        # Schema version validation & migration tests
├── test_scripts.py                  # Script CRUD & symlink protection tests
├── test_tools.py                    # Workspace tool execution tests
└── test_tools_handlers.py           # Agent tool handler tests (schedule, cancel, list, logs)
```

**Total: 17 test files** + conftest.py

## conftest.py Fixtures

The `conftest.py` provides opt-in fixtures for isolated filesystem testing. These fixtures are **NOT autouse** — tests must explicitly request them to avoid breaking existing tests.

### `tmp_core_dir`

Creates a temporary directory mimicking the `~/.kognisant_core/` structure:

```python
@pytest.fixture
def tmp_core_dir(tmp_path):
    """Create a temporary CORE_DIR with scripts/ and logs/ subdirectories."""
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    return tmp_path
```

**Provides**: A `pathlib.Path` to a temp directory with `scripts/` and `logs/` subdirs.
**Cleanup**: Automatic via pytest's `tmp_path` (removed after test).

### `patch_paths`

Patches all module-level path constants to use the temporary directory:

```python
@pytest.fixture
def patch_paths(tmp_core_dir, monkeypatch):
    """Patch module-level path constants across jobs, daemon, and scripts modules."""
    core_dir = str(tmp_core_dir)
    scripts_dir = str(tmp_core_dir / "scripts")

    # Patch daemon module
    monkeypatch.setattr("cli_kognisant.daemon.CORE_DIR", core_dir)
    monkeypatch.setattr("cli_kognisant.daemon.PID_FILE", os.path.join(core_dir, "daemon.pid"))
    monkeypatch.setattr("cli_kognisant.daemon.LOG_FILE", os.path.join(core_dir, "daemon.log"))

    # Patch scripts module
    monkeypatch.setattr("cli_kognisant.scripts.SCRIPTS_DIR", scripts_dir)

    # Patch os.path.expanduser to redirect ~/.kognisant_core
    # (intercepts and redirects any path starting with the real core path)

    return core_dir
```

**Provides**: The string path to the patched core directory.
**Patches**: `cli_kognisant.daemon.*`, `cli_kognisant.scripts.*`, `os.path.expanduser`

### `job_queue`

Creates a pre-configured `JobQueue` instance using the temporary filesystem:

```python
@pytest.fixture
def job_queue(tmp_core_dir):
    """Create a JobQueue instance using the temporary core directory."""
    return JobQueue(base_dir=str(tmp_core_dir))
```

**Provides**: A fully functional `JobQueue` with isolated filesystem.
**Usage**: `def test_add_job(job_queue): ...`

## Test Categories

### Unit Tests

Test individual functions and methods in isolation with mocked dependencies.

| File | Covers |
|------|--------|
| `test_config.py` | Global core init, project discovery, file scanning, model pool |
| `test_jobs.py` | JobQueue CRUD, CronParser, FileLock, state transitions |
| `test_daemon.py` | DaemonManager methods, signal handling, PID file operations |
| `test_daemon_process_manager.py` | ProcessManager.spawn, is_alive, get_start_time, kill_gracefully |
| `test_scripts.py` | Script creation, validation, symlink checks |
| `test_tools.py` | Workspace tool handlers, sandbox verification |
| `test_tools_handlers.py` | Agent tool handlers (schedule_job, cancel_job, remove_job, etc.) |
| `test_agents.py` | PERP orchestration, subtask threading, semaphore throttling |
| `test_chat_slash_commands.py` | Slash command parsing and dispatch |
| `test_main_daemon_job.py` | CLI argument parsing, subcommand dispatch |
| `test_schema_versioning.py` | Schema validation, migration registry, version detection |

### Integration Tests

Test multiple components working together with real filesystem operations.

| File | Covers |
|------|--------|
| `test_daemon_integration.py` | Full daemon start/stop/restart cycle (using fork mocks) |
| `test_atomic_write.py` | End-to-end atomic write with actual file operations |
| `test_recovery.py` | Backup recovery with real corrupted/missing files |
| `test_locked_modify.py` | Multi-process locking with actual fcntl.flock |

### Stress Tests

Test behavior under concurrent or repeated access patterns.

| File | Covers |
|------|--------|
| `test_locked_modify.py` | Concurrent writers, lock timeout behavior |
| `test_pid_reuse.py` | Rapid PID recycling scenarios |

## How to Run Tests

### Run All Tests

```bash
# From project root
pytest
```

### Run with Verbose Output

```bash
pytest -v
```

### Run a Specific Test File

```bash
pytest tests/test_recovery.py
```

### Run a Specific Test Function

```bash
pytest tests/test_recovery.py::test_recovers_from_corrupted_primary
```

### Run Tests Matching a Pattern

```bash
pytest -k "atomic"       # All tests with "atomic" in the name
pytest -k "daemon"       # All daemon-related tests
pytest -k "not integration"  # Skip integration tests
```

### Run with Coverage (if pytest-cov installed)

```bash
pytest --cov=cli_kognisant --cov-report=html
```

### Common Pytest Flags

| Flag | Purpose |
|------|---------|
| `-v` | Verbose: show each test name |
| `-s` | Show stdout/stderr from tests (don't capture) |
| `-x` | Stop on first failure |
| `--tb=short` | Short traceback format |
| `-k EXPR` | Run tests matching expression |
| `--lf` | Re-run only last failed tests |

## Coverage Areas

### Atomic Writes (`test_atomic_write.py`)

- Temp file creation and cleanup on failure
- fsync called on file descriptor
- fsync called on directory descriptor
- Rename atomicity
- Backup file created after successful write
- Permissions set to 0o600
- Failed rename leaves original unchanged
- Concurrent writes don't corrupt (via locking)

### Schema Versioning (`test_schema_versioning.py`)

- Valid version 1 loads correctly
- Unknown version raises ValueError
- Legacy bare array auto-migrates to versioned format
- Migration functions apply in sequence
- Missing migration raises ValueError
- Each migration increments version by 1
- Migrated data is atomically saved

### PID Reuse (`test_pid_reuse.py`)

- Dead PID → mark job as failed
- Alive PID with matching start time → leave running
- Alive PID with non-matching start time → mark failed, don't signal
- Platform-specific start time retrieval (mocked)
- Multiple orphaned jobs cleaned up in single pass

### Concurrent Access (`test_locked_modify.py`)

- Two writers: second waits for lock, both complete successfully
- Lock timeout: blocked writer raises TimeoutError after 5s
- Lock released on exception (no deadlock on crash)
- Read-modify-write cycle is atomic (no lost updates)

### Crash Recovery (`test_recovery.py`)

- Primary valid → load normally
- Primary corrupted + backup valid → recover from backup
- Primary missing + backup valid → restore from backup
- Both corrupted → initialize empty, log error
- Both missing → initialize empty
- Recovery re-saves atomically (primary restored from backup)

### Tool Handlers (`test_tools_handlers.py`)

- `schedule_job`: valid inputs create job, returns success
- `schedule_job`: duplicate name returns error
- `cancel_job`: running job → SIGTERM + state change
- `cancel_job`: non-existent job → error message
- `remove_job`: running job → terminate + remove
- `list_jobs`: returns formatted job list
- `job_logs`: returns last N lines of log file
- Each handler: correct error format on invalid inputs

## Adding New Tests

### Guidelines

1. **Use existing fixtures**: Request `tmp_core_dir`, `patch_paths`, or `job_queue` instead of creating your own temp directories.

2. **Isolate from real filesystem**: Never read/write to `~/.kognisant_core/` in tests. Always use the patched paths.

3. **Name tests descriptively**:
   ```python
   # Good
   def test_atomic_write_sets_permissions_to_0o600():
   def test_recovery_from_backup_when_primary_corrupted():

   # Bad
   def test_write():
   def test_recovery():
   ```

4. **One assertion per concept**: Each test should verify one behavior. Multiple assertions are fine if they're checking different aspects of the same behavior.

5. **Use pytest parametrize for variants**:
   ```python
   @pytest.mark.parametrize("exit_code,expected_state", [
       (0, "completed"),
       (1, "failed"),
       (137, "failed"),
   ])
   def test_job_state_after_exit(exit_code, expected_state):
       ...
   ```

6. **Mock at the boundary**: Mock external calls (`os.fork`, `subprocess.Popen`, `fcntl.flock`) not internal functions. This tests more real code.

7. **Clean up after yourself**: If your test creates files outside `tmp_path`, use a fixture with teardown to clean them up.

### Test File Template

```python
"""Tests for [module/feature description].

Tests cover:
- [Category 1]
- [Category 2]
"""

import os
import pytest
from unittest.mock import patch, MagicMock

from cli_kognisant.module import function_under_test


class TestFeatureName:
    """Tests for [specific feature]."""

    def test_happy_path(self, job_queue):
        """[Description of what success looks like]."""
        result = function_under_test(valid_input)
        assert result == expected_output

    def test_error_case(self, job_queue):
        """[Description of error scenario]."""
        with pytest.raises(ValueError, match="expected message"):
            function_under_test(invalid_input)

    def test_edge_case(self, tmp_core_dir):
        """[Description of edge case]."""
        # Setup edge condition
        ...
        result = function_under_test(edge_input)
        assert result == edge_expected
```

### Where to Put New Tests

| Testing... | Put in... |
|-----------|-----------|
| JobQueue methods | `test_jobs.py` |
| Daemon lifecycle | `test_daemon.py` or `test_daemon_integration.py` |
| Process management | `test_daemon_process_manager.py` |
| Script operations | `test_scripts.py` |
| CLI commands | `test_main_daemon_job.py` |
| Chat slash commands | `test_chat_slash_commands.py` |
| Tool handlers | `test_tools_handlers.py` or `test_tools.py` |
| Atomic operations | `test_atomic_write.py` |
| Recovery scenarios | `test_recovery.py` |
| New feature | Create `test_<feature_name>.py` |

## Cross-References

- [Architecture](architecture.md) — Module structure and responsibilities
- [Execution Engine](execution-engine.md) — Internals being tested
- [Security](security.md) — Security test cases
