# Kognisant 🧠 — Autonomous, Model-Agnostic Developer Copilot CLI

[![Python](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
![Project Status](https://img.shields.io/badge/status-active-success.svg)

A dependency-free Python CLI that transforms your terminal into an autonomous coding environment. Kognisant connects seamlessly to local LLMs via **Ollama Native**, **Llama.cpp**, and any **OpenAI-compatible API** (OpenAI, DeepSeek, OpenRouter, Groq, and custom endpoints). It features dynamic ANSI animations, persistent local and global memory layers (“Membrain”), built-in agentic tool execution, headless web browsing, and an interactive multi-turn chat with checkpoint-based rollback.

---

## 📑 Table of Contents

- [Project Overview](#project-overview)
- [Key Features](#key-features)
- [Project Structure](#project-structure)
- [Installation and Quickstart](#installation-and-quickstart)
- [Usage Examples](#usage-examples)
- [In-Chat Slash Commands](#in-chat-slash-commands)
- [Autonomous Agent Toolkit](#autonomous-agent-toolkit)
- [Global Core Folder Layout](#global-core-folder-layout)
- [Security](#security)
- [Environment Variables](#environment-variables)
- [Troubleshooting](#troubleshooting)
- [Test Plan](#test-plan)
- [Contributing](#contributing)
- [License](#license)

---

## 📖 Project Overview

Kognisant is a terminal-native AI assistant designed for software engineers who want model-agnostic, privacy-conscious tooling without external bloat. Built entirely on the Python standard library, it requires zero runtime dependencies and runs on Python 3.8+.

Core design goals:
- **Model Agnostic** — Switch between Ollama, OpenAI, DeepSeek, or any custom endpoint mid-session.
- **Workspace Aware** — Every project gets a local `.kognisant/` “Membrain” that persists build context across sessions.
- **Agentic by Default** — A built-in PERP swarm (`/agent <task>`) can read files, edit code, browse the web, and autonomously reflect on its own work.
- **Resilient & Safe** — Checkpoint rollbacks, directory-traversal protection, and exponential-backoff API retries keep your sessions stable.

---

## 🚀 Key Features

### Terminal UX
- **2-Second True-Color Fade-In Logo** — ASCII art intro with RGB ANSI animation (`cli_kognisant/colors.py`).
- **Collapsible Input Cards** — A bordered typing frame that collapses to a clean prompt on Enter.
- **Thread-Safe Spinners** — Smooth Braille spinners during model inference.

### Multi-Provider AI Support
- **100% Model-Agnostic** — Chat with Ollama (Native), Llama.cpp (Native), OpenAI, DeepSeek, or add any custom OpenAI-compatible endpoint on the fly.
- **Interactive Model Wizard** — Switch models mid-session, add new endpoints, and securely persist API keys globally.
- **Sticky Default Model** — Your last selected model is automatically persisted in `~/.kognisant_core/models_pool.json`. The next time you launch `kognisant chat`, it resumes with your preferred endpoint—no re-selection required (`config.py`).
- **Dynamic Capability Routing** — The PERP swarm automatically delegates planning to cloud models and tasks to local models when available (`agents.py`).
- **Offline Mock Mode** — When no APIs are configured, Kognisant falls back to a fully functional mock chat session.

### Memory Architecture
- **Local Membrain (`.kognisant/`)** — Initialize per-project workspaces that track tasks, milestones, and architectural decisions in `context.md`. Auto-loaded as system context on startup.
- **Global Core (`~/.kognisant_core/`)** — Cross-project registry, provider configs, model pool, and transferable skill files (Markdown) dynamically injected into every session.
- **Zero-Leak Security** — Project context is sandboxed; no cross-project bleed.

### Agentic Tool Execution
- **Workspace Tools** — Securely read (`read_project_file`), list (`list_project_files`), edit (`edit_project_file`), create (`create_project_file`/`create_project_directory`), and delete (`delete_project_path`) files directly in your project root workspace (`tools.py`).
- **Web & Search Tools** — Headless webpage extraction (via Brave/Chrome if installed), native desktop browser control, and background DuckDuckGo search.
- **PERP Swarm Pipeline** — Invoke `/agent <task>` to trigger an autonomous **Plan → Execute → Reflect → Persist** workflow powered by threaded subtask agents (`agents.py`).

### Reliability & Safety
- **Checkpoint-Based Rollback** — If a tool call or API failure occurs, conversation state rolls back to the pre-turn checkpoint (`chat.py`).
- **Directory Traversal Protection** — All file operations resolve canonical paths via `os.path.realpath` and are strictly sandboxed to the project root.
- **Local Concurrency Throttling** — Semaphore-based limits for local Ollama tasks to prevent system overload (`agents.py`).
- **Exponential Backoff & Retry** — Resilient API transport with automatic retries on transient HTTP errors (`network.py`).

---

## 📂 Project Structure

```text
cli-kognisant/
├── pyproject.toml                 # Build system & project metadata
├── README.md                      # Project documentation
├── cli_kognisant/
│   ├── __init__.py                # Package version (0.1.0)
│   ├── main.py                    # CLI entry point (argparse subcommands: init, chat, greet, spec, awesome_feature)
│   ├── config.py                  # Project/global config, Membrain & Core initialization
│   ├── chat.py                    # Interactive chat loop, slash commands, model selection
│   ├── agents.py                  # PERP orchestration & subtask agent swarm
│   ├── network.py                 # OpenAI-compatible API client with backoff & Ollama auto-detection
│   ├── tools.py                   # Tool specifications & execution (file, web, browser, search)
│   └── colors.py                  # ANSI color palette, spinner, animated logo, boxed input
├── cli_kognisant.egg-info/        # Package metadata generated by setuptools
├── tests/                         # Unit tests (unittest, zero third-party deps)
│   ├── __init__.py
│   ├── test_agents.py
│   ├── test_config.py
│   └── test_tools.py
└── ...
```

---

## 📦 Installation and Quickstart

Requires **Python 3.8+**. Zero external runtime dependencies.

### Editable / Development Mode

```bash
git clone https://github.com/<your-username>/cli-kognisant.git
cd cli-kognisant
pip install -e .
```

### Standard Installation

```bash
pip install .
```

This registers the `kognisant` console script globally.

---

## 🛠️ Usage Examples

### 1. Initialize a Workspace

Inside your project directory, create the local Membrain:

```bash
kognisant init
```

**Generates:**
- `.kognisant/config.json` — Workspace name and file exclusion patterns.
- `.kognisant/context.md` — Persistent build memory template.
- `.kognisant/history/` — Crash-resilient saved session logs.

### 2. Scaffold a Feature Spec (SDD)

Inside an initialized workspace:

```bash
kognisant spec my-new-feature
```

**Generates:**
- `.kognisant/specs/my-new-feature/requirements.md` — Feature requirements and success criteria.
- `.kognisant/specs/my-new-feature/design.md` — Architecture and interface contract.
- `.kognisant/specs/my-new-feature/tasks.md` — Phased implementation checklist.

List all existing specs:

```bash
kognisant spec --list
```

### 3. Launch an AI Chat Session

```bash
kognisant chat
```

- Pre-configured endpoints for Ollama, OpenAI, DeepSeek, and custom OpenAI-compatible APIs.
- Prompts you to select or add a model if none is configured.
- **Sticky default:** If you previously switched models via `/model`, that choice is restored automatically from `~/.kognisant_core/models_pool.json` on the next launch.
- Supports offline **Mock Chat** mode when no APIs are available.

### 4. Basic Greeting Subcommand

```bash
kognisant greet --name "Developer" --verbose
```

### 5. Awesome Feature Subcommand

```bash
kognisant awesome_feature --level 5
```

**Output:**
- Prints an awesome, colored engagement message with a configurable intensity level (1–10).
- Defaults to level `1` if `--level` is omitted.

---

## 💬 In-Chat Slash Commands

While in a chat session, type:

| Command | Description |
|---|---|
| `/help` | Show available commands and descriptions. |
| `/clear` | Clear conversation history while preserving the system prompt. |
| `/context` | Display local `.kognisant/context.md` (Membrain). |
| `/skills` | List global transferable skills loaded from `~/.kognisant_core/skills/`. |
| `/model` | Switch active model or add a custom OpenAI-compatible endpoint. |
| `/providers` | Inspect configured providers, URLs, and API key statuses. |
| `/files` | List all indexed files in the workspace. |
| `/read <path>` | Load a project file into the conversation context. |
| `/agent <task>` | Dispatch the autonomous PERP swarm (Plan → Execute → Reflect → Persist). |

---

## 🤖 Autonomous Agent Toolkit

Models in Kognisant can invoke tools directly via the OpenAI-compatible `tools` channel:

- **`read_project_file(file_path)`** — Safely reads a project file. Enforces workspace root sandboxing (`tools.py`).
- **`edit_project_file(file_path, edits)`** — Applies precise find-and-replace edits sequentially. Fails if `old_text` is not found exactly (`tools.py`).
- **`list_project_files()`** — Returns the workspace file tree.
- **`browse_web_page(url)`** — Fetches and cleans HTML. Uses headless Brave/Chrome for JS rendering if available; falls back to `urllib` (`tools.py`).
- **`search_web(query)`** — Headless DuckDuckGo search returning plain text snippets.
- **`open_in_native_browser(query_or_url)`** — Opens a URL or Google search in your default desktop browser.

---

## 🌍 Global Core Folder Layout

Kognisant maintains a minimal global footprint in your home directory:

```text
~/.kognisant_core/
├── projects.json          # Global workspace registry (paths, names, and timestamps)
├── models_pool.json       # Active/selectable model configurations
├── skills/                # Transferable coding standards & lessons
│   └── coding_standards.md
└── tools/                 # Globally transferable custom tool specifications & scripts
    ├── my_custom_tool.json # OpenAI-compatible tool JSON specification schema
    └── my_custom_tool.py   # Isolated Python subprocess implementation script
```

---

## 🔒 Security

- **Realpath Verification:** Every file operation uses `os.path.realpath` to ensure the target resides inside the project root.
- **No Temporary Staging:** Agents are forbidden from creating draft or staging files; they edit or overwrite target files directly.
- **API Key Masking:** Keys are stored locally in your home directory and are never hard-coded or transmitted beyond their intended endpoints.

---

## 🌱 Environment Variables

Kognisant is designed to require **zero application-specific environment variables**. All configuration lives in local JSON files under `~/.kognisant_core/` and per-project `.kognisant/` directories.

However, the following standard operating-system variables are relied upon implicitly:

| Variable | Purpose |
|---|---|
| `HOME` / `USERPROFILE` | Resolves the `~` expansion used for the global core memory directory (`~/.kognisant_core/`). |
| `PWD` / `CWD` | Determines the current working directory for `kognisant init` and workspace discovery. |

> **Note:** Ollama is assumed to be reachable at `http://localhost:11434`. If your Ollama instance runs on a different host or port, update `providers.json` manually or via the `/model` wizard.

---

## 🧯 Troubleshooting

| Symptom | Likely Cause | Resolution |
|---|---|---|
| `Connection refused` when selecting an Ollama model | Ollama server is not running. | Start Ollama locally (`ollama serve`) and ensure it is reachable at `http://localhost:11434`. |
| `API HTTP Error 401` or `Invalid API Key` | Placeholder key in `providers.json` has not been replaced. | Run `kognisant chat`, use `/model` to select the provider, and paste your real key when prompted. |
| `No active project detected` | You are outside a directory initialized with `kognisant init`. | Run `kognisant init` in your project root to create the `.kognisant/` Membrain. |
| `Permission denied` creating `.kognisant/` or `~/.kognisant_core/` | Insufficient filesystem permissions. | Ensure your user has write access to the project directory and your home folder. |
| Rollback or "API Transport Failure" after a tool call | Transient network error or invalid tool payload. | The conversation automatically reverts to the pre-turn checkpoint. Retry your message, or check network connectivity. |
| Python `SyntaxError` on launch | Python version is below 3.8. | Upgrade to Python 3.8 or newer. |

---

## 🧪 Test Plan

This section provides a comprehensive, implementation-aware test strategy for Kognisant. It covers **happy-path validation**, **failure-triggered rollback verification**, **pre-operation state restoration assertions**, **cascading failure scenarios**, and **resource cleanup validation**.

All tests are designed to be implemented with Python's built-in `unittest` and `unittest.mock` modules (zero third-party dependencies), preserving the project's commitment to the standard library.

---

### 1. Successful Paths (Happy-Path Validation)

#### 1.1 CLI Subcommands
| Test Case | Steps | Expected Result |
|---|---|---|
| `init` in fresh directory | Run `kognisant init` in an empty directory | Creates `.kognisant/config.json`, `.kognisant/context.md`, `.kognisant/history/`, and registers the project in `~/.kognisant_core/projects.json` |
| `init` in already-initialized directory | Run `kognisant init` again | Prints warning; no duplicate files created; no exceptions raised |
| `greet` | Run `kognisant greet --name Alice --verbose` | Prints `Hello, Alice!` and verbose stderr output |
| `chat` with no configured models | Run `kognisant chat` with empty `models_pool.json` | Falls back to offline Mock Chat mode; interactive loop functions with canned responses |

#### 1.2 Configuration & Global Core
| Test Case | Steps | Expected Result |
|---|---|---|
| `init_global_core()` first run | Remove `~/.kognisant_core/` and call function | Creates `providers.json`, `models_pool.json`, `projects.json`, and default `skills/coding_standards.md` with correct schema |
| `init_global_core()` idempotency | Call function twice | Second call is a no-op; existing files are not overwritten or corrupted |
| `register_project_globally()` | Invoke with a temporary project path | Updates `projects.json` with correct `initialized_at` and `last_accessed` timestamps; preserves existing entries |
| `get_compiled_models()` | Load after initialization | Returns the 3 default models (Ollama gemma3:1b, OpenAI gpt-4o-mini, DeepSeek deepseek-chat) with all required dict keys |
| Sticky default model persistence | Select a model via `/model` or `set_default_model()` | `models_pool.json` contains `default_model` field; `get_default_model()` returns the same model on next load |

#### 1.3 Project Discovery & File Scanning
| Test Case | Steps | Expected Result |
|---|---|---|
| `find_project_root()` from nested child dir | Create `.kognisant/` in parent; run from child subdir | Returns the absolute path of the parent directory containing `.kognisant/` |
| `find_project_root()` outside project | Run from `/tmp` with no `.kognisant/` ancestors | Returns `None` |
| `scan_project_files()` with exclusions | Create files under `.git`, `__pycache__`, `node_modules` | Excluded patterns are omitted from the returned list; normal files are included |
| `get_project_info()` | Initialize a project and call function | Returns dict with `root`, `name`, and `files`; `files` is sorted and contains relative paths |

#### 1.4 Tool Execution (`tools.py`)
| Test Case | Steps | Expected Result |
|---|---|---|
| `read_project_file()` | Request read of an existing project file | Returns exact file content as string |
| `edit_project_file()` single edit | Provide one `old_text` → `new_text` pair | File is atomically overwritten; only the first exact match is replaced |
| `edit_project_file()` sequential edits | Provide two edits targeting different lines | Both edits apply in order; final file content matches expectations |
| `list_project_files()` | Invoke in a workspace with tracked files | Returns a JSON string array of all project-relative file paths |
| `browse_web_page()` — urllib fallback | Mock `find_chrome_or_brave()` to return `None`, request a `file://` or HTTP URL | Returns cleaned HTML text; gracefully skips headless browser subprocess |
| `search_web()` | Mock `urllib.request.urlopen` with synthetic DuckDuckGo HTML | Returns cleaned text truncated correctly at 8000 chars |
| `open_in_native_browser()` | Mock `webbrowser.open` | Returns success message; `webbrowser.open` called with the correctly URL-encoded query string |

#### 1.5 Network Layer (`network.py`)
| Test Case | Steps | Expected Result |
|---|---|---|
| `query_model_api_raw()` 200 OK | Mock `urllib.request.urlopen` with valid OpenAI JSON | Returns parsed dict with `choices` list |
| Exponential backoff success on retry | Mock `urlopen` to fail with `HTTPError(503, ...)` on attempts 1–2, then succeed on attempt 3 | Function sleeps with exponentially increasing delays (`1.0s`, `2.0s`) and ultimately returns the successful payload |
| `query_model_api()` content extraction | Mock `query_model_api_raw()` with a standard `choices[0].message.content` payload | Returns the assistant's content string |
| `get_ollama_models()` available | Mock Ollama `/api/tags` endpoint with a JSON model list | Returns `['gemma3:1b', 'llama3:latest']` |

#### 1.6 Chat Loop (`chat.py`)
| Test Case | Steps | Expected Result |
|---|---|---|
| `/clear` command | Append several messages; send `/clear` | Messages list is cleared except for the first system prompt element |
| `/help` command | Send `/help` | Prints formatted help text; returns to prompt |
| `/context` command | Send `/context` in a project | Prints contents of `.kognisant/context.md` |
| `/files` command | Send `/files` | Prints each file in `project_info['files']` |
| `/read <path>` | Send `/read README.md` | Loads file content as a system message into the conversation list |
| `/agent <task>` in mock mode | Send `/agent write test cases` with `is_mock=True` | `perp_orchestrate()` runs in mock mode; updated context is injected back into chat history |
| Tool call chat turn | Simulate LLM response containing `tool_calls` for `read_project_file` | Tool is executed via `execute_tool`; tool response appended; assistant final response appended; session saved after each step |

#### 1.7 PERP Swarm (`agents.py`)
| Test Case | Steps | Expected Result |
|---|---|---|
| `perp_orchestrate()` mock plan and reflect | Invoke with `force_mock=True` | Outputs a mock strategic plan; executes 2 default subtasks in phases; reflection passes on loop 1; persists mock memory updates to `context.md` |
| Phase ordering | Provide a plan with phases `[1, 2, 1]` | Subtasks are grouped by phase; phase 1 tasks run in parallel first, then phase 2 |
| `run_subtask_agent()` success path | Execute a read-file subtask with a valid model | `results_dict` receives `success=True`, description, and response content |
| Local semaphore throttling | Set `MAX_LOCAL_CONCURRENCY=1`; dispatch 3 local subtasks sequentially | Each local task acquires and releases the semaphore; no more than one local thread executes concurrently |

---

### 2. Failures That Trigger Rollback

The primary rollback mechanism lives in `run_api_chat()` (chat.py) and relies on **checkpointing** the `messages` list length before each user turn.

#### 2.1 API Transport Failure Rollback
| Test Case | Failure Injection | Expected Rollback |
|---|---|---|
| `KognisantAPIError` during initial LLM call | Mock `query_model_api_raw()` to raise `KognisantAPIError("Simulated timeout")` immediately after user sends a message | `messages` list is truncated to `checkpoint_idx`; user message and any partially appended system/tool messages are removed; session file is re-saved with the restored list |
| `HTTPError 429` exhausting retries | Mock `urlopen` to raise `HTTPError` with code `429` on all 3 attempts | After 3 retries with exponential backoff, `KognisantAPIError` propagates; checkpoint is restored |
| `HTTPError 502/503/504` exhausting retries | Same as above with codes `502`, `503`, `504` | Same rollback behavior |
| `URLError` / `TimeoutError` exhaustion | Mock `urlopen` to raise `URLError` on all retries | Network failure propagates; checkpoint restored |
| Malformed JSON response | Mock `urlopen` to return `b"not json"` | `json.JSONDecodeError` caught and re-raised as `KognisantAPIError`; checkpoint restored |
| Empty `resp_data` (no `choices`) | Mock `query_model_api_raw()` to return `{}` | Raises `KognisantAPIError`; checkpoint restored |
| Tool execution exception mid-turn | Mock `execute_tool()` to raise an unhandled `RuntimeError` inside a tool-call loop | Exception bubbles out of the loop; `finally` block stops spinner; outer `except` catches it and restores checkpoint |

#### 2.2 Tool-Level Failures That Do Not Rollback (by design)
These tool failures return error strings to the LLM rather than crashing the chat loop:
| Test Case | Failure Injection | Expected Behavior |
|---|---|---|
| File not found | `read_project_file` on a nonexistent path | Returns `"[Error] File 'X' not found."` appended to chat as tool result; conversation continues |
| Directory traversal attempt | Request `file_path="../etc/passwd"` | Returns `"[Error] Access denied..."`; state is NOT rolled back |
| Edit with missing `old_text` | `edit_project_file` where `old_text` is not in the file | Returns `"[Error] Could not find old_text..."`; file is NOT modified; conversation continues |
| Web browse timeout | `browse_web_page` with non-routable URL or mocked timeout | Returns `"[Error] Failed to fetch webpage: ..."` |
| Invalid tool arguments | Supply malformed JSON string to `execute_tool` | Returns `"[Error] Failed to parse tool arguments: ..."` |

---

### 3. Assertions Verifying Pre-Operation State Is Restored After Rollback

These assertions must be implemented explicitly in the test code after injecting a failure.

#### 3.1 Messages List Integrity
```python
def test_rollback_restores_exact_message_state(self):
    # Setup: populate messages with system prompt + 2 assistant/user pairs
    messages = [system_prompt, msg1, msg2]
    checkpoint_idx = len(messages)  # e.g., 3

    # Inject failure after user message is appended
    with mock.patch('network.query_model_api_raw', side_effect=KognisantAPIError("boom")):
        # Run the turn
        run_api_chat_turn(model_config, project_info, messages, user_input)

    # ASSERTIONS:
    assert len(messages) == checkpoint_idx
    assert messages[0] == system_prompt
    assert messages[1] == msg1
    assert messages[2] == msg2
    assert all(m["role"] != "user" or m != user_input_message for m in messages[checkpoint_idx:])
```

#### 3.2 Session File Reversion
```python
def test_rollback_rewrites_session_file(self):
    # Setup: save a known-good session snapshot
    original_snapshot = json.dumps(messages.copy())

    with mock.patch('network.query_model_api_raw', side_effect=KognisantAPIError("boom")):
        run_api_chat_turn(...)

    # ASSERTION: session file on disk matches pre-turn snapshot
    with open(session_filepath) as f:
        disk_content = f.read()
    assert json.loads(disk_content) == json.loads(original_snapshot)
```

#### 3.3 File System State After Tool Edit Failure
```python
def test_failed_edit_leaves_file_untouched(self):
    original_sha = hashlib.sha256(original_content.encode()).hexdigest()
    # execute_tool with missing old_text returns error but must not touch disk
    result = execute_tool("edit_project_file", json.dumps({
        "file_path": "foo.py",
        "edits": [{"old_text": "NONEXISTENT", "new_text": "REPLACEMENT"}]
    }), project_info)

    # ASSERTIONS:
    assert "[Error]" in result
    with open(real_path, "rb") as f:
        assert hashlib.sha256(f.read()).hexdigest() == original_sha
```

#### 3.4 Configuration State After Failed Model Switch
```python
def test_failed_model_key_prompt_aborts_cleanly(self):
    old_pool = load_models_pool()
    # Simulate user pressing Ctrl+C during the API key prompt
    with mock.patch('builtins.input', side_effect=KeyboardInterrupt):
        process_slash_commands("/model", ...)

    # ASSERTION: models_pool.json is unchanged; no orphaned keys written
    assert load_models_pool() == old_pool
```

---

### 4. Cascading Failure Scenarios

Cascading failures test what happens when one failure triggers or worsens another, evaluating the robustness of cleanup and recovery chains.

#### 4.1 Rollback + Session Save Failure
| Scenario | Trigger | Expected Containment |
|---|---|---|
| Checkpoint rollback succeeds, but `save_chat_session()` raises `OSError` during re-save | Mock `open(..., 'w')` to raise `PermissionError` after API failure | The in-memory `messages` list remains correctly truncated; the exception from `save_chat_session` is caught (or printed); the program does not crash or re-corrupt the message list |

#### 4.2 Tool Failure + Follow-up Tool Failure
| Scenario | Trigger | Expected Containment |
|---|---|---|
| LLM requests multiple tools; first tool succeeds, second fails with exception | Mock `execute_tool` to succeed on first call, then raise | First tool result is already appended. Exception propagates out of loop; checkpoint restores state. No partial tool results remain in the conversation because `messages` is reverted to `checkpoint_idx` |

#### 4.3 PERP Swarm Partial Phase Failure
| Scenario | Trigger | Expected Containment |
|---|---|---|
| One agent in a parallel phase crashes; others return success | Mock `run_subtask_agent` so thread-2 raises before writing to `results_dict` | `results_dict` contains entries for the successful threads; the crashed thread's entry contains `success=False` and an error message; the orchestrator proceeds to reflection; the swarm does not hang because `t.join()` waits for all threads |

#### 4.4 Planning → Execution Chain Break
| Scenario | Trigger | Expected Containment |
|---|---|---|
| Planner returns malformed JSON, so `json.loads()` fails | Mock `query_model_api` to return `"not json"` in planning phase | Spinner is stopped; error is printed; function returns early; no subtasks are executed; no file modifications occur |

#### 4.5 Persistence Phase Failure After Successful Execution
| Scenario | Trigger | Expected Containment |
|---|---|---|
| Reflection passes, but updating `context.md` raises `IOError` during persistence | Mock `open(..., 'w')` on context.md path to raise | The persistence exception is caught and printed as a warning; the swarm prints "PERP Swarm Process Finished Successfully"; the chat session continues running |

#### 4.6 Global Core Initialization Failures
| Scenario | Trigger | Expected Containment |
|---|---|---|
| `init_global_core()` cannot create `~/.kognisant_core` (read-only home dir) | Mock `os.makedirs` to raise `PermissionError` | Error is printed to stderr; downstream functions that depend on the directory (e.g., `register_project_globally`) fail gracefully without unhandled exceptions |

---

### 5. Resource Cleanup Validation

#### 5.1 Thread / Semaphore Cleanup
| Test Case | Validation Steps |
|---|---|
| Local semaphore release on subtask exception | Force `run_subtask_agent` to raise inside the `try` block after acquiring a local semaphore. Assert `local_semaphore._value == MAX_LOCAL_CONCURRENCY` after the thread terminates. |
| Spinner thread join on exception | Mock a failure inside a turn where a spinner is active. Assert `spinner.thread.is_alive()` is `False` after the turn completes (via the `finally: spinner.stop()` path). |
| Daemon thread cleanup | All subtask threads are created with `daemon=True`; after `t.join()` returns, assert the thread is no longer alive. |

#### 5.2 File Handle Cleanup
| Test Case | Validation Steps |
|---|---|
| `read_project_file` closes file handles | Use `mock.patch('builtins.open', mock.mock_open())` and assert `mock_file.close.called` after `execute_tool` returns. |
| `edit_project_file` closes both read and write handles | Assert `mock_file.close.call_count >= 2` (or monitor context manager exit count). |
| `save_chat_session` closes handle | Same mock strategy; assert the mock file handle is closed after the write. |
| `init_global_core` closes all created files | Each `open(..., 'w')` context manager exits cleanly; no leaked descriptors. |

#### 5.3 Network Resource Cleanup
| Test Case | Validation Steps |
|---|---|
| `urlopen` response context manager exited | Mock `urllib.request.urlopen` returning a context manager. Assert `__exit__` is called after each request, including retries. |
| SSL context creation per-request | `ssl._create_unverified_context()` is used; assert no global SSL state is mutated. |

#### 5.4 Subprocess Resource Cleanup
| Test Case | Validation Steps |
|---|---|
| `browse_web_page` subprocess timeout kills the process | Mock `subprocess.run` with `side_effect=subprocess.TimeoutExpired(...)`; assert the process mock does not leak a running handle. |
| Headless browser process termination | On successful headless run, assert `subprocess.run` is called with `capture_output=True`; stdout is consumed inside the function and returned. |

#### 5.5 Session File Cleanup
| Test Case | Validation Steps |
|---|---|
| Non-project chat sessions do not write files | Set `project_info=None`. Assert `save_chat_session` returns immediately without creating files on disk. |
| Chat exit without project leaves no orphans | Run `run_mock_chat` with `project_info=None`; assert no history files are created in the current working directory. |
| Project history directory contains only valid JSON | After running a session, assert every file in `.kognisant/history/` is valid JSON parseable by `json.load`. |

#### 5.6 Memory / State Cleanup After Model Switch
| Test Case | Validation Steps |
|---|---|
| In-place `active_model_config.clear().update()` is atomic | Mock a dict reference; after `/model` selection, assert the dict object ID is unchanged and contents match the new model. Old model metadata is fully evicted. |
| `messages_or_history.clear()` preserves system prompt | After `/clear`, assert `messages[0]["role"] == "system"` and `len(messages) == 1`. |

#### 5.7 Project Root / Sandbox Cleanup
| Test Case | Validation Steps |
|---|---|
| Directory traversal does not create files outside root | Attempt to read or edit with `file_path="../../tmp/evil.txt"`. Assert no files are created or read outside the real project root. |
| `os.path.realpath` is called before sandbox check | Mock `os.path.realpath` and assert it is invoked on both `project_info["root"]` and the target file path. |

---

### 6. Recommended Test File Layout

Because the codebase is organized into distinct modules, the test plan maps naturally to the following test modules (all using `unittest`):

```
tests/
├── __init__.py
├── test_main.py          # CLI argument parsing, subcommand dispatch
├── test_config.py        # Global core init, project discovery, file scanning, model pool
├── test_network.py       # API client, retry/backoff, Ollama detection, SSL contexts
├── test_tools.py         # Tool specifications, sandboxing, file edit atomicity, web/browse
├── test_chat.py          # Chat loops, slash commands, checkpoint rollback, session persistence
├── test_agents.py        # PERP orchestration, subtask threading, semaphore throttling, reflection loops
└── test_colors.py        # Spinner lifecycle, logo rendering, terminal width calculation
```

### 7. Mocking Strategy Summary

| Component | Primary Mock Target |
|---|---|
| API calls | `urllib.request.urlopen` or `network.query_model_api_raw` |
| User input | `builtins.input` |
| File system | `os.path.exists`, `os.makedirs`, `builtins.open` via `mock_open()` |
| Subprocess | `subprocess.run` |
| Web browser | `webbrowser.open`, `shutil.which` |
| Time / delays | `time.sleep` (to speed up exponential backoff and spinner tests) |
| Threading | `threading.Thread` (for deterministic agent swarm tests) |

---

## 🤝 Contributing

Contributions, issues, and feature requests are welcome!

1. Fork the repository.
2. Create a feature branch (`git checkout -b feature/amazing-feature`).
3. Commit your changes (`git commit -m 'Add amazing feature'`).
4. Push to the branch (`git push origin feature/amazing-feature`).
5. Open a Pull Request.

Please ensure your changes stay within the **Python 3.8+ standard library** to preserve the zero-dependency promise.

---

## 📄 License

This project is licensed under the **MIT License**. See `pyproject.toml` for the declared license.



