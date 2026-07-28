"""Stable identity and compatibility rules for Omnigent inventory sync."""
from api_service.services.omnigent_agent_profile_service import projection_identity


def test_projection_identity_uses_stable_id_not_display_name():
    first = projection_identity("default", "agent-1", "v1")
    assert first == projection_identity("default", "agent-1", "v1")
    assert first != projection_identity("default", "agent-2", "v1")
    assert first != projection_identity("other", "agent-1", "v1")


def test_projection_identity_is_bounded_for_untrusted_upstream_values():
    result = projection_identity("e" * 1000, "a" * 10000, "v" * 1000)
    assert result.startswith("upstream:")
    assert len(result) == 73
