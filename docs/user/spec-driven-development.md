# Spec-Driven Development

Spec-Driven Development (SDD) is Kognisant's structured approach to building features. Instead of diving into code immediately, you define requirements, design the architecture, break work into tasks, and then execute methodically. The AI helps at every stage.

---

## Why Use SDD?

Ad-hoc coding works for small fixes and quick features. But for anything substantial (a new module, a refactor, a complex integration), SDD prevents:

- **Scope creep** - Requirements are defined upfront, so the AI does not wander
- **Missing edge cases** - The design phase forces you to think about contracts and boundaries
- **Lost context** - Tasks are checkpointed, so you can pause and resume days later
- **Inconsistent implementation** - The agent executes against a spec, not against ad-hoc instructions

Use SDD when the feature is big enough that you would normally spend time planning before coding.

---

## The kognisant spec Command

### Create a new spec

```bash
kognisant spec auth_module
```

This creates `.kognisant/specs/auth_module/` with three template files:
- `requirements.md` - What to build and why
- `design.md` - How to build it (architecture, data structures, APIs)
- `tasks.md` - Step-by-step implementation checklist

### List all specs

```bash
kognisant spec --list
```

Shows all specs with their current status:

```
Specs:
  🔨 auth_module    BUILD (3/8)
  📝 rate_limiter   DESIGN
  ✅ logging        DONE (5/5)
```

### Resume an existing spec

```bash
kognisant spec auth_module --resume
```

Kognisant loads the spec and picks up where you left off:

```
🛠️  Spec: auth_module
Status: BUILD (3/8 tasks done)

[c] Continue building (auto)
[n] Execute next task only
[s] Show full spec
[q] Save and quit
```

### Check spec status

```bash
kognisant spec auth_module --status
```

Shows detailed progress with completed and remaining tasks.

---

## The SDD Workflow

### Stage 1: Requirements (DEFINE)

When you create or resume a spec, Kognisant asks you to describe what you want to build. Based on your description, it generates structured requirements:

```markdown
# Feature Requirements: auth_module

## Overview
JWT-based authentication replacing the current session-based system.

## Functional Requirements
- [ ] Users can log in with email/password and receive a JWT
- [ ] JWTs expire after 15 minutes with refresh token support
- [ ] Protected routes validate the JWT before processing
- [ ] Invalid/expired tokens return 401 with clear error messages

## Non-Functional Requirements
- [ ] Token verification must complete in <5ms
- [ ] No external auth dependencies (self-contained)
- [ ] Backward-compatible with existing user database

## Success Criteria
All protected routes reject requests without valid JWTs.
Existing tests continue passing after migration.
```

You review, edit, and approve the requirements before moving on.

### Stage 2: Design (DESIGN)

Based on approved requirements, the AI proposes architecture:

```markdown
# Design Document: auth_module

## Architecture
- New module: auth/jwt_utils.py (token creation and verification)
- Modified: auth/middleware.py (session check → JWT check)
- Modified: routes/login.py (return JWT instead of session cookie)

## Data Structures
- AccessToken: {sub, exp, iat, jti}
- RefreshToken: {sub, exp, family_id}

## Behavior
1. Login: validate credentials → generate access + refresh tokens
2. Request: extract Bearer token → verify signature → attach user to request
3. Refresh: validate refresh token → issue new access token

## Interface Contract
- POST /login → {access_token, refresh_token, expires_in}
- Authorization: Bearer <token> header on all protected routes
- 401 response: {error: "token_expired" | "token_invalid"}
```

### Stage 3: Tasks (PLAN)

The design is broken into executable implementation tasks:

```markdown
# Implementation Tasks: auth_module

## Phase 1 - Scaffolding
- [ ] Create auth/jwt_utils.py with generate_token and verify_token
- [ ] Add JWT secret to config

## Phase 2 - Core Logic
- [ ] Implement token generation with proper claims
- [ ] Implement token verification with expiry check
- [ ] Update middleware to extract and validate Bearer tokens

## Phase 3 - Integration
- [ ] Modify login route to return JWTs
- [ ] Add refresh token endpoint
- [ ] Update all protected route tests
- [ ] Write integration tests for token flow
```

### Stage 4: Build (BUILD)

Now the AI executes tasks one by one (or in parallel via the agent swarm):

```
/spec auth_module run       Execute next task
/spec auth_module run all   Execute all remaining tasks
/spec auth_module done      Mark current task as complete
```

Each task execution follows the full PERP pipeline (Plan, Execute, Reflect, Persist), with the spec providing focused context.

### Stage 5: Verify (VERIFY)

After all tasks are complete, Kognisant validates the implementation against the original requirements:

- Are all functional requirements met?
- Do tests pass?
- Are non-functional constraints satisfied?

---

## Spec File Structure

All specs live under `.kognisant/specs/` in your project:

```
.kognisant/specs/
├── auth_module/
│   ├── requirements.md
│   ├── design.md
│   └── tasks.md
├── rate_limiter/
│   ├── requirements.md
│   ├── design.md
│   └── tasks.md
└── logging/
    ├── requirements.md
    ├── design.md
    └── tasks.md
```

Each spec is a self-contained directory with exactly three files. You can edit these files directly in your editor, and Kognisant will respect your changes on the next resume.

---

## Managing Specs from Chat

Inside `kognisant chat`, use the `/spec` command:

```
/spec list                    List all specs with status
/spec auth_module             Load spec context into conversation
/spec auth_module run         Execute the next unchecked task
/spec auth_module run all     Execute all remaining tasks
/spec auth_module done        Mark current task as done
```

Loading a spec into chat gives the AI full awareness of the feature being built, including requirements, design decisions, and completed/remaining tasks.

---

## How Agents Execute Against Specs

When you run `/spec auth_module run all`, Kognisant:

1. Reads the requirements and design for context
2. Identifies remaining unchecked tasks
3. Groups tasks by phase (respecting dependencies)
4. Dispatches phase tasks to the PERP agent swarm
5. Each worker has access to the full spec context
6. Completed tasks are checked off in `tasks.md`
7. Project memory is updated with progress

The spec acts as a constraint: agents cannot wander beyond what is defined. If a task requires something not in the spec, the agent reports it rather than improvising.

---

## When to Use SDD vs. Ad-hoc /agent

| Situation | Approach |
|:---|:---|
| Quick bug fix | Just ask in chat |
| Small feature (one file, one function) | `/agent` or direct chat |
| Multi-file feature with clear scope | SDD - define requirements first |
| Large refactor affecting many modules | SDD - design the migration path |
| Exploratory work (not sure what to build) | Start with chat, upgrade to SDD when scope clarifies |
| Team project with code review | SDD - the spec doubles as documentation |

**Rule of thumb:** If you would normally create a design doc or ticket before coding, use SDD. If you would just start typing, use chat or `/agent`.

---

## Pausing and Resuming

Specs are persistent. You can:

- Close the terminal
- Work on something else for days
- Come back and resume exactly where you left off

```bash
kognisant spec auth_module --resume
```

The spec remembers:
- Which tasks are completed (checked in `tasks.md`)
- Which stage you are in (DEFINE, DESIGN, PLAN, BUILD, VERIFY)
- Any notes or decisions made during the process

---

## Editing Specs Manually

All spec files are plain markdown. Edit them freely:

```bash
vim .kognisant/specs/auth_module/tasks.md
```

Common reasons to edit manually:
- Reorder tasks based on new priorities
- Add tasks you realized are needed
- Check off tasks you completed without Kognisant
- Revise requirements after user feedback
- Update the design after discovering constraints

Kognisant respects your edits. If you check off a task manually, it will skip it on the next run.

---

## Spec Status in kognisant status

The global status command shows spec progress:

```bash
kognisant status
```

```
  Specs:
    🔨 auth_module  BUILD (3/8)
    📝 rate_limiter  DESIGN
    ✅ logging      DONE (5/5)
```

Status icons:
- 📝 In planning (DEFINE or DESIGN stage)
- 🔨 In progress (BUILD stage with tasks remaining)
- ✅ Complete (all tasks checked)
