"""Unit tests for the NDJSON stream parser module."""

import json

from moonmind.agents.base.ndjson_parser import (
    CursorStreamEvent,
    detect_rate_limit,
    parse_ndjson_line,
    parse_ndjson_stream,
)


def test_parse_ndjson_line_system_event():
    """Parse a valid system event from NDJSON line."""
    line = json.dumps({
        "type": "system",
        "timestamp": "2026-03-20T12:00:00Z",
        "data": {"version": "1.0", "model": "claude-4-sonnet"},
    })
    event = parse_ndjson_line(line)
    assert event is not None
    assert event.event_type == "system"
    assert event.timestamp == "2026-03-20T12:00:00Z"
    assert event.data["version"] == "1.0"
    assert event.data["model"] == "claude-4-sonnet"


def test_parse_ndjson_line_assistant_event():
    """Parse an assistant text event."""
    line = json.dumps({
        "type": "assistant",
        "timestamp": "2026-03-20T12:00:01Z",
        "data": {"text": "Here is the solution..."},
    })
    event = parse_ndjson_line(line)
    assert event is not None
    assert event.event_type == "assistant"
    assert event.data["text"] == "Here is the solution..."


def test_parse_ndjson_line_tool_call_event():
    """Parse a tool_call event."""
    line = json.dumps({
        "type": "tool_call",
        "timestamp": "2026-03-20T12:00:02Z",
        "data": {"tool": "edit_file", "status": "started"},
    })
    event = parse_ndjson_line(line)
    assert event is not None
    assert event.event_type == "tool_call"
    assert event.data["tool"] == "edit_file"
    assert event.data["status"] == "started"


def test_parse_ndjson_line_result_event():
    """Parse a result event."""
    line = json.dumps({
        "type": "result",
        "timestamp": "2026-03-20T12:00:03Z",
        "data": {"success": True, "text": "Task completed successfully"},
    })
    event = parse_ndjson_line(line)
    assert event is not None
    assert event.event_type == "result"
    assert event.data["success"] is True


def test_parse_ndjson_line_user_event():
    """Parse a user event."""
    line = json.dumps({
        "type": "user",
        "data": {"text": "implement the feature"},
    })
    event = parse_ndjson_line(line)
    assert event is not None
    assert event.event_type == "user"
    assert event.timestamp is None


def test_parse_ndjson_line_malformed_json():
    """Malformed JSON returns None."""
    event = parse_ndjson_line("this is not json{{{")
    assert event is None


def test_parse_ndjson_line_missing_type():
    """JSON without 'type' field returns None."""
    line = json.dumps({"data": {"text": "no type"}})
    event = parse_ndjson_line(line)
    assert event is None


def test_parse_ndjson_line_empty():
    """Empty / whitespace-only line returns None."""
    assert parse_ndjson_line("") is None
    assert parse_ndjson_line("  \n") is None


def test_parse_ndjson_line_not_object():
    """Non-object JSON (e.g. array) returns None."""
    event = parse_ndjson_line("[1, 2, 3]")
    assert event is None


def test_parse_ndjson_line_no_data():
    """Event without 'data' field gets empty dict default."""
    line = json.dumps({"type": "system", "timestamp": "2026-01-01T00:00:00Z"})
    event = parse_ndjson_line(line)
    assert event is not None
    assert event.data == {}


def test_parse_ndjson_stream():
    """Stream parser yields multiple events from line iterable."""
    lines = [
        json.dumps({"type": "system", "data": {"version": "1.0"}}),
        json.dumps({"type": "assistant", "data": {"text": "hello"}}),
        json.dumps({"type": "result", "data": {"success": True}}),
    ]
    events = list(parse_ndjson_stream(lines))
    assert len(events) == 3
    assert events[0].event_type == "system"
    assert events[1].event_type == "assistant"
    assert events[2].event_type == "result"


def test_parse_ndjson_stream_skips_malformed():
    """Stream parser yields good events and skips bad ones."""
    lines = [
        json.dumps({"type": "system", "data": {"v": "1"}}),
        "not valid json",
        "",
        json.dumps({"type": "result", "data": {"success": True}}),
    ]
    events = list(parse_ndjson_stream(lines))
    assert len(events) == 2
    assert events[0].event_type == "system"
    assert events[1].event_type == "result"


# --- Rate-limit detection tests ---


def test_detect_rate_limit_429_status():
    """Detect rate limit from status=429."""
    event = CursorStreamEvent(
        event_type="error",
        data={"status": 429, "error": "Too many requests"},
    )
    result = detect_rate_limit(event)
    assert result["detected"] is True
    assert result["retry_after_seconds"] is None


def test_detect_rate_limit_429_status_code():
    """Detect rate limit from statusCode=429."""
    event = CursorStreamEvent(
        event_type="error",
        data={"statusCode": 429, "retry_after": 60},
    )
    result = detect_rate_limit(event)
    assert result["detected"] is True
    assert result["retry_after_seconds"] == 60


def test_detect_rate_limit_error_text():
    """Detect rate limit from error message text."""
    event = CursorStreamEvent(
        event_type="error",
        data={"error": "You have been rate limited, please wait"},
    )
    result = detect_rate_limit(event)
    assert result["detected"] is True


def test_detect_rate_limit_message_text():
    """Detect rate limit from message field containing 'too many requests'."""
    event = CursorStreamEvent(
        event_type="error",
        data={"message": "Too many requests, slow down"},
    )
    result = detect_rate_limit(event)
    assert result["detected"] is True


def test_detect_rate_limit_not_detected():
    """No rate-limit indicators returns detected=False."""
    event = CursorStreamEvent(
        event_type="assistant",
        data={"text": "Here is the solution"},
    )
    result = detect_rate_limit(event)
    assert result["detected"] is False
    assert result["retry_after_seconds"] is None

