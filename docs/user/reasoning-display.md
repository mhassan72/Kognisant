# Reasoning Display

Some models "think out loud" before producing their final answer. Kognisant captures these thinking tokens and displays them transparently so you can see exactly how the AI is working through your request.

---

## Why This Matters

Most AI tools hide the reasoning process. You get a final answer but no visibility into how the model arrived there. Kognisant shows you the thinking steps in real-time, which helps you:

- Verify the AI's logic before it acts
- Catch misunderstandings early (before code is written)
- Understand why a particular approach was chosen
- Build trust in the AI's decision-making
- Debug cases where the output seems wrong

---

## Which Models Support Reasoning

Reasoning (also called "thinking" or "extended thinking") is supported by models that produce structured thinking tokens before their response:

| Model | Provider | Reasoning |
|:---|:---|:---|
| gemma4 | Ollama | Yes |
| deepseek-r1 | Ollama / DeepSeek | Yes |
| qwen3 | Ollama | Yes |
| deepseek-chat | DeepSeek Cloud | Yes |
| gpt-4o | OpenAI | No (standard chain-of-thought) |
| llama-3.3-70b | Groq | Yes (model-dependent) |

The key differentiator: reasoning models produce a separate "thinking" block that Kognisant can detect and display independently from the final answer.

---

## How Thinking Tokens Are Displayed

When a reasoning model responds, you see numbered steps in gray text:

```
💭 Thinking...
  1. Read the current auth module to understand the structure.
  2. The bcrypt-based session system needs to be replaced with JWT.
  3. I'll need to update the middleware, login route, and tests.
  4. Start with middleware since everything depends on it.
💭 Thought for 12.4s
```

The thinking steps appear before the actual response. They show the model's internal reasoning process, numbered sequentially.

After thinking completes, the actual response follows (tool calls, code output, explanations).

---

## The /thinking Command

Review reasoning from past executions without scrolling through your terminal:

### View the most recent thinking

```
/thinking last
```

Displays the thinking steps from the last execution that produced reasoning.

### View thinking from N executions ago

```
/thinking 3
```

Shows thinking from 3 executions ago.

### List all stored thinking sessions

```
/thinking list
```

Shows a summary of all thinking records in the current session with timestamps and step counts:

```
Thinking Records:
  [1] 14:30:22 - 4 steps (12.4s) - "refactor authentication..."
  [2] 14:28:01 - 2 steps (3.1s) - "explain the config..."
  [3] 14:25:44 - 6 steps (18.7s) - "write tests for..."
```

---

## Thinking Storage

Thinking tokens are stored separately from chat history to keep them accessible without polluting the main conversation context.

**Location:** `.kognisant/history/session_*_thinking.json`

Each file contains:

```json
{
  "session_id": "session_20250615_143022",
  "entries": [
    {
      "timestamp": "2025-06-15T14:30:22Z",
      "duration_ms": 12400,
      "steps": [
        "Read the current auth module to understand the structure.",
        "The bcrypt-based session system needs to be replaced with JWT.",
        "I'll need to update the middleware, login route, and tests.",
        "Start with middleware since everything depends on it."
      ],
      "user_message_preview": "refactor authentication to use JWT"
    }
  ]
}
```

---

## Why Thinking Never Pollutes Chat Context

Thinking tokens are intentionally excluded from the conversation history that gets sent to the model on subsequent turns. Here is why:

1. **Token efficiency** - Thinking can be hundreds of tokens long. Including them in history would rapidly consume context window space.
2. **Clean context** - The model's next response should be informed by the final answer, not the intermediate reasoning that led there.
3. **Accurate behavior** - Reasoning steps are internal deliberation, not part of the conversation. Including them would confuse the model about what was actually said.

The separation means you can review thinking anytime via `/thinking` without it affecting response quality or context usage.

---

## How Reasoning Detection Works

Kognisant does not hardcode which models produce thinking tokens. Instead, it detects reasoning dynamically from the response format:

### Detection patterns

The system looks for structured thinking blocks in the model's response:

- Content wrapped in `<think>...</think>` tags (DeepSeek style)
- Separate `thinking` field in the response JSON (Gemma style)
- Content before a clear delimiter that separates reasoning from response

### Parsing

Once detected, the raw thinking text is parsed into discrete steps:

1. Split on sentence boundaries or numbered patterns
2. Remove XML/markdown formatting
3. Filter out empty or trivial steps
4. Number the remaining steps sequentially

### Graceful fallback

If a model that previously produced reasoning stops doing so (e.g., for a very short response), Kognisant simply does not show a thinking block. There is no error, the response just appears without the `💭` section.

---

## When Reasoning Appears

Not every response triggers reasoning. It depends on:

- **Model capability** - Only reasoning-capable models produce thinking tokens
- **Task complexity** - Models tend to reason more on complex tasks
- **Classification** - COMPLEX and AUTONOMOUS classifications are more likely to trigger extended thinking
- **Model discretion** - The model itself decides when to "think" vs. respond directly

For SIMPLE classifications with short responses, you typically will not see a thinking block even from reasoning-capable models. The model reserves extended thinking for tasks that benefit from deliberation.

---

## Configuration

Reasoning display is automatic and requires no configuration. If your model supports it, you will see it. If not, responses appear normally without thinking steps.

There are no settings to:
- Force reasoning on/off (it is model-determined)
- Change display format (it is always numbered gray steps)
- Adjust thinking timeout (it uses the standard execution timeout)

This design keeps the feature zero-config while providing full transparency when available.
