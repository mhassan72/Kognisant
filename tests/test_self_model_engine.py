"""Tests for SelfModelEngine — persistence, Bayesian updates, circuit breaker, model selection."""

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from cli_kognisant.self_model_engine import (
    CircuitBreakerState,
    ModelReliability,
    SelfModel,
    SelfModelEngine,
    ToolReliability,
    _bayesian_reliability,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_dir(tmp_path):
    """Provide a temporary directory for test files."""
    return tmp_path


@pytest.fixture
def model_path(tmp_dir):
    """Provide a path for self_model.json in a temp dir."""
    return str(tmp_dir / "self_model.json")


@pytest.fixture
def fresh_model():
    """Create a fresh SelfModel with default values."""
    return SelfModel()


# ---------------------------------------------------------------------------
# Bayesian Formula
# ---------------------------------------------------------------------------


class TestBayesianReliability:
    def test_zero_counts(self):
        # (0+1)/(0+0+2) = 0.5
        assert _bayesian_reliability(0, 0) == 0.5

    def test_all_successes(self):
        # (10+1)/(10+0+2) = 11/12
        assert abs(_bayesian_reliability(10, 0) - 11 / 12) < 1e-9

    def test_all_failures(self):
        # (0+1)/(0+10+2) = 1/12
        assert abs(_bayesian_reliability(0, 10) - 1 / 12) < 1e-9

    def test_mixed(self):
        # (5+1)/(5+5+2) = 6/12 = 0.5
        assert _bayesian_reliability(5, 5) == 0.5


# ---------------------------------------------------------------------------
# Persistence (R1.1)
# ---------------------------------------------------------------------------


class TestPersistence:
    def test_load_missing_file_returns_defaults(self, tmp_dir):
        path = str(tmp_dir / "nonexistent.json")
        model = SelfModelEngine.load(path)
        assert model.version == 1
        assert model.valence == 0
        assert model.frustration == 0.0
        assert model.total_executions == 0

    def test_save_and_load_roundtrip(self, model_path):
        model = SelfModel(valence=42, frustration=3.5, total_executions=10)
        model.model_reliability["gpt-4"] = ModelReliability(successes=5, failures=2)
        model.tool_reliability["read_file"] = ToolReliability(successes=10, failures=1)
        model.circuit_breakers["gpt-4"] = CircuitBreakerState(state="half_open")

        SelfModelEngine.save(model, model_path)
        loaded = SelfModelEngine.load(model_path)

        assert loaded.valence == 42
        assert loaded.frustration == 3.5
        assert loaded.total_executions == 10
        assert "gpt-4" in loaded.model_reliability
        assert loaded.model_reliability["gpt-4"].successes == 5
        assert "read_file" in loaded.tool_reliability
        assert loaded.tool_reliability["read_file"].successes == 10
        assert loaded.circuit_breakers["gpt-4"].state == "half_open"

    def test_save_creates_directory(self, tmp_dir):
        nested_path = str(tmp_dir / "nested" / "deep" / "self_model.json")
        model = SelfModel(valence=7)
        SelfModelEngine.save(model, nested_path)
        assert os.path.exists(nested_path)

    def test_atomic_write_produces_valid_json(self, model_path):
        model = SelfModel(valence=-50)
        SelfModelEngine.save(model, model_path)

        with open(model_path, "r") as f:
            data = json.load(f)
        assert data["valence"] == -50

    def test_load_corrupted_file_returns_defaults(self, tmp_dir):
        path = str(tmp_dir / "corrupted.json")
        with open(path, "w") as f:
            f.write("not valid json{{{")
        model = SelfModelEngine.load(path)
        assert model.version == 1
        assert model.valence == 0


# ---------------------------------------------------------------------------
# Temporal Decay (R1.7)
# ---------------------------------------------------------------------------


class TestApplyDecay:
    def test_no_last_execution_no_decay(self, fresh_model):
        fresh_model.valence = 50
        fresh_model.frustration = 10.0
        SelfModelEngine.apply_decay(fresh_model)
        assert fresh_model.valence == 50
        assert fresh_model.frustration == 10.0

    def test_valence_decays_toward_zero(self, fresh_model):
        # Set last_execution_at to 1 day ago
        one_day_ago = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        fresh_model.valence = 100
        fresh_model.last_execution_at = one_day_ago

        SelfModelEngine.apply_decay(fresh_model)

        # 100 * 0.9^~1 ≈ 89-90 (int truncation, slight timing variance)
        assert fresh_model.valence in (89, 90)

    def test_negative_valence_decays_toward_zero(self, fresh_model):
        one_day_ago = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        fresh_model.valence = -100
        fresh_model.last_execution_at = one_day_ago

        SelfModelEngine.apply_decay(fresh_model)

        # -100 * 0.9^~1 ≈ -89 to -90 (int truncation toward zero)
        assert fresh_model.valence in (-89, -90)

    def test_frustration_halves_per_day(self, fresh_model):
        one_day_ago = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        fresh_model.frustration = 16.0
        fresh_model.last_execution_at = one_day_ago

        SelfModelEngine.apply_decay(fresh_model)

        # 16.0 * 0.5^1 = 8.0
        assert abs(fresh_model.frustration - 8.0) < 0.1

    def test_two_days_decay(self, fresh_model):
        two_days_ago = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        fresh_model.valence = 100
        fresh_model.frustration = 16.0
        fresh_model.last_execution_at = two_days_ago

        SelfModelEngine.apply_decay(fresh_model)

        # Valence: 100 * 0.9^~2 ≈ 80-81 (int truncation, timing variance)
        assert fresh_model.valence in (80, 81)
        # Frustration: 16.0 * 0.5^~2 ≈ 4.0
        assert abs(fresh_model.frustration - 4.0) < 0.1


# ---------------------------------------------------------------------------
# Record Success/Failure (R1.2)
# ---------------------------------------------------------------------------


class TestRecordSuccess:
    def test_first_success(self, fresh_model):
        SelfModelEngine.record_success(fresh_model, "gpt-4", response_time=5.0)

        rel = fresh_model.model_reliability["gpt-4"]
        assert rel.successes == 1
        assert rel.failures == 0
        assert rel.attempts == 1
        # (1+1)/(1+0+2) = 2/3
        assert abs(rel.reliability - 2 / 3) < 1e-9
        assert rel.last_success_at is not None
        assert rel.avg_response_time == 5.0
        assert fresh_model.consecutive_failures == 0

    def test_consecutive_failures_reset(self, fresh_model):
        fresh_model.consecutive_failures = 3
        SelfModelEngine.record_success(fresh_model, "gpt-4", response_time=2.0)
        assert fresh_model.consecutive_failures == 0

    def test_response_time_rolling_average(self, fresh_model):
        SelfModelEngine.record_success(fresh_model, "gpt-4", response_time=10.0)
        assert fresh_model.model_reliability["gpt-4"].avg_response_time == 10.0

        SelfModelEngine.record_success(fresh_model, "gpt-4", response_time=5.0)
        # 10.0 * 0.8 + 5.0 * 0.2 = 9.0
        assert abs(fresh_model.model_reliability["gpt-4"].avg_response_time - 9.0) < 1e-9


class TestRecordFailure:
    def test_first_failure(self, fresh_model):
        SelfModelEngine.record_failure(fresh_model, "gpt-4")

        rel = fresh_model.model_reliability["gpt-4"]
        assert rel.successes == 0
        assert rel.failures == 1
        assert rel.attempts == 1
        # (0+1)/(0+1+2) = 1/3
        assert abs(rel.reliability - 1 / 3) < 1e-9
        assert rel.last_failure_at is not None
        assert fresh_model.consecutive_failures == 1

    def test_multiple_failures_increment(self, fresh_model):
        SelfModelEngine.record_failure(fresh_model, "gpt-4")
        SelfModelEngine.record_failure(fresh_model, "gpt-4")
        assert fresh_model.consecutive_failures == 2
        assert fresh_model.model_reliability["gpt-4"].failures == 2


# ---------------------------------------------------------------------------
# Tool Reliability (R1.6)
# ---------------------------------------------------------------------------


class TestRecordToolResult:
    def test_tool_success(self, fresh_model):
        SelfModelEngine.record_tool_result(fresh_model, "read_file", success=True)

        tool = fresh_model.tool_reliability["read_file"]
        assert tool.successes == 1
        assert tool.failures == 0
        # (1+1)/(1+0+2) = 2/3
        assert abs(tool.reliability - 2 / 3) < 1e-9

    def test_tool_failure(self, fresh_model):
        SelfModelEngine.record_tool_result(fresh_model, "edit_file", success=False)

        tool = fresh_model.tool_reliability["edit_file"]
        assert tool.successes == 0
        assert tool.failures == 1
        # (0+1)/(0+1+2) = 1/3
        assert abs(tool.reliability - 1 / 3) < 1e-9

    def test_mixed_tool_results(self, fresh_model):
        SelfModelEngine.record_tool_result(fresh_model, "search", success=True)
        SelfModelEngine.record_tool_result(fresh_model, "search", success=True)
        SelfModelEngine.record_tool_result(fresh_model, "search", success=False)

        tool = fresh_model.tool_reliability["search"]
        assert tool.successes == 2
        assert tool.failures == 1
        # (2+1)/(2+1+2) = 3/5 = 0.6
        assert abs(tool.reliability - 0.6) < 1e-9


# ---------------------------------------------------------------------------
# Valence (R1.3)
# ---------------------------------------------------------------------------


class TestUpdateValence:
    def test_positive_clamp(self, fresh_model):
        fresh_model.valence = 95
        SelfModelEngine.update_valence(fresh_model, 10)
        assert fresh_model.valence == 100

    def test_negative_clamp(self, fresh_model):
        fresh_model.valence = -95
        SelfModelEngine.update_valence(fresh_model, -10)
        assert fresh_model.valence == -100

    def test_normal_update(self, fresh_model):
        fresh_model.valence = 0
        SelfModelEngine.update_valence(fresh_model, 5)
        assert fresh_model.valence == 5

    def test_negative_delta(self, fresh_model):
        fresh_model.valence = 10
        SelfModelEngine.update_valence(fresh_model, -15)
        assert fresh_model.valence == -5


# ---------------------------------------------------------------------------
# Token Calibration (R11.1)
# ---------------------------------------------------------------------------


class TestTokenCalibration:
    def test_initial_calibration_update(self, fresh_model):
        # Initial calibration = 1.0
        SelfModelEngine.update_token_calibration(fresh_model, "gpt-4", actual_tokens=120, estimated_tokens=100)

        rel = fresh_model.model_reliability["gpt-4"]
        # 1.0 * 0.8 + (120/100) * 0.2 = 0.8 + 0.24 = 1.04
        assert abs(rel.token_calibration - 1.04) < 1e-9

    def test_zero_estimated_no_update(self, fresh_model):
        fresh_model.model_reliability["gpt-4"] = ModelReliability(token_calibration=1.5)
        SelfModelEngine.update_token_calibration(fresh_model, "gpt-4", actual_tokens=100, estimated_tokens=0)
        assert fresh_model.model_reliability["gpt-4"].token_calibration == 1.5

    def test_repeated_calibration_converges(self, fresh_model):
        # Repeated updates with ratio=1.0 should converge calibration to 1.0
        for _ in range(20):
            SelfModelEngine.update_token_calibration(fresh_model, "gpt-4", actual_tokens=100, estimated_tokens=100)
        rel = fresh_model.model_reliability["gpt-4"]
        assert abs(rel.token_calibration - 1.0) < 0.01


# ---------------------------------------------------------------------------
# Background Signals (R1.3)
# ---------------------------------------------------------------------------


class TestBackgroundSignals:
    def test_no_pressure_when_healthy(self, fresh_model):
        signal = SelfModelEngine.compute_background_signals(fresh_model)
        assert signal == 0

    def test_low_tool_reliability_pressure(self, fresh_model):
        fresh_model.tool_reliability["t1"] = ToolReliability(successes=0, failures=5, reliability=0.1)
        fresh_model.tool_reliability["t2"] = ToolReliability(successes=0, failures=5, reliability=0.2)
        signal = SelfModelEngine.compute_background_signals(fresh_model)
        assert signal == -2

    def test_failed_jobs_pressure(self, fresh_model):
        signal = SelfModelEngine.compute_background_signals(fresh_model, failed_jobs_count=3)
        assert signal == -3

    def test_stale_world_model_pressure(self, fresh_model):
        signal = SelfModelEngine.compute_background_signals(fresh_model, world_model_stale=True)
        assert signal == -1

    def test_low_goal_rate_pressure(self, fresh_model):
        signal = SelfModelEngine.compute_background_signals(fresh_model, goal_acceptance_rate=0.2)
        assert signal == -1

    def test_combined_capped_at_minus_five(self, fresh_model):
        fresh_model.tool_reliability["t1"] = ToolReliability(successes=0, failures=10, reliability=0.1)
        signal = SelfModelEngine.compute_background_signals(
            fresh_model,
            failed_jobs_count=5,
            world_model_stale=True,
            goal_acceptance_rate=0.1,
        )
        # -2 (tools) + -5 (jobs) + -1 (stale) + -1 (goals) = -9, capped to -5
        assert signal == -5


# ---------------------------------------------------------------------------
# Circuit Breaker (R1.4)
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    def test_closed_allows_attempt(self):
        cb = CircuitBreakerState(state="closed")
        assert SelfModelEngine.cb_can_attempt(cb) is True

    def test_half_open_allows_attempt(self):
        cb = CircuitBreakerState(state="half_open")
        assert SelfModelEngine.cb_can_attempt(cb) is True

    def test_open_blocks_before_cooldown(self):
        future = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()
        cb = CircuitBreakerState(state="open", open_until=future)
        assert SelfModelEngine.cb_can_attempt(cb) is False

    def test_open_allows_after_cooldown(self):
        past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        cb = CircuitBreakerState(state="open", open_until=past)
        assert SelfModelEngine.cb_can_attempt(cb) is True
        assert cb.state == "half_open"

    def test_five_failures_trips_breaker(self):
        cb = CircuitBreakerState(state="closed")
        for _ in range(5):
            SelfModelEngine.cb_record_failure(cb)

        assert cb.state == "open"
        assert cb.open_until is not None

    def test_failures_outside_window_dont_trip(self):
        cb = CircuitBreakerState(state="closed")
        # Set window_start to 31 seconds ago (outside the 30s window)
        old_start = (datetime.now(timezone.utc) - timedelta(seconds=31)).isoformat()
        cb.window_start = old_start
        cb.failures_in_window = 4

        # This failure should reset the window because old window expired
        SelfModelEngine.cb_record_failure(cb)
        assert cb.state == "closed"
        assert cb.failures_in_window == 1

    def test_half_open_success_closes(self):
        cb = CircuitBreakerState(state="half_open")
        SelfModelEngine.cb_record_success(cb)
        assert cb.state == "closed"
        assert cb.failures_in_window == 0

    def test_half_open_failure_reopens(self):
        cb = CircuitBreakerState(state="half_open")
        SelfModelEngine.cb_record_failure(cb)
        assert cb.state == "open"
        assert cb.open_until is not None

    def test_success_in_closed_resets_window(self):
        cb = CircuitBreakerState(state="closed", failures_in_window=3, window_start="2024-01-01T00:00:00+00:00")
        SelfModelEngine.cb_record_success(cb)
        assert cb.failures_in_window == 0
        assert cb.window_start is None


# ---------------------------------------------------------------------------
# Model Selection (R1.5)
# ---------------------------------------------------------------------------


class TestSelectModel:
    def test_default_closed_selected(self, fresh_model):
        model_name, switched, reason = SelfModelEngine.select_model(
            fresh_model, "gpt-4", ["gpt-4", "claude-3"]
        )
        assert model_name == "gpt-4"
        assert switched is False
        assert reason == ""

    def test_default_half_open_selected(self, fresh_model):
        fresh_model.circuit_breakers["gpt-4"] = CircuitBreakerState(state="half_open")
        model_name, switched, reason = SelfModelEngine.select_model(
            fresh_model, "gpt-4", ["gpt-4", "claude-3"]
        )
        assert model_name == "gpt-4"
        assert switched is False

    def test_default_open_switches_to_alternative(self, fresh_model):
        future = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()
        fresh_model.circuit_breakers["gpt-4"] = CircuitBreakerState(state="open", open_until=future)
        fresh_model.model_reliability["claude-3"] = ModelReliability(
            successes=8, failures=2, reliability=0.75
        )

        model_name, switched, reason = SelfModelEngine.select_model(
            fresh_model, "gpt-4", ["gpt-4", "claude-3"]
        )
        assert model_name == "claude-3"
        assert switched is True
        assert "claude-3" in reason

    def test_default_open_no_good_alt_uses_default_with_warning(self, fresh_model):
        future = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()
        fresh_model.circuit_breakers["gpt-4"] = CircuitBreakerState(state="open", open_until=future)
        # Alternative has low reliability
        fresh_model.model_reliability["claude-3"] = ModelReliability(
            successes=1, failures=10, reliability=0.2
        )

        model_name, switched, reason = SelfModelEngine.select_model(
            fresh_model, "gpt-4", ["gpt-4", "claude-3"]
        )
        assert model_name == "gpt-4"
        assert switched is False
        assert "no reliable alternative" in reason

    def test_default_open_cooldown_expired_transitions(self, fresh_model):
        past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        fresh_model.circuit_breakers["gpt-4"] = CircuitBreakerState(state="open", open_until=past)

        model_name, switched, reason = SelfModelEngine.select_model(
            fresh_model, "gpt-4", ["gpt-4", "claude-3"]
        )
        assert model_name == "gpt-4"
        assert switched is False
        # CB should now be half_open
        assert fresh_model.circuit_breakers["gpt-4"].state == "half_open"


# ---------------------------------------------------------------------------
# Scan Capabilities (R1.8)
# ---------------------------------------------------------------------------


class TestScanCapabilities:
    def test_empty_directories(self, tmp_dir, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_dir))
        # Patch expanduser so it uses tmp_dir
        core_dir = tmp_dir / ".kognisant_core"
        core_dir.mkdir()
        monkeypatch.setattr(os.path, "expanduser", lambda p: str(tmp_dir / p.lstrip("~/")))

        result = SelfModelEngine.scan_capabilities()
        assert result["scripts_count"] == 0
        assert result["skills_count"] == 0
        assert result["custom_tools_count"] == 0

    def test_counts_scripts_and_skills(self, tmp_dir, monkeypatch):
        core_dir = tmp_dir / ".kognisant_core"
        scripts_dir = core_dir / "scripts"
        skills_dir = core_dir / "skills"
        tools_dir = core_dir / "tools"
        scripts_dir.mkdir(parents=True)
        skills_dir.mkdir(parents=True)
        tools_dir.mkdir(parents=True)

        # Create test files
        (scripts_dir / "script1.py").write_text("")
        (scripts_dir / "script2.py").write_text("")
        (scripts_dir / "readme.txt").write_text("")  # Not a .py
        (skills_dir / "skill1.md").write_text("")
        (tools_dir / "tool1.json").write_text("")
        (tools_dir / "tool2.json").write_text("")

        monkeypatch.setattr(os.path, "expanduser", lambda p: str(core_dir) if "~/.kognisant_core" in p else p)

        result = SelfModelEngine.scan_capabilities()
        assert result["scripts_count"] == 2
        assert result["skills_count"] == 1
        assert result["custom_tools_count"] == 2

    def test_counts_project_capabilities(self, tmp_dir, monkeypatch):
        core_dir = tmp_dir / ".kognisant_core"
        core_dir.mkdir(parents=True)
        monkeypatch.setattr(os.path, "expanduser", lambda p: str(core_dir) if "~/.kognisant_core" in p else p)

        # Set up project structure
        project = tmp_dir / "myproject"
        kognisant_dir = project / ".kognisant"
        specs_dir = kognisant_dir / "specs"
        history_dir = kognisant_dir / "history"
        specs_dir.mkdir(parents=True)
        history_dir.mkdir(parents=True)

        # Create specs (directories)
        (specs_dir / "feature-a").mkdir()
        (specs_dir / "feature-b").mkdir()

        # Create history files
        (history_dir / "session_001.json").write_text("{}")
        (history_dir / "session_002.json").write_text("{}")

        # Create goals file
        (kognisant_dir / "goals.json").write_text('[{"name": "goal1"}, {"name": "goal2"}]')

        result = SelfModelEngine.scan_capabilities(project_path=str(project))
        assert result["specs_count"] == 2
        assert result["goals_count"] == 2
        assert result["session_history_count"] == 2

    def test_active_jobs_count(self, tmp_dir, monkeypatch):
        core_dir = tmp_dir / ".kognisant_core"
        core_dir.mkdir(parents=True)
        monkeypatch.setattr(os.path, "expanduser", lambda p: str(core_dir) if "~/.kognisant_core" in p else p)

        jobs_data = [
            {"name": "job1", "status": "active"},
            {"name": "job2", "status": "active"},
            {"name": "job3", "status": "completed"},
        ]
        (core_dir / "jobs.json").write_text(json.dumps(jobs_data))

        result = SelfModelEngine.scan_capabilities()
        assert result["active_jobs_count"] == 2
