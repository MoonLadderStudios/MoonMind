"""Stable identity and compatibility rules for Omnigent inventory sync."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from api_service.services.omnigent_agent_profile_service import (
    projection_identity,
    projection_readiness,
)


def test_projection_identity_uses_stable_id_not_display_name():
    first = projection_identity("default", "agent-1", "v1")
    assert first == projection_identity("default", "agent-1", "v1")
    assert first != projection_identity("default", "agent-2", "v1")
    assert first != projection_identity("other", "agent-1", "v1")


def test_projection_identity_is_bounded_for_untrusted_upstream_values():
    result = projection_identity("e" * 1000, "a" * 10000, "v" * 1000)
    assert result.startswith("upstream:")
    assert len(result) == 73


def _projection(now: datetime, **overrides):
    values = {
        "available": True,
        "compatible": True,
        "last_successful_sync_at": now - timedelta(minutes=1),
        "last_attempt_at": now,
        "error": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_projection_readiness_requires_recent_success_without_error():
    now = datetime(2026, 7, 28, tzinfo=timezone.utc)

    assert projection_readiness(_projection(now), now=now)["ready"] is True
    stale = projection_readiness(
        _projection(now, last_successful_sync_at=now - timedelta(minutes=6)),
        now=now,
    )
    failed = projection_readiness(_projection(now, error="endpoint timeout"), now=now)

    assert stale["freshness"] == "stale"
    assert stale["reason"] == "upstream inventory is stale"
    assert failed["freshness"] == "stale"
    assert failed["ready"] is False


def test_projection_readiness_explains_missing_unavailable_and_incompatible():
    now = datetime(2026, 7, 28, tzinfo=timezone.utc)

    missing = projection_readiness(None, now=now)
    unavailable = projection_readiness(
        _projection(now, available=False),
        now=now,
    )
    incompatible = projection_readiness(
        _projection(now, compatible=False),
        now=now,
    )

    assert missing["freshness"] == "missing"
    assert unavailable["reason"] == "stable upstream identity is unavailable"
    assert incompatible["reason"] == "stable upstream identity is incompatible"
