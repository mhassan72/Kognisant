# CLI Integration — Model Selection & Execution Pipeline

This document describes how the Kognisant CLI consumes the Inference API for model selection, execution, and graceful degradation. Authentication is covered separately in [authentication.md](./authentication.md).

---

## Overview

The CLI treats Kognisant Cloud as the primary inference backend. When a user is logged in and has funds, all inference routes through `https://inference.kognisant.xyz/v1/`. External and local models serve as fallbacks.

```
┌─────────────────────────────────────────────────────────────────┐
│                     Model Selection Hierarchy                     │
├─────────────────────────────────────────────────────────────────┤
│  Tier 1: Kognisant Cloud   (logged in + balance > 0)            │
│  Tier 2: External models   (OpenAI, Anthropic, user-configured) │
│  Tier 3: Local models      (Ollama, llama.cpp)                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 1. Fetching Available Models

### Endpoint

```
GET https://inference.kognisant.xyz/v1/models
```

This endpoint is public — no authentication required for listing models. Users can browse the catalog before logging in.

### Response Shape

```json
{
  "object": "list",
  "data": [
    {
      "id": "MiniMaxAI/MiniMax-M3",
      "object": "model",
      "created": 1784600000,
      "owned_by": "minimax",
      "description": "MiniMax-M3 is a 428B MoE reasoning model...",
      "pricing": {
        "input_per_million": 0.30,
        "output_per_million": 1.20
      },
      "throughput_tps": 190,
      "capabilities": {
        "modality": "text",
        "context_window": 1049000,
        "quantization": "FP8",
        "tool_calling": true,
        "reasoning": true,
        "responses_api": false
      }
    }
  ]
}
```

### CLI Implementation

```python
def _fetch_kognisant_models() -> list[dict]:
    """Fetch models from Kognisant Cloud. Returns [] on failure.

    Endpoint is public — no auth required for model listing.
    """
    cache = _load_model_cache()
    if cache and cache["expires_at"] > time.time():
        return cache["models"]

    try:
        url = "https://inference.kognisant.xyz/v1/models"
        req = urllib.request.Request(url, method="GET")
        ctx = ssl._create_unverified_context()
        with urllib.request.urlopen(req, timeout=5.0, context=ctx) as resp:
            data = json.loads(resp.read().decode())
        models = data.get("data", [])
        _save_model_cache(models, ttl=3600)
        return models
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError):
        # Fallback to cached data if API unreachable (even expired cache)
        return cache["models"] if cache else []
```

### Caching Strategy

| Layer | TTL | Location |
|-------|-----|----------|
| In-memory | Session lifetime | Process memory |
| On-disk | 1 hour | `~/.kognisant_core/cloud_models_cache.json` |

If the API is unreachable, the CLI falls back to the on-disk cache (even if expired) to avoid blocking the user.

---

## 2. Building the Compiled Model List

`get_compiled_models()` assembles the tiered model list. Kognisant Cloud models are injected first.

```python
def get_compiled_models() -> list[dict]:
    compiled_models = []

    # ── Tier 1: Kognisant Cloud ──────────────────────────────────
    if is_logged_in():
        cloud_models = _fetch_kognisant_models()
        for m in cloud_models:
            # Skip embedding models — CLI uses chat models only
            if m["capabilities"]["modality"] == "embedding":
                continue
            compiled_models.append({
                "name": m["id"],
                "display_name": m["id"].split("/")[-1],
                "provider": "Kognisant Cloud",
                "protocol": "openai",
                "api_base_url": "https://inference.kognisant.xyz/v1/",
                "api_key": "",  # Token injected at request time
                "capabilities": m["capabilities"],
                "_kognisant_hosted": True,
                "_pricing": m.get("pricing"),
            })

    # ── Tier 2: User-configured external models ──────────────────
    for entry in selected_models_from_pool():
        compiled_models.append(entry)

    # ── Tier 3: Local models (Ollama auto-discovery) ─────────────
    compiled_models.extend(discover_ollama_models())

    return compiled_models
```

### Key Fields on a Compiled Model Entry

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Full model ID used in API requests (e.g., `MiniMaxAI/MiniMax-M3`) |
| `display_name` | string | Short name for UI display (e.g., `MiniMax-M3`) |
| `provider` | string | `"Kognisant Cloud"`, `"OpenAI"`, `"Ollama"`, etc. |
| `protocol` | string | Always `"openai"` for Kognisant Cloud |
| `api_base_url` | string | Base URL for inference calls |
| `api_key` | string | Empty for cloud (token injected dynamically) |
| `capabilities` | object | Mirrors the API model capabilities object |
| `_kognisant_hosted` | bool | `True` for cloud models, absent otherwise |
| `_pricing` | object | `{ input_per_million, output_per_million }` |

---

## 3. Default Model Selection

The CLI picks a default model using this priority:

```python
def get_default_model(compiled_models: list[dict]) -> dict:
    # 1. Explicit user override (sticky setting)
    explicit = load_explicit_default()
    if explicit:
        match = find_model(compiled_models, explicit)
        if match:
            return match

    # 2. Best Kognisant Cloud model (if logged in)
    cloud_models = [m for m in compiled_models if m.get("_kognisant_hosted")]
    if cloud_models:
        # Prefer: reasoning + tool_calling, then sort by context_window as tiebreaker
        best = [m for m in cloud_models
                if m["capabilities"].get("reasoning")
                and m["capabilities"].get("tool_calling")]
        if best:
            return max(best, key=lambda m: m["capabilities"].get("context_window", 0))
        return cloud_models[0]

    # 3. First available external/local model
    return compiled_models[0] if compiled_models else None
```

The user can override at any time via:
- `/model` command in chat — interactive picker
- `set_default_model("model-id")` — sticky preference stored in `models_pool.json`
- `kognisant setup` — configure external providers

If a user explicitly sets an external model as default, the cloud-first behavior is bypassed. The explicit choice always wins.

---

## 4. Making Inference Requests

### Token Injection

The CLI injects the Firebase ID token at request time for Kognisant Cloud models. No static API key is stored.

```python
def _build_headers(model: dict) -> dict:
    headers = {"Content-Type": "application/json"}

    if model.get("api_key"):
        # External provider with static key
        headers["Authorization"] = f"Bearer {model['api_key']}"
    elif "inference.kognisant.xyz" in model.get("api_base_url", ""):
        # Kognisant Cloud — inject fresh Firebase ID token
        from .auth import get_id_token
        token = get_id_token()
        if not token:
            raise KognisantAuthError(
                "Session expired. Run 'kognisant login' to re-authenticate."
            )
        headers["Authorization"] = f"Bearer {token}"

    return headers
```

### Chat Completion (Non-Streaming)

```python
def query_model_api_raw(api_base_url, api_key, payload, protocol="openai"):
    url = api_base_url.rstrip("/")
    if not url.endswith("/chat/completions"):
        url = f"{url}/chat/completions"

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    elif "inference.kognisant.xyz" in api_base_url:
        from .auth import get_id_token
        token = get_id_token()
        if not token:
            raise KognisantAPIError(
                "Session expired. Run 'kognisant login' to re-authenticate."
            )
        headers["Authorization"] = f"Bearer {token}"

    req_body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=req_body, headers=headers, method="POST")
    ctx = ssl._create_unverified_context()

    # Retry with backoff...
    max_retries = 3
    backoff = 1.0

    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=120.0, context=ctx) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            _handle_http_error(e, api_base_url, attempt, max_retries)
            time.sleep(backoff)
            backoff *= 2.0
            continue
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < max_retries - 1:
                time.sleep(backoff)
                backoff *= 2.0
                continue
            raise KognisantAPIError(f"Network Connection Failed: {e}")
```

### Chat Completion (Streaming)

```python
def query_model_api_stream(api_base_url, api_key, payload, protocol="openai", timeout=120.0):
    url = api_base_url.rstrip("/")
    if not url.endswith("/chat/completions"):
        url = f"{url}/chat/completions"

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    elif "inference.kognisant.xyz" in api_base_url:
        from .auth import get_id_token
        token = get_id_token()
        if not token:
            raise KognisantAPIError(
                "Session expired. Run 'kognisant login' to re-authenticate."
            )
        headers["Authorization"] = f"Bearer {token}"

    payload = dict(payload)
    payload["stream"] = True

    req_body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=req_body, headers=headers, method="POST")
    ctx = ssl._create_unverified_context()

    response = urllib.request.urlopen(req, timeout=timeout, context=ctx)
    # ... SSE parsing continues as existing implementation ...
```

### SSE Chunk Shape (from API)

Each streamed chunk follows the OpenAI-compatible format:

```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion.chunk",
  "created": 1700000000,
  "model": "MiniMaxAI/MiniMax-M3",
  "choices": [{
    "index": 0,
    "delta": { "content": "Hello" },
    "finish_reason": null
  }]
}
```

- First chunk includes `"delta": { "role": "assistant" }`
- Content chunks include `"delta": { "content": "..." }`
- Final chunk includes `"finish_reason": "stop"` and may contain `"usage": {...}`
- Stream terminates with `data: [DONE]`

---

## 5. Error Handling & Fallback Logic

### API Error Shape

All errors from the inference API follow this structure:

```json
{
  "error": {
    "message": "Human-readable description",
    "type": "error_category",
    "code": null
  }
}
```

### Status Code Handling

| Status | Type | CLI Behavior |
|--------|------|--------------|
| 400 | `invalid_request_error` | Surface to user. Do not retry. |
| 401 | `authentication_error` | Force-refresh token, retry once. If still 401 → prompt `kognisant login`. Mark provider unreachable. |
| 402 | `insufficient_balance` | Mark **entire Kognisant Cloud provider** as session-unreachable. Fall back to Tier 2/3. Notify user with billing link. |
| 404 | `not_found_error` | Mark **that specific model** as unreachable. Pick next available cloud model. |
| 429 | `rate_limit_error` | Retry with backoff. After 3 failures → mark **that model** unreachable, try next cloud model. |
| 500 | `server_error` | Retry with backoff (1s → 2s → 4s). After 3 failures → mark **that model** unreachable, try next cloud model. |
| 502 | `upstream_error` | Same as 500. |
| Network timeout | — | Same as 500. |

**Key distinction:** 401 and 402 are account-level problems (mark the whole provider). 404, 429, 500, 502 are model-level problems (mark only that model, try another cloud model first).

### Error Handler Implementation

```python
def _handle_http_error(error, api_base_url, model_name, attempt, max_retries):
    """Handle HTTP errors from the inference API.

    Key distinction:
      - 401, 402 → provider-level (all cloud models affected)
      - 404, 429, 500, 502 → model-level (only this model marked, try others)
    """
    is_kognisant = "inference.kognisant.xyz" in api_base_url

    try:
        body = json.loads(error.read().decode("utf-8"))
        message = body["error"]["message"]
        error_type = body["error"]["type"]
    except (json.JSONDecodeError, KeyError):
        message = f"HTTP {error.code}"
        error_type = "unknown_error"

    # ── Account-level failures (mark entire provider) ──────────
    if error.code == 401 and is_kognisant:
        # Force token refresh, retry once
        from .auth import get_id_token
        token = get_id_token(force_refresh=True)
        if token and attempt == 0:
            return  # Will retry with fresh token
        _mark_provider_unreachable("Kognisant Cloud", "authentication failed")
        raise KognisantAPIError(
            "Authentication failed. Run 'kognisant login' to re-authenticate.\n"
            "  If the problem persists, contact support@kognisant.xyz"
        )

    if error.code == 402 and is_kognisant:
        _mark_provider_unreachable("Kognisant Cloud", "insufficient balance")
        print(f"  ⚠️  Kognisant Cloud unavailable (insufficient balance). Using external models.")
        print(f"      Top up at: kognisant.xyz/console/billing")
        raise ProviderUnreachableError("Kognisant Cloud", "insufficient balance")

    # ── Model-level failures (mark only this model) ────────────
    if error.code == 404 and is_kognisant:
        _mark_model_unreachable(model_name)
        raise ModelNotFoundError(f"Model not found: {message}")

    if error.code in (429, 500, 502, 503) and attempt < max_retries - 1:
        return  # Will retry with backoff

    if error.code in (429, 500, 502, 503) and is_kognisant:
        # Retries exhausted — mark THIS MODEL unreachable, not the provider
        _mark_model_unreachable(model_name)
        raise ModelUnreachableError(model_name, message)

    raise KognisantAPIError(f"API Error {error.code}: {message}")
```

---

## 6. Model & Provider Unreachable Tracking

Failures are tracked at two levels:

1. **Model-level** — a specific model is down (500, 502, timeout). Other cloud models may still work.
2. **Provider-level** — the entire provider is unusable (402 no balance, 401 auth expired). All models from that provider are skipped.

```python
_session_unreachable_models: set[str] = set()
_session_unreachable_providers: set[str] = set()


def _mark_model_unreachable(model_name: str):
    """Mark a specific model as unreachable (server error, overloaded)."""
    _session_unreachable_models.add(model_name)


def _mark_provider_unreachable(provider: str, reason: str):
    """Mark all models from a provider as unreachable (billing, auth)."""
    _session_unreachable_providers.add(provider)


def _is_model_available(model: dict) -> bool:
    """Check if a model can be used right now."""
    if model.get("provider") in _session_unreachable_providers:
        return False
    if model.get("name") in _session_unreachable_models:
        return False
    return True
```

**Important:** Filtering happens at model selection time, not at compilation time. `get_compiled_models()` always returns the full list. Selection functions (`get_default_model`, `get_best_models_pool`) filter by availability:

```python
def get_default_model(compiled_models: list[dict]) -> dict:
    # Filter unavailable models at selection time
    available = [m for m in compiled_models if _is_model_available(m)]
    # ... selection logic on `available` ...
```

This ensures that if a model or provider becomes unreachable mid-session, subsequent selections reflect it without rebuilding the compiled list.

---

## 7. Bootstrap Awareness

During `_bootstrap()`, the runtime checks whether its active model is still viable:

```python
def _bootstrap(ctx):
    # If active model is cloud-hosted and provider became unreachable
    if (ctx.active_model.get("_kognisant_hosted")
            and not _is_provider_reachable("Kognisant Cloud")):
        ctx.auto_switched = True
        ctx.switch_reason = "Kognisant Cloud unavailable"
        ctx.active_model = _pick_best_external(compiled_models)

    # Continue with existing bootstrap logic...
    ctx.self_model = SelfModelEngine.load()
    SelfModelEngine.apply_decay(ctx.self_model)
    # ...
```

```python
def _pick_best_external(compiled_models: list[dict]) -> dict:
    """Pick the best non-cloud model from available models."""
    externals = [m for m in compiled_models
                 if not m.get("_kognisant_hosted")
                 and _is_provider_reachable(m["provider"])]

    # Prefer models with tool_calling + reasoning
    preferred = [m for m in externals
                 if m.get("capabilities", {}).get("tool_calling")
                 and m.get("capabilities", {}).get("reasoning")]

    if preferred:
        return preferred[0]
    return externals[0] if externals else compiled_models[0]
```

---

## 8. Retry Strategy

For transient errors (429, 500, 502, 503, network timeouts):

| Attempt | Wait | Action |
|---------|------|--------|
| 1 | 1 second | Retry same model |
| 2 | 2 seconds | Retry same model |
| 3 | 4 seconds | Retry same model |
| 4 | — | Mark provider unreachable, fall back to next tier |

```python
def _execute_with_fallback(fn, model, compiled_models, max_retries=3):
    """Execute a model call with retry, model-level fallback, then provider fallback.

    Cascade:
      1. Retry same model (backoff)
      2. Try next available cloud model
      3. Fall back to external/local
    """
    for attempt in range(max_retries):
        try:
            return fn(model)
        except ProviderUnreachableError:
            # 401/402 — entire provider is done, skip straight to external
            break
        except ModelUnreachableError:
            # This specific model failed after retries — try next cloud model
            next_cloud = _pick_next_cloud_model(compiled_models, exclude=model["name"])
            if next_cloud:
                model = next_cloud
                continue  # Try the new cloud model
            # No more cloud models available — fall back to external
            break
        except KognisantAPIError:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue
            # Retries exhausted on this model
            _mark_model_unreachable(model["name"])
            next_cloud = _pick_next_cloud_model(compiled_models, exclude=model["name"])
            if next_cloud:
                model = next_cloud
                continue
            break

    # All cloud models exhausted or provider unreachable — try external/local
    fallback = _pick_best_external(compiled_models)
    if fallback:
        return fn(fallback)
    raise KognisantAPIError("All model providers unavailable.")


def _pick_next_cloud_model(compiled_models: list[dict], exclude: str) -> dict | None:
    """Pick the next available cloud model, excluding the one that just failed."""
    candidates = [m for m in compiled_models
                  if m.get("_kognisant_hosted")
                  and _is_model_available(m)
                  and m.get("name") != exclude]
    if not candidates:
        return None
    # Prefer cheapest for task workers, or most capable for planners
    # (caller context determines which list this feeds into)
    return candidates[0]
```

---

## 9. Status Display

The CLI status line communicates the active model and any degradation:

### Normal operation

```
⚡ MiniMax-M3 (Kognisant Cloud) | valence: +12 | 3 skills
```

### After automatic fallback

```
⚡ gpt-4o (OpenAI) | valence: +12 | ⚠️ Cloud unavailable (insufficient balance)
```

### Not logged in (no cloud models)

```
⚡ gpt-4o (OpenAI) | valence: +12 | ready
```

### Implementation

```python
def _format_status(ctx) -> str:
    model = ctx.active_model
    name = model["display_name"]
    provider = model["provider"]

    cap_parts = []
    if ctx.capability_snapshot.get("skills_count"):
        cap_parts.append(f"{ctx.capability_snapshot['skills_count']} skills")
    if ctx.capability_snapshot.get("custom_tools_count"):
        cap_parts.append(f"{ctx.capability_snapshot['custom_tools_count']} tools")
    cap_summary = ", ".join(cap_parts) if cap_parts else "ready"

    base = f"⚡ {name} ({provider}) | valence: {ctx.self_model.valence:+d} | {cap_summary}"

    if ctx.auto_switched:
        base += f"\n  ⚠️  {ctx.switch_reason}"

    return base
```

---

## 10. Model Capabilities Reference

When selecting models, the CLI uses capabilities to determine fitness for a task:

| Capability | Type | CLI Usage |
|-----------|------|-----------|
| `modality` | `text` / `vision` / `embedding` | Filter out embedding models; `vision` models can handle image inputs |
| `context_window` | number | Determine if conversation fits; warn user if approaching limit |
| `tool_calling` | boolean | Required for agent mode (function calling) |
| `reasoning` | boolean | Preferred for complex multi-step tasks |
| `throughput_tps` | number | Used as tiebreaker when multiple models are equally capable |
| `quantization` | string | Informational — displayed in model picker |

### Selecting by Task

```python
def _select_model_for_task(compiled_models: list[dict], task_type: str) -> dict:
    """Select the best model for a given task type."""
    candidates = [m for m in compiled_models if _is_provider_reachable(m["provider"])]

    if task_type == "agent":
        # Must have tool_calling
        candidates = [m for m in candidates if m["capabilities"].get("tool_calling")]
    elif task_type == "reasoning":
        # Prefer reasoning models
        reasoning = [m for m in candidates if m["capabilities"].get("reasoning")]
        if reasoning:
            candidates = reasoning

    # Sort by throughput (faster = better UX)
    candidates.sort(
        key=lambda m: m.get("capabilities", {}).get("throughput_tps", 0),
        reverse=True,
    )

    return candidates[0] if candidates else None
```

---

## 11. Available Cloud Models

These models are available via `GET /v1/models` and exposed to CLI users as Tier 1:

| Model | Modality | Context | Throughput | Pricing (in/out per 1M) | Key Capabilities |
|-------|----------|---------|------------|--------------------------|------------------|
| `MiniMaxAI/MiniMax-M3` | text | 1,049K | 190 tok/s | $0.30 / $1.20 | reasoning, tool_calling |
| `moonshotai/Kimi-K2.7-Code` | text | 256K | 231 tok/s | $0.95 / $4.00 | reasoning, tool_calling |
| `moonshotai/Kimi-K2.6` | vision | 256K | 60 tok/s | $0.95 / $4.00 | reasoning, tool_calling |
| `zai-org/GLM-5.2` | text | 1,000K | 54 tok/s | $1.40 / $4.40 | reasoning, tool_calling |
| `deepseek-ai/DeepSeek-V4-Pro` | text | 1,000K | 24 tok/s | $1.75 / $3.50 | reasoning, tool_calling |
| `nvidia/Cosmos3-Super-Reasoner` | vision | 256K | 30 tok/s | $0.10 / $0.30 | reasoning, tool_calling |
| `Qwen/Qwen2.5-VL-72B-Instruct` | vision | 32K | 20 tok/s | $0.25 / $0.75 | — |

---

## 12. Full Request Lifecycle

```
User types a message
        │
        ▼
┌──────────────────────┐
│  get_compiled_models │ ← cached cloud models + user pool + Ollama
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│  select active model │ ← explicit default > cloud-first > external > local
│  (filter reachable)  │    (unreachable providers excluded at this step)
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│  _build_headers()    │ ← inject Firebase token for cloud, API key for external
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│  POST /v1/chat/      │
│  completions         │ → stream: true (SSE) or false (JSON)
└──────────┬───────────┘
           │
    ┌──────┴──────┐
    │  Success?   │
    └──────┬──────┘
     Yes   │         No
           │          │
           ▼          ▼
    ┌──────────┐  ┌────────────────────────────┐
    │  Render  │  │  _handle_http_error()       │
    │  output  │  │  → retry / fallback / fail  │
    └──────────┘  └────────────────────────────┘
```

---

## 13. User Commands

| Command | Effect |
|---------|--------|
| `kognisant login` | Authenticate via browser → enables Tier 1 models |
| `kognisant logout` | Clear credentials → Tier 1 models removed from list |
| `kognisant setup` | Configure external providers (API keys for OpenAI, Anthropic, etc.) |
| `/model` (in chat) | Interactive model picker — select any model from any tier |
| `set_default_model(id)` | Sticky override — this model is always used regardless of tier |

If a user explicitly sets an external model as default, cloud-first selection is disabled. The explicit choice always wins.

---

## 14. Edge Cases

### Fresh install — no models configured

If a user hasn't run `kognisant login` AND hasn't configured external providers AND Ollama isn't running:

```
No models available.
  → Run 'kognisant login' to use Kognisant Cloud models
  → Run 'kognisant setup' to configure external providers (OpenAI, Anthropic, etc.)
  → Install Ollama for local inference: https://ollama.com
```

### Logged in but all cloud models return 404

If the model catalog changed and cached models are stale:
1. Clear in-memory and on-disk model cache
2. Re-fetch from `/v1/models`
3. If fetch fails, fall back to Tier 2/3

### Token refresh race during streaming

If a token expires mid-stream:
- The stream will fail with a connection error (not a clean 401)
- The CLI treats this as a network timeout → retries with a fresh token
- Pre-flight: check token expiry before starting a stream; if <2min remaining, refresh first

---

## 15. Context Window Overflow

When the conversation exceeds the active model's `context_window`, the CLI handles it automatically:

### Strategy: auto-switch → truncate

```python
def _enforce_context_window(messages: list[dict], model: dict, compiled_models: list[dict]) -> tuple[list[dict], dict]:
    """Ensure messages fit within the model's context window.

    Strategy:
      1. If messages exceed active model's context → switch to a larger model
      2. If no larger model exists → truncate oldest messages (keep system prompt)

    Returns:
        (possibly_truncated_messages, possibly_switched_model)
    """
    context_limit = model.get("capabilities", {}).get("context_window", 128000)
    estimated_tokens = estimate_conversation_tokens(messages)

    if estimated_tokens <= context_limit:
        return messages, model  # Fits fine

    # ── Step 1: Try to switch to a model with a larger context window ──
    larger_models = [m for m in compiled_models
                     if _is_model_available(m)
                     and m.get("capabilities", {}).get("context_window", 0) > estimated_tokens
                     and m.get("capabilities", {}).get("tool_calling") is True]

    # Prefer cloud, then sort by smallest sufficient window (don't waste capacity)
    larger_models.sort(key=lambda m: (
        not m.get("_kognisant_hosted", False),
        m.get("capabilities", {}).get("context_window", 0),
    ))

    if larger_models:
        new_model = larger_models[0]
        print(f"  ↗ Context exceeds {context_limit // 1000}K — switching to "
              f"{new_model['display_name']} ({new_model['capabilities']['context_window'] // 1000}K)")
        return messages, new_model

    # ── Step 2: No model large enough — truncate oldest messages ──
    # Keep: system prompt (index 0) + most recent messages that fit
    system_msg = messages[0] if messages and messages[0].get("role") == "system" else None
    conversation = messages[1:] if system_msg else messages[:]

    system_tokens = estimate_conversation_tokens([system_msg]) if system_msg else 0
    available = context_limit - system_tokens - 500  # 500 token buffer for response

    # Remove oldest messages until it fits
    while conversation and estimate_conversation_tokens(conversation) > available:
        conversation.pop(0)

    truncated = ([system_msg] + conversation) if system_msg else conversation

    removed_count = len(messages) - len(truncated)
    print(f"  ⚠️  Context full ({estimated_tokens // 1000}K tokens). "
          f"Trimmed {removed_count} oldest messages to fit {context_limit // 1000}K window.")

    return truncated, model
```

### When this runs

Called in `_execute()` just before sending the request to the API:

```python
def _execute(ctx):
    # Enforce context window before sending
    ctx.messages, ctx.active_model = _enforce_context_window(
        ctx.messages, ctx.active_model, get_compiled_models()
    )
    # ... proceed with API call ...
```

### Behavior summary

| Situation | Action | User sees |
|-----------|--------|-----------|
| Conversation fits | Nothing | (normal) |
| Exceeds model, larger model available | Auto-switch to larger model | `↗ Context exceeds 256K — switching to MiniMax-M3 (1049K)` |
| Exceeds model, no larger model exists | Truncate oldest messages (keep system prompt + recent) | `⚠️ Context full (270K tokens). Trimmed 12 oldest messages to fit 256K window.` |
| Exceeds ALL models (>1M) | Truncate to fit largest available | Same as above |

### Token estimation

Uses the existing `estimate_tokens()` from `telemetry.py` for fast counting. Not exact, but good enough for overflow detection (errs on the side of caution with a 500-token buffer).

---

## 16. Vision Model Routing

Cloud models with `modality: vision` (Kimi-K2.6, Cosmos3, Qwen2.5-VL) are only selected when the input requires it.

### Selection rule

```python
def _select_model(compiled_models: list[dict], has_image_input: bool) -> list[dict]:
    """Filter models based on input modality."""
    available = [m for m in compiled_models if _is_model_available(m)]

    if has_image_input:
        # Must use a vision model
        vision = [m for m in available if m.get("capabilities", {}).get("modality") == "vision"]
        if vision:
            return vision
        # No vision model available — strip image, use text model, warn user
        return [m for m in available if m.get("capabilities", {}).get("modality") == "text"]

    # Text-only input — exclude vision models to prefer cheaper/faster text models
    text_models = [m for m in available if m.get("capabilities", {}).get("modality") == "text"]
    return text_models if text_models else available
```

### When a user sends an image

1. The CLI detects image content in the message (base64 or file path)
2. Model selection filters to vision-capable models only
3. Payload includes the image in OpenAI multimodal format:

```python
# Multimodal message format (OpenAI-compatible)
message = {
    "role": "user",
    "content": [
        {"type": "text", "text": "What's in this image?"},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_data}"}},
    ],
}
```

### Behavior summary

| Input | Model pool | Result |
|-------|-----------|--------|
| Text only | Text models only | Cheaper, faster (MiniMax-M3, DeepSeek-V4-Pro) |
| Image + text | Vision models only | Kimi-K2.6, Cosmos3, Qwen2.5-VL |
| Image but no vision model available | Text models (image stripped) | Warn: `⚠️ No vision model available. Image ignored.` |

---

## 17. Token Usage from API Responses

When the inference API returns actual token counts in the response, use them instead of `estimate_tokens()`:

```python
def _extract_usage(response: dict) -> tuple[int, int] | None:
    """Extract actual token usage from API response if available."""
    usage = response.get("usage")
    if usage:
        return usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
    return None


# In the execution path:
result = query_model_api_raw(...)
actual_usage = _extract_usage(result)
if actual_usage:
    tokens_in, tokens_out = actual_usage
else:
    tokens_in = estimate_tokens(messages)
    tokens_out = estimate_tokens(response_content)
```

For streaming, the `usage` object appears in the final chunk (when `stream_options: {"include_usage": true}` is set):

```python
# Request payload for streaming with usage
payload = {
    "model": model["name"],
    "messages": messages,
    "stream": True,
    "stream_options": {"include_usage": True},
}
```

The last SSE chunk before `[DONE]` will contain:
```json
{"usage": {"prompt_tokens": 1234, "completion_tokens": 567, "total_tokens": 1801}}
```

Use real counts for:
- Telemetry (`append_telemetry`)
- Context window tracking (more accurate than estimation)
- Cost calculation (pricing × actual tokens)

---

## 18. Self-Model Reliability Hygiene

The `SelfModel.model_reliability` dict accumulates entries keyed by model name. When cloud models change (removed, renamed, new versions), stale entries remain. Cleanup strategy:

### Pruning rule

During `SelfModelEngine.load()`, prune entries that:
1. Haven't been used in 30 days (`last_success_at` and `last_failure_at` both older than 30 days)
2. Are not in the current compiled model list

```python
def _prune_stale_reliability(model: SelfModel, active_model_names: set[str]) -> None:
    """Remove reliability entries for models that no longer exist and haven't been used recently."""
    stale_keys = []
    cutoff_seconds = 30 * 86400  # 30 days

    for name, rel in model.model_reliability.items():
        # Keep if model is still active
        if name in active_model_names:
            continue

        # Keep if used recently (might come back, e.g., API was temporarily down)
        last_used = max(
            _seconds_since(rel.last_success_at) if rel.last_success_at else float("inf"),
            _seconds_since(rel.last_failure_at) if rel.last_failure_at else float("inf"),
        )
        if last_used == float("inf") or last_used > cutoff_seconds:
            stale_keys.append(name)

    for key in stale_keys:
        del model.model_reliability[key]

    # Also prune circuit breakers for removed models
    for key in list(model.circuit_breakers.keys()):
        if key not in active_model_names and key in stale_keys:
            del model.circuit_breakers[key]
```

### When to run

- Once per session, during `_bootstrap()`, after `get_compiled_models()` returns the active list
- Not on every message — just once at startup

### Why 30 days?

- Models might temporarily disappear (API maintenance) — don't nuke reliability data immediately
- After 30 days of no use, the data is stale enough to be worthless (model may have been retrained, infrastructure changed)
- Keeps `self_model.json` from growing unbounded over months of use

---

## 19. Swarm Model Selection

When the runtime escalates to agent/swarm mode, it needs two models: a **planner** and **task workers**. The current `get_best_models_pool()` picks these independently, but the selection logic doesn't account for tiers or cost.

### New behavior: premium planner, cheaper workers

```python
def get_best_models_pool(compiled_models, active_model_name=None):
    """Select planner and task models with tier and cost awareness.

    Planner (needs reasoning + tool_calling):
      → Most capable cloud model (highest context, reasoning=true)
      → Fallback: best external model with reasoning

    Task workers (needs tool_calling):
      → Cheapest cloud model with tool_calling
      → Fallback: best external model with tool_calling
    """
    reachable = [m for m in compiled_models if _is_provider_reachable(m["provider"])]
    if not reachable:
        mock = {"name": "mock", "provider": "Offline", "api_base_url": ""}
        return mock, mock

    # ── Planner: premium model ─────────────────────────────────
    planner_candidates = [m for m in reachable
                          if m.get("capabilities", {}).get("reasoning") is True
                          and m.get("capabilities", {}).get("tool_calling") is True]

    # Sort: cloud-first, then by context_window (larger = more capable)
    planner_candidates.sort(key=lambda m: (
        not m.get("_kognisant_hosted", False),
        -m.get("capabilities", {}).get("context_window", 0),
    ))

    # If active model is a valid planner candidate, prefer it (proven working)
    if active_model_name:
        for i, m in enumerate(planner_candidates):
            if m.get("name") == active_model_name:
                planner_candidates.insert(0, planner_candidates.pop(i))
                break

    planning_model = planner_candidates[0] if planner_candidates else reachable[0]

    # ── Task workers: cheaper model ───────────────────────────
    task_candidates = [m for m in reachable
                       if m.get("capabilities", {}).get("tool_calling") is True]

    # Sort: cloud-first, then by output cost (cheapest first)
    task_candidates.sort(key=lambda m: (
        not m.get("_kognisant_hosted", False),
        m.get("_pricing", {}).get("output_per_million", 999),
    ))

    task_model = task_candidates[0] if task_candidates else reachable[0]

    return planning_model, task_model
```

### Selection examples (with current cloud catalog)

| Role | Selected Model | Why |
|------|---------------|-----|
| Planner | `deepseek-ai/DeepSeek-V4-Pro` | reasoning + tool_calling + 1M context |
| Task worker | `nvidia/Cosmos3-Super-Reasoner` | tool_calling + cheapest ($0.10/$0.30 per 1M) |

If the user's task is code-heavy, the planner might pick `moonshotai/Kimi-K2.7-Code` (231 tok/s, code-focused). Future improvement: task-type-aware model routing.

### Fallback cascade — per-model, not per-provider

A single cloud model failing (500, timeout, overloaded) does NOT mean the whole cloud is down. Fallback is granular:

```
Model-level failure (500, 502, timeout on specific model)
  → Mark THAT MODEL as session-unreachable
  → Pick next best cloud model for the same role
  → Only if ALL cloud models are unreachable → fall back to external/local

Account-level failure (402 insufficient balance, 401 auth)
  → Mark entire Kognisant Cloud provider as unreachable
  → Fall back to external models → then local
```

```python
# Per-model unreachable (e.g., DeepSeek-V4-Pro is down but MiniMax-M3 works)
_session_unreachable_models: set[str] = set()

# Provider-level unreachable (402 = no balance, affects ALL cloud models)
_session_unreachable_providers: set[str] = set()


def _mark_model_unreachable(model_name: str, reason: str):
    """Mark a specific model as unreachable (server error, timeout)."""
    _session_unreachable_models.add(model_name)


def _mark_provider_unreachable(provider: str, reason: str):
    """Mark entire provider as unreachable (auth/billing failure)."""
    _session_unreachable_providers.add(provider)


def _is_model_available(model: dict) -> bool:
    """Check if a model is available (not individually failed, provider not down)."""
    if model.get("provider") in _session_unreachable_providers:
        return False
    if model.get("name") in _session_unreachable_models:
        return False
    return True
```

### Example: DeepSeek is down but MiniMax works

```
Request to DeepSeek-V4-Pro → 502 upstream error
  → Retry 3x with backoff → still failing
  → Mark "deepseek-ai/DeepSeek-V4-Pro" as unreachable
  → Re-select planner from remaining cloud models
  → Pick MiniMaxAI/MiniMax-M3 (next best reasoning + tool_calling)
  → Continue on cloud ✓
```

### Example: all cloud models fail

```
DeepSeek-V4-Pro → 502 (marked unreachable)
MiniMax-M3 → 502 (marked unreachable)
Kimi-K2.7-Code → timeout (marked unreachable)
... all cloud models exhausted ...
  → No available cloud models remain
  → Fall back to Tier 2 (external: OpenAI, Anthropic, etc.)
  → If no external → Tier 3 (Ollama)
```

### Example: billing failure (402)

```
Any cloud model → 402 insufficient balance
  → Mark "Kognisant Cloud" provider as unreachable (affects ALL cloud models)
  → Immediately fall back to Tier 2/3 (no point trying other cloud models)
  → Print: "⚠️  Insufficient balance. Top up at: kognisant.xyz/console/billing"
```

### Mid-swarm behavior

If cloud fails during subtask 3 of 5:
- The failed subtask retries with the next available model (cloud or external)
- Remaining subtasks use whatever is available
- The planning phase is already complete so it's not re-run
- The swarm doesn't restart — it continues from where it left off

### Key fix: explicit `tool_calling` check

The old code used `.get("tool_calling", True)` which defaulted missing capabilities to True — allowing local models without explicit tool_calling support (like Gemma) to pass the filter. The new code requires `is True` explicitly:

```python
# OLD (broken) — Gemma sneaks through
tool_capable = [m for m in candidates
                if m.get("capabilities", {}).get("tool_calling", True)]

# NEW (correct) — only models that explicitly declare tool_calling
task_candidates = [m for m in reachable
                   if m.get("capabilities", {}).get("tool_calling") is True]
```

---

## 20. Contact & Support

| Resource | URL/Address |
|----------|-------------|
| Billing & top-up | `kognisant.xyz/console/billing` |
| Support | `support@kognisant.xyz` |
| Login portal | `kognisant.xyz/login` |
| Model catalog | `inference.kognisant.xyz/v1/models` |

---

## 21. Implementation Checklist

| File | Change | Priority |
|------|--------|----------|
| **New: `auth.py`** | Firebase login/logout/token lifecycle (decoupled from sync) | P0 |
| **`main.py`** | Add `kognisant login` / `kognisant logout` top-level commands | P0 |
| **`config.py`** | Cloud models injected first in `get_compiled_models()`, model list caching | P0 |
| **`network.py`** | Firebase JWT injection for `inference.kognisant.xyz`, error handling, provider-level unreachable tracking | P0 |
| **`agents.py`** | Rewrite `get_best_models_pool()` with tier-aware selection, premium planner + cheap worker, fix `tool_calling` default bug | P0 |
| **`runtime.py`** | `_bootstrap` awareness of cloud degradation, status messages | P1 |
| **`sync.py`** | Refactor to import from `auth.py`, remove duplicate token logic | P2 |
