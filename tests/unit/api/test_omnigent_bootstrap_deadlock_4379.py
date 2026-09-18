"""Replay #4379: default < latest-active deadlock wedges bootstrap auto-update.

Minimized fixture: policy default v1 (old digest) + active non-default v2
(same old digest) + resolved image advanced to a new digest. Pre-fix
``seed_bootstrap_policies`` defers forever ("later policy version owns
evolution") and creates nothing; post-fix it creates/activates v3 (or
equivalent) without manual DB writes.
"""

from __future__ import annotations

import pytest

from api_service.services import omnigent_policies as policies
from moonmind.omnigent.policies import PolicyDocument, PolicyState
from tests.unit.api.test_omnigent_policy_service import policy_db


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
    # Live container already carries the resolved digest: deployment intent is
    # authority, not a pulled tag.
    return image_ref


def _no_daemon(monkeypatch):
    async def command(argv, **kwargs):
        raise AssertionError(f"bootstrap must not probe Docker: {argv!r}")

    monkeypatch.setattr(policies, "run_runtime_command", command)


@pytest.mark.asyncio
async def test_bootstrap_advances_past_stale_active_non_default(tmp_path, monkeypatch):
    """Default v1 + active v2 (old image) + advanced image => v3 default."""
    monkeypatch.setenv("MOONMIND_CONTAINER_JOBS_ENABLED", "true")
    _no_daemon(monkeypatch)
    async with policy_db(tmp_path) as sessions, sessions() as session:
        await policies.seed_bootstrap_policies(
            session,
            image_resolver=_image_resolver_factory(OLD_SERVER, OLD_HOST),
            live_server_image_resolver=_live_server,
        )
        service = policies.OmnigentPolicyService(session)
        policy_id = "omnigent-on-demand"
        default = await service.resolve_default_runtime_snapshot(policy_id)
        assert default["policyRef"] == f"{policy_id}@1"
        assert default["boundaries"]["host"]["hostImageRef"] == OLD_HOST

        # Active non-default v2: same old image, operator resource tweak.
        # Mirrors issue v15 (resource-only change, same old image).
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
        assert tweaked.resources.cpu_millis == 3000
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
        # Default stays at v1 while latest active is v2: the deadlock shape.

        reconciled = await policies.seed_bootstrap_policies(
            session,
            image_resolver=_image_resolver_factory(NEW_SERVER, NEW_HOST),
            live_server_image_resolver=_live_server,
        )
        assert policy_id in reconciled
        latest = await service.resolve_default_runtime_snapshot(policy_id)
        assert latest["policyRef"] == f"{policy_id}@3"
        assert latest["boundaries"]["host"]["hostImageRef"] == NEW_HOST
        assert latest["boundaries"]["host"]["serverImageRef"] == NEW_SERVER
        # History preserved: v1 and v2 still resolve.
        assert (await service.resolve_runtime_snapshot(f"{policy_id}@1"))["boundaries"][
            "host"
        ]["hostImageRef"] == OLD_HOST
        assert (await service.resolve_runtime_snapshot(f"{policy_id}@2"))["boundaries"][
            "host"
        ]["hostImageRef"] == OLD_HOST


@pytest.mark.asyncio
async def test_version_then_compile_uses_fresh_default(tmp_path, monkeypatch):
    """Single flow: deadlock reconcile to @3, then compile against @3 (R2).

    Versioning (R1) and same-repo compile tolerance were proven as separate
    unit pieces. This closes the composition gap: the freshly auto-versioned
    default ref must compile with the advanced Host Class image, while a
    foreign image still fails closed (covered by
    test_foreign_repository_compile_fails_with_actionable_evidence).
    """
    from types import SimpleNamespace

    from api_service.services import omnigent_execution_plan_service as plans
    from tests.unit.omnigent.test_planning_host_drift import (
        _ArtifactService,
        _drift_snapshot,
        _mock_current_host,
        _PlanStore,
    )

    monkeypatch.setenv("MOONMIND_CONTAINER_JOBS_ENABLED", "true")
    _no_daemon(monkeypatch)
    async with policy_db(tmp_path) as sessions, sessions() as session:
        await policies.seed_bootstrap_policies(
            session,
            image_resolver=_image_resolver_factory(OLD_SERVER, OLD_HOST),
            live_server_image_resolver=_live_server,
        )
        service = policies.OmnigentPolicyService(session)
        policy_id = "omnigent-on-demand"
        default = await service.resolve_default_runtime_snapshot(policy_id)
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
        assert tweaked.resources.cpu_millis == 3000
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
        reconciled = await policies.seed_bootstrap_policies(
            session,
            image_resolver=_image_resolver_factory(NEW_SERVER, NEW_HOST),
            live_server_image_resolver=_live_server,
        )
        assert policy_id in reconciled
        latest = await service.resolve_default_runtime_snapshot(policy_id)
        assert latest["policyRef"] == f"{policy_id}@3"
        assert latest["boundaries"]["host"]["hostImageRef"] == NEW_HOST

        # Compile against the freshly versioned default with the real
        # session: policy authority comes from @3, Host Class from NEW_HOST.
        _mock_current_host(monkeypatch, NEW_HOST)

        async def _no_catalog(**_kwargs):
            return None

        monkeypatch.setattr(
            plans, "_try_load_real_harness_config", _no_catalog
        )
        monkeypatch.setattr(
            plans,
            "resolve_execution_evidence",
            lambda plan_payload, **_kwargs: (
                {
                    "schemaVersion": 1,
                    "evidenceIssuer": "test",
                    "status": "passed",
                    "sourceCommit": "abc",
                    "protectedRunRef": "https://example.invalid/r/1",
                    "evidenceManifestRef": "artifact://m",
                    "evidenceManifestDigest": "sha256:" + "6" * 64,
                    "generatedAt": "2026-01-01T00:00:00Z",
                    "expiresAt": "2026-01-08T00:00:00Z",
                    "supportClassification": "fully_managed",
                    "supportCombinationKey": plan_payload.supportCombinationKey,
                    "supportIdentity": plan_payload.supportIdentity.model_dump(
                        mode="json", by_alias=True
                    ),
                    "hostImageRef": plan_payload.hostImageRef,
                    "policySnapshotDigest": plan_payload.policySnapshotDigest,
                    "effectiveLaunchSnapshotDigest": (
                        plan_payload.effectiveLaunchSnapshotDigest
                    ),
                    "policyGateRef": "deployment-ready",
                    "policyQualified": True,
                    "exactArtifactsVerified": True,
                    "featureGeneration": 1,
                    "replayCompatibilityVersion": 1,
                    "rollbackPolicyVersion": 1,
                },
                "supported",
            ),
        )
        snapshot = _drift_snapshot(policy=f"{policy_id}@3")
        result = await plans.compile_and_persist_execution_plan(
            session_factory=object(),
            artifact_service=_ArtifactService(),
            principal="user-1",
            workflow_id="mm:test-4379-version-then-compile",
            agent_profile_snapshot=snapshot,
            provider_profile=SimpleNamespace(
                profile_id="provider-opencode-native",
                runtime_id="opencode",
                provider_id="opencode-go",
            ),
            initial_parameters={
                "model": "example/model",
                "targetRuntime": "omnigent",
                "publishMode": "none",
                "maxAttempts": 2,
                "workflow": {"instructions": "drift."},
            },
            authored_request_ref="art_request_1",
            authored_request_digest="sha256:" + "1" * 64,
            task_input_snapshot_ref="art_request_1",
            task_input_snapshot_digest="sha256:" + "1" * 64,
            execution_plan_store=_PlanStore(object()),
            db_session=session,
        )
        assert result.envelope.payload.hostImageRef == NEW_HOST


@pytest.mark.asyncio
async def test_bootstrap_reuses_later_active_with_desired_digest(tmp_path, monkeypatch):
    """No duplicate successor when a later active already carries the image."""
    monkeypatch.setenv("MOONMIND_CONTAINER_JOBS_ENABLED", "true")
    _no_daemon(monkeypatch)
    async with policy_db(tmp_path) as sessions, sessions() as session:
        await policies.seed_bootstrap_policies(
            session,
            image_resolver=_image_resolver_factory(OLD_SERVER, OLD_HOST),
            live_server_image_resolver=_live_server,
        )
        service = policies.OmnigentPolicyService(session)
        policy_id = "omnigent-on-demand"
        default = await service.resolve_default_runtime_snapshot(policy_id)
        new_doc = PolicyDocument.model_validate(
            {
                **default["boundaries"],
                "host": {
                    **default["boundaries"]["host"],
                    "serverImageRef": NEW_SERVER,
                    "hostImageRef": NEW_HOST,
                },
            }
        )
        v2 = await service.new_version(
            policy_id=policy_id,
            document=new_doc,
            actor="bootstrap",
            expected_parent_ref=f"{policy_id}@1",
        )
        await service.transition(
            policy_id=policy_id,
            version=v2.version,
            state=PolicyState.ACTIVE,
            actor="bootstrap",
        )
        reconciled = await policies.seed_bootstrap_policies(
            session,
            image_resolver=_image_resolver_factory(NEW_SERVER, NEW_HOST),
            live_server_image_resolver=_live_server,
        )
        assert policy_id in reconciled
        latest = await service.resolve_default_runtime_snapshot(policy_id)
        assert latest["policyRef"] == f"{policy_id}@2"
        assert len(await service.versions(policy_id)) == 2
