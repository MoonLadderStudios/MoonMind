"""Unit tests for the provider-profile tier capabilities contract."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from api_service.services.provider_profile_tier_capabilities import (
    tier_capabilities_for_draft,
    tier_capabilities_for_profile,
)
from moonmind.omnigent.bootstrap.opencode import (
    DEFAULT_OPENCODE_QUALIFIED,
    ZEN_FREE_QUALIFIED,
    validate_effort_for_model,
)


def _profile(**overrides):
    base = {
        "runtime_id": "codex_cli",
        "provider_id": "opencode-go",
        "profile_id": "profile-1",
        "credential_generation": 3,
        "default_model": "opencode-model-a",
        "runtime_validation_image_ref": "img:v2",
        "model_catalog_evidence_json": {
            "credentialGeneration": 3,
            "imageRef": "img:v2",
            "validatedAt": datetime.now(UTC).isoformat(),
            "models": [
                {"qualifiedId": "opencode-model-a"},
                {"qualifiedId": "opencode-model-b", "label": "Model B"},
            ],
        },
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_options_come_from_profile_catalog_evidence():
    result = tier_capabilities_for_profile(_profile())
    assert [o["value"] for o in result["model"]["options"]] == [
        "opencode-model-a",
        "opencode-model-b",
    ]
    assert result["model"]["options"][0]["recommended"] is True
    assert result["evidence"]["stale"] is False


def test_generation_mismatch_marks_stale():
    profile = _profile(credential_generation=4)
    result = tier_capabilities_for_profile(profile)
    assert result["evidence"]["stale"] is True
    assert any(d["code"] == "evidence_stale" for d in result["diagnostics"])


def test_image_mismatch_marks_stale():
    profile = _profile(runtime_validation_image_ref="img:v3")
    result = tier_capabilities_for_profile(profile)
    assert result["evidence"]["stale"] is True
    assert "image" in result["diagnostics"][0]["message"]


def test_missing_evidence_is_stale_with_diagnostic():
    profile = _profile(model_catalog_evidence_json=None)
    result = tier_capabilities_for_profile(profile)
    assert result["evidence"]["stale"] is True
    assert [d["code"] for d in result["diagnostics"]] == ["evidence_missing"]


def test_evidence_without_models_falls_back_with_diagnostic():
    evidence = dict(_profile().model_catalog_evidence_json)
    evidence["models"] = []
    result = tier_capabilities_for_profile(_profile(model_catalog_evidence_json=evidence))
    assert [d["code"] for d in result["diagnostics"]] == ["evidence_models_fallback"]
    assert len(result["model"]["options"]) > 0


def test_opencode_drafts_do_not_invent_provider_models():
    for provider_id in ("openrouter", "future-provider", "openai", "opencode-go", "opencode"):
        result = tier_capabilities_for_draft("opencode", provider_id)
        assert result["model"]["options"] == []
        assert result["model"]["runtime_default"] is None
        assert result["model"]["allow_custom"] is True


def test_opencode_saved_catalog_retains_qualified_models():
    result = tier_capabilities_for_profile(_profile(
        runtime_id="opencode", provider_id="openrouter",
        default_model="openrouter/anthropic/model",
        model_catalog_evidence_json={"models": [{"qualifiedId": "openrouter/anthropic/model"}]},
    ))
    assert [item["value"] for item in result["model"]["options"]] == ["openrouter/anthropic/model"]


def test_draft_capabilities_are_not_stale():
    result = tier_capabilities_for_draft("codex_cli", "openai")
    assert result["evidence"]["stale"] is False
    assert result["evidence"]["source"] == "runtime_draft"


def test_effort_options_offer_max_above_xhigh():
    for result in (
        tier_capabilities_for_draft("claude_code", "anthropic"),
        tier_capabilities_for_profile(_profile()),
    ):
        values = [o["value"] for o in result["effort"]["options"]]
        assert values == ["low", "medium", "high", "xhigh", "max"]
        max_option = result["effort"]["options"][-1]
        assert max_option["label"] == "Max"
        assert max_option["status"] == "available"


@pytest.mark.parametrize(
    "models",
    [
        [DEFAULT_OPENCODE_QUALIFIED],
        [ZEN_FREE_QUALIFIED],
        [DEFAULT_OPENCODE_QUALIFIED, ZEN_FREE_QUALIFIED],
        [DEFAULT_OPENCODE_QUALIFIED, "opencode-go/future-model"],
        ["opencode-go/future-model"],
    ],
)
def test_opencode_advertised_efforts_are_accepted_at_launch(models):
    result = tier_capabilities_for_profile(
        _profile(
            runtime_id="opencode",
            model_catalog_evidence_json={
                "models": [{"qualifiedId": model} for model in models]
            },
        )
    )
    for option in result["effort"]["options"]:
        for model in models:
            compatible_models = option["compatible_models"]
            if option["status"] == "unavailable" or (
                compatible_models is not None and model not in compatible_models
            ):
                continue
            assert validate_effort_for_model(option["value"], model) == option["value"]

    max_option = next(
        option for option in result["effort"]["options"] if option["value"] == "max"
    )
    for model in models:
        available = max_option["status"] != "unavailable" and (
            max_option["compatible_models"] is None
            or model in max_option["compatible_models"]
        )
        assert available is (
            model not in {DEFAULT_OPENCODE_QUALIFIED, ZEN_FREE_QUALIFIED}
        )


def test_opencode_draft_does_not_offer_max_without_a_compatible_model():
    result = tier_capabilities_for_draft("opencode", "opencode-go")
    max_option = next(
        option for option in result["effort"]["options"] if option["value"] == "max"
    )
    assert max_option["status"] == "unavailable"
    assert max_option["compatible_models"] == []
