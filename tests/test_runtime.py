"""Integration tests for cli_kognisant.runtime module.

Tests the 5-phase execution lifecycle: Bootstrap → Plan → Execute → Reflect → Persist.
"""

import json
import os
import sys
import threading
import time
from dataclasses import asdict
from unittest.mock import MagicMock, patch, call

import pytest

from cli_kognisant.runtime import (
    ExecutionContext,
    ExecutionResult,
    execute_message,
    _bootstrap,
    _plan,
    _execute,
    _reflect,
    _persist,
    _rollback,
    _render_tool_box,
    _get_tool_label,
    _get_result_summary,
    _is_tty,
)
from cli_kognisant.self_model_engine import SelfModel, SelfModelEngine
from cli_kognisant.colors import Spinner


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def model_config():
    """Minimal model config for testing."""
    return {
        "name": "test-model",
        "display_name": "Test Model",
        "api_base_url": "https://api.test.com/v1",
        "api_key": "test-key-123",
        "protocol": "openai",
        "provider": "TestProvider",
    }


@pytest.fixture
def project_info(tmp_path):
    """Mock project info."""
    proj_dir = tmp_path / "test_project"
    proj_dir.mkdir()
    kog_dir = proj_dir / ".kognisant"
    kog_dir.mkdir()
    history_dir = kog_dir / "history"
    history_dir.mkdir()
    return {
        "name": "test-project",
        "root": str(proj_dir),
        "files": ["main.py", "utils.py"],
    }


@pytest.fixture
def basic_ctx(model_config, project_info):
    """A basic ExecutionContext for unit-testing individual phases."""
    return ExecutionContext(
        user_message="hello",
        messages=[],
        model_config=model_config,
        project_info=project_info,
        session_file="test_session.json",
        checkpoint_idx=0,
    )


def _mock_stream_success(content="Hello! I'm Kognisant."):
    """Create a mock generator that simulates successful streaming."""
    def mock_gen(*args, **kwargs):
        yield ("phase", "connected")
        for chunk in content:
            yield ("content", chunk)
        yield ("done", {"role": "assistant", "content": content})
    return mock_gen


def _mock_stream_with_tools():
    """Create a mock generator that returns tool calls."""
    def mock_gen(*args, **kwargs):
        yield ("phase", "connected")
        tool_calls = [{
            "id": "call_123",
            "function": {"name": "read_project_file", "arguments": '{"file_path": "main.py"}'},
            "type": "function",
        }]
        yield ("tool_calls", tool_calls)
        yield ("done", {
            "role": "assistant",
            "content": None,
            "tool_calls": tool_calls,
        })
    return mock_gen


# ---------------------------------------------------------------------------
# Test: Dataclasses
# ---------------------------------------------------------------------------

class TestDataclasses:
    def test_execution_context_defaults(self, model_config):
        ctx = ExecutionContext(
            user_message="hi",
            messages=[],
            model_config=model_config,
            project_info=None,
            session_file=None,
            checkpoint_idx=0,
        )
        assert ctx.classification == ""
        assert ctx.success is False
        assert ctx.tools is None
        assert ctx.timeout == 120
        assert ctx.phase_times == {}
        assert ctx.tools_used == []

    def test_execution_result_fields(self):
        result = ExecutionResult(
            success=True,
            response="Hello!",
            streamed=True,
            error=None,
            classification="SIMPLE",
            model_used="test-model",
            response_time=1.5,
            tool_calls_made=0,
            valence_delta=5,
            timed_out=False,
            cancelled=False,
            tokens_in=100,
            tokens_out=50,
        )
        assert result.success is True
        assert result.response == "Hello!"
        assert result.classification == "SIMPLE"
        assert result.valence_delta == 5


# ---------------------------------------------------------------------------
# Test: Bootstrap Phase
# ---------------------------------------------------------------------------

class TestBootstrap:
    @patch("cli_kognisant.runtime.SelfModelEngine.load")
    @patch("cli_kognisant.runtime.SelfModelEngine.scan_capabilities")
    @patch("cli_kognisant.runtime._is_tty", return_value=False)
    def test_bootstrap_prints_phase_line(self, mock_tty, mock_scan, mock_load,
                                         basic_ctx, capsys):
        mock_load.return_value = SelfModel(valence=10, total_executions=5)
        mock_scan.return_value = {"skills_count": 2, "custom_tools_count": 1, "active_jobs_count": 0}

        _bootstrap(basic_ctx)
        captured = capsys.readouterr()
        assert "⚡" in captured.out
        assert "Test Model" in captured.out
        assert "valence:" in captured.out


    @patch("cli_kognisant.runtime.SelfModelEngine.load")
    @patch("cli_kognisant.runtime.SelfModelEngine.scan_capabilities")
    @patch("cli_kognisant.runtime._is_tty", return_value=False)
    def test_bootstrap_first_run(self, mock_tty, mock_scan, mock_load,
                                  basic_ctx, capsys):
        mock_load.return_value = SelfModel(total_executions=0)
        mock_scan.return_value = {}

        _bootstrap(basic_ctx)
        captured = capsys.readouterr()
        assert "Welcome" in captured.out
        assert "first execution" in captured.out

    @patch("cli_kognisant.runtime.SelfModelEngine.load")
    @patch("cli_kognisant.runtime.SelfModelEngine.scan_capabilities")
    @patch("cli_kognisant.runtime._is_tty", return_value=False)
    def test_bootstrap_records_phase_time(self, mock_tty, mock_scan, mock_load,
                                          basic_ctx):
        mock_load.return_value = SelfModel(valence=5, total_executions=3)
        mock_scan.return_value = {}

        _bootstrap(basic_ctx)
        assert "bootstrap" in basic_ctx.phase_times
        assert basic_ctx.phase_times["bootstrap"] >= 0


# ---------------------------------------------------------------------------
# Test: Plan Phase
# ---------------------------------------------------------------------------

class TestPlan:
    @patch("cli_kognisant.runtime._is_tty", return_value=False)
    def test_plan_simple_classification(self, mock_tty, basic_ctx, capsys):
        basic_ctx.user_message = "hi"
        basic_ctx.self_model = SelfModel()
        basic_ctx.active_model = {"name": "test-model"}

        _plan(basic_ctx)
        assert basic_ctx.classification == "SIMPLE"
        assert basic_ctx.timeout == 30
        assert basic_ctx.tools is None
        captured = capsys.readouterr()
        assert "📋" in captured.out
        assert "SIMPLE" in captured.out

    @patch("cli_kognisant.runtime._is_tty", return_value=False)
    def test_plan_complex_classification(self, mock_tty, basic_ctx, capsys):
        basic_ctx.user_message = "refactor the authentication module in auth.py"
        basic_ctx.self_model = SelfModel()
        basic_ctx.active_model = {"name": "test-model"}

        _plan(basic_ctx)
        assert basic_ctx.classification == "COMPLEX"
        assert basic_ctx.timeout == 120
        assert basic_ctx.tools is not None
        captured = capsys.readouterr()
        assert "COMPLEX" in captured.out


    @patch("cli_kognisant.runtime._is_tty", return_value=False)
    def test_plan_token_breakdown(self, mock_tty, basic_ctx, capsys):
        basic_ctx.user_message = "explain the project structure and recent changes"
        basic_ctx.self_model = SelfModel()
        basic_ctx.active_model = {"name": "test-model"}

        _plan(basic_ctx)
        assert "total" in basic_ctx.token_breakdown
        assert basic_ctx.total_tokens_in > 0

    @patch("cli_kognisant.runtime._is_tty", return_value=False)
    def test_plan_disables_tools_when_capability_off(self, mock_tty, basic_ctx, capsys):
        basic_ctx.user_message = "fix the bug in network.py please"
        basic_ctx.self_model = SelfModel()
        basic_ctx.active_model = {"name": "test-model", "_tool_calling_disabled": True}

        _plan(basic_ctx)
        assert basic_ctx.classification == "COMPLEX"
        assert basic_ctx.tools is None  # Disabled by persisted capability


# ---------------------------------------------------------------------------
# Test: Execute Phase
# ---------------------------------------------------------------------------

class TestExecute:
    @patch("cli_kognisant.runtime._is_tty", return_value=False)
    @patch("cli_kognisant.runtime.query_model_api_stream")
    def test_execute_successful_stream(self, mock_stream, mock_tty, basic_ctx, capsys):
        mock_stream.side_effect = _mock_stream_success("Hello world!")
        basic_ctx.active_model = {
            "name": "test-model",
            "display_name": "Test",
            "api_base_url": "https://api.test.com",
            "api_key": "key",
            "protocol": "openai",
        }
        basic_ctx.classification = "SIMPLE"
        basic_ctx.api_messages = [{"role": "system", "content": "test"}]
        basic_ctx.timeout = 30

        _execute(basic_ctx)

        assert basic_ctx.success is True
        assert basic_ctx.response == "Hello world!"
        assert basic_ctx.streamed is True
        captured = capsys.readouterr()
        assert "Kognisant >" in captured.out

    @patch("cli_kognisant.runtime._is_tty", return_value=False)
    @patch("cli_kognisant.runtime.query_model_api_stream")
    def test_execute_empty_response(self, mock_stream, mock_tty, basic_ctx, capsys):
        mock_stream.side_effect = _mock_stream_success("")
        basic_ctx.active_model = {
            "name": "test-model",
            "display_name": "Test",
            "api_base_url": "https://api.test.com",
            "api_key": "key",
            "protocol": "openai",
        }
        basic_ctx.classification = "SIMPLE"
        basic_ctx.api_messages = [{"role": "system", "content": "test"}]
        basic_ctx.timeout = 30

        _execute(basic_ctx)

        assert basic_ctx.success is False
        assert basic_ctx.error_type == "empty"


    @patch("cli_kognisant.runtime._is_tty", return_value=False)
    @patch("cli_kognisant.runtime.query_model_api_stream")
    def test_execute_api_error_rollback(self, mock_stream, mock_tty, basic_ctx, capsys):
        from cli_kognisant.network import KognisantAPIError
        mock_stream.side_effect = KognisantAPIError("Connection refused")
        basic_ctx.active_model = {
            "name": "test-model",
            "display_name": "Test",
            "api_base_url": "https://api.test.com",
            "api_key": "key",
            "protocol": "openai",
        }
        basic_ctx.classification = "SIMPLE"
        basic_ctx.api_messages = [{"role": "system", "content": "test"}]
        basic_ctx.timeout = 30
        basic_ctx.checkpoint_idx = 0

        _execute(basic_ctx)

        # Messages should be rolled back
        assert len(basic_ctx.messages) == 0
        assert basic_ctx.error is not None
        assert "endpoint" in basic_ctx.error

    @patch("cli_kognisant.runtime._is_tty", return_value=False)
    @patch("cli_kognisant.runtime.query_model_api_stream")
    @patch("cli_kognisant.runtime.execute_tool")
    def test_execute_tool_loop(self, mock_tool, mock_stream, mock_tty,
                                basic_ctx, capsys):
        """Test that tool calls are executed and results appended."""
        # First call returns tool_calls, second call returns content
        call_count = [0]

        def stream_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                yield ("phase", "connected")
                tool_calls = [{
                    "id": "call_1",
                    "function": {"name": "read_project_file", "arguments": '{"file_path": "main.py"}'},
                    "type": "function",
                }]
                yield ("tool_calls", tool_calls)
                yield ("done", {"role": "assistant", "content": None, "tool_calls": tool_calls})
            else:
                yield ("phase", "connected")
                yield ("content", "Done reading!")
                yield ("done", {"role": "assistant", "content": "Done reading!"})

        mock_stream.side_effect = stream_side_effect
        mock_tool.return_value = "# Main module\nprint('hello')"

        basic_ctx.active_model = {
            "name": "test-model",
            "display_name": "Test",
            "api_base_url": "https://api.test.com",
            "api_key": "key",
            "protocol": "openai",
        }
        basic_ctx.classification = "COMPLEX"
        basic_ctx.tools = [{"type": "function", "function": {"name": "read_project_file"}}]
        basic_ctx.api_messages = [{"role": "system", "content": "test"}]
        basic_ctx.timeout = 120
        basic_ctx.self_model = SelfModel()

        _execute(basic_ctx)

        assert basic_ctx.success is True
        assert basic_ctx.tool_calls_made == 1
        assert len(basic_ctx.tools_used) == 1
        assert basic_ctx.tools_used[0]["name"] == "read_project_file"
        assert basic_ctx.tools_used[0]["success"] is True


# ---------------------------------------------------------------------------
# Test: Reflect Phase
# ---------------------------------------------------------------------------

class TestReflect:
    @patch("cli_kognisant.runtime._is_tty", return_value=False)
    @patch("cli_kognisant.runtime.append_telemetry")
    def test_reflect_success_prints_line(self, mock_telemetry, mock_tty,
                                         basic_ctx, capsys):
        basic_ctx.self_model = SelfModel(valence=10, total_executions=4)
        basic_ctx.active_model = {"name": "test-model", "provider": "test"}
        basic_ctx.success = True
        basic_ctx.response_time = 2.5
        basic_ctx.response = "Hello world!"
        basic_ctx.total_tokens_in = 100
        basic_ctx.total_tokens_out = 50
        basic_ctx.classification = "SIMPLE"

        valence_delta = _reflect(basic_ctx)
        captured = capsys.readouterr()
        assert "🔍" in captured.out
        assert "2.5s" in captured.out
        assert valence_delta > 0  # Success + fast response → positive delta

    @patch("cli_kognisant.runtime._is_tty", return_value=False)
    @patch("cli_kognisant.runtime.append_telemetry")
    def test_reflect_timeout_shows_timeout(self, mock_telemetry, mock_tty,
                                            basic_ctx, capsys):
        basic_ctx.self_model = SelfModel(valence=10, total_executions=4)
        basic_ctx.active_model = {"name": "test-model", "provider": "test"}
        basic_ctx.success = False
        basic_ctx.timed_out = True
        basic_ctx.error_type = "timeout"
        basic_ctx.response_time = 120.0
        basic_ctx.response = ""
        basic_ctx.total_tokens_in = 100
        basic_ctx.classification = "COMPLEX"

        valence_delta = _reflect(basic_ctx)
        captured = capsys.readouterr()
        assert "TIMEOUT" in captured.out
        assert valence_delta < 0  # Timeout → negative delta

    @patch("cli_kognisant.runtime._is_tty", return_value=False)
    @patch("cli_kognisant.runtime.append_telemetry")
    def test_reflect_appends_telemetry(self, mock_telemetry, mock_tty, basic_ctx):
        basic_ctx.self_model = SelfModel(valence=0, total_executions=1)
        basic_ctx.active_model = {"name": "test-model", "provider": "test"}
        basic_ctx.success = True
        basic_ctx.response_time = 5.0
        basic_ctx.response = "test"
        basic_ctx.total_tokens_in = 50
        basic_ctx.classification = "SIMPLE"

        _reflect(basic_ctx)
        mock_telemetry.assert_called_once()
        record = mock_telemetry.call_args[0][0]
        assert record["model"] == "test-model"
        assert record["success"] is True
        assert record["classification"] == "SIMPLE"


# ---------------------------------------------------------------------------
# Test: Tool Box Rendering
# ---------------------------------------------------------------------------

class TestToolBoxRendering:
    def test_render_tool_box_success(self):
        box = _render_tool_box("Read main.py", "✓", 45.0, "1.2KB read", "\033[38;2;39;174;96m", is_tty=True)
        assert "┌" in box
        assert "Read main.py" in box
        assert "✓" in box
        assert "45ms" in box
        assert "1.2KB read" in box
        assert "└" in box

    def test_render_tool_box_failure(self):
        box = _render_tool_box("Failed to read auth.py", "✗", 120.0, "not found", "\033[38;2;231;76;60m", is_tty=True)
        assert "Failed to read auth.py" in box
        assert "✗" in box

    def test_render_tool_box_non_tty(self):
        box = _render_tool_box("Read file", "✓", 30.0, "done", "", is_tty=False)
        # No ANSI codes
        assert "\033[" not in box
        assert "┌" in box

    def test_get_tool_label_progress(self):
        label = _get_tool_label("read_project_file", {"file_path": "main.py"}, "progress")
        assert "Reading" in label
        assert "main.py" in label

    def test_get_tool_label_success(self):
        label = _get_tool_label("read_project_file", {"file_path": "main.py"}, "success")
        assert "Read" in label
        assert "main.py" in label

    def test_get_tool_label_failure(self):
        label = _get_tool_label("read_project_file", {"file_path": "main.py"}, "failure")
        assert "Failed" in label

    def test_get_result_summary_read(self):
        result = "x" * 1024
        summary = _get_result_summary("read_project_file", result)
        assert "KB read" in summary

    def test_get_result_summary_error(self):
        summary = _get_result_summary("read_project_file", "[Error] file not found")
        assert "[Error]" in summary


# ---------------------------------------------------------------------------
# Test: Rollback
# ---------------------------------------------------------------------------

class TestRollback:
    def test_rollback_restores_messages(self, basic_ctx):
        basic_ctx.messages = [{"role": "system", "content": "sys"}]
        basic_ctx.checkpoint_idx = 1

        # Simulate adding messages during execution
        basic_ctx.messages.append({"role": "user", "content": "hello"})
        basic_ctx.messages.append({"role": "assistant", "content": "hi"})
        assert len(basic_ctx.messages) == 3

        _rollback(basic_ctx)
        assert len(basic_ctx.messages) == 1
        assert basic_ctx.messages[0]["role"] == "system"


# ---------------------------------------------------------------------------
# Test: Full execute_message Integration
# ---------------------------------------------------------------------------

class TestExecuteMessage:
    @patch("cli_kognisant.runtime._is_tty", return_value=False)
    @patch("cli_kognisant.runtime.SelfModelEngine.load")
    @patch("cli_kognisant.runtime.SelfModelEngine.save")
    @patch("cli_kognisant.runtime.SelfModelEngine.scan_capabilities")
    @patch("cli_kognisant.runtime.query_model_api_stream")
    @patch("cli_kognisant.runtime.append_telemetry")
    def test_full_lifecycle_success(self, mock_telemetry, mock_stream, mock_scan,
                                     mock_save, mock_load, mock_tty,
                                     model_config, project_info, capsys):
        mock_load.return_value = SelfModel(valence=5, total_executions=2)
        mock_scan.return_value = {"skills_count": 1, "custom_tools_count": 0, "active_jobs_count": 0}
        mock_stream.side_effect = _mock_stream_success("Hi there!")

        messages = []
        result = execute_message(
            user_message="hello",
            messages=messages,
            model_config=model_config,
            project_info=project_info,
            session_file="test.json",
        )

        assert result.success is True
        assert result.response == "Hi there!"
        assert result.streamed is True
        assert result.classification == "SIMPLE"
        assert result.model_used == "test-model"
        assert result.timed_out is False
        assert result.cancelled is False
        assert result.valence_delta > 0

        # Verify phase output printed
        captured = capsys.readouterr()
        assert "⚡" in captured.out
        assert "📋" in captured.out
        assert "🔍" in captured.out


    @patch("cli_kognisant.runtime._is_tty", return_value=False)
    @patch("cli_kognisant.runtime.SelfModelEngine.load")
    @patch("cli_kognisant.runtime.SelfModelEngine.save")
    @patch("cli_kognisant.runtime.SelfModelEngine.scan_capabilities")
    @patch("cli_kognisant.runtime.query_model_api_stream")
    @patch("cli_kognisant.runtime.append_telemetry")
    def test_full_lifecycle_error_rollback(self, mock_telemetry, mock_stream, mock_scan,
                                           mock_save, mock_load, mock_tty,
                                           model_config, project_info, capsys):
        from cli_kognisant.network import KognisantAPIError
        mock_load.return_value = SelfModel(valence=0, total_executions=3)
        mock_scan.return_value = {}
        mock_stream.side_effect = KognisantAPIError("HTTP Error 401: Unauthorized")

        messages = [{"role": "system", "content": "sys prompt"}]
        result = execute_message(
            user_message="test message",
            messages=messages,
            model_config=model_config,
            project_info=project_info,
            session_file="test.json",
        )

        assert result.success is False
        assert result.error is not None
        assert "API key" in result.error
        # Messages rolled back to checkpoint
        assert len(messages) == 1

    @patch("cli_kognisant.runtime._is_tty", return_value=False)
    @patch("cli_kognisant.runtime.SelfModelEngine.load")
    @patch("cli_kognisant.runtime.SelfModelEngine.save")
    @patch("cli_kognisant.runtime.SelfModelEngine.scan_capabilities")
    @patch("cli_kognisant.runtime.query_model_api_stream")
    @patch("cli_kognisant.runtime.append_telemetry")
    def test_execute_message_returns_correct_result(self, mock_telemetry, mock_stream,
                                                     mock_scan, mock_save, mock_load,
                                                     mock_tty, model_config,
                                                     project_info):
        mock_load.return_value = SelfModel(valence=-10, total_executions=10)
        mock_scan.return_value = {}
        mock_stream.side_effect = _mock_stream_success("Response text")

        messages = []
        result = execute_message(
            user_message="hi",
            messages=messages,
            model_config=model_config,
            project_info=project_info,
            session_file="test.json",
        )

        assert isinstance(result, ExecutionResult)
        assert result.tokens_in > 0
        assert result.tokens_out > 0
        assert result.response_time > 0


# ---------------------------------------------------------------------------
# Test: Spinner Modifications (colors.py)
# ---------------------------------------------------------------------------

class TestSpinnerUpdates:
    def test_spinner_has_update_message(self):
        spinner = Spinner(message="test", show_elapsed=False)
        assert hasattr(spinner, "update_message")
        spinner.update_message("new message")
        assert spinner.message == "new message"

    def test_spinner_accepts_timeout(self):
        spinner = Spinner(message="test", show_elapsed=True, timeout=30)
        assert spinner.timeout == 30

    def test_spinner_timeout_none_default(self):
        spinner = Spinner(message="test")
        assert spinner.timeout is None
