"""Explicit optional-provider policy for unit tests."""

import pytest


@pytest.fixture
def minimax_egress(tmp_path, monkeypatch):
    from moonmind.security import egress

    policy = tmp_path / "omnigent-provider-domains.txt"
    policy.write_text("api.minimax.io\n")
    profile = egress.OMNIGENT_EGRESS_PROFILE.model_copy(
        update={
            "destinations": (
                *egress.DEFAULT_EGRESS_PROFILE.destinations,
                *egress.load_omnigent_provider_destinations(policy),
            )
        }
    )
    monkeypatch.setattr(egress, "OMNIGENT_EGRESS_PROFILE", profile)
