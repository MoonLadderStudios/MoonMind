"""Deployment configuration -> encrypted profile -> runtime credential authority.

SQLite is real; leases, Docker/provider validation, image/catalog discovery and
deployment qualification are scripted. No production credentials are used.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from api_service.db import base as db_base
from api_service.db.models import ManagedAgentProviderProfile, ManagedSecret
from moonmind.omnigent import production
from moonmind.omnigent.bootstrap import controller as controller_module
from moonmind.omnigent.bootstrap.models import BootstrapState
from moonmind.omnigent.bootstrap.provider_revalidation import (
    reconcile_opencode_provider_readiness,
    reset_enrollment_attempts,
)
from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.secret_resolution import OmnigentSecretResolutionService
from tests.unit.api_service import test_provider_profile_auto_seed as seed_tests

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, pytest.mark.integration_ci]
_module_db = seed_tests._module_db


@pytest.mark.parametrize("reject_rotation", [False, True])
async def test_environment_key_rotation_reaches_runtime_authority(
    monkeypatch, _module_db, reject_rotation
):
    """Replay mm:311b5931: a ready default still holds the previous env key."""
    old_key, new_key = "sk-old-deployment-test-key", "sk-new-deployment-test-key"
    resolver_factory = production.build_omnigent_secret_resolver
    reset_enrollment_attempts()
    await seed_tests._enroll_opencode_go_with_pinned_runtime(monkeypatch, api_key=old_key)
    monkeypatch.setattr(production, "build_omnigent_secret_resolver", resolver_factory)
    monkeypatch.setattr(
        "moonmind.auth.resolvers.db_resolver.async_session_maker",
        db_base.async_session_maker,
    )
    async with db_base.async_session_maker() as session:
        profile = await session.get(ManagedAgentProviderProfile, "opencode-go-default")
        old_generation = profile.credential_generation
        secret_slug = profile.secret_refs["opencode_api_key"].removeprefix("db://")
        image_ref = profile.model_catalog_evidence_json["imageRef"]
        original_tiers = profile.model_tiers

    # Keep the controller's production credential transaction; the seeding
    # helper supplies scripted lease and pinned-runtime infrastructure.
    resolved = SimpleNamespace(
        server_image_ref=image_ref,
        opencode_host_image_ref=image_ref,
        omnigent_build_digest="sha256:" + "b" * 64,
        architecture="linux/amd64",
        resolved_at=datetime.now(UTC),
    )
    record = {"value": None}
    monkeypatch.setattr(
        controller_module, "load_bootstrap_record", lambda: record["value"]
    )
    monkeypatch.setattr(
        controller_module,
        "save_bootstrap_record",
        lambda value: record.update(value=value),
    )
    monkeypatch.setattr(
        controller_module,
        "publish_resolved_omnigent_images",
        AsyncMock(return_value=resolved),
    )
    controller = controller_module.BootstrapController(
        session_factory=db_base.async_session_maker
    )
    monkeypatch.setattr(controller, "_sync_catalog", AsyncMock())
    monkeypatch.setattr(
        controller,
        "_ensure_agent_profile",
        AsyncMock(return_value="omnigent-opencode-default@1"),
    )
    qualification = AsyncMock(
        side_effect=lambda **kwargs: (
            {"supportCombinationKey": "test-support-combination"},
            kwargs["record"],
        )
    )
    monkeypatch.setattr(controller, "_qualify_and_publish", qualification)

    validation_calls = []

    class Validation:
        def __init__(self, **kwargs):
            assert kwargs["image_ref"] == image_ref

        async def validate(
            self, *, profile, lease, candidate_secret, candidate_generation
        ):
            validation_calls.append(candidate_generation)
            assert lease is not None
            assert candidate_secret == new_key
            assert candidate_generation == old_generation + 1
            if reject_rotation:
                raise ValueError("provider rejected candidate credential")
            return {
                "schemaVersion": "moonmind.provider-model-catalog-evidence.v1",
                "credentialGeneration": candidate_generation,
                "imageRef": image_ref,
                "materializerRef": "opencode-auth-json@1",
                "runtimeVersions": {"opencode": "1.18.11"},
                "validatedAt": datetime.now(UTC).isoformat(),
                "models": [{"qualifiedId": profile.default_model}],
            }

    monkeypatch.setattr(
        "moonmind.omnigent.opencode_runtime_validation.OpenCodeProviderRuntimeValidationService",
        Validation,
    )
    monkeypatch.setenv("OPENCODE_API_KEY", new_key)
    monkeypatch.delenv("OPENCODE_ACCEPT_CONTRIBUTOR_DATA_USE", raising=False)
    outcome = await reconcile_opencode_provider_readiness(
        session_factory=db_base.async_session_maker, controller=controller
    )
    assert outcome.ready is (not reject_rotation)
    assert outcome.enrolled is (not reject_rotation)
    assert validation_calls == [old_generation + 1]
    async with db_base.async_session_maker() as session:
        profile = await session.get(ManagedAgentProviderProfile, "opencode-go-default")
        secret = await session.scalar(
            select(ManagedSecret).where(ManagedSecret.slug == secret_slug)
        )
        assert secret.ciphertext == (old_key if reject_rotation else new_key)
        assert profile.credential_generation == old_generation + (not reject_rotation)
        assert (
            profile.model_catalog_evidence_json["credentialGeneration"]
            == profile.credential_generation
        )
        assert profile.model_tiers == original_tiers
        assert profile.enabled and profile.is_default

    # Exercise the runtime handoff: the admitted generation fences the secret
    # resolution, and only the committed credential reaches materialization.
    resolution = OmnigentSecretResolutionService(
        session_factory=db_base.async_session_maker, resolver=resolver_factory()
    )
    acquired = SimpleNamespace(
        provider_profile_ref=profile.profile_id,
        credential_generation=profile.credential_generation,
    )
    with await resolution.resolve(
        acquired=acquired, allowed_secret_roles=["opencode_api_key"]
    ) as bundle:
        assert bundle.require("opencode_api_key") == (
            old_key if reject_rotation else new_key
        )
    if not reject_rotation:
        acquired.credential_generation = old_generation
        with pytest.raises(HarnessPlatformError) as fenced:
            await resolution.resolve(
                acquired=acquired, allowed_secret_roles=["opencode_api_key"]
            )
        assert fenced.value.code == "OMNIGENT_CREDENTIAL_GENERATION_FENCED"
        assert record["value"].state == BootstrapState.ready
        repeated = await reconcile_opencode_provider_readiness(
            session_factory=db_base.async_session_maker, controller=controller
        )
        assert repeated.ready and not repeated.enrolled
        assert validation_calls == [old_generation + 1]
        qualification.assert_awaited_once()
    else:
        qualification.assert_not_awaited()
