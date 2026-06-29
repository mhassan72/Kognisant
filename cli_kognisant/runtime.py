"""Runtime Orchestrator — 5-phase execution lifecycle.

Implements Bootstrap → Plan → Execute → Reflect → Persist phases
for every non-slash user message. Returns ExecutionResult to chat.py.

Requirements: R3.1-R3.10, R6.1-R6.5, R8.1-R8.7, R9.1-R9.6, R10.1-R10.2,
              R11.2-R11.5, R11.7
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time
from dataclasses import dataclass, field

from .self_model_engine import (
    SelfModel,
    SelfModelEngine,
    ModelReliability,
    CircuitBreakerState,
)
from .fast_path_classifier import classify
from .reflect_engine import (
    reflect_hot,
    reflect_warm,
    reflect_cold,
    should_run_warm,
    should_run_cold,
)
from .telemetry import estimate_tokens, compute_token_breakdown, append_telemetry
from .network import query_model_api_stream, query_model_api_raw, KognisantAPIError
from .tools import get_active_tools, execute_tool
from .colors import Colors, Spinner, render_markdown
from .config import save_chat_session, load_project_context, load_project_memory_guidelines, load_global_skills


# ---------------------------------------------------------------------------
# Non-TTY detection
# ---------------------------------------------------------------------------

def _is_tty() -> bool:
    """Check if stdout is a real terminal."""
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


# ---------------------------------------------------------------------------
# ANSI Color Codes for Tool Boxes (24-bit)
# ---------------------------------------------------------------------------

_GRAY = "\033[38;2;149;165;166m"
_ORANGE = "\033[38;2;243;156;18m"
_GREEN = "\033[38;2;39;174;96m"
_RED = "\033[38;2;231;76;60m"
_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"


# ---------------------------------------------------------------------------
# Local Model Detection and Health Check
# ---------------------------------------------------------------------------

def _is_local_model(model_config: dict) -> bool:
    """Check if a model is local (Ollama or llama.cpp)."""
    protocol = model_config.get("protocol", "openai")
    return protocol in ("ollama", "llama_cpp")


def _check_local_health(model_config: dict) -> tuple[bool, str]:
    """Pre-flight health check for local model services.

    Returns (healthy, error_message). Completes in <2s.
    """
    import urllib.request
    import urllib.error

    protocol = model_config.get("protocol", "openai")
    api_base = model_config.get("api_base_url", "").rstrip("/")

    if protocol == "ollama":
        # Ollama: GET /api/tags should respond quickly
        health_url = api_base.replace("/api/chat", "").rstrip("/") + "/api/tags"
    elif protocol == "llama_cpp":
        health_url = api_base.replace("/v1/chat/completions", "").replace("/completion", "").rstrip("/") + "/health"
    else:
        return (True, "")

    try:
        req = urllib.request.Request(health_url, method="GET")
        urllib.request.urlopen(req, timeout=2.0)
        return (True, "")
    except urllib.error.URLError:
        if protocol == "ollama":
            return (False, "Ollama is not running. Start it with: ollama serve")
        else:
            return (False, "llama.cpp server is not reachable. Ensure it is running.")
    except Exception:
        return (False, "Local model service not reachable.")


# ---------------------------------------------------------------------------
# Thinking/Reasoning Step Parser
# ---------------------------------------------------------------------------

import re

_NUMBERED_STEP_PATTERN = re.compile(r"^\s*(\d+)\.\s+", re.MULTILINE)
_BULLET_STEP_PATTERN = re.compile(r"^\s*[-*]\s+", re.MULTILINE)


def _parse_reasoning_steps(raw_thinking: str) -> list[str]:
    """Parse raw thinking text into structured reasoning steps.

    Priority:
    1. Numbered patterns (1. 2. 3.) - split by those
    2. Bullet patterns (- or *) - split by those
    3. Newline-separated blocks - split by double newlines or single newlines
    4. Fallback: return the raw string as a single-element list
    """
    if not raw_thinking or not raw_thinking.strip():
        return []

    text = raw_thinking.strip()

    # 1. Check for numbered patterns
    numbered_matches = list(_NUMBERED_STEP_PATTERN.finditer(text))
    if len(numbered_matches) >= 2:
        steps = []
        for i, match in enumerate(numbered_matches):
            start = match.end()
            end = numbered_matches[i + 1].start() if i + 1 < len(numbered_matches) else len(text)
            step_text = text[start:end].strip()
            if step_text:
                steps.append(step_text)
        if steps:
            return steps

    # 2. Check for bullet patterns
    bullet_matches = list(_BULLET_STEP_PATTERN.finditer(text))
    if len(bullet_matches) >= 2:
        steps = []
        for i, match in enumerate(bullet_matches):
            start = match.end()
            end = bullet_matches[i + 1].start() if i + 1 < len(bullet_matches) else len(text)
            step_text = text[start:end].strip()
            if step_text:
                steps.append(step_text)
        if steps:
            return steps

    # 3. Newline-separated blocks
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if len(lines) >= 2:
        return lines

    # 4. Fallback: single string
    return [text]


def _save_thinking(ctx, thinking_text: str, thinking_duration_ms: float) -> None:
    """Save thinking to the session's thinking file."""
    if not thinking_text or not ctx.session_file:
        return

    try:
        reasoning_steps = _parse_reasoning_steps(thinking_text)
        turn_number = len([m for m in ctx.messages if m.get("role") == "user"])

        entry = {
            "turn": turn_number,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "model": ctx.active_model.get("name", "unknown"),
            "user_message": ctx.user_message[:200],
            "thinking_duration_ms": round(thinking_duration_ms),
            "reasoning": reasoning_steps,
        }

        # Determine thinking file path
        thinking_file = ctx.session_file.replace(".json", "_thinking.json")
        if ctx.project_info:
            history_dir = os.path.join(ctx.project_info.get("root", ""), ".kognisant", "history")
        else:
            history_dir = os.path.expanduser("~/.kognisant_core/history")

        os.makedirs(history_dir, exist_ok=True)
        thinking_path = os.path.join(history_dir, thinking_file)

        # Load existing entries or create new
        entries = []
        if os.path.exists(thinking_path):
            with open(thinking_path, "r", encoding="utf-8") as f:
                entries = json.load(f)

        entries.append(entry)

        with open(thinking_path, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2, ensure_ascii=False)
    except Exception:
        pass  # Never interrupt execution for thinking storage


# ---------------------------------------------------------------------------
# Autonomous Task Detection
# ---------------------------------------------------------------------------

_RESEARCH_VERBS = {"look", "browse", "fetch", "check", "explore", "research", "search", "find", "inspect"}
_CREATION_VERBS = {"write", "create", "generate", "build", "make", "produce", "draft", "compose", "author"}
_ANALYSIS_VERBS = {"compare", "analyze", "evaluate", "assess", "contrast", "benchmark"}

_MULTI_OUTPUT_MARKERS = [
    "then write", "then create", "and write", "and create",
    "write an article", "write a report", "create a document",
    "generate a report", "draft an article", "write a comparison",
    "write a summary", "create a plan", "build a report",
    "write documentation", "create documentation",
]


def _detect_autonomous(message: str) -> tuple[bool, str]:
    """Detect if a COMPLEX message should escalate to agent/swarm mode.

    Returns (should_escalate, reason).
    """
    words = message.lower().split()
    lower_msg = message.lower()

    # Count distinct action verb groups
    has_research = bool(set(words) & _RESEARCH_VERBS)
    has_creation = bool(set(words) & _CREATION_VERBS)
    has_analysis = bool(set(words) & _ANALYSIS_VERBS)

    distinct_phases = sum([has_research, has_creation, has_analysis])

    # Rule 1: 2+ distinct phases (research + write, or analyze + create)
    if distinct_phases >= 2:
        phases = []
        if has_research:
            phases.append("research")
        if has_analysis:
            phases.append("analysis")
        if has_creation:
            phases.append("creation")
        return (True, f"Multi-phase task detected: {' + '.join(phases)}")

    # Rule 2: URL + creation intent
    has_url = "http://" in message or "https://" in message
    if has_url and has_creation:
        return (True, "URL research + content creation")

    # Rule 3: Explicit multi-output markers
    for marker in _MULTI_OUTPUT_MARKERS:
        if marker in lower_msg:
            return (True, f"Multi-output pattern: '{marker}'")

    # Rule 4: Very long compound instruction (50+ words with conjunctions)
    if len(words) > 50:
        conjunctions = sum(1 for w in words if w in ("and", "then", "also", "after", "next"))
        if conjunctions >= 3:
            return (True, "Long compound instruction with multiple steps")

    return (False, "")


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ExecutionContext:
    """Internal context passed through all 5 phases."""

    # Input
    user_message: str
    messages: list[dict]
    model_config: dict
    project_info: dict | None
    session_file: str | None
    checkpoint_idx: int

    # Bootstrap output
    self_model: SelfModel = field(default_factory=SelfModel)
    active_model: dict = field(default_factory=dict)
    auto_switched: bool = False
    switch_reason: str = ""
    capability_snapshot: dict = field(default_factory=dict)

    # Plan output
    classification: str = ""
    system_prompt: str = ""
    api_messages: list[dict] = field(default_factory=list)
    tools: list[dict] | None = None
    timeout: int = 120
    token_breakdown: dict = field(default_factory=dict)
    total_tokens_in: int = 0

    # Execute output
    success: bool = False
    response: str = ""
    streamed: bool = False
    response_time: float = 0.0
    total_tokens_out: int = 0
    tool_calls_made: int = 0
    tools_used: list[dict] = field(default_factory=list)
    error: str | None = None
    error_type: str | None = None
    timed_out: bool = False
    cancelled: bool = False
    stalled: bool = False

    # Phase timing
    phase_times: dict = field(default_factory=dict)


@dataclass
class ExecutionResult:
    """Returned to chat.py after execute_message completes."""

    success: bool
    response: str
    streamed: bool
    error: str | None
    classification: str
    model_used: str
    response_time: float
    tool_calls_made: int
    valence_delta: int
    timed_out: bool
    cancelled: bool
    tokens_in: int
    tokens_out: int


# ---------------------------------------------------------------------------
# Tool Box Rendering Helpers
# ---------------------------------------------------------------------------

_SPINNER_CHARS = ["◐", "◓", "◑", "◒"]

_PROGRESS_VERBS = {
    "read_project_file": ("Reading", "Read", "Failed to read"),
    "read_global_file": ("Reading", "Read", "Failed to read"),
    "edit_project_file": ("Editing", "Accepted edits to", "Rejected edits to"),
    "edit_global_file": ("Editing", "Accepted edits to", "Rejected edits to"),
    "create_project_file": ("Creating", "Created", "Failed to create"),
    "create_global_file": ("Creating", "Created", "Failed to create"),
    "create_project_directory": ("Creating", "Created", "Failed to create"),
    "delete_project_path": ("Deleting", "Deleted", "Failed to delete"),
    "search_web": ("Searching", "Searched", "Failed to search"),
    "browse_web_page": ("Fetching", "Fetched", "Failed to fetch"),
    "list_project_files": ("Listing", "Listed", "Failed to list"),
    "shell_execution": ("Executing", "Executed", "Failed to execute"),
    "schedule_job": ("Scheduling", "Scheduled", "Failed to schedule"),
    "cancel_job": ("Cancelling", "Cancelled", "Failed to cancel"),
}


def _get_tool_label(name: str, args: dict, state: str = "progress") -> str:
    """Get human-readable label for tool box header."""
    verbs = _PROGRESS_VERBS.get(name, ("Processing", "Processed", "Failed"))
    target = ""
    if "file_path" in args:
        target = args["file_path"]
    elif "path" in args:
        target = args["path"]
    elif "directory_path" in args:
        target = args["directory_path"]
    elif "query" in args:
        target = args["query"]
    elif "url" in args:
        target = args["url"]
    elif "name" in args:
        target = args["name"]

    if state == "progress":
        return f"{verbs[0]} {target}" if target else verbs[0]
    elif state == "success":
        return f"{verbs[1]} {target}" if target else verbs[1]
    else:
        return f"{verbs[2]} {target}" if target else verbs[2]


def _get_result_summary(name: str, result: str) -> str:
    """Generate a concise result summary for the tool box content line."""
    if result.startswith("[Error]"):
        return result[:60]
    if "read" in name:
        size_kb = len(result) / 1024
        return f"{size_kb:.1f}KB read"
    elif "edit" in name:
        # Count edit references
        edits_count = result.lower().count("applied") or 1
        return f"{edits_count} edits applied"
    elif "create" in name:
        size_kb = len(result) / 1024
        return f"created ({size_kb:.1f}KB)"
    elif "delete" in name:
        return "deleted"
    elif "search" in name:
        lines = result.strip().split("\n")
        return f"{len(lines)} results"
    elif "shell" in name:
        first_line = result.strip().split("\n")[0][:50] if result.strip() else "done"
        return first_line
    elif "schedule" in name or "job" in name:
        return "job updated"
    else:
        return result[:50] if result else "done"


def _get_box_width() -> int:
    """Get tool box width based on terminal width."""
    try:
        cols = shutil.get_terminal_size().columns
    except Exception:
        cols = 80
    return max(50, min(cols - 4, 76))


def _render_tool_box(header: str, status_icon: str, duration_ms: float,
                     summary: str, color: str, is_tty: bool = True) -> str:
    """Render a complete tool box (3 lines) with the given color."""
    box_width = _get_box_width()
    inner_width = box_width - 2  # for │ ... │

    # Truncate header if needed
    max_header_len = inner_width - 4  # account for "─ " prefix and " ─" suffix
    if len(header) > max_header_len:
        header = header[:max_header_len - 3] + "..."

    # Build header line
    padding_len = inner_width - len(header) - 2  # "─ header ─...─"
    header_line = f"  ┌─ {header} " + "─" * max(0, padding_len - 2) + "┐"

    # Build content line
    duration_str = f"{duration_ms:.0f}ms"
    content = f" {status_icon} {duration_str} | {summary}"
    # Truncate summary if needed
    max_content_len = inner_width - 2
    if len(content) > max_content_len:
        content = content[:max_content_len - 3] + "..."
    content_padding = " " * max(0, inner_width - len(content))
    content_line = f"  │{content}{content_padding}│"

    # Build bottom line
    bottom_line = f"  └" + "─" * inner_width + "┘"

    if is_tty:
        return f"{color}{header_line}\n{content_line}\n{bottom_line}{_RESET}"
    else:
        return f"{header_line}\n{content_line}\n{bottom_line}"


def _animate_tool_box(name: str, args: dict, done_event: threading.Event,
                      is_tty: bool = True):
    """Animation thread for tool boxes ≥150ms. Runs as daemon thread."""
    if not is_tty:
        return
    box_width = _get_box_width()
    inner_width = box_width - 2
    header = _get_tool_label(name, args, "progress")
    max_header_len = inner_width - 4
    if len(header) > max_header_len:
        header = header[:max_header_len - 3] + "..."

    idx = 0
    start_time = time.monotonic()
    while not done_event.is_set():
        spinner_char = _SPINNER_CHARS[idx % 4]
        # Color cycle: gray for 2 frames, orange for 2 frames
        color = _GRAY if (idx // 2) % 2 == 0 else _ORANGE
        elapsed_ms = (time.monotonic() - start_time) * 1000
        duration_str = f"{elapsed_ms:.0f}ms"

        padding_len = inner_width - len(header) - 2
        header_line = f"  ┌─ {header} " + "─" * max(0, padding_len - 2) + "┐"
        content = f" {spinner_char} {duration_str}"
        content_padding = " " * max(0, inner_width - len(content))
        content_line = f"  │{content}{content_padding}│"
        bottom_line = f"  └" + "─" * inner_width + "┘"

        # Move up 3, clear, redraw
        sys.stdout.write(f"\033[3A\033[2K{color}{header_line}{_RESET}\n"
                         f"\033[2K{color}{content_line}{_RESET}\n"
                         f"\033[2K{color}{bottom_line}{_RESET}\n")
        sys.stdout.flush()
        idx += 1
        time.sleep(0.15)


# ---------------------------------------------------------------------------
# Phase Implementations
# ---------------------------------------------------------------------------

def _bootstrap(ctx: ExecutionContext) -> None:
    """Phase 1: Load SelfModel, apply decay, select model, scan capabilities."""
    t0 = time.monotonic()
    is_tty = _is_tty()

    # Load and decay
    ctx.self_model = SelfModelEngine.load()
    SelfModelEngine.apply_decay(ctx.self_model)

    # Determine available models and default
    model_name = ctx.model_config.get("name", "unknown")
    available_models = [model_name]  # Primary model always available

    # Select model using self-model engine
    selected, auto_switched, reason = SelfModelEngine.select_model(
        ctx.self_model, model_name, available_models
    )

    ctx.active_model = dict(ctx.model_config)  # shallow copy, never mutate original
    ctx.auto_switched = auto_switched
    ctx.switch_reason = reason

    # Check persisted capabilities (R11.7)
    model_rel = ctx.self_model.model_reliability.get(selected)
    if model_rel and not model_rel.capabilities.get("tool_calling", True):
        # This model had tool_calling disabled by self-healing
        ctx.active_model["_tool_calling_disabled"] = True

    # Scan capabilities
    project_path = ctx.project_info.get("root") if ctx.project_info else None
    ctx.capability_snapshot = SelfModelEngine.scan_capabilities(project_path)

    # Health check for local models (pre-flight, once per session)
    if _is_local_model(ctx.active_model):
        healthy, health_error = _check_local_health(ctx.active_model)
        if not healthy:
            ctx.error = f"⚠️  {health_error}"
            ctx.error_type = "api_error"
            if is_tty:
                print(f"⚡ {Colors.RED}{health_error}{Colors.RESET}")
            else:
                print(f"⚡ {health_error}")
            ctx.phase_times["bootstrap"] = (time.monotonic() - t0) * 1000
            return

    # Determine first-run
    first_run = ctx.self_model.total_executions == 0

    # Print ⚡ line
    valence = ctx.self_model.valence
    valence_color = Colors.GREEN if valence >= 0 else Colors.RED
    display_name = ctx.active_model.get("display_name", selected)

    cap = ctx.capability_snapshot
    cap_parts = []
    if cap.get("skills_count"):
        cap_parts.append(f"{cap['skills_count']} skills")
    if cap.get("custom_tools_count"):
        cap_parts.append(f"{cap['custom_tools_count']} tools")
    if cap.get("active_jobs_count"):
        cap_parts.append(f"{cap['active_jobs_count']} jobs active")
    cap_summary = ", ".join(cap_parts) if cap_parts else "ready"

    if first_run:
        if is_tty:
            print(f"⚡ Welcome — first execution. Using {Colors.CYAN}{display_name}{Colors.RESET} (configured default). No history yet.")
        else:
            print(f"⚡ Welcome — first execution. Using {display_name} (configured default). No history yet.")
    elif auto_switched:
        if is_tty:
            print(f"⚡ Switching → {Colors.CYAN}{display_name}{Colors.RESET}")
            print(f"  ⚠️  {reason}")
        else:
            print(f"⚡ Switching → {display_name}")
            print(f"  ⚠️  {reason}")
    else:
        if is_tty:
            print(f"⚡ {Colors.CYAN}{display_name}{Colors.RESET} | valence: {valence_color}{valence:+d}{Colors.RESET} | {cap_summary}")
        else:
            print(f"⚡ {display_name} | valence: {valence:+d} | {cap_summary}")

    ctx.phase_times["bootstrap"] = (time.monotonic() - t0) * 1000


def _plan(ctx: ExecutionContext) -> None:
    """Phase 2: Classify message, build system prompt, construct payload."""
    t0 = time.monotonic()
    is_tty = _is_tty()

    # Classify
    ctx.classification = classify(ctx.user_message)

    # Check for AUTONOMOUS escalation (multi-step tasks that need agent swarm)
    if ctx.classification == "COMPLEX":
        should_escalate, escalation_reason = _detect_autonomous(ctx.user_message)
        if should_escalate:
            ctx.classification = "AUTONOMOUS"
            ctx.switch_reason = escalation_reason

    # Build system prompt per classification
    if ctx.classification == "SIMPLE":
        ctx.system_prompt = "You are Kognisant, a helpful AI assistant. Respond naturally and concisely."
        ctx.timeout = 30
    elif ctx.classification == "CONTEXT":
        ctx.system_prompt = _build_context_prompt(ctx)
        ctx.timeout = 60
    else:  # COMPLEX
        ctx.system_prompt = _build_complex_prompt(ctx)
        ctx.timeout = 120

    # Adaptive timeouts: local models get extended timeouts for loading + thinking
    if _is_local_model(ctx.active_model):
        if ctx.classification == "SIMPLE":
            ctx.timeout = 120
        elif ctx.classification == "CONTEXT":
            ctx.timeout = 180
        else:
            ctx.timeout = 300

    # Build api_messages with appropriate window
    ctx.api_messages = _build_api_messages(ctx)

    # Set tools
    if ctx.classification == "COMPLEX" and not ctx.active_model.get("_tool_calling_disabled"):
        ctx.tools = get_active_tools()
    else:
        ctx.tools = None

    # Compute token breakdown
    tools_json = json.dumps(ctx.tools) if ctx.tools else None
    # Exclude system message from history for breakdown (it's counted separately)
    history_for_tokens = [m for m in ctx.api_messages if m.get("role") != "system"]
    ctx.token_breakdown = compute_token_breakdown(
        ctx.system_prompt, tools_json, history_for_tokens, ctx.user_message
    )
    ctx.total_tokens_in = ctx.token_breakdown.get("total", 0)

    # Print 📋 line
    cls_name = ctx.classification
    total = ctx.total_tokens_in
    if is_tty:
        if ctx.classification == "AUTONOMOUS":
            print(f"📋 {_BOLD}{cls_name}{_RESET} → delegating to agent swarm")
            print(f"  {ctx.switch_reason}")
        elif ctx.classification == "COMPLEX":
            bd = ctx.token_breakdown
            breakdown = f"sys: {bd['system']} + tools: {bd['tools']} + hist: {bd['history']} + msg: {bd['user_message']}"
            print(f"📋 {_BOLD}{cls_name}{_RESET} → ~{total} tokens input ({breakdown})")
        elif ctx.classification == "CONTEXT":
            bd = ctx.token_breakdown
            breakdown = f"sys: {bd['system']} + hist: {bd['history']} + msg: {bd['user_message']}"
            print(f"📋 {_BOLD}{cls_name}{_RESET} → ~{total} tokens input ({breakdown})")
        else:
            print(f"📋 {_BOLD}{cls_name}{_RESET} → ~{total} tokens input")
    else:
        if ctx.classification == "AUTONOMOUS":
            print(f"📋 {cls_name} -> delegating to agent swarm")
            print(f"  {ctx.switch_reason}")
        else:
            print(f"📋 {cls_name} → ~{total} tokens input")

    # Emit classification event to json_stream
    from . import json_stream
    if json_stream.is_active():
        json_stream.emit_classification(ctx.classification, ctx.total_tokens_in)

    ctx.phase_times["plan"] = (time.monotonic() - t0) * 1000


def _build_context_prompt(ctx: ExecutionContext) -> str:
    """Build CONTEXT-level system prompt (~1500 tokens)."""
    if not ctx.project_info:
        return "You are Kognisant, a helpful AI assistant. Respond based on the conversation context."

    project_name = ctx.project_info.get("name", "project")
    files = ctx.project_info.get("files", [])
    files_str = "\n".join([f"- {f}" for f in files[:100]])
    if len(files) > 100:
        files_str += f"\n- ... and {len(files) - 100} more files"

    context_content = load_project_context(ctx.project_info.get("root", ""))

    prompt = (
        f"You are Kognisant, an AI assistant for the project '{project_name}'.\n\n"
        f"Project files:\n{files_str}\n\n"
    )
    if context_content:
        prompt += f"Project memory (.kognisant/context.md):\n{context_content}\n\n"
    prompt += "Respond based on the project context above."
    return prompt


def _build_complex_prompt(ctx: ExecutionContext) -> str:
    """Build COMPLEX-level system prompt (~2000 tokens)."""
    if not ctx.project_info:
        return (
            "You are Kognisant, an advanced software engineering assistant. "
            "You have tools available for file operations, web browsing, and more. "
            "Use them when the task requires reading, writing, or searching."
        )

    project_name = ctx.project_info.get("name", "project")
    project_root = ctx.project_info.get("root", "")
    files = ctx.project_info.get("files", [])
    files_str = "\n".join([f"- {f}" for f in files[:100]])
    if len(files) > 100:
        files_str += f"\n- ... and {len(files) - 100} more files"

    context_content = load_project_context(project_root)
    guidelines_content = load_project_memory_guidelines(project_root)
    skills = load_global_skills()

    prompt = (
        f"You are Kognisant, an advanced software engineering assistant for '{project_name}'.\n\n"
        f"Project files:\n{files_str}\n\n"
    )
    if context_content:
        prompt += f"Project memory (.kognisant/context.md):\n{context_content}\n\n"
    if guidelines_content:
        prompt += f"Steering rules (.kognisant/memory-guidlines.md):\n{guidelines_content}\n\n"
    if skills:
        skill_names = ", ".join(s["name"] for s in skills)
        prompt += f"Available skills: {skill_names}\n\n"
    prompt += (
        "You have tools available for file operations, web browsing, script management, "
        "and job scheduling. Use them when the task requires reading, writing, or "
        "searching. Respond directly when the task is conversational."
    )
    return prompt


def _build_api_messages(ctx: ExecutionContext) -> list[dict]:
    """Build the api_messages list with appropriate context window."""
    system_msg = {"role": "system", "content": ctx.system_prompt}

    # Get conversation history (excluding system messages)
    history = [m for m in ctx.messages if m.get("role") != "system"]

    if ctx.classification == "SIMPLE":
        # Last assistant message + user message
        window = []
        for m in reversed(history):
            if m.get("role") == "assistant":
                window.insert(0, m)
                break
        # User message will be appended during _execute
        return [system_msg] + window

    elif ctx.classification == "CONTEXT":
        # Last 10 messages
        window = history[-10:]
        return [system_msg] + window

    else:  # COMPLEX
        # Last 20 messages with tool result pruning (R11.4)
        window = history[-20:]
        pruned = []
        for m in window:
            if m.get("role") == "tool" and len(m.get("content", "")) > 500:
                # Summarize old tool results
                tool_name = m.get("name", "tool")
                char_count = len(m.get("content", ""))
                pruned.append({
                    "role": "tool",
                    "tool_call_id": m.get("tool_call_id", ""),
                    "name": tool_name,
                    "content": f"[Previously: {tool_name} returned {char_count} chars. Re-read if needed.]",
                })
            else:
                pruned.append(m)
        return [system_msg] + pruned


def _execute(ctx: ExecutionContext) -> None:
    """Phase 3: Stream LLM response with retry, thinking display, and tool loop."""
    from . import json_stream

    t0 = time.monotonic()
    is_tty = _is_tty()
    is_json = json_stream.is_active()

    # Append user message and save (R3.8 checkpoint is already set)
    ctx.messages.append({"role": "user", "content": ctx.user_message})
    _save_session_safe(ctx)

    # Add user message to api_messages
    ctx.api_messages.append({"role": "user", "content": ctx.user_message})

    model_name = ctx.active_model.get("name", "unknown")
    display_name = ctx.active_model.get("display_name", model_name)
    api_base = ctx.active_model.get("api_base_url", "")
    api_key = ctx.active_model.get("api_key", "")
    protocol = ctx.active_model.get("protocol", "openai")
    is_local = _is_local_model(ctx.active_model)

    # Check if model supports reasoning (from persisted capabilities)
    model_rel = ctx.self_model.model_reliability.get(model_name)
    reasoning_capable = None  # None = unknown, try with thinking
    if model_rel and "reasoning" in model_rel.capabilities:
        reasoning_capable = model_rel.capabilities["reasoning"]

    max_tool_rounds = 3
    current_round = 0
    tool_tokens_accumulated = 0
    spinner = None

    # Retry configuration: 3 attempts for timeouts/empty responses
    max_attempts = 3

    try:
        for attempt in range(1, max_attempts + 1):
            # Calculate timeout for this attempt
            attempt_timeout = ctx.timeout
            if attempt == 2:
                attempt_timeout = ctx.timeout * 2
            elif attempt == 3:
                attempt_timeout = 300 if is_local else 120

            # Show retry indicator
            if attempt > 1:
                retry_msg = f"Retry {attempt}/{max_attempts}..."
                if attempt == 3:
                    retry_msg = f"Retry {attempt}/{max_attempts} (extended timeout)..."
                if is_tty:
                    print(f"  {Colors.YELLOW}{retry_msg}{Colors.RESET}")
                else:
                    print(f"  {retry_msg}")
                # Delay for remote models (rate limiting)
                if not is_local:
                    time.sleep(2)

            # Reset state for this attempt
            current_round = 0
            ctx.response = ""
            ctx.streamed = False
            attempt_success = False
            attempt_error = None

            # Inner tool loop (max 3 rounds per attempt)
            while current_round < max_tool_rounds:
                current_round += 1

                # Build payload
                payload = {
                    "model": model_name,
                    "messages": ctx.api_messages,
                    "stream": True,
                }
                if ctx.tools:
                    payload["tools"] = ctx.tools
                    # Explicitly signal tool_choice to providers that require it
                    # (Nvidia NIM, some OpenAI-compatible endpoints)
                    payload["tool_choice"] = "auto"

                # Add thinking flag for reasoning-capable local models
                if is_local and reasoning_capable is not False:
                    if protocol == "ollama":
                        payload["think"] = True

                # Start spinner
                round_info = ""
                if current_round > 1:
                    round_info = f" (round {current_round}, +{tool_tokens_accumulated:,} tokens from tools)"

                # Choose initial spinner state based on model type
                if is_local:
                    initial_state = "loading model..."
                else:
                    initial_state = "connecting..."

                spinner_msg = f"⚙️  {display_name} - {initial_state}{round_info}" if is_tty else f"⚙️  {display_name} - {initial_state}"
                spinner = None
                if is_tty:
                    spinner = Spinner(message=spinner_msg, show_elapsed=True, timeout=attempt_timeout)
                    spinner.start()
                else:
                    print(spinner_msg)

                # Stream
                content_parts = []
                thinking_parts = []
                tool_calls = None
                assistant_message = None
                first_content = True
                first_thinking = True
                sub_state = "connecting"
                thinking_start_time = None

                try:
                    for chunk_type, data in query_model_api_stream(
                        api_base, api_key, payload, protocol=protocol, timeout=attempt_timeout
                    ):
                        if chunk_type == "phase" and data == "connected":
                            sub_state = "thinking"
                            if spinner:
                                if is_local:
                                    spinner.update_message(f"⚙️  {display_name} - waiting for response...{round_info}")
                                else:
                                    spinner.update_message(f"⚙️  {display_name} - thinking...{round_info}")

                        elif chunk_type == "thinking":
                            # Thinking tokens arriving - model is alive
                            if first_thinking:
                                first_thinking = False
                                thinking_start_time = time.monotonic()
                                sub_state = "thinking"
                                if spinner:
                                    spinner.stop()
                                    spinner = None
                                # Print thinking header
                                if is_json:
                                    json_stream.emit_thinking_start()
                                    json_stream.set_heartbeat_state("streaming", "thinking")
                                elif is_tty:
                                    sys.stdout.write(f"{_DIM}💭 Thinking...{_RESET}\n")
                                else:
                                    print("[THINKING]")
                                # Mark reasoning capability as detected
                                if reasoning_capable is None:
                                    reasoning_capable = True
                            # Stream thinking tokens
                            if is_json:
                                json_stream.emit_thinking_delta(data)
                            elif is_tty:
                                sys.stdout.write(f"{_DIM}{data}{_RESET}")
                            else:
                                sys.stdout.write(data)
                            if not is_json:
                                sys.stdout.flush()
                            thinking_parts.append(data)

                        elif chunk_type == "content":
                            if first_content:
                                sub_state = "streaming"
                                if spinner:
                                    spinner.stop()
                                    spinner = None
                                # If we were showing thinking, close it and show duration
                                if thinking_parts:
                                    thinking_duration = time.monotonic() - (thinking_start_time or t0)
                                    if is_json:
                                        json_stream.emit_thinking_end(thinking_duration * 1000)
                                    elif is_tty:
                                        sys.stdout.write(f"\n{_DIM}💭 Thought for {thinking_duration:.1f}s{_RESET}\n\n")
                                    else:
                                        sys.stdout.write(f"\n[THOUGHT for {thinking_duration:.1f}s]\n\n")
                                    if not is_json:
                                        sys.stdout.flush()
                                # Print response header / emit content_start
                                if is_json:
                                    json_stream.emit_content_start()
                                    json_stream.set_heartbeat_state("streaming", "llm_response")
                                elif is_tty:
                                    print(f"{Colors.CYAN}Kognisant >{Colors.RESET}")
                                else:
                                    print("Kognisant >")
                                first_content = False
                                ctx.streamed = True
                            # Stream content
                            if is_json:
                                json_stream.emit_content_delta(data)
                                # Check for cancel from frontend
                                if json_stream.check_for_cancel():
                                    raise KeyboardInterrupt
                            else:
                                sys.stdout.write(data)
                                sys.stdout.flush()
                            content_parts.append(data)

                            # Degenerate loop detection: if the model is repeating
                            # the same phrase (tool call narration without actually calling),
                            # abort the stream to prevent infinite output.
                            if len(content_parts) > 20:
                                recent_text = "".join(content_parts[-50:]) if len(content_parts) > 50 else "".join(content_parts)
                                # Check for repetitive patterns (same sentence 3+ times)
                                sentences = [s.strip() for s in recent_text.split("\n") if s.strip()]
                                if len(sentences) >= 6:
                                    last_6 = sentences[-6:]
                                    # If 4+ of the last 6 sentences are near-identical, it's a loop
                                    from collections import Counter
                                    counts = Counter(last_6)
                                    most_common_count = counts.most_common(1)[0][1] if counts else 0
                                    if most_common_count >= 4:
                                        # Degenerate loop detected — abort stream
                                        if is_json:
                                            json_stream.emit_degenerate_loop("Model narrating tool calls instead of executing them")
                                        if is_tty:
                                            sys.stdout.write(f"\n\n{Colors.YELLOW}⚠️  Repetitive output detected — model is narrating tool calls instead of executing them.{Colors.RESET}\n")
                                            sys.stdout.write(f"{Colors.YELLOW}   Try: /model to switch models, or rephrase your request without needing file reads.{Colors.RESET}\n")
                                        else:
                                            sys.stdout.write("\n\n⚠️  Repetitive output detected. Try /model to switch.\n")
                                        sys.stdout.flush()
                                        # Treat as failed attempt
                                        content_parts.clear()
                                        ctx.response = ""
                                        ctx.streamed = False
                                        attempt_success = True  # Don't retry — it'll loop again
                                        break

                        elif chunk_type == "tool_calls":
                            tool_calls = data

                        elif chunk_type == "done":
                            assistant_message = data

                except KognisantAPIError as e:
                    if spinner:
                        spinner.stop()
                        spinner = None
                    error_str = str(e)
                    # Check if this is a retryable error
                    if _is_retryable_error(error_str) and attempt < max_attempts:
                        attempt_error = error_str
                        # Rollback this attempt's messages
                        while len(ctx.messages) > ctx.checkpoint_idx + 1:
                            ctx.messages.pop()
                        break  # Break tool loop, continue retry loop
                    else:
                        _handle_api_error(ctx, error_str, sub_state)
                        return

                finally:
                    if spinner:
                        spinner.stop()
                        spinner = None

                # Newline after streamed content
                if content_parts and ctx.streamed:
                    if is_json:
                        duration_ms = (time.monotonic() - t0) * 1000
                        json_stream.emit_content_end(duration_ms, estimate_tokens("".join(content_parts)))
                        json_stream.set_heartbeat_state("idle")
                    else:
                        sys.stdout.write("\n")
                        sys.stdout.flush()

                # If thinking arrived but no content yet and we hit this point
                if thinking_parts and not content_parts and not tool_calls:
                    # Model thought but produced no content - close thinking display
                    if thinking_start_time:
                        thinking_duration = time.monotonic() - thinking_start_time
                        if is_json:
                            json_stream.emit_thinking_end(thinking_duration * 1000)
                        elif is_tty and first_content:
                            sys.stdout.write(f"\n{_DIM}💭 Thought for {thinking_duration:.1f}s{_RESET}\n")
                            sys.stdout.flush()

                # Save thinking to file
                if thinking_parts:
                    raw_thinking = "".join(thinking_parts)
                    thinking_dur = (time.monotonic() - (thinking_start_time or t0)) * 1000
                    _save_thinking(ctx, raw_thinking, thinking_dur)

                # Record response
                full_content = "".join(content_parts)
                if not ctx.response:
                    ctx.response = full_content
                else:
                    ctx.response += full_content

                # Handle token calibration from _usage
                if assistant_message and assistant_message.get("_usage"):
                    usage = assistant_message["_usage"]
                    actual_in = usage.get("prompt_tokens", 0)
                    if actual_in and ctx.total_tokens_in:
                        SelfModelEngine.update_token_calibration(
                            ctx.self_model, model_name, actual_in, ctx.total_tokens_in
                        )
                    ctx.total_tokens_out = usage.get("completion_tokens", 0)
                else:
                    ctx.total_tokens_out += estimate_tokens(full_content)

                # Append assistant message to messages
                if assistant_message:
                    clean_msg = {k: v for k, v in assistant_message.items()
                                 if k not in ("_usage", "_thinking")}
                    # Add thinking reference
                    if thinking_parts:
                        turn_num = len([m for m in ctx.messages if m.get("role") == "user"])
                        clean_msg["_thinking_turn"] = turn_num
                    ctx.messages.append(clean_msg)
                    ctx.api_messages.append(clean_msg)
                    _save_session_safe(ctx)

                # Check for tool calls
                if tool_calls:
                    if ctx.classification in ("SIMPLE", "CONTEXT"):
                        attempt_success = True
                        break

                    tool_results = _execute_tools(ctx, tool_calls, is_tty)
                    tool_tokens_accumulated += sum(
                        estimate_tokens(r.get("content", "")) for r in tool_results
                    )

                    for tr in tool_results:
                        ctx.messages.append(tr)
                        ctx.api_messages.append(tr)
                        _save_session_safe(ctx)

                    # Reset thinking for next round
                    thinking_parts = []
                    first_thinking = True
                    continue
                else:
                    attempt_success = True
                    break

            # Check if this attempt succeeded
            if attempt_success and ctx.response.strip():
                ctx.success = True
                break
            elif attempt_success and not ctx.response.strip():
                # Empty response - retry if attempts remain
                if attempt < max_attempts:
                    if is_tty:
                        print(f"  {Colors.YELLOW}⚠️  Empty response, retrying...{Colors.RESET}")
                    else:
                        print(f"  ⚠️  Empty response, retrying...")
                    # Rollback this attempt
                    while len(ctx.messages) > ctx.checkpoint_idx + 1:
                        ctx.messages.pop()
                    continue
                else:
                    ctx.success = False
                    ctx.error = "⚠️  Model returned empty response after 3 attempts. Try /model to switch."
                    ctx.error_type = "empty"
                    if is_tty:
                        print(f"\n{Colors.YELLOW}{ctx.error}{Colors.RESET}")
                    else:
                        print(f"\n{ctx.error}")
                    break
            elif attempt_error:
                # Retryable error occurred, continue to next attempt
                continue
            else:
                break

        # Persist reasoning capability detection
        if reasoning_capable is not None:
            rel = SelfModelEngine._ensure_model_reliability(ctx.self_model, model_name)
            rel.capabilities["reasoning"] = reasoning_capable

        # Post-exhaustion escalation: if we exhausted tool rounds without content, escalate
        if not ctx.success and not ctx.cancelled and ctx.tool_calls_made >= 3 and not ctx.response.strip():
            if is_tty:
                print(f"\n  {Colors.YELLOW}⚠️  Task needs more steps than single-model chat allows.{Colors.RESET}")
                print(f"  {Colors.CYAN}🐝 Auto-escalating to agent swarm...{Colors.RESET}\n")
            else:
                print(f"\n  Task needs more steps. Auto-escalating to agent swarm...\n")
            _rollback(ctx)
            _escalate_to_swarm(ctx)

    except KeyboardInterrupt:
        # Graceful cancellation - cancels entire retry sequence
        if spinner:
            spinner.stop()
        ctx.cancelled = True
        ctx.error_type = "cancelled"
        _rollback(ctx)
        if is_tty:
            print(f"\n{Colors.YELLOW}Cancelled.{Colors.RESET} Tip: /model to switch to a faster model.")
        else:
            print("\nCancelled. Tip: /model to switch to a faster model.")
        return

    ctx.response_time = time.monotonic() - t0
    ctx.phase_times["execute"] = ctx.response_time * 1000


def _is_retryable_error(error_str: str) -> bool:
    """Check if an API error should trigger a retry."""
    lower = error_str.lower()
    # Retryable: timeouts, stalls, empty responses, generic connection issues
    if "timeout" in lower or "timed out" in lower:
        return True
    if "stall" in lower:
        return True
    if "connection" in lower and "refused" not in lower:
        return True
    # NOT retryable: auth errors, payment, rate limiting, tool detection
    if "401" in error_str or "402" in error_str or "429" in error_str:
        return False
    if "tool" in lower or "function" in lower:
        return False
    return True


def _escalate_to_swarm(ctx: ExecutionContext) -> None:
    """Escalate an AUTONOMOUS task to the PERP agent swarm."""
    is_tty = _is_tty()

    try:
        from .agents import perp_orchestrate
        from .config import get_compiled_models

        compiled_models = get_compiled_models()

        # Pass active model name so swarm can prioritize it
        if ctx.project_info:
            ctx.project_info["_active_model_name"] = ctx.active_model.get("name", "")

        if is_tty:
            print(f"\n  {Colors.CYAN}🐝 Delegating to agent swarm...{Colors.RESET}")
            print(f"  [Running in background - /status to monitor, /stop to cancel]\n")
        else:
            print("\n  Delegating to agent swarm...")
            print("  [Running in background - /status to monitor, /stop to cancel]\n")

        perp_orchestrate(ctx.user_message, ctx.project_info, compiled_models)

        ctx.success = True
        ctx.response = "(Agent swarm dispatched. Use /status to monitor progress.)"
        ctx.streamed = False

    except ImportError:
        ctx.error = "Agent swarm module not available."
        ctx.error_type = "api_error"
    except Exception as e:
        ctx.error = f"Failed to launch agent swarm: {str(e)[:80]}"
        ctx.error_type = "api_error"


def _execute_tools(ctx: ExecutionContext, tool_calls: list[dict],
                   is_tty: bool) -> list[dict]:
    """Execute tool calls with animated boxes. Returns list of tool result messages."""
    from . import json_stream
    is_json = json_stream.is_active()

    results = []

    for tc in tool_calls:
        func_name = tc.get("function", {}).get("name", "unknown")
        func_args_str = tc.get("function", {}).get("arguments", "{}")
        call_id = tc.get("id", "")

        try:
            args = json.loads(func_args_str) if isinstance(func_args_str, str) else func_args_str
        except (json.JSONDecodeError, TypeError):
            args = {}

        # Emit tool_start event
        if is_json:
            json_stream.emit_tool_start(call_id, func_name, args)
            json_stream.set_heartbeat_state("tool_executing", func_name)

        # Print initial box (placeholder for animation)
        header_label = _get_tool_label(func_name, args, "progress")
        if is_tty:
            # Print placeholder box for animation
            box_width = _get_box_width()
            inner_width = box_width - 2
            max_header_len = inner_width - 4
            if len(header_label) > max_header_len:
                header_label = header_label[:max_header_len - 3] + "..."
            padding_len = inner_width - len(header_label) - 2
            h_line = f"  ┌─ {header_label} " + "─" * max(0, padding_len - 2) + "┐"
            c_line = f"  │ ◐ 0ms" + " " * max(0, inner_width - 7) + "│"
            b_line = f"  └" + "─" * inner_width + "┘"
            print(f"{_GRAY}{h_line}\n{c_line}\n{b_line}{_RESET}")

        # Start animation thread (daemon)
        done_event = threading.Event()
        anim_thread = None
        tool_start = time.monotonic()

        if is_tty:
            anim_thread = threading.Thread(
                target=_animate_tool_box,
                args=(func_name, args, done_event, is_tty),
                daemon=True,
            )
            anim_thread.start()

        # Execute tool synchronously
        tool_result = execute_tool(func_name, func_args_str, ctx.project_info)
        tool_duration_ms = (time.monotonic() - tool_start) * 1000

        # Stop animation
        done_event.set()
        if anim_thread:
            anim_thread.join(timeout=0.5)

        # Determine success/failure
        tool_success = not (isinstance(tool_result, str) and tool_result.startswith("[Error]"))

        # Record tool usage
        ctx.tool_calls_made += 1
        ctx.tools_used.append({
            "name": func_name,
            "success": tool_success,
            "duration": tool_duration_ms / 1000,
        })

        # Emit tool_result event
        if is_json:
            summary = _get_result_summary(func_name, tool_result or "")
            json_stream.emit_tool_result(call_id, tool_success, summary, tool_duration_ms)
            json_stream.set_heartbeat_state("streaming", "llm_response")

        # Redraw final box
        state = "success" if tool_success else "failure"
        final_header = _get_tool_label(func_name, args, state)
        summary = _get_result_summary(func_name, tool_result or "")
        status_icon = "✓" if tool_success else "✗"
        color = _GREEN if tool_success else _RED

        if is_tty:
            # Move up 3 lines, clear, print final
            sys.stdout.write("\033[3A")
            final_box = _render_tool_box(final_header, status_icon, tool_duration_ms, summary, color, is_tty)
            print(final_box)
        else:
            # Non-TTY: just print the final state
            print(f"  [{status_icon}] {final_header} — {tool_duration_ms:.0f}ms | {summary}")

        # Build tool result message
        results.append({
            "role": "tool",
            "tool_call_id": call_id,
            "name": func_name,
            "content": tool_result or "",
        })

        # Update tool reliability in self_model
        SelfModelEngine.record_tool_result(ctx.self_model, func_name, tool_success)

    return results


def _handle_api_error(ctx: ExecutionContext, error_str: str, sub_state: str) -> None:
    """Handle API errors with appropriate user messaging and rollback."""
    from . import json_stream
    if json_stream.is_active():
        json_stream.emit_error(ctx.error_type or "api_error", error_str[:200], recoverable=_is_retryable_error(error_str))

    is_tty = _is_tty()

    if "401" in error_str:
        ctx.error = "⚠️  API key rejected. Use /model to update."
        ctx.error_type = "api_error"
    elif "429" in error_str:
        ctx.error = "⚠️  Rate limited. Wait a moment and try again."
        ctx.error_type = "api_error"
    elif "400" in error_str and ("tool" in error_str.lower() or "function" in error_str.lower()):
        # Self-healing: disable tools for this model (R11.7)
        model_name = ctx.active_model.get("name", "unknown")
        model_rel = SelfModelEngine._ensure_model_reliability(ctx.self_model, model_name)
        model_rel.capabilities["tool_calling"] = False
        if is_tty:
            print(f"  ⚠️  {Colors.YELLOW}{model_name} doesn't support tools. Retrying without.{Colors.RESET}")
        else:
            print(f"  ⚠️  {model_name} doesn't support tools. Retrying without.")
        # Retry once without tools
        ctx.tools = None
        ctx.active_model["_tool_calling_disabled"] = True
        _rollback(ctx)
        # Re-enter execute (simplified non-streaming fallback)
        _execute_fallback(ctx)
        return
    elif "stall" in error_str.lower():
        ctx.error = f"⚠️  Stream stalled — no data received for 30s. Connection dropped."
        ctx.error_type = "timeout"
        ctx.stalled = True
        ctx.timed_out = True
    elif "timeout" in error_str.lower() or "timed out" in error_str.lower():
        ctx.error = f"⚠️  No response in {ctx.timeout}s ({ctx.classification} timeout).\n   Stuck on: {sub_state}\n   Tip: /model to switch to a faster model."
        ctx.error_type = "timeout"
        ctx.timed_out = True
    else:
        ctx.error = f"⚠️  Can't reach endpoint: {error_str[:100]}"
        ctx.error_type = "api_error"

    _rollback(ctx)
    if ctx.error and is_tty:
        print(f"\n{Colors.YELLOW}{ctx.error}{Colors.RESET}")
    elif ctx.error:
        print(f"\n{ctx.error}")


def _execute_fallback(ctx: ExecutionContext) -> None:
    """Non-streaming fallback for retries after tool-detection self-heal."""
    model_name = ctx.active_model.get("name", "unknown")
    api_base = ctx.active_model.get("api_base_url", "")
    api_key = ctx.active_model.get("api_key", "")
    protocol = ctx.active_model.get("protocol", "openai")

    # Re-append user message
    ctx.messages.append({"role": "user", "content": ctx.user_message})
    ctx.api_messages.append({"role": "user", "content": ctx.user_message})
    _save_session_safe(ctx)

    payload = {
        "model": model_name,
        "messages": ctx.api_messages,
        "stream": False,
    }

    try:
        resp = query_model_api_raw(api_base, api_key, payload, protocol=protocol)
        if resp and "choices" in resp and resp["choices"]:
            content = resp["choices"][0].get("message", {}).get("content", "")
            ctx.response = content
            ctx.success = bool(content.strip())
            ctx.streamed = False
            msg = {"role": "assistant", "content": content}
            ctx.messages.append(msg)
            _save_session_safe(ctx)
        else:
            ctx.error = "⚠️  Empty response from fallback."
            ctx.error_type = "empty"
    except KognisantAPIError as e:
        ctx.error = f"⚠️  Fallback also failed: {str(e)[:80]}"
        ctx.error_type = "api_error"
        _rollback(ctx)


def _reflect(ctx: ExecutionContext) -> int:
    """Phase 4: HOT/WARM/COLD reflection. Returns valence_delta."""
    t0 = time.monotonic()
    is_tty = _is_tty()

    model_name = ctx.active_model.get("name", "unknown")

    # HOT reflect
    valence_delta = reflect_hot(
        ctx.self_model,
        success=ctx.success,
        response_time=ctx.response_time,
        timed_out=ctx.timed_out,
        empty=(ctx.error_type == "empty"),
        cancelled=ctx.cancelled,
        error=(ctx.error_type == "api_error"),
        tools_used=ctx.tools_used,
        model_name=model_name,
    )

    # Update circuit breaker
    if model_name not in ctx.self_model.circuit_breakers:
        ctx.self_model.circuit_breakers[model_name] = CircuitBreakerState()
    cb = ctx.self_model.circuit_breakers[model_name]
    if ctx.success:
        SelfModelEngine.cb_record_success(cb)
    elif ctx.error_type in ("timeout", "api_error"):
        SelfModelEngine.cb_record_failure(cb)

    # Update last_execution_at
    from datetime import datetime, timezone
    ctx.self_model.last_execution_at = datetime.now(timezone.utc).isoformat()

    # Print 🔍 line
    response_time_s = ctx.response_time
    tokens_in = ctx.total_tokens_in
    tokens_out = ctx.total_tokens_out or estimate_tokens(ctx.response)
    valence = ctx.self_model.valence
    delta_color = Colors.GREEN if valence_delta >= 0 else Colors.RED
    delta_str = f"{valence_delta:+d}"

    reflect_line = f"🔍 {response_time_s:.1f}s | {tokens_in} in → {tokens_out} out | valence: {valence:+d} ({delta_str})"

    if ctx.tool_calls_made:
        reflect_line += f" | {ctx.tool_calls_made} tool(s)"
    if ctx.auto_switched:
        reflect_line += " | switched model"
    if ctx.timed_out:
        reflect_line = f"🔍 {response_time_s:.1f}s | {tokens_in} in → 0 out | TIMEOUT | valence: {valence:+d} ({delta_str})"

    if is_tty:
        # Color the delta
        print(f"\n{reflect_line}")
    else:
        print(f"\n{reflect_line}")

    # WARM reflect (every 3rd execution)
    if should_run_warm(ctx.self_model.total_executions):
        advisories = reflect_warm(ctx.self_model)
        for advisory in advisories:
            if is_tty:
                print(f"  ⚠️  {Colors.YELLOW}{advisory}{Colors.RESET}")
            else:
                print(f"  ⚠️  {advisory}")

    # COLD reflect (every 20th execution)
    if should_run_cold(ctx.self_model.total_executions):
        report = reflect_cold(ctx.self_model)
        if is_tty:
            print(f"  {Colors.BOLD}── Health Report ──{Colors.RESET}")
        else:
            print("  ── Health Report ──")
        for line in report:
            print(f"  {line}")

    # Append telemetry
    telemetry_record = {
        "timestamp": ctx.self_model.last_execution_at,
        "project": ctx.project_info.get("name", "") if ctx.project_info else "",
        "classification": ctx.classification,
        "model": model_name,
        "provider": ctx.active_model.get("provider", ""),
        "auto_switched": ctx.auto_switched,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "token_breakdown": ctx.token_breakdown,
        "response_time_ms": ctx.response_time * 1000,
        "phase_times_ms": ctx.phase_times,
        "tool_calls": ctx.tools_used,
        "success": ctx.success,
        "error": ctx.error,
        "timed_out": ctx.timed_out,
        "cancelled": ctx.cancelled,
        "valence_before": ctx.self_model.valence - valence_delta,
        "valence_after": ctx.self_model.valence,
        "valence_delta": valence_delta,
        "model_reliability_after": (
            ctx.self_model.model_reliability[model_name].reliability
            if model_name in ctx.self_model.model_reliability else 0.5
        ),
        "circuit_breaker_state": cb.state,
    }
    append_telemetry(telemetry_record)

    ctx.phase_times["reflect"] = (time.monotonic() - t0) * 1000
    return valence_delta


def _persist(ctx: ExecutionContext) -> None:
    """Phase 5: Atomically save SelfModel."""
    t0 = time.monotonic()
    try:
        SelfModelEngine.save(ctx.self_model)
    except Exception:
        pass  # Never interrupt execution for persist failure
    ctx.phase_times["persist"] = (time.monotonic() - t0) * 1000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rollback(ctx: ExecutionContext) -> None:
    """Rollback messages to checkpoint state."""
    while len(ctx.messages) > ctx.checkpoint_idx:
        ctx.messages.pop()
    _save_session_safe(ctx)


def _save_session_safe(ctx: ExecutionContext) -> None:
    """Best-effort session save (never interrupts execution)."""
    try:
        if ctx.project_info and ctx.session_file:
            save_chat_session(ctx.project_info, ctx.messages, ctx.session_file)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Main Entry Point
# ---------------------------------------------------------------------------

def execute_message(
    user_message: str,
    messages: list[dict],
    model_config: dict,
    project_info: dict | None = None,
    session_file: str | None = None,
) -> ExecutionResult:
    """Orchestrate the 5-phase execution lifecycle for a user message.

    Args:
        user_message: The user's input text.
        messages: Mutable message history list (owned by runtime during execution).
        model_config: The configured model dict (NEVER mutated — R11.7).
        project_info: Project context dict or None.
        session_file: Session filename for persistence.

    Returns:
        ExecutionResult with outcome details.
    """
    ctx = ExecutionContext(
        user_message=user_message,
        messages=messages,
        model_config=model_config,
        project_info=project_info,
        session_file=session_file,
        checkpoint_idx=len(messages),
    )

    valence_delta = 0

    try:
        # Phase 1: Bootstrap
        _bootstrap(ctx)

        # Early exit if bootstrap failed (e.g. local service not running)
        if ctx.error:
            model_name = ctx.active_model.get("name", model_config.get("name", "unknown"))
            return ExecutionResult(
                success=False,
                response="",
                streamed=False,
                error=ctx.error,
                classification="",
                model_used=model_name,
                response_time=0.0,
                tool_calls_made=0,
                valence_delta=0,
                timed_out=False,
                cancelled=False,
                tokens_in=0,
                tokens_out=0,
            )

        # Phase 2: Plan
        _plan(ctx)

        # Phase 3: Execute (or escalate to agent swarm for AUTONOMOUS)
        if ctx.classification == "AUTONOMOUS":
            _escalate_to_swarm(ctx)
        else:
            _execute(ctx)

        # Phase 4: Reflect (skip for AUTONOMOUS - swarm handles its own telemetry)
        if ctx.classification != "AUTONOMOUS":
            valence_delta = _reflect(ctx)

        # Phase 5: Persist
        _persist(ctx)

    except KeyboardInterrupt:
        # Top-level catch for cancel during any phase
        ctx.cancelled = True
        _rollback(ctx)
        valence_delta = -5
        is_tty = _is_tty()
        if is_tty:
            print(f"\n{Colors.YELLOW}Cancelled.{Colors.RESET}")
        else:
            print("\nCancelled.")

    model_name = ctx.active_model.get("name", model_config.get("name", "unknown"))

    return ExecutionResult(
        success=ctx.success,
        response=ctx.response,
        streamed=ctx.streamed,
        error=ctx.error,
        classification=ctx.classification,
        model_used=model_name,
        response_time=ctx.response_time,
        tool_calls_made=ctx.tool_calls_made,
        valence_delta=valence_delta,
        timed_out=ctx.timed_out,
        cancelled=ctx.cancelled,
        tokens_in=ctx.total_tokens_in,
        tokens_out=ctx.total_tokens_out or estimate_tokens(ctx.response),
    )
