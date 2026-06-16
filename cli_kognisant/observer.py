"""Observer subsystem for trace collection and codebase introspection.

This module contains the TraceCollector class for instrumenting PERP swarm
executions and the StaticAnalyzer class for AST-based codebase introspection.
Later tasks will add ChangeDetector and TestOutcomeTracker to this same file.
"""

import ast
import fnmatch
import json
import logging
import os
import pathlib
import queue
import subprocess
import threading
from typing import Any

from .models import (
    Edge,
    FileOpTrace,
    LLMCallTrace,
    Node,
    ToolCallTrace,
    TraceRecord,
    generate_uuid,
    utc_now_iso,
)


# Maximum queue size before incremental drain to in-memory buffer
_MAX_QUEUE_SIZE = 1000

logger = logging.getLogger(__name__)


class TraceCollector:
    """Collects execution traces during PERP swarm sessions.

    Thread-safe: subtask threads push trace records to an internal queue.
    The orchestrator drains the queue at session end after all subtasks join.
    If the queue exceeds 1000 entries mid-session, it drains incrementally
    to an in-memory buffer.

    All disk I/O is wrapped in try/except to never interrupt PERP flow.
    Errors are logged to stderr (daemon log).
    """

    def __init__(self, project_root: str) -> None:
        """Initialize with project root for trace storage."""
        self._project_root = project_root
        self._traces_dir = os.path.join(project_root, ".kognisant", "traces")
        # Active sessions keyed by session_id
        self._sessions: dict[str, TraceRecord] = {}
        # Lock for thread-safe session dict access
        self._lock = threading.Lock()
        # Queue for cross-thread trace submission
        self._queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        # In-memory buffer for drained queue items (per session)
        self._buffers: dict[str, list[tuple[str, Any]]] = {}

    def start_session(self, task_description: str) -> str:
        """Begin a new trace session. Returns session_id (UUID v4).

        Creates a TraceRecord with ISO 8601 UTC timestamp and task description
        truncated to 500 characters.
        """
        session_id = generate_uuid()
        # Truncate description to 500 chars (R1.1)
        truncated_desc = task_description[:500]
        record = TraceRecord(
            session_id=session_id,
            start_time=utc_now_iso(),
            task_description=truncated_desc,
            status="running",
        )
        with self._lock:
            self._sessions[session_id] = record
            self._buffers[session_id] = []
        return session_id

    def record_tool_call(
        self,
        session_id: str,
        tool_name: str,
        arguments: str,
        result: str,
        success: bool,
        duration_ms: int,
    ) -> None:
        """Record a tool invocation within an active session.

        Arguments are truncated to 1000 chars, result to 200 chars (R1.2).
        """
        trace = ToolCallTrace(
            timestamp=utc_now_iso(),
            tool_name=tool_name,
            arguments=arguments[:1000],
            result_summary=result[:200],
            success=success,
            duration_ms=duration_ms,
        )
        self._submit_trace(session_id, ("tool_call", trace))

    def record_file_op(
        self,
        session_id: str,
        file_path: str,
        operation: str,
        byte_count: int,
    ) -> None:
        """Record a file read/write operation (R1.3)."""
        trace = FileOpTrace(
            timestamp=utc_now_iso(),
            file_path=file_path,
            operation=operation,
            byte_count=byte_count,
        )
        self._submit_trace(session_id, ("file_op", trace))

    def record_llm_call(
        self,
        session_id: str,
        model_id: str,
        prompt_tokens: int,
        completion_tokens: int,
        latency_ms: int,
    ) -> None:
        """Record an LLM API call (R1.4)."""
        trace = LLMCallTrace(
            timestamp=utc_now_iso(),
            model_id=model_id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
        )
        self._submit_trace(session_id, ("llm_call", trace))

    def end_session(self, session_id: str, status: str) -> None:
        """Finalize and persist the trace record to disk (R1.5).

        Drains the queue, sets end_time, and writes the trace JSON to
        .kognisant/traces/<session_id>.json.
        """
        # Drain remaining queue items for this session
        self._drain_queue()

        with self._lock:
            record = self._sessions.get(session_id)
            if record is None:
                return

            # Apply buffered traces to the record
            self._apply_buffer(session_id, record)

            # Finalize the record
            record.end_time = utc_now_iso()
            record.status = status

            # Remove from active sessions
            del self._sessions[session_id]
            del self._buffers[session_id]

        # Persist to disk (R1.5, R1.6, R1.7)
        self._write_trace(session_id, record)

    def _submit_trace(self, session_id: str, item: tuple[str, Any]) -> None:
        """Submit a trace item to the queue for thread-safe collection."""
        self._queue.put((session_id, item))

        # If queue exceeds 1000 entries, drain incrementally to buffer
        if self._queue.qsize() > _MAX_QUEUE_SIZE:
            self._drain_queue()

    def _drain_queue(self) -> None:
        """Drain all items from the queue into per-session in-memory buffers."""
        drained: list[tuple[str, tuple[str, Any]]] = []
        while True:
            try:
                item = self._queue.get_nowait()
                drained.append(item)
            except queue.Empty:
                break

        if drained:
            with self._lock:
                for sid, trace_item in drained:
                    if sid in self._buffers:
                        self._buffers[sid].append(trace_item)

    def _apply_buffer(self, session_id: str, record: TraceRecord) -> None:
        """Apply buffered trace items to the TraceRecord.

        Must be called while holding self._lock.
        """
        buffer = self._buffers.get(session_id, [])
        for trace_type, trace in buffer:
            if trace_type == "tool_call":
                record.tool_calls.append(trace)
            elif trace_type == "file_op":
                record.file_operations.append(trace)
            elif trace_type == "llm_call":
                record.llm_calls.append(trace)

    def _write_trace(self, session_id: str, record: TraceRecord) -> None:
        """Write trace record to disk as JSON.

        Creates the traces directory if it doesn't exist (R1.6).
        All disk I/O is wrapped in try/except — errors are logged to stderr,
        never raised (R1.7).
        """
        try:
            os.makedirs(self._traces_dir, exist_ok=True)
            file_path = os.path.join(self._traces_dir, f"{session_id}.json")
            tmp_path = f"{file_path}.tmp"
            data = json.dumps(record.to_dict(), indent=2)
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(data)
            os.rename(tmp_path, file_path)
        except OSError as e:
            logger.error(
                "Error writing trace %s: %s", session_id, e
            )
        except (TypeError, ValueError) as e:
            logger.error(
                "Error serializing trace %s: %s", session_id, e
            )


# Default gitignore patterns used when no .gitignore exists
_DEFAULT_GITIGNORE_PATTERNS = [
    ".git",
    "__pycache__",
    "*.pyc",
    "*.pyo",
    ".pytest_cache",
    ".venv",
    "venv",
    "*.egg-info",
    "build",
    "dist",
]

# Binary file extensions excluded from analysis (R20.2)
_BINARY_EXTENSIONS = frozenset([
    ".pyc", ".pyo", ".so", ".dll", ".exe", ".bin",
    ".png", ".jpg", ".gif", ".pdf", ".zip", ".tar", ".whl",
])


class StaticAnalyzer:
    """Performs AST-based static analysis of Python source files.

    Extracts function definitions, class definitions, imports, and call sites
    from Python files. Produces Node and Edge objects for the dependency graph.

    All edges produced by static analysis have confidence=1.0 and
    provenance="static" (R2.5).

    Scope boundaries (R20):
    - Only .py files within project root
    - Skips binary files
    - Respects .gitignore patterns
    - Resolves symlinks and skips those outside project root
    """

    def __init__(self, project_root: str, scope_config: dict) -> None:
        """Initialize with project root and scope boundary configuration.

        Args:
            project_root: Absolute path to the project root directory.
            scope_config: Dict with keys:
                - max_files: Maximum files to analyze (default 1000)
                - gitignore_patterns: List of patterns to exclude
        """
        self._project_root = os.path.realpath(project_root)
        self._max_files = scope_config.get("max_files", 1000)
        self._gitignore_patterns = self._load_gitignore_patterns(
            scope_config.get("gitignore_patterns")
        )
        # Track epistemic gaps for unresolvable call targets (R2.8)
        self._epistemic_gaps: list[dict] = []

    def _load_gitignore_patterns(self, config_patterns: list[str] | None) -> list[str]:
        """Load gitignore patterns from .gitignore file or use defaults.

        If config_patterns is provided, use those. Otherwise, attempt to read
        .gitignore from project root. Fall back to defaults if file is missing.
        """
        if config_patterns is not None:
            return config_patterns

        gitignore_path = os.path.join(self._project_root, ".gitignore")
        try:
            with open(gitignore_path, "r", encoding="utf-8") as f:
                patterns = []
                for line in f:
                    line = line.strip()
                    # Skip empty lines and comments
                    if line and not line.startswith("#"):
                        # Remove trailing slashes for directory patterns
                        patterns.append(line.rstrip("/"))
                return patterns if patterns else _DEFAULT_GITIGNORE_PATTERNS
        except OSError:
            return _DEFAULT_GITIGNORE_PATTERNS

    def _is_in_scope(self, file_path: str) -> bool:
        """Check if a file is within scope boundaries.

        Returns True if the file should be analyzed.
        Checks: .py extension, not binary, not gitignored,
        symlink resolves within project root (R20.1, R20.2, R20.3, R20.6).
        """
        # Must be a .py file (R20.1)
        if not file_path.endswith(".py"):
            return False

        # Check binary extensions (R20.2)
        _, ext = os.path.splitext(file_path)
        if ext.lower() in _BINARY_EXTENSIONS:
            return False

        # Resolve symlinks and check if still under project root (R20.6)
        real_path = os.path.realpath(file_path)
        if not real_path.startswith(self._project_root + os.sep) and real_path != self._project_root:
            return False

        # Check if file is a symlink pointing outside project root
        if os.path.islink(file_path):
            if not real_path.startswith(self._project_root + os.sep):
                return False

        # Check .gitignore patterns (R20.2)
        rel_path = os.path.relpath(file_path, self._project_root)
        for pattern in self._gitignore_patterns:
            # Match against each path component and the full relative path
            if fnmatch.fnmatch(rel_path, pattern):
                return False
            if fnmatch.fnmatch(rel_path, pattern + "/*"):
                return False
            # Check individual path parts
            parts = pathlib.Path(rel_path).parts
            for part in parts:
                if fnmatch.fnmatch(part, pattern):
                    return False

        return True

    def _file_path_to_module(self, file_path: str) -> str:
        """Convert a file path to a Python module name.

        e.g., "cli_kognisant/agents.py" -> "cli_kognisant.agents"
        """
        rel_path = os.path.relpath(file_path, self._project_root)
        # Remove .py extension
        module_path = rel_path[:-3] if rel_path.endswith(".py") else rel_path
        # Convert path separators to dots
        module_name = module_path.replace(os.sep, ".")
        # Handle __init__.py
        if module_name.endswith(".__init__"):
            module_name = module_name[:-9]  # Remove ".__init__"
        return module_name

    def analyze_file(self, file_path: str) -> tuple[list[Node], list[Edge]]:
        """Parse a single Python file, return extracted nodes and edges.

        Handles:
        - FunctionDef/AsyncFunctionDef (including nested) -> Node(node_type="function")
        - ClassDef -> Node(node_type="class")
        - Import/ImportFrom -> Edge(edge_type="imports") or external Node
        - ast.Call -> Edge(edge_type="calls")

        All edges get confidence=1.0 and provenance="static" (R2.5).

        If the file has syntax errors, logs the error and returns empty (R2.6).

        Args:
            file_path: Absolute path to the Python file.

        Returns:
            Tuple of (nodes, edges) extracted from the file.
        """
        nodes: list[Node] = []
        edges: list[Edge] = []

        # Read and parse the file
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                source = f.read()
        except (OSError, UnicodeDecodeError) as e:
            logger.error("Error reading file %s: %s", file_path, e)
            return nodes, edges

        try:
            tree = ast.parse(source, filename=file_path)
        except SyntaxError as e:
            # R2.6: Log and skip files with syntax errors
            logger.error("Syntax error in %s: %s", file_path, e)
            return nodes, edges

        module_name = self._file_path_to_module(file_path)
        rel_path = os.path.relpath(file_path, self._project_root)
        last_modified = utc_now_iso()

        # Extract function and class definitions
        self._extract_definitions(
            tree, module_name, rel_path, last_modified, nodes, prefix=""
        )

        # Extract import edges
        self._extract_imports(tree, module_name, nodes, edges)

        # Extract call site edges
        self._extract_calls(tree, module_name, edges)

        return nodes, edges

    def _extract_definitions(
        self,
        tree: ast.AST,
        module_name: str,
        file_path: str,
        last_modified: str,
        nodes: list[Node],
        prefix: str,
    ) -> None:
        """Recursively extract function and class definitions from AST.

        Args:
            tree: AST node to walk (module, class body, etc.)
            module_name: Dotted module name for node id construction
            file_path: Project-relative file path
            last_modified: ISO timestamp
            nodes: Accumulator list for extracted nodes
            prefix: Current scope prefix (e.g., "ClassName." for methods)
        """
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                func_id = f"{module_name}.{prefix}{node.name}"
                func_node = Node(
                    id=func_id,
                    node_type="function",
                    file_path=file_path,
                    line_start=node.lineno,
                    line_end=node.end_lineno or node.lineno,
                    last_modified=last_modified,
                    tags=[],
                    module=module_name,
                )
                nodes.append(func_node)
                # Recurse into nested functions (R2.1: including nested functions)
                self._extract_definitions(
                    node, module_name, file_path, last_modified, nodes,
                    prefix=f"{prefix}{node.name}."
                )

            elif isinstance(node, ast.ClassDef):
                class_id = f"{module_name}.{prefix}{node.name}"
                class_node = Node(
                    id=class_id,
                    node_type="class",
                    file_path=file_path,
                    line_start=node.lineno,
                    line_end=node.end_lineno or node.lineno,
                    last_modified=last_modified,
                    tags=[],
                    module=module_name,
                )
                nodes.append(class_node)
                # Recurse into class body for methods (R2.3)
                self._extract_definitions(
                    node, module_name, file_path, last_modified, nodes,
                    prefix=f"{prefix}{node.name}."
                )

    def _extract_imports(
        self,
        tree: ast.Module,
        module_name: str,
        nodes: list[Node],
        edges: list[Edge],
    ) -> None:
        """Extract import statements and create edges/external nodes.

        For resolvable imports: creates an "imports" edge.
        For unresolvable imports: creates an external package leaf node (R2.7).
        """
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    target_module = alias.name
                    self._create_import_edge_or_external(
                        module_name, target_module, nodes, edges
                    )

            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    target_module = node.module
                    self._create_import_edge_or_external(
                        module_name, target_module, nodes, edges
                    )

    def _create_import_edge_or_external(
        self,
        source_module: str,
        target_module: str,
        nodes: list[Node],
        edges: list[Edge],
    ) -> None:
        """Create an imports edge or an external package leaf node.

        If target_module looks like an external package (doesn't start with
        the project's top-level package name), create an external leaf node (R2.7).
        """
        # Determine if this is an internal or external import
        # Internal imports share a top-level package name with the project
        project_top = self._project_root.rstrip(os.sep).split(os.sep)[-1]
        # Also check if it's a relative path within the project
        is_internal = (
            target_module.startswith(project_top.replace("-", "_"))
            or target_module.startswith(".")
        )

        if not is_internal:
            # R2.7: Create external package leaf node
            package_name = target_module.split(".")[0]
            ext_node_id = f"external.{package_name}"
            # Only add if not already present (avoid duplicates)
            if not any(n.id == ext_node_id for n in nodes):
                ext_node = Node(
                    id=ext_node_id,
                    node_type="module",
                    file_path="<external>",
                    line_start=0,
                    line_end=0,
                    last_modified="",
                    tags=["external"],
                    module=package_name,
                )
                nodes.append(ext_node)
            target_id = ext_node_id
        else:
            target_id = target_module

        # Create the imports edge (R2.5)
        edge = Edge(
            id=generate_uuid(),
            source=source_module,
            target=target_id,
            edge_type="imports",
            confidence=1.0,
            provenance="static",
        )
        edges.append(edge)

    def _extract_calls(
        self,
        tree: ast.Module,
        module_name: str,
        edges: list[Edge],
    ) -> None:
        """Extract call sites and create 'calls' edges (R2.4).

        Resolves call targets by inspecting the ast.Call.func attribute.
        Handles simple names (Name) and attribute access (Attribute).
        Unresolvable targets are skipped and logged to epistemic gaps (R2.8).
        """
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                target_name = self._resolve_call_target(node.func)
                if target_name is None:
                    # R2.8: Skip unresolvable, log to epistemic gaps
                    self._epistemic_gaps.append({
                        "gap_type": "unresolvable_call",
                        "module": module_name,
                        "line": getattr(node, "lineno", 0),
                        "description": f"Unresolvable call target in {module_name} at line {getattr(node, 'lineno', '?')}",
                    })
                    continue

                # Determine source: use module name as source context
                # (We don't track which function contains the call for now;
                #  the module-level source is sufficient for graph edges)
                source_id = module_name
                # If target doesn't have a module prefix, assume same module
                if "." not in target_name:
                    target_id = f"{module_name}.{target_name}"
                else:
                    target_id = target_name

                edge = Edge(
                    id=generate_uuid(),
                    source=source_id,
                    target=target_id,
                    edge_type="calls",
                    confidence=1.0,
                    provenance="static",
                )
                edges.append(edge)

    def _resolve_call_target(self, func_node: ast.expr) -> str | None:
        """Attempt to resolve an ast.Call's func to a target name.

        Returns the resolved name string, or None if unresolvable.
        Handles:
        - ast.Name (simple function calls like foo())
        - ast.Attribute (method calls like obj.method())
        - Nested attributes (a.b.c())
        """
        if isinstance(func_node, ast.Name):
            return func_node.id
        elif isinstance(func_node, ast.Attribute):
            # Try to resolve the value chain
            parts = [func_node.attr]
            current = func_node.value
            while isinstance(current, ast.Attribute):
                parts.append(current.attr)
                current = current.value
            if isinstance(current, ast.Name):
                parts.append(current.id)
                parts.reverse()
                return ".".join(parts)
            # Unresolvable (e.g., computed value)
            return None
        else:
            # Dynamic dispatch, subscripts, etc. — unresolvable
            return None

    def compute_complexity(self, file_path: str, function_name: str) -> int:
        """Compute cyclomatic complexity for a specific function.

        Counts decision points: If, While, For, With, ExceptHandler,
        BoolOp (each and/or counts as one), Assert, and comprehension nodes.
        Base complexity is 1.

        Args:
            file_path: Absolute path to the Python file.
            function_name: Name of the function to measure.

        Returns:
            Cyclomatic complexity value (minimum 1).
        """
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                source = f.read()
            tree = ast.parse(source, filename=file_path)
        except (OSError, SyntaxError) as e:
            logger.error("Cannot compute complexity for %s in %s: %s",
                         function_name, file_path, e)
            return 1

        # Find the function node
        func_node = self._find_function(tree, function_name)
        if func_node is None:
            return 1

        # Count decision points
        complexity = 1  # Base complexity
        for node in ast.walk(func_node):
            if isinstance(node, (ast.If, ast.While, ast.For, ast.With, ast.Assert)):
                complexity += 1
            elif isinstance(node, ast.ExceptHandler):
                complexity += 1
            elif isinstance(node, ast.BoolOp):
                # Each 'and'/'or' adds one decision point per operator
                # BoolOp with N values has N-1 operators
                complexity += len(node.values) - 1
            elif isinstance(node, ast.comprehension):
                complexity += 1

        return complexity

    def _find_function(
        self, tree: ast.AST, function_name: str
    ) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
        """Find a function node by name in the AST (searches nested scopes)."""
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == function_name:
                    return node
        return None

    def analyze_project(self) -> tuple[list[Node], list[Edge]]:
        """Full project scan. Returns all nodes and edges found.

        Walks the project root, filters for in-scope .py files,
        calls analyze_file on each, and aggregates results.
        Respects the max_files limit from scope_config (R20.5).

        Returns:
            Tuple of (all_nodes, all_edges) across the project.
        """
        all_nodes: list[Node] = []
        all_edges: list[Edge] = []
        file_count = 0

        for dirpath, dirnames, filenames in os.walk(self._project_root):
            # Skip hidden directories and common excluded dirs
            dirnames[:] = [
                d for d in dirnames
                if not self._is_dir_excluded(os.path.join(dirpath, d))
            ]

            for filename in filenames:
                if file_count >= self._max_files:
                    logger.warning(
                        "File count limit reached (%d). Stopping analysis.",
                        self._max_files,
                    )
                    return all_nodes, all_edges

                file_path = os.path.join(dirpath, filename)

                if not self._is_in_scope(file_path):
                    continue

                nodes, edges = self.analyze_file(file_path)
                all_nodes.extend(nodes)
                all_edges.extend(edges)
                file_count += 1

        return all_nodes, all_edges

    def _is_dir_excluded(self, dir_path: str) -> bool:
        """Check if a directory should be excluded from traversal."""
        dir_name = os.path.basename(dir_path)
        rel_path = os.path.relpath(dir_path, self._project_root)

        for pattern in self._gitignore_patterns:
            if fnmatch.fnmatch(dir_name, pattern):
                return True
            if fnmatch.fnmatch(rel_path, pattern):
                return True

        return False


class ChangeDetector:
    """Detects code changes via git and invalidates stale World Model edges.

    Compares current git HEAD against the last recorded commit hash stored in
    change_log.json to identify modified, added, deleted, and renamed files.
    For modified files, determines which functions were affected by overlapping
    git diff hunks with AST-derived function line ranges.

    Requirements covered: R3.1, R3.2, R3.3, R3.4, R3.5, R3.6, R3.7, R3.8
    """

    def __init__(self, project_root: str, store: Any) -> None:
        """Initialize with project root and WorldModelStore reference.

        Args:
            project_root: Absolute path to the project root directory.
            store: A WorldModelStore instance for reading/writing change_log.json.
        """
        self._project_root = project_root
        self._store = store
        self._change_log_path = os.path.join(
            project_root, ".kognisant", "world_model", "change_log.json"
        )

    def detect_changes(self) -> dict:
        """Detect file-level and function-level changes since last recorded commit.

        Reads stored HEAD hash from change_log.json, compares to current HEAD,
        and parses git diff output to identify changes.

        Returns:
            dict with keys:
                'modified_functions': list[str]  - node ids of modified functions
                'added_files': list[str]         - relative paths of added files
                'deleted_files': list[str]       - relative paths of deleted files
                'renamed_files': list[tuple[str, str]]  - (old_path, new_path)

        If git is unavailable, detached HEAD, or no commits exist, logs
        warning and returns empty result (R3.8).
        """
        empty_result: dict = {
            "modified_functions": [],
            "added_files": [],
            "deleted_files": [],
            "renamed_files": [],
        }

        # R3.8: Check git availability and get current HEAD
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                cwd=self._project_root,
                timeout=10,
            )
        except (FileNotFoundError, OSError) as e:
            logger.warning("Git not available: %s. Skipping change detection.", e)
            return empty_result

        if result.returncode != 0:
            logger.warning(
                "Git rev-parse HEAD failed (detached HEAD or no commits): %s. "
                "Skipping change detection.",
                result.stderr.strip(),
            )
            return empty_result

        current_head = result.stdout.strip()

        # Read stored HEAD from change_log.json
        stored_head = self._read_stored_head()

        if stored_head is None:
            # R3.7: First run or corrupted file — trigger full analysis, create log
            self._handle_first_run(current_head)
            return empty_result

        # If HEAD hasn't changed, nothing to do
        if stored_head == current_head:
            return empty_result

        # R3.1: Run git diff --name-status to get file-level changes
        try:
            diff_result = subprocess.run(
                ["git", "diff", "--name-status", stored_head, current_head],
                capture_output=True,
                text=True,
                cwd=self._project_root,
                timeout=30,
            )
        except (FileNotFoundError, OSError) as e:
            logger.warning("Git diff failed: %s. Skipping change detection.", e)
            return empty_result

        if diff_result.returncode != 0:
            logger.warning(
                "Git diff returned non-zero: %s. Skipping change detection.",
                diff_result.stderr.strip(),
            )
            return empty_result

        # Parse diff output into categories
        modified_files: list[str] = []
        added_files: list[str] = []
        deleted_files: list[str] = []
        renamed_files: list[tuple[str, str]] = []

        for line in diff_result.stdout.strip().splitlines():
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                continue

            status = parts[0]
            if status == "M":
                modified_files.append(parts[1])
            elif status == "A":
                added_files.append(parts[1])
            elif status == "D":
                deleted_files.append(parts[1])
            elif status.startswith("R"):
                # Rename: Rxx\told_path\tnew_path
                if len(parts) >= 3:
                    renamed_files.append((parts[1], parts[2]))

        # R3.2: For modified files, identify which functions changed
        modified_functions: list[str] = []
        for file_path in modified_files:
            if not file_path.endswith(".py"):
                continue
            func_ids = self._identify_modified_functions(
                file_path, stored_head, current_head
            )
            modified_functions.extend(func_ids)

        # R3.6: Update stored commit hash
        self._write_change_log(current_head)

        return {
            "modified_functions": modified_functions,
            "added_files": added_files,
            "deleted_files": deleted_files,
            "renamed_files": renamed_files,
        }

    def apply_invalidations(self, changes: dict, graph: Any) -> None:
        """Apply World Model invalidations based on detected changes.

        Args:
            changes: dict returned by detect_changes()
            graph: DependencyGraph instance to modify

        Actions:
            - Modified functions: reduce outgoing edge confidence by 50% (R3.2)
            - Deleted files: remove all nodes from that file (R3.3)
            - Added files: run static analysis and add nodes/edges (R3.4)
            - Renamed files: update file_path on nodes from old path (R3.5)
        """
        # R3.2: Reduce confidence on outgoing edges from modified functions
        for func_id in changes.get("modified_functions", []):
            edges = graph.get_edges_from(func_id)
            for edge in edges:
                edge.confidence = max(0.0, edge.confidence * 0.5)

        # R3.3: Remove nodes for deleted files
        for file_path in changes.get("deleted_files", []):
            self._remove_nodes_for_file(file_path, graph)

        # R3.4: Trigger static analysis for added files
        for file_path in changes.get("added_files", []):
            if not file_path.endswith(".py"):
                continue
            abs_path = os.path.join(self._project_root, file_path)
            if not os.path.isfile(abs_path):
                continue
            analyzer = StaticAnalyzer(
                self._project_root, {"max_files": 1, "gitignore_patterns": []}
            )
            nodes, edges = analyzer.analyze_file(abs_path)
            for node in nodes:
                graph.add_node(node)
            for edge in edges:
                graph.add_edge(edge)

        # R3.5: Update file paths for renamed files
        for old_path, new_path in changes.get("renamed_files", []):
            self._update_file_paths(old_path, new_path, graph)

    def _read_stored_head(self) -> str | None:
        """Read the stored HEAD hash from change_log.json.

        Returns None if file is missing or corrupted (R3.7).
        """
        try:
            with open(self._change_log_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "head_hash" in data:
                return data["head_hash"]
            return None
        except (OSError, json.JSONDecodeError, ValueError):
            return None

    def _write_change_log(self, head_hash: str) -> None:
        """Write the current HEAD hash to change_log.json atomically."""
        data = {"head_hash": head_hash, "updated_at": utc_now_iso()}
        dir_path = os.path.dirname(self._change_log_path)
        os.makedirs(dir_path, exist_ok=True)
        tmp_path = self._change_log_path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.rename(tmp_path, self._change_log_path)
        except OSError as e:
            logger.error("Failed to write change_log.json: %s", e)

    def _handle_first_run(self, current_head: str) -> None:
        """Handle first run: create change_log.json with current HEAD (R3.7).

        Full static analysis is triggered by the caller (orchestrator),
        not directly here, since detect_changes returns empty result on
        first run and the orchestrator handles bootstrap.
        """
        self._write_change_log(current_head)

    def _identify_modified_functions(
        self, file_path: str, old_hash: str, new_hash: str
    ) -> list[str]:
        """Identify which functions were modified in a file.

        Uses git diff to find changed line numbers, then compares against
        AST-derived function line ranges in the current file to determine
        which functions overlap with the changes.

        Args:
            file_path: Project-relative path to the modified file.
            old_hash: Previous commit hash.
            new_hash: Current commit hash.

        Returns:
            List of node ids for functions whose line ranges overlap
            with changed lines.
        """
        # Get changed line numbers using unified diff
        try:
            diff_result = subprocess.run(
                ["git", "diff", "-U0", old_hash, new_hash, "--", file_path],
                capture_output=True,
                text=True,
                cwd=self._project_root,
                timeout=10,
            )
        except (FileNotFoundError, OSError) as e:
            logger.warning("Git diff for %s failed: %s", file_path, e)
            return []

        if diff_result.returncode != 0:
            return []

        # Parse @@ hunk headers to extract changed line numbers in new file
        changed_lines: set[int] = set()
        for line in diff_result.stdout.splitlines():
            if line.startswith("@@"):
                # Format: @@ -old_start,old_count +new_start,new_count @@
                try:
                    plus_part = line.split("+")[1].split("@@")[0].strip()
                    if "," in plus_part:
                        start = int(plus_part.split(",")[0])
                        count = int(plus_part.split(",")[1])
                    else:
                        start = int(plus_part)
                        count = 1
                    for i in range(start, start + count):
                        changed_lines.add(i)
                except (IndexError, ValueError):
                    continue

        if not changed_lines:
            return []

        # Parse the current file with AST to get function line ranges
        abs_path = os.path.join(self._project_root, file_path)
        try:
            with open(abs_path, "r", encoding="utf-8") as f:
                source = f.read()
            tree = ast.parse(source, filename=abs_path)
        except (OSError, SyntaxError) as e:
            logger.warning("Cannot parse %s for function detection: %s", file_path, e)
            return []

        # Build module name from file path
        analyzer = StaticAnalyzer(
            self._project_root, {"max_files": 1, "gitignore_patterns": []}
        )
        module_name = analyzer._file_path_to_module(abs_path)

        # Find functions whose line ranges overlap with changed lines
        modified_func_ids: list[str] = []
        self._find_modified_functions(
            tree, module_name, changed_lines, modified_func_ids, prefix=""
        )

        return modified_func_ids

    def _find_modified_functions(
        self,
        tree: ast.AST,
        module_name: str,
        changed_lines: set[int],
        result: list[str],
        prefix: str,
    ) -> None:
        """Recursively find functions whose line ranges overlap changed lines.

        Args:
            tree: AST node to walk.
            module_name: Dotted module name for node id construction.
            changed_lines: Set of line numbers that were modified.
            result: Accumulator list for modified function node ids.
            prefix: Current scope prefix (e.g., "ClassName." for methods).
        """
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                func_id = f"{module_name}.{prefix}{node.name}"
                line_start = node.lineno
                line_end = node.end_lineno or node.lineno
                func_lines = set(range(line_start, line_end + 1))
                if func_lines & changed_lines:
                    result.append(func_id)
                # Recurse into nested functions
                self._find_modified_functions(
                    node, module_name, changed_lines, result,
                    prefix=f"{prefix}{node.name}."
                )
            elif isinstance(node, ast.ClassDef):
                # Recurse into class body for methods
                self._find_modified_functions(
                    node, module_name, changed_lines, result,
                    prefix=f"{prefix}{node.name}."
                )

    def _remove_nodes_for_file(self, file_path: str, graph: Any) -> None:
        """Remove all nodes originating from a given file path from the graph.

        Args:
            file_path: Project-relative file path.
            graph: DependencyGraph instance.
        """
        # Collect node ids to remove (avoid mutation during iteration)
        nodes_to_remove: list[str] = []
        for node in list(graph._nodes.values()):
            if node.file_path == file_path:
                nodes_to_remove.append(node.id)

        for node_id in nodes_to_remove:
            graph.remove_node(node_id)

    def _update_file_paths(
        self, old_path: str, new_path: str, graph: Any
    ) -> None:
        """Update file_path attribute on all nodes from old_path to new_path.

        Preserves existing edges and confidence scores (R3.5).

        Args:
            old_path: Old project-relative file path.
            new_path: New project-relative file path.
            graph: DependencyGraph instance.
        """
        for node in graph._nodes.values():
            if node.file_path == old_path:
                node.file_path = new_path


# ───────────────────────────────────────────────────────────
# TestOutcomeTracker
# ───────────────────────────────────────────────────────────

# Maximum rolling history entries
_ROLLING_HISTORY_MAX = 20

# Consecutive failure threshold for instability detection
_INSTABILITY_THRESHOLD = 3

# Consecutive pass threshold for recovery detection
_RECOVERY_THRESHOLD = 3


class TestOutcomeTracker:
    """Tracks test outcomes, coverage mapping, and test health over time.

    Records pytest results in a rolling history (last 20 entries), maps
    test functions to source functions via coverage data, and detects
    instability (3 consecutive failures) and recovery (3 consecutive passes)
    patterns to adjust graph confidence.

    Requirements covered: R4.1, R4.2, R4.3, R4.4, R4.5, R4.6
    """

    def __init__(self, project_root: str, store: "WorldModelStore") -> None:
        """Initialize with project root and store reference.

        Args:
            project_root: Absolute path to the project root directory.
            store: WorldModelStore instance for persistence operations.
        """
        self._project_root = project_root
        self._store = store
        self._health_path = os.path.join(
            project_root, ".kognisant", "world_model", "test_health.json"
        )

    # ─── Public API ───────────────────────────────────────────

    def record_test_run(self, results: dict) -> None:
        """Record a pytest run result. Updates test_health.json (R4.1, R4.2, R4.3, R4.5).

        Args:
            results: Dict with keys:
                - total: int
                - passed: int
                - failed: int
                - skipped: int
                - duration_ms: int
                - failed_tests: list[str] - names of failed test functions
                - passed_tests: list[str] - names of passed test functions
                - coverage: dict | None - {source_func: [test_func, ...]} or None
        """
        health_data = self._load_health()

        # Build the entry to record
        entry = {
            "total": results.get("total", 0),
            "passed": results.get("passed", 0),
            "failed": results.get("failed", 0),
            "skipped": results.get("skipped", 0),
            "duration_ms": results.get("duration_ms", 0),
            "failed_tests": results.get("failed_tests", []),
            "passed_tests": results.get("passed_tests", []),
            "timestamp": utc_now_iso(),
        }

        # Append to rolling history, evict oldest if at capacity (R4.3)
        history = health_data.get("history", [])
        if len(history) >= _ROLLING_HISTORY_MAX:
            history.pop(0)
        history.append(entry)
        health_data["history"] = history

        # Update coverage mapping if available (R4.2)
        coverage = results.get("coverage")
        if coverage is not None:
            health_data["coverage_mapping"] = coverage
        elif "coverage_mapping" not in health_data:
            # R4.5: Log that coverage mapping is unavailable
            logger.info(
                "Coverage data unavailable. Recording pass/fail counts only."
            )

        self._save_health(health_data)

    def check_instability(self, graph: "DependencyGraph") -> list[str]:
        """Check for newly unstable nodes based on consecutive test failures (R4.4).

        A test is considered unstable when it has failed in the last 3
        consecutive entries in rolling history. Connected source nodes
        (from coverage mapping) are tagged with "unstable" and their
        edge confidence is reduced by 40% (multiplied by 0.6).

        Args:
            graph: DependencyGraph instance to modify.

        Returns:
            List of node ids that were newly flagged as unstable.
        """
        health_data = self._load_health()
        history = health_data.get("history", [])
        coverage_mapping = health_data.get("coverage_mapping")

        if not coverage_mapping:
            # Can't determine connected nodes without coverage mapping
            return []

        if len(history) < _INSTABILITY_THRESHOLD:
            return []

        # Find tests that failed in the last 3 consecutive entries
        unstable_tests = self._find_consecutive_failures(
            history, _INSTABILITY_THRESHOLD
        )

        if not unstable_tests:
            return []

        # Build reverse mapping: test_func -> [source_func_node_ids]
        reverse_coverage = self._build_reverse_coverage(coverage_mapping)

        flagged_nodes: list[str] = []

        for test_name in unstable_tests:
            source_node_ids = reverse_coverage.get(test_name, [])
            for node_id in source_node_ids:
                node = graph.get_node(node_id)
                if node is None:
                    continue

                # Tag node as unstable if not already tagged
                if "unstable" not in node.tags:
                    node.tags.append("unstable")
                    flagged_nodes.append(node_id)

                # Reduce confidence of all edges connected to this node by 40%
                self._reduce_edge_confidence(graph, node_id, factor=0.6)

        return flagged_nodes

    def check_recovery(self, graph: "DependencyGraph") -> list[str]:
        """Check for recovered nodes based on consecutive passes (R4.6).

        When a test that was previously unstable passes for 3 consecutive
        runs, removes the "unstable" tag from connected source nodes and
        reinforces edge confidence by 10% of remaining distance to 1.0.

        Args:
            graph: DependencyGraph instance to modify.

        Returns:
            List of node ids that had their unstable tag removed.
        """
        health_data = self._load_health()
        history = health_data.get("history", [])
        coverage_mapping = health_data.get("coverage_mapping")

        if not coverage_mapping:
            return []

        if len(history) < _RECOVERY_THRESHOLD:
            return []

        # Find tests that passed in the last 3 consecutive entries
        recovered_tests = self._find_consecutive_passes(
            history, _RECOVERY_THRESHOLD
        )

        if not recovered_tests:
            return []

        # Build reverse mapping: test_func -> [source_func_node_ids]
        reverse_coverage = self._build_reverse_coverage(coverage_mapping)

        recovered_nodes: list[str] = []

        for test_name in recovered_tests:
            source_node_ids = reverse_coverage.get(test_name, [])
            for node_id in source_node_ids:
                node = graph.get_node(node_id)
                if node is None:
                    continue

                # Only recover if currently tagged as unstable
                if "unstable" in node.tags:
                    node.tags.remove("unstable")
                    recovered_nodes.append(node_id)

                    # Reinforce edge confidence by 10% of remaining distance to 1.0
                    self._reinforce_edge_confidence(graph, node_id)

        return recovered_nodes

    # ─── Private Helpers ──────────────────────────────────────

    def _load_health(self) -> dict:
        """Load test_health.json, returning empty dict if missing or corrupted."""
        try:
            with open(self._health_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
                return {}
        except (OSError, json.JSONDecodeError, ValueError):
            return {}

    def _save_health(self, data: dict) -> None:
        """Atomically write test_health.json using tmp + os.rename."""
        dir_path = os.path.dirname(self._health_path)
        os.makedirs(dir_path, exist_ok=True)
        tmp_path = self._health_path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.rename(tmp_path, self._health_path)
        except OSError as e:
            logger.error("Error writing test_health.json: %s", e)
            # Clean up tmp file if rename failed
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    def _find_consecutive_failures(
        self, history: list[dict], threshold: int
    ) -> list[str]:
        """Find test functions that failed in the last `threshold` consecutive entries.

        Returns list of test function names that appear in failed_tests
        for each of the last `threshold` history entries.
        """
        if len(history) < threshold:
            return []

        # Get the last `threshold` entries
        recent = history[-threshold:]

        # A test is unstable if it appears in failed_tests of ALL recent entries
        # Start with the failed tests from the first recent entry
        if not recent[0].get("failed_tests"):
            return []

        candidates = set(recent[0]["failed_tests"])
        for entry in recent[1:]:
            entry_failed = set(entry.get("failed_tests", []))
            candidates = candidates.intersection(entry_failed)
            if not candidates:
                return []

        return list(candidates)

    def _find_consecutive_passes(
        self, history: list[dict], threshold: int
    ) -> list[str]:
        """Find test functions that passed in the last `threshold` consecutive entries.

        Only considers tests that were previously failing (appeared in
        failed_tests in earlier entries), indicating recovery.
        """
        if len(history) < threshold:
            return []

        # Get the last `threshold` entries
        recent = history[-threshold:]

        # A test has recovered if it appears in passed_tests of ALL recent entries
        if not recent[0].get("passed_tests"):
            return []

        candidates = set(recent[0]["passed_tests"])
        for entry in recent[1:]:
            entry_passed = set(entry.get("passed_tests", []))
            candidates = candidates.intersection(entry_passed)
            if not candidates:
                return []

        # Filter to only tests that previously failed (appear in failed_tests
        # of any earlier history entry before the recent window)
        earlier = history[:-threshold]
        previously_failed: set[str] = set()
        for entry in earlier:
            previously_failed.update(entry.get("failed_tests", []))

        return [t for t in candidates if t in previously_failed]

    def _build_reverse_coverage(
        self, coverage_mapping: dict
    ) -> dict[str, list[str]]:
        """Build reverse mapping: test_function_name -> [source_node_ids].

        Input coverage_mapping: {source_func_node_id: [test_func_name, ...]}
        Output: {test_func_name: [source_func_node_id, ...]}
        """
        reverse: dict[str, list[str]] = {}
        for source_node_id, test_funcs in coverage_mapping.items():
            for test_name in test_funcs:
                if test_name not in reverse:
                    reverse[test_name] = []
                reverse[test_name].append(source_node_id)
        return reverse

    def _reduce_edge_confidence(
        self, graph: "DependencyGraph", node_id: str, factor: float
    ) -> None:
        """Reduce confidence of all edges connected to a node by the given factor.

        Applies to both incoming and outgoing edges.
        """
        from .models import clamp_confidence

        # Outgoing edges
        for edge in graph.get_edges_from(node_id):
            edge.confidence = clamp_confidence(edge.confidence * factor)

        # Incoming edges
        for edge in graph.get_edges_to(node_id):
            edge.confidence = clamp_confidence(edge.confidence * factor)

    def _reinforce_edge_confidence(
        self, graph: "DependencyGraph", node_id: str
    ) -> None:
        """Reinforce confidence of all edges connected to a node.

        Increases by 10% of remaining distance to 1.0.
        """
        from .models import clamp_confidence

        # Outgoing edges
        for edge in graph.get_edges_from(node_id):
            remaining = 1.0 - edge.confidence
            edge.confidence = clamp_confidence(
                edge.confidence + 0.10 * remaining
            )

        # Incoming edges
        for edge in graph.get_edges_to(node_id):
            remaining = 1.0 - edge.confidence
            edge.confidence = clamp_confidence(
                edge.confidence + 0.10 * remaining
            )
