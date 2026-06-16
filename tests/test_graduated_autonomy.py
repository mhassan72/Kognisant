"""Tests for GraduatedAutonomyController in goal_engine.py.

Covers: R15.1 (auto_execute), R15.2 (suppress + unsuppression),
R15.3 (ask), R15.4 (per-project rates with global fallback),
R15.5 (cold start mode), R15.6 (persistence), R15.7 (threshold recalculation).
"""

import json
import os

import pytest

from cli_kognisant.goal_engine import GraduatedAutonomyController, LearningLoop
from cli_kognisant.models import FeedbackSignal


@pytest.fixture
def project_root(tmp_path):
    """Create a temporary project root with .kognisant/goals/ directory."""
    goals_dir = tmp_path / ".kognisant" / "goals"
    goals_dir.mkdir(parents=True)
    return str(tmp_path)


@pytest.fixture
def global_core_dir(tmp_path, monkeypatch):
    """Create a temporary global core directory and patch config module."""
    core_dir = tmp_path / "kognisant_core"
    core_dir.mkdir()
    # Write empty autonomy config
    config_path = core_dir / "autonomy_config.json"
    config_path.write_text("{}")

    # Patch the GLOBAL_CORE_DIR in the config module
    monkeypatch.setattr("cli_kognisant.config.GLOBAL_CORE_DIR", str(core_dir))
    return str(core_dir)


@pytest.fixture
def loop(project_root):
    """Create a LearningLoop instance."""
    return LearningLoop(project_root)


@pytest.fixture
def controller(loop, project_root, global_core_dir):
    """Create a GraduatedAutonomyController instance."""
    return GraduatedAutonomyController(loop, project_root)


def _add_signals(loop, goal_type, module, count, polarity="positive"):
    """Helper to add multiple signals to a LearningLoop."""
    for i in range(count):
        signal = FeedbackSignal(
            goal_type=goal_type,
            module=module,
            polarity=polarity,
            strength=1.0,
            timestamp=f"2025-01-01T00:{i:02d}:00+00:00",
            source="accept" if polarity == "positive" else "dismiss",
        )
        loop.record_signal(signal)


class TestThresholdTransitions:
    """Test autonomy level transitions based on acceptance rate thresholds."""

    def test_auto_execute_above_85_percent(self, loop, controller):
        """Rate > 85% → auto_execute (R15.1)."""
        # Need enough signals to exit cold start AND have high rate
        # 20 positive signals → rate = 1.0 > 0.85
        _add_signals(loop, "contract_violation", "agents", 20, "positive")

        level = controller.get_autonomy_level("contract_violation")
        assert level == "auto_execute"

    def test_suppress_below_20_percent(self, loop, controller):
        """Rate < 20% → suppress (R15.2)."""
        # 20 negative signals → rate = 0.0 < 0.20
        _add_signals(loop, "stale_artifact", "agents", 20, "negative")

        level = controller.get_autonomy_level("stale_artifact")
        assert level == "suppress"

    def test_ask_between_thresholds(self, loop, controller):
        """20% <= rate <= 85% → ask (R15.3)."""
        # Mix of positive and negative to get rate in the middle
        # 10 positive + 10 negative (asymmetric): positive=10.0, negative=10*1.5=15.0
        # rate = 10.0 / 25.0 = 0.4 → ask
        _add_signals(loop, "coverage_gap", "agents", 10, "positive")
        _add_signals(loop, "coverage_gap", "agents", 10, "negative")

        level = controller.get_autonomy_level("coverage_gap")
        assert level == "ask"

    def test_exactly_85_percent_is_ask(self, loop, controller):
        """Rate == 85% is NOT > 85%, so it should be 'ask'."""
        # We need exactly 85% rate. Due to asymmetric weighting this is tricky.
        # Let's compute: for rate to be exactly at boundary,
        # we need positive / (positive + negative*1.5) = 0.85
        # With enough signals to exit cold start (>=20 total).
        # 20 positive + some negative won't give exactly 85%, but let's test
        # that the boundary is strictly > 85%.
        # 17 positive + 3 negative: rate = 17 / (17 + 3*1.5) = 17 / 21.5 ≈ 0.7907 → ask
        _add_signals(loop, "decay_alert", "agents", 17, "positive")
        _add_signals(loop, "decay_alert", "agents", 3, "negative")

        level = controller.get_autonomy_level("decay_alert")
        assert level == "ask"

    def test_exactly_20_percent_is_ask(self, loop, controller):
        """Rate at boundary (== 20%) is NOT < 20%, so it should be 'ask'.

        Since asymmetric weighting makes exact 20% hard with integers,
        we test a rate just above the suppress threshold to verify the
        boundary behavior: rate >= 20% → ask, not suppress.
        """
        # 5 positive + 15 negative within a 20-signal window:
        # rate = 5 / (5 + 15*1.5) = 5 / 27.5 ≈ 0.1818 → suppress
        # 6 positive + 14 negative:
        # rate = 6 / (6 + 14*1.5) = 6 / 27 ≈ 0.2222 → ask (above 20%)
        _add_signals(loop, "complexity", "agents", 6, "positive")
        _add_signals(loop, "complexity", "agents", 14, "negative")

        rate = loop.get_acceptance_rate("complexity")
        expected_rate = 6.0 / (6.0 + 14.0 * 1.5)
        assert abs(rate - expected_rate) < 0.001
        # 0.2222 >= 0.20, so should be "ask"
        level = controller.get_autonomy_level("complexity")
        assert level == "ask"


class TestColdStart:
    """Test cold start mode behavior (R15.5)."""

    def test_cold_start_when_under_20_proposals(self, loop, controller):
        """Cold start is active when < 20 total proposals."""
        assert controller.is_cold_start() is True

        # Add 19 signals → still cold start
        _add_signals(loop, "contract_violation", "agents", 19, "positive")
        assert controller.is_cold_start() is True

    def test_cold_start_exits_at_20_proposals(self, loop, controller):
        """Cold start exits when reaching 20 total proposals."""
        _add_signals(loop, "contract_violation", "agents", 20, "positive")
        assert controller.is_cold_start() is False

    def test_cold_start_forces_ask(self, loop, controller):
        """In cold start, all goals return 'ask' regardless of rate."""
        # Even with a perfect rate (all positive), cold start overrides
        _add_signals(loop, "contract_violation", "agents", 10, "positive")
        assert controller.is_cold_start() is True
        level = controller.get_autonomy_level("contract_violation")
        assert level == "ask"

    def test_cold_start_confidence_ceiling(self, loop, controller):
        """Cold start applies confidence ceiling of 0.7 to llm_inference beliefs."""
        assert controller.is_cold_start() is True
        ceiling = controller.get_confidence_ceiling()
        assert ceiling == 0.7

    def test_no_confidence_ceiling_outside_cold_start(self, loop, controller):
        """No confidence ceiling when not in cold start."""
        _add_signals(loop, "contract_violation", "agents", 20, "positive")
        assert controller.is_cold_start() is False
        ceiling = controller.get_confidence_ceiling()
        assert ceiling is None


class TestUnsuppression:
    """Test unsuppression logic (R15.2)."""

    def test_should_unsuppress_after_10_proposals(self, loop, controller):
        """After 10 proposals across other types, present one suppressed goal."""
        # First make a type suppressed (all negative, enough to exit cold start)
        _add_signals(loop, "stale_artifact", "agents", 20, "negative")
        # Add other proposals to exit cold start for the other type
        _add_signals(loop, "contract_violation", "agents", 10, "positive")

        # Verify stale_artifact is suppressed
        assert controller.get_autonomy_level("stale_artifact") == "suppress"

        # Record 10 proposals for other types
        for _ in range(10):
            controller.record_proposal("contract_violation")

        assert controller.should_unsuppress("stale_artifact") is True

    def test_should_not_unsuppress_before_10_proposals(self, loop, controller):
        """Not enough proposals yet → don't unsuppress."""
        _add_signals(loop, "stale_artifact", "agents", 20, "negative")
        _add_signals(loop, "contract_violation", "agents", 10, "positive")

        # Record 9 proposals (not enough)
        for _ in range(9):
            controller.record_proposal("contract_violation")

        assert controller.should_unsuppress("stale_artifact") is False

    def test_unsuppress_counter_resets(self, loop, controller):
        """Counter resets after unsuppression."""
        _add_signals(loop, "stale_artifact", "agents", 20, "negative")
        _add_signals(loop, "contract_violation", "agents", 10, "positive")

        # Record 10 proposals
        for _ in range(10):
            controller.record_proposal("contract_violation")

        assert controller.should_unsuppress("stale_artifact") is True
        controller.reset_unsuppress_counter()
        assert controller.should_unsuppress("stale_artifact") is False

    def test_non_suppressed_type_returns_false(self, loop, controller):
        """should_unsuppress returns False for non-suppressed types."""
        _add_signals(loop, "contract_violation", "agents", 20, "positive")

        for _ in range(10):
            controller.record_proposal("contract_violation")

        assert controller.should_unsuppress("contract_violation") is False

    def test_suppressed_proposals_dont_count(self, loop, controller):
        """Proposals of suppressed types don't count toward unsuppression."""
        _add_signals(loop, "stale_artifact", "agents", 20, "negative")
        _add_signals(loop, "contract_violation", "agents", 10, "positive")

        # Record proposals for the suppressed type — these shouldn't count
        for _ in range(15):
            controller.record_proposal("stale_artifact")

        assert controller.should_unsuppress("stale_artifact") is False


class TestGlobalFallback:
    """Test per-project rates with global fallback (R15.4)."""

    def test_both_counts_zero_defaults_to_0_5(self, loop, controller):
        """When both global and local counts are 0, default to 0.5."""
        # Ensure enough signals exist overall to exit cold start
        # but none for this specific type
        _add_signals(loop, "contract_violation", "agents", 20, "positive")

        rate = controller._compute_effective_rate("unknown_type")
        assert rate == 0.5

    def test_local_exclusive_at_20_signals(self, loop, controller):
        """When local_count >= 20, use local_rate exclusively."""
        # 20 positive signals for coverage_gap → local_rate = 1.0
        _add_signals(loop, "coverage_gap", "agents", 20, "positive")

        rate = controller._compute_effective_rate("coverage_gap")
        assert rate == 1.0  # Uses local_rate exclusively

    def test_weighted_blend_with_global_data(self, loop, project_root, global_core_dir):
        """Weighted blend when local_count < 20 and global data exists."""
        # Set up global data in config
        config_path = os.path.join(global_core_dir, "autonomy_config.json")
        config_data = {
            "global_rates": {"coverage_gap": 0.8},
            "global_counts": {"coverage_gap": 10},
        }
        with open(config_path, "w") as f:
            json.dump(config_data, f)

        # Create a new controller with the global config
        ctrl = GraduatedAutonomyController(loop, project_root)

        # Add 5 local signals (all positive → local_rate = 1.0)
        _add_signals(loop, "coverage_gap", "agents", 5, "positive")

        rate = ctrl._compute_effective_rate("coverage_gap")
        # effective = (10 * 0.8 + 5 * 1.0) / (10 + 5) = (8 + 5) / 15 = 13/15 ≈ 0.8667
        expected = (10 * 0.8 + 5 * 1.0) / (10 + 5)
        assert abs(rate - expected) < 0.001

    def test_local_count_19_still_blends(self, loop, project_root, global_core_dir):
        """At 19 local signals, still uses weighted blend with global."""
        config_path = os.path.join(global_core_dir, "autonomy_config.json")
        config_data = {
            "global_rates": {"coverage_gap": 0.3},
            "global_counts": {"coverage_gap": 10},
        }
        with open(config_path, "w") as f:
            json.dump(config_data, f)

        ctrl = GraduatedAutonomyController(loop, project_root)

        # Add 19 positive signals → local_rate = 1.0, local_count = 19
        _add_signals(loop, "coverage_gap", "agents", 19, "positive")

        rate = ctrl._compute_effective_rate("coverage_gap")
        # effective = (10 * 0.3 + 19 * 1.0) / (10 + 19) = (3 + 19) / 29 = 22/29 ≈ 0.7586
        expected = (10 * 0.3 + 19 * 1.0) / (10 + 19)
        assert abs(rate - expected) < 0.001


class TestPersistence:
    """Test configuration persistence (R15.6)."""

    def test_recalculate_persists_config(self, loop, controller, global_core_dir):
        """recalculate_on_signal persists the updated autonomy config."""
        _add_signals(loop, "contract_violation", "agents", 20, "positive")

        level = controller.recalculate_on_signal("contract_violation")
        assert level == "auto_execute"

        # Verify persistence
        config_path = os.path.join(global_core_dir, "autonomy_config.json")
        with open(config_path, "r") as f:
            data = json.load(f)

        assert data["levels"]["contract_violation"] == "auto_execute"

    def test_config_survives_reload(self, loop, project_root, global_core_dir):
        """Config persisted by one controller can be read by another."""
        ctrl1 = GraduatedAutonomyController(loop, project_root)
        _add_signals(loop, "contract_violation", "agents", 20, "positive")
        ctrl1.recalculate_on_signal("contract_violation")

        # Create a new controller — should load persisted config
        ctrl2 = GraduatedAutonomyController(loop, project_root)
        assert "levels" in ctrl2._config
        assert ctrl2._config["levels"]["contract_violation"] == "auto_execute"


class TestThresholdRecalculation:
    """Test threshold recalculation on new signal (R15.7)."""

    def test_recalculate_updates_level_on_threshold_cross(self, loop, controller):
        """When a new signal crosses a threshold, level updates."""
        # Start with all positive (auto_execute)
        _add_signals(loop, "contract_violation", "agents", 20, "positive")
        assert controller.get_autonomy_level("contract_violation") == "auto_execute"

        # Add enough negative signals to drop below 85%
        # Current: 20 positive. Add 10 negative:
        # positive=20, negative=10*1.5=15 → rate = 20/35 ≈ 0.571 → "ask"
        _add_signals(loop, "contract_violation", "agents", 10, "negative")

        new_level = controller.recalculate_on_signal("contract_violation")
        assert new_level == "ask"

    def test_recalculate_returns_current_level(self, loop, controller):
        """recalculate_on_signal returns the current autonomy level."""
        _add_signals(loop, "coverage_gap", "agents", 10, "positive")
        _add_signals(loop, "coverage_gap", "agents", 10, "negative")

        level = controller.recalculate_on_signal("coverage_gap")
        assert level == "ask"
