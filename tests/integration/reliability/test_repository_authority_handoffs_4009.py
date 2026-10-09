"""Issue #4009: ordinary authority travels through the production owners."""

from __future__ import annotations

import hashlib
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

from api_service.db import base
from api_service.db.models import (
    Base,
    ManagedAgentProviderProfile,
    RepositoryConnectionRecord,
)
from api_service.services import omnigent_execution_plan_service as writer
from moonmind.omnigent.harness_platform.stores import DbExecutionPlanStore
from moonmind.omnigent.host_services.github_credentials import (
    OmnigentGithubCredentialService,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal.activities import omnigent_activities as children
from moonmind.workflows.temporal.activities import omnigent_session_activities as reader
from tests.helpers.repository_connections import (
    github_pat_connection,
    github_repository_assignment,
    record_repository_connections,
)
from tests.unit.services.test_omnigent_execution_plan_service import (  # noqa: F401
    _ready_opencode_image_pair,
)
from tests.unit.services.test_omnigent_execution_plan_service import (
    _ArtifactService,
    _compile_opencode_plan,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]

_REPO = "MoonLadderStudios/MoonMind"
_WORKFLOW = "mm:test-deployment-evidence"
_OPERATIONS = ("read", "write", "branch_write", "review_request", "merge_request")


@pytest_asyncio.fixture
async def authority_context(tmp_path, monkeypatch):
    monkeypatch.setenv("SELECTED_REPOSITORY_PAT", "selected-secret-canary")
    monkeypatch.setenv("GITHUB_TOKEN", "ambient-secret-canary")
    monkeypatch.setenv("MOONMIND_OMNIGENT_EVIDENCE_POLICY", "either")
    engine = await record_repository_connections(
        monkeypatch,
        tmp_path,
        # The merge resolver child derives merge_request from its finish mode,
        # so its connection and live assignment must admit that action.
        github_pat_connection(
            "selected-repository", "SELECTED_REPOSITORY_PAT", operations=_OPERATIONS
        ),
        assignments=[
            github_repository_assignment(
                "selected-repository", _REPO, operations=_OPERATIONS
            )
        ],
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(base, "async_session_maker", sessions)
    monkeypatch.setattr(
        writer, "_try_load_real_harness_config", AsyncMock(return_value=None)
    )
    artifacts = _ArtifactService()

    async def read_json(ref, **_kwargs):
        return json.loads(artifacts.payloads[ref.removeprefix("artifact:")])

    async def read_bytes(ref):
        return artifacts.payloads[ref.removeprefix("artifact:")]

    async def read_repository_access_snapshot(ref, *, request):
        return await read_bytes(ref)

    async def read(*, artifact_id, **_kwargs):
        return SimpleNamespace(artifact_id=artifact_id), artifacts.payloads[artifact_id]

    artifacts.read = read

    monkeypatch.setattr(reader, "_read_json_artifact", read_json)
    monkeypatch.setattr(
        writer,
        "resolve_execution_evidence",
        lambda *_args, **_kwargs: (None, "uncertified"),
    )
    try:
        yield sessions, artifacts, SimpleNamespace(
            read_bytes=read_bytes,
            read_repository_access_snapshot=read_repository_access_snapshot,
        )
    finally:
        from moonmind.omnigent.production import close_omnigent_transport_pool

        await close_omnigent_transport_pool()
        await engine.dispose()


async def _compile(monkeypatch, context, *, workflow_id=_WORKFLOW, **parameters):
    sessions, artifacts, _gateway = context
    return await _compile_opencode_plan(
        monkeypatch,
        artifacts=artifacts,
        launch_policy_ref="omnigent-on-demand@1",
        plan_store=DbExecutionPlanStore(sessions),
        session_factory=sessions,
        extra_parameters=parameters,
        workflow_id=workflow_id,
    )


def _target():
    return {
        "provider": "git",
        "connectionRef": "selected-repository",
        "repository": {"name": _REPO},
        "branch": {"name": "main"},
    }


def _request():
    return AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef="provider-opencode-native",
        correlationId=_WORKFLOW,
        idempotencyKey="attempt-4009",
        workspaceSpec={"repositoryTarget": _target()},
    )


async def test_selected_repository_writer_store_reader_reaches_bound_consumer(
    authority_context, monkeypatch
):
    compiled = await _compile(
        monkeypatch,
        authority_context,
        repository=_target(),
        requiredCapabilities=["gh"],
    )
    plan = await reader._load_verified_execution_plan(compiled.binding)
    assert (
        plan.payload.credentialBindings["source"].connectionRef == "selected-repository"
    )
    assert (
        plan.payload.credentialBindings["collaboration"].repositoryRole
        == "collaboration"
    )
    assert "destination" not in plan.payload.credentialBindings  # save-only
    assert (
        plan.payload.credentialBindings["primary-model"].providerProfileRef
        == "provider-opencode-native"
    )
    sessions, artifacts, gateway = authority_context
    credentials = OmnigentGithubCredentialService(
        SimpleNamespace(), session_factory=sessions, artifact_gateway=gateway
    )
    acquired = await credentials.acquire_repository_use(
        plan=plan, request=_request(), role="source_read", operation="read"
    )
    assert acquired.binding.connection_id == "selected-repository"
    assert acquired.binding.endpoint == "https://github.com"
    assert acquired.binding.operations == ("read",)
    assert acquired.credential.use_now(bytes) == b"selected-secret-canary"
    assert b"selected-secret-canary" not in b"".join(artifacts.payloads.values())
    assert b"ambient-secret-canary" not in b"".join(artifacts.payloads.values())


@pytest.mark.parametrize("state", ["disabled", "stale"])
async def test_selected_snapshot_rejects_revoked_or_stale_connection_at_consumer(
    authority_context, monkeypatch, state
):
    compiled = await _compile(monkeypatch, authority_context, repository=_target())
    plan = await reader._load_verified_execution_plan(compiled.binding)
    assert "source" in plan.payload.credentialBindings
    sessions, _artifacts, gateway = authority_context
    async with sessions() as session:
        connection = await session.get(
            RepositoryConnectionRecord, "selected-repository"
        )
        if state == "disabled":
            connection.lifecycle = "disabled"
        else:
            connection.credential_revision += 1
        await session.commit()
    credentials = OmnigentGithubCredentialService(
        SimpleNamespace(), session_factory=sessions, artifact_gateway=gateway
    )
    with pytest.raises(ValueError, match="disabled|active|stale|revision"):
        await credentials.acquire_repository_use(
            plan=plan, request=_request(), role="source_read", operation="read"
        )


async def test_anonymous_source_and_scratch_never_acquire_repository_credentials(
    authority_context, monkeypatch
):
    anonymous = await _compile(
        monkeypatch,
        authority_context,
        workspace={
            "workspaceSource": {
                "kind": "repository",
                "repository": _REPO,
                "branch": "main",
                "accessMode": "anonymous",
            }
        },
    )
    plan = await reader._load_verified_execution_plan(anonymous.binding)
    assert set(plan.payload.credentialBindings) == {"primary-model"}
    assert plan.payload.resolvedTools["repositoryAccess"]["source"][
        "snapshotRef"
    ].startswith("repository-access-snapshot:sha256:")
    sessions, _artifacts, gateway = authority_context
    credentials = OmnigentGithubCredentialService(
        SimpleNamespace(), session_factory=sessions, artifact_gateway=gateway
    )
    assert (
        await credentials.acquire_repository_use(
            plan=plan, request=_request(), role="source_read", operation="read"
        )
        is None
    )
    scratch = await _compile(monkeypatch, authority_context)
    assert set(scratch.envelope.payload.credentialBindings) == {"primary-model"}
    assert not scratch.envelope.payload.resolvedTools.get("repositoryAccess")


async def test_repository_plan_routes_to_authorized_realizer_before_capacity(
    authority_context, monkeypatch
):
    compiled = await _compile(monkeypatch, authority_context, repository=_target())
    plan = await reader._load_verified_execution_plan(compiled.binding)
    assert "source" in plan.payload.credentialBindings
    selected_realizer = SimpleNamespace(authority_kinds=("model", "repository"))
    monkeypatch.setattr(
        "moonmind.omnigent.realizers.registry.get_default_registry",
        lambda: SimpleNamespace(require=lambda _ref: selected_realizer),
    )
    capacity = AsyncMock(return_value={})
    monkeypatch.setattr(reader, "_plan_capacity_authority", capacity)
    payload = {
        "agentRunId": "run-4009",
        "workflowId": _WORKFLOW,
        "stepExecutionId": "implement",
        "executionProfileRef": "provider-opencode-native",
        "omnigentExecutionPlan": compiled.binding.model_dump(by_alias=True),
    }
    result = await reader.omnigent_evaluate_session_admission_activity(payload)
    assert result["reasonCode"] == "realizer_managed_lifecycle"
    capacity.assert_awaited_once()
    selected_realizer.authority_kinds = ("model",)
    capacity.reset_mock()
    from moonmind.omnigent.harness_platform.failures import HarnessPlatformError

    with pytest.raises(HarnessPlatformError, match="authority|repository"):
        await reader.omnigent_evaluate_session_admission_activity(payload)
    capacity.assert_not_awaited()


@pytest.mark.parametrize("parent_publish_mode", ["auto", "pr"])
async def test_merge_resolver_child_activity_readmits_mixed_parent_without_caller_grants(
    authority_context, monkeypatch, parent_publish_mode
):
    # Auto publication is provider-neutral, so GitHub actions are declared.
    declared = (
        {"githubOperations": ["write", "branch_write", "review_request"]}
        if parent_publish_mode == "auto"
        else {}
    )
    parent = await _compile(
        monkeypatch,
        authority_context,
        repository=_target(),
        requiredCapabilities=["gh"],
        publishMode=parent_publish_mode,
        mergeAutomation={"enabled": True},
        **declared,
    )
    assert "source" in parent.envelope.payload.credentialBindings
    sessions, artifacts, _gateway = authority_context
    async with sessions() as session:
        session.add(
            ManagedAgentProviderProfile(
                profile_id="provider-opencode-native",
                runtime_id="opencode",
                provider_id="opencode-go",
            )
        )
        await session.commit()
    monkeypatch.setattr(
        children, "_OnDemandTemporalArtifactService", lambda _factory: artifacts
    )
    from moonmind.workflows.temporal.workflows import merge_automation
    from moonmind.workflows.temporal.workflows.merge_gate import (
        build_resolver_run_request,
    )
    from tests.unit.workflows.temporal.workflows.test_merge_automation_temporal import (
        _payload,
    )

    payload = _payload()
    payload["parentWorkflowId"] = _WORKFLOW
    payload["principal"] = "user-1"
    payload["resolverTemplate"] = {
        "targetRuntime": "omnigent",
        "executionProfileRef": "provider-opencode-native",
        "parentOmnigentExecutionPlan": parent.binding.model_dump(by_alias=True),
    }
    workflow = merge_automation.MoonMindMergeAutomationWorkflow()
    workflow._input = merge_automation.MergeAutomationStartInput.model_validate(payload)
    request = build_resolver_run_request(
        parent_workflow_id=_WORKFLOW,
        pull_request=payload["pullRequest"],
        jira_issue_key=None,
        merge_method="squash",
        resolver_template=payload["resolverTemplate"],
    )

    async def execute_activity(name, inputs, **_kwargs):
        assert name == "omnigent.prepare_child_execution_plan"
        assert "childRepositorySnapshotRefs" not in inputs
        return await children.omnigent_prepare_child_execution_plan_activity(inputs)

    monkeypatch.setattr(merge_automation.workflow, "execute_activity", execute_activity)
    prepared = await workflow._prepare_omnigent_resolver_request(
        request, resolver_workflow_id="mm:resolver-4009"
    )
    from moonmind.schemas.agent_runtime_models import OmnigentExecutionPlanBinding

    child = await reader._load_verified_execution_plan(
        OmnigentExecutionPlanBinding.model_validate(
            prepared["initial_parameters"]["omnigentExecutionPlan"]
        )
    )
    assert (
        child.payload.credentialBindings["source"].connectionRef
        == "selected-repository"
    )
    assert (
        child.payload.repositoryAuthorityRefs["source"]
        != parent.envelope.payload.repositoryAuthorityRefs["source"]
    )
    assert set(child.payload.credentialBindings) == {
        "primary-model",
        "source",
        "collaboration",
    }
    assert set(child.payload.credentialBindings) <= set(
        parent.envelope.payload.credentialBindings
    )
    assert "providerLeases" not in prepared["initial_parameters"]
    assert "repositoryIssuance" not in prepared["initial_parameters"]


@pytest.mark.parametrize("anonymous", [False, True])
async def test_loaded_plan_prepares_clone_and_cli_without_ambient_credentials(
    authority_context, monkeypatch, tmp_path, anonymous
):
    from pathlib import Path

    # The simulated clone runs as pytest, so use its owner for preparation and retry.
    runtime_uid, runtime_gid = os.getuid(), os.getgid()
    parameters = (
        {
            "workspace": {
                "workspaceSource": {
                    "kind": "repository",
                    "repository": _REPO,
                    "branch": "main",
                    "accessMode": "anonymous",
                }
            }
        }
        if anonymous
        else {"repository": _target()}
    )
    compiled = await _compile(
        monkeypatch, authority_context, requiredCapabilities=["gh"], **parameters
    )
    plan = await reader._load_verified_execution_plan(compiled.binding)
    sessions, _artifacts, gateway = authority_context
    calls = []
    owner = "runtime-binding-4009:generation-2"

    class Backend:
        async def run(self, argv, **kwargs):
            calls.append((list(argv), kwargs.get("input_bytes")))
            if argv[1:3] == ["volume", "inspect"]:
                return 0, hashlib.sha256(owner.encode()).hexdigest()[:32], ""
            if argv[1:2] == ["run"] and "-ceu" in argv:
                # The projection writer acknowledges its reservation stamp.
                return 0, argv[-1], ""
            return 0, "", ""

    async def clone_runner(argv, input_bytes=None):
        calls.append((list(argv), input_bytes))
        if "-ceu" in argv:
            checkout = tmp_path / Path(argv[-3].removeprefix("/work/"))
            (checkout / ".git").mkdir(parents=True)
            (checkout / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
        return 0, "", ""

    from moonmind.omnigent import production

    monkeypatch.setattr(production, "DockerCommandBackend", Backend)
    monkeypatch.setattr(production, "generic_host_enabled", lambda: True)
    monkeypatch.setattr(
        production, "resolved_server_url", lambda: "http://omnigent:8080"
    )
    monkeypatch.setenv("MOONMIND_OMNIGENT_HOST_SERVER_URL", "http://omnigent:8080")
    monkeypatch.setenv("MOONMIND_OMNIGENT_EXPECTED_HOST_OWNER", "deployment")
    monkeypatch.setenv("WORKFLOW_WORKSPACE_ROOT", str(tmp_path))
    services = production.build_generic_omnigent_execution_services(
        session_factory=sessions, artifact_gateway=gateway
    )
    runtime = services.generic_realizer._host_runtime
    workspace = runtime._workspace
    workspace._runner = clone_runner
    credentials = runtime._github_credentials
    assert workspace._repository_credentials is credentials
    skill_attachment = {
        "kind": "bind",
        "sourceRef": "skills",
        "cleanupRef": "skills-cleanup",
    }
    runtime._skills = SimpleNamespace(
        anticipated_attachment=AsyncMock(return_value=skill_attachment),
        materialize=AsyncMock(return_value=skill_attachment),
        cleanup=AsyncMock(),
    )
    runtime._tools = SimpleNamespace(materialize=AsyncMock(return_value=()))
    runtime._egress = SimpleNamespace(attest=AsyncMock(return_value={}))
    workspace_id = hashlib.sha256(f"{_WORKFLOW}:attempt-4009".encode()).hexdigest()[:24]
    spec = parameters.get("workspace", {"repositoryTarget": _target()})
    request = _request().model_copy(
        update={
            "workspace_spec": {
                **spec,
                "workspaceLocator": {
                    "kind": "sandbox",
                    "workspaceId": workspace_id,
                    "relativePath": "repo",
                },
            }
        }
    )
    from moonmind.omnigent.runtime_bindings import DbRuntimeBindingStore

    runtime_store = DbRuntimeBindingStore(sessions)
    binding = await runtime_store.create_initial(
        execution_plan_ref=plan.planRef,
        idempotency_key=request.idempotency_key,
        provider_leases={},
    )
    # Mirror the generic realizer: projection is reserved durably before prepare.
    from moonmind.omnigent.runtime_bindings import (
        RuntimeBindingState,
        reserve_github_projection,
        validate_github_projection,
    )

    reservation = None
    if not anonymous:
        for state in (
            RuntimeBindingState.credentials_acquired,
            RuntimeBindingState.credentials_materialized,
        ):
            binding = await runtime_store.update(
                binding.bindingId,
                expected_revision=binding.revision,
                expected_fencing_generation=binding.fencingGeneration,
                state=state,
            )
        binding, reservation = await reserve_github_projection(runtime_store, binding)
    owner = f"{binding.bindingId}:{binding.fencingGeneration}"
    authority = list(binding.cleanupAuthorityRefs)

    async def verify_projection():
        await validate_github_projection(
            runtime_store, binding.bindingId, reservation
        )

    async def record(value):
        nonlocal binding
        authority.append(value)
        binding = await runtime_store.update(
            binding.bindingId,
            expected_revision=binding.revision,
            expected_fencing_generation=binding.fencingGeneration,
            updates={"cleanupAuthorityRefs": list(authority)},
        )

    prepared = await runtime.prepare(
        request=request,
        plan=plan,
        host_class=SimpleNamespace(
            runtime={"uid": runtime_uid, "gid": runtime_gid},
            imageRef="host-image",
            omnigentVersion="",
        ),
        launch_policy=SimpleNamespace(),
        authority_sink=record,
        repository_owner_ref=owner,
        github_projection_reservation=reservation,
        github_projection_verifier=verify_projection,
    )
    clone_argv, clone_input = next(
        (argv, data) for argv, data in calls if "-ceu" in argv
    )
    assert clone_input == (b"" if anonymous else b"selected-secret-canary")
    assert ("token_file" in " ".join(clone_argv)) is not anonymous
    assert b"ambient-secret-canary" not in [data for _argv, data in calls]
    assert "ambient-secret-canary" not in json.dumps([argv for argv, _data in calls])
    assert "selected-secret-canary" not in json.dumps([argv for argv, _data in calls])
    # Reopen the actual runtime owner: issuance and cleanup survive worker loss.
    reloaded = await DbRuntimeBindingStore(sessions).get(binding.bindingId)
    assert list(reloaded.cleanupAuthorityRefs) == authority
    assert reloaded.providerLeases == {}
    uses = [value for value in authority if value.get("kind") == "repository_use"]
    assert [value["slot"] for value in uses] == (
        [] if anonymous else ["source", "collaboration"]
    )
    assert all(value["record"]["useOwner"] == owner for value in uses)
    assert "selected-secret-canary" not in json.dumps(authority)
    if anonymous:
        assert prepared.github_credential_attachment is None
    else:
        assert (
            prepared.github_credential_attachment["ownerDigest"]
            == hashlib.sha256(owner.encode()).hexdigest()[:32]
        )
        assert [data for _argv, data in calls if data] == [
            b"selected-secret-canary",
            b"selected-secret-canary",
        ]

    # A ready attempt is retained even if source authority is subsequently revoked.
    saved = Path(prepared.workspace_attachment["sourceRef"]) / "saved-work.txt"
    saved.write_text("accepted work survives source revocation")
    async with sessions() as session:
        connection = await session.get(
            RepositoryConnectionRecord, "selected-repository"
        )
        connection.lifecycle = "disabled"
        await session.commit()
    calls.clear()
    await workspace.materialize(
        request,
        plan=plan,
        repository_owner_ref=owner,
        authority_sink=record,
        runtime_uid=runtime_uid,
        runtime_gid=runtime_gid,
    )
    assert not calls
    assert saved.read_text() == "accepted work survives source revocation"
    if not anonymous:
        from moonmind.omnigent.harness_platform.failures import HarnessPlatformError

        # A stale cleanup record cannot remove the current generation's volume.
        stale = {
            **prepared.github_credential_attachment,
            "ownerDigest": "stale-generation",
        }
        with pytest.raises(HarnessPlatformError, match="stale owner"):
            await credentials.cleanup(stale)
        assert not any(argv[1:3] == ["volume", "rm"] for argv, _data in calls)
        await runtime.cleanup_authorities(reloaded.cleanupAuthorityRefs)
        assert any(argv[1:3] == ["volume", "rm"] for argv, _data in calls)
        assert saved.exists()
    await production.close_omnigent_transport_pool()


async def test_mixed_writer_plan_consumes_only_existing_model_capacity(
    authority_context, monkeypatch
):
    from moonmind.omnigent.provider_leases import OmnigentProviderLeaseCoordinator
    from tests.unit.omnigent.test_provider_leases_workflow_owned_capacity import (
        _admitted,
        _inspection,
        _LeaseClient,
        _profiles,
        _session_factory,
    )

    compiled = await _compile(
        monkeypatch,
        authority_context,
        repository=_target(),
        requiredCapabilities=["gh"],
    )
    plan = await reader._load_verified_execution_plan(compiled.binding)
    client = _LeaseClient(
        inspection=_inspection(
            profile_ref="provider-opencode-native", plan_ref=plan.planRef
        )
    )
    coordinator = OmnigentProviderLeaseCoordinator(
        session_factory=_session_factory(_profiles("provider-opencode-native")),
        lease_client=client,
    )
    acquired = await coordinator.acquire_all(
        plan=plan,
        workflow_id=_WORKFLOW,
        step_execution_id="step-1",
        idempotency_key="idem-1",
        admitted_capacity=_admitted("provider-opencode-native", plan_ref=plan.planRef),
    )
    assert [lease.slot for lease in acquired] == ["primary-model"]
    assert [lease.profile_id for lease in client.inspected] == [
        "provider-opencode-native"
    ]
    assert client.acquired == []
    await coordinator.release_all(acquired)
    assert client.released == []


async def test_unadmitted_clone_fails_before_ambient_resolution_or_transport(
    monkeypatch, tmp_path
):
    from pathlib import Path

    from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
    from moonmind.omnigent.host_services.workspace import OmnigentWorkspaceMaterializer

    ambient = AsyncMock(return_value="ambient-secret-canary")
    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_api_key_resolve.resolve_github_token_for_launch",
        ambient,
    )
    calls = []

    async def runner(argv, input_bytes=None):
        calls.append((argv, input_bytes))
        if "-ceu" in argv:
            checkout = tmp_path / Path(argv[-3].removeprefix("/work/"))
            checkout.mkdir(parents=True)
        return 0, "", ""

    workspace_id = hashlib.sha256(f"{_WORKFLOW}:attempt-4009".encode()).hexdigest()[:24]
    request = _request().model_copy(
        update={
            "workspace_spec": {
                "repositoryTarget": _target(),
                "workspaceLocator": {
                    "kind": "sandbox",
                    "workspaceId": workspace_id,
                    "relativePath": "repo",
                },
            }
        }
    )
    workspace = OmnigentWorkspaceMaterializer(
        command_runner=runner, workspace_root=tmp_path
    )
    with pytest.raises(HarnessPlatformError, match="admitted"):
        await workspace.materialize(request)
    ambient.assert_not_awaited()
    assert not calls


async def test_omitted_connection_consumes_the_recorded_route_revision(
    authority_context, monkeypatch
):
    sessions, _artifacts, gateway = authority_context
    async with sessions() as session:
        connection = await session.get(
            RepositoryConnectionRecord, "selected-repository"
        )
        connection.policy_revision = 7
        await session.commit()
    compiled = await _compile(monkeypatch, authority_context, repository=_REPO)
    plan = await reader._load_verified_execution_plan(compiled.binding)
    credentials = OmnigentGithubCredentialService(
        SimpleNamespace(), session_factory=sessions, artifact_gateway=gateway
    )
    acquired = await credentials.acquire_repository_use(
        plan=plan, request=_request(), role="source_read", operation="read"
    )
    assert acquired.binding.connection_id == "selected-repository"
    assert acquired.binding.connection_revision == 7
    acquired.credential.clear()


async def test_authenticated_workspace_source_preserves_its_selected_connection(
    authority_context, monkeypatch
):
    from api_service.services.repository_connections import RepositoryConnectionService

    sessions, _artifacts, gateway = authority_context
    async with sessions() as session:
        service = RepositoryConnectionService(session)
        await service.create_connection(
            github_pat_connection("other-repository-connection", "GITHUB_TOKEN"),
            actor_ref="system:deployment",
            request_id="other-connection-4009",
            principal_ref="system:deployment",
            principal_scope=("system", None),
        )
        await service.set_assignment(
            github_repository_assignment("other-repository-connection", _REPO),
            actor_ref="system:deployment",
            request_id="other-assignment-4009",
            principal_ref="system:deployment",
            principal_scope=("system", None),
        )
    spec = {"workspaceSource": {"kind": "repository", "repositoryTarget": _target()}}
    compiled = await _compile(
        monkeypatch, authority_context, workspace=spec, requiredCapabilities=["gh"]
    )
    plan = await reader._load_verified_execution_plan(compiled.binding)
    assert (
        plan.payload.credentialBindings["source"].connectionRef == "selected-repository"
    )
    credentials = OmnigentGithubCredentialService(
        SimpleNamespace(), session_factory=sessions, artifact_gateway=gateway
    )
    acquired = await credentials.acquire_repository_use(
        plan=plan,
        request=_request().model_copy(update={"workspace_spec": spec}),
        role="collaboration",
        operation="read",
    )
    assert acquired.credential.use_now(bytes) == b"selected-secret-canary"
    acquired.credential.clear()


@pytest.mark.parametrize(
    "alteration", ["target", "snapshot", "operation", "undeclared_role"]
)
async def test_loaded_authority_rejects_altered_or_unbound_consumption(
    authority_context, monkeypatch, alteration
):
    compiled = await _compile(monkeypatch, authority_context, repository=_target())
    plan = await reader._load_verified_execution_plan(compiled.binding)
    sessions, artifacts, gateway = authority_context
    credentials = OmnigentGithubCredentialService(
        SimpleNamespace(), session_factory=sessions, artifact_gateway=gateway
    )
    request = _request()
    role, operation = "source_read", "read"
    if alteration == "target":
        target = _target()
        target["repository"] = {"name": "another/repository"}
        request = request.model_copy(
            update={"workspace_spec": {"repositoryTarget": target}}
        )
    elif alteration == "snapshot":
        ref = plan.payload.resolvedTools["repositoryAccess"]["source"][
            "artifactRef"
        ].removeprefix("artifact:")
        artifacts.payloads[ref] += b" "
    elif alteration == "operation":
        operation = "write"
    else:
        role = "destination_write"
    with pytest.raises(ValueError, match="admitted|snapshot|target"):
        await credentials.acquire_repository_use(
            plan=plan, request=request, role=role, operation=operation
        )


async def test_schedule_plan_can_issue_for_its_admitted_execution_owner(
    authority_context, monkeypatch
):
    compiled = await _compile(
        monkeypatch,
        authority_context,
        workflow_id="mm-schedule:4009",
        repository=_target(),
    )
    plan = await reader._load_verified_execution_plan(compiled.binding)
    request = _request().model_copy(
        update={
            "correlation_id": "mm:scheduled-fire-4009",
            "idempotency_key": "scheduled-fire-4009",
            "parameters": {"executionPlanRef": plan.planRef},
        }
    )
    sessions, _artifacts, gateway = authority_context
    credentials = OmnigentGithubCredentialService(
        SimpleNamespace(), session_factory=sessions, artifact_gateway=gateway
    )
    acquired = await credentials.acquire_repository_use(
        plan=plan, request=request, role="source_read", operation="read"
    )
    assert acquired.binding.execution_owner == "scheduled-fire-4009"
    acquired.credential.clear()
    request = request.model_copy(
        update={
            "parameters": {
                "executionPlanRef": "omnigent-execution-plan:sha256:" + "0" * 64
            }
        }
    )
    with pytest.raises(ValueError, match="execution plan"):
        await credentials.acquire_repository_use(
            plan=plan, request=request, role="source_read", operation="read"
        )
