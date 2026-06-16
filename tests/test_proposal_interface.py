"""Tests for ProposalInterface in goal_engine.py.

Covers: R12.1 (session-start display), R12.2 (/goals list),
R12.3 (/goals accept), R12.4 (/goals dismiss),
R12.5 (invalid id error handling), R12.6 (critical priority notification),
R12.7 (inline contextual suggestion).
"""

import json
import logging
import os

import pytest

from cli_kognisant.goal_engine import LearningLoop, ProposalInterface
from cli_kognisant.models import Goal


@pytest.fixture
def project_root(tmp_path):
    """Create a temporary project root with .kognisant/goals/ directory."""
    goals_dir = tmp_path / ".kognisant" / "goals"
    goals_dir.mkdir(parents=True)
    return str(tmp_path)


@pytest.fixture
def sample_goals():
    """Create a set of sample goals for testing."""
    return [
        Goal(
            id="cv-001",
            goal_type="contract_violation",
            title="Fix argument mismatch in agents.run_subtask_agent",
            target_node="agents.run_subtask_agent",
            target_file="cli_kognisant/agents.py",
            context={"module": "agents", "source_node": "agents.orchestrate"},
            priority_score=7.5,
            validation_status="high_confidence",
            status="active",
            created_at="2025-01-01T00:00:00+00:00",
        ),
        Goal(
            id="cg-002",
            goal_type="coverage_gap",
            title="Add tests for chat.process_slash_commands",
            target_node="chat.process_slash_commands",
            target_file="cli_kognisant/chat.py",
            context={"module": "chat", "affected_functions": ["process_slash_commands"]},
            priority_score=5.2,
            validation_status="partially_validated",
            status="active",
            created_at="2025-01-02T00:00:00+00:00",
        ),
        Goal(
            id="cx-003",
            goal_type="complexity",
            title="Refactor daemon._poll_loop to reduce cyclomatic complexity",
            target_node="daemon._poll_loop",
            target_file="cli_kognisant/daemon.py",
            context={"module": "daemon", "complexity": 18},
            priority_score=9.1,
            validation_status="high_confidence",
            status="active",
            created_at="2025-01-03T00:00:00+00:00",
        ),
        Goal(
            id="da-004",
            goal_type="decay_alert",
            title="Review stale beliefs in observer module",
            target_node="observer.StaticAnalyzer",
            target_file="cli_kognisant/observer.py",
            context={"module": "observer"},
            priority_score=3.0,
            validation_status="requires_user_review",
            status="active",
            created_at="2025-01-04T00:00:00+00:00",
        ),
    ]


@pytest.fixture
def learning_loop(project_root):
    """Create a LearningLoop instance."""
    return LearningLoop(project_root)


@pytest.fixture
def interface(project_root, sample_goals, learning_loop):
    """Create a ProposalInterface with sample goals."""
    return ProposalInterface(
        project_root=project_root,
        goals=sample_goals,
        learning_loop=learning_loop,
    )


class TestDisplaySessionStartGoals:
    """Test session-start goal display (R12.1)."""

    def test_shows_top_3_by_priority(self, interface):
        result = interface.display_session_start_goals()
        # Should contain top 3 goals by priority: cx-003 (9.1), cv-001 (7.5), cg-002 (5.2)
        assert "cx-003" in result
        assert "cv-001" in result
        assert "cg-002" in result
        # da-004 has lowest priority (3.0) so should NOT appear
        assert "da-004" not in result

    def test_shows_goal_type(self, interface):
        result = interface.display_session_start_goals()
        assert "complexity" in result
        assert "contract_violation" in result
        assert "coverage_gap" in result

    def test_shows_priority_score(self, interface):
        result = interface.display_session_start_goals()
        assert "9.1" in result
        assert "7.5" in result
        assert "5.2" in result

    def test_shows_description_truncated(self, interface, sample_goals):
        # Add a goal with a very long title
        long_title = "A" * 200
        sample_goals.append(
            Goal(
                id="lt-005",
                goal_type="stale_artifact",
                title=long_title,
                priority_score=10.0,
                status="active",
                created_at="2025-01-05T00:00:00+00:00",
            )
        )
        iface = ProposalInterface(
            project_root="/tmp", goals=sample_goals
        )
        result = iface.display_session_start_goals()
        # Should not contain the full 200-char title
        assert long_title not in result
        # Should contain the first 120 chars
        assert "A" * 120 in result

    def test_empty_when_no_active_goals(self, project_root):
        iface = ProposalInterface(project_root=project_root, goals=[])
        result = iface.display_session_start_goals()
        assert result == ""

    def test_empty_when_all_goals_non_active(self, project_root):
        goals = [
            Goal(
                id="done-001",
                goal_type="complexity",
                title="Already completed",
                priority_score=5.0,
                status="completed",
                created_at="2025-01-01T00:00:00+00:00",
            ),
        ]
        iface = ProposalInterface(project_root=project_root, goals=goals)
        result = iface.display_session_start_goals()
        assert result == ""


class TestHandleCommandList:
    """Test /goals command listing (R12.2)."""

    def test_list_all_active_goals(self, interface):
        result = interface.handle_command([])
        # Should show all 4 active goals
        assert "cv-001" in result
        assert "cg-002" in result
        assert "cx-003" in result
        assert "da-004" in result

    def test_goals_grouped_by_type(self, interface):
        result = interface.handle_command([])
        # Should contain type headers
        assert "contract_violation" in result
        assert "coverage_gap" in result
        assert "complexity" in result
        assert "decay_alert" in result

    def test_shows_scores_and_descriptions(self, interface):
        result = interface.handle_command([])
        assert "9.1" in result
        assert "7.5" in result
        assert "Refactor daemon._poll_loop" in result

    def test_no_active_goals_message(self, project_root):
        iface = ProposalInterface(project_root=project_root, goals=[])
        result = iface.handle_command([])
        assert "No active goals" in result


class TestHandleCommandAccept:
    """Test /goals accept command (R12.3)."""

    def test_accept_valid_goal(self, interface, sample_goals):
        result = interface.handle_command(["accept", "cv-001"])
        assert "accepted" in result
        assert "cv-001" in result

        # Goal status should be updated
        goal = next(g for g in sample_goals if g.id == "cv-001")
        assert goal.status == "accepted"

    def test_accept_records_positive_signal(self, interface, learning_loop):
        interface.handle_command(["accept", "cv-001"])
        assert len(learning_loop._signals) == 1
        signal = learning_loop._signals[0]
        assert signal.polarity == "positive"
        assert signal.strength == 1.0
        assert signal.source == "accept"
        assert signal.goal_type == "contract_violation"

    def test_accept_invalid_id(self, interface):
        result = interface.handle_command(["accept", "nonexistent"])
        assert "not found" in result
        assert "nonexistent" in result
        # Should list active ids
        assert "cv-001" in result


class TestHandleCommandDismiss:
    """Test /goals dismiss command (R12.4)."""

    def test_dismiss_valid_goal(self, interface, sample_goals):
        result = interface.handle_command(["dismiss", "cg-002"])
        assert "dismissed" in result
        assert "cg-002" in result

        # Goal status should be updated
        goal = next(g for g in sample_goals if g.id == "cg-002")
        assert goal.status == "dismissed"

    def test_dismiss_records_negative_signal(self, interface, learning_loop):
        interface.handle_command(["dismiss", "cg-002"])
        assert len(learning_loop._signals) == 1
        signal = learning_loop._signals[0]
        assert signal.polarity == "negative"
        assert signal.strength == 1.0
        assert signal.source == "dismiss"
        assert signal.goal_type == "coverage_gap"

    def test_dismiss_invalid_id(self, interface):
        result = interface.handle_command(["dismiss", "fake-999"])
        assert "not found" in result
        assert "fake-999" in result


class TestInvalidIdHandling:
    """Test error handling for invalid goal ids (R12.5)."""

    def test_error_message_includes_invalid_id(self, interface):
        result = interface.handle_command(["accept", "bad-id"])
        assert "bad-id" in result
        assert "not found" in result

    def test_error_lists_active_ids(self, interface):
        result = interface.handle_command(["accept", "bad-id"])
        assert "cv-001" in result
        assert "cg-002" in result
        assert "cx-003" in result
        assert "da-004" in result

    def test_error_when_no_active_goals(self, project_root):
        iface = ProposalInterface(project_root=project_root, goals=[])
        result = iface.handle_command(["accept", "bad-id"])
        assert "not found" in result
        assert "No active goals" in result

    def test_cannot_accept_already_dismissed_goal(self, interface):
        # First dismiss
        interface.handle_command(["dismiss", "cv-001"])
        # Then try to accept same goal
        result = interface.handle_command(["accept", "cv-001"])
        assert "not found" in result


class TestCriticalPriorityNotification:
    """Test critical priority notification (R12.6)."""

    def test_emits_notification_for_score_above_8(self, interface):
        result = interface.check_critical_notifications()
        # cx-003 has score 9.1 > 8.0
        assert result is not None
        assert "cx-003" in result
        assert "CRITICAL" in result

    def test_emits_only_once(self, interface):
        # First call triggers
        result1 = interface.check_critical_notifications()
        assert result1 is not None
        assert "cx-003" in result1

        # Second call should not re-emit
        result2 = interface.check_critical_notifications()
        assert result2 is None

    def test_logs_critical_notification(self, interface, caplog):
        with caplog.at_level(logging.WARNING):
            interface.check_critical_notifications()
        assert any("CRITICAL PRIORITY" in record.message for record in caplog.records)
        assert any("cx-003" in record.message for record in caplog.records)

    def test_no_notification_when_all_below_threshold(self, project_root):
        goals = [
            Goal(
                id="low-001",
                goal_type="decay_alert",
                title="Low priority goal",
                priority_score=4.0,
                status="active",
                created_at="2025-01-01T00:00:00+00:00",
            ),
        ]
        iface = ProposalInterface(project_root=project_root, goals=goals)
        result = iface.check_critical_notifications()
        assert result is None


class TestInlineContextualSuggestion:
    """Test inline contextual suggestion (R12.7)."""

    def test_suggests_for_matching_file(self, interface):
        result = interface.get_inline_suggestion("cli_kognisant/agents.py")
        assert result is not None
        assert "cv-001" in result

    def test_selects_highest_priority(self, project_root):
        goals = [
            Goal(
                id="low-001",
                goal_type="decay_alert",
                title="Low priority",
                target_file="cli_kognisant/agents.py",
                priority_score=2.0,
                status="active",
                created_at="2025-01-01T00:00:00+00:00",
            ),
            Goal(
                id="high-002",
                goal_type="complexity",
                title="High priority",
                target_file="cli_kognisant/agents.py",
                priority_score=8.0,
                status="active",
                created_at="2025-01-02T00:00:00+00:00",
            ),
        ]
        iface = ProposalInterface(project_root=project_root, goals=goals)
        result = iface.get_inline_suggestion("cli_kognisant/agents.py")
        assert result is not None
        assert "high-002" in result
        assert "low-001" not in result

    def test_no_suggestion_for_unrelated_file(self, interface):
        result = interface.get_inline_suggestion("cli_kognisant/config.py")
        assert result is None

    def test_matches_by_node_module(self, interface):
        # Goal cv-001 targets node "agents.run_subtask_agent" — should match agents.py
        result = interface.get_inline_suggestion("agents.py")
        assert result is not None
        assert "cv-001" in result

    def test_no_suggestion_when_no_goals(self, project_root):
        iface = ProposalInterface(project_root=project_root, goals=[])
        result = iface.get_inline_suggestion("cli_kognisant/agents.py")
        assert result is None


class TestPersistence:
    """Test goal persistence on accept/dismiss."""

    def test_accept_persists_to_disk(self, project_root, sample_goals):
        iface = ProposalInterface(
            project_root=project_root,
            goals=sample_goals,
        )
        iface.handle_command(["accept", "cv-001"])

        path = os.path.join(project_root, ".kognisant", "goals", "active.json")
        assert os.path.exists(path)
        with open(path, "r") as f:
            data = json.load(f)
        # Find cv-001 in persisted data
        cv_goal = next((g for g in data if g["id"] == "cv-001"), None)
        assert cv_goal is not None
        assert cv_goal["status"] == "accepted"

    def test_loads_from_disk_when_no_goals_provided(self, project_root):
        # Write goals to disk
        goals_data = [
            {
                "id": "disk-001",
                "goal_type": "complexity",
                "title": "Goal from disk",
                "priority_score": 6.0,
                "status": "active",
                "created_at": "2025-01-01T00:00:00+00:00",
                "target_node": None,
                "target_file": None,
                "context": {},
                "validation_status": "requires_user_review",
                "resolved_at": None,
                "causal_chain": [],
                "snapshot_path": None,
            }
        ]
        path = os.path.join(project_root, ".kognisant", "goals", "active.json")
        with open(path, "w") as f:
            json.dump(goals_data, f)

        iface = ProposalInterface(project_root=project_root)
        result = iface.display_session_start_goals()
        assert "disk-001" in result

    def test_handles_missing_active_json(self, tmp_path):
        project_root = str(tmp_path)
        iface = ProposalInterface(project_root=project_root)
        result = iface.display_session_start_goals()
        assert result == ""
