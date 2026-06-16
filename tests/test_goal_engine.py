"""Tests for GoalGenerator in goal_engine.py.

Covers: R10.1 (contract_violation), R10.2 (coverage_gap),
R10.3 (decay_alert), R10.8 (deduplication).
"""

import pytest

from cli_kognisant.goal_engine import GoalGenerator, LearningLoop
from cli_kognisant.models import EpistemicGap, Goal, Node, Edge, utc_now_iso
from cli_kognisant.world_model import (
    BeliefSystem,
    ContractRegistry,
    DependencyGraph,
    EpistemicGapTracker,
)


class FakeStore:
    """Minimal fake store for GoalGenerator tests."""
    pass


@pytest.fixture
def graph():
    return DependencyGraph()


@pytest.fixture
def contracts():
    return ContractRegistry()


@pytest.fixture
def gaps():
    return EpistemicGapTracker()


@pytest.fixture
def beliefs():
    return BeliefSystem()


@pytest.fixture
def store():
    return FakeStore()


@pytest.fixture
def generator(graph, contracts, gaps, beliefs, store):
    return GoalGenerator(graph, contracts, gaps, beliefs, store)


class TestContractViolationStrategy:
    """Test _check_contract_violations creates goals from pending violations."""

    def test_creates_goal_from_violation_event(self, generator, contracts):
        """R10.1: Contract violation event produces a goal."""
        # Simulate a pending violation event
        contracts._pending_violations.append(
            ("module_a.func_x", "module_b.func_y", "contract-uuid-123")
        )

        goals = generator.generate_goals()

        assert len(goals) == 1
        goal = goals[0]
        assert goal.goal_type == "contract_violation"
        assert goal.status == "active"
        assert goal.target_node == "module_b.func_y"
        assert goal.context["source_node"] == "module_a.func_x"
        assert goal.context["contract_id"] == "contract-uuid-123"

    def test_goal_id_format(self, generator, contracts):
        """Goal id uses cv prefix with counter."""
        contracts._pending_violations.append(
            ("a.f1", "b.f2", "c1")
        )

        goals = generator.generate_goals()

        assert goals[0].id == "cv-001"

    def test_multiple_violations_produce_multiple_goals(self, generator, contracts):
        """Multiple violation events produce distinct goals."""
        contracts._pending_violations.append(("a.f1", "b.f2", "c1"))
        contracts._pending_violations.append(("a.f3", "c.f4", "c2"))

        goals = generator.generate_goals()

        assert len(goals) == 2
        assert goals[0].id == "cv-001"
        assert goals[1].id == "cv-002"
        assert goals[0].target_node == "b.f2"
        assert goals[1].target_node == "c.f4"

    def test_clears_pending_violations_after_poll(self, generator, contracts):
        """Pending violations are consumed by generate_goals."""
        contracts._pending_violations.append(("a.f1", "b.f2", "c1"))

        generator.generate_goals()
        # Second call should produce no goals since pending was cleared
        goals = generator.generate_goals()

        assert len(goals) == 0


class TestCoverageGapStrategy:
    """Test _check_coverage_gaps creates goals for modules with >3 untested branches."""

    def test_creates_goal_when_module_has_more_than_3_gaps(self, generator, gaps):
        """R10.2: Module with >3 untested_branch gaps produces a goal."""
        # Create 4 untested_branch gaps for the same module
        for i in range(4):
            gap = EpistemicGap(
                id=f"gap-{i}",
                gap_type="untested_branch",
                node_id=f"mymodule.Class.func_{i}",
                description=f"Untested branch in func_{i}",
                discovered_at=utc_now_iso(),
            )
            gaps.record_gap(gap)

        goals = generator.generate_goals()

        assert len(goals) == 1
        goal = goals[0]
        assert goal.goal_type == "coverage_gap"
        assert goal.target_node == "mymodule"
        assert goal.context["gap_count"] == 4
        assert len(goal.context["affected_functions"]) == 4

    def test_no_goal_when_3_or_fewer_gaps(self, generator, gaps):
        """No goal created when module has exactly 3 untested_branch gaps."""
        for i in range(3):
            gap = EpistemicGap(
                id=f"gap-{i}",
                gap_type="untested_branch",
                node_id=f"mymodule.func_{i}",
                description=f"Untested branch in func_{i}",
                discovered_at=utc_now_iso(),
            )
            gaps.record_gap(gap)

        goals = generator.generate_goals()

        assert len(goals) == 0

    def test_ignores_non_untested_branch_gaps(self, generator, gaps):
        """Only untested_branch gaps count toward the threshold."""
        # Add 4 gaps, but only 2 are untested_branch
        for i in range(2):
            gaps.record_gap(EpistemicGap(
                id=f"ub-{i}",
                gap_type="untested_branch",
                node_id=f"mod.func_{i}",
                description="untested",
                discovered_at=utc_now_iso(),
            ))
        for i in range(2):
            gaps.record_gap(EpistemicGap(
                id=f"uf-{i}",
                gap_type="unexercised_function",
                node_id=f"mod.func_{i+10}",
                description="unexercised",
                discovered_at=utc_now_iso(),
            ))

        goals = generator.generate_goals()

        assert len(goals) == 0

    def test_coverage_gap_goal_id_format(self, generator, gaps):
        """Coverage gap goals use 'cg' prefix."""
        for i in range(5):
            gaps.record_gap(EpistemicGap(
                id=f"gap-{i}",
                gap_type="untested_branch",
                node_id=f"testmod.func_{i}",
                description="untested",
                discovered_at=utc_now_iso(),
            ))

        goals = generator.generate_goals()

        assert goals[0].id == "cg-001"


class TestDecayAlertStrategy:
    """Test _check_decay_alerts creates goals when >5 beliefs pruned per module."""

    def test_creates_goal_when_more_than_5_pruned(self, generator):
        """R10.3: >5 beliefs pruned from same module triggers decay_alert."""
        decay_summary = {
            "beliefs_pruned": [
                "mymod.cls.f1", "mymod.cls.f2", "mymod.cls.f3",
                "mymod.cls.f4", "mymod.cls.f5", "mymod.cls.f6",
            ],
        }

        goals = generator.generate_goals(decay_summary=decay_summary)

        assert len(goals) == 1
        goal = goals[0]
        assert goal.goal_type == "decay_alert"
        assert goal.target_node == "mymod"
        assert goal.context["pruned_count"] == 6

    def test_no_goal_when_5_or_fewer_pruned(self, generator):
        """No goal when exactly 5 beliefs pruned from module."""
        decay_summary = {
            "beliefs_pruned": [
                "mod.f1", "mod.f2", "mod.f3", "mod.f4", "mod.f5",
            ],
        }

        goals = generator.generate_goals(decay_summary=decay_summary)

        assert len(goals) == 0

    def test_no_goal_when_no_decay_summary(self, generator):
        """No decay_alert goals when no decay_summary provided."""
        goals = generator.generate_goals(decay_summary=None)

        # Should have no decay_alert goals (may have others from empty strategies)
        decay_goals = [g for g in goals if g.goal_type == "decay_alert"]
        assert len(decay_goals) == 0

    def test_multiple_modules_produce_separate_goals(self, generator):
        """Different modules each produce their own goal."""
        decay_summary = {
            "beliefs_pruned": [
                "mod_a.f1", "mod_a.f2", "mod_a.f3",
                "mod_a.f4", "mod_a.f5", "mod_a.f6",
                "mod_b.f1", "mod_b.f2", "mod_b.f3",
                "mod_b.f4", "mod_b.f5", "mod_b.f6",
            ],
        }

        goals = generator.generate_goals(decay_summary=decay_summary)

        assert len(goals) == 2
        goal_targets = {g.target_node for g in goals}
        assert "mod_a" in goal_targets
        assert "mod_b" in goal_targets


class TestDeduplication:
    """Test that duplicate goals are not created (R10.8)."""

    def test_same_contract_violation_target_not_duplicated(self, generator, contracts):
        """Duplicate contract_violation for same target_node is skipped."""
        contracts._pending_violations.append(("a.f1", "b.f2", "c1"))
        generator.generate_goals()

        # Try to create another violation for the same target
        contracts._pending_violations.append(("a.f3", "b.f2", "c2"))
        goals = generator.generate_goals()

        assert len(goals) == 0

    def test_different_targets_are_not_duplicates(self, generator, contracts):
        """Different target_nodes produce separate goals."""
        contracts._pending_violations.append(("a.f1", "b.f2", "c1"))
        first_goals = generator.generate_goals()

        contracts._pending_violations.append(("a.f1", "c.f3", "c2"))
        second_goals = generator.generate_goals()

        assert len(first_goals) == 1
        assert len(second_goals) == 1

    def test_same_type_different_target_not_duplicate(self, generator, gaps):
        """Coverage gaps for different modules are distinct."""
        # Create gaps for module_a
        for i in range(5):
            gaps.record_gap(EpistemicGap(
                id=f"gap-a-{i}",
                gap_type="untested_branch",
                node_id=f"module_a.func_{i}",
                description="untested",
                discovered_at=utc_now_iso(),
            ))
        # Create gaps for module_b
        for i in range(5):
            gaps.record_gap(EpistemicGap(
                id=f"gap-b-{i}",
                gap_type="untested_branch",
                node_id=f"module_b.func_{i}",
                description="untested",
                discovered_at=utc_now_iso(),
            ))

        goals = generator.generate_goals()

        assert len(goals) == 2

    def test_dedup_across_generate_calls(self, generator, contracts):
        """Deduplication works across multiple generate_goals calls."""
        contracts._pending_violations.append(("a.f1", "target.node", "c1"))
        generator.generate_goals()

        # Second call — same target should be deduped
        contracts._pending_violations.append(("x.y", "target.node", "c2"))
        goals = generator.generate_goals()

        assert len(goals) == 0


class TestSelfValidate:
    """Test self_validate full implementation (R19)."""

    def test_all_three_sources_agree_high_confidence(self, graph, generator):
        """When static, dynamic, and test evidence all present -> high_confidence (R19.2)."""
        # Create a target node
        target = Node(
            id="mymod.MyClass.my_func",
            node_type="function",
            file_path="cli_kognisant/mymod.py",
            line_start=10,
            line_end=20,
            last_modified=utc_now_iso(),
            tags=["unstable"],  # test evidence via tag
            module="mymod",
        )
        graph.add_node(target)

        # Add another node for edge targets
        other = Node(
            id="mymod.other_func",
            node_type="function",
            file_path="cli_kognisant/mymod.py",
            line_start=30,
            line_end=40,
            last_modified=utc_now_iso(),
            module="mymod",
        )
        graph.add_node(other)

        # Static evidence
        static_edge = Edge(
            id="e1",
            source="mymod.MyClass.my_func",
            target="mymod.other_func",
            edge_type="calls",
            confidence=1.0,
            provenance="static",
        )
        graph.add_edge(static_edge)

        # Dynamic evidence
        dynamic_edge = Edge(
            id="e2",
            source="mymod.other_func",
            target="mymod.MyClass.my_func",
            edge_type="calls",
            confidence=0.8,
            provenance="dynamic",
        )
        graph.add_edge(dynamic_edge)

        goal = Goal(
            id="cv-001",
            goal_type="contract_violation",
            title="Test goal",
            target_node="mymod.MyClass.my_func",
            priority_score=5.0,
        )

        result = generator.self_validate(goal)

        assert result == "high_confidence"
        assert goal.validation_status == "high_confidence"
        assert goal.priority_score == 5.0  # no reduction

    def test_two_sources_agree_partially_validated(self, graph, generator):
        """When exactly two sources agree -> partially_validated, 15% reduction (R19.3)."""
        target = Node(
            id="mymod.func_a",
            node_type="function",
            file_path="cli_kognisant/mymod.py",
            line_start=10,
            line_end=20,
            last_modified=utc_now_iso(),
            tags=[],  # no test evidence via tag
            module="mymod",
        )
        graph.add_node(target)

        other = Node(
            id="mymod.func_b",
            node_type="function",
            file_path="cli_kognisant/mymod.py",  # not a test file
            line_start=30,
            line_end=40,
            last_modified=utc_now_iso(),
            module="mymod",
        )
        graph.add_node(other)

        # Static evidence: present
        static_edge = Edge(
            id="e1",
            source="mymod.func_a",
            target="mymod.func_b",
            edge_type="calls",
            confidence=1.0,
            provenance="static",
        )
        graph.add_edge(static_edge)

        # Dynamic evidence: present
        dynamic_edge = Edge(
            id="e2",
            source="mymod.func_b",
            target="mymod.func_a",
            edge_type="calls",
            confidence=0.8,
            provenance="dynamic",
        )
        graph.add_edge(dynamic_edge)

        # Test evidence: absent (no unstable tag, no test file connections)

        goal = Goal(
            id="cv-002",
            goal_type="contract_violation",
            title="Test goal",
            target_node="mymod.func_a",
            priority_score=10.0,
        )

        result = generator.self_validate(goal)

        assert result == "partially_validated"
        assert goal.validation_status == "partially_validated"
        assert goal.priority_score == pytest.approx(10.0 * 0.85)

    def test_sources_disagree_requires_user_review(self, graph, generator):
        """When sources disagree -> requires_user_review, 30% reduction (R19.4)."""
        target = Node(
            id="mymod.func_c",
            node_type="function",
            file_path="cli_kognisant/mymod.py",
            line_start=10,
            line_end=20,
            last_modified=utc_now_iso(),
            tags=[],
            module="mymod",
        )
        graph.add_node(target)

        other = Node(
            id="mymod.func_d",
            node_type="function",
            file_path="cli_kognisant/mymod.py",
            line_start=30,
            line_end=40,
            last_modified=utc_now_iso(),
            module="mymod",
        )
        graph.add_node(other)

        # Static evidence: present
        static_edge = Edge(
            id="e1",
            source="mymod.func_c",
            target="mymod.func_d",
            edge_type="calls",
            confidence=1.0,
            provenance="static",
        )
        graph.add_edge(static_edge)

        # Dynamic evidence: absent (no dynamic edges)
        # Test evidence: absent (no unstable tag, no test connections)

        goal = Goal(
            id="cv-003",
            goal_type="contract_violation",
            title="Test goal",
            target_node="mymod.func_c",
            priority_score=10.0,
        )

        result = generator.self_validate(goal)

        assert result == "requires_user_review"
        assert goal.validation_status == "requires_user_review"
        assert goal.priority_score == pytest.approx(10.0 * 0.70)

    def test_fewer_than_two_sources_partially_validated(self, generator):
        """When node doesn't exist (no sources) -> partially_validated, 15% reduction (R19.6)."""
        goal = Goal(
            id="cv-004",
            goal_type="contract_violation",
            title="Test goal",
            target_node="nonexistent.func",
            priority_score=8.0,
        )

        result = generator.self_validate(goal)

        assert result == "partially_validated"
        assert goal.validation_status == "partially_validated"
        assert goal.priority_score == pytest.approx(8.0 * 0.85)

    def test_no_target_node_partially_validated(self, generator):
        """When goal has no target_node -> partially_validated, 15% reduction (R19.6)."""
        goal = Goal(
            id="da-001",
            goal_type="decay_alert",
            title="Test goal",
            target_node=None,
            priority_score=6.0,
        )

        result = generator.self_validate(goal)

        assert result == "partially_validated"
        assert goal.validation_status == "partially_validated"
        assert goal.priority_score == pytest.approx(6.0 * 0.85)

    def test_test_evidence_from_unstable_tag(self, graph, generator):
        """Test evidence detected from 'unstable' tag on node."""
        target = Node(
            id="mymod.unstable_func",
            node_type="function",
            file_path="cli_kognisant/mymod.py",
            line_start=10,
            line_end=20,
            last_modified=utc_now_iso(),
            tags=["unstable"],
            module="mymod",
        )
        graph.add_node(target)

        other = Node(
            id="mymod.caller",
            node_type="function",
            file_path="cli_kognisant/mymod.py",
            line_start=30,
            line_end=40,
            last_modified=utc_now_iso(),
            module="mymod",
        )
        graph.add_node(other)

        # Static evidence
        graph.add_edge(Edge(
            id="e1", source="mymod.unstable_func", target="mymod.caller",
            edge_type="calls", confidence=1.0, provenance="static",
        ))

        # Dynamic evidence
        graph.add_edge(Edge(
            id="e2", source="mymod.caller", target="mymod.unstable_func",
            edge_type="calls", confidence=0.8, provenance="dynamic",
        ))

        goal = Goal(
            id="cv-005",
            goal_type="contract_violation",
            title="Test goal",
            target_node="mymod.unstable_func",
            priority_score=5.0,
        )

        result = generator.self_validate(goal)
        assert result == "high_confidence"

    def test_test_evidence_from_test_file_connection(self, graph, generator):
        """Test evidence detected from edges to test file nodes."""
        target = Node(
            id="mymod.tested_func",
            node_type="function",
            file_path="cli_kognisant/mymod.py",
            line_start=10,
            line_end=20,
            last_modified=utc_now_iso(),
            tags=[],
            module="mymod",
        )
        graph.add_node(target)

        test_node = Node(
            id="tests.test_mymod.test_func",
            node_type="function",
            file_path="tests/test_mymod.py",
            line_start=5,
            line_end=15,
            last_modified=utc_now_iso(),
            module="tests.test_mymod",
        )
        graph.add_node(test_node)

        # Static evidence
        graph.add_edge(Edge(
            id="e1", source="tests.test_mymod.test_func",
            target="mymod.tested_func",
            edge_type="calls", confidence=1.0, provenance="static",
        ))

        # Dynamic evidence
        graph.add_edge(Edge(
            id="e2", source="tests.test_mymod.test_func",
            target="mymod.tested_func",
            edge_type="calls", confidence=0.8, provenance="dynamic",
        ))

        goal = Goal(
            id="cv-006",
            goal_type="contract_violation",
            title="Test goal",
            target_node="mymod.tested_func",
            priority_score=5.0,
        )

        result = generator.self_validate(goal)
        # Static: True (edge e1), Dynamic: True (edge e2), Test: True (test file connection)
        assert result == "high_confidence"


class TestRecordValidationSuccess:
    """Test record_validation_success integration with LearningLoop (R19.5)."""

    def test_records_positive_signal_with_learning_loop(self, tmp_path, generator):
        """Records strong positive signal when learning_loop is set."""
        loop = LearningLoop(str(tmp_path))
        generator.learning_loop = loop

        goal = Goal(
            id="cv-001",
            goal_type="contract_violation",
            title="Test goal",
            target_node="agents.MyClass.my_func",
            priority_score=5.0,
        )

        generator.record_validation_success(goal)

        assert len(loop._signals) == 1
        signal = loop._signals[0]
        assert signal.goal_type == "contract_violation"
        assert signal.module == "agents"
        assert signal.polarity == "positive"
        assert signal.strength == 1.0
        assert signal.source == "self_validate"

    def test_no_op_without_learning_loop(self, generator):
        """Does nothing when learning_loop is not set."""
        goal = Goal(
            id="cv-001",
            goal_type="contract_violation",
            title="Test goal",
            target_node="agents.func",
        )

        # Should not raise
        generator.record_validation_success(goal)

    def test_extracts_module_from_target_node(self, tmp_path, generator):
        """Extracts module name from first part of target_node id."""
        loop = LearningLoop(str(tmp_path))
        generator.learning_loop = loop

        goal = Goal(
            id="cg-001",
            goal_type="coverage_gap",
            title="Test",
            target_node="world_model.DependencyGraph.query_reachable",
        )

        generator.record_validation_success(goal)

        assert loop._signals[0].module == "world_model"

    def test_handles_no_target_node(self, tmp_path, generator):
        """Empty module when goal has no target_node."""
        loop = LearningLoop(str(tmp_path))
        generator.learning_loop = loop

        goal = Goal(
            id="da-001",
            goal_type="decay_alert",
            title="Test",
            target_node=None,
        )

        generator.record_validation_success(goal)

        assert loop._signals[0].module == ""


class TestGoalCreation:
    """Test goal record structure (R10.7)."""

    def test_goal_has_required_fields(self, generator, contracts):
        """Every goal has: id, goal_type, creation timestamp, target, context, status."""
        contracts._pending_violations.append(("a.f1", "b.f2", "c1"))

        goals = generator.generate_goals()
        goal = goals[0]

        assert goal.id  # non-empty
        assert goal.goal_type == "contract_violation"
        assert goal.created_at  # non-empty ISO timestamp
        assert goal.target_node == "b.f2"
        assert goal.context  # non-empty dict
        assert goal.status == "active"

    def test_title_truncated_to_120_chars(self, generator, contracts):
        """Title is truncated to 120 characters max."""
        # Create a violation with a very long target name
        long_name = "a" * 200
        contracts._pending_violations.append((long_name, long_name, "c1"))

        goals = generator.generate_goals()

        assert len(goals[0].title) <= 120

    def test_goal_counter_increments(self, generator, contracts):
        """Goal counter increments across multiple create calls."""
        contracts._pending_violations.append(("a.f1", "target1", "c1"))
        contracts._pending_violations.append(("a.f2", "target2", "c2"))

        goals = generator.generate_goals()

        assert goals[0].id == "cv-001"
        assert goals[1].id == "cv-002"

        # Next batch continues counting
        contracts._pending_violations.append(("a.f3", "target3", "c3"))
        goals2 = generator.generate_goals()

        assert goals2[0].id == "cv-003"
