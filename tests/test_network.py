"""Tests for cli_kognisant/network.py — streaming API modifications."""

import io
import json
import socket
from unittest.mock import MagicMock, patch

import pytest

from cli_kognisant.network import KognisantAPIError, query_model_api_stream


def _make_sse_lines(chunks, done=True):
    """Helper: build raw SSE byte lines from a list of chunk dicts."""
    lines = []
    for chunk in chunks:
        lines.append(f"data: {json.dumps(chunk)}\n".encode())
        lines.append(b"\n")
    if done:
        lines.append(b"data: [DONE]\n")
        lines.append(b"\n")
    return lines


def _make_mock_response(sse_lines, status=200):
    """Create a mock response object that iterates over SSE lines."""
    response = MagicMock()
    response.status = status
    response.__iter__ = lambda self: iter(sse_lines)
    response.close = MagicMock()
    # Mock the socket for settimeout
    mock_sock = MagicMock()
    response.fp = MagicMock()
    response.fp._sock = mock_sock
    return response


class TestPhaseConnectedYield:
    """Verify ("phase", "connected") is yielded before any content events."""

    def test_phase_connected_yielded_first(self):
        """The first yielded event must be ("phase", "connected")."""
        chunks = [
            {"choices": [{"delta": {"content": "Hello"}}]},
        ]
        sse_lines = _make_sse_lines(chunks)
        mock_response = _make_mock_response(sse_lines)

        with patch("urllib.request.urlopen", return_value=mock_response):
            events = list(
                query_model_api_stream("http://test.api/v1", "key", {"model": "gpt-4", "messages": []})
            )

        assert events[0] == ("phase", "connected")

    def test_phase_connected_before_content(self):
        """("phase", "connected") must come before any ("content", ...) events."""
        chunks = [
            {"choices": [{"delta": {"content": "Hi"}}]},
            {"choices": [{"delta": {"content": " there"}}]},
        ]
        sse_lines = _make_sse_lines(chunks)
        mock_response = _make_mock_response(sse_lines)

        with patch("urllib.request.urlopen", return_value=mock_response):
            events = list(
                query_model_api_stream("http://test.api/v1", "key", {"model": "gpt-4", "messages": []})
            )

        phase_idx = next(i for i, e in enumerate(events) if e[0] == "phase")
        content_indices = [i for i, e in enumerate(events) if e[0] == "content"]
        assert all(phase_idx < ci for ci in content_indices)


class TestTimeoutPassthrough:
    """Verify the timeout parameter is passed through to urlopen."""

    def test_default_timeout(self):
        """Default timeout of 120.0 is passed to urlopen."""
        chunks = [{"choices": [{"delta": {"content": "ok"}}]}]
        sse_lines = _make_sse_lines(chunks)
        mock_response = _make_mock_response(sse_lines)

        with patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
            list(query_model_api_stream("http://test.api/v1", "key", {"model": "m", "messages": []}))

        _, kwargs = mock_urlopen.call_args
        assert kwargs["timeout"] == 120.0

    def test_custom_timeout(self):
        """Custom timeout=5.0 is passed to urlopen."""
        chunks = [{"choices": [{"delta": {"content": "ok"}}]}]
        sse_lines = _make_sse_lines(chunks)
        mock_response = _make_mock_response(sse_lines)

        with patch("urllib.request.urlopen", return_value=mock_response) as mock_urlopen:
            list(
                query_model_api_stream(
                    "http://test.api/v1", "key", {"model": "m", "messages": []}, timeout=5.0
                )
            )

        _, kwargs = mock_urlopen.call_args
        assert kwargs["timeout"] == 5.0


class TestStallDetection:
    """Verify socket.timeout during iteration raises KognisantAPIError."""

    def test_socket_timeout_raises_api_error(self):
        """When response iteration raises socket.timeout, KognisantAPIError is raised."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.close = MagicMock()
        mock_response.fp = MagicMock()
        mock_response.fp._sock = MagicMock()

        # Make the iterator raise socket.timeout after yielding phase
        def raise_timeout():
            raise socket.timeout("timed out")

        mock_response.__iter__ = lambda self: iter([])
        # We need to simulate the iteration raising socket.timeout
        # Override __iter__ to raise on the first call
        def stalling_iter(self):
            yield b"data: " + json.dumps({"choices": [{"delta": {"content": "H"}}]}).encode() + b"\n"
            raise socket.timeout("timed out")

        mock_response.__iter__ = stalling_iter

        with patch("urllib.request.urlopen", return_value=mock_response):
            with pytest.raises(KognisantAPIError, match="Stream stalled"):
                list(
                    query_model_api_stream("http://test.api/v1", "key", {"model": "m", "messages": []})
                )

    def test_stall_error_message(self):
        """Error message mentions 30s timeout."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.close = MagicMock()
        mock_response.fp = MagicMock()
        mock_response.fp._sock = MagicMock()

        def stalling_iter(self):
            raise socket.timeout("timed out")

        mock_response.__iter__ = stalling_iter

        with patch("urllib.request.urlopen", return_value=mock_response):
            with pytest.raises(KognisantAPIError, match="no data for 30s"):
                list(
                    query_model_api_stream("http://test.api/v1", "key", {"model": "m", "messages": []})
                )


class TestUsageExtraction:
    """Verify usage data from the final chunk is included in done message."""

    def test_usage_in_final_chunk(self):
        """When a chunk contains 'usage', it appears as _usage in the done message."""
        chunks = [
            {"choices": [{"delta": {"content": "Hello"}}]},
            {
                "choices": [{"delta": {}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        ]
        sse_lines = _make_sse_lines(chunks)
        mock_response = _make_mock_response(sse_lines)

        with patch("urllib.request.urlopen", return_value=mock_response):
            events = list(
                query_model_api_stream("http://test.api/v1", "key", {"model": "m", "messages": []})
            )

        done_events = [e for e in events if e[0] == "done"]
        assert len(done_events) == 1
        done_msg = done_events[0][1]
        assert "_usage" in done_msg
        assert done_msg["_usage"] == {"prompt_tokens": 10, "completion_tokens": 5}

    def test_no_usage_field_when_absent(self):
        """When no chunk contains 'usage', _usage is not in the done message."""
        chunks = [
            {"choices": [{"delta": {"content": "Hi"}}]},
        ]
        sse_lines = _make_sse_lines(chunks)
        mock_response = _make_mock_response(sse_lines)

        with patch("urllib.request.urlopen", return_value=mock_response):
            events = list(
                query_model_api_stream("http://test.api/v1", "key", {"model": "m", "messages": []})
            )

        done_events = [e for e in events if e[0] == "done"]
        assert len(done_events) == 1
        done_msg = done_events[0][1]
        assert "_usage" not in done_msg

    def test_usage_from_last_chunk_wins(self):
        """If multiple chunks have usage, the last one is used."""
        chunks = [
            {
                "choices": [{"delta": {"content": "A"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 1},
            },
            {
                "choices": [{"delta": {"content": "B"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 3},
            },
        ]
        sse_lines = _make_sse_lines(chunks)
        mock_response = _make_mock_response(sse_lines)

        with patch("urllib.request.urlopen", return_value=mock_response):
            events = list(
                query_model_api_stream("http://test.api/v1", "key", {"model": "m", "messages": []})
            )

        done_msg = [e for e in events if e[0] == "done"][0][1]
        assert done_msg["_usage"] == {"prompt_tokens": 10, "completion_tokens": 3}


class TestSocketTimeoutIsSet:
    """Verify settimeout(30.0) is called on the response socket."""

    def test_settimeout_called(self):
        """After yielding connected, settimeout(30.0) is called on the socket."""
        chunks = [{"choices": [{"delta": {"content": "x"}}]}]
        sse_lines = _make_sse_lines(chunks)
        mock_response = _make_mock_response(sse_lines)

        with patch("urllib.request.urlopen", return_value=mock_response):
            list(query_model_api_stream("http://test.api/v1", "key", {"model": "m", "messages": []}))

        mock_response.fp._sock.settimeout.assert_called_once_with(30.0)
