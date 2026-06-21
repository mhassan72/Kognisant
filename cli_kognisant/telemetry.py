"""Telemetry recording, rotation, aggregation, and formatting.

Implements per-execution recording to ~/.kognisant_core/telemetry.jsonl,
file rotation, and the /telemetry command output formatting.
"""

import json
import logging
import os
import time
from collections import defaultdict

logger = logging.getLogger(__name__)

TELEMETRY_DIR = os.path.expanduser("~/.kognisant_core")
TELEMETRY_FILE = os.path.join(TELEMETRY_DIR, "telemetry.jsonl")
TELEMETRY_BACKUP = os.path.join(TELEMETRY_DIR, "telemetry.1.jsonl")
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB


def estimate_tokens(text: str) -> int:
    """Estimate token count for a text string.

    Uses len(text) // 4 as a stdlib-only heuristic.
    """
    if not text:
        return 0
    return len(text) // 4


def compute_token_breakdown(
    system_prompt: str,
    tools_json: str | None,
    history_msgs: list[dict],
    user_msg: str,
) -> dict:
    """Compute token estimates for each component of an LLM request.

    Args:
        system_prompt: The system prompt text.
        tools_json: JSON string of tools definition, or None if no tools.
        history_msgs: List of message dicts with 'content' keys.
        user_msg: The user's message text.

    Returns:
        Dict with keys: system, tools, history, user_message, total.
    """
    system = estimate_tokens(system_prompt)
    tools = estimate_tokens(tools_json) if tools_json else 0
    history = sum(
        estimate_tokens(msg.get("content", "") or "")
        for msg in history_msgs
    )
    user_message = estimate_tokens(user_msg)
    total = system + tools + history + user_message

    return {
        "system": system,
        "tools": tools,
        "history": history,
        "user_message": user_message,
        "total": total,
    }


def append_telemetry(record: dict) -> None:
    """Append a telemetry record as a JSON line to telemetry.jsonl.

    Never raises — all exceptions are caught and logged as warnings.
    """
    try:
        os.makedirs(TELEMETRY_DIR, exist_ok=True)
        rotate_if_needed()
        line = json.dumps(record, default=str) + "\n"
        with open(TELEMETRY_FILE, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        logger.warning(f"Failed to write telemetry: {e}")


def rotate_if_needed() -> None:
    """Rotate telemetry file if it exceeds 5MB.

    Renames telemetry.jsonl to telemetry.1.jsonl (overwrites old backup).
    """
    try:
        if os.path.exists(TELEMETRY_FILE):
            size = os.path.getsize(TELEMETRY_FILE)
            if size > MAX_FILE_SIZE:
                # Overwrite old backup
                if os.path.exists(TELEMETRY_BACKUP):
                    os.remove(TELEMETRY_BACKUP)
                os.rename(TELEMETRY_FILE, TELEMETRY_BACKUP)
    except Exception as e:
        logger.warning(f"Failed to rotate telemetry: {e}")


def load_recent_telemetry(count: int = 50) -> list[dict]:
    """Load the last N telemetry records from the JSONL file.

    Skips unparseable lines. Returns empty list if file doesn't exist.
    """
    if not os.path.exists(TELEMETRY_FILE):
        return []

    try:
        with open(TELEMETRY_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception as e:
        logger.warning(f"Failed to read telemetry: {e}")
        return []

    # Take last N lines
    recent_lines = lines[-count:] if len(lines) > count else lines

    records = []
    for line in recent_lines:
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except (json.JSONDecodeError, ValueError):
            continue

    return records


def aggregate_telemetry(records: list[dict]) -> dict:
    """Aggregate telemetry records into summary statistics.

    Returns dict with:
        total, success_count, avg_response_time, total_tokens_in,
        total_tokens_out, per_model, per_classification, tool_usage,
        valence_trend.
    """
    if not records:
        return {
            "total": 0,
            "success_count": 0,
            "avg_response_time": 0.0,
            "total_tokens_in": 0,
            "total_tokens_out": 0,
            "per_model": {},
            "per_classification": {},
            "tool_usage": {},
            "valence_trend": {"first": 0, "last": 0, "delta": 0},
        }

    total = len(records)
    success_count = sum(1 for r in records if r.get("success", False))
    response_times = [r.get("response_time_ms", 0) for r in records if r.get("response_time_ms")]
    avg_response_time = sum(response_times) / len(response_times) if response_times else 0.0
    total_tokens_in = sum(r.get("tokens_in", 0) for r in records)
    total_tokens_out = sum(r.get("tokens_out", 0) for r in records)

    # Per-model breakdown
    per_model: dict = defaultdict(lambda: {
        "calls": 0, "successes": 0, "total_time": 0.0, "tokens_in": 0, "tokens_out": 0,
    })
    for r in records:
        model = r.get("model", "unknown")
        per_model[model]["calls"] += 1
        if r.get("success", False):
            per_model[model]["successes"] += 1
        per_model[model]["total_time"] += r.get("response_time_ms", 0)
        per_model[model]["tokens_in"] += r.get("tokens_in", 0)
        per_model[model]["tokens_out"] += r.get("tokens_out", 0)

    # Compute derived fields
    model_stats = {}
    for model, stats in per_model.items():
        calls = stats["calls"]
        model_stats[model] = {
            "calls": calls,
            "success_rate": stats["successes"] / calls if calls else 0.0,
            "avg_time": stats["total_time"] / calls if calls else 0.0,
            "tokens_in": stats["tokens_in"],
            "tokens_out": stats["tokens_out"],
        }

    # Per-classification breakdown
    per_classification: dict = defaultdict(lambda: {
        "count": 0, "total_tokens": 0, "total_time": 0.0,
    })
    for r in records:
        cls = r.get("classification", "unknown")
        per_classification[cls]["count"] += 1
        per_classification[cls]["total_tokens"] += r.get("tokens_in", 0) + r.get("tokens_out", 0)
        per_classification[cls]["total_time"] += r.get("response_time_ms", 0)

    classification_stats = {}
    for cls, stats in per_classification.items():
        count = stats["count"]
        classification_stats[cls] = {
            "count": count,
            "avg_tokens": stats["total_tokens"] / count if count else 0,
            "avg_time": stats["total_time"] / count if count else 0.0,
        }

    # Tool usage breakdown
    tool_usage: dict = defaultdict(lambda: {"calls": 0, "successes": 0})
    for r in records:
        for tc in r.get("tool_calls", []):
            name = tc.get("name", "unknown")
            tool_usage[name]["calls"] += 1
            if tc.get("success", False):
                tool_usage[name]["successes"] += 1

    tool_stats = {}
    for name, stats in tool_usage.items():
        calls = stats["calls"]
        tool_stats[name] = {
            "calls": calls,
            "success_rate": stats["successes"] / calls if calls else 0.0,
        }

    # Valence trend
    first_valence = records[0].get("valence_after", 0)
    last_valence = records[-1].get("valence_after", 0)

    return {
        "total": total,
        "success_count": success_count,
        "avg_response_time": avg_response_time,
        "total_tokens_in": total_tokens_in,
        "total_tokens_out": total_tokens_out,
        "per_model": model_stats,
        "per_classification": classification_stats,
        "tool_usage": tool_stats,
        "valence_trend": {
            "first": first_valence,
            "last": last_valence,
            "delta": last_valence - first_valence,
        },
    }


def format_telemetry_summary(records: list[dict]) -> str:
    """Format aggregated telemetry for terminal display (/telemetry command).

    Returns a formatted multi-line string summarizing the last N executions.
    """
    if not records:
        return "No telemetry data available."

    agg = aggregate_telemetry(records)

    lines = []
    lines.append(f"═══ Telemetry Summary (last {agg['total']} executions) ═══")
    lines.append("")

    # Overall stats
    success_rate = (agg["success_count"] / agg["total"] * 100) if agg["total"] else 0
    lines.append(f"  Total: {agg['total']}  |  Success: {agg['success_count']} ({success_rate:.0f}%)")
    lines.append(f"  Avg response time: {agg['avg_response_time']:.0f}ms")
    lines.append(f"  Tokens: {agg['total_tokens_in']:,} in  |  {agg['total_tokens_out']:,} out")
    lines.append("")

    # Model breakdown
    if agg["per_model"]:
        lines.append("  Models:")
        for model, stats in sorted(agg["per_model"].items()):
            rate = stats["success_rate"] * 100
            lines.append(
                f"    {model}: {stats['calls']} calls, "
                f"{rate:.0f}% success, "
                f"{stats['avg_time']:.0f}ms avg"
            )
        lines.append("")

    # Classification breakdown
    if agg["per_classification"]:
        lines.append("  Classifications:")
        for cls, stats in sorted(agg["per_classification"].items()):
            lines.append(
                f"    {cls}: {stats['count']} calls, "
                f"~{stats['avg_tokens']:.0f} tokens avg, "
                f"{stats['avg_time']:.0f}ms avg"
            )
        lines.append("")

    # Tool usage
    if agg["tool_usage"]:
        lines.append("  Tools:")
        for name, stats in sorted(agg["tool_usage"].items()):
            rate = stats["success_rate"] * 100
            lines.append(f"    {name}: {stats['calls']} calls, {rate:.0f}% success")
        lines.append("")

    # Valence trend
    trend = agg["valence_trend"]
    direction = "↑" if trend["delta"] > 0 else "↓" if trend["delta"] < 0 else "→"
    lines.append(f"  Valence trend: {trend['first']:+d} {direction} {trend['last']:+d} (Δ{trend['delta']:+d})")

    return "\n".join(lines)


def format_model_telemetry(records: list[dict], model_name: str) -> str:
    """Format per-model deep dive telemetry for /telemetry <model> command.

    Filters records to the specified model and provides detailed statistics.
    """
    model_records = [r for r in records if r.get("model", "") == model_name]

    if not model_records:
        return f"No telemetry data for model '{model_name}'."

    total = len(model_records)
    successes = sum(1 for r in model_records if r.get("success", False))
    success_rate = (successes / total * 100) if total else 0

    response_times = [r.get("response_time_ms", 0) for r in model_records if r.get("response_time_ms")]
    avg_time = sum(response_times) / len(response_times) if response_times else 0
    fastest = min(response_times) if response_times else 0
    slowest = max(response_times) if response_times else 0

    tokens_in = sum(r.get("tokens_in", 0) for r in model_records)
    tokens_out = sum(r.get("tokens_out", 0) for r in model_records)
    avg_tokens_in = tokens_in / total if total else 0
    avg_tokens_out = tokens_out / total if total else 0

    timeouts = sum(1 for r in model_records if r.get("timed_out", False))
    empty_responses = sum(1 for r in model_records if r.get("error") and "empty" in str(r.get("error", "")).lower())

    # Get reliability from most recent record
    reliability = model_records[-1].get("model_reliability_after", "N/A")

    # Circuit breaker state from most recent record
    cb_state = model_records[-1].get("circuit_breaker_state", "closed")

    lines = []
    lines.append(f"═══ Model: {model_name} ═══")
    lines.append("")
    lines.append(f"  Total calls: {total}")
    lines.append(f"  Success rate: {success_rate:.0f}% ({successes}/{total})")
    lines.append(f"  Reliability: {reliability}")
    lines.append("")
    lines.append(f"  Response time:")
    lines.append(f"    Average: {avg_time:.0f}ms")
    lines.append(f"    Fastest: {fastest:.0f}ms")
    lines.append(f"    Slowest: {slowest:.0f}ms")
    lines.append("")
    lines.append(f"  Tokens:")
    lines.append(f"    Avg in: {avg_tokens_in:.0f}  |  Avg out: {avg_tokens_out:.0f}")
    lines.append(f"    Total in: {tokens_in:,}  |  Total out: {tokens_out:,}")
    lines.append("")
    lines.append(f"  Issues:")
    lines.append(f"    Timeouts: {timeouts}")
    lines.append(f"    Empty responses: {empty_responses}")
    lines.append("")
    lines.append(f"  Circuit breaker: {cb_state}")

    return "\n".join(lines)
