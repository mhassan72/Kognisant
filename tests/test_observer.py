"""Tests for TraceCollector in cli_kognisant/observer.py.

Validates requirements R1.1 through R1.7 for trace collection during
PERP execution.
"""

import json
import os
import threading
import uuid

import pytest

from cli_kognisant.observer import TraceCollector


@pytest.fixture
def project_root(tmp_path):
    """Provide a temporary project root for trace storage."""
    return str(tmp_path)


@pytest.fixture
def collector(project_root):
    """Provide a TraceCollector instance."""
    return TraceCollector(project_root)


class TestStartSession:
    """R1.1: Session creation with UUID, ISO timestamp, truncated desc."""

    def test_returns_valid_uuid4(self, collector):
        session_id = collector.start_session("test task")
        parsed = uuid.UUID(session_id, version=4)
        assert str(parsed) == session_id

    def test_truncates_description_to_500_chars(self, collector, project_root):
        long_desc = "x" * 600
        session_id = collector.start_session(long_desc)
        collector.end_session(session_id, "completed")
        trace_path = os.path.join(
            project_root, ".kognisant", "traces", f"{session_id}.json"
        )
        with open(trace_path) as f:
            data = json.load(f)
        assert len(data["task_description"]) == 500

    def test_short_description_preserved(self, collector, project_root):
        desc = "short task"
        session_id = collector.start_session(desc)
        collector.end_session(session_id, "completed")
        trace_path = os.path.join(
            project_root, ".kognisant", "traces", f"{session_id}.json"
        )
        with open(trace_path) as f:
            data = json.load(f)
        assert data["task_description"] == desc


class TestRecordToolCall:
    """R1.2: Tool call recording with truncation."""

    def test_records_tool_call(self, collector, project_root):
        session_id = collector.start_session("task")
        collector.record_tool_call(
            session_id, "read_file", "arg1", "result1", True, 42
        )
        collector.end_session(session_id, "completed")
        trace_path = os.path.join(
            project_root, ".kognisant", "traces", f"{session_id}.json"
        )
        with open(trace_path) as f:
            data = json.load(f)
        assert len(data["tool_calls"]) == 1
        tc = data["tool_calls"][0]
        assert tc["tool_name"] == "read_file"
        assert tc["arguments"] == "arg1"
        assert tc["result_summary"] == "result1"
        assert tc["success"] is True
        assert tc["duration_ms"] == 42

    def test_truncates_arguments_to_1000(self, collector, project_root):
        session_id = collector.start_session("task")
        long_args = "a" * 1500
        collector.record_tool_call(
            session_id, "tool", long_args, "res", True, 10
        )
        collector.end_session(session_id, "completed")
        trace_path = os.path.join(
            project_root, ".kognisant", "traces", f"{session_id}.json"
        )
        with open(trace_path) as f:
            data = json.load(f)
        assert len(data["tool_calls"][0]["arguments"]) == 1000

    def test_truncates_result_to_200(self, collector, project_root):
        session_id = collector.start_session("task")
        long_result = "r" * 300
        collector.record_tool_call(
            session_id, "tool", "args", long_result, True, 10
        )
        collector.end_session(session_id, "completed")
        trace_path = os.path.join(
            project_root, ".kognisant", "traces", f"{session_id}.json"
        )
        with open(trace_path) as f:
            data = json.load(f)
        assert len(data["tool_calls"][0]["result_summary"]) == 200


class TestRecordFileOp:
    """R1.3: File operation recording."""

    def test_records_file_op(self, collector, project_root):
        session_id = collector.start_session("task")
        collector.record_file_op(session_id, "/src/main.py", "read", 1024)
        collector.end_session(session_id, "completed")
        trace_path = os.path.join(
            project_root, ".kognisant", "traces", f"{session_id}.json"
        )
        with open(trace_path) as f:
            data = json.load(f)
        assert len(data["file_operations"]) == 1
        fo = data["file_operations"][0]
        assert fo["file_path"] == "/src/main.py"
        assert fo["operation"] == "read"
        assert fo["byte_count"] == 1024


class TestRecordLLMCall:
    """R1.4: LLM call recording."""

    def test_records_llm_call(self, collector, project_root):
        session_id = collector.start_session("task")
        collector.record_llm_call(session_id, "gpt-4", 100, 50, 1500)
        collector.end_session(session_id, "completed")
        trace_path = os.path.join(
            project_root, ".kognisant", "traces", f"{session_id}.json"
        )
        with open(trace_path) as f:
            data = json.load(f)
        assert len(data["llm_calls"]) == 1
        lc = data["llm_calls"][0]
        assert lc["model_id"] == "gpt-4"
        assert lc["prompt_tokens"] == 100
        assert lc["completion_tokens"] == 50
        assert lc["latency_ms"] == 1500


class TestEndSession:
    """R1.5: Session finalization and persistence."""

    def test_writes_trace_file(self, collector, project_root):
        session_id = collector.start_session("my task")
        collector.end_session(session_id, "completed")
        trace_path = os.path.join(
            project_root, ".kognisant", "traces", f"{session_id}.json"
        )
        assert os.path.exists(trace_path)
        with open(trace_path) as f:
            data = json.load(f)
        assert data["session_id"] == session_id
        assert data["status"] == "completed"
        assert data["end_time"] is not None
        assert data["start_time"] is not None

    def test_sets_failed_status(self, collector, project_root):
        session_id = collector.start_session("failing task")
        collector.end_session(session_id, "failed")
        trace_path = os.path.join(
            project_root, ".kognisant", "traces", f"{session_id}.json"
        )
        with open(trace_path) as f:
            data = json.load(f)
        assert data["status"] == "failed"

    def test_end_nonexistent_session_is_noop(self, collector):
        # Should not raise
        collector.end_session("nonexistent-id", "completed")


class TestDirectoryCreation:
    """R1.6: Creates traces directory if it doesn't exist."""

    def test_creates_traces_dir(self, project_root):
        traces_dir = os.path.join(project_root, ".kognisant", "traces")
        assert not os.path.exists(traces_dir)
        collector = TraceCollector(project_root)
        session_id = collector.start_session("task")
        collector.end_session(session_id, "completed")
        assert os.path.isdir(traces_dir)


class TestErrorHandling:
    """R1.7: Disk I/O errors are logged, never raised."""

    def test_unwritable_dir_does_not_raise(self, tmp_path):
        # Create a file where the traces dir should be (blocking mkdir)
        kognisant_dir = tmp_path / ".kognisant"
        kognisant_dir.mkdir()
        traces_blocker = kognisant_dir / "traces"
        traces_blocker.write_text("not a directory")
        collector = TraceCollector(str(tmp_path))
        session_id = collector.start_session("task")
        # Should not raise even though directory creation will fail
        collector.end_session(session_id, "completed")


class TestThreadSafety:
    """Queue-based thread-safe trace submission."""

    def test_concurrent_tool_calls(self, collector, project_root):
        session_id = collector.start_session("concurrent task")
        threads = []
        for i in range(20):
            t = threading.Thread(
                target=collector.record_tool_call,
                args=(session_id, f"tool_{i}", "args", "res", True, i),
            )
            threads.append(t)
            t.start()
        for t in threads:
            t.join()
        collector.end_session(session_id, "completed")
        trace_path = os.path.join(
            project_root, ".kognisant", "traces", f"{session_id}.json"
        )
        with open(trace_path) as f:
            data = json.load(f)
        assert len(data["tool_calls"]) == 20
