"""Exact-host catalog observations for Codex and Claude Code profiles."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Base,
    ManagedAgentProviderProfile,
    ProviderCredentialSource,
    RuntimeMaterializationMode,
)
from moonmind.provider_profiles.catalog_observations import (
    catalog_evidence_from_model_options,
    record_profile_catalog_observation,
)
from moonmind.provider_profiles.model_families import (
    catalog_rows_from_evidence,
    resolve_family_model,
)

CLAUDE_HOST_OPTIONS = {
    "models": [
        {"id": "opus", "model": "claude-opus-6", "displayName": "Opus 6"},
        {"id": "sonnet", "model": "claude-sonnet-5-5", "displayName": "Sonnet 5.5"},
        {"id": "opus[1m]", "model": "claude-opus-6[1m]"},
    ],
    "routable_models": ["claude-opus-6", "claude-opus-5-5", "claude-haiku-5-5"],
}


def test_claude_alias_rows_record_their_concrete_resolution():
    evidence = catalog_evidence_from_model_options(
        CLAUDE_HOST_OPTIONS, credential_generation=3
    )

    assert evidence is not None
    ids = [row["qualifiedId"] for row in evidence["models"]]
    assert ids == [
        "claude-opus-6",
        "claude-sonnet-5-5",
        "claude-opus-6[1m]",
        "claude-opus-5-5",
        "claude-haiku-5-5",
    ]
    assert evidence["credentialGeneration"] == 3
    resolution = resolve_family_model(
        "opus",
        provider_id="anthropic",
        catalog_rows=catalog_rows_from_evidence(evidence),
    )
    assert resolution.model == "claude-opus-6"


def test_codex_rows_keep_effort_and_lifecycle_metadata():
    evidence = catalog_evidence_from_model_options(
        {
            "models": [
                {
                    "id": "gpt-6.1-sol",
                    "model": "gpt-6.1-sol",
                    "supportedReasoningEfforts": [
                        {"reasoningEffort": "low"},
                        {"reasoningEffort": "medium"},
                    ],
                    "hidden": True,
                    "apiKey": "must-not-be-copied",
                }
            ]
        },
        credential_generation=None,
    )

    assert evidence["models"] == [
        {
            "qualifiedId": "gpt-6.1-sol",
            "hidden": True,
            "supportedEfforts": ["low", "medium"],
        }
    ]


def test_empty_observation_is_not_recorded():
    assert (
        catalog_evidence_from_model_options({"models": []}, credential_generation=1)
        is None
    )


@pytest.fixture()
def session_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/obs.db", future=True)
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as session:
            for profile_id, runtime_id, provider_id in (
                ("claude_anthropic_oauth", "claude_code", "anthropic"),
                ("opencode-go-default", "opencode", "opencode-go"),
            ):
                session.add(
                    ManagedAgentProviderProfile(
                        profile_id=profile_id,
                        runtime_id=runtime_id,
                        provider_id=provider_id,
                        credential_source=ProviderCredentialSource.NONE,
                        runtime_materialization_mode=RuntimeMaterializationMode.API_KEY_ENV,
                        credential_generation=2,
                    )
                )
            await session.commit()

    asyncio.run(_setup())
    yield factory
    asyncio.run(engine.dispose())


async def _row(factory, profile_id):
    async with factory() as session:
        return await session.get(ManagedAgentProviderProfile, profile_id)


@pytest.mark.asyncio
async def test_observation_updates_only_evidence_for_the_current_credential(
    session_factory,
):
    before = await _row(session_factory, "claude_anthropic_oauth")
    observed_at = datetime(2026, 11, 6, tzinfo=UTC)

    stale = await record_profile_catalog_observation(
        session_factory,
        profile_id="claude_anthropic_oauth",
        credential_generation=1,
        model_options=CLAUDE_HOST_OPTIONS,
    )
    written = await record_profile_catalog_observation(
        session_factory,
        profile_id="claude_anthropic_oauth",
        credential_generation=2,
        model_options=CLAUDE_HOST_OPTIONS,
        observed_at=observed_at,
    )
    failed = await record_profile_catalog_observation(
        session_factory,
        profile_id="claude_anthropic_oauth",
        credential_generation=2,
        model_options={"models": []},
    )

    after = await _row(session_factory, "claude_anthropic_oauth")
    assert stale is False
    assert written is True
    assert failed is False
    # A failed or empty read keeps the last usable observation.
    assert after.model_catalog_evidence_json["validatedAt"] == observed_at.isoformat()
    assert after.model_tiers == before.model_tiers
    assert after.default_model_tier == before.default_model_tier
    assert after.updated_at == before.updated_at


@pytest.mark.asyncio
async def test_opencode_evidence_keeps_its_own_owner(session_factory):
    written = await record_profile_catalog_observation(
        session_factory,
        profile_id="opencode-go-default",
        credential_generation=2,
        model_options=CLAUDE_HOST_OPTIONS,
    )

    assert written is False
    assert (
        await _row(session_factory, "opencode-go-default")
    ).model_catalog_evidence_json is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("harness_id", "expected_calls"),
    [("claude-native", 1), ("codex-native", 1), ("opencode-native", 0)],
)
async def test_exact_host_attestation_records_the_admitted_profile_catalog(
    monkeypatch, harness_id, expected_calls
):
    from types import SimpleNamespace

    from moonmind.omnigent.host_services import attestation
    from moonmind.provider_profiles import catalog_observations

    calls = []

    async def fake_record(_factory, **kwargs):
        calls.append(kwargs)
        return True

    monkeypatch.setattr(
        catalog_observations, "record_profile_catalog_observation", fake_record
    )
    plan = SimpleNamespace(
        payload=SimpleNamespace(
            harnessId=harness_id,
            credentialBindings={
                "primary-model": SimpleNamespace(
                    providerProfileRef="claude_anthropic_oauth"
                )
            },
        )
    )

    await attestation._record_profile_catalog(
        plan=plan,
        credential_handles=[
            {"providerProfileRef": "other", "credentialGeneration": 9},
            {"providerProfileRef": "claude_anthropic_oauth", "credentialGeneration": 4},
        ],
        model_options=CLAUDE_HOST_OPTIONS,
    )

    assert len(calls) == expected_calls
    if calls:
        assert calls[0]["profile_id"] == "claude_anthropic_oauth"
        assert calls[0]["credential_generation"] == 4
