import json
import ssl
import time
import urllib.error
import urllib.request

OLLAMA_HOST = "http://localhost:11434"


class KognisantAPIError(Exception):
    """Custom exception raised for Kognisant network and API transport layer failures."""

    pass


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


def query_model_api_raw(api_base_url, api_key, payload, protocol="openai"):
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
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

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
            # Retry on transient status codes
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
            raise KognisantAPIError(f"Network Connection Failed: {e}")


def query_model_api(api_base_url, api_key, model_name, messages, protocol="openai"):
    """Queries any supported API protocol and returns content."""
    payload = {"model": model_name, "messages": messages, "stream": False}
    resp_data = query_model_api_raw(api_base_url, api_key, payload, protocol=protocol)

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
