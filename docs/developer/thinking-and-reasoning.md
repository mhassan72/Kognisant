# Thinking and Reasoning Token Handling

How Kognisant receives, parses, displays, and stores reasoning tokens from
models that support "thinking out loud" (DeepSeek, Ollama with `think: true`,
QwQ, etc.)

## Protocol Differences

Different providers expose thinking tokens through different fields in the
streaming response. The network layer normalizes all of them to the same
`("thinking", text)` event type.

```
┌────────────────────────────────────────────────────────────────────────────┐
│                     Provider Thinking Token Formats                         │
├──────────────────┬─────────────────────────────────────────────────────────┤
│ Ollama           │ msg.thinking field in native JSON stream                │
│                  │ {"message": {"thinking": "...", "content": "..."}}      │
│                  │ Triggered by: payload["think"] = True                   │
├──────────────────┼─────────────────────────────────────────────────────────┤
│ DeepSeek (OpenAI)│ delta.reasoning_content in SSE stream                  │
│                  │ "delta": {"reasoning_content": "..."}                   │
│                  │ Triggered by: model default behavior                    │
├──────────────────┼─────────────────────────────────────────────────────────┤
│ Generic OpenAI   │ delta.thinking in SSE stream (some providers)           │
│                  │ "delta": {"thinking": "..."}                            │
│                  │ Triggered by: model default behavior                    │
└──────────────────┴─────────────────────────────────────────────────────────┘
```

### Network Layer Normalization

In `query_model_api_stream()`:

```python
# Ollama native
if "thinking" in msg and msg["thinking"]:
    thinking_parts.append(msg["thinking"])
    yield ("thinking", msg["thinking"])

# OpenAI SSE (DeepSeek variant)
if "reasoning_content" in delta and delta["reasoning_content"]:
    thinking_parts.append(delta["reasoning_content"])
    yield ("thinking", delta["reasoning_content"])

# OpenAI SSE (generic variant)
elif "thinking" in delta and delta["thinking"]:
    thinking_parts.append(delta["thinking"])
    yield ("thinking", delta["thinking"])
```

The consumer (runtime Execute phase) sees only `("thinking", text)` events
regardless of the provider. This decoupling means adding a new provider with
a different thinking field is a 2-line change in network.py.

## Step Parsing Strategy

Raw thinking text arrives as a stream of fragments. Once complete, it's parsed
into structured steps for storage and the `/thinking` command.

```python
def _parse_reasoning_steps(raw_thinking: str) -> list[str]:
```

### Priority Order

```
1. Numbered patterns (1. 2. 3.)
   - Best structure, model naturally produces numbered thoughts
   - Split by regex: r"^\s*(\d+)\.\s+" (multiline)
   - Requires >= 2 matches to trigger

2. Bullet patterns (- or *)
   - Second best, common in shorter reasoning
   - Split by regex: r"^\s*[-*]\s+" (multiline)
   - Requires >= 2 matches to trigger

3. Newline-separated blocks
   - Fallback for unstructured thinking
   - Split by "\n", filter empty lines
   - Requires >= 2 non-empty lines

4. Raw string as single element
   - Last resort (very short thinking, single paragraph)
   - Return [text] as a 1-element list
```

### Why This Priority

Models that reason well tend to naturally number their steps. Numbered parsing
gives the cleanest output. Bullets are the next most common format. Newline
splitting handles free-form thinking that doesn't use any markers. The single
element fallback prevents empty lists from being stored.

## Display in Terminal

Thinking tokens are displayed in real-time as they arrive, using dim gray text:

```
💭 Thinking...
  1. First I need to understand the current file structure
  2. The user wants to add a test for the auth module
  3. I should check if there's an existing test file
  4. Then create a new test following the project's pytest conventions
💭 Thought for 3.2s

Kognisant >
Here's the test file...
```

### Implementation

```python
# First thinking token arrives
if first_thinking:
    spinner.stop()                    # Kill the waiting spinner
    thinking_start_time = time.monotonic()
    sys.stdout.write(f"{_DIM}💭 Thinking...{_RESET}\n")

# Each thinking token
sys.stdout.write(f"{_DIM}{data}{_RESET}")
sys.stdout.flush()

# First content token arrives (end of thinking)
if thinking_parts:
    thinking_duration = time.monotonic() - thinking_start_time
    sys.stdout.write(f"\n{_DIM}💭 Thought for {thinking_duration:.1f}s{_RESET}\n\n")
```

### Color Code

```python
_DIM = "\033[2m"    # ANSI dim attribute - gray/muted text
_RESET = "\033[0m"
```

Dim text keeps thinking visually subordinate to the actual response. Users
can follow the reasoning without it competing with the answer for attention.

### Non-TTY Behavior

```python
if not is_tty:
    print("[THINKING]")
    sys.stdout.write(data)       # No ANSI codes
    print(f"\n[THOUGHT for {duration:.1f}s]\n")
```

Plain text markers for piped/logged output.

## Storage Design

Thinking is stored in a separate file per session, as a JSON array of entries.

### File Location

```
{history_dir}/session_20250612_103045_thinking.json
```

Derived from the session filename by replacing `.json` with `_thinking.json`.
Located in the same history directory as the session.

### Entry Format

```json
[
  {
    "turn": 3,
    "timestamp": "2025-06-12T10:31:15Z",
    "model": "gemma4:latest",
    "user_message": "add a test for the auth module...",
    "thinking_duration_ms": 3200,
    "reasoning": [
      "First I need to understand the current file structure",
      "The user wants to add a test for the auth module",
      "I should check if there's an existing test file",
      "Then create a new test following the project's pytest conventions"
    ]
  }
]
```

### Why Separate Files

- Session files are already large (full message history)
- Thinking data is optional (only models that support it)
- Separate files mean non-reasoning sessions have zero overhead
- The `/thinking` command can load just the thinking file without parsing
  the full session

### Why Array of Entries (Not JSONL)

Unlike telemetry, thinking entries are read as a complete set for the
`/thinking` command. JSON array allows:
- Direct `json.load()` to get all entries
- Easy slicing by turn number
- Append by load-modify-save (small file, typically <100KB)

### Write Implementation

```python
def _save_thinking(ctx, thinking_text, thinking_duration_ms):
    reasoning_steps = _parse_reasoning_steps(thinking_text)
    turn_number = len([m for m in ctx.messages if m.get("role") == "user"])

    entry = {
        "turn": turn_number,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "model": ctx.active_model.get("name", "unknown"),
        "user_message": ctx.user_message[:200],  # Truncated for storage
        "thinking_duration_ms": round(thinking_duration_ms),
        "reasoning": reasoning_steps,
    }

    # Load existing, append, write back
    entries = json.load(open(path)) if os.path.exists(path) else []
    entries.append(entry)
    json.dump(entries, open(path, "w"), indent=2)
```

Wrapped in try/except with `pass` - thinking storage never interrupts execution.

## /thinking Command Implementation

The `/thinking` command reads the session thinking file and displays entries.

### Display Format

```
/thinking

💭 Turn 3 (gemma4:latest, 3.2s):
  "add a test for the auth module"
  1. First I need to understand the current file structure
  2. The user wants to add a test for the auth module
  3. I should check if there's an existing test file
  4. Then create a new test following the project's pytest conventions

💭 Turn 7 (gemma4:latest, 1.8s):
  "now add error handling"
  1. The existing test doesn't handle the failure case
  2. I should add a test for invalid credentials
```

### /thinking list

Shows a compact summary of all reasoning entries in the session:

```
/thinking list

Turn 3: 4 steps, 3.2s (gemma4:latest)
Turn 7: 2 steps, 1.8s (gemma4:latest)
Turn 12: 6 steps, 5.1s (gemma4:latest)
```

## Dynamic Capability Detection

The runtime doesn't know in advance whether a model supports thinking.
Detection happens during execution:

```
Bootstrap reads persisted capabilities:
  model_reliability["gemma4:latest"].capabilities["reasoning"]
  - True: model proven to think (send think flag, expect tokens)
  - False: model proven NOT to think (don't send flag)
  - None/absent: unknown (send flag, observe what happens)

Execute phase:
  If reasoning_capable is None:
    Send think: true flag (Ollama) or let model default (OpenAI)
    If thinking tokens arrive:
      reasoning_capable = True  (detected!)
    If execution completes without thinking:
      reasoning_capable = False (detected!)

Post-execute:
  Persist the detected capability:
    model_reliability[name].capabilities["reasoning"] = reasoning_capable
```

### Why Not Just Always Send the Flag

- Some providers reject unknown parameters (400 error)
- Sending `think: true` to non-reasoning models wastes prompt tokens on the flag
- Known non-reasoning models skip the thinking display entirely (cleaner UX)

### First-Use Experience

On first use with a new model, `reasoning_capable` is None. The system
optimistically tries thinking (sends the flag) and observes. After one
execution, the capability is known and persisted forever.

## Timeout Interaction

When thinking tokens are flowing, the model is alive. The stream stall
detector (30s socket timeout) handles the case where thinking stops:

```
Normal stream:     thinking tokens -> 30s gap -> socket.timeout raised
Active thinking:   thinking tokens flow continuously -> no timeout possible
Very long think:   tokens every few seconds -> stall timer resets each time
```

The per-execution timeout (ctx.timeout) applies to the TOTAL response time.
But the stall detector is the important one: if tokens are flowing (thinking
OR content), the model is working. If 30s passes with zero bytes on the wire,
the connection is dead.

```python
# In network.py, after opening the stream:
response.fp._sock.settimeout(30.0)  # Socket-level read timeout
```

This means a model can think for 5 minutes (tokens flowing) without triggering
any timeout. But if it stops emitting tokens for 30s, it's declared stalled.

## Cross-References

- [runtime-lifecycle.md](runtime-lifecycle.md) - Execute phase handles thinking display
- [self-model-engine.md](self-model-engine.md) - Capability persistence
- [model-selection.md](model-selection.md) - Reasoning capability affects planner selection
