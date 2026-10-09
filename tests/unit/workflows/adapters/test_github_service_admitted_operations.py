"""PAT writers consume frozen admission and current grants before any secret."""

from __future__ import annotations

import hashlib
import json

import httpx
import pytest

from moonmind.workflows.adapters.github_service import GitHubService
from tests.helpers.repository_connections import (
    github_pat_connection,
    github_repository_assignment,
    record_repository_connections,
)
from tests.unit.workflows.adapters.test_github_service import _call_writer

_OPERATIONS = ("read", "merge_request", "review_request")
_WRITERS = ("merge", "update_base", "review_request")


def _transport(monkeypatch):
    requests = []

    def respond(request):
        requests.append(request)
        if request.method == "PUT":
            return httpx.Response(200, json={"merged": True, "sha": "c" * 40})
        if request.method == "PATCH":
            return httpx.Response(200, json={})
        if request.url.path.endswith("/pulls/7"):
            return httpx.Response(
                200,
                json={
                    "number": 7,
                    "state": "open",
                    "head": {"sha": "a" * 40},
                    "base": {"repo": {"full_name": "acme/repo"}},
                },
            )
        if request.url.path.endswith("/comments"):
            if request.method == "POST":
                return httpx.Response(
                    201,
                    json={
                        "id": 21,
                        "created_at": "2026-10-07T00:00:01Z",
                        "user": {"login": "operator"},
                    },
                )
            return httpx.Response(200, json=[])
        if request.url.path == "/user":
            return httpx.Response(200, json={"login": "operator"})
        raise AssertionError(
            f"Unexpected provider request: {request.method} {request.url.path}"
        )

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original_client(
            transport=httpx.MockTransport(respond), **kwargs
        ),
    )
    return requests


def _plan(connection, assignment, operations, *, consumer="agent", anonymous=False):
    from moonmind.auth.bound_acquisition import AccessMode, select_repository_authority
    from moonmind.omnigent.harness_platform.execution_plan import (
        create_execution_plan_envelope,
        compute_model_config_digest,
    )

    snapshot = select_repository_authority(
        access_mode=AccessMode.ANONYMOUS if anonymous else AccessMode.EXPLICIT,
        principal_ref="system:deployment",
        principal_scope=("system", None),
        identity=assignment.identity,
        role="source_read" if anonymous else "collaboration",
        requested_operations=operations,
        policy_revision=1,
        explicit_connection=None if anonymous else connection,
        explicit_assignment=None if anonymous else assignment,
    )
    body = json.dumps(
        {
            "selection": snapshot.model_dump(by_alias=True, mode="json"),
            "repositoryIdentity": assignment.identity.model_dump(
                by_alias=True, mode="json"
            ),
            "assignmentRevision": assignment.revision,
        }
    ).encode()
    snapshot_ref = (
        "repository-access-snapshot:sha256:" + hashlib.sha256(body).hexdigest()
    )
    payload = {
        "endpointRef": "default",
        "agentProfileSnapshotRef": "artifact:art_profile",
        "harnessCatalogRef": "omnigent-harness-catalog:sha256:" + "2" * 64,
        "harnessId": "opencode-native",
        "harnessImplementationRef": "omnigent-harness-implementation:sha256:"
        + "3" * 64,
        "agentSource": {
            "kind": "upstream",
            "upstreamId": "opencode",
            "upstreamVersion": "1",
            "upstreamSnapshotDigest": "sha256:" + "4" * 64,
        },
        "credentialBindingSetRef": "omnigent-credential-bindings:primary@1#sha256:"
        + "5" * 64,
        "credentialBindings": {
            "primary-model": {
                "authorityKind": "provider_profile",
                "providerProfileRef": "test-profile",
                "materializerRef": "none@1",
            },
            "collaboration": {
                "authorityKind": "repository_connection",
                "connectionRef": connection.id,
                "repositoryAccessSnapshotRef": snapshot_ref,
                "materializerRef": "repository-broker@1",
                "repositoryRole": "collaboration",
                "consumer": consumer,
            },
        },
        "hostClassRef": "omnigent-opencode@1",
        "launchPolicyRef": "omnigent-on-demand@1",
        "executionRealizerRef": "generic-omnigent-host@1",
        "model": {
            "qualifiedId": "test/model",
            "effort": None,
            "routeRef": "test",
            "normalizedOptions": {},
            "modelConfigDigest": compute_model_config_digest(
                qualifiedId="test/model",
                effort=None,
                routeRef="test",
                normalizedOptions={},
            ),
        },
        "resolvedSkills": {
            "resolvedSkillSetRef": "artifact:art_skills",
            "resolvedSkillSetDigest": "sha256:" + hashlib.sha256(b"{}").hexdigest(),
        },
        "resolvedTools": {
            "repositoryAccess": {
                "collaboration": {
                    "artifactRef": "artifact:art_snapshot",
                    "snapshotRef": snapshot_ref,
                }
            }
        },
        "classAdmissionDecision": {"allowed": True},
        "runtimeValidationRequirements": [],
        "workspaceIntentRef": "workspace-intent:sha256:" + "8" * 64,
        "policySnapshotRef": "omnigent-policy:sha256:" + "9" * 64,
        "supportCombinationKey": "omnigent-support-combination:sha256:" + "0" * 64,
    }
    if anonymous:
        payload["credentialBindings"].pop("collaboration")
        access = payload["resolvedTools"]["repositoryAccess"]
        access["source"] = access.pop("collaboration")
    return create_execution_plan_envelope(payload), body


async def _record_authority(
    monkeypatch,
    tmp_path,
    *,
    active_operations=_OPERATIONS,
    admitted_operations=None,
    admitted_repository="acme/repo",
    consumer="agent",
    connection_id="repository-connection:pat-b",
    recorded_repository="acme/repo",
    tamper_snapshot=False,
    anonymous=False,
    legacy_parameters=None,
    connection_endpoint="https://github.com",
):
    from moonmind.auth import github_app_wiring, github_credentials
    from moonmind.omnigent import bridge_artifacts
    from moonmind.omnigent.harness_platform.stores import (
        SessionExecutionPlanStore,
    )
    from moonmind.workflows.temporal.activities import (
        omnigent_session_activities as activities,
    )

    connection = github_pat_connection(connection_id, "SYNTHETIC_PAT").model_copy(
        update={"allowed_operations": _OPERATIONS, "endpoint_ref": connection_endpoint}
    )
    active_assignment = github_repository_assignment(
        connection.id, "acme/repo", operations=active_operations
    )
    active_assignment = active_assignment.model_copy(
        update={
            "identity": active_assignment.identity.model_copy(
                update={"endpoint": connection_endpoint}
            )
        }
    )
    parameters = {
        "repository": {
            "provider": "git",
            "connectionRef": connection.id,
            "repository": {"name": recorded_repository},
        }
    }
    if legacy_parameters is not None:
        parameters = legacy_parameters
    plan = None
    if admitted_operations is not None:
        frozen_assignment = github_repository_assignment(
            connection.id, admitted_repository, operations=_OPERATIONS
        )
        frozen_assignment = frozen_assignment.model_copy(
            update={
                "identity": frozen_assignment.identity.model_copy(
                    update={"endpoint": connection_endpoint}
                )
            }
        )
        plan, body = _plan(
            connection,
            frozen_assignment,
            admitted_operations,
            consumer=consumer,
            anonymous=anonymous,
        )
        parameters["omnigentExecutionPlan"] = {
            "planRef": plan.planRef,
            "planDigest": "sha256:" + plan.planRef.rsplit(":", 1)[-1],
            "planArtifactRef": "artifact:art_plan",
            "taskInputSnapshotRef": "artifact:art_task",
            "taskInputSnapshotDigest": "sha256:" + "b" * 64,
        }

        async def read_plan(ref, **kwargs):
            assert kwargs["admitted_principal"] == "workflow:mm:run-b"
            if ref == "artifact:art_plan":
                return plan.model_dump(by_alias=True, mode="json")
            if ref == "art_profile":
                return {
                    "providerProfileRef": "test-profile",
                    "policyRef": plan.payload.policySnapshotRef,
                    "document": {
                        "harness": plan.payload.harnessId,
                        "source": plan.payload.agentSource,
                    },
                }
            if ref == "art_skills":
                return {}
            raise AssertionError(f"Unexpected artifact: {ref}")

        async def read_snapshot(self, ref):
            assert self._principal == "workflow:mm:run-b"
            assert ref == "artifact:art_snapshot"
            return body + b" " if tamper_snapshot else body

        async def link_inputs(**kwargs):
            assert kwargs["workflow_id"] == "mm:run-b"
            assert kwargs["run_id"] == "run-0"

        monkeypatch.setattr(activities, "_read_json_artifact", read_plan)
        monkeypatch.setattr(
            bridge_artifacts.TemporalOmnigentArtifactGateway,
            "read_bytes",
            read_snapshot,
        )
        monkeypatch.setattr(
            bridge_artifacts, "link_verified_execution_plan_inputs", link_inputs
        )

    engine = await record_repository_connections(
        monkeypatch,
        tmp_path,
        connection,
        assignments=[active_assignment],
        admitted_runs={"mm:run-b": parameters},
    )
    if plan is not None:
        from api_service.db import base as db_base
        from api_service.db.models import OmnigentExecutionPlanRecord

        async with engine.begin() as database:
            await database.run_sync(
                lambda sync: OmnigentExecutionPlanRecord.__table__.create(sync)
            )
        async with db_base.async_session_maker() as session:
            await SessionExecutionPlanStore(session).persist(plan)
            await session.commit()
    reads = []

    async def resolve(ref):
        reads.append(ref)
        return "synthetic-selected-pat"

    monkeypatch.setattr(github_credentials, "_resolve_secret_ref", resolve)
    monkeypatch.setattr(github_app_wiring, "default_resolve_secret_ref", resolve)
    monkeypatch.setenv("GITHUB_TOKEN", "synthetic-ambient-pat")
    return engine, reads


@pytest.mark.asyncio
@pytest.mark.parametrize("writer", _WRITERS)
@pytest.mark.parametrize("admitted_operations", [None, ("read",)])
async def test_admitted_pat_writer_denies_read_only_authority_before_secret(
    monkeypatch, tmp_path, writer, admitted_operations
):
    requests = _transport(monkeypatch)
    engine, reads = await _record_authority(
        monkeypatch,
        tmp_path,
        active_operations=("read",) if admitted_operations is None else _OPERATIONS,
        admitted_operations=admitted_operations,
    )
    try:
        succeeded, summary = await _call_writer(
            GitHubService(), writer, admitted_workflow_id="mm:run-b"
        )
    finally:
        await engine.dispose()
    assert reads == [], "Disallowed operation acquired PAT material"
    assert requests == [], "Disallowed operation reached GitHub"
    assert succeeded is False
    assert "no other GitHub credential is used" in summary


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["other-repository", "revoked-operation"])
async def test_frozen_pat_authority_cannot_expand_to_current_connection(
    monkeypatch, tmp_path, defect
):
    requests = _transport(monkeypatch)
    engine, reads = await _record_authority(
        monkeypatch,
        tmp_path,
        admitted_operations=_OPERATIONS,
        admitted_repository=(
            "acme/other" if defect == "other-repository" else "acme/repo"
        ),
        active_operations=("read",) if defect == "revoked-operation" else _OPERATIONS,
    )
    try:
        merged, _ = await _call_writer(
            GitHubService(), "merge", admitted_workflow_id="mm:run-b"
        )
    finally:
        await engine.dispose()
    assert reads == []
    assert requests == []
    assert merged is False


@pytest.mark.asyncio
@pytest.mark.parametrize("writer", _WRITERS)
async def test_frozen_pat_writer_uses_actual_admitted_operation(
    monkeypatch, tmp_path, writer
):
    requests = _transport(monkeypatch)
    engine, reads = await _record_authority(
        monkeypatch, tmp_path, admitted_operations=_OPERATIONS
    )
    try:
        succeeded, summary = await _call_writer(
            GitHubService(), writer, admitted_workflow_id="mm:run-b"
        )
    finally:
        await engine.dispose()
    assert succeeded, summary
    assert reads == ["env://SYNTHETIC_PAT"]
    assert {request.headers["Authorization"] for request in requests} == {
        "Bearer synthetic-selected-pat"
    }


@pytest.mark.asyncio
async def test_server_writer_cannot_consume_native_review_only_binding(
    monkeypatch, tmp_path
):
    requests = _transport(monkeypatch)
    engine, reads = await _record_authority(
        monkeypatch,
        tmp_path,
        admitted_operations=("read", "review_request"),
        consumer="native",
    )
    try:
        merged, _ = await _call_writer(
            GitHubService(), "merge", admitted_workflow_id="mm:run-b"
        )
    finally:
        await engine.dispose()
    assert not merged and reads == [] and requests == []


@pytest.mark.asyncio
async def test_admitted_pat_writer_cannot_be_overridden_by_service_connection(
    monkeypatch, tmp_path
):
    requests = _transport(monkeypatch)
    engine, reads = await _record_authority(
        monkeypatch, tmp_path, admitted_operations=("read",)
    )
    override = github_pat_connection(
        "repository-connection:override", "OVERRIDE_PAT"
    ).model_copy(update={"allowed_operations": _OPERATIONS})
    try:
        merged, _ = await _call_writer(
            GitHubService(connection=override), "merge", admitted_workflow_id="mm:run-b"
        )
    finally:
        await engine.dispose()
    assert reads == [] and requests == [] and not merged


@pytest.mark.asyncio
async def test_default_pat_writer_respects_its_recorded_read_only_assignment(
    monkeypatch, tmp_path
):
    requests = _transport(monkeypatch)
    engine, reads = await _record_authority(
        monkeypatch,
        tmp_path,
        active_operations=("read",),
        connection_id="repository-connection:git-default",
    )
    try:
        merged, _ = await _call_writer(
            GitHubService(), "merge", admitted_workflow_id="mm:run-b"
        )
    finally:
        await engine.dispose()
    assert reads == [] and requests == [] and not merged


@pytest.mark.asyncio
@pytest.mark.parametrize("operations", [("read",), _OPERATIONS])
async def test_frozen_child_uses_its_recorded_parent_operation_grant(
    monkeypatch, tmp_path, operations
):
    from temporalio import activity
    from tests.helpers.repository_connections import TemporalParentClient

    requests = _transport(monkeypatch)
    engine, reads = await _record_authority(
        monkeypatch, tmp_path, admitted_operations=operations
    )
    client = TemporalParentClient({"resolver:child": "mm:run-b"})
    monkeypatch.setattr(activity, "in_activity", lambda: True)
    monkeypatch.setattr(activity, "client", lambda: client)
    try:
        merged, _ = await _call_writer(
            GitHubService(), "merge", admitted_workflow_id="resolver:child"
        )
    finally:
        await engine.dispose()
    assert client.described == ["resolver:child"]
    assert merged == ("merge_request" in operations)
    if merged:
        assert reads == ["env://SYNTHETIC_PAT"] and len(requests) == 1
    else:
        assert reads == [] and requests == []


@pytest.mark.asyncio
async def test_frozen_snapshot_integrity_failure_denies_before_secret(
    monkeypatch, tmp_path
):
    requests = _transport(monkeypatch)
    engine, reads = await _record_authority(
        monkeypatch, tmp_path, admitted_operations=_OPERATIONS, tamper_snapshot=True
    )
    try:
        merged, summary = await _call_writer(
            GitHubService(), "merge", admitted_workflow_id="mm:run-b"
        )
    finally:
        await engine.dispose()
    assert "snapshot digest mismatch" in summary
    assert reads == [] and requests == [] and not merged


@pytest.mark.asyncio
async def test_legacy_recorded_repository_cannot_follow_another_current_grant(
    monkeypatch, tmp_path
):
    requests = _transport(monkeypatch)
    engine, reads = await _record_authority(
        monkeypatch, tmp_path, recorded_repository="acme/other"
    )
    try:
        merged, summary = await _call_writer(
            GitHubService(), "merge", admitted_workflow_id="mm:run-b"
        )
    finally:
        await engine.dispose()
    assert "target conflicts with the recorded run" in summary
    assert reads == [] and requests == [] and not merged


@pytest.mark.asyncio
async def test_admitted_unrecorded_default_does_not_grant_ambient_merge(
    monkeypatch, tmp_path
):
    requests = _transport(monkeypatch)
    monkeypatch.setenv("GITHUB_TOKEN", "synthetic-ambient-pat")
    engine = await record_repository_connections(
        monkeypatch, tmp_path, admitted_runs={"mm:run-b": {"repository": "acme/repo"}}
    )
    try:
        merged, summary = await _call_writer(
            GitHubService(), "merge", admitted_workflow_id="mm:run-b"
        )
    finally:
        await engine.dispose()
    assert "No recorded repository connection grants merge_request" in summary
    assert requests == [] and not merged


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "workspace",
    [
        {"repo": "acme/repo"},
        {"repository": {"name": "acme/repo"}},
        {
            "workspaceSource": {
                "kind": "repository",
                "repository": {"name": "acme/repo"},
            }
        },
    ],
)
async def test_legacy_repository_forms_keep_their_recorded_authority(
    monkeypatch, tmp_path, workspace
):
    requests = _transport(monkeypatch)
    workspace = {**workspace, "connectionRef": "repository-connection:pat-b"}
    engine, reads = await _record_authority(
        monkeypatch, tmp_path, legacy_parameters={"workspace": workspace}
    )
    try:
        merged, summary = await _call_writer(
            GitHubService(), "merge", admitted_workflow_id="mm:run-b"
        )
    finally:
        await engine.dispose()
    assert merged, summary
    assert reads == ["env://SYNTHETIC_PAT"] and len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("admitted_operations", [None, _OPERATIONS])
async def test_admitted_host_mismatch_denies_before_secret(
    monkeypatch, tmp_path, admitted_operations
):
    from moonmind.config.settings import settings

    monkeypatch.setattr(settings.github, "github_trusted_api_hosts", "ghe.example.test")
    requests = _transport(monkeypatch)
    engine, reads = await _record_authority(
        monkeypatch,
        tmp_path,
        admitted_operations=admitted_operations,
        connection_endpoint="https://ghe.example.test",
    )
    try:
        merged, summary = await _call_writer(
            GitHubService(), "merge", admitted_workflow_id="mm:run-b"
        )
    finally:
        await engine.dispose()
    assert "does not serve https://api.github.com" in summary
    assert reads == [] and requests == [] and not merged


@pytest.mark.asyncio
async def test_frozen_anonymous_read_remains_anonymous_and_cannot_merge(
    monkeypatch, tmp_path
):
    requests = _transport(monkeypatch)
    engine, reads = await _record_authority(
        monkeypatch, tmp_path, admitted_operations=("read",), anonymous=True
    )
    try:
        result = await GitHubService().read_pull_request(
            "acme/repo",
            "https://github.com/acme/repo/pull/7",
            admitted_workflow_id="mm:run-b",
        )
        assert result["number"] == 7, result
        assert len(requests) == 1 and "Authorization" not in requests[0].headers
        requests.clear()
        merged, _ = await _call_writer(
            GitHubService(), "merge", admitted_workflow_id="mm:run-b"
        )
    finally:
        await engine.dispose()
    assert reads == [] and requests == [] and not merged
