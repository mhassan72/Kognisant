"""Tests for cli_kognisant/telemetry.py.

Covers: estimate_tokens, compute_token_breakdown, append_telemetry,
rotate_if_needed, load_recent_telemetry, aggregate_telemetry,
format_telemetry_summary, format_model_telemetry.
"""

import json
import os
import tempfile

import pytest

from cli_kognisant import telemetry


# --- Fixtures ---


@pytest.fixture
def tmp_telemetry_dir(tmp_path, monkeypatch):
    """Redirect telemetry to a temp directory."""
    telemetry_file = str(tmp_path / "telemetry.jsonl")
    backup_file = str(tmp_path / "telemetry.1.jsonl")
    monkeypatch.setattr(telemetry, "TELEMETRY_DIR", str(tmp_path))
    monkeypatch.setattr(telemetry, "TELEMETRY_FILE", telemetry_file)
    monkeypatch.setattr(telemetry, "TELEMETRY_BACKUP", backup_file)
    return tmp_path


@pytest.fixture
def sample_records():
    """A set of sample telemetry records for aggregation tests."""
    return [
        {
            "timestamp": "2024-01-01T00:00:00",
            "model": "gpt-4",
            "classification": "COMPLEX",
            "success": True,
            "response_time_ms": 2000,
            "tokens_in": 1500,
            "tokens_out": 500,
            "tool_calls": [{"name": "read_project_file", "success": True}],
            "valence_after": 5,
            "timed_out": False,
        },
        {
            "timestamp": "2024-01-01T00:01:00",
            "model": "gpt-4",
            "classification": "CONTEXT",
            "success": True,
            "response_time_ms": 1000,
            "tokens_in": 800,
            "tokens_out": 200,
            "tool_calls": [],
            "valence_after": 8,
            "timed_out": False,
        },
        {
            "timestamp": "2024-01-01T00:02:00",
            "model": "claude-3",
            "classification": "SIMPLE",
            "success": False,
            "response_time_ms": 5000,
            "tokens_in": 100,
            "tokens_out": 0,
            "tool_calls": [{"name": "shell_execution", "success": False}],
            "valence_after": 3,
            "timed_out": True,
            "error": "timeout",
        },
        {
            "timestamp": "2024-01-01T00:03:00",
            "model": "gpt-4",
            "classification": "COMPLEX",
            "success": True,
            "response_time_ms": 1500,
            "tokens_in": 2000,
            "tokens_out": 800,
            "tool_calls": [
                {"name": "read_project_file", "success": True},
                {"name": "edit_project_file", "success": True},
            ],
            "valence_after": 12,
            "timed_out": False,
        },
    ]


# --- estimate_tokens ---


class TestEstimateTokens:
    def test_empty_string(self):
        assert telemetry.estimate_tokens("") == 0

    def test_short_text(self):
        # "hello" = 5 chars → 5 // 4 = 1
        assert telemetry.estimate_tokens("hello") == 1

    def test_longer_text(self):
        text = "a" * 100
        assert telemetry.estimate_tokens(text) == 25

    def test_exact_multiple(self):
        text = "a" * 8
        assert telemetry.estimate_tokens(text) == 2

    def test_not_exact_multiple(self):
        text = "a" * 9
        assert telemetry.estimate_tokens(text) == 2  # 9 // 4 = 2


# --- compute_token_breakdown ---


class TestComputeTokenBreakdown:
    def test_basic_breakdown(self):
        result = telemetry.compute_token_breakdown(
            system_prompt="a" * 100,  # 25 tokens
            tools_json="b" * 40,      # 10 tokens
            history_msgs=[{"content": "c" * 20}],  # 5 tokens
            user_msg="d" * 12,        # 3 tokens
        )
        assert result == {
            "system": 25,
            "tools": 10,
            "history": 5,
            "user_message": 3,
            "total": 43,
        }

    def test_none_tools(self):
        result = telemetry.compute_token_breakdown(
            system_prompt="hello world",
            tools_json=None,
            history_msgs=[],
            user_msg="hi",
        )
        assert result["tools"] == 0
        assert result["total"] == result["system"] + result["user_message"]

    def test_multiple_history_messages(self):
        history = [
            {"content": "a" * 8},   # 2
            {"content": "b" * 12},  # 3
            {"content": "c" * 4},   # 1
        ]
        result = telemetry.compute_token_breakdown(
            system_prompt="",
            tools_json=None,
            history_msgs=history,
            user_msg="",
        )
        assert result["history"] == 6
        assert result["total"] == 6

    def test_history_with_none_content(self):
        history = [
            {"content": "a" * 8},
            {"content": None},
            {"role": "assistant"},  # no content key
        ]
        result = telemetry.compute_token_breakdown(
            system_prompt="",
            tools_json=None,
            history_msgs=history,
            user_msg="",
        )
        assert result["history"] == 2  # only first message counts

    def test_empty_inputs(self):
        result = telemetry.compute_token_breakdown(
            system_prompt="",
            tools_json=None,
            history_msgs=[],
            user_msg="",
        )
        assert result == {
            "system": 0,
            "tools": 0,
            "history": 0,
            "user_message": 0,
            "total": 0,
        }


# --- append_telemetry ---


class TestAppendTelemetry:
    def test_appends_json_line(self, tmp_telemetry_dir):
        record = {"model": "gpt-4", "success": True}
        telemetry.append_telemetry(record)

        file_path = str(tmp_telemetry_dir / "telemetry.jsonl")
        assert os.path.exists(file_path)
        with open(file_path, "r") as f:
            lines = f.readlines()
        assert len(lines) == 1
        assert json.loads(lines[0]) == record

    def test_appends_multiple_records(self, tmp_telemetry_dir):
        telemetry.append_telemetry({"n": 1})
        telemetry.append_telemetry({"n": 2})
        telemetry.append_telemetry({"n": 3})

        file_path = str(tmp_telemetry_dir / "telemetry.jsonl")
        with open(file_path, "r") as f:
            lines = f.readlines()
        assert len(lines) == 3
        assert json.loads(lines[0])["n"] == 1
        assert json.loads(lines[2])["n"] == 3

    def test_never_raises(self, monkeypatch):
        """append_telemetry should swallow all exceptions."""
        # Point to an invalid path
        monkeypatch.setattr(telemetry, "TELEMETRY_DIR", "/nonexistent/path/xyz")
        monkeypatch.setattr(telemetry, "TELEMETRY_FILE", "/nonexistent/path/xyz/t.jsonl")
        # Should not raise
        telemetry.append_telemetry({"test": True})

    def test_creates_directory_if_missing(self, tmp_telemetry_dir):
        # Remove the dir
        new_dir = str(tmp_telemetry_dir / "subdir")
        telemetry.TELEMETRY_DIR = new_dir
        telemetry.TELEMETRY_FILE = os.path.join(new_dir, "telemetry.jsonl")

        telemetry.append_telemetry({"created": True})
        assert os.path.exists(os.path.join(new_dir, "telemetry.jsonl"))


# --- rotate_if_needed ---


class TestRotateIfNeeded:
    def test_no_rotation_under_limit(self, tmp_telemetry_dir):
        file_path = str(tmp_telemetry_dir / "telemetry.jsonl")
        # Write a small file
        with open(file_path, "w") as f:
            f.write("small content\n")

        telemetry.rotate_if_needed()
        # File should still exist, no backup
        assert os.path.exists(file_path)
        assert not os.path.exists(str(tmp_telemetry_dir / "telemetry.1.jsonl"))

    def test_rotation_over_limit(self, tmp_telemetry_dir):
        file_path = str(tmp_telemetry_dir / "telemetry.jsonl")
        backup_path = str(tmp_telemetry_dir / "telemetry.1.jsonl")

        # Write a file larger than 5MB
        with open(file_path, "w") as f:
            f.write("x" * (5 * 1024 * 1024 + 1))

        telemetry.rotate_if_needed()

        # Original should be gone, backup should exist
        assert not os.path.exists(file_path)
        assert os.path.exists(backup_path)

    def test_rotation_overwrites_old_backup(self, tmp_telemetry_dir):
        file_path = str(tmp_telemetry_dir / "telemetry.jsonl")
        backup_path = str(tmp_telemetry_dir / "telemetry.1.jsonl")

        # Create an existing backup
        with open(backup_path, "w") as f:
            f.write("old backup content\n")

        # Write a large main file
        with open(file_path, "w") as f:
            f.write("y" * (6 * 1024 * 1024))

        telemetry.rotate_if_needed()

        assert not os.path.exists(file_path)
        assert os.path.exists(backup_path)
        with open(backup_path, "r") as f:
            content = f.read()
        assert content.startswith("y")  # New content, not old

    def test_no_file_no_error(self, tmp_telemetry_dir):
        """Should not raise if telemetry file doesn't exist."""
        telemetry.rotate_if_needed()


# --- load_recent_telemetry ---


class TestLoadRecentTelemetry:
    def test_empty_when_no_file(self, tmp_telemetry_dir):
        result = telemetry.load_recent_telemetry()
        assert result == []

    def test_loads_all_when_under_count(self, tmp_telemetry_dir):
        file_path = str(tmp_telemetry_dir / "telemetry.jsonl")
        records = [{"n": i} for i in range(5)]
        with open(file_path, "w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

        result = telemetry.load_recent_telemetry(count=50)
        assert len(result) == 5
        assert result[0]["n"] == 0
        assert result[4]["n"] == 4

    def test_loads_last_n(self, tmp_telemetry_dir):
        file_path = str(tmp_telemetry_dir / "telemetry.jsonl")
        records = [{"n": i} for i in range(100)]
        with open(file_path, "w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

        result = telemetry.load_recent_telemetry(count=10)
        assert len(result) == 10
        assert result[0]["n"] == 90
        assert result[9]["n"] == 99

    def test_skips_unparseable_lines(self, tmp_telemetry_dir):
        file_path = str(tmp_telemetry_dir / "telemetry.jsonl")
        with open(file_path, "w") as f:
            f.write('{"valid": true}\n')
            f.write("not json at all\n")
            f.write('{"also_valid": true}\n')

        result = telemetry.load_recent_telemetry()
        assert len(result) == 2
        assert result[0]["valid"] is True
        assert result[1]["also_valid"] is True

    def test_skips_empty_lines(self, tmp_telemetry_dir):
        file_path = str(tmp_telemetry_dir / "telemetry.jsonl")
        with open(file_path, "w") as f:
            f.write('{"a": 1}\n')
            f.write("\n")
            f.write('{"b": 2}\n')

        result = telemetry.load_recent_telemetry()
        assert len(result) == 2


# --- aggregate_telemetry ---


class TestAggregateTelemetry:
    def test_empty_records(self):
        result = telemetry.aggregate_telemetry([])
        assert result["total"] == 0
        assert result["success_count"] == 0
        assert result["per_model"] == {}

    def test_basic_aggregation(self, sample_records):
        result = telemetry.aggregate_telemetry(sample_records)

        assert result["total"] == 4
        assert result["success_count"] == 3
        assert result["total_tokens_in"] == 1500 + 800 + 100 + 2000
        assert result["total_tokens_out"] == 500 + 200 + 0 + 800

    def test_per_model_breakdown(self, sample_records):
        result = telemetry.aggregate_telemetry(sample_records)

        assert "gpt-4" in result["per_model"]
        assert "claude-3" in result["per_model"]

        gpt4 = result["per_model"]["gpt-4"]
        assert gpt4["calls"] == 3
        assert gpt4["success_rate"] == 1.0  # 3/3

        claude = result["per_model"]["claude-3"]
        assert claude["calls"] == 1
        assert claude["success_rate"] == 0.0

    def test_per_classification_breakdown(self, sample_records):
        result = telemetry.aggregate_telemetry(sample_records)

        assert "COMPLEX" in result["per_classification"]
        assert result["per_classification"]["COMPLEX"]["count"] == 2
        assert "CONTEXT" in result["per_classification"]
        assert result["per_classification"]["CONTEXT"]["count"] == 1
        assert "SIMPLE" in result["per_classification"]
        assert result["per_classification"]["SIMPLE"]["count"] == 1

    def test_tool_usage(self, sample_records):
        result = telemetry.aggregate_telemetry(sample_records)

        assert "read_project_file" in result["tool_usage"]
        assert result["tool_usage"]["read_project_file"]["calls"] == 2
        assert result["tool_usage"]["read_project_file"]["success_rate"] == 1.0

        assert "shell_execution" in result["tool_usage"]
        assert result["tool_usage"]["shell_execution"]["calls"] == 1
        assert result["tool_usage"]["shell_execution"]["success_rate"] == 0.0

    def test_valence_trend(self, sample_records):
        result = telemetry.aggregate_telemetry(sample_records)

        assert result["valence_trend"]["first"] == 5
        assert result["valence_trend"]["last"] == 12
        assert result["valence_trend"]["delta"] == 7

    def test_avg_response_time(self, sample_records):
        result = telemetry.aggregate_telemetry(sample_records)
        expected_avg = (2000 + 1000 + 5000 + 1500) / 4
        assert result["avg_response_time"] == expected_avg


# --- format_telemetry_summary ---


class TestFormatTelemetrySummary:
    def test_empty_records(self):
        result = telemetry.format_telemetry_summary([])
        assert result == "No telemetry data available."

    def test_contains_header(self, sample_records):
        result = telemetry.format_telemetry_summary(sample_records)
        assert "Telemetry Summary" in result
        assert "last 4 executions" in result

    def test_contains_success_rate(self, sample_records):
        result = telemetry.format_telemetry_summary(sample_records)
        assert "Success: 3 (75%)" in result

    def test_contains_model_breakdown(self, sample_records):
        result = telemetry.format_telemetry_summary(sample_records)
        assert "gpt-4" in result
        assert "claude-3" in result

    def test_contains_valence_trend(self, sample_records):
        result = telemetry.format_telemetry_summary(sample_records)
        assert "Valence trend" in result


# --- format_model_telemetry ---


class TestFormatModelTelemetry:
    def test_no_data_for_model(self, sample_records):
        result = telemetry.format_model_telemetry(sample_records, "nonexistent")
        assert "No telemetry data" in result
        assert "nonexistent" in result

    def test_model_header(self, sample_records):
        result = telemetry.format_model_telemetry(sample_records, "gpt-4")
        assert "Model: gpt-4" in result

    def test_model_call_count(self, sample_records):
        result = telemetry.format_model_telemetry(sample_records, "gpt-4")
        assert "Total calls: 3" in result

    def test_model_success_rate(self, sample_records):
        result = telemetry.format_model_telemetry(sample_records, "gpt-4")
        assert "100%" in result

    def test_model_response_times(self, sample_records):
        result = telemetry.format_model_telemetry(sample_records, "gpt-4")
        assert "Fastest:" in result
        assert "Slowest:" in result

    def test_model_timeouts(self, sample_records):
        result = telemetry.format_model_telemetry(sample_records, "claude-3")
        assert "Timeouts: 1" in result

    def test_model_circuit_breaker(self, sample_records):
        result = telemetry.format_model_telemetry(sample_records, "gpt-4")
        assert "Circuit breaker:" in result
