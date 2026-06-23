import json
import logging
import os
import re
import sys
import threading
import time

from .colors import Colors, Spinner
from .config import GLOBAL_CORE_DIR, load_spec_info, is_world_model_enabled, load_world_model
from .network import query_model_api, query_model_api_raw
from .observer import TraceCollector
from .telemetry import estimate_tokens
from .tools import execute_tool, load_global_tools

logger = logging.getLogger(__name__)

# Device Capability Awareness: Cap local concurrency based on CPU counts to prevent system freezes
CPU_COUNT = os.cpu_count() or 4
MAX_LOCAL_CONCURRENCY = max(1, CPU_COUNT // 4)
local_semaphore = threading.Semaphore(MAX_LOCAL_CONCURRENCY)

print_lock = threading.Lock()


class SwarmController:
    """Thread-safe global state manager for pause, resume, stop, and status control of background swarms."""

    is_active = False
    is_paused = False
    stop_event = threading.Event()
    resume_event = threading.Event()
    active_task_description = ""

    # Initialize state
    resume_event.set()


def get_best_models_pool(compiled_models, active_model_name=None):
    """Select planner and task models based on capabilities, not provider type.

    Priority for planner (needs reasoning):
      1. Active model (if reasoning-capable, it's proven working)
      2. Models with capabilities.reasoning == true, sorted by reliability
      3. Models with unknown reasoning capability (worth trying)
      4. Any reachable model (last resort)

    Priority for task workers (needs tool_calling):
      1. Models with capabilities.tool_calling == true
      2. Any reachable model

    Returns (planning_model, task_model).
    """
    if not compiled_models:
        mock = {"name": "mock", "provider": "Offline", "api_base_url": ""}
        return mock, mock

    # Load self_model for learned capabilities
    from .self_model_engine import SelfModelEngine
    self_model = SelfModelEngine.load()

    def _get_reasoning_capability(model):
        """Check if model is reasoning-capable from pool config or learned state."""
        # Check pool config first (user override)
        caps = model.get("capabilities", {})
        if "reasoning" in caps:
            return caps["reasoning"]
        # Check learned state from self_model
        name = model.get("name", "")
        rel = self_model.model_reliability.get(name)
        if rel and "reasoning" in rel.capabilities:
            return rel.capabilities["reasoning"]
        return None  # Unknown

    def _get_reliability(model):
        """Get model reliability score from self_model (higher is better)."""
        name = model.get("name", "")
        rel = self_model.model_reliability.get(name)
        if rel:
            return rel.reliability
        return 0.5  # Unknown default

    def _is_session_reachable(model):
        """Check if model hasn't failed with auth/payment errors this session."""
        name = model.get("name", "")
        return name not in _session_unreachable

    # Categorize models
    reasoning_true = []    # Proven reasoning capability
    reasoning_unknown = [] # Not yet tested
    reasoning_false = []   # Proven non-reasoning

    for model in compiled_models:
        if not _is_session_reachable(model):
            continue
        cap = _get_reasoning_capability(model)
        if cap is True:
            reasoning_true.append(model)
        elif cap is None:
            reasoning_unknown.append(model)
        else:
            reasoning_false.append(model)

    # Sort by reliability (highest first)
    reasoning_true.sort(key=_get_reliability, reverse=True)
    reasoning_unknown.sort(key=_get_reliability, reverse=True)

    # If active model is reasoning-capable and reachable, put it first
    if active_model_name:
        for model_list in [reasoning_true, reasoning_unknown]:
            for i, m in enumerate(model_list):
                if m.get("name") == active_model_name:
                    # Move to front
                    model_list.insert(0, model_list.pop(i))
                    break

    # Build planner candidates: reasoning_true > reasoning_unknown > any reachable
    planner_candidates = reasoning_true + reasoning_unknown
    if not planner_candidates:
        # Last resort: use any reachable model even if reasoning: false
        planner_candidates = reasoning_false

    planning_model = planner_candidates[0] if planner_candidates else compiled_models[0]

    # Task model: prefer tool_calling capable, any reachable model
    task_candidates = [m for m in compiled_models if _is_session_reachable(m)]
    tool_capable = [m for m in task_candidates
                    if m.get("capabilities", {}).get("tool_calling", True)]
    task_model = tool_capable[0] if tool_capable else (task_candidates[0] if task_candidates else compiled_models[0])

    return planning_model, task_model


# Session-level unreachable tracking (in-memory, resets on restart)
_session_unreachable: set = set()


def _mark_session_unreachable(model_name: str):
    """Mark a model as unreachable for this session (auth/payment/rate failures)."""
    _session_unreachable.add(model_name)


def _get_planner_candidates(compiled_models, active_model_name=None):
    """Get ordered list of planner candidate models for cascading fallback."""
    from .self_model_engine import SelfModelEngine
    self_model = SelfModelEngine.load()

    candidates = []
    for model in compiled_models:
        name = model.get("name", "")
        if name in _session_unreachable:
            continue

        # Check reasoning capability
        caps = model.get("capabilities", {})
        reasoning = caps.get("reasoning")
        if reasoning is None:
            rel = self_model.model_reliability.get(name)
            if rel and "reasoning" in rel.capabilities:
                reasoning = rel.capabilities["reasoning"]

        # Skip models proven non-reasoning
        if reasoning is False:
            continue

        # Get reliability for sorting
        rel = self_model.model_reliability.get(name)
        reliability = rel.reliability if rel else 0.5

        candidates.append((model, reasoning, reliability))

    # Sort: reasoning=True first, then by reliability descending
    candidates.sort(key=lambda x: (x[1] is not True, -x[2]))

    # If active model is in the list, move it to front
    if active_model_name:
        for i, (m, _, _) in enumerate(candidates):
            if m.get("name") == active_model_name:
                candidates.insert(0, candidates.pop(i))
                break

    return [m for m, _, _ in candidates]


def run_subtask_agent(subtask, task_model, project_info, results_dict, subtask_id):
    """Executes a single subtask on a background thread utilizing the appropriate model and semaphore."""
    # 1. Early abort check
    if SwarmController.stop_event.is_set():
        return

    # 2. Asynchronous Pause check (wait here if paused)
    SwarmController.resume_event.wait()

    provider = task_model.get("provider", "")
    is_local = provider == "Ollama (Local)"

    # Throttling: If local, acquire semaphore to prevent local GPU/CPU overload
    if is_local:
        local_semaphore.acquire()

    try:
        # Format a highly descriptive, concise, and friendly subtask name
        desc = subtask.get("description", "Perform codebase task").strip()
        desc_display = desc if len(desc) < 65 else desc[:62] + "..."

        # Re-check stop event
        if SwarmController.stop_event.is_set():
            return

        with print_lock:
            print(
                f"  🚀 {Colors.CYAN}Agent [{subtask_id}] Booted:{Colors.RESET} {Colors.BOLD}{desc_display}{Colors.RESET} using '{task_model['name']}'..."
            )
            sys.stdout.flush()

        # Build local subtask assistant messages context
        messages = [
            {
                "role": "system",
                "content": (
                    "You are Kognisant's Subtask Execution Agent. Complete the specific subtask assigned to you. Use tools. "
                    "IMPORTANT DIRECTIVE: You are strictly forbidden from creating temporary, draft, or staging files (such as README_UPDATED.md). "
                    "You must read, edit, or overwrite existing files (like 'README.md') directly inside the project root workspace."
                ),
            },
            {
                "role": "user",
                "content": f"Project Root files metadata: {json.dumps(project_info['files']) if project_info else '[]'}\n\nYour Subtask Description: {subtask['description']}",
            },
        ]

        success = False
        attempts = 0
        response_content = ""

        # Limit tool calls to prevent infinite loops in automated background agents
        # Higher limit (12) needed for autonomous pipeline tasks: research + write script + schedule job
        while attempts < 12:
            # Check for pause and abort before each tool execution turn
            if SwarmController.stop_event.is_set():
                return
            SwarmController.resume_event.wait()

            attempts += 1

            if task_model["name"] == "mock":
                response_content = f"Simulated completed subtask: '{subtask['description']}' successfully offline."
                success = True
                break

            if is_local:
                response_content = query_model_api(
                    task_model["api_base_url"],
                    task_model.get("api_key", ""),
                    task_model["name"],
                    messages,
                    protocol=task_model.get("protocol", "openai"),
                )
                success = True
                break

            # Define subagent's baseline filesystem tools list
            baseline_tools = [
                {
                    "type": "function",
                    "function": {
                        "name": "read_project_file",
                        "description": "Read the contents of a specific file in the active project.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "file_path": {
                                    "type": "string",
                                    "description": "The file path relative to project root.",
                                }
                            },
                            "required": ["file_path"],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "create_project_file",
                        "description": "Create a brand new file inside the project workspace root directory with specified content.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "file_path": {
                                    "type": "string",
                                    "description": "The project-relative path of the new file to create.",
                                },
                                "content": {
                                    "type": "string",
                                    "description": "The initial text content for the new file.",
                                },
                            },
                            "required": ["file_path", "content"],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "create_project_directory",
                        "description": "Create a new directory (and any necessary parent directories) inside the project workspace root.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "directory_path": {
                                    "type": "string",
                                    "description": "The project-relative path of the directory to create.",
                                }
                            },
                            "required": ["directory_path"],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "delete_project_path",
                        "description": "Delete a file or directory recursively inside the project workspace root.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "path": {
                                    "type": "string",
                                    "description": "The project-relative path of the file or directory to delete.",
                                }
                            },
                            "required": ["path"],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "edit_project_file",
                        "description": "Edit an existing file by applying find-and-replace edits sequentially.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "file_path": {
                                    "type": "string",
                                    "description": "The file path relative to project root.",
                                },
                                "edits": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "old_text": {
                                                "type": "string",
                                                "description": "Precise code snippet to find.",
                                            },
                                            "new_text": {
                                                "type": "string",
                                                "description": "Code snippet to replace it with.",
                                            },
                                        },
                                        "required": ["old_text", "new_text"],
                                    },
                                },
                            },
                            "required": ["file_path", "edits"],
                        },
                    },
                },
            ]

            # Autonomous pipeline tools: script creation, job scheduling, web search
            autonomous_tools = [
                {
                    "type": "function",
                    "function": {
                        "name": "create_script",
                        "description": "Create a new Python script in the global scripts folder (~/.kognisant_core/scripts/) with accompanying metadata. Use this to create long-running pipeline scripts that the daemon will execute.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "name": {
                                    "type": "string",
                                    "description": "Script name (lowercase alphanumeric, hyphens, underscores, 1-64 chars).",
                                },
                                "content": {
                                    "type": "string",
                                    "description": "The Python script content to write.",
                                },
                                "description": {
                                    "type": "string",
                                    "description": "Human-readable description of what the script does.",
                                },
                                "env_vars": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "List of required environment variable names for this script.",
                                },
                            },
                            "required": ["name", "content"],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "schedule_job",
                        "description": "Create a new job in the job queue. For 'scheduled' type, a valid cron expression is required. For 'persistent' type, the script runs continuously. The daemon executes these autonomously in the background.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "name": {
                                    "type": "string",
                                    "description": "Job name (lowercase alphanumeric, hyphens, underscores, 1-64 chars).",
                                },
                                "script_path": {
                                    "type": "string",
                                    "description": "Script filename in ~/.kognisant_core/scripts/ (e.g. 'my-script.py'). Required for persistent and scheduled job types.",
                                },
                                "job_type": {
                                    "type": "string",
                                    "enum": ["scheduled", "persistent", "agent"],
                                    "description": "The type of job: 'scheduled' (cron-based), 'persistent' (always-on long-running), or 'agent' (one-shot AI task).",
                                },
                                "cron_expression": {
                                    "type": "string",
                                    "description": "A 5-field cron expression (e.g., '*/15 * * * *'). Required when job_type is 'scheduled'.",
                                },
                                "task": {
                                    "type": "string",
                                    "description": "Task description for agent-type jobs. Required when job_type is 'agent'.",
                                },
                                "env_vars": {
                                    "type": "object",
                                    "description": "Environment variables to pass to the job (key-value pairs).",
                                },
                            },
                            "required": ["name", "job_type"],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "search_web",
                        "description": "Perform a headless background web search using DuckDuckGo and return the text search results. Use for researching requirements, finding documentation, or discovering resources needed for the task.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "query": {
                                    "type": "string",
                                    "description": "The search query.",
                                }
                            },
                            "required": ["query"],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "browse_web_page",
                        "description": "Fetch a public webpage URL and return readable text content. Use to read documentation or reference material needed for script creation.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "url": {
                                    "type": "string",
                                    "description": "The absolute URL to fetch.",
                                }
                            },
                            "required": ["url"],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "list_scripts",
                        "description": "List all scripts in the global scripts folder with their names and descriptions.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "list_jobs",
                        "description": "List all jobs in the job queue with their name, type, and current state.",
                        "parameters": {"type": "object", "properties": {}},
                    },
                },
            ]

            # Merge with globally transferable tools dynamically
            subagent_tools = baseline_tools + autonomous_tools + load_global_tools()

            # OpenAI / Cloud completion with full tool access
            payload = {
                "model": task_model["name"],
                "messages": messages,
                "stream": False,
                "tools": subagent_tools,
            }

            resp_data = query_model_api_raw(
                task_model["api_base_url"],
                task_model.get("api_key", ""),
                payload,
                protocol=task_model.get("protocol", "openai"),
            )

            if not resp_data or "choices" not in resp_data:
                raise Exception("Empty or malformed JSON returned from the model API.")

            choice = resp_data["choices"][0]
            assistant_message = choice["message"]
            tool_calls = assistant_message.get("tool_calls")

            if tool_calls:
                messages.append(assistant_message)
                for tool_call in tool_calls:
                    # Thread pause check before executing tool calls
                    if SwarmController.stop_event.is_set():
                        return
                    SwarmController.resume_event.wait()

                    call_id = tool_call.get("id")
                    func_name = tool_call["function"]["name"]
                    func_args = tool_call["function"]["arguments"]

                    try:
                        args_dict = json.loads(func_args)
                        file_display = args_dict.get(
                            "file_path",
                            args_dict.get(
                                "directory_path", args_dict.get("path", "file")
                            ),
                        )
                    except Exception:
                        file_display = "file"

                    with print_lock:
                        if func_name == "read_project_file":
                            frames = ["◐", "◓", "◑", "◒"]
                            for frame in frames:
                                sys.stdout.write(
                                    f"\r  {Colors.CYAN}{frame} [Reading]{Colors.RESET} {desc_display} (Scanning: {file_display}) "
                                )
                                sys.stdout.flush()
                                time.sleep(0.05)
                            tool_start = time.time()
                            result = execute_tool(func_name, func_args, project_info)
                            tool_duration_ms = int((time.time() - tool_start) * 1000)
                            sys.stdout.write(
                                f"\r  {Colors.CYAN}✓ [Read]{Colors.RESET} {desc_display} ({Colors.GREEN}Done{Colors.RESET})\n"
                            )
                            sys.stdout.flush()
                        elif func_name == "edit_project_file":
                            frames = ["◴", "◷", "◶", "◵"]
                            for frame in frames:
                                sys.stdout.write(
                                    f"\r  {Colors.YELLOW}{frame} [Writing]{Colors.RESET} {desc_display} (Modifying: {file_display}) "
                                )
                                sys.stdout.flush()
                                time.sleep(0.05)
                            tool_start = time.time()
                            result = execute_tool(func_name, func_args, project_info)
                            tool_duration_ms = int((time.time() - tool_start) * 1000)
                            sys.stdout.write(
                                f"\r  {Colors.YELLOW}✓ [Write]{Colors.RESET} {desc_display} ({Colors.GREEN}Done{Colors.RESET})\n"
                            )
                            sys.stdout.flush()
                        else:
                            print(
                                f"  🔧 Agent [{subtask_id}] Tool Call: {func_name}({func_args})"
                            )
                            sys.stdout.flush()
                            tool_start = time.time()
                            result = execute_tool(func_name, func_args, project_info)
                            tool_duration_ms = int((time.time() - tool_start) * 1000)

                    # Trace: record tool call and file operations
                    try:
                        tc = project_info.get("_trace_collector") if project_info else None
                        sid = project_info.get("_trace_session_id") if project_info else None
                        if tc and sid:
                            tool_success = not result.startswith("[Error")
                            tc.record_tool_call(
                                sid, func_name, func_args, result[:200], tool_success, tool_duration_ms
                            )
                            # Detect file read/write operations
                            if func_name == "read_project_file":
                                tc.record_file_op(sid, file_display, "read", len(result))
                            elif func_name in ("edit_project_file", "create_project_file"):
                                tc.record_file_op(sid, file_display, "write", len(result))
                    except Exception:
                        pass

                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call_id,
                            "name": func_name,
                            "content": result,
                        }
                    )
                continue
            else:
                response_content = assistant_message.get("content", "")
                success = True
                break

        results_dict[subtask_id] = {
            "success": success,
            "description": subtask["description"],
            "response": response_content,
            "tool_calls_made": attempts,
            "tokens_in": estimate_tokens(json.dumps(messages)),
            "tokens_out": estimate_tokens(response_content),
        }

        with print_lock:
            if success:
                tokens_in = results_dict[subtask_id]["tokens_in"]
                tokens_out = results_dict[subtask_id]["tokens_out"]
                print(
                    f"  ✅ {Colors.GREEN}Agent [{subtask_id}] Completed:{Colors.RESET} Finished task: '{desc_display}' | {tokens_in} in / {tokens_out} out"
                )
            else:
                reason = (
                    response_content.strip().split("\n")[0][:120]
                    if response_content
                    else "No output details returned."
                )
                print(
                    f"  ❌ {Colors.RED}Agent [{subtask_id}] Failed:{Colors.RESET} '{desc_display}' (Reason: {reason}...)"
                )
            sys.stdout.flush()

    except Exception as e:
        results_dict[subtask_id] = {
            "success": False,
            "description": subtask["description"],
            "response": f"[Error] Agent crashed: {e}",
        }
        with print_lock:
            print(f"  ❌ {Colors.RED}Agent [{subtask_id}] Crashed:{Colors.RESET} {e}")
            sys.stdout.flush()
    finally:
        if is_local:
            local_semaphore.release()


def _orchestrate_worker(user_task, project_info, compiled_models, force_mock=False):
    """The actual background worker thread processing our strategic 4-stage pipeline."""
    # Compile a blank swarm summary to prevent unbound static checks
    swarm_summary = ""

    # Trace: Initialize trace collector for this swarm session
    trace_collector = None
    trace_session_id = None
    try:
        if project_info and project_info.get("root"):
            trace_collector = TraceCollector(project_info["root"])
            trace_session_id = trace_collector.start_session(user_task[:500])
            project_info["_trace_collector"] = trace_collector
            project_info["_trace_session_id"] = trace_session_id
    except Exception:
        trace_collector = None
        trace_session_id = None

    # Dynamic Capability Analysis
    if force_mock:
        planning_model = {"name": "mock", "provider": "Offline", "api_base_url": ""}
        task_model = {"name": "mock", "provider": "Offline", "api_base_url": ""}
    else:
        planning_model, task_model = get_best_models_pool(
            compiled_models,
            active_model_name=project_info.get("_active_model_name") if project_info else None,
        )

    # SDD (Spec-Driven Development) Auto-Detection
    spec_info = None
    compiled_spec = None
    if project_info:
        spec_match = re.search(
            r"(?:specs/|spec\s+)([a-zA-Z0-9_\-]+)", user_task, re.IGNORECASE
        )
        if spec_match:
            feature_name = spec_match.group(1)
            spec_info = load_spec_info(project_info["root"], feature_name)
            if spec_info:
                from .sdd import compile_spec, validate_spec

                # Phase 0: Compile Spec
                compiled_spec = compile_spec(
                    project_info["root"], feature_name, spec_info
                )

                # Phase 1: Validate Spec (Cheap syntax boundary check)
                validation = validate_spec(compiled_spec)

                if validation["errors"]:
                    with print_lock:
                        print(
                            f"  ❌ {Colors.RED}Spec Validation Failed (Syntax Errors):{Colors.RESET}"
                        )
                        for err in validation["errors"]:
                            print(f"     - {err}")
                        print(
                            "\n     Swarm aborted. Please correct your specs and retry.\n"
                        )
                        sys.stdout.flush()
                    # Trace: end session on spec validation failure
                    try:
                        if trace_collector and trace_session_id:
                            trace_collector.end_session(trace_session_id, "failed")
                    except Exception:
                        pass
                    SwarmController.is_active = False
                    return

                with print_lock:
                    print(
                        f"  📋 {Colors.GREEN}Spec-Driven Development Active:{Colors.RESET} Loaded & compiled contract spec.json for feature '{feature_name}'!"
                    )
                    if validation["warnings"]:
                        print(
                            f"  🔍 {Colors.YELLOW}Spec Validation Warnings:{Colors.RESET}"
                        )
                        for warn in validation["warnings"]:
                            print(f"     - {warn}")
                    print()
                    sys.stdout.flush()

    # ==========================================
    # 1. PLAN PHASE
    # ==========================================
    if SwarmController.stop_event.is_set():
        # Trace: end session on user stop
        try:
            if trace_collector and trace_session_id:
                trace_collector.end_session(trace_session_id, "cancelled")
        except Exception:
            pass
        SwarmController.is_active = False
        return
    SwarmController.resume_event.wait()

    spinner = Spinner("Planning task strategy")
    spinner.start()

    plan_prompt = (
        "You are Kognisant's Planning Agent. Analyze the following user task:\n"
        f'"""\n{user_task}\n"""\n\n'
    )

    # Load context files
    from .config import load_project_context, load_project_memory_guidelines

    context_content = (
        load_project_context(project_info["root"]) if project_info else None
    )
    guidelines_content = (
        load_project_memory_guidelines(project_info["root"]) if project_info else None
    )

    if context_content:
        plan_prompt += (
            f"PROJECT BUILD CONTEXT (.kognisant/context.md):\n"
            f"```markdown\n{context_content}\n```\n\n"
        )

    if guidelines_content:
        plan_prompt += (
            f"PROJECT STEERING MEMORY GUIDELINES (.kognisant/memory-guidlines.md):\n"
            f"```markdown\n{guidelines_content}\n```\n\n"
        )

    plan_prompt += (
        "Generate a structured, strategic step-by-step plan to complete this task. "
        "Divide the subtasks into sequential execution phases (using an integer 'phase' field, starting at 1). "
        "Independent research/cataloging tasks must go into earlier phases (e.g., Phase 1). "
        "Drafting/writing/implementation edits must go into intermediate phases (e.g., Phase 2). "
        "Validation and overwrite steps must go into final phases (e.g., Phase 3).\n\n"
        "IMPORTANT DIRECTIVE: Kognisant does not support creating temporary, draft, or staging files (such as README_UPDATED.md). "
        "All file-modifying subtasks must instruct the execution agents to edit or overwrite the target files (like 'README.md') directly.\n\n"
    )

    if spec_info:
        plan_prompt += (
            f"CRITICAL SDD BOUNDARY:\n"
            f"You are implementing the SPECIFICATION for feature '{spec_info['feature']}'.\n"
            f"Requirements:\n```markdown\n{spec_info.get('requirements', 'None')}\n```\n\n"
            f"Design Architecture:\n```markdown\n{spec_info.get('design', 'None')}\n```\n\n"
            f"Spec Task Checklist (tasks.md):\n```markdown\n{spec_info.get('tasks', 'None')}\n```\n\n"
            f"IMPORTANT: You must base your sequential phase subtasks strictly on the checklist provided in tasks.md! "
            f"Do not invent independent structures; translate the tasks.md checklist directly into Kognisant execution phases.\n\n"
        )

    plan_prompt += (
        "You must return your entire response as a valid, parsable JSON block matching this schema:\n"
        "{\n"
        '  "intent": "Brief summary of user task intent",\n'
        '  "beliefs": "What is currently known to be true about the project structure/goal",\n'
        '  "concepts": "Core codebase concepts involved",\n'
        '  "strategy": "Your step-by-step strategic path",\n'
        '  "subtasks": [\n'
        "    {\n"
        '      "description": "Specific instruction for an execution agent to run (e.g. read file X, edit file Y to add function Z)",\n'
        '      "phase": 1\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "Ensure you do not output any surrounding conversation text, only the raw JSON block."
    )

    try:
        if planning_model["name"] == "mock":
            plan_data = {
                "intent": f"Simulated execution of task: '{user_task}'",
                "beliefs": "Running inside unauthenticated offline simulation mode.",
                "concepts": "Mock framework, testing primitives, dry run parameters",
                "strategy": "Formulate mock strategy, execute parallel threads, summarize outcome.",
                "subtasks": [
                    {
                        "description": f"Read codebase files to inspect '{user_task}' context",
                        "phase": 1,
                    },
                    {
                        "description": f"Edit target files sequentially to integrate '{user_task}' components",
                        "phase": 2,
                    },
                ],
            }
        else:
            # Cascading fallback: try each planner candidate until one works
            planner_candidates = _get_planner_candidates(compiled_models)
            plan_content = None
            plan_llm_duration_ms = 0

            for candidate_model in planner_candidates:
                try:
                    plan_llm_start = time.time()
                    plan_content = query_model_api(
                        candidate_model["api_base_url"],
                        candidate_model.get("api_key", ""),
                        candidate_model["name"],
                        [{"role": "user", "content": plan_prompt}],
                        protocol=candidate_model.get("protocol", "openai"),
                    ).strip()
                    plan_llm_duration_ms = int((time.time() - plan_llm_start) * 1000)

                    if plan_content:
                        # Success - update planning_model reference
                        planning_model = candidate_model
                        with print_lock:
                            print(
                                f"  📋 Planner: {Colors.CYAN}{candidate_model['name']}{Colors.RESET}"
                            )
                            sys.stdout.flush()
                        break
                except Exception as e:
                    err_str = str(e)
                    # Auth/payment/rate errors - mark unreachable and try next
                    if "402" in err_str or "401" in err_str or "429" in err_str:
                        _mark_session_unreachable(candidate_model.get("name", ""))
                        with print_lock:
                            print(
                                f"  ⚠️  {Colors.YELLOW}{candidate_model['name']}: {err_str[:60]}. Trying next...{Colors.RESET}"
                            )
                            sys.stdout.flush()
                        continue
                    elif "timeout" in err_str.lower() or "timed out" in err_str.lower():
                        _mark_session_unreachable(candidate_model.get("name", ""))
                        with print_lock:
                            print(
                                f"  ⚠️  {Colors.YELLOW}{candidate_model['name']}: timeout. Trying next...{Colors.RESET}"
                            )
                            sys.stdout.flush()
                        continue
                    else:
                        raise  # Unknown error, propagate

            if not plan_content:
                raise Exception("All models in pool are unreachable or returned empty plans")

            # Trace: record planning LLM call
            try:
                if trace_collector and trace_session_id:
                    prompt_tokens_est = len(plan_prompt) // 4
                    completion_tokens_est = len(plan_content) // 4
                    trace_collector.record_llm_call(
                        trace_session_id,
                        planning_model["name"],
                        prompt_tokens_est,
                        completion_tokens_est,
                        plan_llm_duration_ms,
                    )
            except Exception:
                pass

            if "xml" in plan_content:
                pass
            if "```json" in plan_content:
                plan_content = (
                    plan_content.split("```json", 1)[1].split("```", 1)[0].strip()
                )
            elif "```" in plan_content:
                plan_content = (
                    plan_content.split("```", 1)[1].split("```", 1)[0].strip()
                )

            plan_data = json.loads(plan_content)
    except Exception as e:
        spinner.stop()
        with print_lock:
            print(
                f"  ❌ {Colors.RED}Planning Failed:{Colors.RESET} Could not generate strategic plan. Error: {e}"
            )
            sys.stdout.flush()
        # Trace: end session on planning failure
        try:
            if trace_collector and trace_session_id:
                trace_collector.end_session(trace_session_id, "failed")
        except Exception:
            pass
        SwarmController.is_active = False
        return

    spinner.stop()

    with print_lock:
        print(f"  📝 {Colors.BOLD}Strategic Plan Formulated:{Colors.RESET}")
        print(
            f"     - {Colors.CYAN}Intent:{Colors.RESET} {plan_data.get('intent', 'N/A')}"
        )
        print(
            f"     - {Colors.CYAN}Beliefs:{Colors.RESET} {plan_data.get('beliefs', 'N/A')}"
        )
        print(
            f"     - {Colors.CYAN}Concepts:{Colors.RESET} {plan_data.get('concepts', 'N/A')}"
        )
        print(
            f"     - {Colors.CYAN}Strategy:{Colors.RESET} {plan_data.get('strategy', 'N/A')}\n"
        )
        sys.stdout.flush()

    subtasks = plan_data.get("subtasks", [])
    if not subtasks:
        with print_lock:
            print(
                f"  ⚠️  {Colors.YELLOW}No execution subtasks formulated. Ending pipeline.{Colors.RESET}\n"
            )
            sys.stdout.flush()
        # Trace: end session when no subtasks
        try:
            if trace_collector and trace_session_id:
                trace_collector.end_session(trace_session_id, "completed")
        except Exception:
            pass
        SwarmController.is_active = False
        return

    # ========================================================
    # 2 & 3. EXECUTE & REFLECT PHASES (Corrective Retry Loop)
    # ========================================================
    results_dict = {}
    max_correction_loops = 2
    reflection_content = ""
    reflect_data = {}

    for loop in range(max_correction_loops + 1):
        if SwarmController.stop_event.is_set():
            break
        SwarmController.resume_event.wait()

        phases_dict = {}
        for task in subtasks:
            phase_num = int(task.get("phase", 1))
            if phase_num not in phases_dict:
                phases_dict[phase_num] = []
            phases_dict[phase_num].append(task)

        sorted_phase_keys = sorted(phases_dict.keys())
        idx_counter = 1
        results_dict.clear()

        for phase_num in sorted_phase_keys:
            if SwarmController.stop_event.is_set():
                break
            SwarmController.resume_event.wait()

            phase_tasks = phases_dict[phase_num]

            with print_lock:
                print(
                    f"  ⚡ {Colors.BOLD}Executing Phase {phase_num} Swarm ({len(phase_tasks)} Tasks in Parallel):{Colors.RESET}"
                )
                sys.stdout.flush()

            threads = []
            for task in phase_tasks:
                t = threading.Thread(
                    target=run_subtask_agent,
                    args=(task, task_model, project_info, results_dict, idx_counter),
                    daemon=True,
                )
                threads.append(t)
                idx_counter += 1
                t.start()

            for t in threads:
                t.join()

            with print_lock:
                print(
                    f"  ✅ {Colors.GREEN}Phase {phase_num} Swarm Completed.{Colors.RESET}\n"
                )
                sys.stdout.flush()

        if SwarmController.stop_event.is_set():
            break
        SwarmController.resume_event.wait()

        with print_lock:
            print(
                f"  ✅ {Colors.BOLD}Execution Phase {loop + 1} Swarm Completed.{Colors.RESET}\n"
            )
            sys.stdout.flush()

        swarm_summary = ""
        for idx, res in sorted(results_dict.items()):
            status = "SUCCESS" if res["success"] else "FAILED"
            swarm_summary += f"Subtask [{idx}]: {res['description']} -> Status: {status}\nResponse:\n{res['response']}\n\n"

        # REFLECTION STAGE
        if SwarmController.stop_event.is_set():
            break
        SwarmController.resume_event.wait()

        spinner = Spinner("Reflecting on swarm outcomes")
        spinner.start()

        reflect_prompt = (
            "You are Kognisant's Reflection Agent. Analyze the user's initial task, the strategic plan, and the results of our sequential execution phases.\n\n"
            f'Initial User Task: "{user_task}"\n\n'
            f"Plan Formulated: {json.dumps(plan_data, indent=2)}\n\n"
            f'Swarm Outcomes:\n"""\n{swarm_summary}"""\n\n'
            "Evaluate the results rigorously. Did we successfully complete all strategic goals? Is the code integration functionally sound?\n"
            "You must return your response as a valid, parsable JSON block matching this schema:\n"
            "{\n"
            '  "completed": true or false (true ONLY if all goals are fully met and code is functionally sound, false if we need to retry or correct anything),\n'
            '  "critique": "Comprehensive summary of your critique, calling out what went right, what went wrong, and why",\n'
            '  "adjustments": [\n'
            "    {\n"
            '      "description": "Specific corrective instruction for an agent to run in the next loop to fix the identified issues (e.g. Re-run subtask X with feedback Y)",\n'
            '      "phase": 1\n'
            "    }\n"
            "  ]\n"
            "}\n\n"
            "Ensure you do not output any surrounding conversation text, only the raw JSON block."
        )

        try:
            if planning_model["name"] == "mock":
                reflect_data = {
                    "completed": True,
                    "critique": "Simulation check passed. All modular objectives successfully accomplished.",
                    "adjustments": [],
                }
            else:
                reflect_llm_start = time.time()
                reflection_content = query_model_api(
                    planning_model["api_base_url"],
                    planning_model.get("api_key", ""),
                    planning_model["name"],
                    [{"role": "user", "content": reflect_prompt}],
                    protocol=planning_model.get("protocol", "openai"),
                ).strip()
                reflect_llm_duration_ms = int((time.time() - reflect_llm_start) * 1000)

                # Trace: record reflection LLM call
                try:
                    if trace_collector and trace_session_id:
                        prompt_tokens_est = len(reflect_prompt) // 4
                        completion_tokens_est = len(reflection_content) // 4
                        trace_collector.record_llm_call(
                            trace_session_id,
                            planning_model["name"],
                            prompt_tokens_est,
                            completion_tokens_est,
                            reflect_llm_duration_ms,
                        )
                except Exception:
                    pass

                if "```json" in reflection_content:
                    reflection_content = (
                        reflection_content.split("```json", 1)[1]
                        .split("```", 1)[0]
                        .strip()
                    )
                elif "```" in reflection_content:
                    reflection_content = (
                        reflection_content.split("```", 1)[1].split("```", 1)[0].strip()
                    )

                reflect_data = json.loads(reflection_content)
        except Exception as e:
            reflect_data = {
                "completed": True,
                "critique": f"Could not parse critique: {e}. Defaulting to completed.",
                "adjustments": [],
            }

        spinner.stop()

        with print_lock:
            print(
                f"  🔍 {Colors.BOLD}Reflection Summary Loop {loop + 1}:{Colors.RESET}"
            )
            print(
                f"     {reflect_data.get('critique', 'No critique summary provided.')}\n"
            )
            sys.stdout.flush()

        if reflect_data.get("completed", True):
            break

        adjustments = reflect_data.get("adjustments", [])
        if not adjustments or loop == max_correction_loops:
            with print_lock:
                print(
                    f"  ❌ {Colors.RED}Self-Correction limit reached or no adjustments formulated. Proceeding to persistence...{Colors.RESET}\n"
                )
                sys.stdout.flush()
            break

        with print_lock:
            print(
                f"  ⚠️  {Colors.YELLOW}Reflection Rejected Outcomes. Initiating Self-Correction Loop {loop + 1}/{max_correction_loops}...{Colors.RESET}\n"
            )
            sys.stdout.flush()
        subtasks = adjustments

    # ==========================================
    # 4. PERSIST PHASE (Self-modeling context)
    # ==========================================
    if SwarmController.stop_event.is_set():
        # Trace: end session on user stop before persist
        try:
            if trace_collector and trace_session_id:
                trace_collector.end_session(trace_session_id, "cancelled")
        except Exception:
            pass
        SwarmController.is_active = False
        return
    SwarmController.resume_event.wait()

    if not project_info:
        # Trace: end session when no project_info
        try:
            if trace_collector and trace_session_id:
                trace_collector.end_session(trace_session_id, "completed")
        except Exception:
            pass
        SwarmController.is_active = False
        return

    spinner = Spinner("Persisting memory and context changes")
    spinner.start()

    persist_prompt = (
        "You are Kognisant's Persistence Agent. Evaluate the completed task, our plan, the reflection, and the codebase files.\n"
        "We need to update our memories:\n"
        "1. Local Membrain (.kognisant/context.md): What project phases, checkbox tasks, or decisions should be marked completed or modified?\n"
        "2. Global Core Memory (~/.kognisant_core/skills/): Is there any universal, transferable coding skill, pattern, or lesson learned we should save as a Markdown skill file so we can reuse it across other projects?\n\n"
        f'User Task: "{user_task}"\n\n'
        f'Reflection: "{reflect_data.get("critique", "")}"\n\n'
        "Output a valid JSON block containing updates to apply:\n"
        "{\n"
        '  "project_context_update": "A concise instruction on how to update .kognisant/context.md (e.g. check off task X, move phase Y to completed)",\n'
        '  "global_skill_title": "Title of any new transferable skill discovered (e.g. pytest_mock_conventions). Leave empty if none.",\n'
        '  "global_skill_content": "Full markdown text of the transferable skill to save in ~/.kognisant_core/skills/<title>.md. Leave empty if none."\n'
        "}\n\n"
        "Ensure you only output the raw JSON block."
    )

    try:
        if planning_model["name"] == "mock":
            persist_raw = (
                "{\n"
                '  "project_context_update": "Check off simulated task items inside .kognisant/context.md.",\n'
                '  "global_skill_title": "simulated_learning_caps",\n'
                '  "global_skill_content": "# Simulated Learning Cards\\n\\n- Accomplished dry run execution of agent swarm."\n'
                "}"
            )
        else:
            persist_llm_start = time.time()
            persist_raw = query_model_api(
                planning_model["api_base_url"],
                planning_model.get("api_key", ""),
                planning_model["name"],
                [{"role": "user", "content": persist_prompt}],
                protocol=planning_model.get("protocol", "openai"),
            )
            persist_llm_duration_ms = int((time.time() - persist_llm_start) * 1000)

            # Trace: record persistence LLM call
            try:
                if trace_collector and trace_session_id:
                    prompt_tokens_est = len(persist_prompt) // 4
                    completion_tokens_est = len(persist_raw) // 4 if persist_raw else 0
                    trace_collector.record_llm_call(
                        trace_session_id,
                        planning_model["name"],
                        prompt_tokens_est,
                        completion_tokens_est,
                        persist_llm_duration_ms,
                    )
            except Exception:
                pass

        if not persist_raw:
            raise Exception("No response received from the Persistence Agent.")

        persist_raw = persist_raw.strip()
        if "```json" in persist_raw:
            persist_raw = persist_raw.split("```json", 1)[1].split("```", 1)[0].strip()
        elif "```" in persist_raw:
            persist_raw = persist_raw.split("```", 1)[1].split("```", 1)[0].strip()

        persist_data = json.loads(persist_raw)

        # Apply Project Memory updates (self-modifying context!)
        context_path = os.path.join(project_info["root"], ".kognisant", "context.md")
        context_update_inst = persist_data.get("project_context_update", "").strip()

        if context_update_inst and os.path.exists(context_path):
            with open(context_path, "r", encoding="utf-8") as f:
                old_context = f.read()

            if planning_model["name"] == "mock":
                updated_context = (
                    old_context + f"\n- [x] {user_task} (Simulated Completed)"
                )
            else:
                mod_prompt = (
                    f"Modify this .kognisant/context.md file according to the following instruction:\n"
                    f'Instruction: "{context_update_inst}"\n\n'
                    f"Original context.md:\n```markdown\n{old_context}\n```\n\n"
                    "Return ONLY the updated context.md markdown content. Do not include conversational text or wrapping codeblocks."
                )

                updated_context = query_model_api(
                    planning_model["api_base_url"],
                    planning_model.get("api_key", ""),
                    planning_model["name"],
                    [{"role": "user", "content": mod_prompt}],
                    protocol=planning_model.get("protocol", "openai"),
                ).strip()

                # Trace: record context modification LLM call
                try:
                    if trace_collector and trace_session_id:
                        mod_tokens_est = len(mod_prompt) // 4
                        mod_completion_est = len(updated_context) // 4
                        trace_collector.record_llm_call(
                            trace_session_id,
                            planning_model["name"],
                            mod_tokens_est,
                            mod_completion_est,
                            0,
                        )
                except Exception:
                    pass

                if "```markdown" in updated_context:
                    updated_context = (
                        updated_context.split("```markdown", 1)[1]
                        .split("```", 1)[0]
                        .strip()
                    )
                elif "```" in updated_context:
                    updated_context = (
                        updated_context.split("```", 1)[1].split("```", 1)[0].strip()
                    )

            with open(context_path, "w", encoding="utf-8") as f:
                f.write(updated_context)

            with print_lock:
                print(
                    f"  💾 {Colors.GREEN}Project Membrain Saved:{Colors.RESET} Updated '.kognisant/context.md' task items autonomously."
                )
                sys.stdout.flush()

        # Apply SDD (Spec-Driven Development) Task Checklist updates autonomously (Phase 4)!
        if spec_info and compiled_spec:
            # Update task states based on result outcomes
            for idx, res in results_dict.items():
                if res["success"]:
                    for task in compiled_spec.get("tasks", []):
                        if (
                            task["description"].lower() in res["description"].lower()
                            or res["description"].lower() in task["description"].lower()
                        ):
                            task["completed"] = True

            # Calculate overall state
            total_tasks = len(compiled_spec.get("tasks", []))
            done_tasks = sum(
                1 for t in compiled_spec.get("tasks", []) if t["completed"]
            )
            if done_tasks == total_tasks:
                compiled_spec["task_state"] = "COMPLETED"
            elif done_tasks > 0:
                compiled_spec["task_state"] = "IN_PROGRESS"

            # Write back compiled spec.json contract
            spec_json_path = os.path.join(spec_info["root"], "spec.json")
            with open(spec_json_path, "w", encoding="utf-8") as f:
                json.dump(compiled_spec, f, indent=2)

            # Generate the human-readable tasks.md scratchpad log directly from the contract spec.json!
            tasks_path = os.path.join(spec_info["root"], "tasks.md")
            with open(tasks_path, "w", encoding="utf-8") as f:
                f.write(f"# Tasks Checklist — {spec_info['feature']}\n\n")
                f.write(
                    f"Last updated: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n"
                )
                f.write(
                    f"Swarm Status: {compiled_spec['task_state']} ({done_tasks}/{total_tasks} completed)\n\n"
                )
                for task in compiled_spec.get("tasks", []):
                    check = "[x]" if task["completed"] else "[ ]"
                    f.write(f"- {check} {task['description']}\n")

            with print_lock:
                print(
                    f"  💾 {Colors.GREEN}Spec Membrain Saved:{Colors.RESET} Updated '.kognisant/specs/{spec_info['feature']}/tasks.md' autonomously."
                )
                sys.stdout.flush()

        # Apply Global Memory updates (transferable skills!)
        skill_title = (
            persist_data.get("global_skill_title", "").strip().lower().replace(" ", "_")
        )
        skill_content = persist_data.get("global_skill_content", "").strip()

        if skill_title and skill_content:
            if not skill_title.endswith(".md"):
                skill_title = f"{skill_title}.md"

            skills_dir = os.path.join(GLOBAL_CORE_DIR, "skills")
            os.makedirs(skills_dir, exist_ok=True)
            skill_path = os.path.join(skills_dir, skill_title)

            with open(skill_path, "w", encoding="utf-8") as f:
                f.write(skill_content)

            with print_lock:
                print(
                    f"  💾 {Colors.GREEN}Global Core Memory Saved:{Colors.RESET} Registered new transferable skill: '~/.kognisant_core/skills/{skill_title}'"
                )
                sys.stdout.flush()

    except Exception as e:
        with print_lock:
            print(
                f"  ⚠️  {Colors.YELLOW}Persistence Warning:{Colors.RESET} Memory update failed: {e}"
            )
            sys.stdout.flush()

    # ==========================================
    # 4b. PERSIST PHASE — World Model Integration
    # ==========================================
    # After existing PERSIST logic completes, reinforce world model edges
    # based on traced file operations and check for new goals.
    # Guarded by world_model_enabled config flag. Never interrupts PERP flow.
    if project_info and is_world_model_enabled(project_info["root"]):
        try:
            store = load_world_model(project_info["root"])
            graph_data = store.load_graph()

            # Only proceed if graph has content (skip gracefully on first run)
            if graph_data.get("nodes") or graph_data.get("edges"):
                from .models import Node as NodeModel, Edge as EdgeModel
                from .world_model import (
                    DependencyGraph,
                    BeliefSystem,
                    ContractRegistry,
                    EpistemicGapTracker,
                    GraphMaintenanceEngine,
                )
                from .goal_engine import GoalGenerator

                # Reconstruct in-memory graph from stored data
                graph = DependencyGraph()
                for node_dict in graph_data.get("nodes", []):
                    try:
                        graph.add_node(NodeModel.from_dict(node_dict))
                    except (KeyError, TypeError):
                        continue
                for edge_dict in graph_data.get("edges", []):
                    try:
                        graph.add_edge(EdgeModel.from_dict(edge_dict))
                    except (KeyError, TypeError):
                        continue

                # Load beliefs, contracts, gaps
                beliefs = BeliefSystem()
                for b_dict in store.load_beliefs():
                    try:
                        from .models import Belief
                        beliefs.add_belief(Belief.from_dict(b_dict))
                    except (KeyError, TypeError):
                        continue

                contracts = ContractRegistry()
                for c_dict in store.load_contracts():
                    try:
                        from .models import Contract
                        contracts.register_contract(Contract.from_dict(c_dict))
                    except (KeyError, TypeError):
                        continue

                gaps = EpistemicGapTracker()
                for g_dict in store.load_gaps():
                    try:
                        from .models import EpistemicGap
                        gaps.record_gap(EpistemicGap.from_dict(g_dict))
                    except (KeyError, TypeError):
                        continue

                # Extract traced edge ids: find edges connected to files touched in this session
                traced_edge_ids = []
                if trace_collector and trace_session_id:
                    # Drain the queue to get up-to-date file operations
                    trace_collector._drain_queue()
                    with trace_collector._lock:
                        session_record = trace_collector._sessions.get(trace_session_id)
                        if session_record:
                            # Collect file paths from buffer and record
                            touched_files = set()
                            # From already-applied file_operations
                            for fop in session_record.file_operations:
                                touched_files.add(fop.file_path)
                            # From buffer (not yet applied)
                            for trace_type, trace_item in trace_collector._buffers.get(trace_session_id, []):
                                if trace_type == "file_op":
                                    touched_files.add(trace_item.file_path)

                            # Map touched files to graph edges
                            # Find nodes in those files, then find edges connected to them
                            touched_node_ids = set()
                            for node in graph._nodes.values():
                                if node.file_path in touched_files:
                                    touched_node_ids.add(node.id)

                            # Collect edge ids connected to touched nodes
                            for node_id in touched_node_ids:
                                for edge_id in graph._edges_from.get(node_id, set()):
                                    traced_edge_ids.append(edge_id)
                                for edge_id in graph._edges_to.get(node_id, set()):
                                    traced_edge_ids.append(edge_id)

                # Reinforce edges that were touched during this session
                if traced_edge_ids:
                    maintenance = GraphMaintenanceEngine(graph, beliefs, contracts, gaps)
                    maintenance.reinforce_edges(traced_edge_ids)

                # Generate new goals based on current world model state
                generator = GoalGenerator(graph, contracts, gaps, beliefs, store)
                generator.generate_goals()

                # Save updated world model state
                # Serialize graph back to dict format
                save_graph_data = {
                    "nodes": [n.to_dict() for n in graph._nodes.values()],
                    "edges": [e.to_dict() for e in graph._edges.values()],
                }
                store.save_graph(save_graph_data)
                store.save_beliefs([b.to_dict() for b in beliefs._beliefs.values()])
                store.save_contracts([c.to_dict() for c in contracts._contracts.values()])

        except Exception as e:
            # Never interrupt PERP flow
            logger.warning("World model integration skipped: %s", e)

    spinner.stop()
    with print_lock:
        # Print swarm completion summary with token breakdown
        total_tokens_in = 0
        total_tokens_out = 0
        total_tool_calls = 0
        artifacts = []

        # Collect per-worker stats
        worker_stats = []
        for sid, result in sorted(results_dict.items()):
            tokens_in = result.get("tokens_in", 0)
            tokens_out = result.get("tokens_out", 0)
            tool_calls = result.get("tool_calls_made", 0)
            total_tokens_in += tokens_in
            total_tokens_out += tokens_out
            total_tool_calls += tool_calls
            worker_stats.append({
                "id": sid,
                "description": result.get("description", "")[:40],
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "success": result.get("success", False),
            })

        # Collect artifacts (files created/modified by agents)
        if project_info and project_info.get("root"):
            try:
                tc = project_info.get("_trace_collector")
                sid_trace = project_info.get("_trace_session_id")
                if tc and sid_trace and hasattr(tc, "get_file_ops"):
                    file_ops = tc.get_file_ops(sid_trace)
                    for op in file_ops:
                        artifacts.append(op)
            except Exception:
                pass

        print(f"\n  {Colors.BOLD}🐝 Swarm Complete ({len(results)} subtasks){Colors.RESET}")
        print(f"  ┌{'─' * 72}┐")
        print(f"  │ {'Token Usage':<70} │")
        print(f"  ├{'─' * 72}┤")
        for ws in worker_stats:
            icon = "✓" if ws["success"] else "✗"
            desc = ws["description"][:35]
            line = f"    {icon} Worker {ws['id']}: {ws['tokens_in']:>6,} in / {ws['tokens_out']:>5,} out  ({desc})"
            print(f"  │ {line:<70} │")
        print(f"  ├{'─' * 72}┤")
        total_line = f"    Total: {total_tokens_in:>6,} in / {total_tokens_out:>5,} out | {total_tool_calls} tool calls"
        print(f"  │ {total_line:<70} │")

        if artifacts:
            print(f"  ├{'─' * 72}┤")
            art_header = f"  📄 Artifacts ({len(artifacts)}):"
            print(f"  │ {art_header:<70} │")
            for art in artifacts[:10]:
                action = art.get("action", "modified")
                path = art.get("path", "unknown")[:55]
                icon = "✓" if action in ("created", "write") else "~" if action in ("modified", "read") else "✗"
                art_line = f"    {icon} {action:<10} {path}"
                print(f"  │ {art_line:<70} │")

        print(f"  └{'─' * 72}┘")
        print(
            f"\n  ✨ {Colors.BOLD}PERP Swarm Process Finished Successfully!{Colors.RESET}\n"
        )
        sys.stdout.flush()

    # Trace: end session on successful completion
    try:
        if trace_collector and trace_session_id:
            trace_collector.end_session(trace_session_id, "completed")
    except Exception:
        pass

    # Release global active flag
    SwarmController.is_active = False


def perp_orchestrate(user_task, project_info, compiled_models, force_mock=False):
    """Orchestrates and launches the PERP Swarm pipeline on an asynchronous background thread."""
    # Enforce non-blocking: Check if another swarm is already running
    if SwarmController.is_active:
        print(
            f"\n  ⚠️  {Colors.YELLOW}Wait:{Colors.RESET} Another background swarm is currently executing. Type {Colors.CYAN}/status{Colors.RESET} or {Colors.CYAN}/stop{Colors.RESET}.\n"
        )
        return

    # Reset thread-safe Controller Events
    SwarmController.stop_event.clear()
    SwarmController.resume_event.set()
    SwarmController.is_active = True
    SwarmController.is_paused = False
    SwarmController.active_task_description = user_task

    # Spawn and start the background thread worker instantly!
    t = threading.Thread(
        target=_orchestrate_worker,
        args=(user_task, project_info, compiled_models, force_mock),
        daemon=True,
    )
    t.start()
