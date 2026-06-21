"""Reflect Engine — HOT/WARM/COLD reflection logic.

Runs after every execution to update valence, track reliability,
and produce advisory/health reports. All operations are pure computation
(no LLM calls, no network, no disk I/O) to complete in <10ms.
"""

from __future__ import annotations

from cli_kognisant.self_model_engine import ModelReliability, SelfModel, ToolReliability


# --- Valence delta rules ---

_FAST_THRESHOLD = 10.0  # seconds
_MODERATE_THRESHOLD = 30.0  # seconds


def reflect_hot(
    self_model: SelfModel,
    *,
    success: bool,
    response_time: float,
    timed_out: bool = False,
    empty: bool = False,
    cancelled: bool = False,
    error: bool = False,
    tools_used: list[dict] | None = None,
    model_name: str | None = None,
    background_pressure: int = 0,
) -> int:
    """HOT reflect — runs after every execution.

    Updates valence, model/tool reliability, and consecutive failure count.
    Returns the total valence_delta applied.

    Args:
        self_model: The current cognitive state (mutated in place).
        success: Whether the execution succeeded.
        response_time: Wall-clock seconds for the execution.
        timed_out: Whether the execution timed out.
        empty: Whether the response was empty.
        cancelled: Whether the user cancelled (Ctrl+C).
        error: Whether a generic error occurred.
        tools_used: List of tool dicts [{"name": str, "success": bool, "duration": float}].
        model_name: Name of the model used (for reliability tracking).
        background_pressure: Pre-computed background signal pressure (max -5).

    Returns:
        Total valence delta applied this execution.
    """
    tools_used = tools_used or []

    # --- Compute outcome-based valence delta ---
    if timed_out:
        valence_delta = -15
    elif empty:
        valence_delta = -10
    elif cancelled:
        valence_delta = -5
    elif error:
        valence_delta = -10
    elif success:
        if response_time < _FAST_THRESHOLD:
            valence_delta = 5
        elif response_time <= _MODERATE_THRESHOLD:
            valence_delta = 3
        else:
            valence_delta = 1
    else:
        # Generic failure not covered above
        valence_delta = -10

    # --- Apply background pressure (clamped to max -5) ---
    clamped_pressure = max(background_pressure, -5)
    if clamped_pressure < 0:
        valence_delta += clamped_pressure

    # --- Update valence (clamped to [-100, +100]) ---
    self_model.valence = max(-100, min(100, self_model.valence + valence_delta))

    # --- Update consecutive failures ---
    if success:
        self_model.consecutive_failures = 0
    else:
        self_model.consecutive_failures += 1

    # --- Update model reliability ---
    if model_name:
        if model_name not in self_model.model_reliability:
            self_model.model_reliability[model_name] = ModelReliability()
        mr = self_model.model_reliability[model_name]
        mr.attempts += 1
        if success:
            mr.successes += 1
        else:
            mr.failures += 1
        # Bayesian reliability: (s+1) / (s+f+2)
        mr.reliability = (mr.successes + 1) / (mr.successes + mr.failures + 2)
        # Update avg response time (rolling average)
        if mr.attempts == 1:
            mr.avg_response_time = response_time
        else:
            mr.avg_response_time = (
                mr.avg_response_time * (mr.attempts - 1) + response_time
            ) / mr.attempts

    # --- Update tool reliability ---
    for tool in tools_used:
        tool_name = tool.get("name", "")
        tool_success = tool.get("success", True)
        if tool_name not in self_model.tool_reliability:
            self_model.tool_reliability[tool_name] = ToolReliability()
        tr = self_model.tool_reliability[tool_name]
        if tool_success:
            tr.successes += 1
        else:
            tr.failures += 1
        tr.reliability = (tr.successes + 1) / (tr.successes + tr.failures + 2)

    # --- Increment total executions ---
    self_model.total_executions += 1

    return valence_delta


def reflect_warm(self_model: SelfModel) -> list[str]:
    """WARM reflect — runs every 3rd execution.

    Checks for consecutive failures and low-reliability models.

    Returns:
        List of advisory strings for the user.
    """
    advisories: list[str] = []

    # Check consecutive failures
    if self_model.consecutive_failures >= 3:
        advisories.append(
            "3 consecutive failures. Consider /model to switch."
        )

    # Check model reliability
    for model_name, mr in self_model.model_reliability.items():
        if mr.attempts >= 5 and mr.reliability < 0.3:
            pct = f"{mr.reliability:.0%}"
            advisories.append(
                f"{model_name} has low reliability ({pct})"
            )

    return advisories


def reflect_cold(self_model: SelfModel) -> list[str]:
    """COLD reflect — runs every 20th execution.

    Produces a health report with execution stats, per-model breakdown,
    and valence trend description.

    Returns:
        List of health report lines.
    """
    lines: list[str] = []

    # Total executions
    lines.append(f"Total executions: {self_model.total_executions}")

    # Overall success rate
    total_successes = sum(
        mr.successes for mr in self_model.model_reliability.values()
    )
    total_failures = sum(
        mr.failures for mr in self_model.model_reliability.values()
    )
    total = total_successes + total_failures
    if total > 0:
        rate = total_successes / total
        lines.append(f"Success rate: {rate:.0%}")
    else:
        lines.append("Success rate: N/A")

    # Average response time across models
    models_with_time = [
        mr for mr in self_model.model_reliability.values() if mr.attempts > 0
    ]
    if models_with_time:
        avg_time = sum(mr.avg_response_time for mr in models_with_time) / len(
            models_with_time
        )
        lines.append(f"Avg response time: {avg_time:.1f}s")
    else:
        lines.append("Avg response time: N/A")

    # Per-model breakdown
    for model_name, mr in self_model.model_reliability.items():
        lines.append(
            f"  {model_name}: {mr.successes}s/{mr.failures}f "
            f"(rel: {mr.reliability:.0%}, avg: {mr.avg_response_time:.1f}s)"
        )

    # Valence trend description
    valence = self_model.valence
    if valence >= 50:
        trend = "Excellent"
    elif valence >= 20:
        trend = "Good"
    elif valence >= 0:
        trend = "Neutral"
    elif valence >= -20:
        trend = "Declining"
    elif valence >= -50:
        trend = "Poor"
    else:
        trend = "Critical"
    lines.append(f"Valence trend: {trend} ({valence:+d})")

    return lines


def should_run_warm(total_executions: int) -> bool:
    """Return True when WARM reflect should run (every 3rd execution)."""
    return total_executions > 0 and total_executions % 3 == 0


def should_run_cold(total_executions: int) -> bool:
    """Return True when COLD reflect should run (every 20th execution)."""
    return total_executions > 0 and total_executions % 20 == 0
