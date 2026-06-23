# LLM Uptime and Reasoning Process Display

## Problem Statement

Kognisant times out on local reasoning models (gemma4, deepseek-r1, qwen3) because:
1. The SIMPLE timeout (30s) is shorter than the model's thinking time (35s+)
2. The system reports "Stuck on: connecting" when the model is actually thinking
3. Thinking/reasoning tokens are never displayed to the user
4. A single timeout kills the request with no retry

The Ollama app handles this correctly because it has no timeout and streams
thinking tokens in real-time.

## Goals

- 99% uptime for local LLM connections (retry until we get a response)
- Display thinking/reasoning process from any model that provides it
- Dynamic detection of reasoning capabilities per model in the pool
- Never show "empty response" when the model is actively working
- Store reasoning steps as structured indexed arrays for telemetry
- Keep thinking out of chat context (zero token overhead in LLM payload)


## Design

### 1. Retry Strategy (3 attempts before failure)

Every execution gets up to 3 attempts before reporting failure to the user.

```
Attempt 1: Normal request (streaming, configured timeout)
  |
  v (timeout or empty response)
Attempt 2: Retry with extended timeout (2x original)
  |
  v (timeout or empty response)
Attempt 3: Retry non-streaming with maximum timeout (5 min for local, 2 min for remote)
  |
  v (still fails)
Report failure to user
```

Rules:
- Each attempt resets the spinner and shows "Retry 2/3..." or "Retry 3/3 (non-streaming)..."
- Retries only trigger for timeouts and empty responses (not for 401, 402, 429, etc.)
- Between retries: no delay for local models, 2s delay for remote (rate limiting)
- The retry loop lives inside _execute, invisible to the rest of the pipeline
- Ctrl+C during any attempt cancels the entire retry sequence immediately
- On retry, previous failed attempt output is cleared (no double error messages)


### 2. Timeout Strategy (adaptive per model type)

| Model Type | SIMPLE | CONTEXT | COMPLEX | Source |
|------------|--------|---------|---------|--------|
| Local (Ollama, llama.cpp) | 120s | 180s | 300s | Local models need loading + thinking time |
| Remote (OpenAI-compat) | 30s | 60s | 120s | Remote APIs are fast, pay-per-token |

Detection: `protocol == "ollama"` or `protocol == "llama_cpp"` marks a model as local.

Additionally:
- Once thinking or content tokens start arriving, disable the timeout entirely
  (model is alive and working, stall detection remains as safety net)
- The stall timeout (30s of zero data) remains regardless of model type
- Stall timer resets on ANY data: thinking tokens, content tokens, or keepalive
- Retry attempt 2 uses 2x the base timeout
- Retry attempt 3 (non-streaming) uses maximum: 300s local, 120s remote


### 3. Reasoning/Thinking Token Parsing

Different providers expose thinking differently:

| Protocol | Field Location | Example |
|----------|---------------|---------|
| Ollama native | `message.thinking` | `{"message": {"thinking": "Let me analyze...", "content": ""}}` |
| OpenAI-compat (DeepSeek, Qwen) | `delta.reasoning_content` | `{"choices": [{"delta": {"reasoning_content": "Step 1..."}}]}` |
| OpenAI-compat (generic) | `delta.thinking` | `{"choices": [{"delta": {"thinking": "..."}}]}` |

The network layer yields a new event type:
```python
("thinking", text)   # reasoning/thinking token fragment
```

This is yielded alongside content events. The runtime decides how to display it.

Thinking tokens reset the stall timer (they prove the model is alive).


### 4. Thinking Display in Terminal

When thinking tokens arrive, accumulate them and display as numbered reasoning steps:

```
💭 Thought for 34.7s
  1. Analyze the request: The user typed "hello".
  2. Determine context: There was no preceding command or specific topic, just a greeting.
  3. Formulate a response: A friendly, reciprocal greeting is appropriate.
  4. Self-Correction/Refinement: Keep it simple, welcoming, and helpful.

Kognisant >
Hello! How can I assist you today?
```

Display rules:
- Thinking text is printed in dim/gray color
- Header shows total thinking duration: `💭 Thought for {N}s`
- Each reasoning step is numbered and indented
- Steps are split by detecting: numbered patterns (1. 2. 3.), newlines, or sentence
  boundaries in the raw thinking output
- When content tokens start, print blank line + `Kognisant >` header, switch to normal color
- The spinner shows "thinking..." sub-state while thinking tokens stream
- Non-TTY mode: print as `[THINKING] 1. step text` without color

Thinking + tool calls interaction:
- Round 1: show thinking, then tool boxes
- Round 2+: show thinking again if the model reasons about tool results
- Each round's thinking is stored as a separate entry in the reasoning array


### 5. Thinking Storage (1 file per session)

Two files per session, side by side:

```
.kognisant/history/
  session_20260623_143022.json              # chat messages (no thinking in payload)
  session_20260623_143022_thinking.json     # reasoning log for the session
```

The thinking file structure:

```json
[
  {
    "turn": 1,
    "timestamp": "2026-06-23T14:30:25Z",
    "model": "gemma4:latest",
    "user_message": "hello",
    "thinking_duration_ms": 34700,
    "reasoning": [
      "Analyze the request: The user typed \"hello\".",
      "Determine context: There was no preceding command or specific topic, just a greeting.",
      "Formulate a response: A friendly, reciprocal greeting is appropriate.",
      "Self-Correction/Refinement: Keep it simple, welcoming, and helpful."
    ]
  },
  {
    "turn": 3,
    "timestamp": "2026-06-23T14:31:02Z",
    "model": "gemma4:latest",
    "user_message": "fix the bug in auth.py",
    "thinking_duration_ms": 45200,
    "reasoning": [
      "Read the file first to understand the current state.",
      "The import on line 5 references a module that was renamed in the last refactor.",
      "Replace 'from auth_utils import verify' with 'from auth.utils import verify'.",
      "Check if there are other references to the old module name."
    ]
  }
]
```

Chat history reference (in the session .json messages):
```json
{
  "role": "assistant",
  "content": "Hello! How can I assist you today?",
  "_thinking_turn": 1
}
```

Rules:
- Thinking is NEVER included in api_messages sent to the LLM (zero context overhead)
- The `_thinking_turn` reference on assistant messages links back to the thinking file
- Thinking file is append-only during the session (one entry per model response)
- Delete session = delete both files
- Thinking file is only read on demand: `/thinking` command, telemetry aggregation


### 6. Dynamic Reasoning Capability Detection

Rather than hardcoding which models support reasoning, detect it dynamically:

On first use of a model:
1. Include thinking/reasoning flags in the payload (`"think": true` for Ollama)
2. If the response includes thinking tokens, mark: `capabilities.reasoning = true`
3. If no thinking tokens arrive, mark: `capabilities.reasoning = false`
4. Store in SelfModel: `model_reliability[name].capabilities.reasoning`

Subsequent uses:
- `reasoning == true`: include thinking parameters in payload
- `reasoning == false`: skip thinking parameters (model doesn't support it)
- `reasoning == null` (first use): try with thinking enabled, update on result

Payload modifications per protocol:

Ollama:
```json
{"model": "gemma4", "messages": [...], "stream": true, "think": true}
```

OpenAI-compat (DeepSeek-style):
```json
{"model": "deepseek-r1", "messages": [...], "stream": true}
```
(DeepSeek/Qwen emit reasoning_content automatically, no flag needed)

Safety: If sending `"think": true` to a non-reasoning Ollama model causes an error,
catch it and mark `capabilities.reasoning = false`. Retry without the flag.

Remote model reasoning costs:
- Reasoning is only auto-enabled for local models on first detection
- For remote models, reasoning is enabled only if the model already emits it
  without explicit flags (DeepSeek, Qwen do this naturally)
- We never add `reasoning_effort` or similar cost-increasing flags to remote models
  unless the user explicitly configures it


### 7. Connection Health Check (pre-flight for local models)

Before the first real request to a local model in a session, do a lightweight
health check:

```python
# Ollama: GET http://localhost:11434/api/tags (list models) - responds in <500ms
# llama.cpp: GET http://localhost:8080/health - responds in <100ms
```

This fires once per session start (in Bootstrap). If it fails:
- Print: "Ollama is not running. Start it with: ollama serve"
- Do NOT attempt the real request (saves 120s of waiting)
- Skip to alternatives if available in the pool

Rules:
- Health check timeout: 2s (if it takes longer, the service is likely down)
- Only runs on first request of the session, not every message
- Result cached: `_local_service_verified = True` for the session lifetime
- If the health check passes but the model request later fails, don't re-check
  (the service is up, it's a model-level issue)


### 8. Model Loading Awareness (Ollama-specific)

Ollama loads models into memory on first request. This can take 10-60s for large
models. During loading, the HTTP connection is held open but no tokens arrive.

Spinner states for local models:
```
⚙️  gemma4:latest - loading model... 8s        (connected, no data yet, <15s)
⚙️  gemma4:latest - thinking... 25s            (thinking tokens arriving)
⚙️  gemma4:latest - streaming... 37s           (content tokens arriving)
```

Detection logic:
- Connected (HTTP 200) + no data for first 2s on local model = "loading model..."
- First thinking token arrives = switch to "thinking..."
- First content token arrives = switch to "streaming...", stop spinner, print response

Track in SelfModel: `model_reliability[name].last_used_at` timestamp.
If last_used_at was <5 min ago, skip "loading model..." state (model is likely hot).


### 9. Valence Calculation with Thinking

When a model thinks for 35s then responds successfully:
- This is a SUCCESS, not a timeout
- Valence delta: use the content response time (time from first content token to done)
  NOT the total wall clock time
- Fast response after thinking (<10s of actual streaming): +5
- The 35s of thinking doesn't penalize valence

When a model thinks but never produces content (timeout after thinking):
- This IS a failure: valence -15
- But it should be rare since we disable timeout once thinking starts


### 10. Reasoning Step Parsing Strategy

The raw thinking output from models varies in format. Parse it with this priority:

1. **Numbered patterns detected** (`1.`, `2.`, `3.` or `- `, `* `):
   Split by the pattern, each becomes an array entry. Display numbered.

2. **Newline-separated blocks** (multiple lines/paragraphs):
   Split by newlines, filter empties, each becomes an array entry.

3. **Single string (no structure)**:
   Store as-is in a single-element array: `["Full thinking text..."]`.
   Display as one block without numbering.

The parser never forces artificial splits. If the model gives clean steps, show
them numbered. If it gives a blob, dump the blob. All three cases produce a valid
`reasoning` array for storage.


### 11. /thinking Slash Command

```
/thinking          Show reasoning for the last turn
/thinking N        Show reasoning for turn N
/thinking list     Show summary: turn numbers + first 50 chars of first step
```

Examples:

```
You > /thinking
💭 Turn 5 - Thought for 34.7s (gemma4:latest)
  1. Analyze the request: The user typed "hello".
  2. Determine context: No preceding command, just a greeting.
  3. Formulate a response: A friendly greeting is appropriate.
  4. Self-Correction: Keep it simple and welcoming.

You > /thinking list
💭 Session reasoning history:
  Turn 1: "Analyze the request: The user typed \"hello\"..."
  Turn 3: "Read the file first to understand the current..."
  Turn 5: "The user wants to know about the project str..."
```

Rules:
- If no thinking file exists for the session, print: "No reasoning data for this session."
- If the turn has no thinking entry (non-reasoning model), print: "No reasoning recorded for turn N."
- Loads from the `session_*_thinking.json` file on demand (never kept in memory)

1. Network layer: parse thinking tokens from Ollama (`message.thinking`) and
   OpenAI-compat (`delta.reasoning_content`, `delta.thinking`)
2. Network layer: yield `("thinking", text)` events, reset stall timer on thinking
3. Runtime: accumulate thinking tokens, split into reasoning steps
4. Runtime: display thinking in terminal (gray, numbered steps)
5. Runtime: retry loop (3 attempts) for timeouts and empty responses
6. Runtime: adaptive timeouts (local vs remote detection)
7. Runtime: spinner sub-states (loading model / thinking / streaming)
8. Runtime: save thinking to session_*_thinking.json file
9. Bootstrap: local model health check (pre-flight)
10. SelfModel: persist reasoning capability detection
11. Plan phase: include `think: true` flag for reasoning-capable local models
12. Valence: separate thinking time from response time in calculations
13. Tests: mock thinking streams, verify display, verify retries, verify storage


## Success Criteria

- gemma4 "hello" works first time without timeout (thinking displayed in real-time)
- Thinking steps render as numbered list in terminal
- Thinking stored in session file, never pollutes chat context
- If Ollama isn't running, user sees clear message within 2s (not 120s timeout)
- 3 retries ensure transient failures don't surface to user
- No regressions on remote API models (they keep their fast timeouts)
- Remote models never get surprise cost-increasing reasoning flags
- Ctrl+C cancels immediately regardless of retry state
- Valence correctly reflects success even after 35s of thinking


## Implementation Order

1. Network layer: parse thinking tokens from Ollama (`message.thinking`) and
   OpenAI-compat (`delta.reasoning_content`, `delta.thinking`)
2. Network layer: yield `("thinking", text)` events, reset stall timer on thinking
3. Runtime: accumulate thinking tokens, split into reasoning steps (priority: numbered > newlines > raw string)
4. Runtime: display thinking in terminal (gray, numbered if structured, raw if not)
5. Runtime: retry loop (3 attempts) for timeouts and empty responses
6. Runtime: adaptive timeouts (local vs remote detection)
7. Runtime: spinner sub-states (loading model / thinking / streaming)
8. Runtime: save thinking to session_*_thinking.json file
9. Runtime: /thinking slash command (last, N, list)
10. Bootstrap: local model health check (pre-flight)
11. SelfModel: persist reasoning capability detection
12. Plan phase: include `think: true` flag for reasoning-capable local models
13. Valence: separate thinking time from response time in calculations
14. Tests: mock thinking streams, verify display, verify retries, verify storage


## Success Criteria

- gemma4 "hello" works first time without timeout (thinking displayed in real-time)
- Thinking steps render as numbered list when structured, raw dump when not
- /thinking command shows last turn, /thinking N shows specific turn, /thinking list shows summary
- Thinking stored in session file, never pollutes chat context
- If Ollama isn't running, user sees clear message within 2s (not 120s timeout)
- 3 retries ensure transient failures don't surface to user
- No regressions on remote API models (they keep their fast timeouts)
- Remote models never get surprise cost-increasing reasoning flags
- Ctrl+C cancels immediately regardless of retry state
- Valence correctly reflects success even after 35s of thinking
