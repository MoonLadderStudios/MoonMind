"""Stable identity and compatibility rules for Omnigent inventory sync."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from api_service.services.omnigent_agent_profile_service import (
    _bounded_metadata,
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
        "bridge_mode": "proxy",
        "metadata_snapshot": {
            "harness": "codex-native",
            "capabilities": ["session.start"],
        },
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


def test_projection_readiness_enforces_requested_contract():
    now = datetime(2026, 7, 28, tzinfo=timezone.utc)
    projection = _projection(now)
    projection.bridge_mode = "proxy"
    projection.metadata_snapshot = {
        "harness": "codex-native",
        "capabilities": ["session.start"],
    }

    mismatch = projection_readiness(
        projection,
        now=now,
        bridge_mode="embedded",
        harness="other",
        required_capabilities=["tools"],
    )

    assert mismatch["ready"] is False
    assert mismatch["reason"] == "upstream metadata does not satisfy the requested profile contract"


def test_upstream_metadata_is_allowlisted_and_bounded():
    result = _bounded_metadata({
        "id": "agent-1",
        "name": "n" * 1000,
        "capabilities": ["tools", "session.start"],
        "apiToken": "must-not-persist",
        "nested": {"arbitrary": "payload"},
    })

    assert result["id"] == "agent-1"
    assert len(result["name"]) == 512
    assert result["capabilities"] == ["session.start", "tools"]
    assert "apiToken" not in result
    assert "nested" not in result
