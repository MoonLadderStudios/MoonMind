"""Unit tests for RAG telemetry helpers (DOC-REQ-004)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from moonmind.rag.telemetry import VectorTelemetry

def test_record_emits_increment_metric() -> None:
    mock_emitter = MagicMock()
    with patch("moonmind.rag.telemetry.get_metrics_emitter", return_value=mock_emitter):
        telemetry = VectorTelemetry(run_id="run-1", job_id="job-1")
        telemetry.record("search", count=3, top_k=5)

    mock_emitter.increment.assert_called_once_with("rag.search.count", value=3)

def test_timing_emits_observe_metric() -> None:
    mock_emitter = MagicMock()
    with patch("moonmind.rag.telemetry.get_metrics_emitter", return_value=mock_emitter):
        telemetry = VectorTelemetry(run_id="run-1", job_id="job-1")
        telemetry.timing("search", milliseconds=42.5)

    mock_emitter.observe.assert_called_once_with(
        "rag.search.latency_ms", value=42.5 / 1000.0
    )

def test_timer_context_manager_records_duration() -> None:
    mock_emitter = MagicMock()
    with patch("moonmind.rag.telemetry.get_metrics_emitter", return_value=mock_emitter):
        telemetry = VectorTelemetry(run_id="run-1", job_id=None)
        with telemetry.timer("embedding"):
            pass  # simulate instant work

    # Should have called observe for the timing
    mock_emitter.observe.assert_called_once()
    call_args = mock_emitter.observe.call_args
    assert call_args[0][0] == "rag.embedding.latency_ms"
    # Duration should be >= 0
    assert call_args[1]["value"] >= 0

def test_telemetry_with_none_ids() -> None:
    """VectorTelemetry should handle None run_id and job_id gracefully."""
    mock_emitter = MagicMock()
    with patch("moonmind.rag.telemetry.get_metrics_emitter", return_value=mock_emitter):
        telemetry = VectorTelemetry(run_id=None, job_id=None)
        telemetry.record("upsert")

    mock_emitter.increment.assert_called_once()
