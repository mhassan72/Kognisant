# Fast-Path Classifier

Rule-based message classification that assigns every user message to a
processing tier in under 5ms. No LLM calls, no network, no disk I/O.

## Why Rule-Based (Not LLM)

The classifier runs on every single message, including "hi" and "thanks".
Using an LLM for classification would mean:

- 200-2000ms latency before the actual work begins
- Wasted tokens on trivial messages
- Network dependency for a pre-processing step
- Cold-start penalty on local models (loading weights just to classify)

The rule-based approach gives us:
- <5ms classification on any hardware
- Zero token cost
- Works offline
- Deterministic (same input always gets same tier)

The tradeoff is precision. The classifier uses heuristics that can misfire on
edge cases. But the cost of misclassification is low: SIMPLE getting classified
as CONTEXT just means slightly more system prompt tokens. COMPLEX getting
classified as CONTEXT means no tools (the model responds conversationally,
and the user can rephrase).

## The 4 Tiers

| Tier | Timeout | Tools | Context Window | System Prompt |
|------|---------|-------|----------------|---------------|
| SIMPLE | 30s (120s local) | None | Last assistant msg | Minimal (~50 tokens) |
| CONTEXT | 60s (180s local) | None | Last 10 messages | Project context (~1500 tokens) |
| COMPLEX | 120s (300s local) | Yes | Last 20 messages (pruned) | Full (~2000 tokens) |
| AUTONOMOUS | N/A | Swarm | N/A | Delegated to PERP orchestrator |

AUTONOMOUS is not assigned by the classifier directly. It's an upgrade
applied to COMPLEX messages in the Plan phase when `_detect_autonomous()`
detects multi-step intent.

## Pattern Sets

### ACTION_VERBS (32 verbs)

```python
ACTION_VERBS = {
    "fix", "create", "read", "edit", "write", "modify", "delete", "remove",
    "refactor", "implement", "build", "run", "execute", "test", "deploy",
    "add", "update", "install", "search", "browse", "download", "schedule",
    "script", "migrate", "optimize", "debug", "change", "move", "rename",
    "copy", "generate", "make",
}
```

These are the words that indicate the user wants the system to DO something,
not just respond conversationally. Any one of these triggers COMPLEX.

### PROJECT_REFS (8 words)

```python
PROJECT_REFS = {
    "we", "our", "project", "working", "progress", "status", "recap", "summary",
}
```

Words that indicate the user is asking about project state. These DON'T
trigger COMPLEX (no tools needed), but they block SIMPLE (need project context).

### CONTEXT_INDICATORS (12 words)

```python
CONTEXT_INDICATORS = {
    "explain", "describe", "how", "why", "what", "when", "where", "which",
    "compare", "difference", "overview", "meaning",
}
```

Question words that indicate conceptual queries. Block SIMPLE (need context to
answer well) but don't trigger COMPLEX (no tool use needed).

### FILE_PATTERN (regex)

```python
FILE_PATTERN = re.compile(
    r"\b\w+\.\w{1,5}\b"   # word.ext (1-5 char extension)
    r"|"
    r"\w+/\w+"             # word/word paths
)
```

Matches: `main.py`, `src/utils`, `package.json`, `a/b`
Does NOT match: `e.g.`, `3.14` (numbers filtered by word boundary rules)

### CODE_PATTERN (regex)

```python
CODE_PATTERN = re.compile(
    r"\b\w+_\w+\b"              # underscore: user_model, get_name
    r"|"
    r"\b[A-Z][a-z]+[A-Z]\w*\b" # UpperCamel: UserModel, HttpClient
    r"|"
    r"\b[a-z]+[A-Z]\w*\b"      # lowerCamel: getElementById, myFunc
    r"|"
    r"__\w+__"                   # dunder: __init__, __name__
)
```

Detects code identifiers in natural language. If the user mentions code
symbols, they likely need tools to interact with those symbols.

## Gate Logic

Classification uses two gates applied in order:

```
classify(message):
│
├─ Compute signals:
│   has_verb          = lower_words ∩ ACTION_VERBS != ∅
│   has_file          = FILE_PATTERN matches
│   has_code          = CODE_PATTERN matches
│   has_refs          = lower_words ∩ PROJECT_REFS != ∅
│   has_context       = lower_words ∩ CONTEXT_INDICATORS != ∅
│   is_multi_sentence = /\.\s+[A-Z]/ matches
│   word_count        = len(message.split())
│
├─ SIMPLE gate (ALL must be true):
│   word_count <= 6
│   AND NOT has_verb
│   AND NOT has_file
│   AND NOT has_code
│   AND NOT has_refs
│   AND NOT has_context
│   AND NOT is_multi_sentence
│   -> return "SIMPLE"
│
├─ COMPLEX gate (ANY triggers):
│   has_verb
│   OR has_file
│   OR has_code
│   OR is_multi_sentence
│   OR word_count > 30
│   -> return "COMPLEX"
│
└─ CONTEXT fallback:
    Neither SIMPLE nor COMPLEX
    -> return "CONTEXT"
```

### Why This Gate Order

SIMPLE is checked first because it's the strictest. It's a "prove you're trivial"
gate. If ANY complexity signal is present, it fails.

COMPLEX is checked next because any single action indicator is enough to justify
tools and extended context.

CONTEXT is the fallback. Questions about the project, multi-word greetings,
conceptual queries that need context but not tools.

## AUTONOMOUS Detection

Applied only to COMPLEX messages, in the Plan phase:

```python
def _detect_autonomous(message: str) -> tuple[bool, str]:
```

### The 3 Verb Groups

```python
_RESEARCH_VERBS = {"look", "browse", "fetch", "check", "explore", "research", "search", "find", "inspect"}
_CREATION_VERBS = {"write", "create", "generate", "build", "make", "produce", "draft", "compose", "author"}
_ANALYSIS_VERBS = {"compare", "analyze", "evaluate", "assess", "contrast", "benchmark"}
```

### Detection Rules (in order)

```
Rule 1: 2+ distinct phases present (research + creation, analysis + creation, etc.)
         -> "Multi-phase task detected: research + creation"

Rule 2: URL present + creation verb
         -> "URL research + content creation"

Rule 3: Multi-output marker phrases in message
         Markers: "then write", "then create", "and write", "and create",
                  "write an article", "write a report", "create a document",
                  "generate a report", "draft an article", "write a comparison",
                  "write a summary", "create a plan", "build a report",
                  "write documentation", "create documentation"
         -> "Multi-output pattern: 'write a report'"

Rule 4: 50+ words with 3+ conjunctions (and, then, also, after, next)
         -> "Long compound instruction with multiple steps"
```

## False Positive Safeguards

The AUTONOMOUS classifier does NOT trigger for:

| Message | Why NOT |
|---------|---------|
| "fix the bug in auth.py" | Only 1 phase (creation), no research |
| "read main.py and explain it" | "read" is research, "explain" is not creation |
| "what does this function do?" | No action verbs at all (CONTEXT) |
| "refactor the auth module" | Only 1 phase (creation) |
| "write a test for login" | Only 1 phase (creation) |

It DOES trigger for:

| Message | Rule | Reason |
|---------|------|--------|
| "look at the repo and write a README" | 1 | research + creation |
| "compare our API to Stripe's and write a report" | 1 | research + analysis + creation |
| "https://example.com then create a summary" | 2 | URL + creation |
| "browse the docs, find examples, and create test files" | 1 | research + creation |

### Why Not Check Word Count Alone?

A 60-word message might still be a single action ("here's my detailed bug report,
please fix the auth module to handle the case where..."). Length alone doesn't
indicate multiple autonomous steps. The conjunction count (Rule 4) requires
BOTH length AND coordination markers.

## Performance Characteristics

```
Typical classification time: 0.1-0.5ms
Worst case (long message, all patterns checked): ~2ms
Memory: all patterns compiled at module import (re.compile)
Allocations per call: 1 set (lower_words), a few regex match objects
```

The function has zero I/O, zero imports at call time, and no global state
mutation. It's safe to call from any thread.

## Cross-References

- [runtime-lifecycle.md](runtime-lifecycle.md) - Plan phase calls classify()
- [agent-escalation.md](agent-escalation.md) - AUTONOMOUS detection details
