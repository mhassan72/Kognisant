# Kognisant User Documentation

Welcome to the Kognisant user documentation. These guides cover every major feature in depth, with explanations of why, how, and when to use each one.

---

## Table of Contents

### Getting Started

| Document | Description |
|:---|:---|
| [Getting Started](getting-started.md) | Installation, first setup, your first chat session |

### Core Features

| Document | Description |
|:---|:---|
| [Persistent Memory](persistent-memory.md) | Two-layer memory system, skills, manual editing |
| [Models and Providers](models-and-providers.md) | Multi-model support, switching, circuit breakers, reliability |
| [Autonomous Agents](autonomous-agents.md) | PERP swarm, /agent command, monitoring, escalation |
| [Background Daemon](background-daemon.md) | Daemon management, job types, cron scheduling |
| [Tools and Skills](tools-and-skills.md) | Built-in tools, dynamic tool creation, custom skills |

### Advanced Features

| Document | Description |
|:---|:---|
| [World Model](world-model.md) | Dependency graph, beliefs, goal generation, graduated autonomy |
| [Spec-Driven Development](spec-driven-development.md) | Feature planning with requirements, design, tasks |
| [Reasoning Display](reasoning-display.md) | Thinking tokens, /thinking command, transparency |
| [Runtime Lifecycle](runtime-lifecycle.md) | The 5-phase execution pipeline, emoji status lines |
| [Telemetry](telemetry.md) | Execution statistics, valence, model reliability |

### Reference

| Document | Description |
|:---|:---|
| [Slash Commands](slash-commands.md) | Complete command reference with examples |

### Guides and Journeys

| Document | Description |
|:---|:---|
| [User Journeys](user_journeys.md) | Step-by-step scenario walkthroughs |
| [User Manual](user_manual.md) | Comprehensive reference manual |

---

## Reading Order

If you are new to Kognisant, we recommend this order:

1. **[Getting Started](getting-started.md)** - Install, configure, first session
2. **[Persistent Memory](persistent-memory.md)** - Understand how memory works
3. **[Models and Providers](models-and-providers.md)** - Set up your model pool
4. **[Slash Commands](slash-commands.md)** - Learn what you can do in chat
5. **[Autonomous Agents](autonomous-agents.md)** - For complex tasks
6. **[User Journeys](user_journeys.md)** - See real workflow examples

Then explore the advanced features as needed.

---

## Quick Links

- **CLI commands:** `kognisant init`, `kognisant chat`, `kognisant setup`, `kognisant status`, `kognisant spec`, `kognisant daemon`, `kognisant job`
- **In-chat help:** type `/help` inside any session
- **Developer docs:** see [`docs/developer/`](../developer/README.md) for internals and architecture
