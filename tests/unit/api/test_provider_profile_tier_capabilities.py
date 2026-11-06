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


def test_missing_evidence_is_stale_without_advisory_diagnostic():
    profile = _profile(model_catalog_evidence_json=None)
    result = tier_capabilities_for_profile(profile)
    assert result["evidence"]["stale"] is True
    assert result["diagnostics"] == []


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


def _first_party_profile(**overrides):
    from moonmind.provider_profiles.model_families import (
        first_party_family_tier_template,
    )

    tiers, default_tier = first_party_family_tier_template("claude_code", "anthropic")
    base = {
        "runtime_id": "claude_code",
        "provider_id": "anthropic",
        "profile_id": "claude_anthropic_oauth",
        "credential_generation": 1,
        "default_model": None,
        "runtime_validation_image_ref": None,
        "model_tiers": tiers,
        "default_model_tier": default_tier,
        "model_catalog_evidence_json": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_first_party_draft_supplies_the_backend_family_template():
    result = tier_capabilities_for_draft("codex_cli", "openai")

    policy = result["recommended_policy"]
    assert policy["default_model_tier"] == 2
    assert [tier["family"] for tier in policy["model_tiers"]] == [
        "astra",
        "sol",
        "terra",
        "luna",
    ]
    assert [family["value"] for family in result["families"]] == [
        "astra",
        "sol",
        "terra",
        "luna",
    ]
    # No stale static model list: starting releases are explicitly unverified.
    assert {option["status"] for option in result["model"]["options"]} == {"unverified"}
    assert "gpt-4o" not in {option["value"] for option in result["model"]["options"]}


def test_other_drafts_have_no_family_template():
    result = tier_capabilities_for_draft("opencode", "openrouter")
    assert result["recommended_policy"] is None
    assert result["families"] == []


def test_unavailable_family_keeps_its_tier_number_and_status():
    evidence = {
        "credentialGeneration": 1,
        "validatedAt": datetime.now(UTC).isoformat(),
        "models": [
            {"id": "claude-opus-5-5"},
            {"id": "claude-sonnet-5-5"},
            {"id": "claude-haiku-5-5"},
        ],
    }
    result = tier_capabilities_for_profile(
        _first_party_profile(model_catalog_evidence_json=evidence)
    )

    tiers = {entry["index"]: entry for entry in result["tiers"]}
    assert len(tiers) == 4
    assert tiers[1]["family"] == "fable"
    assert tiers[1]["status"] == "unavailable"
    assert tiers[1]["model"] is None
    assert tiers[1]["reason"]
    assert tiers[2]["status"] == "resolved"
    assert tiers[2]["model"] == "claude-opus-5-5"
    assert tiers[3]["model"] == "claude-sonnet-5-5"


def test_pinned_tier_reports_status_without_changing_the_pin():
    from moonmind.provider_profiles.model_families import (
        first_party_family_tier_template,
    )

    tiers, _ = first_party_family_tier_template("claude_code", "anthropic")
    tiers[1] = {**tiers[1], "model_mode": "pinned", "model": "claude-opus-5"}
    evidence = {
        "credentialGeneration": 1,
        "validatedAt": datetime.now(UTC).isoformat(),
        "models": [{"id": "claude-opus-6"}],
    }
    result = tier_capabilities_for_profile(
        _first_party_profile(model_tiers=tiers, model_catalog_evidence_json=evidence)
    )

    opus = result["tiers"][1]
    assert opus["model_mode"] == "pinned"
    assert opus["model"] == "claude-opus-5"
    assert opus["status"] == "unavailable"


def test_unobserved_first_party_profile_reports_unverified_resolutions():
    result = tier_capabilities_for_profile(_first_party_profile())
    assert {entry["status"] for entry in result["tiers"]} == {"unverified"}
    assert result["tiers"][1]["model"] == "claude-opus-5-5"


@pytest.mark.parametrize("runtime_id", ["codex_cli", "claude_code"])
def test_runtime_default_matches_the_launch_resolver(runtime_id, monkeypatch):
    from moonmind.config.settings import settings
    from moonmind.workflows.executions.runtime_defaults import resolve_runtime_defaults

    for key in (
        "MOONMIND_CODEX_MODEL",
        "CODEX_MODEL",
        "MOONMIND_CLAUDE_MODEL",
        "CLAUDE_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)
    model, effort = resolve_runtime_defaults(
        runtime_id, workflow_settings=settings.workflow
    )

    result = tier_capabilities_for_draft(
        runtime_id, "anthropic" if runtime_id == "claude_code" else "openai"
    )

    assert result["model"]["runtime_default"] == model
    assert result["effort"]["runtime_default"] == effort
