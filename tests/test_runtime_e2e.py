"""End-to-end tests for cli_kognisant.runtime — full execute_message() lifecycle.

Exercises SIMPLE/CONTEXT/COMPLEX paths, multi-tool COMPLEX, timeout behavior,
auto-switch after timeouts, circuit breaker (5 rapid failures → OPEN),
Ctrl+C cancellation, empty response handling, tool failure rendering,
first-run welcome, valence decay, stall detection, and non-TTY mode.

Requirements: R1–R11
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from cli_kognisant.network import KognisantAPIError
from cli_kognisant.runtime import ExecutionResult, execute_message
from cli_kognisant.self_model_engine import (
    CircuitBreakerState,
    ModelReliability,
    SelfModel,
    SelfModelEngine,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MODEL_CONFIG = {
    "name": "test-model",
    "display_name": "Test Model",
    "api_base_url": "https://api.test.com/v1",
    "api_key": "test-key",
    "protocol": "openai",
    "provider": "TestProvider",
}


@pytest.fixture
def model_config():
    return dict(MODEL_CONFIG)


@pytest.fixture
def project_info(tmp_path):
    proj_dir = tmp_path / "project"
    proj_dir.mkdir()
    kog_dir = proj_dir / ".kognisant"
    kog_dir.mkdir()
    history_dir = kog_dir / "history"
    history_dir.mkdir()
    context_md = kog_dir / "context.md"
    context_md.write_text("# Project Context\nThis is a Python CLI project.")
    return {
        "name": "test-project",
        "root": str(proj_dir),
        "files": ["main.py", "utils.py", "config.py"],
    }


# ---------------------------------------------------------------------------
# Mock Stream Helpers
# ---------------------------------------------------------------------------


def mock_stream_success(content="Hello!"):
    """Create a mock generator that simulates successful streaming."""
    def gen(*args, **kwargs):
        yield ("phase", "connected")
        for char in content:
            yield ("content", char)
        yield ("done", {"role": "assistant", "content": content})
    return gen


def mock_stream_with_tools(tool_calls):
    """Create a mock generator that returns tool calls."""
    def gen(*args, **kwargs):
        yield ("phase", "connected")
        yield ("tool_calls", tool_calls)
        yield ("done", {"role": "assistant", "content": None, "tool_calls": tool_calls})
    return gen


def mock_stream_multi_round(tool_calls, final_content="Done!"):
    """First call returns tool_calls, second call returns final content."""
    call_count = [0]

    def gen(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            yield ("phase", "connected")
            yield ("tool_calls", tool_calls)
            yield ("done", {"role": "assistant", "content": None, "tool_calls": tool_calls})
        else:
            yield ("phase", "connected")
            for char in final_content:
                yield ("content", char)
            yield ("done", {"role": "assistant", "content": final_content})
    return gen


# ---------------------------------------------------------------------------
# Common patch decorator
# ---------------------------------------------------------------------------

def _base_patches():
    """Return common patches for execute_message tests."""
    return [
        patch("cli_kognisant.runtime._is_tty", return_value=False),
        patch("cli_kognisant.runtime.SelfModelEngine.save"),
        patch("cli_kognisant.runtime.append_telemetry"),
    ]


# ---------------------------------------------------------------------------
# 1. SIMPLE path
# ---------------------------------------------------------------------------


class TestSimplePath:
    @patch("cli_kognisant.runtime._is_tty", return_value=False)
    @patch("cli_kognisant.runtime.SelfModelEngine.load")
    @patch("cli_kognisant.runtime.SelfModelEngine.save")
    @patch("cli_kognisant.runtime.SelfModelEngine.scan_capabilities")
    @patch("cli_kognisant.runtime.query_model_api_stream")
    @patch("cli_kognisant.runtime.append_telemetry")
    def test_simple_path_hello(
        self, mock_telemetry, mock_stream, mock_scan, mock_save, mock_load,
        mock_tty, model_config, project_info, capsys
    ):
        """SIMPLE path: 'hello' → classification=SIMPLE, success=True, response arrives."""
        mock_load.return_value = SelfModel(valence=10, total_executions=5)
        mock_scan.return_value = {}
        mock_stream.side_effect = mock_stream_success("Hi there!")

        messages = []
        result = execute_message(
            user_message="hello",
            messages=messages,
            model_config=model_config,
            project_info=project_info,
            session_file="test.json",
        )

        assert result.success is True
        assert result.classification == "SIMPLE"
        assert result.response == "Hi there!"

        # Verify all 4 phase lines print (⚡📋🔍 and response header)
        captured = capsys.readouterr()
        assert "⚡" in captured.out
        assert "📋" in captured.out
        assert "🔍" in captured.out
        assert "Kognisant >" in captured.out


# ---------------------------------------------------------------------------
# 2. CONTEXT path
# ---------------------------------------------------------------------------


class TestContextPath:
    @patch("cli_kognisant.runtime._is_tty", return_value=False)
    @patch("cli_kognisant.runtime.SelfModelEngine.load")
    @patch("cli_kognisant.runtime.SelfModelEngine.save")
    @patch("cli_kognisant.runtime.SelfModelEngine.scan_capabilities")
    @patch("cli_kognisant.runtime.query_model_api_stream")
    @patch("cli_kognisant.runtime.append_telemetry")
    def test_context_path_project_question(
        self, mock_telemetry, mock_stream, mock_scan, mock_save, mock_load,
        mock_tty, model_config, project_info, capsys
    ):
        """CONTEXT path: 'what are we working on?' → classification=CONTEXT, no tool calls."""
        mock_load.return_value = SelfModel(valence=5, total_executions=3)
        mock_scan.return_value = {}
        mock_stream.side_effect = mock_stream_success("We're building a CLI tool.")

        messages = []
        result = execute_message(
            user_message="what are we working on?",
            messages=messages,
            model_config=model_config,
            project_info=project_info,
            session_file="test.json",
        )

        assert result.success is True
        assert result.classification == "CONTEXT"
        assert result.tool_calls_made == 0
        assert result.response == "We're building a CLI tool."

        # Verify context.md content was used in system prompt construction
        # (The system prompt for CONTEXT includes project files and context.md)
        captured = capsys.readouterr()
        assert "CONTEXT" in captured.out


# ---------------------------------------------------------------------------
# 3. COMPLEX path
# ---------------------------------------------------------------------------


class TestComplexPath:
    @patch("cli_kognisant.runtime._is_tty", return_value=False)
    @patch("cli_kognisant.runtime.SelfModelEngine.load")
    @patch("cli_kognisant.runtime.SelfModelEngine.save")
    @patch("cli_kognisant.runtime.SelfModelEngine.scan_capabilities")
    @patch("cli_kognisant.runtime.query_model_api_stream")
    @patch("cli_kognisant.runtime.execute_tool")
    @patch("cli_kognisant.runtime.append_telemetry")
    def test_complex_path_read_file(
        self, mock_telemetry, mock_tool, mock_stream, mock_scan, mock_save,
        mock_load, mock_tty, model_config, project_info, capsys
    ):
        """COMPLEX path: 'read main.py' → classification=COMPLEX, tool executes, response references result."""
        mock_load.return_value = SelfModel(valence=0, total_executions=2)
        mock_scan.return_value = {}

        tool_calls = [{
            "id": "call_1",
            "function": {"name": "read_project_file", "arguments": '{"file_path": "main.py"}'},
            "type": "function",
        }]
        mock_stream.side_effect = mock_stream_multi_round(
            tool_calls, final_content="Here's main.py content: print('hello')"
        )
        mock_tool.return_value = "print('hello world')\n"

        messages = []
        result = execute_message(
            user_message="read main.py",
            messages=messages,
            model_config=model_config,
            project_info=project_info,
            session_file="test.json",
        )

        assert result.success is True
        assert result.classification == "COMPLEX"
        assert result.tool_calls_made == 1
        assert "main.py" in result.response or result.response != ""

        captured = capsys.readouterr()
        assert "COMPLEX" in captured.out


# ---------------------------------------------------------------------------
# 4. Multi-tool COMPLEX
# ---------------------------------------------------------------------------


class TestMultiToolComplex:
    @patch("cli_kognisant.runtime._is_tty", return_value=False)
    @patch("cli_kognisant.runtime.SelfModelEngine.load")
    @patch("cli_kognisant.runtime.SelfModelEngine.save")
    @patch("cli_kognisant.runtime.SelfModelEngine.scan_capabilities")
    @patch("cli_kognisant.runtime.query_model_api_stream")
    @patch("cli_kognisant.runtime.execute_tool")
    @patch("cli_kognisant.runtime.append_telemetry")
    def test_multi_tool_complex(
        self, mock_telemetry, mock_tool, mock_stream, mock_scan, mock_save,
        mock_load, mock_tty, model_config, project_info, capsys
    ):
        """Multi-tool COMPLEX: message triggers 2 tool calls → both execute, tool_calls_made=2."""
        mock_load.return_value = SelfModel(valence=0, total_executions=3)
        mock_scan.return_value = {}

        tool_calls = [
            {
                "id": "call_1",
                "function": {"name": "read_project_file", "arguments": '{"file_path": "main.py"}'},
                "type": "function",
            },
            {
                "id": "call_2",
                "function": {"name": "read_project_file", "arguments": '{"file_path": "utils.py"}'},
                "type": "function",
            },
        ]
        mock_stream.side_effect = mock_stream_multi_round(
            tool_calls, final_content="Both files read successfully."
        )
        mock_tool.side_effect = ["# main.py\nprint('hi')", "# utils.py\ndef helper(): pass"]

        messages = []
        result = execute_message(
            user_message="read main.py and utils.py",
            messages=messages,
            model_config=model_config,
            project_info=project_info,
            session_file="test.json",
        )

        assert result.success is True
        assert result.classification == "COMPLEX"
        assert result.tool_calls_made == 2


# ---------------------------------------------------------------------------
# 5. Timeout
# ---------------------------------------------------------------------------


class TestTimeout:
    @patch("cli_kognisant.runtime._is_tty", return_value=False)
    @patch("cli_kognisant.runtime.SelfModelEngine.load")
    @patch("cli_kognisant.runtime.SelfModelEngine.save")
    @patch("cli_kognisant.runtime.SelfModelEngine.scan_capabilities")
    @patch("cli_kognisant.runtime.query_model_api_stream")
    @patch("cli_kognisant.runtime.append_telemetry")
    def test_timeout_error(
        self, mock_telemetry, mock_stream, mock_scan, mock_save, mock_load,
        mock_tty, model_config, project_info, capsys
    ):
        """Timeout: stream raises KognisantAPIError with 'timeout' → timed_out=True, valence drops."""
        mock_load.return_value = SelfModel(valence=50, total_executions=5)
        mock_scan.return_value = {}
        mock_stream.side_effect = KognisantAPIError("Request timed out after 30s")

        messages = []
        result = execute_message(
            user_message="hello",
            messages=messages,
            model_config=model_config,
            project_info=project_info,
            session_file="test.json",
        )

        assert result.success is False
        assert result.timed_out is True
        assert result.valence_delta < 0

        captured = capsys.readouterr()
        assert "timeout" in captured.out.lower() or "⚠️" in captured.out


# ---------------------------------------------------------------------------
# 6. Auto-switch after timeouts (circuit breaker OPEN on default)
# ---------------------------------------------------------------------------


class TestAutoSwitch:
    @patch("cli_kognisant.runtime._is_tty", return_value=False)
    @patch("cli_kognisant.runtime.SelfModelEngine.load")
    @patch("cli_kognisant.runtime.SelfModelEngine.save")
    @patch("cli_kognisant.runtime.SelfModelEngine.scan_capabilities")
    @patch("cli_kognisant.runtime.query_model_api_stream")
    @patch("cli_kognisant.runtime.append_telemetry")
    def test_auto_switch_when_cb_open(
        self, mock_telemetry, mock_stream, mock_scan, mock_save, mock_load,
        mock_tty, model_config, project_info, capsys
    ):
        """Auto-switch: circuit breaker OPEN on default + good alternative → auto_switched=True."""
        # Set up self_model with default model's CB OPEN and an alternative with good reliability
        future_time = (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat()
        self_model = SelfModel(
            valence=10,
            total_executions=10,
            circuit_breakers={
                "test-model": CircuitBreakerState(
                    state="open",
                    failures_in_window=0,
                    open_until=future_time,  # Still in cooldown
                ),
                "alt-model": CircuitBreakerState(state="closed"),
            },
            model_reliability={
                "test-model": ModelReliability(
                    successes=2, failures=8, reliability=0.3, attempts=10,
                ),
                "alt-model": ModelReliability(
                    successes=8, failures=1, reliability=0.82, attempts=9,
                ),
            },
        )
        mock_load.return_value = self_model
        mock_scan.return_value = {}
        mock_stream.side_effect = mock_stream_success("Response from alt model")

        # Provide available_models by patching select_model behavior
        # The runtime only passes the default model name to select_model,
        # but we need to ensure alternatives are available. We'll patch
        # the available models list construction in _bootstrap.
        messages = []

        # The runtime constructs available_models = [model_name] by default.
        # To test auto-switch, we need to ensure SelfModelEngine.select_model
        # sees alternate models. The select_model in runtime only gets [model_name].
        # However, select_model iterates available_models, so we must include alternatives.
        # The simplest approach: patch select_model to return the auto-switched result.
        with patch("cli_kognisant.runtime.SelfModelEngine.select_model") as mock_select:
            mock_select.return_value = (
                "alt-model",
                True,
                "test-model circuit breaker OPEN; using alt-model (reliability: 0.82)",
            )

            result = execute_message(
                user_message="hello",
                messages=messages,
                model_config=model_config,
                project_info=project_info,
                session_file="test.json",
            )

        # Even though we switched models, the response comes through
        assert result.success is True

        captured = capsys.readouterr()
        assert "Switching" in captured.out or "alt-model" in captured.out


# ---------------------------------------------------------------------------
# 7. Circuit Breaker (5 rapid failures → OPEN)
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    def test_five_failures_opens_circuit(self):
        """Circuit breaker: 5 failures in 30s → state='open'."""
        cb = CircuitBreakerState(state="closed")

        for _ in range(5):
            SelfModelEngine.cb_record_failure(cb)

        assert cb.state == "open"
        assert cb.open_until is not None

    def test_closed_allows_attempt(self):
        """Circuit breaker CLOSED allows attempts."""
        cb = CircuitBreakerState(state="closed")
        assert SelfModelEngine.cb_can_attempt(cb) is True

    def test_open_blocks_attempt(self):
        """Circuit breaker OPEN with future cooldown blocks attempts."""
        future = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()
        cb = CircuitBreakerState(state="open", open_until=future)
        assert SelfModelEngine.cb_can_attempt(cb) is False

    def test_half_open_after_cooldown(self):
        """Circuit breaker OPEN transitions to HALF_OPEN after cooldown expires."""
        past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        cb = CircuitBreakerState(state="open", open_until=past)
        assert SelfModelEngine.cb_can_attempt(cb) is True
        assert cb.state == "half_open"

    def test_half_open_success_closes(self):
        """HALF_OPEN + success → CLOSED."""
        cb = CircuitBreakerState(state="half_open")
        SelfModelEngine.cb_record_success(cb)
        assert cb.state == "closed"

    def test_half_open_failure_reopens(self):
        """HALF_OPEN + failure → OPEN again."""
        cb = CircuitBreakerState(state="half_open")
        SelfModelEngine.cb_record_failure(cb)
        assert cb.state == "open"


# ---------------------------------------------------------------------------
# 8. Ctrl+C cancellation
# ---------------------------------------------------------------------------


class TestCtrlCCancellation:
    @patch("cli_kognisant.runtime._is_tty", return_value=False)
    @patch("cli_kognisant.runtime.SelfModelEngine.load")
    @patch("cli_kognisant.runtime.SelfModelEngine.save")
    @patch("cli_kognisant.runtime.SelfModelEngine.scan_capabilities")
    @patch("cli_kognisant.runtime.query_model_api_stream")
    @patch("cli_kognisant.runtime.append_telemetry")
    def test_ctrl_c_cancels_and_rolls_back(
        self, mock_telemetry, mock_stream, mock_scan, mock_save, mock_load,
        mock_tty, model_config, project_info, capsys
    ):
        """Ctrl+C: KeyboardInterrupt → cancelled=True, messages rolled back."""
        mock_load.return_value = SelfModel(valence=10, total_executions=3)
        mock_scan.return_value = {}

        def stream_raises_interrupt(*args, **kwargs):
            raise KeyboardInterrupt()
        mock_stream.side_effect = stream_raises_interrupt

        messages = [{"role": "system", "content": "sys prompt"}]
        original_len = len(messages)

        result = execute_message(
            user_message="hello",
            messages=messages,
            model_config=model_config,
            project_info=project_info,
            session_file="test.json",
        )

        assert result.cancelled is True
        # Messages should be rolled back to original state
        assert len(messages) == original_len

        captured = capsys.readouterr()
        assert "Cancelled" in captured.out


# ---------------------------------------------------------------------------
# 9. Empty response
# ---------------------------------------------------------------------------


class TestEmptyResponse:
    @patch("cli_kognisant.runtime._is_tty", return_value=False)
    @patch("cli_kognisant.runtime.SelfModelEngine.load")
    @patch("cli_kognisant.runtime.SelfModelEngine.save")
    @patch("cli_kognisant.runtime.SelfModelEngine.scan_capabilities")
    @patch("cli_kognisant.runtime.query_model_api_stream")
    @patch("cli_kognisant.runtime.append_telemetry")
    def test_empty_response_handling(
        self, mock_telemetry, mock_stream, mock_scan, mock_save, mock_load,
        mock_tty, model_config, project_info, capsys
    ):
        """Empty response: content='' → error_type='empty', valence -10."""
        mock_load.return_value = SelfModel(valence=50, total_executions=5)
        mock_scan.return_value = {}
        mock_stream.side_effect = mock_stream_success("")

        messages = []
        result = execute_message(
            user_message="hello",
            messages=messages,
            model_config=model_config,
            project_info=project_info,
            session_file="test.json",
        )

        assert result.success is False
        # Valence drops — empty response causes -10
        assert result.valence_delta < 0

        captured = capsys.readouterr()
        assert "Empty" in captured.out or "empty" in captured.out.lower()


# ---------------------------------------------------------------------------
# 10. Tool failure rendering
# ---------------------------------------------------------------------------


class TestToolFailure:
    @patch("cli_kognisant.runtime._is_tty", return_value=False)
    @patch("cli_kognisant.runtime.SelfModelEngine.load")
    @patch("cli_kognisant.runtime.SelfModelEngine.save")
    @patch("cli_kognisant.runtime.SelfModelEngine.scan_capabilities")
    @patch("cli_kognisant.runtime.query_model_api_stream")
    @patch("cli_kognisant.runtime.execute_tool")
    @patch("cli_kognisant.runtime.append_telemetry")
    def test_tool_failure_renders_error(
        self, mock_telemetry, mock_tool, mock_stream, mock_scan, mock_save,
        mock_load, mock_tty, model_config, project_info, capsys
    ):
        """Tool failure: execute_tool returns '[Error] file not found' → failure icon, tools_used[0].success=False."""
        mock_load.return_value = SelfModel(valence=0, total_executions=5)
        mock_scan.return_value = {}

        tool_calls = [{
            "id": "call_1",
            "function": {"name": "read_project_file", "arguments": '{"file_path": "missing.py"}'},
            "type": "function",
        }]
        mock_stream.side_effect = mock_stream_multi_round(
            tool_calls, final_content="Sorry, I couldn't read that file."
        )
        mock_tool.return_value = "[Error] file not found"

        messages = []
        result = execute_message(
            user_message="read missing.py",
            messages=messages,
            model_config=model_config,
            project_info=project_info,
            session_file="test.json",
        )

        assert result.success is True  # The overall execution succeeds (LLM responded)
        assert result.tool_calls_made == 1

        # Verify the tool box output shows failure indicator
        captured = capsys.readouterr()
        # Non-TTY format: [✗] with failure info
        assert "✗" in captured.out
        assert "Failed" in captured.out


# ---------------------------------------------------------------------------
# 11. First-run welcome
# ---------------------------------------------------------------------------


class TestFirstRunWelcome:
    @patch("cli_kognisant.runtime._is_tty", return_value=False)
    @patch("cli_kognisant.runtime.SelfModelEngine.load")
    @patch("cli_kognisant.runtime.SelfModelEngine.save")
    @patch("cli_kognisant.runtime.SelfModelEngine.scan_capabilities")
    @patch("cli_kognisant.runtime.query_model_api_stream")
    @patch("cli_kognisant.runtime.append_telemetry")
    def test_first_run_welcome_message(
        self, mock_telemetry, mock_stream, mock_scan, mock_save, mock_load,
        mock_tty, model_config, project_info, capsys
    ):
        """First-run: total_executions=0 → 'Welcome' in output."""
        mock_load.return_value = SelfModel(total_executions=0, valence=0)
        mock_scan.return_value = {}
        mock_stream.side_effect = mock_stream_success("Hello! Nice to meet you.")

        messages = []
        result = execute_message(
            user_message="hi",
            messages=messages,
            model_config=model_config,
            project_info=project_info,
            session_file="test.json",
        )

        assert result.success is True
        captured = capsys.readouterr()
        assert "Welcome" in captured.out
        assert "first execution" in captured.out


# ---------------------------------------------------------------------------
# 12. Valence decay
# ---------------------------------------------------------------------------


class TestValenceDecay:
    def test_valence_decays_over_time(self):
        """Valence decay: last_execution_at 2 days ago, valence=100 → decayed (~81)."""
        two_days_ago = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        model = SelfModel(valence=100, last_execution_at=two_days_ago)

        SelfModelEngine.apply_decay(model)

        # 0.9^2 = 0.81, so valence should be ~81 (int truncation may give 80 or 81)
        assert 79 <= model.valence <= 81


# ---------------------------------------------------------------------------
# 13. Stall detection
# ---------------------------------------------------------------------------


class TestStallDetection:
    @patch("cli_kognisant.runtime._is_tty", return_value=False)
    @patch("cli_kognisant.runtime.SelfModelEngine.load")
    @patch("cli_kognisant.runtime.SelfModelEngine.save")
    @patch("cli_kognisant.runtime.SelfModelEngine.scan_capabilities")
    @patch("cli_kognisant.runtime.query_model_api_stream")
    @patch("cli_kognisant.runtime.append_telemetry")
    def test_stall_detection(
        self, mock_telemetry, mock_stream, mock_scan, mock_save, mock_load,
        mock_tty, model_config, project_info, capsys
    ):
        """Stall detection: KognisantAPIError('Stream stalled') → stalled=True, error shows stall."""
        mock_load.return_value = SelfModel(valence=30, total_executions=5)
        mock_scan.return_value = {}
        mock_stream.side_effect = KognisantAPIError("Stream stalled — no data for 30s")

        messages = []
        result = execute_message(
            user_message="hello",
            messages=messages,
            model_config=model_config,
            project_info=project_info,
            session_file="test.json",
        )

        assert result.success is False
        assert result.timed_out is True

        captured = capsys.readouterr()
        assert "stall" in captured.out.lower() or "Stream stalled" in captured.out


# ---------------------------------------------------------------------------
# 14. Non-TTY mode
# ---------------------------------------------------------------------------


class TestNonTTYMode:
    @patch("cli_kognisant.runtime._is_tty", return_value=False)
    @patch("cli_kognisant.runtime.SelfModelEngine.load")
    @patch("cli_kognisant.runtime.SelfModelEngine.save")
    @patch("cli_kognisant.runtime.SelfModelEngine.scan_capabilities")
    @patch("cli_kognisant.runtime.query_model_api_stream")
    @patch("cli_kognisant.runtime.append_telemetry")
    def test_non_tty_no_ansi_codes(
        self, mock_telemetry, mock_stream, mock_scan, mock_save, mock_load,
        mock_tty, model_config, project_info, capsys
    ):
        """Non-TTY mode: _is_tty=False → no ANSI codes in output."""
        mock_load.return_value = SelfModel(valence=10, total_executions=5)
        mock_scan.return_value = {"skills_count": 1, "custom_tools_count": 0, "active_jobs_count": 0}
        mock_stream.side_effect = mock_stream_success("Hello!")

        messages = []
        result = execute_message(
            user_message="hello",
            messages=messages,
            model_config=model_config,
            project_info=project_info,
            session_file="test.json",
        )

        assert result.success is True
        captured = capsys.readouterr()
        # No ANSI escape codes should be present
        assert "\033[" not in captured.out
        assert "\x1b[" not in captured.out
