"""Hermetic #4379 integration: deadlock reconcile cuts policy AND profile.

Exercises the policy/bootstrap/schedule boundaries together on disposable
SQLite storage (no production execution, no registry): default v1 (old
digest) + active non-default v2 + advanced resolved image must yield default
@3 with the new image, an auto-advanced Agent Profile admitting @3, and
preserved v1/v2 history — without manual DB writes or manual
profile/schedule edits. In-flight usages keep their pinned version.
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
    OmnigentBridgeSession,
    OmnigentOAuthHostBindingRecord,
    OmnigentOAuthHostLeaseRecord,
    OmnigentPolicy,
    OmnigentPolicyEvent,
    OmnigentPolicyVersion,
)
from api_service.services import omnigent_policies as policies
from api_service.services.omnigent_agent_profile_selection import (
    _profile_document_digest,
    advance_agent_profiles_for_policy_cutover,
)
from moonmind.omnigent.policies import PolicyDocument, PolicyState
from moonmind.workflows.skills.omnigent_release import (
    raise_for_release_policy_drift,
    release_policy_drift_dispositions,
)

pytestmark = [pytest.mark.integration, pytest.mark.integration_ci]

OLD_SERVER = "ghcr.io/example/omnigent-server@sha256:" + "a" * 64
OLD_HOST = "ghcr.io/example/omnigent-host@sha256:" + "a" * 64
NEW_SERVER = "ghcr.io/example/omnigent-server@sha256:" + "b" * 64
NEW_HOST = "ghcr.io/example/omnigent-host@sha256:" + "b" * 64


def _image_resolver_factory(server_ref: str, host_ref: str):
    async def _image(image_ref: str):
        kind = "host" if "host" in image_ref else "server"
        return host_ref if kind == "host" else server_ref

    return _image


async def _live_server(image_ref: str):
    return image_ref


def _profile_document(policy_ref: str) -> dict:
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
async def cutover_db(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/cutover-4379.db", future=True
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
                    OmnigentOAuthHostLeaseRecord.__table__,
                    OmnigentBridgeSession.__table__,
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


@pytest.mark.asyncio
async def test_deadlock_reconcile_advances_policy_and_profile(tmp_path, monkeypatch):
    """End-to-end without manual edits; in-flight usage keeps old authority."""
    monkeypatch.setenv("MOONMIND_CONTAINER_JOBS_ENABLED", "true")

    async def _no_docker(argv, **kwargs):
        raise AssertionError(f"bootstrap must not probe Docker: {argv!r}")

    monkeypatch.setattr(policies, "run_runtime_command", _no_docker)
    monkeypatch.setattr(
        "api_service.services.omnigent_policies.platform.machine",
        lambda: "x86_64",
    )
    async with cutover_db(tmp_path) as sessions, sessions() as session:
        await policies.seed_bootstrap_policies(
            session,
            image_resolver=_image_resolver_factory(OLD_SERVER, OLD_HOST),
            live_server_image_resolver=_live_server,
        )
        service = policies.OmnigentPolicyService(session)
        policy_id = "omnigent-on-demand"
        default = await service.resolve_default_runtime_snapshot(policy_id)
        assert default["boundaries"]["host"]["hostImageRef"] == OLD_HOST

        # Active non-default v2: the deadlock shape (issue v15).
        current_doc = PolicyDocument.model_validate(dict(default["boundaries"]))
        tweaked = PolicyDocument.model_validate(
            {
                **current_doc.model_dump(by_alias=True),
                "resources": {
                    **current_doc.model_dump(by_alias=True)["resources"],
                    "cpuMillis": 3000,
                },
            }
        )
        v2 = await service.new_version(
            policy_id=policy_id,
            document=tweaked,
            actor="operator",
            expected_parent_ref=f"{policy_id}@1",
        )
        await service.transition(
            policy_id=policy_id,
            version=v2.version,
            state=PolicyState.ACTIVE,
            actor="operator",
        )

        # Schedule pins @1; profile allows only @1; usage records v1.
        document = _profile_document(f"{policy_id}@1")
        session.add(
            OmnigentAgentProfile(
                profile_id="profile-4379",
                display_name="Schedule profile",
                state="active",
                active_version=1,
            )
        )
        session.add(
            OmnigentAgentProfileVersion(
                profile_id="profile-4379",
                version=1,
                digest=_profile_document_digest(document),
                document=document,
                parent_version=None,
                upstream_snapshot={},
                validation_result={"ready": True},
                rollout_metadata={"origin": "test"},
            )
        )
        session.add(
            OmnigentAgentProfileUsage(
                consumer_type="schedule",
                consumer_id="schedule-4379",
                profile_id="profile-4379",
                version=1,
                digest=_profile_document_digest(document),
                effective_snapshot={"launchPolicyRef": f"{policy_id}@1"},
            )
        )
        await session.commit()

        # Pre-fix shape: drift is a compatible same-repo rebuild.
        pre = release_policy_drift_dispositions(
            {policy_id: OLD_HOST}, {"opencode": NEW_HOST}
        )
        assert len(pre) == 1 and pre[0]["compatibleRebuild"] is True

        reconciled = await policies.seed_bootstrap_policies(
            session,
            image_resolver=_image_resolver_factory(NEW_SERVER, NEW_HOST),
            live_server_image_resolver=_live_server,
        )
        assert policy_id in reconciled

        latest = await service.resolve_default_runtime_snapshot(policy_id)
        assert latest["policyRef"] == f"{policy_id}@3"
        assert latest["boundaries"]["host"]["hostImageRef"] == NEW_HOST

        # Profile auto-advanced by the reconcile wiring: no manual edit.
        profile = await session.get(OmnigentAgentProfile, "profile-4379")
        assert profile.active_version == 2
        from sqlalchemy import select

        current = await session.scalar(
            select(OmnigentAgentProfileVersion).where(
                OmnigentAgentProfileVersion.profile_id == "profile-4379",
                OmnigentAgentProfileVersion.version == 2,
            )
        )
        assert current.document["execution"]["allowedLaunchPolicyRefs"] == [
            f"{policy_id}@3"
        ]
        # The active profile now admits exactly one same-policy candidate,
        # which is what `refresh_schedule_deployment_snapshot` requires.
        candidates = [
            ref
            for ref in current.document["execution"]["allowedLaunchPolicyRefs"]
            if ref.rpartition("@")[0] == policy_id
        ]
        assert candidates == [f"{policy_id}@3"]

        # In-flight usage keeps the recorded v1 authority.
        usage = await session.scalar(
            select(OmnigentAgentProfileUsage).where(
                OmnigentAgentProfileUsage.consumer_id == "schedule-4379"
            )
        )
        assert usage.version == 1
        assert usage.effective_snapshot == {"launchPolicyRef": f"{policy_id}@1"}

        # Post-cut: no drift remains, promotion unfenced.
        post = release_policy_drift_dispositions(
            {policy_id: NEW_HOST}, {"opencode": NEW_HOST}
        )
        assert post == []
        assert raise_for_release_policy_drift(post) is None

        # History preserved: v1 and v2 still resolve on the old image.
        for version in (1, 2):
            snapshot = await service.resolve_runtime_snapshot(
                f"{policy_id}@{version}"
            )
            assert (
                snapshot["boundaries"]["host"]["hostImageRef"] == OLD_HOST
            )


@pytest.mark.asyncio
async def test_profile_cutover_helper_advances_without_reconcile(tmp_path):
    """Direct helper proof independent of the seed wiring."""
    async with cutover_db(tmp_path) as sessions, sessions() as session:
        document = _profile_document("p@1")
        session.add(
            OmnigentAgentProfile(
                profile_id="profile-direct",
                display_name="Direct",
                state="active",
                active_version=1,
            )
        )
        session.add(
            OmnigentAgentProfileVersion(
                profile_id="profile-direct",
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
            session, cutovers={"p@1": "p@2"}
        )
        await session.commit()
        assert [row["profileId"] for row in advanced] == ["profile-direct"]
        profile = await session.get(OmnigentAgentProfile, "profile-direct")
        assert profile.active_version == 2
