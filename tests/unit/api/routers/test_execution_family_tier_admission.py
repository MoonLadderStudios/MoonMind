"""Admission resolves Provider Profile family tiers to a concrete selection."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api_service.api.routers import executions
from moonmind.provider_profiles.model_families import first_party_family_tier_template
from moonmind.workflows.executions.model_resolver import codex_uses_native_default


def _codex_profile(*catalog: str, tiers=None, **overrides):
    template_tiers, default_tier = first_party_family_tier_template(
        "codex_cli", "openai"
    )
    base = {
        "profile_id": "codex_openai_oauth",
        "runtime_id": "codex_cli",
        "provider_id": "openai",
        "enabled": True,
        "auth_state": "connected",
        "disabled_reason": None,
        "default_model": None,
        "default_effort": None,
        "model_tiers": tiers if tiers is not None else template_tiers,
        "default_model_tier": default_tier,
        "command_behavior": {"suppress_default_model_flag": True},
        "model_catalog_evidence_json": (
            {"models": [{"id": model_id} for model_id in catalog]} if catalog else None
        ),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_omitted_model_admits_default_tier_two_and_medium():
    profile = _codex_profile("gpt-6.1-sol", "gpt-6.2-sol", "gpt-6-luna")

    model, source, effort, resolution = executions._resolve_runtime_model_effort(
        runtime_id="codex_cli",
        profile=profile,
        runtime_payload={},
        requested_model=None,
    )

    assert (model, source, effort) == ("gpt-6.2-sol", "profile_default_tier", "medium")
    assert resolution["effectiveModelTier"] == 2
    assert resolution["modelFamily"] == "sol"
    assert resolution["modelMode"] == "automatic"
    assert resolution["providerProfileId"] == "codex_openai_oauth"
    # The legacy Codex native-default flag cannot erase the default tier.
    assert not codex_uses_native_default(
        suppress_default_model_flag=True,
        requested_model=None,
        resolved_model=model,
        profile_default_model=None,
        model_source=source,
    )


def test_each_new_admission_follows_current_policy_without_freezing_intent():
    runtime_payload: dict = {"modelTier": 2}
    first = executions._resolve_runtime_model_effort(
        runtime_id="codex_cli",
        profile=_codex_profile("gpt-6.1-sol"),
        runtime_payload=runtime_payload,
        requested_model=None,
    )
    later = executions._resolve_runtime_model_effort(
        runtime_id="codex_cli",
        profile=_codex_profile("gpt-6.1-sol", "gpt-7-sol"),
        runtime_payload=runtime_payload,
        requested_model=None,
    )

    assert first[0] == "gpt-6.1-sol"
    assert later[0] == "gpt-7-sol"
    # The authored tier intent is untouched by resolution.
    assert runtime_payload == {"modelTier": 2}


def test_unavailable_selected_family_is_an_actionable_refusal():
    profile = _codex_profile("gpt-6.1-sol")

    with pytest.raises(HTTPException) as excinfo:
        executions._resolve_runtime_model_effort(
            runtime_id="codex_cli",
            profile=profile,
            runtime_payload={"modelTier": 1},
            requested_model=None,
        )

    assert excinfo.value.status_code == 422
    assert excinfo.value.detail["code"] == "model_tier_family_unavailable"
    assert excinfo.value.detail["modelFamily"] == "astra"
    assert excinfo.value.detail["effectiveModelTier"] == 1


def test_custom_model_selection_does_not_rewrite_the_profile():
    profile = _codex_profile("gpt-6.1-sol")
    before = [dict(tier) for tier in profile.model_tiers]

    model, source, effort, _ = executions._resolve_runtime_model_effort(
        runtime_id="codex_cli",
        profile=profile,
        runtime_payload={"model": "gpt-6-luna", "effort": "high"},
        requested_model="gpt-6-luna",
    )

    assert (model, source, effort) == ("gpt-6-luna", "task_override", "high")
    assert profile.model_tiers == before
