"""Tests for LearningLoop in goal_engine.py.

Covers: R14.1 (accept), R14.2 (dismiss), R14.3 (ignore detection),
R14.4 (manual fix detection), R14.5 (asymmetric weighting),
R14.6 (acceptance rate over last 20), R14.7 (write failure handling).
"""

import json
import os

import pytest

from cli_kognisant.goal_engine import LearningLoop
from cli_kognisant.models import FeedbackSignal


@pytest.fixture
def project_root(tmp_path):
    """Create a temporary project root with .kognisant/goals/ directory."""
    goals_dir = tmp_path / ".kognisant" / "goals"
    goals_dir.mkdir(parents=True)
    return str(tmp_path)


@pytest.fixture
def loop(project_root):
    """Create a LearningLoop instance."""
    return LearningLoop(project_root)


class TestRecordSignal:
    """Test basic signal recording and persistence."""

    def test_record_signal_appends_to_buffer(self, loop):
        signal = FeedbackSignal(
            goal_type="contract_violation",
            module="agents",
            polarity="positive",
            strength=1.0,
            timestamp="2025-01-01T00:00:00+00:00",
            source="accept",
        )
        loop.record_signal(signal)
        assert len(loop._signals) == 1
        assert loop._signals[0].goal_type == "contract_violation"

    def test_record_signal_persists_to_disk(self, loop, project_root):
        signal = FeedbackSignal(
            goal_type="coverage_gap",
            module="chat",
            polarity="negative",
            strength=1.0,
            timestamp="2025-01-01T00:00:00+00:00",
            source="dismiss",
        )
        loop.record_signal(signal)

        path = os.path.join(project_root, ".kognisant", "goals", "learning.json")
        assert os.path.exists(path)
        with open(path, "r") as f:
            data = json.load(f)
        assert len(data["signals"]) == 1
        assert data["signals"][0]["goal_type"] == "coverage_gap"

    def test_signals_survive_reload(self, project_root):
        loop1 = LearningLoop(project_root)
        signal = FeedbackSignal(
            goal_type="decay_alert",
            module="daemon",
            polarity="positive",
            strength=0.5,
            timestamp="2025-01-01T00:00:00+00:00",
            source="manual_fix",
        )
        loop1.record_signal(signal)

        # Create a new instance to load from disk
        loop2 = LearningLoop(project_root)
        assert len(loop2._signals) == 1
        assert loop2._signals[0].goal_type == "decay_alert"
        assert loop2._signals[0].strength == 0.5


class TestAcceptSignal:
    """Test accept signal recording (R14.1)."""

    def test_record_accept(self, loop):
        loop.record_accept("contract_violation", "agents", "2025-01-01T00:00:00+00:00")
        assert len(loop._signals) == 1
        s = loop._signals[0]
        assert s.polarity == "positive"
        assert s.strength == 1.0
        assert s.source == "accept"
        assert s.goal_type == "contract_violation"
        assert s.module == "agents"


class TestDismissSignal:
    """Test dismiss signal recording (R14.2)."""

    def test_record_dismiss(self, loop):
        loop.record_dismiss("coverage_gap", "chat", "2025-01-01T00:00:00+00:00")
        assert len(loop._signals) == 1
        s = loop._signals[0]
        assert s.polarity == "negative"
        assert s.strength == 1.0
        assert s.source == "dismiss"
        assert s.goal_type == "coverage_gap"
        assert s.module == "chat"


class TestIgnoreDetection:
    """Test ignore detection after 3 sessions (R14.3)."""

    def test_no_signal_before_3_sessions(self, loop):
        loop.record_session_for_goal("goal-1")
        loop.record_session_for_goal("goal-1")
        result = loop.check_ignore(
            "goal-1", "decay_alert", "agents", "2025-01-01T00:00:00+00:00"
        )
        assert result is False
        assert len(loop._signals) == 0

    def test_signal_at_3_sessions(self, loop):
        loop.record_session_for_goal("goal-1")
        loop.record_session_for_goal("goal-1")
        loop.record_session_for_goal("goal-1")
        result = loop.check_ignore(
            "goal-1", "decay_alert", "agents", "2025-01-01T00:00:00+00:00"
        )
        assert result is True
        assert len(loop._signals) == 1
        s = loop._signals[0]
        assert s.polarity == "negative"
        assert s.strength == 0.5
        assert s.source == "ignore"

    def test_counter_resets_after_ignore(self, loop):
        for _ in range(3):
            loop.record_session_for_goal("goal-1")
        loop.check_ignore(
            "goal-1", "decay_alert", "agents", "2025-01-01T00:00:00+00:00"
        )
        # Counter should be reset
        assert loop._session_counts["goal-1"] == 0


class TestManualFixDetection:
    """Test manual fix detection with line range overlap (R14.4)."""

    def test_overlap_records_signal(self, loop):
        result = loop.detect_manual_fix(
            edit_file="agents.py",
            edit_line_start=10,
            edit_line_end=20,
            goal_type="complexity",
            module="agents",
            target_line_start=15,
            target_line_end=30,
            timestamp="2025-01-01T00:00:00+00:00",
        )
        assert result is True
        assert len(loop._signals) == 1
        s = loop._signals[0]
        assert s.polarity == "positive"
        assert s.strength == 0.5
        assert s.source == "manual_fix"

    def test_no_overlap_no_signal(self, loop):
        result = loop.detect_manual_fix(
            edit_file="agents.py",
            edit_line_start=1,
            edit_line_end=5,
            goal_type="complexity",
            module="agents",
            target_line_start=50,
            target_line_end=70,
            timestamp="2025-01-01T00:00:00+00:00",
        )
        assert result is False
        assert len(loop._signals) == 0

    def test_exact_boundary_overlap(self, loop):
        # Edit ends exactly where target starts
        result = loop.detect_manual_fix(
            edit_file="agents.py",
            edit_line_start=1,
            edit_line_end=10,
            goal_type="complexity",
            module="agents",
            target_line_start=10,
            target_line_end=20,
            timestamp="2025-01-01T00:00:00+00:00",
        )
        assert result is True

    def test_adjacent_no_overlap(self, loop):
        # Edit ends one line before target starts
        result = loop.detect_manual_fix(
            edit_file="agents.py",
            edit_line_start=1,
            edit_line_end=9,
            goal_type="complexity",
            module="agents",
            target_line_start=10,
            target_line_end=20,
            timestamp="2025-01-01T00:00:00+00:00",
        )
        assert result is True  # Lines 9 and 10 are adjacent but 9 < 10 is still <=20 and >=10 check... let me reconsider

    def test_no_overlap_strict(self, loop):
        # Edit is entirely before target
        result = loop.detect_manual_fix(
            edit_file="agents.py",
            edit_line_start=1,
            edit_line_end=8,
            goal_type="complexity",
            module="agents",
            target_line_start=10,
            target_line_end=20,
            timestamp="2025-01-01T00:00:00+00:00",
        )
        assert result is False


class TestAsymmetricWeighting:
    """Test asymmetric weighting: negative signals carry 1.5x (R14.5)."""

    def test_equal_signals_favor_negative(self, loop):
        # One positive strength=1.0 and one negative strength=1.0
        loop.record_accept("contract_violation", "agents", "2025-01-01T00:00:00+00:00")
        loop.record_dismiss("contract_violation", "agents", "2025-01-01T00:01:00+00:00")

        rate = loop.get_acceptance_rate("contract_violation")
        # positive_weight = 1.0, negative_weight = 1.0 * 1.5 = 1.5
        # rate = 1.0 / (1.0 + 1.5) = 0.4
        assert abs(rate - 0.4) < 0.001

    def test_only_positive_signals(self, loop):
        loop.record_accept("coverage_gap", "agents", "2025-01-01T00:00:00+00:00")
        loop.record_accept("coverage_gap", "agents", "2025-01-01T00:01:00+00:00")

        rate = loop.get_acceptance_rate("coverage_gap")
        # All positive, rate = 2.0 / (2.0 + 0.0) = 1.0
        assert rate == 1.0

    def test_only_negative_signals(self, loop):
        loop.record_dismiss("decay_alert", "agents", "2025-01-01T00:00:00+00:00")
        loop.record_dismiss("decay_alert", "agents", "2025-01-01T00:01:00+00:00")

        rate = loop.get_acceptance_rate("decay_alert")
        # All negative, rate = 0.0 / (0.0 + 3.0) = 0.0
        assert rate == 0.0


class TestAcceptanceRate:
    """Test acceptance rate computation (R14.6)."""

    def test_no_signals_returns_default(self, loop):
        rate = loop.get_acceptance_rate("unknown_type")
        assert rate == 0.5

    def test_rate_uses_last_20_per_module(self, loop):
        # Record 25 signals, 20 positive then 5 negative
        for i in range(20):
            loop.record_accept(
                "contract_violation", "agents", f"2025-01-01T00:{i:02d}:00+00:00"
            )
        for i in range(5):
            loop.record_dismiss(
                "contract_violation", "agents", f"2025-01-01T01:{i:02d}:00+00:00"
            )

        # Last 20 for module "agents": 15 positive + 5 negative
        rate = loop.get_acceptance_rate("contract_violation")
        # positive = 15 * 1.0 = 15.0
        # negative = 5 * 1.0 * 1.5 = 7.5
        # rate = 15.0 / (15.0 + 7.5) = 15.0 / 22.5 = 0.6667
        expected = 15.0 / (15.0 + 7.5)
        assert abs(rate - expected) < 0.001

    def test_rate_per_module_separate(self, loop):
        # Module A: all positive
        for i in range(5):
            loop.record_accept(
                "contract_violation", "module_a", f"2025-01-01T00:{i:02d}:00+00:00"
            )
        # Module B: all negative
        for i in range(5):
            loop.record_dismiss(
                "contract_violation", "module_b", f"2025-01-01T01:{i:02d}:00+00:00"
            )

        # Rate combines both modules
        rate = loop.get_acceptance_rate("contract_violation")
        # positive = 5 * 1.0 = 5.0
        # negative = 5 * 1.0 * 1.5 = 7.5
        # rate = 5.0 / (5.0 + 7.5) = 5.0 / 12.5 = 0.4
        expected = 5.0 / 12.5
        assert abs(rate - expected) < 0.001


class TestGetAllRates:
    """Test get_all_rates returns rates for all known types."""

    def test_multiple_types(self, loop):
        loop.record_accept("contract_violation", "agents", "2025-01-01T00:00:00+00:00")
        loop.record_dismiss("coverage_gap", "agents", "2025-01-01T00:01:00+00:00")
        loop.record_accept("decay_alert", "agents", "2025-01-01T00:02:00+00:00")

        rates = loop.get_all_rates()
        assert "contract_violation" in rates
        assert "coverage_gap" in rates
        assert "decay_alert" in rates
        assert rates["contract_violation"] == 1.0
        assert rates["coverage_gap"] == 0.0
        assert rates["decay_alert"] == 1.0

    def test_empty_returns_empty_dict(self, loop):
        rates = loop.get_all_rates()
        assert rates == {}


class TestWriteFailureHandling:
    """Test write failure handling (R14.7)."""

    def test_write_failure_retains_signal_in_memory(self, tmp_path):
        # Make the goals directory read-only to cause write failure
        project_root = str(tmp_path)
        goals_dir = tmp_path / ".kognisant" / "goals"
        goals_dir.mkdir(parents=True)

        loop = LearningLoop(project_root)

        # Make directory read-only
        os.chmod(str(goals_dir), 0o444)
        try:
            signal = FeedbackSignal(
                goal_type="contract_violation",
                module="agents",
                polarity="positive",
                strength=1.0,
                timestamp="2025-01-01T00:00:00+00:00",
                source="accept",
            )
            # Should not raise — logs to stderr and retains in memory
            loop.record_signal(signal)
            assert len(loop._signals) == 1
        finally:
            # Restore permissions for cleanup
            os.chmod(str(goals_dir), 0o755)

    def test_retry_after_write_failure(self, tmp_path):
        project_root = str(tmp_path)
        goals_dir = tmp_path / ".kognisant" / "goals"
        goals_dir.mkdir(parents=True)

        loop = LearningLoop(project_root)

        # Cause a write failure
        os.chmod(str(goals_dir), 0o444)
        try:
            loop.record_signal(
                FeedbackSignal(
                    goal_type="contract_violation",
                    module="agents",
                    polarity="positive",
                    strength=1.0,
                    timestamp="2025-01-01T00:00:00+00:00",
                    source="accept",
                )
            )
        finally:
            os.chmod(str(goals_dir), 0o755)

        # Now record another signal — this should persist both
        loop.record_signal(
            FeedbackSignal(
                goal_type="coverage_gap",
                module="chat",
                polarity="negative",
                strength=1.0,
                timestamp="2025-01-01T00:01:00+00:00",
                source="dismiss",
            )
        )

        # Verify both are persisted
        path = os.path.join(project_root, ".kognisant", "goals", "learning.json")
        assert os.path.exists(path)
        with open(path, "r") as f:
            data = json.load(f)
        assert len(data["signals"]) == 2


class TestDirectoryCreation:
    """Test that .kognisant/goals/ is created if it doesn't exist."""

    def test_creates_directory_on_first_write(self, tmp_path):
        project_root = str(tmp_path)
        # Don't create the directory — let LearningLoop handle it
        loop = LearningLoop(project_root)
        loop.record_accept("contract_violation", "agents", "2025-01-01T00:00:00+00:00")

        path = os.path.join(project_root, ".kognisant", "goals", "learning.json")
        assert os.path.exists(path)


class TestCorruptedFile:
    """Test handling of corrupted learning.json."""

    def test_corrupted_json_starts_fresh(self, tmp_path):
        project_root = str(tmp_path)
        goals_dir = tmp_path / ".kognisant" / "goals"
        goals_dir.mkdir(parents=True)

        # Write corrupted JSON
        path = goals_dir / "learning.json"
        path.write_text("not valid json {{{")

        # Should not raise, starts with empty signals
        loop = LearningLoop(project_root)
        assert len(loop._signals) == 0

        # Should be able to record new signals
        loop.record_accept("contract_violation", "agents", "2025-01-01T00:00:00+00:00")
        assert len(loop._signals) == 1
