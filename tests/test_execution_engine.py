"""Tests for ExecutionEngine in goal_engine.py.

Covers: R13.1 (task description building), R13.2 (pre-execution snapshot),
R13.3 (snapshot failure handling), R13.4 (edge reinforcement on success),
R13.5 (failure/timeout with rollback), R13.6 (goal status updates).
"""

import json
import os
import time
from unittest.mock import MagicMock, patch

import pytest

from cli_kognisant.goal_engine import ExecutionEngine
from cli_kognisant.models import Edge, Goal, Node, utc_now_iso


@pytest.fixture
def project_root(tmp_path):
    """Create a temporary project root with .kognisant/goals/ directory."""
    goals_dir = tmp_path / ".kognisant" / "goals"
    goals_dir.mkdir(parents=True)
    return str(tmp_path)


@pytest.fixture
def mock_store():
    """Create a mock WorldModelStore."""
    store = MagicMock()
    store.create_snapshot.return_value = "/tmp/snapshots/20250101T000000Z"
    store.restore_snapshot.return_value = None
    store.delete_snapshot.return_value = None
    return store


@pytest.fixture
def mock_graph():
    """Create a mock DependencyGraph with sample data."""
    graph = MagicMock()

    # Sample edges
    edge1 = Edge(
        id="e1",
        source="module_a.func_x",
        target="module_b.func_y",
        edge_type="calls",
        confidence=0.8,
        provenance="static",
        version=1,
    )
    edge2 = Edge(
        id="e2",
        source="module_b.func_y",
        target="module_c.func_z",
        edge_type="calls",
        confidence=0.5,
        provenance="dynamic",
        version=2,
    )
    edge3 = Edge(
        id="e3",
        source="module_a.func_x",
        target="module_d.func_w",
        edge_type="imports",
        confidence=0.2,  # Below threshold
        provenance="static",
        version=1,
    )

    # query_reachable returns nodes within 2 hops
    node_b = Node(
        id="module_b.func_y",
        node_type="function",
        file_path="module_b.py",
        line_start=10,
        line_end=20,
        last_modified="2025-01-01T00:00:00+00:00",
    )
    node_c = Node(
        id="module_c.func_z",
        node_type="function",
        file_path="module_c.py",
        line_start=5,
        line_end=15,
        last_modified="2025-01-01T00:00:00+00:00",
    )
    graph.query_reachable.return_value = [node_b, node_c]

    # get_edges_from returns edges from a node
    def mock_get_edges_from(node_id):
        if node_id == "module_a.func_x":
            return [edge1, edge3]
        elif node_id == "module_b.func_y":
            return [edge2]
        return []

    def mock_get_edges_to(node_id):
        if node_id == "module_a.func_x":
            return []
        elif node_id == "module_b.func_y":
            return [edge1]
        return []

    graph.get_edges_from.side_effect = mock_get_edges_from
    graph.get_edges_to.side_effect = mock_get_edges_to

    return graph


@pytest.fixture
def sample_goal():
    """Create a sample goal for testing."""
    return Goal(
        id="cv-001",
        goal_type="contract_violation",
        title="Fix contract violation between func_x and func_y",
        target_node="module_a.func_x",
        target_file="module_a.py",
        context={
            "source_node": "module_a.func_x",
            "target_node": "module_b.func_y",
            "contract_id": "c-123",
        },
        priority_score=5.0,
        status="accepted",
        created_at="2025-01-01T00:00:00+00:00",
    )


@pytest.fixture
def engine(mock_store, mock_graph, project_root):
    """Create an ExecutionEngine instance with mocks."""
    return ExecutionEngine(
        store=mock_store,
        graph=mock_graph,
        project_root=project_root,
    )


class TestBuildTaskDescription:
    """Test build_task_description produces rich task context (R13.1)."""

    def test_includes_goal_type_and_title(self, engine, sample_goal):
        desc = engine.build_task_description(sample_goal)
        assert "contract_violation" in desc
        assert sample_goal.title in desc

    def test_includes_affected_nodes(self, engine, sample_goal):
        desc = engine.build_task_description(sample_goal)
        assert "module_a.func_x" in desc

    def test_includes_causal_chain_edges(self, engine, sample_goal):
        desc = engine.build_task_description(sample_goal)
        # Should include edges with confidence > 0.3
        assert "module_b.func_y" in desc
        assert "calls" in desc

    def test_excludes_low_confidence_edges(self, engine, sample_goal):
        desc = engine.build_task_description(sample_goal)
        # edge3 has confidence 0.2, should not appear in causal chain
        assert "module_d.func_w" not in desc

    def test_includes_remediation_steps(self, engine, sample_goal):
        desc = engine.build_task_description(sample_goal)
        assert "Review the contract" in desc
        assert "Update function signatures" in desc

    def test_includes_additional_context(self, engine, sample_goal):
        desc = engine.build_task_description(sample_goal)
        assert "contract_id" in desc
        assert "c-123" in desc

    def test_handles_goal_without_target_node(self, engine):
        goal = Goal(
            id="cg-001",
            goal_type="coverage_gap",
            title="Improve test coverage for module_x",
            target_node=None,
            target_file="module_x.py",
            context={"affected_functions": ["func_a", "func_b"]},
            status="accepted",
            created_at="2025-01-01T00:00:00+00:00",
        )
        desc = engine.build_task_description(goal)
        assert "coverage_gap" in desc
        assert "module_x.py" in desc

    def test_handles_all_goal_types(self, engine):
        for goal_type in [
            "contract_violation",
            "coverage_gap",
            "decay_alert",
            "complexity",
            "stale_artifact",
            "pattern_detection",
        ]:
            goal = Goal(
                id=f"test-{goal_type}",
                goal_type=goal_type,
                title=f"Test {goal_type}",
                target_node="some.node",
                context={},
                status="accepted",
                created_at="2025-01-01T00:00:00+00:00",
            )
            desc = engine.build_task_description(goal)
            assert goal_type in desc
            assert "Remediation" in desc


class TestSnapshotCreation:
    """Test pre-execution snapshot creation (R13.2, R13.3)."""

    def test_creates_snapshot_with_affected_nodes(
        self, mock_store, mock_graph, sample_goal, project_root
    ):
        engine = ExecutionEngine(
            store=mock_store,
            graph=mock_graph,
            project_root=project_root,
        )
        engine.execute_goal(sample_goal, {}, [])
        mock_store.create_snapshot.assert_called_once()
        # Should include target_node and source_node from context
        call_args = mock_store.create_snapshot.call_args[0][0]
        assert "module_a.func_x" in call_args

    def test_snapshot_failure_aborts_execution(
        self, mock_store, mock_graph, sample_goal, project_root
    ):
        """R13.3: Snapshot failure → goal status=failed."""
        mock_store.create_snapshot.side_effect = OSError("disk full")
        engine = ExecutionEngine(
            store=mock_store,
            graph=mock_graph,
            project_root=project_root,
        )

        result = engine.execute_goal(sample_goal, {}, [])

        assert result is False
        assert sample_goal.status == "failed"
        assert sample_goal.context["failure_reason"] == "snapshot creation failed"
        assert sample_goal.resolved_at is not None

    def test_snapshot_failure_does_not_call_perp(
        self, mock_store, mock_graph, sample_goal, project_root
    ):
        mock_store.create_snapshot.side_effect = OSError("disk full")
        perp_cb = MagicMock(return_value=True)
        engine = ExecutionEngine(
            store=mock_store,
            graph=mock_graph,
            perp_callback=perp_cb,
            project_root=project_root,
        )

        engine.execute_goal(sample_goal, {}, [])
        perp_cb.assert_not_called()


class TestExecuteGoalSuccess:
    """Test successful goal execution (R13.4, R13.6)."""

    def test_stub_mode_succeeds_without_perp_callback(
        self, engine, sample_goal
    ):
        """Without a perp_callback, goal is marked completed (stub mode)."""
        result = engine.execute_goal(sample_goal, {}, [])
        assert result is True
        assert sample_goal.status == "completed"
        assert sample_goal.resolved_at is not None

    def test_successful_callback_marks_completed(
        self, mock_store, mock_graph, sample_goal, project_root
    ):
        perp_cb = MagicMock(return_value=True)
        engine = ExecutionEngine(
            store=mock_store,
            graph=mock_graph,
            perp_callback=perp_cb,
            project_root=project_root,
        )

        result = engine.execute_goal(sample_goal, {"root": "/project"}, ["model1"])
        assert result is True
        assert sample_goal.status == "completed"
        perp_cb.assert_called_once()

    def test_success_deletes_snapshot(
        self, mock_store, mock_graph, sample_goal, project_root
    ):
        engine = ExecutionEngine(
            store=mock_store,
            graph=mock_graph,
            project_root=project_root,
        )
        engine.execute_goal(sample_goal, {}, [])
        mock_store.delete_snapshot.assert_called_once()

    def test_success_reinforces_edges(
        self, mock_store, mock_graph, sample_goal, project_root
    ):
        """R13.4: Successful execution reinforces traversed edges."""
        maintenance = MagicMock()
        engine = ExecutionEngine(
            store=mock_store,
            graph=mock_graph,
            maintenance_engine=maintenance,
            project_root=project_root,
        )

        engine.execute_goal(sample_goal, {}, [])
        maintenance.reinforce_edges.assert_called_once()
        # Should reinforce edges with confidence > 0.3
        edge_ids = maintenance.reinforce_edges.call_args[0][0]
        assert "e1" in edge_ids  # confidence 0.8
        assert "e2" in edge_ids  # confidence 0.5
        # edge3 has confidence 0.2, below threshold
        assert "e3" not in edge_ids


class TestExecuteGoalFailure:
    """Test failure handling and rollback (R13.5)."""

    def test_failed_callback_restores_snapshot(
        self, mock_store, mock_graph, sample_goal, project_root
    ):
        perp_cb = MagicMock(return_value=False)
        engine = ExecutionEngine(
            store=mock_store,
            graph=mock_graph,
            perp_callback=perp_cb,
            project_root=project_root,
        )

        result = engine.execute_goal(sample_goal, {}, [])
        assert result is False
        assert sample_goal.status == "failed"
        mock_store.restore_snapshot.assert_called_once()

    def test_exception_in_callback_restores_snapshot(
        self, mock_store, mock_graph, sample_goal, project_root
    ):
        perp_cb = MagicMock(side_effect=RuntimeError("PERP crashed"))
        engine = ExecutionEngine(
            store=mock_store,
            graph=mock_graph,
            perp_callback=perp_cb,
            project_root=project_root,
        )

        result = engine.execute_goal(sample_goal, {}, [])
        assert result is False
        assert sample_goal.status == "failed"
        assert "execution error" in sample_goal.context["failure_reason"]
        mock_store.restore_snapshot.assert_called_once()

    def test_timeout_restores_snapshot(
        self, mock_store, mock_graph, sample_goal, project_root
    ):
        """R13.5: 10-minute timeout triggers rollback."""

        def slow_perp(*args):
            time.sleep(5)  # Would be 600s in production
            return True

        engine = ExecutionEngine(
            store=mock_store,
            graph=mock_graph,
            perp_callback=slow_perp,
            project_root=project_root,
        )
        # Override timeout for test speed
        engine.EXECUTION_TIMEOUT_SECONDS = 0.1

        result = engine.execute_goal(sample_goal, {}, [])
        assert result is False
        assert sample_goal.status == "failed"
        assert "timed out" in sample_goal.context["failure_reason"]
        mock_store.restore_snapshot.assert_called_once()

    def test_failure_records_reason(
        self, mock_store, mock_graph, sample_goal, project_root
    ):
        perp_cb = MagicMock(return_value=False)
        engine = ExecutionEngine(
            store=mock_store,
            graph=mock_graph,
            perp_callback=perp_cb,
            project_root=project_root,
        )

        engine.execute_goal(sample_goal, {}, [])
        assert "failure_reason" in sample_goal.context
        assert sample_goal.context["failure_reason"] != ""


class TestGoalStatusUpdates:
    """Test goal status persistence to active.json and completed.json (R13.6)."""

    def test_completed_goal_moved_to_completed_json(
        self, engine, sample_goal, project_root
    ):
        engine.execute_goal(sample_goal, {}, [])

        completed_path = os.path.join(
            project_root, ".kognisant", "goals", "completed.json"
        )
        assert os.path.exists(completed_path)
        with open(completed_path) as f:
            completed = json.load(f)
        assert len(completed) == 1
        assert completed[0]["id"] == "cv-001"
        assert completed[0]["status"] == "completed"

    def test_completed_goal_removed_from_active_json(
        self, engine, sample_goal, project_root
    ):
        engine.execute_goal(sample_goal, {}, [])

        active_path = os.path.join(
            project_root, ".kognisant", "goals", "active.json"
        )
        assert os.path.exists(active_path)
        with open(active_path) as f:
            active = json.load(f)
        # Goal should not be in active
        goal_ids = [g["id"] for g in active]
        assert "cv-001" not in goal_ids

    def test_failed_goal_moved_to_completed_json(
        self, mock_store, mock_graph, project_root
    ):
        mock_store.create_snapshot.side_effect = OSError("disk full")
        goal = Goal(
            id="cv-002",
            goal_type="contract_violation",
            title="Test failure",
            target_node="node_a",
            context={},
            status="accepted",
            created_at="2025-01-01T00:00:00+00:00",
        )
        engine = ExecutionEngine(
            store=mock_store,
            graph=mock_graph,
            project_root=project_root,
        )

        engine.execute_goal(goal, {}, [])

        completed_path = os.path.join(
            project_root, ".kognisant", "goals", "completed.json"
        )
        with open(completed_path) as f:
            completed = json.load(f)
        assert len(completed) == 1
        assert completed[0]["status"] == "failed"

    def test_active_json_preserves_other_goals(
        self, engine, sample_goal, project_root
    ):
        """Other goals in active.json are not affected."""
        active_path = os.path.join(
            project_root, ".kognisant", "goals", "active.json"
        )
        # Pre-populate with another goal
        other_goal = {
            "id": "cg-001",
            "goal_type": "coverage_gap",
            "title": "Other goal",
            "status": "active",
        }
        with open(active_path, "w") as f:
            json.dump([other_goal], f)

        engine.execute_goal(sample_goal, {}, [])

        with open(active_path) as f:
            active = json.load(f)
        assert len(active) == 1
        assert active[0]["id"] == "cg-001"

    def test_no_project_root_skips_persistence(
        self, mock_store, mock_graph, sample_goal
    ):
        """When project_root is None, status update is skipped gracefully."""
        engine = ExecutionEngine(
            store=mock_store,
            graph=mock_graph,
            project_root=None,
        )
        # Should not raise
        result = engine.execute_goal(sample_goal, {}, [])
        assert result is True
