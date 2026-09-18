"""#4379 R3: Agent Profiles advance across same-policy cutovers automatically.

When bootstrap reconcile moves a policy default from @1 to @3 for a
compatible rebuild, active profiles admitting @1 must advance to an
equivalent version admitting @3 so `refresh_schedule_deployment_snapshot`
can cut over without manual profile/schedule edits. In-flight runs keep
their recorded version authority (usages are never rewritten).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Base,
    OmnigentAgentProfile,
    OmnigentAgentProfileAuditEvent,
    OmnigentAgentProfileUsage,
    OmnigentAgentProfileVersion,
)
from api_service.services.omnigent_agent_profile_selection import (
    advance_agent_profiles_for_policy_cutover,
)


def _v1_document(policy_ref: str) -> dict:
    return {
        "schemaVersion": "moonmind.omnigent-agent-profile.v1",
        "endpointRef": "default",
        "bridgeMode": "embedded",
        "source": {"upstreamId": "agent-1"},
        "harness": "opencode-native",
        "requiredCapabilities": [],
        "execution": {
            "defaultExecutionProfileRef": "omnigent-opencode@1",
            "allowedLaunchPolicyRefs": [policy_ref],
        },
        "providerRequirements": {
            "runtimeId": "opencode",
            "providerIds": [],
            "credentialSource": "secret_ref",
            "materializationMode": "generated_file",
        },
        "model": {},
        "workspace": {"mutation": "allowed"},
        "skills": [],
        "tools": [],
        "capture": {"stream": True},
        "continuations": {"checkpoint": True},
        "publish": {"mode": "none"},
        "policyRef": policy_ref,
    }


@asynccontextmanager
async def profile_db(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/agent-profiles-4379.db", future=True
    )
    sessions = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: Base.metadata.create_all(
                sync_connection,
                tables=[
                    OmnigentAgentProfile.__table__,
                    OmnigentAgentProfileVersion.__table__,
                    OmnigentAgentProfileAuditEvent.__table__,
                    OmnigentAgentProfileUsage.__table__,
                ],
            )
        )
    try:
        yield sessions
    finally:
        await engine.dispose()


async def _seed_profile(session, profile_id="profile-1", policy_ref="p@1"):
    from api_service.services.omnigent_agent_profile_selection import (
        _profile_document_digest,
    )

    document = _v1_document(policy_ref)
    session.add(
        OmnigentAgentProfile(
            profile_id=profile_id,
            display_name="Test",
            state="active",
            active_version=1,
        )
    )
    session.add(
        OmnigentAgentProfileVersion(
            profile_id=profile_id,
            version=1,
            digest=_profile_document_digest(document),
            document=document,
            parent_version=None,
            upstream_snapshot={},
            validation_result={"ready": True},
            rollout_metadata={"origin": "test"},
        )
    )
    await session.commit()


@pytest.mark.asyncio
async def test_profile_advances_across_same_policy_cutover(tmp_path):
    async with profile_db(tmp_path) as sessions, sessions() as session:
        await _seed_profile(session)
        advanced = await advance_agent_profiles_for_policy_cutover(
            session, cutovers={"p@1": "p@3"}
        )
        await session.commit()
        assert len(advanced) == 1
        assert advanced[0]["profileId"] == "profile-1"
        assert advanced[0]["version"] == 2
        profile = await session.get(OmnigentAgentProfile, "profile-1")
        assert profile.active_version == 2
        current = await session.scalar(
            __import__("sqlalchemy").select(OmnigentAgentProfileVersion).where(
                OmnigentAgentProfileVersion.profile_id == "profile-1",
                OmnigentAgentProfileVersion.version == 2,
            )
        )
        assert current.document["execution"]["allowedLaunchPolicyRefs"] == ["p@3"]
        assert current.document["policyRef"] == "p@3"
        assert current.parent_version == 1


@pytest.mark.asyncio
async def test_profile_cutover_is_idempotent(tmp_path):
    async with profile_db(tmp_path) as sessions, sessions() as session:
        await _seed_profile(session)
        await advance_agent_profiles_for_policy_cutover(
            session, cutovers={"p@1": "p@3"}
        )
        await session.commit()
        again = await advance_agent_profiles_for_policy_cutover(
            session, cutovers={"p@1": "p@3"}
        )
        await session.commit()
        # Already cut over: nothing left admitting the predecessor ref.
        assert again == []
        from sqlalchemy import func, select

        count = await session.scalar(
            select(func.count())
            .select_from(OmnigentAgentProfileVersion)
            .where(OmnigentAgentProfileVersion.profile_id == "profile-1")
        )
        assert count == 2


@pytest.mark.asyncio
async def test_profile_cutover_ignores_different_policy_identity(tmp_path):
    async with profile_db(tmp_path) as sessions, sessions() as session:
        await _seed_profile(session)
        advanced = await advance_agent_profiles_for_policy_cutover(
            session, cutovers={"p@1": "q@1"}
        )
        assert advanced == []
        profile = await session.get(OmnigentAgentProfile, "profile-1")
        assert profile.active_version == 1


@pytest.mark.asyncio
async def test_profile_cutover_preserves_in_flight_usage(tmp_path):
    async with profile_db(tmp_path) as sessions, sessions() as session:
        await _seed_profile(session)
        session.add(
            OmnigentAgentProfileUsage(
                consumer_type="schedule",
                consumer_id="schedule-1",
                profile_id="profile-1",
                version=1,
                digest="sha256:" + "1" * 64,
                effective_snapshot={"version": 1},
            )
        )
        await session.commit()
        await advance_agent_profiles_for_policy_cutover(
            session, cutovers={"p@1": "p@3"}
        )
        await session.commit()
        from sqlalchemy import select

        usage = await session.scalar(
            select(OmnigentAgentProfileUsage).where(
                OmnigentAgentProfileUsage.consumer_id == "schedule-1"
            )
        )
        assert usage.version == 1
        assert usage.effective_snapshot == {"version": 1}


@pytest.mark.asyncio
async def test_profile_cutover_deduplicates_successor_refs(tmp_path):
    """P1: a profile admitting both predecessor and successor keeps one ref."""
    from api_service.services.omnigent_agent_profile_selection import (
        _profile_document_digest,
    )

    async with profile_db(tmp_path) as sessions, sessions() as session:
        document = _v1_document("p@1")
        document["execution"]["allowedLaunchPolicyRefs"] = ["p@1", "p@3"]
        document["policyRef"] = "p@1"
        session.add(
            OmnigentAgentProfile(
                profile_id="profile-1",
                display_name="Test",
                state="active",
                active_version=1,
            )
        )
        session.add(
            OmnigentAgentProfileVersion(
                profile_id="profile-1",
                version=1,
                digest=_profile_document_digest(document),
                document=document,
                parent_version=None,
                upstream_snapshot={},
                validation_result={"ready": True},
                rollout_metadata={"origin": "test"},
            )
        )
        await session.commit()
        advanced = await advance_agent_profiles_for_policy_cutover(
            session, cutovers={"p@1": "p@3"}
        )
        await session.commit()
        assert len(advanced) == 1
        from sqlalchemy import select

        current = await session.scalar(
            select(OmnigentAgentProfileVersion).where(
                OmnigentAgentProfileVersion.profile_id == "profile-1",
                OmnigentAgentProfileVersion.version == advanced[0]["version"],
            )
        )
        assert current.document["execution"]["allowedLaunchPolicyRefs"] == ["p@3"]


@pytest.mark.asyncio
async def test_profile_cutover_keeps_concurrent_activation(tmp_path):
    """P1: an explicit concurrent activation must not be overwritten."""
    from api_service.services.omnigent_agent_profile_selection import (
        _profile_document_digest,
    )

    async with profile_db(tmp_path) as sessions, sessions() as session:
        await _seed_profile(session)
        operator_document = _v1_document("q@9")
        session.add(
            OmnigentAgentProfileVersion(
                profile_id="profile-1",
                version=2,
                digest=_profile_document_digest(operator_document),
                document=operator_document,
                parent_version=1,
                upstream_snapshot={},
                validation_result={"ready": True},
                rollout_metadata={"origin": "operator"},
            )
        )
        profile = await session.get(OmnigentAgentProfile, "profile-1")
        profile.active_version = 2
        await session.commit()
        advanced = await advance_agent_profiles_for_policy_cutover(
            session, cutovers={"p@1": "p@3"}
        )
        await session.commit()
        assert advanced == []
        profile = await session.get(OmnigentAgentProfile, "profile-1")
        assert profile.active_version == 2


@pytest.mark.asyncio
async def test_profile_cutover_skips_unvalidated_matching_version(tmp_path):
    """P2: never activate a matching version whose validation is not ready."""
    from api_service.services.omnigent_agent_profile_selection import (
        _profile_document_digest,
    )

    async with profile_db(tmp_path) as sessions, sessions() as session:
        await _seed_profile(session)
        # An identical inactive version exists but was never validated.
        unvalidated = _v1_document("p@3")
        session.add(
            OmnigentAgentProfileVersion(
                profile_id="profile-1",
                version=2,
                digest=_profile_document_digest(unvalidated),
                document=unvalidated,
                parent_version=1,
                upstream_snapshot={},
                validation_result={"ready": False},
                rollout_metadata={"origin": "test"},
            )
        )
        await session.commit()
        advanced = await advance_agent_profiles_for_policy_cutover(
            session, cutovers={"p@1": "p@3"}
        )
        await session.commit()
        assert advanced == []
        profile = await session.get(OmnigentAgentProfile, "profile-1")
        assert profile.active_version == 1
