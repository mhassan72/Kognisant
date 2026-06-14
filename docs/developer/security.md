# Security

Documentation of security mechanisms in the Kognisant execution engine.

## Symlink Containment

### The Threat

A malicious symlink placed in the scripts directory could trick the daemon into executing arbitrary files anywhere on the filesystem:

```
~/.kognisant_core/scripts/legit-bot.py → /etc/cron.d/malicious
```

Without protection, `ProcessManager.spawn("legit-bot.py", ...)` would execute whatever `/etc/cron.d/malicious` points to.

### The Protection: `_resolve_script_path()`

```python
def _resolve_script_path(script_path: str) -> str:
    """Resolve and validate script path against symlink attacks.

    1. Construct full path: SCRIPTS_DIR / script_path
    2. Resolve ALL symlinks: os.path.realpath(full_path)
    3. Verify resolved path starts with SCRIPTS_DIR

    Returns:
        Resolved absolute path (safe to execute).

    Raises:
        SecurityError: If resolved path escapes the scripts directory.
    """
    full_path = os.path.join(SCRIPTS_DIR, script_path)
    real_path = os.path.realpath(full_path)

    if not real_path.startswith(os.path.realpath(SCRIPTS_DIR) + os.sep):
        raise SecurityError(
            f"Script path resolves outside allowed directory: {real_path}"
        )

    return real_path
```

### Key Details

- **`os.path.realpath()`** resolves ALL symlinks in the entire path - not just the final component.
- We also call `os.path.realpath()` on `SCRIPTS_DIR` itself, in case the scripts directory is a symlink.
- The trailing `os.sep` prevents prefix confusion (e.g., `/scripts` vs `/scripts-evil`).

### When Symlink Check Fails

```python
# In ProcessManager.spawn():
if not ProcessManager.check_symlink(script_path, SCRIPTS_DIR):
    job_queue.update_status(job_name, "failed",
                           error="Script path resolves outside allowed directory")
    logger.warning(f"SECURITY: Symlink escape attempt for job '{job_name}': {script_path}")
    return None
```

The job is marked as `"failed"` and a security warning is logged. The script is never executed.

## File Permissions

### Permission Model

| File | Mode | Rationale |
|------|------|-----------|
| `jobs.json` | `0o600` | Contains env vars (potentially sensitive) |
| `jobs.json.bak` | `0o600` | Same content as primary |
| `daemon.pid` | `0o644` | PID is not sensitive; other tools may read it |
| `daemon.log` | `0o644` | Operational logs, not secrets |
| Job log files | `0o644` | Script output, not inherently sensitive |
| Script files (`.py`) | `0o644` | Readable but not directly executable |

### Enforcement Points

Permissions are set at two critical moments:

1. **During atomic write** (before rename):
   ```python
   os.chmod(tmp_path, 0o600)  # Set before file becomes visible
   os.rename(tmp_path, QUEUE_PATH)  # Now visible with correct permissions
   ```

2. **During backup creation**:
   ```python
   shutil.copy2(QUEUE_PATH, BACKUP_PATH)
   os.chmod(BACKUP_PATH, 0o600)  # Ensure backup has same protection
   ```

### Why `0o600`?

- **Owner read/write only**: No group or world access
- Prevents other system users from reading job environment variables
- On a shared multi-user system, this is the minimum required protection

### Limitations

- Any process running as the same user can still read `jobs.json`
- Environment variables are stored in **plaintext** (not encrypted)
- The system is **NOT** a secrets manager - for high-security environments, use a proper vault (HashiCorp Vault, AWS Secrets Manager, etc.)

## Root Privilege Warning

### Why Warn About Root?

Running the daemon as root is dangerous because:
- Spawned scripts inherit root privileges
- A bug in a script could damage the system
- The advisory file lock provides no protection against root-level interference

### Implementation

```python
def _check_root_privileges():
    """Warn if running as root."""
    if os.geteuid() == 0:
        logger.warning(
            "Daemon running with root privileges. "
            "Recommend running under a non-root user account."
        )
        # Also display to terminal (before fork, in CLI)
        print("Warning: Daemon running with root privileges. "
              "Recommend running under a non-root user.", file=sys.stderr)
```

### When It Fires

1. **CLI side** (before fork): Prints warning to terminal so the user sees it immediately
2. **Daemon side** (after fork): Logs warning to `daemon.log` for audit trail

The warning is informational - the daemon does not refuse to start as root. Some deployments may legitimately require root (e.g., binding to privileged ports).

## Directory Traversal Protection

### In Workspace Tools (`tools.py`)

All file operations in the workspace use a two-step validation:

```python
def _resolve_safe_path(file_path: str, project_root: str) -> str:
    """Resolve file path and verify it's within the project root.

    Protection against:
    - Relative traversal: "../../../etc/passwd"
    - Absolute paths: "/etc/passwd"
    - Symlink escape: "link_to_etc/passwd"
    """
    # Resolve both paths to eliminate symlinks
    real_root = os.path.realpath(project_root)
    real_target = os.path.realpath(os.path.join(project_root, file_path))

    # Verify target is within root
    if not real_target.startswith(real_root + os.sep) and real_target != real_root:
        return None  # Access denied

    return real_target
```

### Attack Vectors Blocked

| Attack | Example | Result |
|--------|---------|--------|
| Relative traversal | `../../../etc/passwd` | Blocked: realpath resolves to `/etc/passwd` which is outside root |
| Absolute path | `/etc/passwd` | Blocked: doesn't start with project root |
| Symlink escape | `data/link → /etc/passwd` | Blocked: realpath follows symlink, resolved path is outside root |
| Null byte injection | `file\x00.txt` | Python's `os.path` rejects null bytes |
| Unicode normalization | Various | `os.path.realpath` handles normalization before comparison |

### In Daemon Scripts

The scripts directory has its own containment (see [Symlink Containment](#symlink-containment) above). Scripts can only be executed if they physically reside within `~/.kognisant_core/scripts/`.

## Env-File Best Practices

### Recommended Pattern

```bash
# 1. Create a dedicated secrets directory
mkdir -p ~/.secrets
chmod 700 ~/.secrets

# 2. Create the env file with restricted permissions
cat > ~/.secrets/my-bot.env << 'EOF'
# Bot credentials
TELEGRAM_TOKEN=123456:ABC-DEF
DATABASE_URL=postgres://user:pass@localhost/db
API_SECRET=sk-very-secret-key
EOF
chmod 600 ~/.secrets/my-bot.env

# 3. Reference it when creating the job
kognisant job add --name my-bot --script bot.py --type persistent \
    --env-file ~/.secrets/my-bot.env
```

### Env File Format

```
# Lines starting with # are comments
# Blank lines are ignored
# Format: KEY=VALUE (no quotes needed around value)

API_KEY=sk-abc123
DB_URL=postgres://user:pass@localhost/db
DEBUG=false

# Inline comments are NOT supported:
# BAD: API_KEY=sk-abc123  # this would include "  # this would include" in the value
```

### What Happens Internally

1. Kognisant reads the env file and parses KEY=VALUE pairs
2. Values are stored in the job's `env_vars` field in `jobs.json`
3. The daemon passes them as environment variables to the subprocess via `Popen(env=...)`

### Security Considerations

- The env file itself is not monitored or managed by Kognisant
- After reading, values live in `jobs.json` (protected with `0o600`)
- Consider rotating secrets by editing the job: `kognisant job edit my-bot --env-file ~/.secrets/new.env`
- For production secrets, consider a proper secrets manager with short-lived tokens

## Atomic Script Creation

### Two-Phase Rename

Script creation is atomic to prevent partial artifacts:

```python
def create_script(name: str, content: str, description: str = "",
                  env_vars: list[str] | None = None) -> str:
    """Create script atomically.

    Sequence:
    1. Write content → {name}.py.tmp
    2. Write metadata → {name}.json.tmp
    3. Rename {name}.py.tmp → {name}.py     (atomic)
    4. Rename {name}.json.tmp → {name}.json  (atomic)

    On failure at any step:
    - Remove all .tmp files
    - Leave no partial artifacts
    """
```

### Why Two Files Need Coordinated Creation

A script without its metadata JSON would be:
- Unlistable (the `list_scripts` tool reads `.json` files to discover scripts)
- Missing description and env var specifications
- Potentially confusing to users ("I see the .py but it doesn't show up in `list_scripts`")

By writing both to temp files first, then renaming both, we ensure either both exist or neither does.

### Failure Cleanup

```python
try:
    # Write temps
    write_temp_py(...)
    write_temp_json(...)
    # Rename (atomic)
    os.rename(tmp_py, final_py)
    os.rename(tmp_json, final_json)
except Exception:
    # Clean up any temps that were created
    for tmp in [tmp_py, tmp_json]:
        if os.path.exists(tmp):
            os.unlink(tmp)
    raise
```

## Advisory Locking

### How `fcntl.flock()` Works

```python
import fcntl

fd = open("jobs.lock", "w")
fcntl.flock(fd, fcntl.LOCK_EX)  # Exclusive lock - blocks until acquired
# ... critical section ...
fcntl.flock(fd, fcntl.LOCK_UN)  # Release
fd.close()
```

### Properties of Advisory Locks

| Property | Implication |
|----------|-------------|
| **Advisory** (not mandatory) | Only works if all participants use it. We control both CLI and daemon, so this is fine. |
| **Per-file-descriptor** | Lock is released when FD is closed (even on crash) |
| **Process-scoped** | Same process can re-acquire without deadlock (reentrant) |
| **Not inherited across fork** | Child processes don't inherit parent's locks (important for daemon fork) |

### Non-Blocking with Timeout

Our `FileLock` uses `LOCK_NB` (non-blocking) with polling:

```python
fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
```

If the lock is held by another process, this raises `IOError` immediately instead of blocking forever. We retry every 50ms until the timeout (5 seconds) expires.

### What Gets Locked

The `jobs.lock` file protects the `_locked_modify` cycle:
- CLI commands that modify jobs (add, remove, edit, cancel)
- Daemon state updates (running, completed, failed)
- Both use the same lock file, so they coordinate safely

### Lock Not Acquired - Error

```
Error: timeout - Could not acquire file lock within 5 seconds.
Another process may be holding the lock.
```

This typically means:
- The daemon is in the middle of a state update (wait and retry)
- A previous CLI command crashed while holding the lock (the OS releases it when the process dies)
- A deadlock bug (should not happen with single-lock design)

## Cross-References

- [Architecture](architecture.md) - Process model and file system layout
- [Execution Engine](execution-engine.md) - Atomic write sequence and FD cleanup
- [CLI Reference](cli-reference.md) - `--env-file` flag documentation
- [Testing](testing.md) - Security-related test cases
