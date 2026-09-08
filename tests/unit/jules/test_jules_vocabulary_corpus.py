"""Table-driven Jules corpus for MoonMind#3952.

Covers every token either legacy helper accepted, plus blank/None,
case/whitespace, hyphen/underscore/space variants, unknown and malformed
input. Both the display-safe classifier and the execution-critical string
helper must agree on classification; only their unknown handling differs
(display reports unknown, execution boundary raises).
"""

from __future__ import annotations

import pytest

from moonmind.jules.vocabulary import (
    JULES_WIRE_STATUS_MAP,
    classify_jules_status,
    require_known_jules_status,
)
from moonmind.jules.status import normalize_jules_status as snapshot_normalize
from moonmind.schemas.agent_runtime_models import UnsupportedStatusError
from moonmind.schemas.jules_models import (
    normalize_jules_status as string_normalize,
)

# Reviewed expected outcomes: (raw token, expected normalized).
CORPUS: list[tuple[str, str]] = [
    # queued family (schema + snapshot union)
    ("accepted", "queued"),
    ("assigned", "queued"),
    ("created", "queued"),
    ("open", "queued"),
    ("pending", "queued"),
    ("queued", "queued"),
    ("submitted", "queued"),
    # running family
    ("awaiting_plan_approval", "running"),
    ("blocked", "running"),
    ("in_progress", "running"),
    ("in-progress", "running"),
    ("in progress", "running"),
    ("paused", "running"),
    ("planning", "running"),
    ("processing", "running"),
    ("running", "running"),
    ("started", "running"),
    # completed family
    ("completed", "completed"),
    ("done", "completed"),
    ("finished", "completed"),
    ("resolved", "completed"),
    ("success", "completed"),
    # failed family (error/rejected snapshot-only; errored/failure schema-only)
    ("error", "failed"),
    ("errored", "failed"),
    ("failed", "failed"),
    ("failure", "failed"),
    ("rejected", "failed"),
    ("timed_out", "failed"),
    ("timeout", "failed"),
    # canceled family
    ("canceled", "canceled"),
    ("cancelled", "canceled"),
    # provider extension
    ("awaiting_user_feedback", "awaiting_feedback"),
    # explicit provider unknown sentinel
    ("state_unspecified", "unknown"),
]


@pytest.mark.parametrize("raw,expected", CORPUS)
def test_corpus_classification_agrees(raw: str, expected: str) -> None:
    snapshot = classify_jules_status(raw)
    assert snapshot.normalized_status == expected
    assert snapshot.provider_status_token == raw.replace("-", "_").replace(" ", "_").lower()
    # Terminal/success flags follow only the normalized value.
    assert snapshot.terminal == (expected in {"completed", "failed", "canceled"})
    assert snapshot.succeeded == (expected == "completed")
    # Snapshot wrapper agrees.
    assert snapshot_normalize(raw).normalized_status == expected
    # String helper agrees for known tokens (raises only for unknown sentinel).
    if expected == "unknown":
        # state_unspecified is the explicit known-unknown sentinel.
        assert string_normalize(raw) == "unknown"
    else:
        assert string_normalize(raw) == expected


@pytest.mark.parametrize("raw", ["COMPLETED", "  completed  ", "In-Progress", "AWAITING_PLAN_APPROVAL", "  Paused "])
def test_corpus_case_and_whitespace(raw: str) -> None:
    snapshot = classify_jules_status(raw)
    assert snapshot.is_missing is False
    assert snapshot.is_known is True
    assert string_normalize(raw) == snapshot.normalized_status


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_blank_classifies_unknown_never_queued(raw) -> None:
    snapshot = classify_jules_status(raw)
    assert snapshot.normalized_status == "unknown"
    assert snapshot.is_missing is True
    assert snapshot.is_known is False
    assert snapshot.terminal is False
    assert snapshot.succeeded is False
    assert string_normalize(raw) == "unknown"
    assert snapshot_normalize(raw).normalized_status == "unknown"


@pytest.mark.parametrize("raw", ["mystery", "awaiting_operator", "COMPLETELY_NEW", "error!!"])
def test_unknown_never_terminalizes(raw: str) -> None:
    snapshot = classify_jules_status(raw)
    assert snapshot.normalized_status == "unknown"
    assert snapshot.terminal is False
    assert snapshot.succeeded is False
    assert snapshot.failed is False
    assert snapshot.canceled is False
    with pytest.raises(UnsupportedStatusError):
        string_normalize(raw)
    with pytest.raises(UnsupportedStatusError):
        require_known_jules_status(raw)


@pytest.mark.parametrize("raw", [123, 4.5, ["running"], {"state": "running"}, b"running"])
def test_malformed_input_is_unknown(raw: object) -> None:
    snapshot = classify_jules_status(raw)
    assert snapshot.normalized_status == "unknown"
    assert snapshot.terminal is False
    assert snapshot.succeeded is False
    # Bounded provenance: no unbounded raw echo.
    assert len(snapshot.provider_status) <= 64


def test_wire_map_covers_legacy_union() -> None:
    # Every token either legacy helper accepted must be in the canonical map
    # (or fold to a map entry via token normalization).
    legacy_tokens = [raw for raw, _ in CORPUS if raw not in {"in-progress", "in progress"}]
    for token in legacy_tokens:
        assert token in JULES_WIRE_STATUS_MAP, token


def test_historical_pending_still_queued() -> None:
    # Pre-fix blank payloads were persisted as provider_status="pending";
    # that historical spelling must retain its queued meaning.
    assert classify_jules_status("pending").normalized_status == "queued"


def test_unknown_cannot_authorize_success() -> None:
    for raw in (None, "", "mystery", "state_unspecified"):
        snapshot = classify_jules_status(raw)
        assert not (snapshot.terminal and snapshot.succeeded)
        assert snapshot.succeeded is False
