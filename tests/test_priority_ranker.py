"""Tests for PriorityRanker in goal_engine.py.

Covers: R11.1 (formula correctness), R11.2 (impact_radius),
R11.3 (severity weights), R11.4 (effort mapping), R11.5 (ranking & tiebreaker),
R11.6 (zero effort handling).
"""

import pytest

from cli_kognisant.goal_engine import PriorityRanker
from cli_kognisant.models import Edge, Goal, Node, generate_uuid, utc_now_iso
from cli_kognisant.world_model import DependencyGraph


@pytest.fixture
def graph():
    """Create a DependencyGraph with a small connected topology."""
    g = DependencyGraph()

    # Add nodes
    now = utc_now_iso()
    nodes = [
        Node(id="mod.funcA", node_type="function", file_path="mod.py",
             line_start=1, line_end=10, last_modified=now, module="mod"),
        Node(id="mod.funcB", node_type="function", file_path="mod.py",
             line_start=11, line_end=20, last_modified=now, module="mod"),
        Node(id="mod.funcC", node_type="function", file_path="mod.py",
             line_start=21, line_end=30, last_modified=now, module="mod"),
        Node(id="mod.funcD", node_type="function", file_path="mod.py",
             line_start=31, line_end=40, last_modified=now, module="mod"),
        Node(id="other.funcE", node_type="function", file_path="other.py",
             line_start=1, line_end=10, last_modified=now, module="other"),
    ]
    for node in nodes:
        g.add_node(node)

    # Add edges: A -> B -> C -> D, A -> E
    # All with confidence >= 0.3 so they are traversed
    edges = [
        Edge(id=generate_uuid(), source="mod.funcA", target="mod.funcB",
             edge_type="calls", confidence=0.8, provenance="static"),
        Edge(id=generate_uuid(), source="mod.funcB", target="mod.funcC",
             edge_type="calls", confidence=0.6, provenance="static"),
        Edge(id=generate_uuid(), source="mod.funcC", target="mod.funcD",
             edge_type="calls", confidence=0.5, provenance="dynamic"),
        Edge(id=generate_uuid(), source="mod.funcA", target="other.funcE",
             edge_type="imports", confidence=0.9, provenance="static"),
    ]
    for edge in edges:
        g.add_edge(edge)

    return g


@pytest.fixture
def ranker(graph):
    """Create a PriorityRanker instance."""
    return PriorityRanker(graph)


class TestComputeScore:
    """Test compute_score formula correctness (R11.1)."""

    def test_basic_formula(self, ranker):
        """Score = (impact_radius × severity_weight × likelihood) / effort."""
        goal = Goal(
            id="cv-001",
            goal_type="contract_violation",
            title="Test violation",
            target_node="mod.funcA",
            context={"source_node": "mod.funcA", "target_node": "mod.funcB"},
            created_at="2025-01-01T00:00:00+00:00",
        )
        score = ranker.compute_score(goal)
        # impact_radius: A can reach B (1 hop), C (2 hops), E (1 hop) = 3 nodes
        # severity_weight: contract_violation = 3.0
        # likelihood: max confidence of edges from/to A = 0.9 (A->E edge)
        # effort: source_node + target_node = 2 items → effort 1
        expected = (3 * 3.0 * 0.9) / 1
        assert abs(score - expected) < 0.001

    def test_disconnected_node_impact_radius_is_1(self, graph):
        """Disconnected nodes should have impact_radius = 1."""
        # Add an isolated node
        isolated = Node(
            id="isolated.func", node_type="function", file_path="isolated.py",
            line_start=1, line_end=5, last_modified=utc_now_iso(), module="isolated",
        )
        graph.add_node(isolated)
        ranker = PriorityRanker(graph)

        goal = Goal(
            id="sa-001",
            goal_type="stale_artifact",
            title="Stale isolated",
            target_node="isolated.func",
            context={"file_path": "isolated.py"},
            created_at="2025-01-01T00:00:00+00:00",
        )
        score = ranker.compute_score(goal)
        # impact_radius = 1 (disconnected), severity = 1.0, likelihood = 0.5 (default, no edges)
        # effort: file_path = 1 item → effort 1
        expected = (1 * 1.0 * 0.5) / 1
        assert abs(score - expected) < 0.001

    def test_null_target_node(self, ranker):
        """Goals with no target_node should get impact_radius=1, likelihood=0.5."""
        goal = Goal(
            id="sa-002",
            goal_type="stale_artifact",
            title="File-level goal",
            target_node=None,
            target_file="some_file.py",
            context={"file_path": "some_file.py"},
            created_at="2025-01-01T00:00:00+00:00",
        )
        score = ranker.compute_score(goal)
        # impact_radius=1, severity=1.0, likelihood=0.5, effort=1
        expected = (1 * 1.0 * 0.5) / 1
        assert abs(score - expected) < 0.001


class TestImpactRadius:
    """Test impact_radius calculation (R11.2)."""

    def test_counts_nodes_within_2_hops(self, ranker):
        """Should count reachable nodes within 2 hops with confidence >= 0.3."""
        goal = Goal(
            id="cv-001",
            goal_type="contract_violation",
            title="Test",
            target_node="mod.funcA",
            context={},
            created_at="2025-01-01T00:00:00+00:00",
        )
        radius = ranker._compute_impact_radius(goal)
        # A -> B (1 hop), A -> E (1 hop), B -> C (2 hops from A) = 3 nodes
        # D is 3 hops from A, so not included
        assert radius == 3

    def test_respects_confidence_threshold(self, graph):
        """Edges below 0.3 confidence should not be traversed."""
        # Add a low-confidence edge
        low_edge = Edge(
            id=generate_uuid(), source="mod.funcA", target="mod.funcD",
            edge_type="calls", confidence=0.2, provenance="static",
        )
        graph.add_edge(low_edge)
        ranker = PriorityRanker(graph)

        goal = Goal(
            id="cv-001", goal_type="contract_violation", title="Test",
            target_node="mod.funcA", context={},
            created_at="2025-01-01T00:00:00+00:00",
        )
        radius = ranker._compute_impact_radius(goal)
        # A -> B (0.8), A -> E (0.9), B -> C (0.6) = 3 reachable
        # A -> D (0.2) is below threshold, doesn't count
        assert radius == 3

    def test_nonexistent_node_returns_1(self, ranker):
        """A target_node not in the graph should return impact_radius = 1."""
        goal = Goal(
            id="cv-001", goal_type="contract_violation", title="Test",
            target_node="nonexistent.func", context={},
            created_at="2025-01-01T00:00:00+00:00",
        )
        radius = ranker._compute_impact_radius(goal)
        assert radius == 1


class TestEffortMapping:
    """Test effort estimation and bracket mapping (R11.4)."""

    def test_empty_context_returns_1(self, ranker):
        """Empty context should map to effort 1 (R11.6)."""
        goal = Goal(
            id="cv-001", goal_type="contract_violation", title="Test",
            target_node="mod.funcA", context={},
            created_at="2025-01-01T00:00:00+00:00",
        )
        effort = ranker._compute_effort(goal)
        assert effort == 1

    def test_bracket_1_2_items(self, ranker):
        """1-2 items → effort 1."""
        goal = Goal(
            id="cv-001", goal_type="contract_violation", title="Test",
            target_node="mod.funcA",
            context={"source_node": "a", "target_node": "b"},
            created_at="2025-01-01T00:00:00+00:00",
        )
        effort = ranker._compute_effort(goal)
        assert effort == 1

    def test_bracket_3_4_items(self, ranker):
        """3-4 items → effort 2."""
        goal = Goal(
            id="cg-001", goal_type="coverage_gap", title="Test",
            target_node="mod.funcA",
            context={"affected_functions": ["f1", "f2", "f3"]},
            created_at="2025-01-01T00:00:00+00:00",
        )
        effort = ranker._compute_effort(goal)
        assert effort == 2

    def test_bracket_5_6_items(self, ranker):
        """5-6 items → effort 3."""
        goal = Goal(
            id="cg-001", goal_type="coverage_gap", title="Test",
            target_node="mod.funcA",
            context={"affected_functions": ["f1", "f2", "f3", "f4", "f5"]},
            created_at="2025-01-01T00:00:00+00:00",
        )
        effort = ranker._compute_effort(goal)
        assert effort == 3

    def test_bracket_37_plus_items(self, ranker):
        """37+ items → effort 10."""
        goal = Goal(
            id="cg-001", goal_type="coverage_gap", title="Test",
            target_node="mod.funcA",
            context={"affected_functions": [f"f{i}" for i in range(40)]},
            created_at="2025-01-01T00:00:00+00:00",
        )
        effort = ranker._compute_effort(goal)
        assert effort == 10

    def test_map_effort_all_brackets(self, ranker):
        """Verify all bracket boundaries produce correct effort values."""
        # (item_count, expected_effort)
        test_cases = [
            (1, 1), (2, 1),
            (3, 2), (4, 2),
            (5, 3), (6, 3),
            (7, 4), (8, 4), (9, 4),
            (10, 5), (11, 5), (12, 5),
            (13, 6), (14, 6), (15, 6), (16, 6),
            (17, 7), (18, 7), (19, 7), (20, 7), (21, 7),
            (22, 8), (25, 8), (28, 8),
            (29, 9), (32, 9), (36, 9),
            (37, 10), (50, 10), (100, 10),
        ]
        for item_count, expected_effort in test_cases:
            assert ranker._map_effort(item_count) == expected_effort, (
                f"item_count={item_count} should map to effort={expected_effort}"
            )


class TestZeroEffort:
    """Test zero-effort handling (R11.6)."""

    def test_empty_context_no_division_by_zero(self, ranker):
        """Goals with empty context should not cause division by zero."""
        goal = Goal(
            id="da-001", goal_type="decay_alert", title="Decay test",
            target_node="mod.funcA", context={},
            created_at="2025-01-01T00:00:00+00:00",
        )
        # Should not raise ZeroDivisionError
        score = ranker.compute_score(goal)
        assert score > 0

    def test_context_with_no_countable_items(self, ranker):
        """Context with no recognized keys should default to effort 1."""
        goal = Goal(
            id="da-001", goal_type="decay_alert", title="Decay test",
            target_node="mod.funcA",
            context={"pruned_count": 7, "module": "mod"},
            created_at="2025-01-01T00:00:00+00:00",
        )
        effort = ranker._compute_effort(goal)
        assert effort == 1


class TestRankGoals:
    """Test rank_goals sorting and tiebreaker (R11.5)."""

    def test_sorts_descending_by_score(self, ranker):
        """Goals should be sorted by priority_score descending."""
        goals = [
            Goal(id="cv-001", goal_type="contract_violation", title="Low impact",
                 target_node="mod.funcD", context={"source_node": "x", "target_node": "y"},
                 created_at="2025-01-01T00:00:00+00:00"),
            Goal(id="cv-002", goal_type="contract_violation", title="High impact",
                 target_node="mod.funcA", context={"source_node": "x", "target_node": "y"},
                 created_at="2025-01-02T00:00:00+00:00"),
        ]
        ranked = ranker.rank_goals(goals)
        assert ranked[0].priority_score >= ranked[1].priority_score

    def test_tiebreaker_oldest_first(self, graph):
        """Equal scores should be broken by creation timestamp (oldest first)."""
        # Create two goals targeting the same node with same context
        # so they produce identical scores
        g = DependencyGraph()
        node = Node(
            id="mod.func", node_type="function", file_path="mod.py",
            line_start=1, line_end=10, last_modified=utc_now_iso(), module="mod",
        )
        g.add_node(node)
        ranker = PriorityRanker(g)

        goal_older = Goal(
            id="cv-001", goal_type="contract_violation", title="Older",
            target_node="mod.func", context={},
            created_at="2025-01-01T00:00:00+00:00",
        )
        goal_newer = Goal(
            id="cv-002", goal_type="contract_violation", title="Newer",
            target_node="mod.func", context={},
            created_at="2025-01-02T00:00:00+00:00",
        )
        ranked = ranker.rank_goals([goal_newer, goal_older])
        # Same score → oldest first
        assert ranked[0].id == "cv-001"
        assert ranked[1].id == "cv-002"

    def test_updates_priority_score_on_goals(self, ranker):
        """rank_goals should set priority_score on each goal object."""
        goal = Goal(
            id="cv-001", goal_type="contract_violation", title="Test",
            target_node="mod.funcA", context={"source_node": "a", "target_node": "b"},
            created_at="2025-01-01T00:00:00+00:00",
        )
        assert goal.priority_score == 0.0
        ranker.rank_goals([goal])
        assert goal.priority_score > 0.0

    def test_empty_list(self, ranker):
        """rank_goals with empty list should return empty list."""
        result = ranker.rank_goals([])
        assert result == []


class TestLikelihood:
    """Test likelihood computation."""

    def test_max_confidence_from_connected_edges(self, ranker):
        """Likelihood should be the max confidence from connected edges."""
        goal = Goal(
            id="cv-001", goal_type="contract_violation", title="Test",
            target_node="mod.funcA", context={},
            created_at="2025-01-01T00:00:00+00:00",
        )
        likelihood = ranker._compute_likelihood(goal)
        # A has outgoing edges: A->B (0.8), A->E (0.9)
        # No incoming edges to A
        assert abs(likelihood - 0.9) < 0.001

    def test_default_likelihood_no_edges(self, graph):
        """Nodes with no edges should return default likelihood of 0.5."""
        isolated = Node(
            id="isolated.func", node_type="function", file_path="isolated.py",
            line_start=1, line_end=5, last_modified=utc_now_iso(), module="isolated",
        )
        graph.add_node(isolated)
        ranker = PriorityRanker(graph)

        goal = Goal(
            id="sa-001", goal_type="stale_artifact", title="Test",
            target_node="isolated.func", context={},
            created_at="2025-01-01T00:00:00+00:00",
        )
        likelihood = ranker._compute_likelihood(goal)
        assert likelihood == 0.5

    def test_null_target_returns_default(self, ranker):
        """None target_node should return default likelihood of 0.5."""
        goal = Goal(
            id="sa-001", goal_type="stale_artifact", title="Test",
            target_node=None, context={},
            created_at="2025-01-01T00:00:00+00:00",
        )
        likelihood = ranker._compute_likelihood(goal)
        assert likelihood == 0.5


class TestSeverityWeights:
    """Test severity weight assignment (R11.3)."""

    def test_all_defined_weights(self, ranker):
        """All goal types should have correct severity weights."""
        assert PriorityRanker.SEVERITY_WEIGHTS["contract_violation"] == 3.0
        assert PriorityRanker.SEVERITY_WEIGHTS["coverage_gap"] == 2.0
        assert PriorityRanker.SEVERITY_WEIGHTS["decay_alert"] == 1.5
        assert PriorityRanker.SEVERITY_WEIGHTS["complexity"] == 2.5
        assert PriorityRanker.SEVERITY_WEIGHTS["stale_artifact"] == 1.0
        assert PriorityRanker.SEVERITY_WEIGHTS["pattern_detection"] == 2.8

    def test_unknown_type_defaults_to_1(self, ranker):
        """Unknown goal types should use a default severity weight of 1.0."""
        goal = Goal(
            id="xx-001", goal_type="unknown_type", title="Test",
            target_node="mod.funcA", context={},
            created_at="2025-01-01T00:00:00+00:00",
        )
        score = ranker.compute_score(goal)
        # severity_weight=1.0, impact=3, likelihood=0.9, effort=1
        expected = (3 * 1.0 * 0.9) / 1
        assert abs(score - expected) < 0.001
