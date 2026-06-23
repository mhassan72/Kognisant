# Models and Providers

Kognisant is model-agnostic. You can use any LLM from any provider, switch between them mid-session, and even let the system auto-switch when one model becomes unreliable. This guide covers the full model management system.

---

## Why Multi-Model Support Matters

Different models excel at different tasks. A small local model handles quick questions instantly, while a large cloud model tackles complex refactoring. Kognisant lets you keep multiple models configured and switch based on what you need right now, without restarting or reconfiguring anything.

---

## Supported Providers

| Provider | Type | Protocol | Notes |
|:---|:---|:---|:---|
| Ollama | Local | `openai` or `ollama` | Free, private, GPU-accelerated |
| llama.cpp | Local | `llama_cpp` or `openai` | Raw GGUF model server |
| OpenAI | Cloud | `openai` | GPT-4o, GPT-4o-mini |
| DeepSeek | Cloud | `openai` | Affordable, reasoning-capable |
| Groq | Cloud | `openai` | Ultra-fast inference |
| NVidia NIM | Cloud | `openai` | Enterprise GPU inference |
| Kimi (Moonshot) | Cloud | `openai` | Long-context specialist |
| Nebius | Cloud | `openai` | European cloud inference |
| Any OpenAI-compatible | Cloud/Local | `openai` | Any server with `/v1/chat/completions` |

All cloud providers use the OpenAI-compatible protocol, meaning any server that exposes `/v1/chat/completions` works out of the box.

---

## Adding Models

### Method 1: Setup wizard (recommended for first-time)

```bash
kognisant setup
```

This launches an interactive menu where you pick a provider, enter credentials, and Kognisant tests the connection.

### Method 2: In-session with /model

While chatting, type:

```
/model
```

You will see your current model pool plus an option to add new ones:

```
📦 Select an AI Model:

  [1] gemma3:1b (Ollama) 🟢
  [2] llama-3.3-70b-versatile (Groq) 🟢 [Active]
  [3] gpt-4o-mini (OpenAI) 🟡
  [a] Add custom provider / model
```

Press `a` to add a new provider. You choose from templates (just pick a provider and enter your key), or configure a fully custom endpoint.

### Method 3: Edit models_pool.json directly

The model pool is stored at `~/.kognisant_core/models_pool.json`. You can edit it with any text editor:

```bash
vim ~/.kognisant_core/models_pool.json
```

---

## The models_pool.json Structure

The file uses a nested structure grouping models by provider:

```json
{
  "selected_models": [
    {
      "provider": "Ollama (Local)",
      "api_key": "",
      "models": [
        {
          "vendor": "Google",
          "name": "gemma3:1b",
          "model_id": "gemma3:1b",
          "api_base_url": "http://localhost:11434/v1",
          "context_window": 131072,
          "modality": "text-to-text",
          "capabilities": {
            "tool_calling": true,
            "reasoning": true
          }
        }
      ]
    },
    {
      "provider": "Groq",
      "api_key": "gsk_your_key_here",
      "models": [
        {
          "vendor": "Groq",
          "name": "llama-3.3-70b-versatile",
          "model_id": "llama-3.3-70b-versatile",
          "api_base_url": "https://api.groq.com/openai/v1",
          "pricing": {
            "input_per_1m_tokens_usd": 0.59,
            "output_per_1m_tokens_usd": 0.79
          },
          "context_window": 128000,
          "modality": "text-to-text",
          "capabilities": {
            "tool_calling": true,
            "reasoning": true
          }
        }
      ]
    }
  ]
}
```

### Key fields per model:

| Field | Required | Description |
|:---|:---|:---|
| `name` | Yes | Display name shown in model selection |
| `model_id` | Yes | The actual model identifier sent to the API |
| `api_base_url` | Yes | Base URL for the API endpoint |
| `context_window` | No | Max tokens the model accepts |
| `capabilities.tool_calling` | No | Whether the model can call tools |
| `capabilities.reasoning` | No | Whether thinking tokens are available |
| `pricing` | No | Token pricing for cost tracking |
| `protocol` | No | `openai`, `ollama`, or `llama_cpp` (defaults to `openai`) |

---

## Switching Models Mid-Session

Inside a chat session:

```
/model
```

Select a different model by number. The switch is instant, no restart needed. Your conversation history carries over. The new model picks up exactly where the old one left off.

This is useful when:
- A small model cannot handle a complex request
- You want to compare outputs between models
- Your local model is slow and you need a quick answer from the cloud
- A model's circuit breaker trips and you need to manually switch

---

## Removing Models

Inside a chat session:

```
/model
```

Select `r` to remove a model from your pool:

```
📦 Kognisant Model Pool Wizard:

  [1] gemma4:latest (Ollama) 🟢 [Active]
  [2] Kimi-K2.6 (Nebius Cloud) 🟢
  [3] GPT OSS 120b (Groq) 🟢
  [a] Add custom provider / model
  [r] Remove a model from pool
  [Enter] Cancel and resume chat

👉 Enter selection: r

🗑️  Remove a model:

  [1] gemma4:latest (Ollama) [Active - cannot remove]
  [2] Kimi-K2.6 (Nebius Cloud)
  [3] GPT OSS 120b (Groq)
  [Enter] Cancel

👉 Enter number to remove: 2
Remove 'Kimi-K2.6' (Nebius Cloud)? [y/N]: y
✅ 'Kimi-K2.6' removed from model pool.
```

Rules:
- You cannot remove the currently active model. Switch to another one first.
- Removal is permanent (the model is deleted from `models_pool.json`).
- You can always re-add a model later using the `[a]` add option.

---

## Local vs. Cloud Model Differences

### Timeouts

Local models (Ollama, llama.cpp) get extended timeouts because they may need time to load into GPU memory:

| Classification | Local Timeout | Cloud Timeout |
|:---|:---|:---|
| SIMPLE | 120s | 30s |
| CONTEXT | 180s | 60s |
| COMPLEX | 300s | 120s |

### Reasoning support

Local models that support reasoning (gemma4, deepseek-r1 variants, qwen3) display thinking tokens just like cloud models. The detection is dynamic based on the model's response format.

### Health checks

On startup, Kognisant pings local models to verify they are reachable. If Ollama is not running or the model is not loaded, you get an immediate error rather than a timeout during your first message:

```
⚡ ⚠️  Ollama server not reachable at http://localhost:11434
```

Cloud models skip the health check and instead validate that the API key is set.

---

## Capability Detection

Kognisant tracks two key capabilities per model:

### Tool Calling

If a model supports function calling (most modern models do), Kognisant sends tool definitions alongside your message for COMPLEX tasks. This enables file operations, web browsing, shell commands, and custom tools.

If a model does not support tool calling, Kognisant gracefully downgrades to text-only mode. It will notify you:

```
⚠️  This model does not support tool calling. Responses will be text-only.
```

### Reasoning

Models with reasoning capability (gemma4, deepseek-r1, qwen3) produce "thinking" tokens before their actual response. Kognisant detects this dynamically from the response format and displays the thinking steps in gray with numbered headings.

If tool_calling was previously disabled by self-healing (e.g., the model repeatedly failed at tool calls), that preference is persisted and respected in future sessions.

---

## Circuit Breakers and Auto-Switching

Kognisant tracks per-model reliability using Bayesian scoring and protects you from persistent failures with circuit breakers.

### How circuit breakers work:

1. **CLOSED** (normal) - The model is healthy. All requests go through.
2. **OPEN** (tripped) - 5 failures within 30 seconds. The model is temporarily blocked.
3. **HALF_OPEN** (testing) - After a 30-second cooldown, one test request is allowed through.

When a circuit breaker trips, Kognisant automatically switches to the next most reliable model in your pool:

```
⚡ Switching → llama-3.3-70b-versatile
  ⚠️  gemma3:1b circuit breaker OPEN; using llama-3.3-70b-versatile (reliability: 0.89)
```

If the test request in HALF_OPEN state succeeds, the breaker returns to CLOSED. If it fails, it goes back to OPEN for another 30-second cooldown.

### When no alternative is available:

If all configured models have tripped circuit breakers, Kognisant falls back to the default model with a warning:

```
⚠️  gemma3:1b circuit breaker OPEN but no reliable alternative available
```

---

## Model Reliability Tracking

Every execution updates per-model statistics stored in `~/.kognisant_core/self_model.json`:

- **Successes and failures** - Raw counts
- **Bayesian reliability** - `(successes + 1) / (successes + failures + 2)` (starts at 0.5, moves toward truth)
- **Average response time** - Rolling average with 0.8/0.2 weighting
- **Token calibration** - How accurate your token estimates are vs. actual usage
- **Last success/failure timestamps** - For recency tracking

### Viewing reliability data:

```
/telemetry gemma3:1b
```

Shows per-model deep dive with success rate, average response time, and reliability score.

---

## Token Calibration

Different models tokenize text differently. A message that is 500 tokens for GPT-4o might be 600 tokens for Gemma. Kognisant tracks a per-model correction factor:

```
calibration = calibration * 0.8 + (actual/estimated) * 0.2
```

Over time, this means token estimates shown in the `📋` line become more accurate for each model you use regularly.

---

## Valence and Model Interaction

The system valence (mood score from -100 to +100) is affected by model performance:

| Outcome | Valence Change |
|:---|:---|
| Success, fast (<10s) | +5 |
| Success, moderate (10-30s) | +3 |
| Success, slow (>30s) | +1 |
| Timeout | -15 |
| Empty response | -10 |
| Error | -10 |
| User cancelled (Ctrl+C) | -5 |

When valence drops low, you will see it in the `⚡` line:

```
⚡ gemma3:1b | valence: -22 | 3 skills, 4 tools
```

This is a signal that the model has been underperforming. Consider switching to a different one.

---

## Adding a Custom OpenAI-Compatible Endpoint

Any server that exposes the OpenAI chat completions API works. Add it to your `models_pool.json`:

```json
{
  "provider": "My Custom Server",
  "api_key": "optional-key",
  "models": [
    {
      "vendor": "Custom",
      "name": "my-finetuned-model",
      "model_id": "my-model-v2",
      "api_base_url": "https://my-server.com/v1",
      "context_window": 32000,
      "modality": "text-to-text",
      "capabilities": {
        "tool_calling": true,
        "reasoning": false
      }
    }
  ]
}
```

Or use `/model` in chat, press `a`, and choose "Custom endpoint" from the templates.
