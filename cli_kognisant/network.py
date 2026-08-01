import json
import socket
import ssl
import time
import urllib.error
import urllib.request

OLLAMA_HOST = "http://localhost:11434"

KOGNISANT_INFERENCE_BASE = "https://inference.kognisant.xyz"


class KognisantAPIError(Exception):
    """Custom exception raised for Kognisant network and API transport layer failures."""

    pass


class ModelUnreachableError(KognisantAPIError):
    """Raised when a specific model is unreachable after retries (500, 502, timeout)."""

    def __init__(self, model_name: str, message: str = ""):
        self.model_name = model_name
        super().__init__(f"Model '{model_name}' unreachable: {message}")


class ProviderUnreachableError(KognisantAPIError):
    """Raised when an entire provider is unreachable (402, 401 after refresh)."""

    def __init__(self, provider: str, reason: str = ""):
        self.provider = provider
        self.reason = reason
        super().__init__(f"Provider '{provider}' unreachable: {reason}")


# ─── Session-level unreachable tracking ───────────────────────────────────────

_session_unreachable_models: set = set()
_session_unreachable_providers: set = set()


def _mark_model_unreachable(model_name: str):
    """Mark a specific model as unreachable for this session."""
    _session_unreachable_models.add(model_name)


def _mark_provider_unreachable(provider: str, reason: str):
    """Mark all models from a provider as unreachable for this session."""
    _session_unreachable_providers.add(provider)


def is_model_available(model: dict) -> bool:
    """Check if a model can be used right now."""
    if model.get("provider") in _session_unreachable_providers:
        return False
    if model.get("name") in _session_unreachable_models:
        return False
    return True


def is_provider_reachable(provider: str) -> bool:
    """Check if a provider is reachable."""
    return provider not in _session_unreachable_providers


def _get_auth_header(api_base_url: str, api_key: str) -> str | None:
    """Get the Authorization header value for a request.

    For Kognisant Cloud: injects Firebase token or API key from auth module.
    For external providers: uses the provided api_key.
    """
    if api_key:
        return f"Bearer {api_key}"

    if KOGNISANT_INFERENCE_BASE in api_base_url:
        from .auth import get_id_token
        token = get_id_token()
        if not token:
            raise KognisantAPIError(
                "Not authenticated. Run 'kognisant login' to use Kognisant Cloud models."
            )
        return f"Bearer {token}"

    return None


def get_ollama_models():
    """Queries local Ollama to get list of downloaded models. Returns None if Ollama is unreachable."""
    url = f"{OLLAMA_HOST}/api/tags"
    try:
        context = ssl._create_unverified_context()
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=1.5, context=context) as response:
            if response.status == 200:
                data = json.loads(response.read().decode("utf-8"))
                return [m["name"] for m in data.get("models", [])]
    except (urllib.error.URLError, TimeoutError):
        pass
    return None


def query_model_api_raw(api_base_url, api_key, payload, protocol="openai", model_name=""):
    """Sends a payload to any supported API protocol (OpenAI, Ollama, Llama.cpp) with retry and backoff."""
    url = api_base_url.rstrip("/")

    if protocol == "ollama":
        if not url.endswith("/api/chat"):
            url = f"{url}/api/chat"
    elif protocol == "llama_cpp":
        if not url.endswith("/completion") and not url.endswith("/v1/chat/completions"):
            # Prefer OpenAI-compatible endpoint if available in llama.cpp server, fallback to native completion
            url = f"{url}/v1/chat/completions"
    else:  # Default to openai
        if not url.endswith("/chat/completions") and not url.endswith("/chat"):
            url = f"{url}/chat/completions"

    headers = {"Content-Type": "application/json"}
    auth_header = _get_auth_header(api_base_url, api_key)
    if auth_header:
        headers["Authorization"] = auth_header

    # Adaptive Payload Conversion (if necessary)
    if protocol == "llama_cpp" and url.endswith("/completion"):
        # Convert Chat messages to single prompt for llama.cpp native completion API
        messages = payload.get("messages", [])
        prompt = ""
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            prompt += f"\n\n{role.upper()}: {content}"
        prompt += "\n\nASSISTANT: "

        payload = {
            "prompt": prompt,
            "stream": False,
            "n_predict": 2048,
        }

    req_body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=req_body, headers=headers, method="POST")

    context = ssl._create_unverified_context()

    # Exponential Backoff and Retry Parameters
    max_retries = 3
    backoff = 1.0
    is_kognisant = KOGNISANT_INFERENCE_BASE in api_base_url

    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=60.0, context=context) as response:
                if response.status == 200:
                    try:
                        return json.loads(response.read().decode("utf-8"))
                    except json.JSONDecodeError:
                        raise KognisantAPIError(
                            "Failed to parse API response as JSON (received malformed content)."
                        )
                else:
                    raise KognisantAPIError(f"HTTP Error {response.status} from API.")

        except urllib.error.HTTPError as e:
            if is_kognisant:
                _handle_kognisant_error(e, model_name, attempt, max_retries)
                # If _handle_kognisant_error returns (didn't raise), retry
                # Rebuild request with fresh token on 401 retry
                if e.code == 401:
                    fresh_auth = _get_auth_header(api_base_url, api_key)
                    if fresh_auth:
                        req.remove_header("Authorization")
                        req.add_header("Authorization", fresh_auth)
                time.sleep(backoff)
                backoff *= 2.0
                continue

            # Non-Kognisant: existing retry logic
            if e.code in [429, 502, 503, 504] and attempt < max_retries - 1:
                time.sleep(backoff)
                backoff *= 2.0
                continue

            # Fatal error
            try:
                error_body = e.read().decode("utf-8")
            except Exception:
                error_body = e.reason
            raise KognisantAPIError(f"API HTTP Error {e.code}: {error_body}")

        except (urllib.error.URLError, TimeoutError) as e:
            # Retry on network dropouts or timeouts
            if attempt < max_retries - 1:
                time.sleep(backoff)
                backoff *= 2.0
                continue
            if is_kognisant and model_name:
                _mark_model_unreachable(model_name)
                raise ModelUnreachableError(model_name, str(e))
            raise KognisantAPIError(f"Network Connection Failed: {e}")


def query_model_api(api_base_url, api_key, model_name, messages, protocol="openai"):
    """Queries any supported API protocol and returns content."""
    payload = {"model": model_name, "messages": messages, "stream": False}
    resp_data = query_model_api_raw(api_base_url, api_key, payload, protocol=protocol, model_name=model_name)

    if not resp_data:
        raise KognisantAPIError("Received empty response from model API.")

    # 1. OpenAI Standard format
    if "choices" in resp_data and len(resp_data["choices"]) > 0:
        choice = resp_data["choices"][0]
        if "message" in choice:
            return choice["message"].get("content", "")
        elif "text" in choice:  # legacy or llama.cpp completion
            return choice.get("text", "")

    # 2. Ollama Native format
    if "message" in resp_data:
        return resp_data["message"].get("content", "")

    # 3. Llama.cpp Native /completion format
    if "content" in resp_data:
        return resp_data.get("content", "")

    # 4. Direct text fallback
    if isinstance(resp_data, str):
        return resp_data

    raise KognisantAPIError(
        f"Unknown model API response format for protocol '{protocol}'."
    )


def query_model_api_stream(api_base_url, api_key, payload, protocol="openai", timeout=120.0, model_name=""):
    """Send a streaming request to the LLM API. Yields (chunk_type, data) tuples.

    chunk_type is one of:
      - "phase": data is a phase name (e.g. "connected")
      - "content": data is a text fragment to display
      - "tool_calls": data is the accumulated tool_calls list (yielded once at end)
      - "done": data is the full assembled assistant message dict

    Falls back to non-streaming if the API does not support SSE.
    """
    url = api_base_url.rstrip("/")

    if protocol == "ollama":
        if not url.endswith("/api/chat"):
            url = f"{url}/api/chat"
    elif protocol == "llama_cpp":
        if not url.endswith("/completion") and not url.endswith("/v1/chat/completions"):
            url = f"{url}/v1/chat/completions"
    else:
        if not url.endswith("/chat/completions") and not url.endswith("/chat"):
            url = f"{url}/chat/completions"

    headers = {"Content-Type": "application/json"}
    auth_header = _get_auth_header(api_base_url, api_key)
    if auth_header:
        headers["Authorization"] = auth_header

    # Force stream=True in payload
    payload = dict(payload)
    payload["stream"] = True

    req_body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=req_body, headers=headers, method="POST")
    context = ssl._create_unverified_context()

    try:
        response = urllib.request.urlopen(req, timeout=timeout, context=context)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
        raise KognisantAPIError(f"Streaming request failed: {e}")

    if response.status != 200:
        raise KognisantAPIError(f"HTTP Error {response.status} from streaming API.")

    yield ("phase", "connected")

    # Set socket-level stall timeout for read operations (best-effort)
    try:
        if hasattr(response.fp, '_sock') and response.fp._sock is not None:
            response.fp._sock.settimeout(30.0)
        elif hasattr(response.fp, 'raw') and hasattr(response.fp.raw, '_sock'):
            response.fp.raw._sock.settimeout(30.0)
    except (AttributeError, OSError):
        pass  # Stall detection unavailable for this connection type (e.g. local Ollama)

    # Parse SSE stream (or raw JSON for Ollama)
    content_parts = []
    thinking_parts = []
    tool_calls_accumulated = []
    assistant_role = "assistant"
    usage_data = None

    try:
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace").strip()

            if not line:
                continue

            # Ollama native streaming: raw JSON objects, one per line (no SSE prefix)
            if protocol == "ollama":
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Track usage if present
                if "usage" in chunk and chunk["usage"]:
                    usage_data = chunk["usage"]

                # Ollama format: {"message": {"role": "assistant", "content": "...", "thinking": "..."}, "done": false}
                if "message" in chunk:
                    msg = chunk["message"]
                    # Thinking/reasoning tokens (Ollama uses "thinking" field)
                    if "thinking" in msg and msg["thinking"]:
                        thinking_parts.append(msg["thinking"])
                        yield ("thinking", msg["thinking"])
                    # Content tokens
                    if "content" in msg and msg["content"]:
                        content_parts.append(msg["content"])
                        yield ("content", msg["content"])
                    # Ollama tool calls
                    if "tool_calls" in msg and msg["tool_calls"]:
                        for tc in msg["tool_calls"]:
                            tool_calls_accumulated.append(tc)

                # Ollama signals completion with "done": true
                if chunk.get("done", False):
                    break
                continue

            # SSE format (OpenAI-compatible, llama.cpp, etc.)
            if line.startswith(":"):
                continue  # SSE comment
            if not line.startswith("data:"):
                continue

            data_str = line[5:].strip()
            if data_str == "[DONE]":
                break

            try:
                chunk = json.loads(data_str)
            except json.JSONDecodeError:
                continue

            # Track usage if present in chunk
            if "usage" in chunk and chunk["usage"]:
                usage_data = chunk["usage"]

            # Handle OpenAI-compatible streaming format
            if "choices" in chunk and len(chunk["choices"]) > 0:
                delta = chunk["choices"][0].get("delta", {})

                # Thinking/reasoning tokens (DeepSeek: reasoning_content, generic: thinking)
                if "reasoning_content" in delta and delta["reasoning_content"]:
                    thinking_parts.append(delta["reasoning_content"])
                    yield ("thinking", delta["reasoning_content"])
                elif "thinking" in delta and delta["thinking"]:
                    thinking_parts.append(delta["thinking"])
                    yield ("thinking", delta["thinking"])

                # Content token
                if "content" in delta and delta["content"]:
                    content_parts.append(delta["content"])
                    yield ("content", delta["content"])

                # Tool calls (streamed incrementally)
                if "tool_calls" in delta:
                    for tc_delta in delta["tool_calls"]:
                        idx = tc_delta.get("index", 0)
                        # Extend the accumulated list if needed
                        while len(tool_calls_accumulated) <= idx:
                            tool_calls_accumulated.append(
                                {"id": "", "function": {"name": "", "arguments": ""}, "type": "function"}
                            )
                        tc = tool_calls_accumulated[idx]
                        if "id" in tc_delta and tc_delta["id"]:
                            tc["id"] = tc_delta["id"]
                        if "function" in tc_delta:
                            fn = tc_delta["function"]
                            if "name" in fn and fn["name"]:
                                tc["function"]["name"] += fn["name"]
                            if "arguments" in fn and fn["arguments"]:
                                tc["function"]["arguments"] += fn["arguments"]

            # Handle Ollama native streaming format (fallback for OpenAI-compat endpoints)
            elif "message" in chunk:
                msg = chunk["message"]
                if "thinking" in msg and msg["thinking"]:
                    thinking_parts.append(msg["thinking"])
                    yield ("thinking", msg["thinking"])
                if "content" in msg and msg["content"]:
                    content_parts.append(msg["content"])
                    yield ("content", msg["content"])

    except socket.timeout:
        raise KognisantAPIError("Stream stalled — no data for 30s")
    except Exception:
        pass  # Stream ended unexpectedly, use what we have
    finally:
        response.close()

    # Assemble final message
    full_content = "".join(content_parts)
    full_thinking = "".join(thinking_parts)
    assistant_message = {"role": assistant_role, "content": full_content}

    if full_thinking:
        assistant_message["_thinking"] = full_thinking

    if tool_calls_accumulated:
        assistant_message["tool_calls"] = tool_calls_accumulated
        assistant_message["content"] = full_content or None
        yield ("tool_calls", tool_calls_accumulated)

    if usage_data:
        assistant_message["_usage"] = usage_data

    yield ("done", assistant_message)


# ─── Kognisant Cloud Error Handler ────────────────────────────────────────────


def _handle_kognisant_error(error, model_name: str, attempt: int, max_retries: int):
    """Handle HTTP errors from the Kognisant inference API.

    Key distinction:
      - 401, 402 → provider-level (all cloud models affected)
      - 404, 429, 500, 502, 503 → model-level (only this model marked, try others)

    Returns normally if the error is retryable (caller should retry).
    Raises an exception if the error is fatal.
    """
    try:
        body = json.loads(error.read().decode("utf-8"))
        message = body.get("error", {}).get("message", f"HTTP {error.code}")
    except (json.JSONDecodeError, KeyError, AttributeError):
        message = f"HTTP {error.code}"

    # ── Account-level failures (mark entire provider) ──────────
    if error.code == 401:
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

    if error.code == 402:
        _mark_provider_unreachable("Kognisant Cloud", "insufficient balance")
        import sys
        print(
            f"  \u26a0\ufe0f  Kognisant Cloud unavailable (insufficient balance). Using external models.\n"
            f"      Top up at: kognisant.xyz/console/billing",
            file=sys.stderr,
        )
        raise ProviderUnreachableError("Kognisant Cloud", "insufficient balance")

    # ── Model-level failures (mark only this model) ────────────
    if error.code == 404:
        if model_name:
            _mark_model_unreachable(model_name)
        raise ModelUnreachableError(model_name or "unknown", f"Model not found: {message}")

    if error.code in (429, 500, 502, 503):
        if attempt < max_retries - 1:
            return  # Will retry with backoff

        # Retries exhausted — mark this model unreachable
        if model_name:
            _mark_model_unreachable(model_name)
        raise ModelUnreachableError(model_name or "unknown", message)

    raise KognisantAPIError(f"API Error {error.code}: {message}")


def fetch_kognisant_models() -> list:
    """Fetch available models from Kognisant Cloud inference API.

    Endpoint is public — no auth required for model listing.
    Returns list of model dicts, or [] on failure.
    """
    import os

    cache_path = os.path.join(
        os.path.expanduser("~/.kognisant_core"), "cloud_models_cache.json"
    )

    # Check disk cache first
    try:
        if os.path.exists(cache_path):
            with open(cache_path, "r", encoding="utf-8") as f:
                cache = json.load(f)
            if cache.get("expires_at", 0) > time.time():
                return cache.get("models", [])
    except (json.JSONDecodeError, OSError):
        pass

    # Fetch from API
    url = f"{KOGNISANT_INFERENCE_BASE}/v1/models"
    req = urllib.request.Request(url, method="GET")
    ctx = ssl._create_unverified_context()

    try:
        with urllib.request.urlopen(req, timeout=5.0, context=ctx) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        models = data.get("data", [])

        # Save to disk cache (1 hour TTL)
        try:
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            cache_data = {"models": models, "expires_at": time.time() + 3600}
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(cache_data, f)
        except OSError:
            pass

        return models
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        # Fallback to expired cache if API unreachable
        try:
            if os.path.exists(cache_path):
                with open(cache_path, "r", encoding="utf-8") as f:
                    cache = json.load(f)
                return cache.get("models", [])
        except (json.JSONDecodeError, OSError):
            pass
        return []
