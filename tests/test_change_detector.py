"""Unit tests for the ChangeDetector class in observer.py."""

import json
import os
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from cli_kognisant.models import Edge, Node
from cli_kognisant.observer import ChangeDetector
from cli_kognisant.world_model import DependencyGraph


# ───────────────────────────────────────────────────────────
# Fixtures
# ───────────────────────────────────────────────────────────


@pytest.fixture
def tmp_project(tmp_path):
    """Create a temporary project directory with world_model dir."""
    wm_dir = tmp_path / ".kognisant" / "world_model"
    wm_dir.mkdir(parents=True)
    return tmp_path


@pytest.fixture
def mock_store():
    """Create a mock WorldModelStore."""
    return MagicMock()


@pytest.fixture
def detector(tmp_project, mock_store):
    """Create a ChangeDetector instance."""
    return ChangeDetector(str(tmp_project), mock_store)


@pytest.fixture
def graph():
    """Create a DependencyGraph with some test nodes and edges."""
    g = DependencyGraph()
    # Add nodes for a file "src/utils.py"
    node_a = Node(
        id="utils.helper_func",
        node_type="function",
        file_path="src/utils.py",
        line_start=1,
        line_end=10,
        last_modified="2024-01-01T00:00:00+00:00",
        tags=[],
        module="utils",
    )
    node_b = Node(
        id="utils.other_func",
        node_type="function",
        file_path="src/utils.py",
        line_start=12,
        line_end=20,
        last_modified="2024-01-01T00:00:00+00:00",
        tags=[],
        module="utils",
    )
    node_c = Node(
        id="main.run",
        node_type="function",
        file_path="src/main.py",
        line_start=1,
        line_end=5,
        last_modified="2024-01-01T00:00:00+00:00",
        tags=[],
        module="main",
    )
    g.add_node(node_a)
    g.add_node(node_b)
    g.add_node(node_c)

    # Add edges: helper_func -> other_func, helper_func -> main.run
    edge1 = Edge(
        id="e1",
        source="utils.helper_func",
        target="utils.other_func",
        edge_type="calls",
        confidence=0.9,
        provenance="static",
    )
    edge2 = Edge(
        id="e2",
        source="utils.helper_func",
        target="main.run",
        edge_type="calls",
        confidence=0.8,
        provenance="dynamic",
    )
    g.add_edge(edge1)
    g.add_edge(edge2)
    return g


# ───────────────────────────────────────────────────────────
# Tests: detect_changes()
# ───────────────────────────────────────────────────────────


class TestDetectChanges:
    """Tests for ChangeDetector.detect_changes()."""

    def test_no_git_available_returns_empty(self, detector):
        """R3.8: Missing git returns empty changes."""
        with patch("subprocess.run", side_effect=FileNotFoundError("git not found")):
            result = detector.detect_changes()

        assert result == {
            "modified_functions": [],
            "added_files": [],
            "deleted_files": [],
            "renamed_files": [],
        }

    def test_detached_head_returns_empty(self, detector):
        """R3.8: Detached HEAD / no commits returns empty changes."""
        mock_result = MagicMock()
        mock_result.returncode = 128
        mock_result.stderr = "fatal: not a git repository"

        with patch("subprocess.run", return_value=mock_result):
            result = detector.detect_changes()

        assert result["modified_functions"] == []
        assert result["added_files"] == []

    def test_first_run_no_change_log(self, detector, tmp_project):
        """R3.7: Missing change_log.json triggers first-run behavior."""
        # Simulate git rev-parse HEAD succeeding
        mock_head = MagicMock()
        mock_head.returncode = 0
        mock_head.stdout = "abc123\n"

        with patch("subprocess.run", return_value=mock_head):
            result = detector.detect_changes()

        # Should return empty (first run) but create change_log.json
        assert result["modified_functions"] == []
        change_log = tmp_project / ".kognisant" / "world_model" / "change_log.json"
        assert change_log.exists()
        data = json.loads(change_log.read_text())
        assert data["head_hash"] == "abc123"

    def test_corrupted_change_log(self, detector, tmp_project):
        """R3.7: Corrupted change_log.json is treated as first run."""
        # Write invalid JSON
        change_log = tmp_project / ".kognisant" / "world_model" / "change_log.json"
        change_log.write_text("not valid json {{{{")

        mock_head = MagicMock()
        mock_head.returncode = 0
        mock_head.stdout = "def456\n"

        with patch("subprocess.run", return_value=mock_head):
            result = detector.detect_changes()

        assert result["modified_functions"] == []
        # change_log.json should be recreated
        data = json.loads(change_log.read_text())
        assert data["head_hash"] == "def456"

    def test_same_head_returns_empty(self, detector, tmp_project):
        """No changes if HEAD hasn't changed."""
        change_log = tmp_project / ".kognisant" / "world_model" / "change_log.json"
        change_log.write_text(json.dumps({"head_hash": "abc123"}))

        mock_head = MagicMock()
        mock_head.returncode = 0
        mock_head.stdout = "abc123\n"

        with patch("subprocess.run", return_value=mock_head):
            result = detector.detect_changes()

        assert result == {
            "modified_functions": [],
            "added_files": [],
            "deleted_files": [],
            "renamed_files": [],
        }

    def test_parses_modified_added_deleted_renamed(self, detector, tmp_project):
        """R3.1: Correctly parses git diff --name-status output."""
        change_log = tmp_project / ".kognisant" / "world_model" / "change_log.json"
        change_log.write_text(json.dumps({"head_hash": "old123"}))

        def mock_subprocess_run(cmd, **kwargs):
            result = MagicMock()
            if cmd[1] == "rev-parse":
                result.returncode = 0
                result.stdout = "new456\n"
            elif cmd[1] == "diff" and "--name-status" in cmd:
                result.returncode = 0
                result.stdout = "M\tsrc/utils.py\nA\tsrc/new_file.py\nD\tsrc/old.py\nR100\tsrc/renamed_old.py\tsrc/renamed_new.py\n"
            elif cmd[1] == "diff" and "-U0" in cmd:
                # No function-level diff needed for this test
                result.returncode = 0
                result.stdout = ""
            else:
                result.returncode = 0
                result.stdout = ""
            return result

        with patch("subprocess.run", side_effect=mock_subprocess_run):
            result = detector.detect_changes()

        assert "src/new_file.py" in result["added_files"]
        assert "src/old.py" in result["deleted_files"]
        assert ("src/renamed_old.py", "src/renamed_new.py") in result["renamed_files"]

    def test_identifies_modified_functions(self, detector, tmp_project):
        """R3.2: Modified functions are identified via diff hunk overlap."""
        change_log = tmp_project / ".kognisant" / "world_model" / "change_log.json"
        change_log.write_text(json.dumps({"head_hash": "old123"}))

        # Create the Python file at the expected location
        src_dir = tmp_project / "src"
        src_dir.mkdir(exist_ok=True)
        py_file = src_dir / "utils.py"
        py_file.write_text(
            "def foo():\n"
            "    return 1\n"
            "\n"
            "def bar():\n"
            "    return 2\n"
        )

        def mock_subprocess_run(cmd, **kwargs):
            result = MagicMock()
            if cmd[1] == "rev-parse":
                result.returncode = 0
                result.stdout = "new456\n"
            elif cmd[1] == "diff" and "--name-status" in cmd:
                result.returncode = 0
                result.stdout = "M\tsrc/utils.py\n"
            elif cmd[1] == "diff" and "-U0" in cmd:
                # Hunk modifying line 2 (inside foo, lines 1-2)
                result.returncode = 0
                result.stdout = "@@ -2,1 +2,1 @@\n-    return 1\n+    return 42\n"
            else:
                result.returncode = 0
                result.stdout = ""
            return result

        with patch("subprocess.run", side_effect=mock_subprocess_run):
            result = detector.detect_changes()

        # foo should be modified (lines 1-2), bar should not (lines 4-5)
        assert any("foo" in f for f in result["modified_functions"])
        assert not any("bar" in f for f in result["modified_functions"])

    def test_updates_change_log_after_detection(self, detector, tmp_project):
        """R3.6: HEAD hash updated in change_log.json after detection."""
        change_log = tmp_project / ".kognisant" / "world_model" / "change_log.json"
        change_log.write_text(json.dumps({"head_hash": "old123"}))

        def mock_subprocess_run(cmd, **kwargs):
            result = MagicMock()
            if cmd[1] == "rev-parse":
                result.returncode = 0
                result.stdout = "new789\n"
            elif cmd[1] == "diff":
                result.returncode = 0
                result.stdout = ""
            else:
                result.returncode = 0
                result.stdout = ""
            return result

        with patch("subprocess.run", side_effect=mock_subprocess_run):
            detector.detect_changes()

        data = json.loads(change_log.read_text())
        assert data["head_hash"] == "new789"


# ───────────────────────────────────────────────────────────
# Tests: apply_invalidations()
# ───────────────────────────────────────────────────────────


class TestApplyInvalidations:
    """Tests for ChangeDetector.apply_invalidations()."""

    def test_modified_functions_reduce_confidence(self, detector, graph):
        """R3.2: Outgoing edge confidence reduced by 50% for modified functions."""
        changes = {
            "modified_functions": ["utils.helper_func"],
            "added_files": [],
            "deleted_files": [],
            "renamed_files": [],
        }

        detector.apply_invalidations(changes, graph)

        # helper_func had edges with confidence 0.9 and 0.8
        edges = graph.get_edges_from("utils.helper_func")
        for edge in edges:
            if edge.id == "e1":
                assert edge.confidence == pytest.approx(0.45)  # 0.9 * 0.5
            elif edge.id == "e2":
                assert edge.confidence == pytest.approx(0.40)  # 0.8 * 0.5

    def test_deleted_files_remove_nodes(self, detector, graph):
        """R3.3: Nodes from deleted files are removed from graph."""
        changes = {
            "modified_functions": [],
            "added_files": [],
            "deleted_files": ["src/utils.py"],
            "renamed_files": [],
        }

        detector.apply_invalidations(changes, graph)

        # Both utils nodes should be removed
        assert graph.get_node("utils.helper_func") is None
        assert graph.get_node("utils.other_func") is None
        # main.run should still exist
        assert graph.get_node("main.run") is not None

    def test_added_files_trigger_analysis(self, detector, graph, tmp_project):
        """R3.4: Added files trigger static analysis and add nodes/edges."""
        # Create a new Python file
        new_file = tmp_project / "new_module.py"
        new_file.write_text("def new_func():\n    pass\n")

        changes = {
            "modified_functions": [],
            "added_files": ["new_module.py"],
            "deleted_files": [],
            "renamed_files": [],
        }

        detector.apply_invalidations(changes, graph)

        # New node should be added
        # The module name is derived from path relative to project root
        new_node = graph.get_node("new_module.new_func")
        assert new_node is not None
        assert new_node.node_type == "function"

    def test_renamed_files_update_paths(self, detector, graph):
        """R3.5: Renamed files update file_path on nodes."""
        changes = {
            "modified_functions": [],
            "added_files": [],
            "deleted_files": [],
            "renamed_files": [("src/utils.py", "src/helpers.py")],
        }

        detector.apply_invalidations(changes, graph)

        # Nodes should have updated file_path
        node_a = graph.get_node("utils.helper_func")
        assert node_a is not None
        assert node_a.file_path == "src/helpers.py"

        node_b = graph.get_node("utils.other_func")
        assert node_b is not None
        assert node_b.file_path == "src/helpers.py"

    def test_renamed_files_preserve_edges(self, detector, graph):
        """R3.5: Renamed files preserve edges and confidence scores."""
        # Record original edge confidence
        original_edges = graph.get_edges_from("utils.helper_func")
        original_confidences = {e.id: e.confidence for e in original_edges}

        changes = {
            "modified_functions": [],
            "added_files": [],
            "deleted_files": [],
            "renamed_files": [("src/utils.py", "src/helpers.py")],
        }

        detector.apply_invalidations(changes, graph)

        # Edges should still exist with same confidence
        edges_after = graph.get_edges_from("utils.helper_func")
        assert len(edges_after) == len(original_edges)
        for edge in edges_after:
            assert edge.confidence == original_confidences[edge.id]

    def test_empty_changes_no_effect(self, detector, graph):
        """Empty changes dict should not modify the graph."""
        changes = {
            "modified_functions": [],
            "added_files": [],
            "deleted_files": [],
            "renamed_files": [],
        }

        node_count_before = len(graph._nodes)
        detector.apply_invalidations(changes, graph)
        assert len(graph._nodes) == node_count_before
