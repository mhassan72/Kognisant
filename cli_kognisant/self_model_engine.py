"""SelfModel Engine — cognitive state persistence and Bayesian reliability tracking."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


@dataclass
class ModelReliability:
    """Per-model reliability tracking using Bayesian confidence."""

    successes: int = 0
    failures: int = 0
    reliability: float = 0.5  # Bayesian: (s+1)/(s+f+2)
    attempts: int = 0
    avg_response_time: float = 0.0
    last_success_at: str | None = None
    last_failure_at: str | None = None
    token_calibration: float = 1.0  # Estimate correction factor
    capabilities: dict = field(default_factory=lambda: {"tool_calling": True})


@dataclass
class CircuitBreakerState:
    """Per-model circuit breaker state: CLOSED | OPEN | HALF_OPEN."""

    state: str = "closed"  # "closed" | "open" | "half_open"
    failures_in_window: int = 0
    window_start: str | None = None
    open_until: str | None = None


@dataclass
class ToolReliability:
    """Per-tool reliability tracking using Bayesian confidence."""

    successes: int = 0
    failures: int = 0
    reliability: float = 0.5  # Bayesian: (s+1)/(s+f+2)


@dataclass
class SelfModel:
    """Root cognitive state persisted to ~/.kognisant_core/self_model.json."""

    version: int = 1
    valence: int = 0
    frustration: float = 0.0
    total_executions: int = 0
    consecutive_failures: int = 0
    last_execution_at: str | None = None
    model_reliability: dict[str, ModelReliability] = field(default_factory=dict)
    tool_reliability: dict[str, ToolReliability] = field(default_factory=dict)
    circuit_breakers: dict[str, CircuitBreakerState] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEFAULT_PATH = os.path.expanduser("~/.kognisant_core/self_model.json")

# Circuit breaker constants
_CB_FAILURE_THRESHOLD = 5
_CB_WINDOW_SECONDS = 30.0
_CB_COOLDOWN_SECONDS = 30.0


def _now_iso() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(iso_str: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp string, returning None on failure."""
    if not iso_str:
        return None
    try:
        return datetime.fromisoformat(iso_str)
    except (ValueError, TypeError):
        return None


def _seconds_since(iso_str: str | None) -> float:
    """Return seconds elapsed since the given ISO timestamp (0 if None/invalid)."""
    dt = _parse_iso(iso_str)
    if dt is None:
        return 0.0
    now = datetime.now(timezone.utc)
    return max(0.0, (now - dt).total_seconds())


def _bayesian_reliability(successes: int, failures: int) -> float:
    """Compute Bayesian reliability: (s+1) / (s+f+2)."""
    return (successes + 1) / (successes + failures + 2)


# ---------------------------------------------------------------------------
# SelfModelEngine
# ---------------------------------------------------------------------------


class SelfModelEngine:
    """Manages SelfModel persistence, Bayesian updates, circuit breakers, and model selection."""

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    @staticmethod
    def load(path: str | None = None) -> SelfModel:
        """Load SelfModel from disk, creating with safe defaults if absent.

        Args:
            path: File path to self_model.json. Defaults to ~/.kognisant_core/self_model.json.
        """
        path = path or _DEFAULT_PATH
        if not os.path.exists(path):
            return SelfModel()

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return SelfModel()

        return SelfModelEngine._dict_to_model(data)

    @staticmethod
    def save(model: SelfModel, path: str | None = None) -> None:
        """Atomically write SelfModel to disk (tmp + fsync + rename).

        Args:
            model: The SelfModel instance to persist.
            path: Target file path. Defaults to ~/.kognisant_core/self_model.json.
        """
        path = path or _DEFAULT_PATH
        directory = os.path.dirname(path)
        os.makedirs(directory, exist_ok=True)

        data = SelfModelEngine._model_to_dict(model)
        json_bytes = json.dumps(data, indent=2).encode("utf-8")

        # Atomic write: write to temp, fsync, rename over target
        fd, tmp_path = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            os.write(fd, json_bytes)
            os.fsync(fd)
            os.close(fd)
            os.replace(tmp_path, path)
        except Exception:
            os.close(fd) if not os.path.exists(tmp_path) else None
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

    # ------------------------------------------------------------------
    # Temporal Decay (R1.7)
    # ------------------------------------------------------------------

    @staticmethod
    def apply_decay(model: SelfModel) -> None:
        """Apply temporal decay based on time since last execution.

        - Valence decays 10% toward 0 per calendar day.
        - Frustration halves every 24 hours.
        """
        if model.last_execution_at is None:
            return

        elapsed_seconds = _seconds_since(model.last_execution_at)
        days_elapsed = elapsed_seconds / 86400.0

        if days_elapsed <= 0:
            return

        # Valence: decays 10% toward 0 per day → multiply by (0.9 ^ days)
        decay_factor = 0.9 ** days_elapsed
        model.valence = int(model.valence * decay_factor)

        # Frustration: halves every 24 hours → multiply by (0.5 ^ days)
        frustration_factor = 0.5 ** days_elapsed
        model.frustration = model.frustration * frustration_factor

    # ------------------------------------------------------------------
    # Bayesian Updates (R1.2, R1.6)
    # ------------------------------------------------------------------

    @staticmethod
    def record_success(model: SelfModel, model_name: str, response_time: float = 0.0) -> None:
        """Record a successful model execution with Bayesian reliability update.

        Args:
            model: The SelfModel to update.
            model_name: Name of the model that succeeded.
            response_time: Time taken in seconds for the response.
        """
        rel = SelfModelEngine._ensure_model_reliability(model, model_name)
        rel.successes += 1
        rel.attempts += 1
        rel.reliability = _bayesian_reliability(rel.successes, rel.failures)
        rel.last_success_at = _now_iso()

        # Update rolling average response time
        if rel.avg_response_time == 0.0:
            rel.avg_response_time = response_time
        else:
            rel.avg_response_time = rel.avg_response_time * 0.8 + response_time * 0.2

        model.consecutive_failures = 0

    @staticmethod
    def record_failure(model: SelfModel, model_name: str) -> None:
        """Record a failed model execution with Bayesian reliability update.

        Args:
            model: The SelfModel to update.
            model_name: Name of the model that failed.
        """
        rel = SelfModelEngine._ensure_model_reliability(model, model_name)
        rel.failures += 1
        rel.attempts += 1
        rel.reliability = _bayesian_reliability(rel.successes, rel.failures)
        rel.last_failure_at = _now_iso()

        model.consecutive_failures += 1

    @staticmethod
    def record_tool_result(model: SelfModel, tool_name: str, success: bool) -> None:
        """Record a tool execution result with Bayesian reliability update.

        Args:
            model: The SelfModel to update.
            tool_name: Name of the tool.
            success: Whether the tool call succeeded.
        """
        if tool_name not in model.tool_reliability:
            model.tool_reliability[tool_name] = ToolReliability()

        tool_rel = model.tool_reliability[tool_name]
        if success:
            tool_rel.successes += 1
        else:
            tool_rel.failures += 1
        tool_rel.reliability = _bayesian_reliability(tool_rel.successes, tool_rel.failures)

    # ------------------------------------------------------------------
    # Valence (R1.3)
    # ------------------------------------------------------------------

    @staticmethod
    def update_valence(model: SelfModel, delta: int) -> None:
        """Update valence by delta, clamping to [-100, +100].

        Args:
            model: The SelfModel to update.
            delta: Amount to change valence by.
        """
        model.valence = max(-100, min(100, model.valence + delta))

    # ------------------------------------------------------------------
    # Token Calibration (R11.1)
    # ------------------------------------------------------------------

    @staticmethod
    def update_token_calibration(
        model: SelfModel, model_name: str, actual_tokens: int, estimated_tokens: int
    ) -> None:
        """Update per-model token calibration with rolling average.

        Formula: calibration = calibration * 0.8 + ratio * 0.2
        Where ratio = actual / estimated.

        Args:
            model: The SelfModel to update.
            model_name: Name of the model.
            actual_tokens: Actual token count from API response.
            estimated_tokens: Our estimated token count.
        """
        if estimated_tokens <= 0:
            return

        rel = SelfModelEngine._ensure_model_reliability(model, model_name)
        ratio = actual_tokens / estimated_tokens
        rel.token_calibration = rel.token_calibration * 0.8 + ratio * 0.2

    # ------------------------------------------------------------------
    # Background Signals (R1.3)
    # ------------------------------------------------------------------

    @staticmethod
    def compute_background_signals(
        model: SelfModel,
        failed_jobs_count: int = 0,
        world_model_stale: bool = False,
        goal_acceptance_rate: float = 1.0,
    ) -> int:
        """Compute background valence pressure (max -5 combined).

        Signals:
        - Avg tool reliability < 0.5: -2
        - Failed jobs present: -1 per failed job
        - World model stale (>24h): -1
        - Goal acceptance rate < 0.3: -1

        Args:
            model: The SelfModel to read tool reliabilities from.
            failed_jobs_count: Number of currently failed jobs.
            world_model_stale: Whether the world model is older than 24h.
            goal_acceptance_rate: Fraction of goals accepted (0.0 to 1.0).

        Returns:
            Combined pressure value (negative int, min -5).
        """
        pressure = 0

        # Avg tool reliability < 0.5 → -2
        if model.tool_reliability:
            avg_tool_rel = sum(
                t.reliability for t in model.tool_reliability.values()
            ) / len(model.tool_reliability)
            if avg_tool_rel < 0.5:
                pressure -= 2

        # Failed jobs: -1 per failed job
        pressure -= failed_jobs_count

        # World model stale → -1
        if world_model_stale:
            pressure -= 1

        # Goal acceptance rate < 0.3 → -1
        if goal_acceptance_rate < 0.3:
            pressure -= 1

        # Cap at -5
        return max(-5, pressure)

    # ------------------------------------------------------------------
    # Circuit Breaker (R1.4)
    # ------------------------------------------------------------------

    @staticmethod
    def cb_can_attempt(cb: CircuitBreakerState) -> bool:
        """Check if the circuit breaker allows an attempt.

        - CLOSED: always allowed.
        - HALF_OPEN: allowed (exactly one test attempt).
        - OPEN: allowed only if cooldown has expired (transitions to HALF_OPEN).
        """
        if cb.state == "closed":
            return True
        if cb.state == "half_open":
            return True
        if cb.state == "open":
            # Check if cooldown has expired
            if cb.open_until:
                open_until_dt = _parse_iso(cb.open_until)
                if open_until_dt and datetime.now(timezone.utc) >= open_until_dt:
                    # Transition to half_open
                    cb.state = "half_open"
                    return True
            return False
        return False

    @staticmethod
    def cb_record_failure(cb: CircuitBreakerState) -> None:
        """Record a failure in the circuit breaker.

        - In CLOSED: increment failures_in_window. If threshold reached within
          window, transition to OPEN with cooldown.
        - In HALF_OPEN: transition back to OPEN (restart cooldown).
        """
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()

        if cb.state == "half_open":
            # Failed the test attempt → back to OPEN
            cb.state = "open"
            cb.open_until = (
                datetime.now(timezone.utc).__class__(
                    *now.timetuple()[:6], tzinfo=timezone.utc
                )
            ).isoformat()
            # Set open_until to now + cooldown
            from datetime import timedelta

            cb.open_until = (now + timedelta(seconds=_CB_COOLDOWN_SECONDS)).isoformat()
            cb.failures_in_window = 0
            cb.window_start = None
            return

        # CLOSED state
        if cb.state == "closed":
            # Check if window has expired
            if cb.window_start:
                window_start_dt = _parse_iso(cb.window_start)
                if window_start_dt:
                    elapsed = (now - window_start_dt).total_seconds()
                    if elapsed > _CB_WINDOW_SECONDS:
                        # Reset window
                        cb.failures_in_window = 0
                        cb.window_start = now_iso

            if not cb.window_start:
                cb.window_start = now_iso

            cb.failures_in_window += 1

            if cb.failures_in_window >= _CB_FAILURE_THRESHOLD:
                # Trip the circuit breaker
                from datetime import timedelta

                cb.state = "open"
                cb.open_until = (now + timedelta(seconds=_CB_COOLDOWN_SECONDS)).isoformat()
                cb.failures_in_window = 0
                cb.window_start = None

    @staticmethod
    def cb_record_success(cb: CircuitBreakerState) -> None:
        """Record a success in the circuit breaker.

        - In HALF_OPEN: transition to CLOSED.
        - In CLOSED: reset failure window.
        """
        if cb.state == "half_open":
            cb.state = "closed"
            cb.failures_in_window = 0
            cb.window_start = None
            cb.open_until = None
        elif cb.state == "closed":
            # Reset window on success
            cb.failures_in_window = 0
            cb.window_start = None

    # ------------------------------------------------------------------
    # Model Selection (R1.5)
    # ------------------------------------------------------------------

    @staticmethod
    def select_model(
        model: SelfModel,
        default_model_name: str,
        available_models: list[str],
    ) -> tuple[str, bool, str]:
        """Select the best model to use based on circuit breaker state and reliability.

        Priority:
        1. Default model if circuit breaker is CLOSED.
        2. Default model if circuit breaker is HALF_OPEN (one test attempt).
        3. Best alternative with reliability > 0.5 (if default is OPEN or unreliable).
        4. Default model anyway with warning (no better option).

        Args:
            model: The SelfModel with reliability and circuit breaker data.
            default_model_name: The configured default model name.
            available_models: List of all available model names.

        Returns:
            Tuple of (selected_model_name, auto_switched, reason).
        """
        # Ensure circuit breaker exists for default
        if default_model_name not in model.circuit_breakers:
            model.circuit_breakers[default_model_name] = CircuitBreakerState()

        default_cb = model.circuit_breakers[default_model_name]

        # Priority 1: Default is CLOSED → use it
        if default_cb.state == "closed":
            return (default_model_name, False, "")

        # Priority 2: Default is HALF_OPEN → use it for a test
        if default_cb.state == "half_open":
            return (default_model_name, False, "")

        # Priority 3: Default is OPEN — check if cooldown expired
        if default_cb.state == "open":
            if SelfModelEngine.cb_can_attempt(default_cb):
                # Cooldown expired, now half_open
                return (default_model_name, False, "")

            # Look for best alternative with reliability > 0.5
            best_alt = None
            best_rel = 0.0
            for alt_name in available_models:
                if alt_name == default_model_name:
                    continue
                # Ensure circuit breaker allows attempt
                if alt_name not in model.circuit_breakers:
                    model.circuit_breakers[alt_name] = CircuitBreakerState()
                alt_cb = model.circuit_breakers[alt_name]
                if not SelfModelEngine.cb_can_attempt(alt_cb):
                    continue

                alt_rel = model.model_reliability.get(alt_name)
                if alt_rel and alt_rel.reliability > 0.5 and alt_rel.reliability > best_rel:
                    best_alt = alt_name
                    best_rel = alt_rel.reliability

            if best_alt:
                reason = (
                    f"{default_model_name} circuit breaker OPEN; "
                    f"using {best_alt} (reliability: {best_rel:.2f})"
                )
                return (best_alt, True, reason)

            # Priority 4: No better option — use default with warning
            reason = f"{default_model_name} circuit breaker OPEN but no reliable alternative available"
            return (default_model_name, False, reason)

        # Fallback
        return (default_model_name, False, "")

    # ------------------------------------------------------------------
    # Capability Scan (R1.8)
    # ------------------------------------------------------------------

    @staticmethod
    def scan_capabilities(project_path: str | None = None) -> dict:
        """Scan and count available capabilities for system prompt construction.

        Counts: scripts (.py in scripts/), skills (.md in skills/),
        tools (.json in tools/), active jobs from jobs.json,
        registered projects, specs, goals, session history.

        Args:
            project_path: Path to the project root (with .kognisant/ directory).

        Returns:
            Dict with counts of each capability type.
        """
        result = {
            "scripts_count": 0,
            "skills_count": 0,
            "custom_tools_count": 0,
            "active_jobs_count": 0,
            "registered_projects_count": 0,
            "specs_count": 0,
            "goals_count": 0,
            "session_history_count": 0,
        }

        core_dir = os.path.expanduser("~/.kognisant_core")

        # Scripts: .py files in scripts/ directory
        scripts_dir = os.path.join(core_dir, "scripts")
        if os.path.isdir(scripts_dir):
            result["scripts_count"] = len(
                [f for f in os.listdir(scripts_dir) if f.endswith(".py")]
            )

        # Skills: .md files in skills/ directory
        skills_dir = os.path.join(core_dir, "skills")
        if os.path.isdir(skills_dir):
            result["skills_count"] = len(
                [f for f in os.listdir(skills_dir) if f.endswith(".md")]
            )

        # Tools: .json files in tools/ directory
        tools_dir = os.path.join(core_dir, "tools")
        if os.path.isdir(tools_dir):
            result["custom_tools_count"] = len(
                [f for f in os.listdir(tools_dir) if f.endswith(".json")]
            )

        # Active jobs from jobs.json
        jobs_file = os.path.join(core_dir, "jobs.json")
        if os.path.isfile(jobs_file):
            try:
                with open(jobs_file, "r", encoding="utf-8") as f:
                    jobs_data = json.load(f)
                if isinstance(jobs_data, list):
                    result["active_jobs_count"] = len(
                        [j for j in jobs_data if j.get("status") == "active"]
                    )
                elif isinstance(jobs_data, dict):
                    jobs_list = jobs_data.get("jobs", [])
                    result["active_jobs_count"] = len(
                        [j for j in jobs_list if j.get("status") == "active"]
                    )
            except (json.JSONDecodeError, OSError):
                pass

        # Project-specific capabilities
        if project_path:
            kognisant_dir = os.path.join(project_path, ".kognisant")

            # Specs
            specs_dir = os.path.join(kognisant_dir, "specs")
            if os.path.isdir(specs_dir):
                result["specs_count"] = len(
                    [d for d in os.listdir(specs_dir) if os.path.isdir(os.path.join(specs_dir, d))]
                )

            # Goals
            goals_file = os.path.join(kognisant_dir, "goals.json")
            if os.path.isfile(goals_file):
                try:
                    with open(goals_file, "r", encoding="utf-8") as f:
                        goals_data = json.load(f)
                    if isinstance(goals_data, list):
                        result["goals_count"] = len(goals_data)
                except (json.JSONDecodeError, OSError):
                    pass

            # Session history
            history_dir = os.path.join(kognisant_dir, "history")
            if os.path.isdir(history_dir):
                result["session_history_count"] = len(
                    [f for f in os.listdir(history_dir) if f.endswith(".json")]
                )

        # Registered projects
        projects_file = os.path.join(core_dir, "projects.json")
        if os.path.isfile(projects_file):
            try:
                with open(projects_file, "r", encoding="utf-8") as f:
                    projects_data = json.load(f)
                if isinstance(projects_data, list):
                    result["registered_projects_count"] = len(projects_data)
            except (json.JSONDecodeError, OSError):
                pass

        return result

    # ------------------------------------------------------------------
    # Serialization Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _model_to_dict(model: SelfModel) -> dict:
        """Convert SelfModel to a JSON-serializable dict."""
        data = {
            "version": model.version,
            "valence": model.valence,
            "frustration": model.frustration,
            "total_executions": model.total_executions,
            "consecutive_failures": model.consecutive_failures,
            "last_execution_at": model.last_execution_at,
            "model_reliability": {
                name: asdict(rel) for name, rel in model.model_reliability.items()
            },
            "tool_reliability": {
                name: asdict(rel) for name, rel in model.tool_reliability.items()
            },
            "circuit_breakers": {
                name: asdict(cb) for name, cb in model.circuit_breakers.items()
            },
        }
        return data

    @staticmethod
    def _dict_to_model(data: dict) -> SelfModel:
        """Reconstruct SelfModel from a dict (loaded from JSON)."""
        model = SelfModel(
            version=data.get("version", 1),
            valence=data.get("valence", 0),
            frustration=data.get("frustration", 0.0),
            total_executions=data.get("total_executions", 0),
            consecutive_failures=data.get("consecutive_failures", 0),
            last_execution_at=data.get("last_execution_at"),
        )

        # Model reliability
        for name, rel_data in data.get("model_reliability", {}).items():
            model.model_reliability[name] = ModelReliability(
                successes=rel_data.get("successes", 0),
                failures=rel_data.get("failures", 0),
                reliability=rel_data.get("reliability", 0.5),
                attempts=rel_data.get("attempts", 0),
                avg_response_time=rel_data.get("avg_response_time", 0.0),
                last_success_at=rel_data.get("last_success_at"),
                last_failure_at=rel_data.get("last_failure_at"),
                token_calibration=rel_data.get("token_calibration", 1.0),
                capabilities=rel_data.get("capabilities", {"tool_calling": True}),
            )

        # Tool reliability
        for name, tool_data in data.get("tool_reliability", {}).items():
            model.tool_reliability[name] = ToolReliability(
                successes=tool_data.get("successes", 0),
                failures=tool_data.get("failures", 0),
                reliability=tool_data.get("reliability", 0.5),
            )

        # Circuit breakers
        for name, cb_data in data.get("circuit_breakers", {}).items():
            model.circuit_breakers[name] = CircuitBreakerState(
                state=cb_data.get("state", "closed"),
                failures_in_window=cb_data.get("failures_in_window", 0),
                window_start=cb_data.get("window_start"),
                open_until=cb_data.get("open_until"),
            )

        return model

    @staticmethod
    def _ensure_model_reliability(model: SelfModel, model_name: str) -> ModelReliability:
        """Get or create ModelReliability entry for a given model."""
        if model_name not in model.model_reliability:
            model.model_reliability[model_name] = ModelReliability()
        return model.model_reliability[model_name]
