# World Model

The world model is Kognisant's deep understanding system for your codebase. It builds a living dependency graph, tracks beliefs about how modules relate, detects knowledge gaps, and generates improvement goals automatically. Think of it as a second brain that watches over your code health.

---

## Why Use the World Model?

Without the world model, the AI only knows what you tell it or what it reads file-by-file. With the world model enabled, Kognisant:

- Knows which modules depend on which
- Tracks confidence in its understanding (decaying over time if not reinforced)
- Detects when contracts between modules might be violated
- Identifies code that has become stale or overly complex
- Proactively suggests improvements before problems compound

It turns Kognisant from a reactive assistant into a proactive engineering partner.

---

## Enabling and Disabling

### Enable

Inside a chat session:

```
/worldmodel enable
```

On first enable, Kognisant performs an initial analysis of your project structure, building the dependency graph from your source files.

### Disable

```
/worldmodel disable
```

The world model data is preserved on disk but no longer loaded or updated.

### Check status

```
/worldmodel status
```

Shows whether the world model is active, how many nodes and edges are tracked, and when it was last updated.

---

## What the World Model Tracks

### Dependency Graph

Nodes and edges representing your code structure:

- **Nodes** - Functions, classes, modules, files
- **Edges** - Import relationships, function calls, inheritance, composition

Each edge has a confidence score (0.0 to 1.0) that decays over time if the relationship is not reinforced by evidence.

### Beliefs

Things Kognisant believes to be true about your code, with confidence levels:

- "The `auth` module handles all authentication logic" (confidence: 0.9)
- "The `UserService` class validates email before saving" (confidence: 0.7)
- "Tests cover the rate limiter edge cases" (confidence: 0.5)

Beliefs are reinforced when evidence confirms them and contradicted when evidence suggests otherwise.

### Contracts

Implicit agreements between modules about their behavior:

- "Module A always calls Module B with a valid user_id"
- "The response from the API layer always includes a `status` field"
- "The database layer never raises unhandled exceptions to the route layer"

Contracts are auto-registered from function signatures and reinforced through usage patterns. When a contract is violated (detected via traces or static analysis), it becomes a goal candidate.

### Epistemic Gaps

Things Kognisant knows it does NOT know:

- "Unsure whether the rate limiter handles concurrent requests correctly"
- "No test coverage observed for the webhook handler"
- "The relationship between config.py and daemon.py is unclear"

Gaps are tracked and prioritized. They inform goal generation.

---

## Goal Generation

The world model generates improvement goals using 6 strategies:

### 1. Contract Violation Detection

When the system detects that a module's behavior violates an established contract:

```
Goal: Fix contract violation between auth/middleware and auth/jwt_utils
Reason: jwt_utils.verify_token now raises ValueError but middleware doesn't catch it
```

### 2. Coverage Gap Analysis

Identifies code with missing or insufficient test coverage:

```
Goal: Add tests for database/connection_pool.py
Reason: 0% test coverage detected for connection pool lifecycle methods
```

### 3. Decay Alerts

When edge confidence drops below a threshold because the relationship has not been used or verified recently:

```
Goal: Verify the integration between payment_service and notification_service
Reason: Edge confidence decayed to 0.3 (last reinforced 45 days ago)
```

### 4. Complexity Detection

Identifies functions or modules that have grown too complex:

```
Goal: Refactor auth/middleware.py:validate_request (cyclomatic complexity: 18)
Reason: Function exceeds complexity threshold of 15
```

### 5. Stale Artifact Detection

Files that have not been modified in a long time but are still depended upon:

```
Goal: Review utils/legacy_parser.py for relevance
Reason: Not modified in 90+ days but imported by 3 active modules
```

### 6. Pattern Detection

Recurring error patterns or anti-patterns observed in traces:

```
Goal: Address repeated ConnectionError in network/client.py
Reason: Same exception pattern detected in 5 of the last 10 executions
```

---

## Accepting and Dismissing Goals

### View goals

```
/goals
```

Shows active improvement goals ranked by priority:

```
Goals:
  [1] ⚠️  Fix contract violation: auth → jwt_utils (priority: HIGH)
  [2] 📊 Add tests for connection_pool.py (priority: MEDIUM)
  [3] 🔄 Review legacy_parser.py (priority: LOW)
```

### Accept a goal

```
/goals accept 1
```

Kognisant executes the goal, either immediately (if graduated autonomy allows it) or by dispatching it to the agent swarm. The acceptance is recorded as positive feedback, teaching the system to generate more goals of this type.

### Dismiss a goal

```
/goals dismiss 3
```

The goal is removed from the active list. The dismissal is recorded as negative feedback. If a goal type is consistently dismissed, the system learns to suppress or deprioritize similar goals in the future.

---

## How It Learns from Feedback

The world model uses a Learning Loop that tracks:

- **Acceptance rate per goal type** - What percentage of "coverage_gap" goals do you accept vs. dismiss?
- **Per-module patterns** - If you always dismiss goals for `legacy/` modules, it stops suggesting them
- **Manual fix detection** - If you fix something a goal suggested before accepting it, that counts as implicit positive feedback

This feedback loop means the world model gets more relevant over time. It learns what you care about and what you consider noise.

---

## Graduated Autonomy

Goals do not always require your explicit permission. The system uses three autonomy levels based on your feedback history:

### Ask (default for new goal types)

The goal is proposed and requires your `/goals accept` before execution. This is the starting point for all goal types.

### Suggest

The goal is shown proactively at session start with a note that it can auto-execute. You have a chance to dismiss but do not need to explicitly accept:

```
💡 Suggested: Add test for new endpoint in routes/users.py
   (Will auto-execute if not dismissed within this session)
```

### Auto-execute

Goals of this type run automatically in the background without prompting. This level is reached only after a consistent acceptance history (typically 80%+ acceptance rate over 10+ proposals).

The autonomy level can regress: if you start dismissing goals that were auto-executing, the system drops back to "suggest" or "ask" mode.

---

## Background Maintenance

The world model performs maintenance operations that keep its knowledge fresh:

### Confidence Decay

Edge confidence decays over time:
- Edges touching modified nodes decay faster
- Untouched edges decay slowly (preserving stable knowledge)
- Edges that drop below 0.1 confidence are pruned

### Static Analysis

On project changes (detected via git or file modification times):
- New files are analyzed for nodes and edges
- Modified files have their definitions re-extracted
- Deleted files have their nodes and edges removed

### Goal Generation Cycles

Goals are regenerated periodically, incorporating:
- Decay summaries (which edges weakened)
- New gaps discovered
- Contract violations detected
- Fresh complexity calculations

---

## Data Storage

World model data lives in `.kognisant/world_model/`:

```
.kognisant/world_model/
├── change_log.json           # History of detected changes
├── graph/
│   ├── index.json            # Node and edge index
│   ├── cross_module.json     # Cross-module relationships
│   └── modules/              # Per-module graph data
└── snapshots/                # Point-in-time snapshots
```

This data is project-local (not global) because dependency graphs are project-specific.

---

## When to Use the World Model

| Situation | Recommendation |
|:---|:---|
| Small scripts or one-off projects | Probably not needed |
| Growing codebases (10+ files) | Enable it, benefit from goal generation |
| Team projects with many modules | Highly recommended for contract tracking |
| Legacy code you are learning | Great for mapping dependencies |
| Performance-sensitive quick chats | Can disable temporarily for faster startup |

The world model adds a small overhead to session startup (initial graph load). For large projects, this is worth the tradeoff. For quick one-liner questions, you can always disable it temporarily.
