# Google Gemini (GenAI) — Full Model Support

## Overview

Google Gemini models are fully accessible via an **OpenAI-compatible endpoint** at:

```
https://generativelanguage.googleapis.com/v1beta/openai/
```

This means Kognisant already works with Gemini models today — users just set the base URL and API key. However, there are Gemini-specific features we should explicitly support to provide a first-class experience.

---

## Current State (What Already Works)

Since Kognisant uses the OpenAI chat completions protocol, Gemini already works with:

```bash
$ kognisant setup
  Provider: Google Gemini
  API Base: https://generativelanguage.googleapis.com/v1beta/openai/
  API Key: <your Gemini API key from aistudio.google.com>
  Model: gemini-3.5-flash
```

**Working today without changes:**
- ✅ Chat completions (streaming + non-streaming)
- ✅ Function calling / tool use (OpenAI `tools` + `tool_choice` format)
- ✅ Multi-turn conversation
- ✅ System prompts
- ✅ Structured output (JSON mode)
- ✅ Model listing (`/v1/models`)

---

## What Needs Explicit Support

### 1. Thinking / Reasoning Tokens

Gemini 2.5+ and 3.x models support reasoning (thinking). The OpenAI-compatible endpoint exposes this via:

- `reasoning_effort` parameter: `"none"`, `"minimal"`, `"low"`, `"medium"`, `"high"`
- Or via `extra_body` with Gemini-native `thinking_config`

**Current behavior in Kognisant:** We send `"think": true` for Ollama protocol only. For OpenAI protocol, we don't send any thinking parameter.

**Fix needed:** When the model is detected as Gemini (base URL contains `generativelanguage.googleapis.com`), send `reasoning_effort` in the payload based on the classification:

```python
# In runtime.py _execute(), when building payload:
if "generativelanguage.googleapis.com" in api_base:
    if ctx.classification in ("COMPLEX", "AUTONOMOUS"):
        payload["reasoning_effort"] = "high"
    elif ctx.classification == "CONTEXT":
        payload["reasoning_effort"] = "medium"
    else:
        payload["reasoning_effort"] = "low"
```

**Thinking token display:** Gemini's OpenAI endpoint does NOT stream thinking tokens separately (unlike Ollama's `thinking` field). Instead, thinking happens internally and only the final content is streamed. The `reasoning_effort` just controls how much computation happens server-side. No change needed to the streaming parser.

**Thought summaries** (optional, via `include_thoughts: true` in `extra_body`): If enabled, Gemini returns thought summaries. This could integrate with our `/thinking` command. Lower priority.

### 2. Provider Auto-Detection During Setup

When a user enters their API key during `kognisant setup`, we should detect Gemini and pre-fill:

```python
# In setup wizard:
if api_key.startswith("AIza"):  # Gemini API keys start with AIza
    suggested_base = "https://generativelanguage.googleapis.com/v1beta/openai/"
    suggested_provider = "Google Gemini"
    suggested_model = "gemini-3.5-flash"
```

### 3. Available Gemini Models

As of June 2026, the key models to surface:

| Model | Context | Best For |
|-------|---------|----------|
| `gemini-3.5-flash` | Large | Fast, cheap, daily driver |
| `gemini-3.5-pro` | Large | Complex reasoning, coding |
| `gemini-2.5-flash` | 1M tokens | Long context, thinking |
| `gemini-2.5-pro` | 1M tokens | Best quality, always thinks |
| `gemini-2.0-flash` | 1M tokens | Legacy, still fast |

**Model listing:** The `/v1/models` endpoint works. We could auto-populate available models when the user adds a Gemini provider.

### 4. Image Understanding (Multimodal)

Gemini supports image input via base64 in the messages array:

```json
{
  "role": "user",
  "content": [
    {"type": "text", "text": "What's in this image?"},
    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}
  ]
}
```

**Relevance to Kognisant:** If we ever add image-aware tools (screenshot analysis, diagram understanding), Gemini handles it natively via the same OpenAI endpoint. No special code needed — the messages format already supports it.

### 5. Safety Settings

Gemini has configurable safety filters that can block responses. Via `extra_body`:

```python
payload["extra_body"] = {
    "google": {
        "safety_settings": [
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_ONLY_HIGH"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_ONLY_HIGH"},
        ]
    }
}
```

**Why this matters:** If a user's coding request triggers Gemini's safety filter (e.g., security-related code), the response may be blocked. We should detect this (response returns with `finish_reason: "safety"`) and inform the user rather than showing an empty response.

### 6. Context Caching

Gemini supports caching large contexts (like a full codebase) server-side to reduce cost and latency on subsequent requests. This is a future optimization but worth noting.

### 7. Rate Limits and Error Handling

Gemini returns standard HTTP error codes:
- `429` — Rate limited (with `Retry-After` header)
- `400` — Bad request (malformed tools, etc.)
- `403` — API key invalid or quota exceeded
- `500` — Server error

Our existing retry/backoff logic handles these. No changes needed.

---

## Implementation Plan

### Phase 1: First-Class Setup (quick win, ~2 hours)

- [ ] Add "Google Gemini" as a named provider in setup wizard
- [ ] Auto-detect Gemini API key format (`AIza*`) → suggest base URL + model
- [ ] Pre-fill `gemini-3.5-flash` as default model name
- [ ] Set capabilities: `{"tool_calling": true, "reasoning": true}`
- [ ] Add Gemini base URL to provider URL validation

### Phase 2: Reasoning Integration (~1 day)

- [ ] Detect Gemini provider by base URL in runtime
- [ ] Send `reasoning_effort` parameter based on message classification
- [ ] Handle `finish_reason: "safety"` in streaming parser → inform user
- [ ] Test with gemini-3.5-flash and gemini-2.5-pro

### Phase 3: Enhanced Features (optional, ~1 day)

- [ ] Auto-fetch available models from Gemini `/v1/models` endpoint on provider setup
- [ ] Thought summaries integration (via `include_thoughts`) → `/thinking` display
- [ ] Safety filter bypass option in model config (for power users doing security research)
- [ ] Context caching investigation (could reduce token costs for large projects)

---

## Configuration Example

After implementation, adding Gemini looks like:

```bash
$ kognisant setup

  Select a provider:
    1. Ollama (Local)
    2. Google Gemini        ← new
    3. OpenAI
    4. DeepSeek
    5. Groq
    6. Custom endpoint

  > 2

  Google Gemini Setup
  ─────────────────────
  Get your API key at: https://aistudio.google.com/apikey

  API Key: AIza...
  ✓ Key validated

  Available models:
    1. gemini-3.5-flash (recommended - fast, tool calling, reasoning)
    2. gemini-3.5-pro (best quality, always thinks)
    3. gemini-2.5-flash (1M context)
    4. gemini-2.5-pro (1M context, deep reasoning)

  > 1

  ✓ Model "gemini-3.5-flash" configured (Google Gemini)
  ✓ Set as default model
```

---

## What We Get for Free (No Code Changes)

Because Gemini uses the OpenAI-compatible endpoint:

- **Streaming** — works via our existing SSE parser
- **Tool calling** — works via our existing `tool_calls` delta parser + `tool_choice: "auto"` (just added)
- **Multi-turn** — standard messages array
- **Retry/backoff** — our existing error handling covers Gemini's HTTP codes
- **Circuit breaker** — works the same (tracks failures per model name)
- **Self-model reliability** — tracks Gemini reliability automatically
- **Token estimation** — works (response includes `usage` field)

---

## Gemini vs Other Providers (Internal Notes)

| Aspect | Gemini | OpenAI | Ollama |
|--------|--------|--------|--------|
| Protocol | OpenAI-compatible | Native OpenAI | Ollama native + OpenAI compat |
| Tool calling | ✅ (structured) | ✅ | ✅ |
| Streaming | ✅ SSE | ✅ SSE | JSON lines |
| Thinking tokens | Server-side (`reasoning_effort`) | Not exposed | `thinking` field in stream |
| Base URL | `generativelanguage.googleapis.com/v1beta/openai/` | `api.openai.com/v1` | `localhost:11434` |
| Auth | Bearer token (API key) | Bearer token | None (local) |
| Key format | `AIza*` | `sk-*` | N/A |
| Free tier | Yes (generous) | No | N/A (local) |

---

## Why Prioritize Gemini

1. **Free tier** — Gemini has a generous free tier. Perfect for users who don't want to run local models and don't want to pay for OpenAI.
2. **Tool calling works** — Verified via their docs. Uses the same format we already support.
3. **Reasoning built-in** — `reasoning_effort` is simple to pass and improves quality on complex tasks.
4. **1M token context** — Some models support 1M tokens. Huge for loading entire codebases.
5. **No new dependencies** — It's the same OpenAI protocol. Just a different URL.
6. **Growing market share** — Gemini is becoming a top choice for developers alongside Claude and GPT.

---

## Testing Checklist

Before shipping:

- [ ] `kognisant setup` → select Google Gemini → add key → verify model works
- [ ] Simple chat message → streamed response
- [ ] Tool call → `read_project_file` → response uses file content
- [ ] `/agent` task → PERP swarm with Gemini as planner + workers
- [ ] Long file context → verify no truncation issues
- [ ] Safety filter trigger → graceful error message (not empty response)
- [ ] Rate limit hit → proper retry with backoff
- [ ] Switch mid-session `/model` → Gemini → continues conversation
