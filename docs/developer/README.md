# Developer Documentation

Technical documentation for Kognisant contributors and maintainers. These documents cover the internal architecture, execution engine design, and implementation details that go beyond what end-users need to know.

## Documents

| Document | Description |
|----------|-------------|
| [architecture.md](architecture.md) | High-level system architecture, module responsibilities, data flow, threading model, and process model |
| [execution-engine.md](execution-engine.md) | Atomic write sequence, recovery decision tree, schema versioning, FileLock, stream readers, FD cleanup, clock jump detection, and orphan cleanup |
| [job-lifecycle.md](job-lifecycle.md) | Job types, state machine, execution flows for scheduled/persistent/agent jobs, graceful shutdown, and SIGHUP responsiveness |
| [security.md](security.md) | Symlink containment, file permissions, root privilege warning, directory traversal protection, env-file best practices, and advisory locking |
| [testing.md](testing.md) | Test structure, conftest.py fixtures, test categories, running tests, coverage areas, and guidelines for adding new tests |
| [cli-reference.md](cli-reference.md) | Complete CLI command reference for daemon, job, and chat slash commands with flags, examples, and exit codes |
| [cron-scheduling.md](cron-scheduling.md) | CronParser implementation, 5-field format, supported syntax, UTC evaluation, `can_match_within_days()`, `next_run()`, and clock jump handling |

## Quick Links

- **Main README**: [`../../README.md`](../../README.md)
- **User Manual**: [`../user/user_manual.md`](../user/user_manual.md)
- **User Journeys**: [`../user/user_journeys.md`](../user/user_journeys.md)
- **Spec (Execution Engine Hardening)**: [`../../.kiro/specs/execution-engine-hardening/`](../../.kiro/specs/execution-engine-hardening/)

## Conventions

- All timestamps in the system are UTC unless otherwise noted
- The daemon targets POSIX platforms only (Linux, macOS)
- Zero external dependencies — everything uses the Python 3.10+ standard library
- File paths use `~/.kognisant_core/` for the global core directory
