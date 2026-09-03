"""Unit tests for the provider-profile tier capabilities contract."""

from __future__ import annotations

from types import SimpleNamespace

from api_service.services.provider_profile_tier_capabilities import (
    tier_capabilities_for_draft,
    tier_capabilities_for_profile,
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
            "validatedAt": "2026-09-03T04:00:00+00:00",
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


def test_draft_capabilities_are_not_stale():
    result = tier_capabilities_for_draft("codex_cli", "openai")
    assert result["evidence"]["stale"] is False
    assert result["evidence"]["source"] == "runtime_draft"
