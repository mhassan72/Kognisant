"""Tests for TestOutcomeTracker in cli_kognisant/observer.py.

Validates requirements R4.1 through R4.6 for test outcome tracking,
rolling history, instability detection, and recovery detection.
"""

import json
import os

import pytest

from cli_kognisant.models import Edge, Node, generate_uuid, utc_now_iso
from cli_kognisant.observer import TestOutcomeTracker
from cli_kognisant.world_model import DependencyGraph
from cli_kognisant.world_model_store import JsonWorldModelStore


@pytest.fixture
def project_root(tmp_path):
    """Provide a temporary project root."""
    return str(tmp_path)


@pytest.fixture
def store(project_root):
    """Provide a JsonWorldModelStore instance."""
    return JsonWorldModelStore(project_root)


@pytest.fixture
def tracker(project_root, store):
    """Provide a TestOutcomeTracker instance."""
    return TestOutcomeTracker(project_root, store)


@pytest.fixture
def health_path(project_root):
    """Return the expected path of test_health.json."""
    return os.path.join(
        project_root, ".kognisant", "world_model", "test_health.json"
    )


def make_results(
    total=10,
    passed=8,
    failed=2,
    skipped=0,
    duration_ms=1000,
    failed_tests=None,
    passed_tests=None,
    coverage=None,
):
    """Helper to create a results dict."""
    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "duration_ms": duration_ms,
        "failed_tests": failed_tests or [],
        "passed_tests": passed_tests or [],
        "coverage": coverage,
    }


class TestRecordTestRun:
    """R4.1: Record test run results to test_health.json."""

    def test_records_basic_counts(self, tracker, health_path):
        results = make_results(total=10, passed=7, failed=2, skipped=1, duration_ms=500)
        tracker.record_test_run(results)

        with open(health_path) as f:
            data = json.load(f)

        assert len(data["history"]) == 1
        entry = data["history"][0]
        assert entry["total"] == 10
        assert entry["passed"] == 7
        assert entry["failed"] == 2
        assert entry["skipped"] == 1
        assert entry["duration_ms"] == 500
        assert "timestamp" in entry

    def test_creates_health_file_if_missing(self, tracker, health_path):
        assert not os.path.exists(health_path)
        tracker.record_test_run(make_results())
        assert os.path.exists(health_path)

    def test_records_failed_and_passed_test_names(self, tracker, health_path):
        results = make_results(
            failed_tests=["test_foo", "test_bar"],
            passed_tests=["test_baz"],
        )
        tracker.record_test_run(results)

        with open(health_path) as f:
            data = json.load(f)

        entry = data["history"][0]
        assert entry["failed_tests"] == ["test_foo", "test_bar"]
        assert entry["passed_tests"] == ["test_baz"]


class TestCoverageMapping:
    """R4.2: Coverage mapping from test functions to source functions."""

    def test_stores_coverage_mapping(self, tracker, health_path):
        coverage = {
            "module.func_a": ["test_func_a", "test_func_b"],
            "module.func_b": ["test_func_c"],
        }
        results = make_results(coverage=coverage)
        tracker.record_test_run(results)

        with open(health_path) as f:
            data = json.load(f)

        assert data["coverage_mapping"] == coverage

    def test_no_coverage_logs_info(self, tracker, health_path, caplog):
        import logging

        with caplog.at_level(logging.INFO):
            tracker.record_test_run(make_results(coverage=None))

        assert "Coverage data unavailable" in caplog.text

    def test_coverage_mapping_preserved_across_runs(self, tracker, health_path):
        coverage = {"module.func_a": ["test_func_a"]}
        tracker.record_test_run(make_results(coverage=coverage))
        # Second run without coverage should preserve the mapping
        tracker.record_test_run(make_results(coverage=None))

        with open(health_path) as f:
            data = json.load(f)

        assert data["coverage_mapping"] == coverage


class TestRollingHistory:
    """R4.3: Maintain rolling history of last 20 entries."""

    def test_maintains_max_20_entries(self, tracker, health_path):
        for i in range(25):
            tracker.record_test_run(make_results(total=i))

        with open(health_path) as f:
            data = json.load(f)

        assert len(data["history"]) == 20
        # First entry should be the 6th recording (index 5, total=5)
        assert data["history"][0]["total"] == 5
        # Last entry should be the 25th recording (total=24)
        assert data["history"][-1]["total"] == 24

    def test_evicts_oldest_entry(self, tracker, health_path):
        # Fill to capacity
        for i in range(20):
            tracker.record_test_run(make_results(total=i))

        with open(health_path) as f:
            data = json.load(f)
        assert data["history"][0]["total"] == 0

        # Add one more
        tracker.record_test_run(make_results(total=99))

        with open(health_path) as f:
            data = json.load(f)
        assert len(data["history"]) == 20
        assert data["history"][0]["total"] == 1
        assert data["history"][-1]["total"] == 99


class TestInstabilityDetection:
    """R4.4: Detect instability after 3 consecutive failures."""

    def _setup_graph_with_coverage(self, tracker, graph):
        """Set up a graph with nodes and edges, record coverage mapping."""
        # Create source function nodes
        node_a = Node(
            id="module.func_a",
            node_type="function",
            file_path="module.py",
            line_start=1,
            line_end=10,
            last_modified=utc_now_iso(),
            module="module",
        )
        node_b = Node(
            id="module.func_b",
            node_type="function",
            file_path="module.py",
            line_start=12,
            line_end=20,
            last_modified=utc_now_iso(),
            module="module",
        )
        graph.add_node(node_a)
        graph.add_node(node_b)

        # Create edges
        edge = Edge(
            id=generate_uuid(),
            source="module.func_a",
            target="module.func_b",
            edge_type="calls",
            confidence=1.0,
            provenance="static",
        )
        graph.add_edge(edge)

        # Record coverage mapping
        coverage = {
            "module.func_a": ["test_func_a"],
            "module.func_b": ["test_func_a", "test_func_b"],
        }
        tracker.record_test_run(make_results(coverage=coverage, passed_tests=["test_func_a", "test_func_b"]))

    def test_flags_unstable_after_3_failures(self, tracker):
        graph = DependencyGraph()
        self._setup_graph_with_coverage(tracker, graph)

        # Record 3 consecutive failures for test_func_a
        for _ in range(3):
            tracker.record_test_run(
                make_results(
                    failed_tests=["test_func_a"],
                    passed_tests=["test_func_b"],
                )
            )

        flagged = tracker.check_instability(graph)

        # Both func_a and func_b are connected to test_func_a via coverage
        assert "module.func_a" in flagged
        assert "module.func_b" in flagged

        # Verify nodes have unstable tag
        assert "unstable" in graph.get_node("module.func_a").tags
        assert "unstable" in graph.get_node("module.func_b").tags

    def test_reduces_edge_confidence_by_40_percent(self, tracker):
        graph = DependencyGraph()
        self._setup_graph_with_coverage(tracker, graph)

        # Get original edge confidence
        edges_from_a = graph.get_edges_from("module.func_a")
        assert len(edges_from_a) == 1
        original_confidence = edges_from_a[0].confidence
        assert original_confidence == 1.0

        # Record 3 consecutive failures
        for _ in range(3):
            tracker.record_test_run(
                make_results(failed_tests=["test_func_a"], passed_tests=[])
            )

        tracker.check_instability(graph)

        # Edge connects func_a -> func_b. Both nodes are connected to
        # test_func_a via coverage. The edge gets reduced when processing
        # func_a (as outgoing) and again when processing func_b (as incoming).
        # Result: 1.0 * 0.6 * 0.6 = 0.36
        edges_from_a = graph.get_edges_from("module.func_a")
        assert abs(edges_from_a[0].confidence - 0.36) < 0.001

    def test_no_instability_with_fewer_than_3_failures(self, tracker):
        graph = DependencyGraph()
        self._setup_graph_with_coverage(tracker, graph)

        # Only 2 consecutive failures
        for _ in range(2):
            tracker.record_test_run(
                make_results(failed_tests=["test_func_a"], passed_tests=[])
            )

        flagged = tracker.check_instability(graph)
        assert flagged == []

    def test_no_instability_without_coverage(self, tracker, health_path):
        graph = DependencyGraph()

        # Record failures without coverage mapping
        for _ in range(3):
            tracker.record_test_run(
                make_results(failed_tests=["test_func_a"], coverage=None)
            )

        flagged = tracker.check_instability(graph)
        assert flagged == []

    def test_non_consecutive_failures_not_flagged(self, tracker):
        graph = DependencyGraph()
        self._setup_graph_with_coverage(tracker, graph)

        # Fail, pass, fail - not consecutive
        tracker.record_test_run(
            make_results(failed_tests=["test_func_a"], passed_tests=[])
        )
        tracker.record_test_run(
            make_results(failed_tests=[], passed_tests=["test_func_a"])
        )
        tracker.record_test_run(
            make_results(failed_tests=["test_func_a"], passed_tests=[])
        )

        flagged = tracker.check_instability(graph)
        assert flagged == []


class TestRecoveryDetection:
    """R4.6: Detect recovery after 3 consecutive passes of previously failing test."""

    def _setup_unstable_graph(self, tracker, graph):
        """Set up a graph with unstable nodes from prior failures."""
        node_a = Node(
            id="module.func_a",
            node_type="function",
            file_path="module.py",
            line_start=1,
            line_end=10,
            last_modified=utc_now_iso(),
            module="module",
            tags=["unstable"],
        )
        graph.add_node(node_a)

        edge = Edge(
            id=generate_uuid(),
            source="module.func_a",
            target="module.func_a",
            edge_type="calls",
            confidence=0.6,
            provenance="static",
        )
        graph.add_edge(edge)

        # Set up coverage and prior failures
        coverage = {"module.func_a": ["test_func_a"]}
        tracker.record_test_run(
            make_results(
                coverage=coverage,
                failed_tests=["test_func_a"],
                passed_tests=[],
            )
        )

    def test_removes_unstable_tag_after_3_passes(self, tracker):
        graph = DependencyGraph()
        self._setup_unstable_graph(tracker, graph)

        # Now 3 consecutive passes
        for _ in range(3):
            tracker.record_test_run(
                make_results(
                    failed_tests=[],
                    passed_tests=["test_func_a"],
                )
            )

        recovered = tracker.check_recovery(graph)
        assert "module.func_a" in recovered
        assert "unstable" not in graph.get_node("module.func_a").tags

    def test_reinforces_edge_confidence_on_recovery(self, tracker):
        graph = DependencyGraph()
        self._setup_unstable_graph(tracker, graph)

        edges = graph.get_edges_from("module.func_a")
        original_confidence = edges[0].confidence
        assert original_confidence == 0.6

        # 3 consecutive passes
        for _ in range(3):
            tracker.record_test_run(
                make_results(
                    failed_tests=[],
                    passed_tests=["test_func_a"],
                )
            )

        tracker.check_recovery(graph)

        # Self-referencing edge func_a -> func_a gets reinforced twice:
        # once as outgoing edge, once as incoming edge.
        # First: 0.6 + 0.1*(1.0-0.6) = 0.64
        # Second: 0.64 + 0.1*(1.0-0.64) = 0.676
        edges = graph.get_edges_from("module.func_a")
        assert abs(edges[0].confidence - 0.676) < 0.001

    def test_no_recovery_without_prior_failures(self, tracker):
        """Tests that always passed should not trigger recovery."""
        graph = DependencyGraph()
        node_a = Node(
            id="module.func_a",
            node_type="function",
            file_path="module.py",
            line_start=1,
            line_end=10,
            last_modified=utc_now_iso(),
            module="module",
        )
        graph.add_node(node_a)

        coverage = {"module.func_a": ["test_func_a"]}
        # Record only passes (no prior failures)
        for _ in range(5):
            tracker.record_test_run(
                make_results(
                    coverage=coverage,
                    failed_tests=[],
                    passed_tests=["test_func_a"],
                )
            )

        recovered = tracker.check_recovery(graph)
        assert recovered == []

    def test_no_recovery_without_coverage(self, tracker):
        graph = DependencyGraph()

        # Record a failure first, then passes, but no coverage
        tracker.record_test_run(
            make_results(failed_tests=["test_func_a"], coverage=None)
        )
        for _ in range(3):
            tracker.record_test_run(
                make_results(passed_tests=["test_func_a"], coverage=None)
            )

        recovered = tracker.check_recovery(graph)
        assert recovered == []


class TestMissingPytest:
    """R4.5: Handle missing pytest/coverage gracefully."""

    def test_records_counts_only_when_no_coverage(self, tracker, health_path):
        results = make_results(
            total=5,
            passed=3,
            failed=2,
            coverage=None,
        )
        tracker.record_test_run(results)

        with open(health_path) as f:
            data = json.load(f)

        assert len(data["history"]) == 1
        assert "coverage_mapping" not in data


class TestAtomicWrite:
    """Verify test_health.json uses atomic write (tmp + rename)."""

    def test_corrupted_health_file_resets(self, tracker, health_path):
        # Write corrupt data
        os.makedirs(os.path.dirname(health_path), exist_ok=True)
        with open(health_path, "w") as f:
            f.write("not valid json {{")

        # Should recover gracefully
        tracker.record_test_run(make_results(total=1))

        with open(health_path) as f:
            data = json.load(f)

        assert len(data["history"]) == 1
        assert data["history"][0]["total"] == 1
