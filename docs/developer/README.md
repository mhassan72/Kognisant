# Developer Documentation

Technical documentation for Kognisant contributors and maintainers. These documents cover the internal architecture, execution engine design, and implementation details that go beyond what end-users need to know.

## Documents

### Core Architecture

| Document | Description |
|----------|-------------|
| [architecture.md](architecture.md) | High-level system architecture, module responsibilities, data flow, threading model, and process model |
| [runtime-lifecycle.md](runtime-lifecycle.md) | 5-phase cognitive runtime (Bootstrap, Plan, Execute, Reflect, Persist), ExecutionContext design, spinner states, tool box rendering, retry strategy |
| [execution-engine.md](execution-engine.md) | Atomic write sequence, recovery decision tree, schema versioning, FileLock, stream readers, FD cleanup, clock jump detection, and orphan cleanup |

### Classification and Routing

| Document | Description |
|----------|-------------|
| [fast-path-classifier.md](fast-path-classifier.md) | Rule-based message classification (<5ms), 4 tiers (SIMPLE/CONTEXT/COMPLEX/AUTONOMOUS), pattern sets, gate logic, false positive safeguards |
| [agent-escalation.md](agent-escalation.md) | Dynamic auto-escalation to PERP swarm, _detect_autonomous rules, post-exhaustion fallback, worker display, artifact collection |

### Cognitive State

| Document | Description |
|----------|-------------|
| [self-model-engine.md](self-model-engine.md) | SelfModel dataclass, atomic write pattern, Bayesian reliability, valence system, circuit breaker state machine, capability detection |
| [reflect-engine.md](reflect-engine.md) | HOT/WARM/COLD reflection tiers, valence delta rules, background signal pressure, advisories, health reports |

### Model and Network

| Document | Description |
|----------|-------------|
| [model-selection.md](model-selection.md) | Capability-based model selection, cascading fallback on failure, session-level unreachable tracking, active model priority |
| [thinking-and-reasoning.md](thinking-and-reasoning.md) | Reasoning token protocol differences, step parsing, terminal display, storage design, dynamic capability detection |

### Observability

| Document | Description |
|----------|-------------|
| [telemetry-system.md](telemetry-system.md) | JSONL format, rotation strategy, per-execution record schema, aggregation algorithms, token estimation, /telemetry output |

### Scheduling and Jobs

| Document | Description |
|----------|-------------|
| [job-lifecycle.md](job-lifecycle.md) | Job types, state machine, execution flows for scheduled/persistent/agent jobs, graceful shutdown, and SIGHUP responsiveness |
| [cron-scheduling.md](cron-scheduling.md) | CronParser implementation, 5-field format, supported syntax, UTC evaluation, `can_match_within_days()`, `next_run()`, and clock jump handling |
| [channels.md](channels.md) | Channels system: UDS IPC protocol, ChannelManager, ChannelRouter, adapter contract, credential encryption, daemon integration |

### Infrastructure

| Document | Description |
|----------|-------------|
| [security.md](security.md) | Symlink containment, file permissions, root privilege warning, directory traversal protection, env-file best practices, and advisory locking |
| [testing.md](testing.md) | Test structure, conftest.py fixtures, test categories, running tests, coverage areas, and guidelines for adding new tests |
| [world-model.md](world-model.md) | World Model subsystem: observer layer, dependency graph, goal engine, priority ranking, graduated autonomy, and integration points |

### Reference

| Document | Description |
|----------|-------------|
| [cli-reference.md](cli-reference.md) | Complete CLI command reference for daemon, job, and chat slash commands with flags, examples, and exit codes |

### Design Specs (Implementation Plans)

| Document | Description |
|----------|-------------|
| [dynamic_model_selection.md](dynamic_model_selection.md) | Design spec for capability-based model selection (implements model-selection.md) |
| [dynamic_agent_escalation.md](dynamic_agent_escalation.md) | Design spec for auto-escalation to agent swarm (implements agent-escalation.md) |
| [realignment.md](realignment.md) | Runtime realignment spec |
| [llm_uptime_and_reasoning_process.md](llm_uptime_and_reasoning_process.md) | LLM uptime and reasoning process design |

## Quick Links

- **Main README**: [`../../README.md`](../../README.md)
- **User Docs**: [`../user/README.md`](../user/README.md)
- **Spec (Runtime Realignment)**: [`../../.kiro/specs/runtime-realignment/`](../../.kiro/specs/runtime-realignment/)

## Conventions

- All timestamps in the system are UTC unless otherwise noted
- The daemon targets POSIX platforms only (Linux, macOS)
- Zero external dependencies - everything uses the Python 3.10+ standard library
- File paths use `~/.kognisant_core/` for the global core directory
- Cognitive state persisted at `~/.kognisant_core/self_model.json`
- Telemetry persisted at `~/.kognisant_core/telemetry.jsonl`

## Reading Order (Recommended)

For new contributors, read in this order:

1. [architecture.md](architecture.md) - high-level map
2. [runtime-lifecycle.md](runtime-lifecycle.md) - the main execution pipeline
3. [fast-path-classifier.md](fast-path-classifier.md) - how messages are routed
4. [self-model-engine.md](self-model-engine.md) - persistent cognitive state
5. [reflect-engine.md](reflect-engine.md) - how the system learns from outcomes
6. [thinking-and-reasoning.md](thinking-and-reasoning.md) - reasoning token handling
7. [model-selection.md](model-selection.md) - how models are chosen
8. [agent-escalation.md](agent-escalation.md) - auto-escalation to swarm
9. [telemetry-system.md](telemetry-system.md) - observability layer
