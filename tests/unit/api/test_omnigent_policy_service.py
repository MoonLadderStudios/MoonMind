"""Persistence-boundary tests for immutable Omnigent policy lifecycle authority."""

from contextlib import asynccontextmanager
from copy import deepcopy

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Base,
    OmnigentOAuthHostBindingRecord,
    OmnigentPolicy,
    OmnigentPolicyEvent,
    OmnigentPolicyVersion,
)
from api_service.services.omnigent_policies import (
    OmnigentPolicyService,
    PolicyConflict,
)
from moonmind.omnigent.policies import PolicyDocument, PolicyState
from tests.unit.omnigent.test_policy_authority import policy_document


@asynccontextmanager
async def policy_db(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/omnigent-policies.db", future=True
    )
    sessions = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: Base.metadata.create_all(
                sync_connection,
                tables=[
                    OmnigentPolicy.__table__,
                    OmnigentPolicyVersion.__table__,
                    OmnigentPolicyEvent.__table__,
                    OmnigentOAuthHostBindingRecord.__table__,
                ],
            )
        )
    try:
        yield sessions
    finally:
        await engine.dispose()


async def create_policy(service: OmnigentPolicyService, policy_id: str = "policy"):
    return await service.create(
        policy_id=policy_id,
        name=f"{policy_id} name",
        owner_user_id=None,
        visibility="deployment",
        document=PolicyDocument.model_validate(policy_document()),
        actor="operator",
    )


@pytest.mark.asyncio
async def test_append_only_edit_preserves_history_and_rejects_stale_parent(tmp_path):
    async with policy_db(tmp_path) as sessions, sessions() as session:
        service = OmnigentPolicyService(session)
        first = await create_policy(service)
        changed = policy_document()
        changed["resources"]["memoryMiB"] = 8192
        second = await service.new_version(
            policy_id="policy",
            document=PolicyDocument.model_validate(changed),
            actor="editor",
            expected_parent_ref="policy@1",
        )

        assert second.version == 2
        assert second.parent_ref == "policy@1"
        assert first.digest != second.digest
        assert (await service.get_version("policy", 1)).document_json[
            "resources"
        ]["memoryMiB"] == 4096
        with pytest.raises(PolicyConflict, match="stale policy version"):
            await service.new_version(
                policy_id="policy",
                document=PolicyDocument.model_validate(changed),
                actor="stale-editor",
                expected_parent_ref="policy@1",
            )


@pytest.mark.asyncio
async def test_lifecycle_default_switch_rollback_and_historical_read(tmp_path):
    async with policy_db(tmp_path) as sessions, sessions() as session:
        service = OmnigentPolicyService(session)
        await create_policy(service)
        second_document = deepcopy(policy_document())
        second_document["resources"]["cpuMillis"] = 3000
        await service.new_version(
            policy_id="policy",
            document=PolicyDocument.model_validate(second_document),
            actor="editor",
            expected_parent_ref="policy@1",
        )
        await service.transition(
            policy_id="policy",
            version=1,
            state=PolicyState.ACTIVE,
            actor="operator",
            make_default=True,
        )
        await service.transition(
            policy_id="policy",
            version=2,
            state=PolicyState.ACTIVE,
            actor="operator",
            make_default=True,
        )
        with pytest.raises(PolicyConflict, match="switch the default first"):
            await service.transition(
                policy_id="policy",
                version=2,
                state=PolicyState.DISABLED,
                actor="operator",
            )

        # Roll back by selecting the still-valid historical authority, then retire v2.
        await service.transition(
            policy_id="policy",
            version=1,
            state=PolicyState.ACTIVE,
            actor="operator",
            make_default=True,
        )
        await service.transition(
            policy_id="policy",
            version=2,
            state=PolicyState.DISABLED,
            actor="operator",
        )

        assert (await service.get_policy("policy")).default_version == 1
        assert (await service.get_version("policy", 2)).state == "disabled"
        assert (await service.get_version("policy", 1)).state == "active"
        event_types = [event.event_type for event in await service.audit("policy")]
        assert event_types.count("default_changed") == 3
        assert "lifecycle_transition" in event_types


@pytest.mark.asyncio
async def test_clone_requires_existing_immutable_source_and_records_lineage(tmp_path):
    async with policy_db(tmp_path) as sessions, sessions() as session:
        service = OmnigentPolicyService(session)
        await create_policy(service, "source")

        clone = await service.create(
            policy_id="clone",
            name="clone name",
            owner_user_id=None,
            visibility="private",
            document=PolicyDocument.model_validate(policy_document()),
            actor="operator",
            clone_source_ref="source@1",
        )
        assert clone.clone_source_ref == "source@1"

        with pytest.raises(PolicyConflict, match="clone source does not exist"):
            await service.create(
                policy_id="broken-clone",
                name="broken clone",
                owner_user_id=None,
                visibility="private",
                document=PolicyDocument.model_validate(policy_document()),
                actor="operator",
                clone_source_ref="missing@1",
            )


@pytest.mark.asyncio
async def test_duplicate_policy_name_is_a_conflict(tmp_path):
    async with policy_db(tmp_path) as sessions, sessions() as session:
        service = OmnigentPolicyService(session)
        await create_policy(service, "first")
        with pytest.raises(PolicyConflict, match="identity or name already exists"):
            await service.create(
                policy_id="second",
                name="first name",
                owner_user_id=None,
                visibility="deployment",
                document=PolicyDocument.model_validate(policy_document()),
                actor="operator",
            )


@pytest.mark.asyncio
async def test_bound_policy_version_cannot_be_retired(tmp_path):
    async with policy_db(tmp_path) as sessions, sessions() as session:
        service = OmnigentPolicyService(session)
        await create_policy(service)
        await service.transition(
            policy_id="policy", version=1, state=PolicyState.ACTIVE,
            actor="operator", make_default=True,
        )
        changed = deepcopy(policy_document())
        changed["resources"]["cpuMillis"] = 3000
        await service.new_version(
            policy_id="policy",
            document=PolicyDocument.model_validate(changed),
            actor="operator",
            expected_parent_ref="policy@1",
        )
        await service.transition(
            policy_id="policy", version=2, state=PolicyState.ACTIVE,
            actor="operator", make_default=True,
        )
        session.add(OmnigentOAuthHostBindingRecord(
            binding_ref="binding", provider_profile_id="profile",
            endpoint_ref="default", harness="codex-native",
            credential_mount_template_json={
                "authVolumeRef": {
                    "providerProfileId": "profile", "runtimeId": "codex_cli",
                    "providerId": "openai", "volumeRef": "codex_auth_volume",
                    "credentialGeneration": 1, "ownerUserId": "user-1",
                },
                "targetPath": "/home/app/.codex", "accessMode": "read_write",
                "runtimeUid": 1000, "runtimeGid": 1000,
            },
            launch_policy_ref="policy@1",
        ))
        await session.commit()

        with pytest.raises(PolicyConflict, match="bound to an active host profile"):
            await service.transition(
                policy_id="policy", version=1, state=PolicyState.DISABLED,
                actor="operator",
            )


@pytest.mark.asyncio
async def test_runtime_resolution_requires_exact_active_valid_version(tmp_path):
    async with policy_db(tmp_path) as sessions, sessions() as session:
        service = OmnigentPolicyService(session)
        row = await create_policy(service)

        with pytest.raises(PolicyConflict, match="not active"):
            await service.resolve_runtime_snapshot("policy@1")

        await service.transition(
            policy_id="policy",
            version=1,
            state=PolicyState.ACTIVE,
            actor="operator",
            make_default=True,
        )
        snapshot = await service.resolve_runtime_snapshot("policy@1")
        assert snapshot["policyRef"] == "policy@1"
        assert snapshot["policyDigest"] == row.digest
        assert snapshot["validation"]["valid"] is True

        row.digest = "sha256:" + "0" * 64
        await session.commit()
        with pytest.raises(PolicyConflict, match="digest conflict"):
            await service.resolve_runtime_snapshot("policy@1")
