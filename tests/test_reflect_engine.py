"""Tests for the reflect engine module."""

from cli_kognisant.reflect_engine import (
    reflect_cold,
    reflect_hot,
    reflect_warm,
    should_run_cold,
    should_run_warm,
)
from cli_kognisant.self_model_engine import ModelReliability, SelfModel, ToolReliability


class TestReflectHotValenceRules:
    """Test valence delta rules for different outcomes."""

    def _fresh_model(self) -> SelfModel:
        return SelfModel()

    def test_success_fast_gives_plus_5(self):
        sm = self._fresh_model()
        delta = reflect_hot(sm, success=True, response_time=5.0)
        assert delta == 5
        assert sm.valence == 5

    def test_success_moderate_gives_plus_3(self):
        sm = self._fresh_model()
        delta = reflect_hot(sm, success=True, response_time=15.0)
        assert delta == 3
        assert sm.valence == 3

    def test_success_at_boundary_10s_is_moderate(self):
        sm = self._fresh_model()
        delta = reflect_hot(sm, success=True, response_time=10.0)
        assert delta == 3

    def test_success_at_boundary_30s_is_moderate(self):
        sm = self._fresh_model()
        delta = reflect_hot(sm, success=True, response_time=30.0)
        assert delta == 3

    def test_success_slow_gives_plus_1(self):
        sm = self._fresh_model()
        delta = reflect_hot(sm, success=True, response_time=35.0)
        assert delta == 1
        assert sm.valence == 1

    def test_timeout_gives_minus_15(self):
        sm = self._fresh_model()
        delta = reflect_hot(sm, success=False, response_time=60.0, timed_out=True)
        assert delta == -15
        assert sm.valence == -15

    def test_empty_gives_minus_10(self):
        sm = self._fresh_model()
        delta = reflect_hot(sm, success=False, response_time=5.0, empty=True)
        assert delta == -10
        assert sm.valence == -10

    def test_cancelled_gives_minus_5(self):
        sm = self._fresh_model()
        delta = reflect_hot(sm, success=False, response_time=3.0, cancelled=True)
        assert delta == -5
        assert sm.valence == -5

    def test_error_gives_minus_10(self):
        sm = self._fresh_model()
        delta = reflect_hot(sm, success=False, response_time=2.0, error=True)
        assert delta == -10
        assert sm.valence == -10

    def test_priority_timeout_over_empty(self):
        """Timeout takes precedence when both flags are set."""
        sm = self._fresh_model()
        delta = reflect_hot(
            sm, success=False, response_time=60.0, timed_out=True, empty=True
        )
        assert delta == -15


class TestReflectHotBackgroundPressure:
    """Test background signal pressure applied in hot reflect."""

    def test_background_pressure_adds_to_delta(self):
        sm = SelfModel()
        delta = reflect_hot(
            sm, success=True, response_time=5.0, background_pressure=-3
        )
        # +5 from success+fast, -3 from background = +2
        assert delta == 2
        assert sm.valence == 2

    def test_background_pressure_clamped_to_minus_5(self):
        sm = SelfModel()
        delta = reflect_hot(
            sm, success=True, response_time=5.0, background_pressure=-10
        )
        # +5 from success+fast, -5 (clamped) from background = 0
        assert delta == 0
        assert sm.valence == 0

    def test_zero_pressure_has_no_effect(self):
        sm = SelfModel()
        delta = reflect_hot(
            sm, success=True, response_time=5.0, background_pressure=0
        )
        assert delta == 5

    def test_positive_pressure_ignored(self):
        """Positive background pressure should not boost valence."""
        sm = SelfModel()
        delta = reflect_hot(
            sm, success=True, response_time=5.0, background_pressure=3
        )
        # Clamped to max(-5, 3) = 3, but since it's positive, no addition
        assert delta == 5


class TestReflectHotConsecutiveFailures:
    """Test consecutive failure tracking."""

    def test_success_resets_consecutive_failures(self):
        sm = SelfModel(consecutive_failures=5)
        reflect_hot(sm, success=True, response_time=5.0)
        assert sm.consecutive_failures == 0

    def test_failure_increments_consecutive_failures(self):
        sm = SelfModel(consecutive_failures=2)
        reflect_hot(sm, success=False, response_time=5.0, error=True)
        assert sm.consecutive_failures == 3

    def test_multiple_failures_accumulate(self):
        sm = SelfModel()
        reflect_hot(sm, success=False, response_time=5.0, error=True)
        reflect_hot(sm, success=False, response_time=5.0, error=True)
        reflect_hot(sm, success=False, response_time=5.0, error=True)
        assert sm.consecutive_failures == 3


class TestReflectHotModelReliability:
    """Test model reliability updates in hot reflect."""

    def test_records_model_success(self):
        sm = SelfModel()
        reflect_hot(sm, success=True, response_time=5.0, model_name="gpt-4")
        mr = sm.model_reliability["gpt-4"]
        assert mr.successes == 1
        assert mr.failures == 0
        assert mr.attempts == 1
        # Bayesian: (1+1)/(1+0+2) = 2/3
        assert abs(mr.reliability - 2 / 3) < 0.001

    def test_records_model_failure(self):
        sm = SelfModel()
        reflect_hot(
            sm, success=False, response_time=5.0, error=True, model_name="gpt-4"
        )
        mr = sm.model_reliability["gpt-4"]
        assert mr.successes == 0
        assert mr.failures == 1
        assert mr.attempts == 1
        # Bayesian: (0+1)/(0+1+2) = 1/3
        assert abs(mr.reliability - 1 / 3) < 0.001

    def test_updates_avg_response_time(self):
        sm = SelfModel()
        reflect_hot(sm, success=True, response_time=10.0, model_name="gpt-4")
        reflect_hot(sm, success=True, response_time=20.0, model_name="gpt-4")
        mr = sm.model_reliability["gpt-4"]
        assert abs(mr.avg_response_time - 15.0) < 0.001

    def test_no_model_name_skips_reliability(self):
        sm = SelfModel()
        reflect_hot(sm, success=True, response_time=5.0)
        assert len(sm.model_reliability) == 0


class TestReflectHotToolReliability:
    """Test tool reliability updates in hot reflect."""

    def test_records_tool_success(self):
        sm = SelfModel()
        tools = [{"name": "read_file", "success": True, "duration": 0.1}]
        reflect_hot(sm, success=True, response_time=5.0, tools_used=tools)
        tr = sm.tool_reliability["read_file"]
        assert tr.successes == 1
        assert tr.failures == 0
        # Bayesian: (1+1)/(1+0+2) = 2/3
        assert abs(tr.reliability - 2 / 3) < 0.001

    def test_records_tool_failure(self):
        sm = SelfModel()
        tools = [{"name": "write_file", "success": False, "duration": 0.5}]
        reflect_hot(sm, success=True, response_time=5.0, tools_used=tools)
        tr = sm.tool_reliability["write_file"]
        assert tr.successes == 0
        assert tr.failures == 1

    def test_multiple_tools_tracked_independently(self):
        sm = SelfModel()
        tools = [
            {"name": "read_file", "success": True, "duration": 0.1},
            {"name": "write_file", "success": False, "duration": 0.5},
        ]
        reflect_hot(sm, success=True, response_time=5.0, tools_used=tools)
        assert sm.tool_reliability["read_file"].successes == 1
        assert sm.tool_reliability["write_file"].failures == 1


class TestReflectHotValenceClamping:
    """Test that valence is clamped to [-100, +100]."""

    def test_valence_clamped_at_100(self):
        sm = SelfModel(valence=99)
        reflect_hot(sm, success=True, response_time=5.0)
        assert sm.valence == 100

    def test_valence_clamped_at_minus_100(self):
        sm = SelfModel(valence=-90)
        reflect_hot(sm, success=False, response_time=60.0, timed_out=True)
        assert sm.valence == -100

    def test_valence_does_not_exceed_100(self):
        sm = SelfModel(valence=98)
        reflect_hot(sm, success=True, response_time=5.0)
        assert sm.valence == 100  # Not 103


class TestReflectHotTotalExecutions:
    """Test that total_executions is incremented."""

    def test_increments_total_executions(self):
        sm = SelfModel(total_executions=10)
        reflect_hot(sm, success=True, response_time=5.0)
        assert sm.total_executions == 11


class TestReflectWarm:
    """Test WARM reflect advisory generation."""

    def test_no_advisories_when_healthy(self):
        sm = SelfModel(consecutive_failures=0)
        assert reflect_warm(sm) == []

    def test_advisory_on_3_consecutive_failures(self):
        sm = SelfModel(consecutive_failures=3)
        advisories = reflect_warm(sm)
        assert len(advisories) == 1
        assert "3 consecutive failures" in advisories[0]
        assert "/model" in advisories[0]

    def test_advisory_on_more_than_3_consecutive_failures(self):
        sm = SelfModel(consecutive_failures=5)
        advisories = reflect_warm(sm)
        assert any("3 consecutive failures" in a for a in advisories)

    def test_advisory_on_low_reliability_model(self):
        sm = SelfModel()
        sm.model_reliability["bad-model"] = ModelReliability(
            successes=1, failures=8, reliability=0.2, attempts=9
        )
        advisories = reflect_warm(sm)
        assert any("bad-model" in a and "low reliability" in a for a in advisories)

    def test_no_advisory_for_low_reliability_with_few_attempts(self):
        sm = SelfModel()
        sm.model_reliability["new-model"] = ModelReliability(
            successes=0, failures=3, reliability=0.2, attempts=3
        )
        advisories = reflect_warm(sm)
        assert not any("new-model" in a for a in advisories)

    def test_combined_advisories(self):
        sm = SelfModel(consecutive_failures=3)
        sm.model_reliability["bad-model"] = ModelReliability(
            successes=1, failures=8, reliability=0.2, attempts=9
        )
        advisories = reflect_warm(sm)
        assert len(advisories) == 2


class TestReflectCold:
    """Test COLD reflect health report generation."""

    def test_basic_health_report(self):
        sm = SelfModel(total_executions=20, valence=25)
        sm.model_reliability["gpt-4"] = ModelReliability(
            successes=15, failures=5, reliability=0.8, attempts=20, avg_response_time=8.5
        )
        report = reflect_cold(sm)
        assert any("Total executions: 20" in line for line in report)
        assert any("Success rate: 75%" in line for line in report)
        assert any("gpt-4" in line for line in report)
        assert any("Good" in line for line in report)

    def test_health_report_with_no_models(self):
        sm = SelfModel(total_executions=20, valence=0)
        report = reflect_cold(sm)
        assert any("N/A" in line for line in report)

    def test_valence_trend_excellent(self):
        sm = SelfModel(total_executions=20, valence=60)
        report = reflect_cold(sm)
        assert any("Excellent" in line for line in report)

    def test_valence_trend_critical(self):
        sm = SelfModel(total_executions=20, valence=-70)
        report = reflect_cold(sm)
        assert any("Critical" in line for line in report)

    def test_valence_trend_neutral(self):
        sm = SelfModel(total_executions=20, valence=0)
        report = reflect_cold(sm)
        assert any("Neutral" in line for line in report)

    def test_multiple_models_in_breakdown(self):
        sm = SelfModel(total_executions=40, valence=10)
        sm.model_reliability["gpt-4"] = ModelReliability(
            successes=15, failures=5, reliability=0.8, attempts=20, avg_response_time=8.0
        )
        sm.model_reliability["claude"] = ModelReliability(
            successes=12, failures=3, reliability=0.85, attempts=15, avg_response_time=12.0
        )
        report = reflect_cold(sm)
        assert any("gpt-4" in line for line in report)
        assert any("claude" in line for line in report)


class TestShouldRunWarm:
    """Test WARM reflect scheduling."""

    def test_every_3rd_execution(self):
        assert should_run_warm(3) is True
        assert should_run_warm(6) is True
        assert should_run_warm(9) is True

    def test_not_on_non_3rd(self):
        assert should_run_warm(1) is False
        assert should_run_warm(2) is False
        assert should_run_warm(4) is False
        assert should_run_warm(5) is False

    def test_zero_returns_false(self):
        assert should_run_warm(0) is False


class TestShouldRunCold:
    """Test COLD reflect scheduling."""

    def test_every_20th_execution(self):
        assert should_run_cold(20) is True
        assert should_run_cold(40) is True
        assert should_run_cold(60) is True

    def test_not_on_non_20th(self):
        assert should_run_cold(1) is False
        assert should_run_cold(10) is False
        assert should_run_cold(19) is False
        assert should_run_cold(21) is False

    def test_zero_returns_false(self):
        assert should_run_cold(0) is False
