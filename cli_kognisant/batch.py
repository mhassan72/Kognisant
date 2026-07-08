"""
Batch API request management for cost reduction.

Collects eligible LLM requests, submits them as batch jobs to providers
that support it (Gemini, OpenAI), and distributes results via callbacks.
Falls back to single calls for unsupported providers.

Uses only Python standard library per Requirement 13.
"""

import json
import logging
import os
import ssl
import tempfile
import threading
import time
import urllib.request
import urllib.error
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .config import GLOBAL_CORE_DIR

logger = logging.getLogger(__name__)

# ─── Constants ─────────────────────────────────────────────────────────────────

BATCH_DIR = os.path.join(GLOBAL_CORE_DIR, "batch")
BATCH_CONFIG_FILE = os.path.join(GLOBAL_CORE_DIR, "batch_config.json")
PENDING_FILE = os.path.join(BATCH_DIR, "pending.json")
JOBS_FILE = os.path.join(BATCH_DIR, "jobs.json")

# Default flush triggers
DEFAULT_MAX_QUEUE_SIZE = 10
DEFAULT_MAX_QUEUE_AGE_SEC = 60
DEFAULT_POLL_INTERVAL_SEC = 30
DEFAULT_REQUEST_TIMEOUT_SEC = 300  # 5 min max wait for batch result

# Providers with batch support
BATCH_PROVIDERS = {"google gemini", "openai"}


# ─── Configuration ─────────────────────────────────────────────────────────────

def load_batch_config() -> dict:
    """Load batch configuration from disk."""
    if os.path.exists(BATCH_CONFIG_FILE):
        try:
            with open(BATCH_CONFIG_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "enabled": False,
        "max_queue_size": DEFAULT_MAX_QUEUE_SIZE,
        "max_queue_age_sec": DEFAULT_MAX_QUEUE_AGE_SEC,
        "poll_interval_sec": DEFAULT_POLL_INTERVAL_SEC,
        "fallback_to_single": True,
    }


def save_batch_config(config: dict) -> None:
    """Save batch configuration to disk."""
    os.makedirs(GLOBAL_CORE_DIR, exist_ok=True)
    with open(BATCH_CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


# ─── Data Models ───────────────────────────────────────────────────────────────

@dataclass
class BatchRequest:
    """A single LLM request queued for batch submission."""
    request_id: str
    model: str
    provider: str
    api_base: str
    api_key: str
    messages: list
    tools: list | None = None
    callback_id: str | None = None
    queued_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "model": self.model,
            "provider": self.provider,
            "api_base": self.api_base,
            "messages": self.messages,
            "tools": self.tools,
            "callback_id": self.callback_id,
            "queued_at": self.queued_at,
        }

    @classmethod
    def from_dict(cls, d: dict, api_key: str = "") -> "BatchRequest":
        return cls(
            request_id=d["request_id"],
            model=d["model"],
            provider=d["provider"],
            api_base=d["api_base"],
            api_key=api_key,
            messages=d["messages"],
            tools=d.get("tools"),
            callback_id=d.get("callback_id"),
            queued_at=d.get("queued_at", time.time()),
        )


@dataclass
class BatchJob:
    """A submitted batch job being tracked."""
    job_id: str
    provider: str
    api_base: str
    api_key: str
    request_ids: list[str]
    status: str = "processing"  # processing, completed, failed
    submitted_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "provider": self.provider,
            "api_base": self.api_base,
            "request_ids": self.request_ids,
            "status": self.status,
            "submitted_at": self.submitted_at,
            "completed_at": self.completed_at,
            "error": self.error,
        }


# ─── Batch Queue ───────────────────────────────────────────────────────────────

class BatchQueue:
    """Manages queued batch requests and submitted batch jobs.

    Thread-safe. Designed to be called from the daemon main loop
    and from worker threads (via submit()).
    """

    def __init__(self, config: dict | None = None):
        self._config = config or load_batch_config()
        self._pending: list[BatchRequest] = []
        self._jobs: dict[str, BatchJob] = {}
        self._callbacks: dict = {}
        self._results: dict[str, tuple] = {}  # request_id → (content, success)
        self._events: dict[str, threading.Event] = {}  # request_id → event
        self._lock = threading.Lock()
        self._stats = {"submitted": 0, "completed": 0, "failed": 0, "fallback": 0}
        os.makedirs(BATCH_DIR, exist_ok=True)
        self._load_persisted()

    @property
    def enabled(self) -> bool:
        return self._config.get("enabled", False)

    @property
    def stats(self) -> dict:
        return dict(self._stats)

    def submit(self, request: BatchRequest, callback=None) -> str:
        """Queue a request for batch submission.

        Args:
            request: The batch request to queue.
            callback: Optional callback(content: str, success: bool) for async results.

        Returns:
            request_id for tracking.
        """
        with self._lock:
            self._pending.append(request)
            if callback:
                self._callbacks[request.request_id] = callback

            # Create event for synchronous waiters
            event = threading.Event()
            self._events[request.request_id] = event

            # Check flush trigger
            if self._should_flush():
                self._flush()

        return request.request_id

    def wait_for_result(self, request_id: str, timeout: float = None) -> tuple[str, bool]:
        """Block until a batch result is ready.

        Args:
            request_id: The request to wait for.
            timeout: Max wait seconds (default: from config).

        Returns:
            (content, success) tuple.
        """
        if timeout is None:
            timeout = DEFAULT_REQUEST_TIMEOUT_SEC

        event = self._events.get(request_id)
        if not event:
            return ("", False)

        event.wait(timeout=timeout)

        with self._lock:
            result = self._results.pop(request_id, ("", False))
            self._events.pop(request_id, None)
            return result

    def poll(self) -> None:
        """Check queue age and batch job statuses. Called by daemon every cycle."""
        with self._lock:
            if self._pending and self._should_flush():
                self._flush()

        # Poll active batch jobs for completion
        active_jobs = [(jid, job) for jid, job in self._jobs.items()
                       if job.status == "processing"]
        for job_id, job in active_jobs:
            self._check_job_status(job_id, job)

    def flush_now(self) -> None:
        """Force flush all pending requests immediately."""
        with self._lock:
            if self._pending:
                self._flush()

    def get_pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def get_active_jobs_count(self) -> int:
        return sum(1 for j in self._jobs.values() if j.status == "processing")

    # ─── Internal ──────────────────────────────────────────────────────────

    def _should_flush(self) -> bool:
        max_size = self._config.get("max_queue_size", DEFAULT_MAX_QUEUE_SIZE)
        max_age = self._config.get("max_queue_age_sec", DEFAULT_MAX_QUEUE_AGE_SEC)

        if len(self._pending) >= max_size:
            return True
        if self._pending and (time.time() - self._pending[0].queued_at) > max_age:
            return True
        return False

    def _flush(self) -> None:
        """Submit pending requests as batch jobs, grouped by provider."""
        if not self._pending:
            return

        # Group by (provider, model, api_base)
        groups: dict[tuple, list[BatchRequest]] = defaultdict(list)
        for req in self._pending:
            key = (req.provider.lower(), req.model, req.api_base, req.api_key)
            groups[key].append(req)

        self._pending.clear()
        self._persist_pending()

        for (provider, model, api_base, api_key), requests in groups.items():
            if provider in BATCH_PROVIDERS:
                success = self._submit_batch(provider, model, api_base, api_key, requests)
                if not success:
                    self._execute_fallback(requests)
            else:
                self._execute_fallback(requests)

    def _submit_batch(self, provider: str, model: str, api_base: str,
                      api_key: str, requests: list[BatchRequest]) -> bool:
        """Submit a batch to the provider. Returns True if submitted successfully."""
        # Build JSONL content
        jsonl_lines = []
        for req in requests:
            entry = {
                "custom_id": req.request_id,
                "method": "POST",
                "url": "/v1/chat/completions",
                "body": {
                    "model": model,
                    "messages": req.messages,
                },
            }
            if req.tools:
                entry["body"]["tools"] = req.tools
                entry["body"]["tool_choice"] = "auto"
            jsonl_lines.append(json.dumps(entry))

        jsonl_content = "\n".join(jsonl_lines)

        # Determine batch endpoint
        if "google" in provider or "gemini" in provider:
            batch_url = api_base.rstrip("/")
            if batch_url.endswith("/openai"):
                batch_url += "/batches"
            else:
                batch_url = batch_url.rstrip("/") + "/batches"
        else:
            batch_url = api_base.rstrip("/") + "/batches"

        # Step 1: Upload input file (as inline for Gemini, file upload for OpenAI)
        try:
            job_id = self._api_create_batch(batch_url, api_key, jsonl_content, model)
        except Exception as e:
            logger.warning("Batch submission failed for %s: %s", provider, e)
            return False

        if not job_id:
            return False

        # Track the batch job
        job = BatchJob(
            job_id=job_id,
            provider=provider,
            api_base=api_base,
            api_key=api_key,
            request_ids=[r.request_id for r in requests],
        )
        self._jobs[job_id] = job
        self._stats["submitted"] += len(requests)
        self._persist_jobs()

        logger.info("Batch submitted: %s (%d requests, provider=%s)",
                    job_id, len(requests), provider)
        return True

    def _api_create_batch(self, batch_url: str, api_key: str,
                          jsonl_content: str, model: str) -> str | None:
        """Create a batch job via provider API. Returns job_id or None."""
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        # Write JSONL to temp file for upload
        # Most batch APIs require a file upload first, then batch creation
        # Simplified: some providers accept inline input

        body = json.dumps({
            "input": jsonl_content.split("\n"),
            "endpoint": "/v1/chat/completions",
            "completion_window": "24h",
            "model": model,
        }).encode("utf-8")

        req = urllib.request.Request(batch_url, data=body, headers=headers, method="POST")
        context = ssl._create_unverified_context()

        try:
            response = urllib.request.urlopen(req, timeout=30, context=context)
            result = json.loads(response.read())
            return result.get("id") or result.get("batch_id") or result.get("name")
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace")[:200]
            logger.warning("Batch API error %d: %s", e.code, error_body)
            return None
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            logger.warning("Batch API connection error: %s", e)
            return None

    def _check_job_status(self, job_id: str, job: BatchJob) -> None:
        """Poll batch job status from provider."""
        # Determine status URL
        if "google" in job.provider or "gemini" in job.provider:
            status_url = job.api_base.rstrip("/")
            if status_url.endswith("/openai"):
                status_url += f"/batches/{job_id}"
            else:
                status_url = status_url.rstrip("/") + f"/batches/{job_id}"
        else:
            status_url = job.api_base.rstrip("/") + f"/batches/{job_id}"

        headers = {"Authorization": f"Bearer {job.api_key}"}
        req = urllib.request.Request(status_url, headers=headers)
        context = ssl._create_unverified_context()

        try:
            response = urllib.request.urlopen(req, timeout=10, context=context)
            result = json.loads(response.read())
        except Exception as e:
            logger.debug("Batch status poll failed for %s: %s", job_id, e)
            return

        status = result.get("status", "").lower()

        if status in ("completed", "done", "succeeded"):
            job.status = "completed"
            job.completed_at = time.time()
            self._process_completed_batch(job, result)
            self._stats["completed"] += len(job.request_ids)
            self._persist_jobs()

        elif status in ("failed", "expired", "cancelled", "error"):
            job.status = "failed"
            job.error = result.get("error", {}).get("message", status)
            job.completed_at = time.time()
            self._handle_failed_batch(job)
            self._stats["failed"] += len(job.request_ids)
            self._persist_jobs()

    def _process_completed_batch(self, job: BatchJob, result: dict) -> None:
        """Parse batch results and deliver to callbacks/events."""
        # Try to get output from various response formats
        output_file_id = result.get("output_file_id")
        output = result.get("output", [])
        responses = result.get("responses", [])

        # If we have an output_file_id, download it
        if output_file_id and not output and not responses:
            output = self._download_batch_output(job, output_file_id)

        # Combine possible response locations
        all_results = output or responses

        # Parse results — each entry has custom_id matching our request_id
        for entry in all_results:
            request_id = entry.get("custom_id") or entry.get("id", "")
            response_body = entry.get("response", {}).get("body", {})

            # Extract assistant content
            choices = response_body.get("choices", [])
            content = ""
            if choices:
                message = choices[0].get("message", {})
                content = message.get("content", "") or ""

            self._deliver_result(request_id, content, success=True)

        # Any request_ids not in results → deliver empty
        result_ids = {(e.get("custom_id") or e.get("id", "")) for e in all_results}
        for request_id in job.request_ids:
            if request_id not in result_ids:
                self._deliver_result(request_id, "", success=False)

    def _download_batch_output(self, job: BatchJob, file_id: str) -> list:
        """Download batch output file from provider."""
        if "google" in job.provider or "gemini" in job.provider:
            url = job.api_base.rstrip("/") + f"/files/{file_id}/content"
        else:
            url = job.api_base.rstrip("/") + f"/files/{file_id}/content"

        headers = {"Authorization": f"Bearer {job.api_key}"}
        req = urllib.request.Request(url, headers=headers)
        context = ssl._create_unverified_context()

        try:
            response = urllib.request.urlopen(req, timeout=60, context=context)
            content = response.read().decode("utf-8")
            # Parse JSONL output
            results = []
            for line in content.strip().split("\n"):
                if line.strip():
                    results.append(json.loads(line))
            return results
        except Exception as e:
            logger.error("Failed to download batch output %s: %s", file_id, e)
            return []

    def _handle_failed_batch(self, job: BatchJob) -> None:
        """Handle a failed batch job — deliver failures to all waiting requests."""
        logger.warning("Batch %s failed: %s", job.job_id, job.error)
        for request_id in job.request_ids:
            self._deliver_result(request_id, f"Batch failed: {job.error}", success=False)

    def _deliver_result(self, request_id: str, content: str, success: bool) -> None:
        """Deliver a result to callback and/or waiting event."""
        with self._lock:
            # Store result for synchronous waiters
            self._results[request_id] = (content, success)

            # Signal waiting event
            event = self._events.get(request_id)
            if event:
                event.set()

            # Call async callback
            callback = self._callbacks.pop(request_id, None)

        if callback:
            try:
                callback(content, success)
            except Exception as e:
                logger.error("Batch callback error for %s: %s", request_id, e)

    def _execute_fallback(self, requests: list[BatchRequest]) -> None:
        """Execute requests as individual single calls (no batch discount)."""
        from .network import query_model_api

        self._stats["fallback"] += len(requests)

        for req in requests:
            try:
                response = query_model_api(
                    req.api_base, req.api_key, req.model, req.messages,
                    protocol="openai",
                )
                self._deliver_result(req.request_id, response or "", success=True)
            except Exception as e:
                self._deliver_result(req.request_id, str(e), success=False)

    # ─── Persistence ───────────────────────────────────────────────────────

    def _persist_pending(self) -> None:
        """Save pending requests to disk (survive daemon restart)."""
        try:
            data = [r.to_dict() for r in self._pending]
            with open(PENDING_FILE, "w") as f:
                json.dump(data, f)
        except OSError:
            pass

    def _persist_jobs(self) -> None:
        """Save active jobs to disk."""
        try:
            data = {jid: j.to_dict() for jid, j in self._jobs.items()
                    if j.status == "processing"}
            with open(JOBS_FILE, "w") as f:
                json.dump(data, f)
        except OSError:
            pass

    def _load_persisted(self) -> None:
        """Load persisted state from disk on startup."""
        # Load pending requests
        if os.path.exists(PENDING_FILE):
            try:
                with open(PENDING_FILE, "r") as f:
                    data = json.load(f)
                # Note: api_keys not persisted (security) — these will fall back to single
                self._pending = [BatchRequest.from_dict(d) for d in data]
                if self._pending:
                    logger.info("Loaded %d persisted batch requests", len(self._pending))
            except (json.JSONDecodeError, OSError):
                pass

        # Load active jobs
        if os.path.exists(JOBS_FILE):
            try:
                with open(JOBS_FILE, "r") as f:
                    data = json.load(f)
                for jid, jdata in data.items():
                    self._jobs[jid] = BatchJob(
                        job_id=jdata["job_id"],
                        provider=jdata["provider"],
                        api_base=jdata["api_base"],
                        api_key="",  # Not persisted
                        request_ids=jdata["request_ids"],
                        status=jdata["status"],
                        submitted_at=jdata.get("submitted_at", 0),
                    )
                if self._jobs:
                    logger.info("Loaded %d persisted batch jobs", len(self._jobs))
            except (json.JSONDecodeError, OSError):
                pass


# ─── Global Batch Queue Instance ───────────────────────────────────────────────

_global_batch_queue: BatchQueue | None = None
_queue_lock = threading.Lock()


def get_batch_queue() -> BatchQueue | None:
    """Get the global batch queue instance. Returns None if batch is disabled."""
    global _global_batch_queue
    with _queue_lock:
        if _global_batch_queue is None:
            config = load_batch_config()
            if config.get("enabled", False):
                _global_batch_queue = BatchQueue(config)
                logger.info("Batch queue initialized (max_size=%d, max_age=%ds)",
                            config.get("max_queue_size", DEFAULT_MAX_QUEUE_SIZE),
                            config.get("max_queue_age_sec", DEFAULT_MAX_QUEUE_AGE_SEC))
            else:
                return None
        return _global_batch_queue


def is_batch_eligible(model_config: dict) -> bool:
    """Check if a model supports batch API calls.

    Conditions:
    - Batch is globally enabled
    - Provider supports batch (Gemini, OpenAI)
    - Not a local model (free anyway)
    - Model has batch_enabled flag (opt-in per model)
    """
    # Global check
    config = load_batch_config()
    if not config.get("enabled", False):
        return False

    provider = model_config.get("provider", "").lower()

    # Only providers with batch endpoints
    if provider not in BATCH_PROVIDERS:
        return False

    # Local models are free — no point batching
    api_base = model_config.get("api_base_url", "")
    if "localhost" in api_base or "127.0.0.1" in api_base:
        return False

    # Per-model opt-in
    return model_config.get("batch_enabled", False)


def submit_for_batch(
    model_config: dict,
    messages: list,
    tools: list | None = None,
    request_id: str | None = None,
    callback=None,
) -> tuple[str, bool]:
    """Submit a request to batch queue if eligible, else return (None, False).

    Args:
        model_config: Model configuration dict.
        messages: Chat messages for the LLM.
        tools: Optional tool definitions.
        request_id: Custom request ID (auto-generated if None).
        callback: Optional async callback(content, success).

    Returns:
        (request_id, submitted) — submitted=True if queued for batch,
        False if not eligible (caller should use single call).
    """
    queue = get_batch_queue()
    if not queue or not is_batch_eligible(model_config):
        return ("", False)

    if not request_id:
        request_id = f"batch_{int(time.time() * 1000)}_{threading.current_thread().ident}"

    req = BatchRequest(
        request_id=request_id,
        model=model_config.get("name", ""),
        provider=model_config.get("provider", ""),
        api_base=model_config.get("api_base_url", ""),
        api_key=model_config.get("api_key", ""),
        messages=messages,
        tools=tools,
    )

    queue.submit(req, callback=callback)
    return (request_id, True)


def wait_batch_result(request_id: str, timeout: float = None) -> tuple[str, bool]:
    """Wait for a batch result synchronously.

    Args:
        request_id: The request to wait for.
        timeout: Max seconds to wait.

    Returns:
        (content, success) tuple.
    """
    queue = get_batch_queue()
    if not queue:
        return ("", False)
    return queue.wait_for_result(request_id, timeout=timeout)
