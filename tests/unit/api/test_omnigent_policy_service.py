"""Persistence-boundary tests for immutable Omnigent policy lifecycle authority."""

from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from api_service.db.models import (
    Base,
    OmnigentBridgeSession,
    OmnigentOAuthHostBindingRecord,
    OmnigentOAuthHostLeaseRecord,
    OmnigentPolicy,
    OmnigentPolicyEvent,
    OmnigentPolicyVersion,
)
from api_service.services.omnigent_policies import (
    OmnigentPolicyService,
    PolicyConflict,
    bootstrap_policies_ready,
    bootstrap_document,
    configured_bootstrap_image_refs,
    resolve_bootstrap_image_ref,
    resolve_live_server_image_ref,
    seed_bootstrap_policies,
)
from moonmind.omnigent.execution_profiles import (
    PROFILES,
    default_execution_profile_ref_for_runtime,
)
from moonmind.omnigent.policies import PolicyDocument, PolicyState, document_digest
from moonmind.security.egress import OMNIGENT_EGRESS_PROFILE
from tests.unit.omnigent.test_policy_authority import policy_document


@pytest.fixture(autouse=True)
def _stable_deployment_architecture(monkeypatch):
    monkeypatch.setattr(
        "api_service.services.omnigent_policies.platform.machine",
        lambda: "x86_64",
    )


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
                    OmnigentOAuthHostLeaseRecord.__table__,
                    OmnigentBridgeSession.__table__,
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
async def test_default_runtime_snapshot_is_durable_active_authority(tmp_path):
    async with policy_db(tmp_path) as sessions, sessions() as session:
        service = OmnigentPolicyService(session)
        await create_policy(service)
        await service.transition(
            policy_id="policy",
            version=1,
            state=PolicyState.ACTIVE,
            actor="operator",
            make_default=True,
        )

        snapshot = await service.resolve_default_runtime_snapshot("policy")

        assert snapshot["policyRef"] == "policy@1"
        assert snapshot["validation"]["valid"] is True
        assert snapshot["boundaries"]["host"] == policy_document()["host"]


@pytest.mark.asyncio
async def test_default_runtime_snapshot_rejects_missing_default(tmp_path):
    async with policy_db(tmp_path) as sessions, sessions() as session:
        service = OmnigentPolicyService(session)
        await create_policy(service)

        with pytest.raises(PolicyConflict, match="no default version"):
            await service.resolve_default_runtime_snapshot("policy")


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
        usage = await service.usage("policy", 2)
        assert usage["default"] is True
        assert usage["activationImpact"]["compatible"] is True
        assert usage["unavailabilityBlockers"] == [
            "Switch the policy default before disabling or deprecating this version."
        ]

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

        usage = await service.usage("policy", 1)
        assert usage["dependents"] == {
            "hostBindings": ["binding"],
            "hostBindingCount": 1,
            "providerProfiles": ["profile"],
            "providerProfileCount": 1,
            "workflows": [],
            "workflowCount": 0,
            "bridgeSessions": [],
            "bridgeSessionCount": 0,
            "activeBridgeSessions": [],
            "activeBridgeSessionCount": 0,
        }
        assert usage["unavailabilityBlockers"] == [
            "Move dependent host profiles before disabling or deprecating this version."
        ]

        with pytest.raises(PolicyConflict, match="bound to an active host profile"):
            await service.transition(
                policy_id="policy", version=1, state=PolicyState.DISABLED,
                actor="operator",
            )


@pytest.mark.asyncio
async def test_usage_projects_live_and_historical_bridge_dependents(tmp_path):
    async with policy_db(tmp_path) as sessions, sessions() as session:
        service = OmnigentPolicyService(session)
        await create_policy(service)
        snapshot = await service.snapshot("policy", 1)
        for session_id, workflow_id, state in (
            ("bridge-live", "workflow-live", "running"),
            ("bridge-history", "workflow-history", "completed"),
        ):
            session.add(OmnigentBridgeSession(
                bridge_session_id=session_id,
                provider="omnigent",
                compatibility_profile="v1",
                moonmind_workflow_id=workflow_id,
                moonmind_agent_run_id=f"agent-{session_id}",
                idempotency_key=f"key-{session_id}",
                provider_profile_id="profile-from-session",
                effective_launch_snapshot_json={"policyAuthority": snapshot},
                omnigent_endpoint_ref="default",
                host_type="static_compose",
                status=state,
            ))
        await session.commit()

        usage = await service.usage("policy", 1)

        assert usage["dependents"]["providerProfiles"] == ["profile-from-session"]
        assert usage["dependents"]["workflows"] == [
            "workflow-history", "workflow-live",
        ]
        assert usage["dependents"]["bridgeSessions"] == [
            "bridge-history", "bridge-live",
        ]
        assert usage["dependents"]["activeBridgeSessions"] == ["bridge-live"]
        assert usage["unavailabilityBlockers"] == [
            "Wait for dependent bridge sessions to finish before disabling or deprecating this version."
        ]
        with pytest.raises(PolicyConflict, match="active bridge session"):
            await service.transition(
                policy_id="policy",
                version=1,
                state=PolicyState.DISABLED,
                actor="operator",
            )


@pytest.mark.asyncio
async def test_usage_filters_bridge_dependents_in_the_database(tmp_path):
    async with policy_db(tmp_path) as sessions, sessions() as session:
        service = OmnigentPolicyService(session)
        await create_policy(service)
        snapshot = await service.snapshot("policy", 1)
        other = {**snapshot, "policyRef": "policy@2"}
        rows = (
            ("match-live", "wf-match-live", "running", {"policyAuthority": snapshot}),
            ("match-done", "wf-match-done", "completed", {"policyAuthority": snapshot}),
            ("other-policy", "wf-other", "running", {"policyAuthority": other}),
            ("no-authority", "wf-none", "running", {"foo": "bar"}),
            ("null-launch", "wf-null", "running", None),
        )
        for session_id, workflow_id, state, launch in rows:
            session.add(OmnigentBridgeSession(
                bridge_session_id=session_id,
                provider="omnigent",
                compatibility_profile="v1",
                moonmind_workflow_id=workflow_id,
                moonmind_agent_run_id=f"agent-{session_id}",
                idempotency_key=f"key-{session_id}",
                provider_profile_id="profile-x",
                effective_launch_snapshot_json=launch,
                omnigent_endpoint_ref="default",
                host_type="static_compose",
                status=state,
            ))
        await session.commit()

        usage = await service.usage("policy", 1)

        # Only the two policy@1 sessions are dependents; policy@2, the snapshot
        # without policyAuthority, and the null launch are all excluded in SQL.
        assert usage["dependents"]["bridgeSessions"] == ["match-done", "match-live"]
        assert usage["dependents"]["bridgeSessionCount"] == 2
        assert usage["dependents"]["activeBridgeSessions"] == ["match-live"]
        assert usage["dependents"]["activeBridgeSessionCount"] == 1
        assert usage["dependents"]["workflows"] == ["wf-match-done", "wf-match-live"]
        assert usage["dependents"]["workflowCount"] == 2


@pytest.mark.asyncio
async def test_usage_bounds_dependent_lists_but_counts_reflect_totals(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "api_service.services.omnigent_policies._USAGE_DEPENDENT_PAGE_SIZE", 1
    )
    async with policy_db(tmp_path) as sessions, sessions() as session:
        service = OmnigentPolicyService(session)
        await create_policy(service)
        snapshot = await service.snapshot("policy", 1)
        for session_id, workflow_id, state in (
            ("s-a", "wf-a", "running"),
            ("s-b", "wf-b", "running"),
            ("s-c", "wf-c", "completed"),
        ):
            session.add(OmnigentBridgeSession(
                bridge_session_id=session_id,
                provider="omnigent",
                compatibility_profile="v1",
                moonmind_workflow_id=workflow_id,
                moonmind_agent_run_id=f"agent-{session_id}",
                idempotency_key=f"key-{session_id}",
                provider_profile_id="profile-x",
                effective_launch_snapshot_json={"policyAuthority": snapshot},
                omnigent_endpoint_ref="default",
                host_type="static_compose",
                status=state,
            ))
        await session.commit()

        usage = await service.usage("policy", 1)

        # Lists are bounded to the page size; counts still report true totals.
        assert len(usage["dependents"]["bridgeSessions"]) == 1
        assert usage["dependents"]["bridgeSessionCount"] == 3
        assert len(usage["dependents"]["activeBridgeSessions"]) == 1
        assert usage["dependents"]["activeBridgeSessionCount"] == 2
        assert len(usage["dependents"]["workflows"]) == 1
        assert usage["dependents"]["workflowCount"] == 3
        # The active-session blocker keys off the count, not the truncated list.
        assert any(
            "dependent bridge sessions" in blocker
            for blocker in usage["unavailabilityBlockers"]
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


def test_bootstrap_accepts_latest_as_input_but_persists_only_resolved_authority():
    server_input, host_input = configured_bootstrap_image_refs({})
    assert server_input == "ghcr.io/omnigent-ai/omnigent-server:latest"
    assert host_input == "ghcr.io/omnigent-ai/omnigent-host:latest"

    document = bootstrap_document(
        host_mode="on_demand_docker",
        execution_profile_ref="omnigent-codex@1",
        server_image_ref="ghcr.io/omnigent-ai/omnigent-server@sha256:" + "1" * 64,
        host_image_ref="ghcr.io/omnigent-ai/omnigent-host@sha256:" + "2" * 64,
    ).model_dump(by_alias=True, mode="json")

    assert document["host"]["serverImageRef"].endswith("@sha256:" + "1" * 64)
    assert document["host"]["hostImageRef"].endswith("@sha256:" + "2" * 64)
    assert document["network"] == {
        "attachmentRef": OMNIGENT_EGRESS_PROFILE.network_ref,
        "egressProfileRef": OMNIGENT_EGRESS_PROFILE.ref,
    }


@pytest.mark.asyncio
async def test_mutable_bootstrap_image_is_refreshed_and_resolved(monkeypatch):
    digest_ref = "ghcr.io/omnigent-ai/omnigent-server@sha256:" + "a" * 64
    calls: list[tuple[str, ...]] = []

    async def command(argv, **_kwargs):
        calls.append(tuple(argv))
        if argv[1:3] == ("image", "inspect"):
            return 0, f"sha256:{'b' * 64}\t{digest_ref}\n".encode(), b""
        return 0, b"pulled", b""

    monkeypatch.setattr(
        "api_service.services.omnigent_policies.run_runtime_command", command
    )

    assert (
        await resolve_bootstrap_image_ref(
            "ghcr.io/omnigent-ai/omnigent-server:latest"
        )
        == digest_ref
    )
    assert any(call[1] == "pull" for call in calls)


@pytest.mark.asyncio
async def test_bootstrap_uses_safe_local_digest_when_latest_refresh_times_out(
    monkeypatch,
):
    digest_ref = "ghcr.io/omnigent-ai/omnigent-host@sha256:" + "c" * 64

    async def command(argv, **_kwargs):
        if argv[1:3] == ("image", "inspect"):
            return 0, f"sha256:{'d' * 64}\t{digest_ref}\n".encode(), b""
        raise TimeoutError

    monkeypatch.setattr(
        "api_service.services.omnigent_policies.run_runtime_command", command
    )

    assert (
        await resolve_bootstrap_image_ref(
            "ghcr.io/omnigent-ai/omnigent-host:latest"
        )
        == digest_ref
    )


@pytest.mark.asyncio
async def test_bootstrap_policies_activate_with_resolved_latest_images(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("MOONMIND_CONTAINER_JOBS_ENABLED", "true")
    server_digest = "ghcr.io/omnigent-ai/omnigent-server@sha256:" + "1" * 64
    host_digest = "ghcr.io/omnigent-ai/omnigent-host@sha256:" + "2" * 64
    opencode_host_digest = (
        "ghcr.io/moonladderstudios/omnigent-host-opencode@sha256:" + "3" * 64
    )
    resolution_calls: list[str] = []

    async def resolver(image_ref: str) -> str:
        resolution_calls.append(image_ref)
        if "host-opencode" in image_ref:
            return opencode_host_digest
        return host_digest if "host" in image_ref else server_digest

    async with policy_db(tmp_path) as sessions, sessions() as session:
        seeded = await seed_bootstrap_policies(
            session,
            env={
                "OMNIGENT_IMAGE_TAG": "latest",
                "OMNIGENT_HOST_IMAGE_TAG": "latest",
            },
            image_resolver=resolver,
        )

        assert set(seeded) == {
            "omnigent-codex",
            "codex-static",
            "codex-on-demand",
            "omnigent-on-demand",
            "opencode-on-demand",
        }
        versions = list(
            (
                await session.execute(
                    select(OmnigentPolicyVersion).order_by(
                        OmnigentPolicyVersion.policy_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(versions) == 5
        assert all(version.state == "active" for version in versions)
        assert all(version.validation_json["valid"] is True for version in versions)
        assert all(
            version.document_json["host"]["serverImageRef"] == server_digest
            for version in versions
        )
        by_policy = {version.policy_id: version for version in versions}
        for policy_id in {
            "omnigent-codex",
            "codex-static",
            "codex-on-demand",
        }:
            assert by_policy[policy_id].document_json["execution"] == {
                "profileRef": "omnigent-codex@1",
                "harness": "codex-native",
                "agentIdentities": ["codex-native-ui"],
            }
            assert by_policy[policy_id].document_json["host"]["hostImageRef"] == host_digest
        for policy_id in {"omnigent-on-demand", "opencode-on-demand"}:
            assert by_policy[policy_id].document_json["execution"] == {
                "profileRef": "omnigent-opencode@1",
                "harness": "opencode-native",
                "agentIdentities": ["opencode"],
            }
            assert by_policy[policy_id].document_json["providerProfile"]["compatibleProviders"] == ["opencode-go", "opencode"]
            assert by_policy[policy_id].document_json["host"]["hostImageRef"] == opencode_host_digest
            snapshot = await OmnigentPolicyService(session).resolve_runtime_snapshot(f"{policy_id}@1")
            assert snapshot["policyRef"] == f"{policy_id}@1"
        assert await bootstrap_policies_ready(session) is True
        assert await seed_bootstrap_policies(
            session, env={}, image_resolver=resolver
        ) == []
        assert len(resolution_calls) == 6


@pytest.mark.asyncio
async def test_dynamic_opencode_child_resolves_bootstrapped_policy(
    tmp_path, monkeypatch
):
    """Cover the review-loop child path that has no precompiled plan binding."""

    monkeypatch.setenv("MOONMIND_CONTAINER_JOBS_ENABLED", "true")
    server_digest = "ghcr.io/omnigent-ai/omnigent-server@sha256:" + "1" * 64
    codex_host_digest = "ghcr.io/omnigent-ai/omnigent-host@sha256:" + "2" * 64
    opencode_host_digest = (
        "ghcr.io/moonladderstudios/omnigent-host-opencode@sha256:" + "3" * 64
    )

    async def resolver(image_ref: str) -> str:
        if "host-opencode" in image_ref:
            return opencode_host_digest
        return codex_host_digest if "host" in image_ref else server_digest

    async with policy_db(tmp_path) as sessions, sessions() as session:
        await seed_bootstrap_policies(session, image_resolver=resolver)

        profile_ref = default_execution_profile_ref_for_runtime("opencode")
        policy_ref = PROFILES[profile_ref].default_policy_ref
        snapshot = await OmnigentPolicyService(session).resolve_runtime_snapshot(
            policy_ref
        )

        assert profile_ref == "omnigent-opencode@1"
        assert policy_ref == "opencode-on-demand@1"
        assert snapshot["boundaries"]["execution"]["harness"] == "opencode-native"
        assert snapshot["boundaries"]["host"]["hostImageRef"] == opencode_host_digest


@pytest.mark.asyncio
async def test_opencode_kill_switch_does_not_add_policy_or_image_dependencies(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("MOONMIND_CONTAINER_JOBS_ENABLED", "true")
    monkeypatch.setenv("MOONMIND_OMNIGENT_OPENCODE_ENABLED", "false")
    server_digest = "ghcr.io/omnigent-ai/omnigent-server@sha256:" + "1" * 64
    host_digest = "ghcr.io/omnigent-ai/omnigent-host@sha256:" + "2" * 64
    resolution_calls: list[str] = []

    async def resolver(image_ref: str) -> str:
        resolution_calls.append(image_ref)
        return host_digest if "host" in image_ref else server_digest

    async with policy_db(tmp_path) as sessions, sessions() as session:
        seeded = await seed_bootstrap_policies(
            session,
            env={"MOONMIND_OMNIGENT_OPENCODE_ENABLED": "false"},
            image_resolver=resolver,
        )

        assert set(seeded) == {
            "omnigent-codex",
            "codex-static",
            "codex-on-demand",
        }
        assert len(resolution_calls) == 2
        assert await bootstrap_policies_ready(session) is True


@pytest.mark.asyncio
async def test_bootstrap_advances_image_authority_when_mutable_inputs_move(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("MOONMIND_CONTAINER_JOBS_ENABLED", "true")
    first_server = "ghcr.io/omnigent-ai/omnigent-server@sha256:" + "1" * 64
    first_host = "ghcr.io/omnigent-ai/omnigent-host@sha256:" + "2" * 64
    next_server = "ghcr.io/omnigent-ai/omnigent-server@sha256:" + "3" * 64
    next_host = "ghcr.io/omnigent-ai/omnigent-host@sha256:" + "4" * 64
    resolved = {"server": first_server, "host": first_host}

    async def resolver(image_ref: str) -> str:
        return resolved["host" if "host" in image_ref else "server"]

    async def live_server(_image_ref: str) -> str:
        return resolved["server"]

    async with policy_db(tmp_path) as sessions, sessions() as session:
        await seed_bootstrap_policies(
            session,
            image_resolver=resolver,
            live_server_image_resolver=live_server,
        )
        session.add(
            OmnigentOAuthHostBindingRecord(
                binding_ref="bootstrap-binding",
                provider_profile_id="profile",
                endpoint_ref="default",
                harness="codex-native",
                credential_mount_template_json={
                    "authVolumeRef": {
                        "providerProfileId": "profile",
                        "runtimeId": "codex_cli",
                        "providerId": "openai",
                        "volumeRef": "codex_auth_volume",
                        "credentialGeneration": 1,
                        "ownerUserId": "user-1",
                    },
                    "targetPath": "/home/app/.codex",
                    "accessMode": "read_write",
                    "runtimeUid": 1000,
                    "runtimeGid": 1000,
                },
                launch_policy_ref="codex-on-demand@1",
                effective_launch_snapshot_json={"snapshotRef": "stale"},
            )
        )
        await session.commit()
        resolved.update(server=next_server, host=next_host)

        reconciled = await seed_bootstrap_policies(
            session,
            image_resolver=resolver,
            live_server_image_resolver=live_server,
        )

        assert set(reconciled) == {
            "omnigent-codex",
            "codex-static",
            "codex-on-demand",
            "omnigent-on-demand",
            "opencode-on-demand",
        }
        policies = list((await session.execute(select(OmnigentPolicy))).scalars())
        assert all(policy.default_version == 2 for policy in policies)
        defaults = list(
            (
                await session.execute(
                    select(OmnigentPolicyVersion).where(
                        OmnigentPolicyVersion.version == 2
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(defaults) == 5
        assert all(
            row.document_json["host"]["serverImageRef"] == next_server
            for row in defaults
        )
        assert all(
            row.document_json["host"]["hostImageRef"] == next_host
            for row in defaults
        )
        binding = await session.get(
            OmnigentOAuthHostBindingRecord,
            "bootstrap-binding",
        )
        assert binding.launch_policy_ref == "codex-on-demand@2"
        assert binding.effective_launch_snapshot_json is None
        event_types = set(
            (await session.execute(select(OmnigentPolicyEvent.event_type))).scalars()
        )
        assert "bootstrap_image_authority_cutover" in event_types


@pytest.mark.asyncio
async def test_bootstrap_does_not_rewrite_operator_owned_default_images(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("MOONMIND_CONTAINER_JOBS_ENABLED", "true")
    first_server = "ghcr.io/omnigent-ai/omnigent-server@sha256:" + "1" * 64
    first_host = "ghcr.io/omnigent-ai/omnigent-host@sha256:" + "2" * 64
    next_server = "ghcr.io/omnigent-ai/omnigent-server@sha256:" + "3" * 64
    next_host = "ghcr.io/omnigent-ai/omnigent-host@sha256:" + "4" * 64
    resolved = {"server": first_server, "host": first_host}

    async def resolver(image_ref: str) -> str:
        return resolved["host" if "host" in image_ref else "server"]

    async with policy_db(tmp_path) as sessions, sessions() as session:
        await seed_bootstrap_policies(session, image_resolver=resolver)
        service = OmnigentPolicyService(session)
        current = await service.get_version("codex-on-demand", 1)
        operator_document = PolicyDocument.model_validate(
            deepcopy(current.document_json)
        )
        operator_version = await service.new_version(
            policy_id="codex-on-demand",
            document=operator_document,
            actor="operator",
            expected_parent_ref="codex-on-demand@1",
        )
        await service.transition(
            policy_id="codex-on-demand",
            version=operator_version.version,
            state=PolicyState.ACTIVE,
            actor="operator",
            make_default=True,
        )
        resolved.update(server=next_server, host=next_host)

        await seed_bootstrap_policies(session, image_resolver=resolver)

        policy = await session.get(OmnigentPolicy, "codex-on-demand")
        assert policy.default_version == operator_version.version
        versions = await service.versions("codex-on-demand")
        assert len(versions) == 2
        assert versions[0].created_by == "operator"
        assert versions[0].document_json["host"]["serverImageRef"] == first_server
        assert versions[0].document_json["host"]["hostImageRef"] == first_host


@pytest.mark.asyncio
async def test_bootstrap_revisits_image_binding_after_active_lease_drains(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("MOONMIND_CONTAINER_JOBS_ENABLED", "true")
    first_server = "ghcr.io/omnigent-ai/omnigent-server@sha256:" + "1" * 64
    first_host = "ghcr.io/omnigent-ai/omnigent-host@sha256:" + "2" * 64
    next_server = "ghcr.io/omnigent-ai/omnigent-server@sha256:" + "3" * 64
    next_host = "ghcr.io/omnigent-ai/omnigent-host@sha256:" + "4" * 64
    resolved = {"server": first_server, "host": first_host}

    async def resolver(image_ref: str) -> str:
        return resolved["host" if "host" in image_ref else "server"]

    async def live_server(_image_ref: str) -> str:
        return resolved["server"]

    async with policy_db(tmp_path) as sessions, sessions() as session:
        await seed_bootstrap_policies(
            session,
            image_resolver=resolver,
            live_server_image_resolver=live_server,
        )
        session.add(
            OmnigentOAuthHostBindingRecord(
                binding_ref="deferred-image-binding",
                provider_profile_id="profile",
                endpoint_ref="default",
                harness="codex-native",
                credential_mount_template_json={
                    "authVolumeRef": {
                        "providerProfileId": "profile",
                        "runtimeId": "codex_cli",
                        "providerId": "openai",
                        "volumeRef": "codex_auth_volume",
                        "credentialGeneration": 1,
                        "ownerUserId": "user-1",
                    },
                    "targetPath": "/home/app/.codex",
                    "accessMode": "read_write",
                    "runtimeUid": 1000,
                    "runtimeGid": 1000,
                },
                launch_policy_ref="codex-on-demand@1",
                effective_launch_snapshot_json={"snapshotRef": "active"},
            )
        )
        now = datetime.now(UTC)
        await session.execute(
            OmnigentOAuthHostLeaseRecord.__table__.insert().values(
                lease_id="deferred-image-lease",
                provider_profile_id="profile",
                provider_lease_id="provider-lease",
                binding_ref="deferred-image-binding",
                credential_generation=1,
                holder_workflow_id="workflow-active",
                idempotency_key="workflow-active:step-1",
                lease_purpose="execution",
                status="assigned",
                acquired_at=now,
                last_heartbeat_at=now,
                host_capabilities_json={},
                expires_at=now + timedelta(hours=1),
            )
        )
        await session.commit()
        resolved.update(server=next_server, host=next_host)

        await seed_bootstrap_policies(
            session,
            image_resolver=resolver,
            live_server_image_resolver=live_server,
        )
        binding = await session.get(
            OmnigentOAuthHostBindingRecord,
            "deferred-image-binding",
        )
        assert binding.launch_policy_ref == "codex-on-demand@1"
        await session.execute(
            OmnigentOAuthHostLeaseRecord.__table__
            .update()
            .where(
                OmnigentOAuthHostLeaseRecord.lease_id
                == "deferred-image-lease"
            )
            .values(status="stopped", stopped_at=datetime.now(UTC))
        )
        await session.commit()

        reconciled = await seed_bootstrap_policies(
            session,
            image_resolver=resolver,
            live_server_image_resolver=live_server,
        )

        await session.refresh(binding)
        assert reconciled == ["codex-on-demand"]
        assert binding.launch_policy_ref == "codex-on-demand@2"
        assert binding.effective_launch_snapshot_json is None
        versions = await OmnigentPolicyService(session).versions("codex-on-demand")
        assert len(versions) == 2


@pytest.mark.asyncio
async def test_bootstrap_server_authority_follows_live_container_before_tag(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("MOONMIND_CONTAINER_JOBS_ENABLED", "true")
    first_server = "ghcr.io/omnigent-ai/omnigent-server@sha256:" + "1" * 64
    first_host = "ghcr.io/omnigent-ai/omnigent-host@sha256:" + "2" * 64
    next_server = "ghcr.io/omnigent-ai/omnigent-server@sha256:" + "3" * 64
    next_host = "ghcr.io/omnigent-ai/omnigent-host@sha256:" + "4" * 64
    resolved = {"server": first_server, "host": first_host}
    live = {"server": first_server}

    async def resolver(image_ref: str) -> str:
        return resolved["host" if "host" in image_ref else "server"]

    async def live_server(_image_ref: str) -> str:
        return live["server"]

    async with policy_db(tmp_path) as sessions, sessions() as session:
        await seed_bootstrap_policies(
            session,
            image_resolver=resolver,
            live_server_image_resolver=live_server,
        )
        resolved.update(server=next_server, host=next_host)

        await seed_bootstrap_policies(
            session,
            image_resolver=resolver,
            live_server_image_resolver=live_server,
        )

        policy = await session.get(OmnigentPolicy, "codex-on-demand")
        current = await OmnigentPolicyService(session).get_version(
            "codex-on-demand",
            policy.default_version,
        )
        assert current.document_json["host"]["serverImageRef"] == first_server
        assert current.document_json["host"]["hostImageRef"] == next_host

        live["server"] = next_server
        await seed_bootstrap_policies(
            session,
            image_resolver=resolver,
            live_server_image_resolver=live_server,
        )

        await session.refresh(policy)
        current = await OmnigentPolicyService(session).get_version(
            "codex-on-demand",
            policy.default_version,
        )
        assert policy.default_version == 3
        assert current.document_json["host"]["serverImageRef"] == next_server


@pytest.mark.asyncio
async def test_bootstrap_advances_stale_interrupted_draft_lineage(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("MOONMIND_CONTAINER_JOBS_ENABLED", "true")
    first_server = "ghcr.io/omnigent-ai/omnigent-server@sha256:" + "1" * 64
    first_host = "ghcr.io/omnigent-ai/omnigent-host@sha256:" + "2" * 64
    stale_server = "ghcr.io/omnigent-ai/omnigent-server@sha256:" + "3" * 64
    stale_host = "ghcr.io/omnigent-ai/omnigent-host@sha256:" + "4" * 64
    next_server = "ghcr.io/omnigent-ai/omnigent-server@sha256:" + "5" * 64
    next_host = "ghcr.io/omnigent-ai/omnigent-host@sha256:" + "6" * 64
    resolved = {"server": first_server, "host": first_host}

    async def resolver(image_ref: str) -> str:
        return resolved["host" if "host" in image_ref else "server"]

    async def live_server(_image_ref: str) -> str:
        return resolved["server"]

    async with policy_db(tmp_path) as sessions, sessions() as session:
        await seed_bootstrap_policies(
            session,
            image_resolver=resolver,
            live_server_image_resolver=live_server,
        )
        service = OmnigentPolicyService(session)
        current = await service.get_version("codex-on-demand", 1)
        stale_payload = deepcopy(current.document_json)
        stale_payload["host"]["serverImageRef"] = stale_server
        stale_payload["host"]["hostImageRef"] = stale_host
        stale = await service.new_version(
            policy_id="codex-on-demand",
            document=PolicyDocument.model_validate(stale_payload),
            actor="bootstrap",
            expected_parent_ref="codex-on-demand@1",
        )
        assert stale.state == PolicyState.DRAFT.value
        resolved.update(server=next_server, host=next_host)

        reconciled = await seed_bootstrap_policies(
            session,
            image_resolver=resolver,
            live_server_image_resolver=live_server,
        )

        policy = await session.get(OmnigentPolicy, "codex-on-demand")
        latest = await service.get_version("codex-on-demand", 3)
        assert "codex-on-demand" in reconciled
        assert policy.default_version == 3
        assert latest.parent_ref == "codex-on-demand@2"
        assert latest.state == PolicyState.ACTIVE.value
        assert latest.document_json["host"]["serverImageRef"] == next_server
        assert latest.document_json["host"]["hostImageRef"] == next_host


@pytest.mark.asyncio
async def test_live_server_image_resolver_reads_running_compose_image(monkeypatch):
    digest_ref = "ghcr.io/omnigent-ai/omnigent-server@sha256:" + "a" * 64
    image_id = "sha256:" + "b" * 64
    calls: list[tuple[str, ...]] = []

    async def command(argv, **_kwargs):
        calls.append(tuple(argv))
        if argv[1] == "ps":
            return 0, b"container-id\n", b""
        if argv[1:3] == ("inspect", "--format"):
            return 0, f"{image_id}\n".encode(), b""
        if argv[1:3] == ("image", "inspect"):
            return 0, f"{image_id}\t{digest_ref}\n".encode(), b""
        raise AssertionError(argv)

    monkeypatch.setattr(
        "api_service.services.omnigent_policies.run_runtime_command", command
    )

    assert await resolve_live_server_image_ref(digest_ref) == digest_ref
    assert any(
        "com.docker.compose.service=omnigent" in argument
        for call in calls
        for argument in call
    )


@pytest.mark.asyncio
async def test_bootstrap_seed_cuts_over_legacy_stock_agent_identity(tmp_path, monkeypatch):
    """Legacy bootstrap identity advances through an immutable version cutover."""

    monkeypatch.setenv("MOONMIND_CONTAINER_JOBS_ENABLED", "true")
    server_digest = "ghcr.io/omnigent-ai/omnigent-server@sha256:" + "1" * 64
    host_digest = "ghcr.io/omnigent-ai/omnigent-host@sha256:" + "2" * 64

    async def resolver(image_ref: str) -> str:
        return host_digest if "host" in image_ref else server_digest

    async with policy_db(tmp_path) as sessions, sessions() as session:
        await seed_bootstrap_policies(session, image_resolver=resolver)
        versions = list(
            (await session.execute(select(OmnigentPolicyVersion))).scalars().all()
        )
        for version in versions:
            if version.policy_id not in {
                "omnigent-codex",
                "codex-static",
                "codex-on-demand",
            }:
                continue
            legacy = deepcopy(version.document_json)
            legacy["execution"]["agentIdentities"] = ["codex"]
            version.document_json = legacy
            version.digest = document_digest(legacy)
        session.add(
            OmnigentOAuthHostBindingRecord(
                binding_ref="bootstrap-binding",
                provider_profile_id="profile",
                endpoint_ref="default",
                harness="codex-native",
                credential_mount_template_json={
                    "authVolumeRef": {
                        "providerProfileId": "profile",
                        "runtimeId": "codex_cli",
                        "providerId": "openai",
                        "volumeRef": "codex_auth_volume",
                        "credentialGeneration": 1,
                        "ownerUserId": "user-1",
                    },
                    "targetPath": "/home/app/.codex",
                    "accessMode": "read_write",
                    "runtimeUid": 1000,
                    "runtimeGid": 1000,
                },
                launch_policy_ref="codex-on-demand@1",
                effective_launch_snapshot_json={"snapshotRef": "stale"},
            )
        )
        now = datetime.now(UTC)
        await session.execute(
            OmnigentOAuthHostLeaseRecord.__table__.insert().values(
                lease_id="active-bootstrap-lease",
                provider_profile_id="profile",
                provider_lease_id="provider-lease",
                binding_ref="bootstrap-binding",
                credential_generation=1,
                holder_workflow_id="workflow-active",
                idempotency_key="workflow-active:step-1",
                lease_purpose="execution",
                status="assigned",
                acquired_at=now,
                last_heartbeat_at=now,
                host_capabilities_json={},
                expires_at=now + timedelta(hours=1),
            )
        )
        await session.commit()

        seeded = await seed_bootstrap_policies(session, image_resolver=resolver)

        binding = await session.get(
            OmnigentOAuthHostBindingRecord,
            "bootstrap-binding",
        )
        assert binding.launch_policy_ref == "codex-on-demand@1"
        assert binding.effective_launch_snapshot_json == {"snapshotRef": "stale"}
        await session.execute(
            OmnigentOAuthHostLeaseRecord.__table__
            .update()
            .where(
                OmnigentOAuthHostLeaseRecord.lease_id
                == "active-bootstrap-lease"
            )
            .values(status="stopped", stopped_at=datetime.now(UTC))
        )
        await session.commit()
        seeded_after_drain = await seed_bootstrap_policies(
            session,
            image_resolver=resolver,
        )

        assert set(seeded) == {
            "omnigent-codex",
            "codex-static",
            "codex-on-demand",
        }
        assert seeded_after_drain == ["codex-on-demand"]
        policies = list((await session.execute(select(OmnigentPolicy))).scalars())
        assert all(
            policy.default_version == (
                2 if policy.policy_id in {
                    "omnigent-codex", "codex-static", "codex-on-demand"
                } else 1
            )
            for policy in policies
        )
        migrated_versions = list(
            (
                await session.execute(
                    select(OmnigentPolicyVersion).order_by(
                        OmnigentPolicyVersion.policy_id,
                        OmnigentPolicyVersion.version,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(migrated_versions) == 8
        for version in migrated_versions:
            if version.policy_id in {
                "omnigent-codex",
                "codex-static",
                "codex-on-demand",
            }:
                expected_identity = (
                    ["codex"] if version.version == 1 else ["codex-native-ui"]
                )
            else:
                expected_identity = ["opencode"]
            assert version.state == PolicyState.ACTIVE.value
            assert version.document_json["execution"]["agentIdentities"] == (
                expected_identity
            )
            if version.version == 2:
                assert version.parent_ref == f"{version.policy_id}@1"
        event_types = set(
            (await session.execute(select(OmnigentPolicyEvent.event_type))).scalars()
        )
        assert "bootstrap_agent_identity_cutover" in event_types
        binding = await session.get(
            OmnigentOAuthHostBindingRecord,
            "bootstrap-binding",
        )
        assert binding.launch_policy_ref == "codex-on-demand@2"
        assert binding.effective_launch_snapshot_json is None
