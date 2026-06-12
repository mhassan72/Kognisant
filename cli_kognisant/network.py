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


def query_model_api_raw(api_base_url, api_key, payload):
    """Sends a payload to any standard OpenAI-compatible API with retry and backoff on transient errors."""
    url = api_base_url.rstrip("/")
    if not url.endswith("/chat/completions") and not url.endswith("/chat"):
        url = f"{url}/chat/completions"

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    req_body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=req_body, headers=headers, method="POST")

    context = ssl._create_unverified_context()

    # Exponential Backoff and Retry Parameters
    max_retries = 3
    backoff = 1.0

    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=45.0, context=context) as response:
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
            # Retry on transient status codes: 429 (Too Many Requests), 502 (Bad Gateway), 503 (Service Unavailable), 504 (Gateway Timeout)
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


def query_model_api(api_base_url, api_key, model_name, messages):
    """Queries any standard OpenAI-compatible Chat Completions API endpoint and returns content."""
    payload = {"model": model_name, "messages": messages, "stream": False}
    resp_data = query_model_api_raw(api_base_url, api_key, payload)

    if resp_data and "choices" in resp_data:
        return resp_data["choices"][0]["message"]["content"]
    elif resp_data and "message" in resp_data:
        return resp_data["message"]["content"]
    else:
        raise KognisantAPIError("Unknown model API response format.")
