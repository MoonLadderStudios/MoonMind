"""Selected repository authority crosses durable artifacts and publication."""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlsplit

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

from api_service.db import base, models
from api_service.services import omnigent_execution_plan_service as writer
from moonmind.config.settings import settings
from moonmind.omnigent import production
from moonmind.omnigent.bridge_artifacts import (
    OmnigentArtifactError,
    TemporalOmnigentArtifactGateway,
)
from moonmind.omnigent.harness_platform.execution_plan import (
    verify_execution_plan_envelope,
)
from moonmind.omnigent.harness_platform.failures import HarnessPlatformError
from moonmind.omnigent.harness_platform.stores import DbExecutionPlanStore
from moonmind.omnigent.host_services.github_credentials import (
    OmnigentGithubCredentialService,
)
from moonmind.omnigent.workspace_publication import OmnigentWorkspacePublicationService
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.workflows.temporal.activities import omnigent_activities as children
from moonmind.workflows.temporal.activities import omnigent_session_activities as reader
from moonmind.workflows.temporal.activities.omnigent_activities import (
    _OnDemandTemporalArtifactService,
)
from moonmind.workflows.temporal.artifacts import (
    LocalTemporalArtifactStore,
    TemporalArtifactAuthorizationError,
    TemporalArtifactRepository,
    TemporalArtifactService,
    TemporalArtifactStateError,
)
from moonmind.workflows.temporal.runtime.workspace_locators import (
    SandboxWorkspaceRecord,
    SandboxWorkspaceRecordStore,
)
from tests.helpers.repository_connections import (
    github_pat_connection,
    github_repository_assignment,
    record_repository_connections,
)
from tests.unit.omnigent.test_workspace_publication_base import git
from tests.unit.services.test_omnigent_execution_plan_service import (  # noqa: F401
    _compile_opencode_plan,
    _ready_opencode_image_pair,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]

_REPOSITORY = "MoonLadderStudios/MoonMind"
_WORKFLOW = "mm:repository-access-consumers"
_STEP = f"{_WORKFLOW}:run-1:implement:execution:1"


def _target():
    return {
        "provider": "git",
        "connectionRef": "selected-repository",
        "repository": {"name": _REPOSITORY},
        "branch": {"name": "main"},
    }


def _request(compiled, *, publish_mode="none", workspace_id=None):
    workspace = {"repositoryTarget": _target()}
    if workspace_id:
        workspace["workspaceLocator"] = {
            "kind": "sandbox",
            "workspaceId": workspace_id,
            "relativePath": "repo",
        }
    return AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        executionProfileRef="provider-opencode-native",
        omnigentExecutionPlan=compiled.binding,
        correlationId=_WORKFLOW,
        idempotencyKey="repository-access-consumer-attempt",
        parameters={
            "publishMode": publish_mode,
            "executionPlanRef": compiled.binding.plan_ref,
        },
        workspaceSpec=workspace,
        stepExecution={
            "workflowId": _WORKFLOW,
            "runId": "run-1",
            "logicalStepId": "implement",
            "executionOrdinal": 1,
            "stepExecutionId": _STEP,
            "runtimeContextPolicy": "fresh_agent_run",
            "omnigentExecutionPlan": compiled.binding.model_dump(by_alias=True),
        },
    )


@pytest_asyncio.fixture
async def repository_consumers(tmp_path, monkeypatch, request):
    monkeypatch.setenv("SELECTED_REPOSITORY_PAT", "selected-credential-canary")
    monkeypatch.setenv("GITHUB_TOKEN", "ambient-credential-canary")
    monkeypatch.setenv("GH_TOKEN", "other-ambient-credential-canary")
    monkeypatch.setenv("MOONMIND_OMNIGENT_EVIDENCE_POLICY", "either")
    monkeypatch.setattr(settings.security, "high_security_mode", False)
    monkeypatch.setattr(
        writer, "_try_load_real_harness_config", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        writer, "resolve_execution_evidence", lambda *_a, **_kw: (None, "uncertified")
    )
    monkeypatch.setattr(
        "moonmind.workflows.temporal.artifacts.is_disabled_local_mode", lambda: False
    )
    monkeypatch.setattr(
        TemporalArtifactService,
        "_build_store_from_settings",
        staticmethod(lambda: LocalTemporalArtifactStore(tmp_path / "blobs")),
    )
    endpoint = getattr(request, "param", "https://github.com")
    if endpoint != "https://github.com":
        monkeypatch.setattr(
            settings.github, "github_trusted_api_hosts", urlsplit(endpoint).hostname
        )
    connection = github_pat_connection(
        "selected-repository", "SELECTED_REPOSITORY_PAT"
    ).model_copy(update={"endpoint_ref": endpoint})
    assignment = github_repository_assignment("selected-repository", _REPOSITORY)
    assignment = assignment.model_copy(
        update={
            "identity": assignment.identity.model_copy(update={"endpoint": endpoint})
        }
    )
    engine = await record_repository_connections(
        monkeypatch,
        tmp_path,
        connection,
        assignments=[assignment],
    )
    async with engine.begin() as connection:
        await connection.run_sync(models.Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(base, "async_session_maker", sessions)
    artifacts = _OnDemandTemporalArtifactService(sessions)
    plans = DbExecutionPlanStore(sessions)
    gateway = TemporalOmnigentArtifactGateway(sessions)

    async def compile_plan(publish_mode="none", *, profile_tools=(), **parameters):
        input_ref, input_digest = await writer.persist_json_artifact(
            artifact_service=artifacts,
            principal="user-1",
            artifact_class="workflow.task_input_snapshot",
            payload={
                "draft": {
                    "repository": _REPOSITORY,
                    "workflow": {"instructions": "Use selected repository authority."},
                }
            },
        )
        return await _compile_opencode_plan(
            monkeypatch,
            artifacts=artifacts,
            plan_store=plans,
            session_factory=sessions,
            launch_policy_ref="omnigent-on-demand@1",
            workflow_id=_WORKFLOW,
            profile_tools=profile_tools,
            extra_parameters={"repository": _target(), "publishMode": publish_mode, **parameters},
            task_input_snapshot_ref=input_ref,
            task_input_snapshot_digest=input_digest,
        )

    async def link_inputs(compiled):
        await reader.omnigent_evaluate_session_admission_activity(
            {
                "agentRunId": "agent-run-4676",
                "workflowId": _WORKFLOW,
                "stepExecutionId": _STEP,
                "executionProfileRef": "provider-opencode-native",
                "omnigentExecutionPlan": compiled.binding.model_dump(by_alias=True),
            }
        )
        return verify_execution_plan_envelope(
            await plans.load(compiled.binding.plan_ref)
        )

    monkeypatch.setattr(
        "moonmind.omnigent.realizers.registry.get_default_registry",
        lambda: SimpleNamespace(
            require=lambda _ref: SimpleNamespace(
                authority_kinds=("model", "repository")
            )
        ),
    )
    monkeypatch.setattr(reader, "_plan_capacity_authority", AsyncMock(return_value={}))

    try:
        yield SimpleNamespace(
            sessions=sessions,
            artifacts=artifacts,
            plans=plans,
            gateway=gateway,
            compile=compile_plan,
            link_inputs=link_inputs,
            endpoint=endpoint,
        )
    finally:
        await production.close_omnigent_transport_pool()
        await engine.dispose()


async def test_generic_host_reads_selected_snapshot_with_execution_scope(
    repository_consumers,
):
    context = repository_consumers
    compiled = await context.compile()
    plan = await context.link_inputs(compiled)
    assert {
        access["artifactRef"]
        for access in plan.payload.resolvedTools["repositoryAccess"].values()
    }.issubset(set(compiled.artifact_refs))
    credentials = OmnigentGithubCredentialService(
        SimpleNamespace(),
        session_factory=context.sessions,
        artifact_gateway=context.gateway,
    )
    acquired = await credentials.acquire_repository_use(
        plan=plan, request=_request(compiled), role="source_read", operation="read"
    )
    try:
        assert acquired.binding.connection_id == "selected-repository"
        assert acquired.credential.use_now(bytes) == b"selected-credential-canary"
    finally:
        acquired.credential.clear()


async def test_frozen_plan_delegates_only_its_snapshots_to_each_concrete_occurrence(
    repository_consumers,
):
    context = repository_consumers
    compiled = await context.compile()
    credentials = OmnigentGithubCredentialService(
        SimpleNamespace(),
        session_factory=context.sessions,
        artifact_gateway=context.gateway,
    )
    for workflow_id, run_id in (
        (_WORKFLOW, "run-1"),
        ("mm:scheduled-occurrence", "new-run"),
    ):
        step_id = f"{workflow_id}:{run_id}:implement:execution:1"
        await reader.omnigent_evaluate_session_admission_activity(
            {
                "agentRunId": workflow_id + ":agent",
                "workflowId": workflow_id,
                "stepExecutionId": step_id,
                "executionProfileRef": "provider-opencode-native",
                "omnigentExecutionPlan": compiled.binding.model_dump(by_alias=True),
            }
        )
        request = _request(compiled)
        request = request.model_copy(
            update={
                "correlation_id": workflow_id,
                "step_execution": request.step_execution.model_copy(
                    update={
                        "workflow_id": workflow_id,
                        "run_id": run_id,
                        "step_execution_id": step_id,
                    }
                ),
            }
        )
        acquired = await credentials.acquire_repository_use(
            plan=compiled.envelope,
            request=request,
            role="source_read",
            operation="read",
        )
        assert acquired.binding.connection_id == "selected-repository"
        acquired.credential.clear()
    source_ref = compiled.envelope.payload.resolvedTools["repositoryAccess"]["source"][
        "artifactRef"
    ]
    async with context.sessions() as session:
        links = await TemporalArtifactRepository(session).list_links(
            context.gateway._artifact_id(source_ref)
        )
        assert {(link.workflow_id, link.run_id) for link in links} == {
            (_WORKFLOW, "run-1"),
            ("mm:scheduled-occurrence", "new-run"),
        }


@pytest.mark.parametrize(
    "denial", ["unlinked_execution", "unadmitted_ref", "restricted", "quarantined"]
)
async def test_snapshot_delegation_keeps_artifact_access_controls(
    repository_consumers, denial
):
    context = repository_consumers
    compiled = await context.compile()
    plan = await context.link_inputs(compiled)
    ref = plan.payload.resolvedTools["repositoryAccess"]["source"]["artifactRef"]
    request = _request(compiled)
    if denial == "unlinked_execution":
        request = request.model_copy(
            update={
                "step_execution": request.step_execution.model_copy(
                    update={
                        "workflow_id": "unrelated-workflow",
                        "step_execution_id": "unrelated-workflow:run-1:implement:execution:1",
                    }
                )
            }
        )
    elif denial == "unadmitted_ref":
        ref, _ = await writer.persist_json_artifact(
            artifact_service=context.artifacts,
            principal="user-1",
            artifact_class="omnigent.repository_access_snapshot",
            payload={"unrelated": True},
        )
        ref = f"artifact:{ref}"
    else:
        async with context.sessions() as session:
            artifact = await session.get(
                models.TemporalArtifact, context.gateway._artifact_id(ref)
            )
            artifact.redaction_level = models.TemporalArtifactRedactionLevel.RESTRICTED
            if denial == "quarantined":
                artifact.metadata_json = {**artifact.metadata_json, "quarantine": True}
            await session.commit()
    expected_error = {
        "unlinked_execution": TemporalArtifactAuthorizationError,
        "unadmitted_ref": OmnigentArtifactError,
        "restricted": TemporalArtifactAuthorizationError,
        "quarantined": TemporalArtifactStateError,
    }[denial]
    with pytest.raises(expected_error):
        await context.gateway.read_repository_access_snapshot(ref, request=request)


async def test_invalid_plan_artifact_pointer_grants_no_execution_access(
    repository_consumers,
):
    context = repository_consumers
    compiled = await context.compile()
    unrelated_id, _ = await writer.persist_json_artifact(
        artifact_service=context.artifacts,
        principal="user-1",
        artifact_class="unrelated.private",
        payload={"private": "preserve artifact authorization"},
    )
    binding = compiled.binding.model_copy(update={"plan_artifact_ref": unrelated_id})
    with pytest.raises((ValueError, OmnigentArtifactError)):
        await reader._load_verified_execution_plan(
            binding, workflow_id=_WORKFLOW, step_execution_id=_STEP
        )
    async with context.sessions() as session:
        assert not await TemporalArtifactRepository(session).list_links(unrelated_id)


async def test_child_plan_reads_parent_artifacts_with_its_admitted_principal(
    repository_consumers,
):
    context = repository_consumers
    parent = await context.compile()
    async with context.sessions() as session:
        session.add(
            models.ManagedAgentProviderProfile(
                profile_id="provider-opencode-native",
                runtime_id="opencode",
                provider_id="opencode-go",
            )
        )
        await session.commit()
    prepared = await children.omnigent_prepare_child_execution_plan_activity(
        {
            "principal": "user-1",
            "parentWorkflowId": _WORKFLOW,
            "parentExecutionPlan": parent.binding.model_dump(by_alias=True),
            "childWorkflowId": "mm:repository-access-child",
            "childWorkflowRequest": {
                "initial_parameters": {
                    "targetRuntime": "omnigent",
                    "repository": _target(),
                    "publishMode": "none",
                    "workflow": {
                        "instructions": "Continue with the same selected repository."
                    },
                }
            },
        }
    )
    from moonmind.schemas.agent_runtime_models import OmnigentExecutionPlanBinding

    child_binding = OmnigentExecutionPlanBinding.model_validate(
        prepared["initial_parameters"]["omnigentExecutionPlan"]
    )
    child = await reader._load_verified_execution_plan(
        child_binding, admitted_principal="user-1"
    )
    assert (
        child.payload.credentialBindings["source"].connectionRef
        == "selected-repository"
    )
    assert (
        child.payload.repositoryAuthorityRefs["source"]
        != parent.envelope.payload.repositoryAuthorityRefs["source"]
    )
    with pytest.raises(TemporalArtifactAuthorizationError):
        await context.gateway.read_bytes(
            parent.envelope.payload.resolvedTools["repositoryAccess"]["source"][
                "artifactRef"
            ]
        )


@pytest.mark.parametrize(
    "denial", ["missing_destination", "disabled", "stale", "unadmitted_pr_mode"]
)
async def test_typed_publisher_denies_missing_or_stale_destination_before_remote_effects(
    repository_consumers, tmp_path, monkeypatch, denial
):
    context = repository_consumers
    compiled = await context.compile(
        "none" if denial == "missing_destination" else "branch"
    )
    await context.link_inputs(compiled)
    if denial in {"disabled", "stale"}:
        async with context.sessions() as session:
            connection = await session.get(
                models.RepositoryConnectionRecord, "selected-repository"
            )
            if denial == "disabled":
                connection.lifecycle = "disabled"
            else:
                connection.credential_revision += 1
            await session.commit()
    publisher = OmnigentWorkspacePublicationService(
        tmp_path,
        artifact_gateway=context.gateway,
        execution_plan_store=context.plans,
        repository_credential_service=OmnigentGithubCredentialService(
            SimpleNamespace(),
            session_factory=context.sessions,
            artifact_gateway=context.gateway,
        ),
    )
    remote = AsyncMock(
        side_effect=AssertionError("denied destination must not contact a remote")
    )
    publisher._run = remote
    monkeypatch.setattr(
        "moonmind.omnigent.workspace_publication.resolve_github_credential", remote
    )
    request = _request(
        compiled,
        publish_mode="pr" if denial == "unadmitted_pr_mode" else "branch",
        workspace_id="a" * 24,
    )
    with pytest.raises((ValueError, HarnessPlatformError)):
        await publisher.publish_request_workspace(
            request=request,
            current_workflow_id=_WORKFLOW,
            current_step_execution_id=_STEP,
        )
    remote.assert_not_awaited()


@pytest.mark.parametrize("publish_mode", ["branch", "pr"])
@pytest.mark.parametrize("ambient_configured", [True, False])
@pytest.mark.parametrize(
    "repository_consumers",
    ["https://github.com", "https://ghe.example.invalid"],
    indirect=True,
)
async def test_production_publisher_uses_destination_connection_before_remote_commands(
    repository_consumers, tmp_path, monkeypatch, publish_mode, ambient_configured
):
    context = repository_consumers
    compiled = await context.compile(publish_mode)
    plan = await context.link_inputs(compiled)
    if not ambient_configured:
        monkeypatch.delenv("GITHUB_TOKEN")
        monkeypatch.delenv("GH_TOKEN")
    for prefix in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{prefix}_NAME", "Qualification")
        monkeypatch.setenv(f"GIT_{prefix}_EMAIL", "qualification@example.invalid")
    workspace_id = hashlib.sha256(f"{_WORKFLOW}:{_STEP}".encode()).hexdigest()[:24]
    workspace = tmp_path / "temporal_sandbox" / workspace_id / "repo"
    workspace.mkdir(parents=True)
    origin = tmp_path / "origin.git"
    git("init", "--bare", "--initial-branch=main", str(origin), cwd=tmp_path)
    git("init", "--initial-branch=main", cwd=workspace)
    (workspace / "work.txt").write_text("base\n")
    git("add", ".", cwd=workspace)
    git("commit", "-m", "base", cwd=workspace)
    canonical_remote = f"{context.endpoint}/{_REPOSITORY}.git"
    git("remote", "add", "origin", canonical_remote, cwd=workspace)
    git("config", f"url.{origin}.insteadOf", canonical_remote, cwd=workspace)
    git("push", "origin", "main", cwd=workspace)
    (workspace / "work.txt").write_text("completed work\n")
    SandboxWorkspaceRecordStore(tmp_path).ensure(
        SandboxWorkspaceRecord(workspace_id, _WORKFLOW, _STEP, "repo")
    )
    request = _request(compiled, publish_mode=publish_mode, workspace_id=workspace_id)
    monkeypatch.setattr(production, "generic_host_enabled", lambda: True)
    monkeypatch.setattr(
        production, "resolved_server_url", lambda: "http://omnigent:8080"
    )
    monkeypatch.setenv("MOONMIND_OMNIGENT_HOST_SERVER_URL", "http://omnigent:8080")
    monkeypatch.setenv("MOONMIND_OMNIGENT_EXPECTED_HOST_OWNER", "deployment")
    monkeypatch.setenv("WORKFLOW_WORKSPACE_ROOT", str(tmp_path))
    services = production.build_generic_omnigent_execution_services(
        session_factory=context.sessions
    )
    publisher = services.generic_realizer._workspace_publisher

    async def ambient(*_a, **_kw):
        raise AssertionError("typed publication must not resolve an ambient credential")

    monkeypatch.setattr(
        "moonmind.omnigent.workspace_publication.resolve_github_credential", ambient
    )
    observed = []
    run = publisher._run

    async def run_with_evidence(*argv, **kwargs):
        if any(operation in argv for operation in ("fetch", "push", "ls-remote")):
            assert kwargs["env"]["GITHUB_TOKEN"] == "selected-credential-canary"
            assert kwargs["env"].get("GH_TOKEN") != "other-ambient-credential-canary"
            observed.append(argv)
        return await run(*argv, **kwargs)

    publisher._run = run_with_evidence
    lookups = []

    def github(http_request):
        expected_host = (
            "api.github.com"
            if context.endpoint == "https://github.com"
            else urlsplit(context.endpoint).hostname
        )
        assert http_request.url.host == expected_host
        expected_path = (
            f"/repos/{_REPOSITORY}/pulls"
            if context.endpoint == "https://github.com"
            else f"/api/v3/repos/{_REPOSITORY}/pulls"
        )
        assert http_request.url.path == expected_path
        assert (
            http_request.headers["Authorization"] == "Bearer selected-credential-canary"
        )
        head = http_request.url.params["head"].split(":", 1)[1]
        lookups.append(head)
        return httpx.Response(
            200,
            json=[
                {
                    "number": 4676,
                    "html_url": f"{context.endpoint}/{_REPOSITORY}/pull/4676",
                    "head": {
                        "ref": head,
                        "sha": git("rev-parse", "HEAD", cwd=workspace),
                        "repo": {"full_name": _REPOSITORY},
                    },
                    "base": {"ref": "main", "repo": {"full_name": _REPOSITORY}},
                    "draft": False,
                }
            ],
        )

    client = httpx.AsyncClient
    monkeypatch.setattr(
        "moonmind.workflows.adapters.github_service.httpx.AsyncClient",
        lambda **kwargs: client(transport=httpx.MockTransport(github), **kwargs),
    )
    evidence = await publisher.publish_request_workspace(
        request=request, current_workflow_id=_WORKFLOW, current_step_execution_id=_STEP
    )
    assert evidence["push_status"] == "pushed"
    assert evidence["remote_verified"] is True
    assert (
        git("rev-parse", f"refs/heads/{evidence['push_branch']}", cwd=origin)
        == evidence["push_head_sha"]
    )
    assert observed
    if publish_mode == "pr":
        assert (
            evidence["pull_request_url"]
            == f"{context.endpoint}/{_REPOSITORY}/pull/4676"
        )
        assert lookups == [evidence["push_branch"]]
    else:
        assert not lookups
    async with context.sessions() as session:
        source = await session.get(
            models.TemporalArtifact,
            context.gateway._artifact_id(
                plan.payload.resolvedTools["repositoryAccess"]["source"]["artifactRef"]
            ),
        )
        assert (
            b"selected-credential-canary"
            not in (
                await context.artifacts.read(
                    artifact_id=source.artifact_id, principal="user-1"
                )
            )[1]
        )


@pytest.fixture
def native_review_activity(monkeypatch):
    from temporalio import activity
    monkeypatch.setattr(
        activity,
        "info",
        lambda: SimpleNamespace(
            workflow_id="merge-automation:review-only", workflow_run_id="review-run-1"
        ),
    )


@pytest.mark.parametrize("owner_principal", ["user-1", "system"])
@pytest.mark.parametrize(
    "repository_consumers", ["https://github.com", "https://github.com/"], indirect=True
)
async def test_native_review_uses_selected_plan_authority_and_rejects_revocation(
    repository_consumers,
    owner_principal,
    native_review_activity,
):
    from moonmind.workflows.temporal.merge_automation_repository_access import (
        merge_automation_repository_token,
    )

    context = repository_consumers
    compiled = await context.compile(
        profile_tools=("gh",),
        workflow={
            "instructions": "Request a fresh review only.",
            "publish": {
                "mode": "none",
                "mergeAutomation": {
                    "enabled": True,
                    "finishMode": "review_only",
                    "reviewLoop": {"enabled": True, "provider": "codex"},
                },
            },
        },
    )
    authority = {
        "principal": owner_principal,
        "executionOwner": "merge-automation:review-only",
        "parentExecutionPlan": compiled.binding.model_dump(by_alias=True),
    }
    with pytest.raises(ValueError, match="executing workflow"):
        async with merge_automation_repository_token(
            {**authority, "executionOwner": "other-workflow"},
            repository=_REPOSITORY,
            operation="read",
        ):
            pytest.fail("a different execution acquired credentials")
    for operation in ("read", "review_request"):
        async with merge_automation_repository_token(
            authority, repository=_REPOSITORY, operation=operation
        ) as token:
            assert token == "selected-credential-canary"

    with pytest.raises(ValueError, match="target conflicts"):
        async with merge_automation_repository_token(
            authority, repository="Other/Repository", operation="review_request"
        ):
            pytest.fail("wrong target acquired credentials")

    async with context.sessions() as session:
        connection = await session.get(
            models.RepositoryConnectionRecord, "selected-repository"
        )
        connection.lifecycle = "disabled"
        await session.commit()
    with pytest.raises(Exception, match="unavailable|disabled|ACTIVE|active|lifecycle"):
        async with merge_automation_repository_token(
            authority, repository=_REPOSITORY, operation="review_request"
        ):
            pytest.fail("revoked authority acquired credentials")


async def test_native_review_cannot_broaden_read_only_plan(repository_consumers, native_review_activity):
    from moonmind.workflows.temporal.merge_automation_repository_access import (
        merge_automation_repository_token,
    )

    compiled = await repository_consumers.compile(profile_tools=("gh",))
    authority = {
        "principal": "user-1",
        "executionOwner": "merge-automation:review-only",
        "parentExecutionPlan": compiled.binding.model_dump(by_alias=True),
    }
    async with merge_automation_repository_token(
        authority, repository=_REPOSITORY, operation="read"
    ) as token:
        assert token == "selected-credential-canary"
    with pytest.raises(ValueError, match="operation or role is not admitted"):
        async with merge_automation_repository_token(
            authority, repository=_REPOSITORY, operation="review_request"
        ):
            pytest.fail("ordinary read-only authority acquired review permission")


@pytest.mark.parametrize(
    "repository_consumers", ["https://github.enterprise.test"], indirect=True
)
async def test_native_review_rejects_host_mismatch_before_acquisition(
    repository_consumers, monkeypatch, native_review_activity
):
    from moonmind.workflows.temporal.merge_automation_repository_access import (
        merge_automation_repository_token,
    )

    compiled = await repository_consumers.compile(profile_tools=("gh",))
    acquire = AsyncMock(
        side_effect=AssertionError("must validate the host before acquisition")
    )
    monkeypatch.setattr(
        OmnigentGithubCredentialService, "acquire_repository_use", acquire
    )
    with pytest.raises(ValueError, match="target host conflicts"):
        async with merge_automation_repository_token(
            {
                "principal": "user-1",
                "executionOwner": "merge-automation:review-only",
                "parentExecutionPlan": compiled.binding.model_dump(by_alias=True),
            },
            repository=_REPOSITORY,
            operation="read",
        ):
            pytest.fail("different-host authority was consumed")
    acquire.assert_not_awaited()
