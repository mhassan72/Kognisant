# Tools and Skills

Kognisant ships with built-in tools for common operations, but its real power is that it can create new tools on the fly. Combined with the skills system, this means the AI continuously expands its capabilities as you use it.

---

## Why This Matters

Traditional AI assistants have a fixed set of capabilities. If they cannot do something, you are stuck. Kognisant solves this by letting the AI build its own tools when it encounters a task beyond its current toolkit. Once created, those tools are available in every future session across all projects.

---

## Built-in Tools

These tools ship with every Kognisant installation and are always available:

### File Operations

| Tool | What it does |
|:---|:---|
| `create_project_file` | Create a new file in your project |
| `edit_project_file` | Edit an existing project file (with atomic backup) |
| `read_project_file` | Read file contents into context |
| `delete_project_path` | Delete a file or directory |
| `create_project_directory` | Create a new directory |
| `list_project_files` | List files in the workspace |

### Web Tools

| Tool | What it does |
|:---|:---|
| `search_web` | Background DuckDuckGo search (no browser needed) |
| `browse_web_page` | Fetch and clean a URL's content (headless Chrome/Brave if available) |
| `open_in_native_browser` | Open a URL in your desktop browser visually |
| `capture_active_browser_console` | Read Chrome/Brave developer console logs |

### Shell

| Tool | What it does |
|:---|:---|
| `shell_execution` | Run a terminal command with timeout and safety checks |

### Job Management

| Tool | What it does |
|:---|:---|
| `schedule_job` | Create a daemon job (scheduled, persistent, or agent) |
| `create_script` | Write a Python script to `~/.kognisant_core/scripts/` |

### Global File Tools (sandboxed)

| Tool | What it does |
|:---|:---|
| `read_global_file` | Read from `~/.kognisant_core/` only |
| `create_global_file` | Write to `~/.kognisant_core/` only |
| `edit_global_file` | Edit within `~/.kognisant_core/` only |

These global file tools are hard-sandboxed. They refuse to access anything outside `~/.kognisant_core/`.

---

## How AI Creates New Tools Dynamically

When Kognisant encounters a task that no existing tool can handle, it can build a new one. The process:

1. **Detection** - The AI recognizes it needs a capability it does not have
2. **Schema design** - It creates a JSON schema defining the tool's interface
3. **Implementation** - It writes a Python script implementing the logic
4. **Registration** - Both files are saved to `~/.kognisant_core/tools/`
5. **Availability** - The tool is immediately usable in the current and all future sessions

Example conversation:

```
You > can you optimize this PNG file to reduce its size?

Kognisant > I don't have an image optimization tool yet. Let me create one.
  ┌─ Created ~/.kognisant_core/tools/optimize_png.json ─────────────────┐
  │ ✓ 1ms | created (0.4KB)                                             │
  └──────────────────────────────────────────────────────────────────────┘
  ┌─ Created ~/.kognisant_core/tools/optimize_png.py ───────────────────┐
  │ ✓ 1ms | created (1.2KB)                                             │
  └──────────────────────────────────────────────────────────────────────┘

Done. I've built an optimize_png tool that uses pngquant. Now optimizing your file...
```

Next session, next project, the tool is still there.

---

## Tool Schema Format

Every tool consists of two files in `~/.kognisant_core/tools/`:

### 1. JSON Schema (`<tool_name>.json`)

Defines the tool's name, description, and parameters using the OpenAI function calling format:

```json
{
  "type": "function",
  "function": {
    "name": "my_tool",
    "description": "Clear description of what this tool does and when to use it.",
    "parameters": {
      "type": "object",
      "properties": {
        "input_file": {
          "type": "string",
          "description": "Path to the input file to process."
        },
        "quality": {
          "type": "integer",
          "description": "Output quality (1-100). Default: 80."
        }
      },
      "required": ["input_file"]
    }
  }
}
```

### 2. Python Implementation (`<tool_name>.py`)

The actual logic. Must follow this contract:

1. **Arguments** - Received as a single JSON string in `sys.argv[1]`
2. **No interactivity** - Must never prompt for input or wait for user action
3. **Output** - Print results to stdout
4. **Errors** - Print error messages prefixed with `[Error]` and exit with code 1

```python
import sys
import json

def main():
    try:
        args = json.loads(sys.argv[1])
        input_file = args.get("input_file")
        quality = args.get("quality", 80)
    except Exception as e:
        print(f"[Error] Failed to parse arguments: {e}")
        sys.exit(1)

    # Your logic here
    result = process_file(input_file, quality)
    print(f"Processed: {result}")

if __name__ == "__main__":
    main()
```

The tool runs as an isolated subprocess. It has no access to Kognisant's internal state, only to the arguments passed and the filesystem.

---

## Skills System

Skills are markdown files that teach Kognisant how to approach tasks. They are loaded into the system prompt at the start of every session, shaping the AI's behavior without requiring tool calls.

### Where skills live

```
~/.kognisant_core/skills/
```

### Default skills

| Skill | Purpose |
|:---|:---|
| `web_browser_steering.md` | When to search silently vs. fetch a page vs. open a browser |
| `coding_standards.md` | Clean code patterns, modular design, error handling |
| `global_tool_development.md` | Strict rules for building new tools (contract format, safety) |

### How skills work

Every `.md` file in the skills directory is concatenated into the system prompt. The AI reads these instructions before processing your message. This means skills influence behavior without any runtime overhead.

Skills are best for:
- Coding conventions and standards
- Behavioral preferences ("always use pytest", "prefer functional patterns")
- Domain knowledge ("our API uses pagination with cursor tokens")
- Tool usage rules ("only open native browser when user explicitly asks")

### Viewing loaded skills

```
/skills
```

Shows all active skills with their filenames.

---

## Creating Custom Skills

Create any `.md` file in the skills directory:

```bash
cat > ~/.kognisant_core/skills/my_team.md << 'EOF'
# Team Engineering Standards

## Code Style
- 4-space indentation for Python
- Type hints on all public functions
- Docstrings follow Google style

## Architecture
- Repository pattern for data access
- All business logic in service layer, never in routes
- Dependency injection via constructor parameters

## Testing
- Every PR needs tests
- Integration tests use a separate test database
- Mock only external services, never internal modules
EOF
```

This takes effect on your next `kognisant chat` session. You will see the skill count increment in the bootstrap line:

```
⚡ gemma4:latest | valence: +15 | 4 skills, 4 tools
```

---

## Script Factory

The script factory is the mechanism by which the AI creates daemon-schedulable scripts. When you ask Kognisant to build something that should run in the background, it uses the `create_script` tool to:

1. Write the Python script to `~/.kognisant_core/scripts/`
2. Add metadata (description, author, creation date) in comments
3. Optionally register it as a job with the daemon

Example:

```
You > Build a script that checks if my API is healthy every 5 minutes and logs the result

Kognisant >
  ┌─ Created ~/.kognisant_core/scripts/api-health-monitor.py ───────────┐
  │ ✓ 1ms | created (0.9KB)                                             │
  └──────────────────────────────────────────────────────────────────────┘

Done. Created api-health-monitor.py. Would you like me to schedule it?

You > yes, every 5 minutes

Kognisant >
  → Job 'api-health-monitor' created as scheduled (cron: */5 * * * *)
```

Scripts in `~/.kognisant_core/scripts/` are the only location the daemon will execute from. This is a security boundary: no arbitrary paths can be scheduled.

---

## Managing Tools

### List all tools

From chat:
```
/tool list
```

### Register a workspace script as a global tool

If you wrote a useful script in your current project:

```
/tool register my_tool scripts/my_tool.py
```

This copies the script to `~/.kognisant_core/tools/my_tool.py` and scaffolds a JSON schema template for you to customize.

### Delete a tool

```
/tool delete my_tool
```

Removes both the `.json` and `.py` files from the tools directory.

---

## Safety Boundaries

### Path sandboxing

All file operation tools are sandboxed. They can only access:

1. Your active project workspace directory
2. `~/.kognisant_core/tools/` (for tool files)
3. `~/.kognisant_core/skills/` (for skill files)
4. `~/.kognisant_core/scripts/` (for daemon scripts)

Any attempt to access system directories (`/etc/`, `~/.ssh/`, etc.) or use directory traversal (`../../../`) is immediately blocked:

```
[Error] Access denied: path is outside allowed directories
```

### Atomic file operations

All file edits use an atomic write pattern:
1. Create a `.bak` backup of the original
2. Write changes to a temp file
3. Sync to disk (`fsync`)
4. Rename temp over target (`os.replace`)

If anything fails, the original file is preserved unchanged.

### Tool execution isolation

Dynamic tools run as subprocesses. They:
- Have their own process space
- Cannot access Kognisant's internal memory
- Are subject to execution timeouts
- Can only read arguments passed via `sys.argv[1]`
