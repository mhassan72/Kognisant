# Persistent Memory

Kognisant's memory system is what separates it from every other AI CLI tool. Instead of starting fresh each session, Kognisant builds and maintains a living knowledge base that compounds over time.

---

## Why Persistent Memory Matters

Without persistent memory, you re-explain your architecture, coding patterns, and project goals every single session. With Kognisant, you teach it once and it remembers forever. Over weeks of use, the assistant develops a deep understanding of your codebase, your preferences, and your workflow.

---

## The Two-Layer Memory System

Kognisant uses a dual-layer approach that separates project-specific knowledge from universal knowledge.

### Layer 1: Local Memory (per project)

**Location:** `.kognisant/context.md` in your project root

This is the "Membrain" for a single project. It stores:

- Active milestones and development phases
- Architecture notes and decisions made
- Current task checklists with completion state
- File structure maps and key module descriptions
- Technical debt notes and known issues

Every time you start a chat session inside a project, Kognisant loads this file into its system prompt. The AI reads it before responding to any message, giving it full project awareness.

**Example `context.md`:**

```markdown
# Project: my-rest-api

## Architecture
- FastAPI backend with SQLAlchemy ORM
- PostgreSQL database, Redis for caching
- JWT-based authentication

## Current Phase
Building rate limiting middleware

## Active Tasks
- [x] Design rate limiter interface
- [x] Implement sliding window counter
- [ ] Add Redis backend for distributed limiting
- [ ] Write integration tests

## Key Decisions
- Chose sliding window over token bucket (simpler for our scale)
- Rate limits stored in Redis, not in-memory (need multi-instance support)
```

### Layer 2: Global Memory (cross-project)

**Location:** `~/.kognisant_core/`

This is the universal intelligence layer shared across all your projects. It contains:

| Subdirectory | Contents |
|:---|:---|
| `skills/` | Markdown knowledge files that steer AI behavior |
| `tools/` | Custom Python tools with JSON schemas |
| `scripts/` | Daemon-schedulable Python scripts |
| `projects.json` | Registry of all initialized workspaces |
| `models_pool.json` | Model configurations and provider settings |
| `self_model.json` | Reliability tracking, valence, circuit breakers |

When you teach Kognisant something useful in one project (a coding pattern, a debugging technique, a tool), it can carry that knowledge to every other project you work on.

---

## Skills: Steering AI Behavior

Skills are markdown files in `~/.kognisant_core/skills/` that shape how Kognisant approaches tasks. They are loaded into every session's system prompt.

### Default Skills (ship with Kognisant)

- **`web_browser_steering.md`** - When and how to use web browsing tools (background search vs. headless fetch vs. native browser)
- **`coding_standards.md`** - Modular code patterns, clean architecture principles, exception handling guidelines
- **`global_tool_development.md`** - The strict contract for building new dynamic tools

### Creating Custom Skills

Create any `.md` file in the skills directory:

```bash
# Create a skill that teaches Kognisant your team's conventions
cat > ~/.kognisant_core/skills/team_conventions.md << 'EOF'
# Team Coding Conventions

## Naming
- Use snake_case for Python files and functions
- Use PascalCase for classes
- Prefix private methods with underscore

## Testing
- Every public function needs at least one test
- Use pytest fixtures, not setUp/tearDown
- Mock external services, never hit real APIs in tests

## Git
- Commits follow conventional commits format
- Feature branches: feature/short-description
- Always squash merge to main
EOF
```

Once saved, this skill is automatically loaded in your next session. You will see it reflected in the `⚡` line:

```
⚡ gemma4:latest | valence: +15 | 4 skills, 4 tools
```

### Viewing Loaded Skills

Inside a chat session:

```
/skills
```

This shows all active skills with their names and descriptions.

---

## How Memory Compounds Over Time

The magic happens through the combination of layers:

1. **Session 1:** You explain your project architecture. The PERP pipeline's Persist phase writes it to `context.md`.
2. **Session 5:** You teach it a debugging technique. It creates a new global skill.
3. **Session 10:** You ask it to build a tool for image optimization. It creates a global tool available in all projects.
4. **Session 20:** The world model has mapped your dependency graph. Goals are auto-generated for code improvements.

Each session builds on the last. The AI does not start from zero.

---

## When Memory Updates Automatically

Memory updates happen during the **Persist phase** of the PERP execution pipeline. This phase runs after every successful response that:

- Creates or modifies project files
- Completes a milestone or task
- Discovers significant architecture information
- Runs a multi-step agent workflow

The update is atomic: Kognisant writes to a temp file, syncs to disk, then renames over the target. If anything fails, the previous state is preserved.

### What triggers a context.md update:

- Agent swarm completes a task (checks off items, adds notes)
- You ask Kognisant to remember something ("remember that we chose Redis for sessions")
- A spec task is marked complete
- The reflection engine detects a significant project change

### What does NOT trigger an update:

- Simple Q&A exchanges
- Reading files without making changes
- Failed executions (the rollback prevents memory corruption)

---

## Manually Editing context.md

You can (and should) edit `context.md` directly. It is a plain markdown file. Common reasons to edit manually:

- Add architecture decisions made outside Kognisant
- Update task checklists after manual work
- Remove outdated information
- Restructure sections for clarity

```bash
# Open in your editor
vim .kognisant/context.md

# Or use any editor
code .kognisant/context.md
```

Kognisant reads this file fresh at the start of every session, so your changes take effect immediately next time you run `kognisant chat`.

**Tips for effective context.md:**

- Keep it focused on active work (move completed phases to an archive section)
- Use markdown checklists for trackable tasks
- Include "Key Decisions" to prevent the AI from re-proposing rejected approaches
- Note file paths for critical modules so the AI knows where to look

---

## Viewing Memory In-Session

To see the current project memory without leaving your chat:

```
/context
```

This renders the full `context.md` contents directly in the terminal.

---

## Memory vs. Chat History

These are separate concepts:

| | Chat History | Persistent Memory |
|:---|:---|:---|
| Scope | Single session | All sessions |
| Storage | `.kognisant/history/session_*.json` | `.kognisant/context.md` |
| Loaded as | Message array (rolling window) | System prompt (always present) |
| Size | Grows with conversation | Kept concise by AI |
| Cleared by | `/clear` or session end | Manual edit or AI update |

Chat history gives the AI short-term conversational context. Persistent memory gives it long-term project understanding. Both are used together in every response.

---

## Global Core Directory Structure

Here is the full layout of `~/.kognisant_core/`:

```
~/.kognisant_core/
├── skills/                  # Markdown knowledge files
│   ├── web_browser_steering.md
│   ├── coding_standards.md
│   └── global_tool_development.md
├── tools/                   # Dynamic tools (JSON schema + Python impl)
│   ├── search_web.json
│   ├── search_web.py
│   ├── browse_web_page.json
│   ├── browse_web_page.py
│   └── ...
├── scripts/                 # Daemon-schedulable scripts
├── logs/                    # Error and diagnostic logs
├── models_pool.json         # Provider and model configurations
├── projects.json            # Registry of all initialized projects
├── self_model.json          # Reliability, valence, circuit breakers
├── autonomy_config.json     # Graduated autonomy settings
└── goal_stats.json          # Goal acceptance/dismissal tracking
```

---

## When to Use Each Layer

| Situation | Where to store it |
|:---|:---|
| Project architecture, current tasks | Local: `.kognisant/context.md` |
| Coding standards for all your work | Global: `~/.kognisant_core/skills/` |
| A reusable utility tool | Global: `~/.kognisant_core/tools/` |
| A script to run on a schedule | Global: `~/.kognisant_core/scripts/` |
| API keys and model configs | Global: `~/.kognisant_core/models_pool.json` |
