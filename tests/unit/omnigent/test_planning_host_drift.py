"""Planning reconciles same-repo host rebuild drift instead of failing.

Rebuilt host images change SHA/patch digests while keeping the same
repository. The persisted policy snapshot may still pin the previous digest
while Host Class selection reads current deployment evidence. Exact-equality
planning failed every app update even though launch-time drift recovery
(launcher fallback + attestation same-repo gates) already accepts this case.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from api_service.services import omnigent_execution_plan_service as service
from moonmind.omnigent.host_image_drift import (
    reconcile_effective_launch_to_selected_host,
)


def _launch(
    host_ref: str,
    *,
    boundary_host_ref: str | None = None,
    extra: dict | None = None,
) -> dict:
    payload: dict = {
        "hostImageRef": host_ref,
        "boundaries": {"host": {"hostImageRef": boundary_host_ref or host_ref}},
        "policyAuthority": {"boundaries": {"host": {"hostImageRef": host_ref}}},
    }
    if extra:
        payload.update(extra)
    return payload


def test_reconcile_exact_match_returns_equal_copy():
    launch = _launch("ghcr.io/example/host@sha256:" + "a" * 64)
    reconciled = reconcile_effective_launch_to_selected_host(
        launch, "ghcr.io/example/host@sha256:" + "a" * 64
    )
    assert reconciled is not None
    assert reconciled["hostImageRef"] == launch["hostImageRef"]
    # Original is not mutated.
    assert launch["boundaries"]["host"]["hostImageRef"].endswith("a" * 64)


def test_reconcile_same_repo_drift_pins_selected_image():
    old = "ghcr.io/example/host@sha256:" + "a" * 64
    new = "ghcr.io/example/host@sha256:" + "b" * 64
    reconciled = reconcile_effective_launch_to_selected_host(
        _launch(old), new
    )
    assert reconciled is not None
    assert reconciled["hostImageRef"] == new
    assert reconciled["boundaries"]["host"]["hostImageRef"] == new
    # Original policy evidence is preserved, not rewritten.
    assert reconciled["policyAuthority"]["boundaries"]["host"]["hostImageRef"] == old
    # Recomputed snapshotRef validates.
    from moonmind.omnigent.execution_profiles import (
        validate_effective_launch_snapshot,
    )

    validate_effective_launch_snapshot(reconciled)


def test_reconcile_foreign_repository_fails_closed():
    old = "ghcr.io/example/host@sha256:" + "a" * 64
    foreign = "ghcr.io/example/other@sha256:" + "b" * 64
    assert (
        reconcile_effective_launch_to_selected_host(_launch(old), foreign) is None
    )


class _ArtifactService:
    def __init__(self) -> None:
        self.payloads: dict[str, bytes] = {}
        self._index = 0

    async def create(self, **_kwargs):
        self._index += 1
        artifact = SimpleNamespace(artifact_id=f"art_plan_{self._index}")
        return artifact, SimpleNamespace()

    async def write_complete(self, *, artifact_id: str, payload: bytes, **_kwargs):
        self.payloads[artifact_id] = payload
        return SimpleNamespace(artifact_id=artifact_id)


class _PlanStore:
    def __init__(self, _session_factory) -> None:
        pass

    async def persist(self, envelope):
        return envelope


def _drift_policy_snapshot(*, host_image_ref: str, policy: str = "opencode-on-demand@1"):
    from api_service.services.omnigent_policies import bootstrap_document
    from moonmind.omnigent.policies import compile_policy_snapshot

    document = bootstrap_document(
        host_mode="on_demand_docker",
        execution_profile_ref="omnigent-opencode@1",
        server_image_ref="ghcr.io/example/omnigent-server@sha256:" + "a" * 64,
        host_image_ref=host_image_ref,
    ).model_dump(mode="json", by_alias=True)
    document["execution"]["harness"] = "opencode-native"
    document["execution"]["agentIdentities"] = ["opencode"]
    document["providerProfile"]["compatibleProviders"] = ["opencode"]
    policy_id, _, version = policy.rpartition("@")
    return compile_policy_snapshot(
        policy_id=policy_id,
        version=int(version),
        document=document,
        validation={"valid": True},
    )


def _drift_snapshot(*, policy: str = "opencode-on-demand@1"):
    return {
        "schemaVersion": "moonmind.omnigent-agent-profile-snapshot.v1",
        "profileId": "profile-opencode-native",
        "version": 3,
        "digest": "sha256:" + "9" * 64,
        "providerProfileRef": "provider-opencode-native",
        "executionProfileRef": "omnigent-opencode@1",
        "allowedLaunchPolicyRefs": [policy],
        "launchPolicyRef": policy,
        "agentId": "opencode-native-agent",
        "policyRef": "omnigent-policy:sha256:" + "8" * 64,
        "document": {
            "schemaVersion": "moonmind.omnigent-agent-profile.v1",
            "endpointRef": "default",
            "bridgeMode": "embedded",
            "source": {"upstreamId": "opencode-native-agent", "upstreamVersion": "1.0.0"},
            "harness": "opencode-native",
            "requiredCapabilities": [],
            "execution": {
                "defaultExecutionProfileRef": "omnigent-opencode@1",
                "allowedLaunchPolicyRefs": [policy],
            },
            "providerRequirements": {
                "runtimeId": "opencode",
                "providerIds": ["openai"],
                "credentialSource": "secret_ref",
                "materializationMode": "generated_file",
            },
            "model": {"model": "example/model", "settings": {}},
            "workspace": {"mutation": "allowed"},
            "skills": ["github"],
            "tools": [],
            "capture": {"stream": True},
            "continuations": {"checkpoint": True},
            "publish": {"mode": "none"},
            "policyRef": "omnigent-policy:sha256:" + "8" * 64,
        },
    }


def _mock_current_host(monkeypatch, host_ref: str) -> None:
    from moonmind.omnigent.bootstrap import store as _store

    monkeypatch.setenv("OMNIGENT_OPENCODE_HOST_IMAGE_REF", host_ref)
    monkeypatch.setenv(
        "OMNIGENT_IMAGE_REF",
        "ghcr.io/omnigent-ai/omnigent-server@sha256:" + "6" * 64,
    )

    def _load_state():
        import os

        current = os.environ.get("OMNIGENT_OPENCODE_HOST_IMAGE_REF", "")
        return SimpleNamespace(
            server_image_ref=os.environ.get("OMNIGENT_IMAGE_REF"),
            opencode_host_image_ref=current,
            details={
                "opencodeHostCompatibility": {
                    "status": "ready",
                    "failureCode": None,
                    "serverImageRef": os.environ.get("OMNIGENT_IMAGE_REF"),
                    "hostImageRef": current,
                    "hostBuildDigest": "sha256:" + "b" * 64,
                    "hostVersion": "0.10.0",
                }
            },
        )

    monkeypatch.setattr(_store, "load_resolved_state", _load_state)


@pytest.mark.asyncio
async def test_planning_tolerates_same_repo_sha_drift(monkeypatch) -> None:
    """Same repository, different digest must plan with the current image."""
    import json

    old_ref = "ghcr.io/example/omnigent-host@sha256:" + "a" * 64
    new_ref = "ghcr.io/example/omnigent-host@sha256:" + "b" * 64

    async def resolve_policy(**_kwargs):
        return _drift_policy_snapshot(host_image_ref=old_ref)

    monkeypatch.setattr(service, "_resolve_runtime_policy_snapshot", resolve_policy)
    _mock_current_host(monkeypatch, new_ref)
    monkeypatch.setattr(
        service,
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
                "effectiveLaunchSnapshotDigest": plan_payload.effectiveLaunchSnapshotDigest,
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

    artifacts = _ArtifactService()
    result = await service.compile_and_persist_execution_plan(
        session_factory=object(),
        artifact_service=artifacts,
        principal="user-1",
        workflow_id="mm:test-host-drift",
        agent_profile_snapshot=_drift_snapshot(),
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
    )
    assert result.envelope.payload.hostImageRef == new_ref
    # Persisted effective-launch artifact must agree with the plan so the
    # launch-time exact check does not just move the failure downstream.
    launch_artifact_id = result.envelope.payload.effectiveLaunchSnapshotRef.removeprefix(
        "artifact:"
    )
    launch = json.loads(artifacts.payloads[launch_artifact_id])
    assert launch["hostImageRef"] == new_ref


@pytest.mark.asyncio
async def test_planning_still_rejects_foreign_repository(monkeypatch) -> None:
    """Different repository must still fail closed."""
    old_ref = "ghcr.io/example/omnigent-host@sha256:" + "a" * 64
    foreign_ref = "ghcr.io/example/other-host@sha256:" + "b" * 64

    async def resolve_policy(**_kwargs):
        return _drift_policy_snapshot(host_image_ref=old_ref)

    monkeypatch.setattr(service, "_resolve_runtime_policy_snapshot", resolve_policy)
    _mock_current_host(monkeypatch, foreign_ref)
    monkeypatch.setattr(
        service,
        "resolve_execution_evidence",
        lambda plan_payload, **_kwargs: ({}, "supported"),
    )

    with pytest.raises(ValueError, match="effective launch host image conflicts"):
        await service.compile_and_persist_execution_plan(
            session_factory=object(),
            artifact_service=_ArtifactService(),
            principal="user-1",
            workflow_id="mm:test-host-drift-foreign",
            agent_profile_snapshot=_drift_snapshot(),
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
        )
