"""Compiler and runtime agree on frozen repository targets and action intent."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from api_service.services import omnigent_execution_plan_service as plan_service
from moonmind.omnigent.host_services.github_credentials import (
    OmnigentGithubCredentialService,
)
from moonmind.omnigent.profile_bound_execution import (
    OmnigentProfileBoundExecutionCoordinator,
)
from moonmind.omnigent.workspace_intent import (
    WorkspaceIntentCompilationError,
    authored_repository_source,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from tests.unit.services.test_omnigent_execution_plan_service import (
    _compile_opencode_plan,
    _configure_github_repository_plan_test,
    _PlanStore,
    _ReadableRepositoryPlanArtifacts,
    _ready_opencode_image_pair,  # noqa: F401
)


def _request(workspace_repository="MoonLadderStudios/MoonMind", **parameters):
    return AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="frozen-actions",
        idempotencyKey="frozen-actions",
        workspaceSpec={"repository": workspace_repository},
        parameters={"requiredCapabilities": ["gh"], **parameters},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "parameters, expected",
    [
        (
            {"publishMode": "auto", "githubOperations": ["write", "branch_write"]},
            ["read", "write", "branch_write"],
        ),
        ({"githubOperations": ["review_request"]}, ["read", "review_request"]),
        (
            {"workflow": {"skill": {"name": "fix-ci"}}},
            ["read", "write", "branch_write"],
        ),
        (
            {
                "workflow": {
                    "steps": [{"tool": {"type": "skill", "name": "fix-comments"}}]
                }
            },
            ["read", "write", "branch_write", "review_request"],
        ),
        (
            {
                "workflow": {
                    "skill": {"name": "pr-resolver", "args": {"finishMode": "fix_only"}}
                }
            },
            ["read", "write", "branch_write", "review_request"],
        ),
    ],
)
async def test_plan_freezes_canonical_parameters_and_resolved_skill_actions(
    monkeypatch, tmp_path, parameters, expected
):
    repository, engine, sessions = await _configure_github_repository_plan_test(
        monkeypatch, tmp_path
    )
    artifacts = _ReadableRepositoryPlanArtifacts()
    try:
        compiled = await _compile_opencode_plan(
            monkeypatch,
            artifacts=artifacts,
            launch_policy_ref="opencode-on-demand@1",
            plan_store=_PlanStore(object()),
            session_factory=sessions,
            profile_tools=("gh",),
            extra_parameters={"repository": repository, **parameters},
        )
        access = compiled.envelope.payload.resolvedTools["repositoryAccess"]
        selection = json.loads(
            artifacts.payloads[
                access["collaboration"]["artifactRef"].removeprefix("artifact:")
            ]
        )["selection"]
        assert selection["operations"] == expected
        source = json.loads(
            artifacts.payloads[
                access["source"]["artifactRef"].removeprefix("artifact:")
            ]
        )["selection"]
        assert source["operations"] == ["read"]
        assert "destination" not in access
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra, canonical_skill",
    [
        ({"githubOperations": ["merge_request"]}, None),
        ({"skill": {"sideEffect": {"kind": "merge_pull_request"}}}, None),
        ({}, {"sideEffect": {"kind": "merge_pull_request"}}),
    ],
)
async def test_runtime_widening_fails_before_credential_provider_or_writer(
    monkeypatch, tmp_path, extra, canonical_skill
):
    repository, engine, sessions = await _configure_github_repository_plan_test(
        monkeypatch, tmp_path
    )
    artifacts = _ReadableRepositoryPlanArtifacts()
    provider = Mock(side_effect=AssertionError("credential provider was reached"))
    backend = SimpleNamespace(run=AsyncMock())
    try:
        compiled = await _compile_opencode_plan(
            monkeypatch,
            artifacts=artifacts,
            launch_policy_ref="opencode-on-demand@1",
            plan_store=_PlanStore(object()),
            session_factory=sessions,
            profile_tools=("gh",),
            extra_parameters={"repository": repository},
        )
        monkeypatch.setattr(
            "moonmind.auth.github_app_wiring.build_bound_acquirer_for_connection",
            provider,
        )
        gateway = SimpleNamespace(
            read_repository_access_snapshot=AsyncMock(
                side_effect=lambda ref, **_kwargs: artifacts.payloads[
                    ref.removeprefix("artifact:")
                ]
            )
        )
        credential_service = OmnigentGithubCredentialService(
            backend, session_factory=sessions, artifact_gateway=gateway
        )
        with pytest.raises(ValueError, match="actions.*admitted snapshot"):
            await credential_service.materialize(
                plan=compiled.envelope,
                request=_request(repository, **extra).model_copy(
                    update={"skill": canonical_skill}
                ),
                resolved_tools=compiled.envelope.payload.resolvedTools,
                owner_ref="frozen-actions",
                writer_image_ref="writer@sha256:" + "1" * 64,
                runtime_uid=1000,
                runtime_gid=1000,
                projection_reservation={"ownerRef": "frozen-actions"},
            )
        provider.assert_not_called()
        backend.run.assert_not_awaited()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "parameters",
    [
        {"githubOperations": ["merge_request"]},
        {"workflow": {"skill": {"name": "pr-resolver"}}},
    ],
)
async def test_compiler_rejects_requested_merge_without_assignment(
    monkeypatch, tmp_path, parameters
):
    repository, engine, sessions = await _configure_github_repository_plan_test(
        monkeypatch,
        tmp_path,
        operations=("read", "write", "branch_write", "review_request"),
    )
    try:
        with pytest.raises(ValueError, match="operation|route|authority"):
            await _compile_opencode_plan(
                monkeypatch,
                artifacts=_ReadableRepositoryPlanArtifacts(),
                launch_policy_ref="opencode-on-demand@1",
                plan_store=_PlanStore(object()),
                session_factory=sessions,
                profile_tools=("gh",),
                extra_parameters={"repository": repository, **parameters},
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "declared", [[], ["review_request"], ["write", "branch_write", "review_request"]]
)
async def test_runtime_accepts_exact_subset_and_read_only_frozen_actions(
    monkeypatch, tmp_path, declared
):
    repository, engine, sessions = await _configure_github_repository_plan_test(
        monkeypatch, tmp_path
    )
    artifacts = _ReadableRepositoryPlanArtifacts()
    monkeypatch.setenv("REVIEW_REPOSITORY_PAT", "selected-credential-canary")
    try:
        compiled = await _compile_opencode_plan(
            monkeypatch,
            artifacts=artifacts,
            launch_policy_ref="opencode-on-demand@1",
            plan_store=_PlanStore(object()),
            session_factory=sessions,
            profile_tools=("gh",),
            extra_parameters={"repository": repository, "publishMode": "pr"},
        )
        gateway = SimpleNamespace(
            read_repository_access_snapshot=AsyncMock(
                side_effect=lambda ref, **_kwargs: artifacts.payloads[
                    ref.removeprefix("artifact:")
                ]
            )
        )
        credential_service = OmnigentGithubCredentialService(
            None, session_factory=sessions, artifact_gateway=gateway
        )
        request = _request(repository, githubOperations=declared)
        # Source and destination retain their own roles regardless of agent actions.
        for role, operation in (
            ("collaboration", "read"),
            ("source_read", "read"),
            ("destination_write", "write"),
        ):
            acquired = await credential_service.acquire_repository_use(
                plan=compiled.envelope, request=request, role=role, operation=operation
            )
            assert acquired.credential.use_now(bytes) == b"selected-credential-canary"
            acquired.credential.clear()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["artifact", "checkpoint"])
async def test_self_contained_restore_does_not_reacquire_source_authority(kind):
    source = {"kind": kind}
    if kind == "artifact":
        source.update(
            artifactRef="artifact://saved", artifactDigest="sha256:" + "a" * 64
        )
    else:
        source.update(
            checkpointRef="artifact://saved",
            restoreContract="moonmind.workspace-snapshot.v1",
        )
    sessions = Mock(side_effect=AssertionError("source credential reacquisition"))
    artifacts = _ReadableRepositoryPlanArtifacts()
    result = await plan_service._admit_repository_plan_inputs(
        session_factory=sessions,
        db_session=None,
        artifact_service=artifacts,
        principal="user-1",
        workflow_id="restored",
        initial_parameters={
            "repository": "owner/repo",
            "workspaceSpec": {"workspaceSource": source},
        },
        requires_github=False,
        parent_plan=None,
    )
    assert result["sourceKind"] == kind
    assert result["access"] == result["bindings"] == {}
    sessions.assert_not_called()
    assert artifacts.payloads == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "other_projection",
    [
        {"workspaceSpec": {"repository": "owner/repo-a"}},
        {
            "workspaceSpec": {
                "repositoryTarget": {
                    "provider": "git",
                    "repository": {"name": "owner/repo-a"},
                }
            }
        },
        {
            "workspaceSpec": {
                "workspaceSource": {"kind": "repository", "repository": "owner/repo-a"}
            }
        },
        {"workflow": {"repository": "owner/repo-a"}},
    ],
)
async def test_compiler_rejects_repository_conflict_before_connection_or_artifact_effects(
    other_projection,
):
    sessions = Mock(side_effect=AssertionError("repository access preceded validation"))
    artifacts = _ReadableRepositoryPlanArtifacts()
    with pytest.raises(WorkspaceIntentCompilationError, match="repository.*conflict"):
        await plan_service._admit_repository_plan_inputs(
            session_factory=sessions,
            db_session=None,
            artifact_service=artifacts,
            principal="user-1",
            workflow_id="conflicting-repositories",
            initial_parameters={
                "repository": "owner/repo-b",
                **other_projection,
            },
            requires_github=True,
            parent_plan=None,
        )
    sessions.assert_not_called()
    assert artifacts.payloads == {}


def test_typed_repository_target_cannot_conflict_with_legacy_runtime_projection():
    request = _request(repository="owner/repo-b").model_copy(
        update={
            "workspace_spec": {
                "repositoryTarget": {
                    "provider": "git",
                    "repository": {"name": "owner/repo-a"},
                }
            }
        }
    )
    with pytest.raises(WorkspaceIntentCompilationError, match="repository.*conflict"):
        authored_repository_source(request)


def test_equivalent_typed_repository_target_preserves_canonical_source():
    request = _request(repository="https://github.com/owner/repo.git").model_copy(
        update={
            "workspace_spec": {
                "repositoryTarget": {
                    "provider": "git",
                    "repository": {"name": "Owner/Repo"},
                }
            }
        }
    )
    assert authored_repository_source(request) == "Owner/Repo"


@pytest.mark.parametrize(
    "changed", [{"provider": "lore"}, {"connectionRef": "other-connection"}]
)
def test_same_name_typed_repository_targets_reject_different_authority(changed):
    from moonmind.workflows.executions.repository_contract import (
        compile_repository_target,
    )

    target = {
        "provider": "git",
        "connectionRef": "selected-connection",
        "repository": {"name": "owner/repo"},
        "branch": {"name": "main"},
    }
    other = compile_repository_target({**target, **changed}).model_dump(
        by_alias=True, mode="json"
    )
    request = _request(repository=other).model_copy(
        update={"workspace_spec": {"repositoryTarget": target}}
    )
    with pytest.raises(WorkspaceIntentCompilationError, match="repository.*conflict"):
        authored_repository_source(request)


@pytest.mark.asyncio
@pytest.mark.parametrize("publish_mode", [False, 0, [], {}])
async def test_compiler_preserves_malformed_publication_signals_for_canonical_validation(
    publish_mode,
):
    sessions = Mock(side_effect=AssertionError("repository access preceded validation"))
    with pytest.raises(WorkspaceIntentCompilationError, match="publishMode"):
        await plan_service._admit_repository_plan_inputs(
            session_factory=sessions,
            db_session=None,
            artifact_service=_ReadableRepositoryPlanArtifacts(),
            principal="user-1",
            workflow_id="invalid-publication",
            initial_parameters={
                "repository": "owner/repo",
                "publishMode": publish_mode,
            },
            requires_github=True,
            parent_plan=None,
        )
    sessions.assert_not_called()


@pytest.mark.parametrize(
    "workspace, parameter",
    [
        ("owner/repo-a", "owner/repo-b"),
        ("/work/Repo", "/work/repo"),
        ("https://git.example/owner/repo-a", "https://git.example/owner/repo-b"),
        (
            {"provider": "lore", "repository": {"name": "Owner/Repo"}},
            {"provider": "lore", "repository": {"name": "owner/repo"}},
        ),
    ],
)
def test_material_repository_projection_conflicts_are_rejected(workspace, parameter):
    with pytest.raises(WorkspaceIntentCompilationError, match="repository.*conflict"):
        authored_repository_source(_request(workspace, repository=parameter))


@pytest.mark.parametrize(
    "workspace, parameter",
    [
        ("Owner/Repo", "https://github.com/owner/repo.git"),
        ("https://GITHUB.COM/Owner/Repo.git", "owner/repo"),
        ("owner/repo.git", "OWNER/REPO"),
        ("/work/Repo", "/work/Repo"),
        ("https://git.example/owner/repo", "https://git.example/owner/repo"),
    ],
)
def test_equivalent_repository_projections_preserve_source(workspace, parameter):
    assert (
        authored_repository_source(_request(workspace, repository=parameter))
        == workspace
    )


@pytest.mark.asyncio
async def test_generic_credential_owner_rejects_conflicting_repositories_before_snapshot():
    artifacts = SimpleNamespace(
        read_repository_access_snapshot=AsyncMock(),
        read_bytes=AsyncMock(),
    )
    plan = SimpleNamespace(
        payload=SimpleNamespace(
            credentialBindings={},
            resolvedTools={
                "repositoryAccess": {
                    "collaboration": {
                        "artifactRef": "artifact:test",
                        "snapshotRef": "repository-access-snapshot:sha256:" + "0" * 64,
                    }
                }
            },
        )
    )
    service = OmnigentGithubCredentialService(None, artifact_gateway=artifacts)

    with pytest.raises(WorkspaceIntentCompilationError) as excinfo:
        await service.admitted_repository_identity(
            plan=plan,
            request=_request("owner/repo-a", repository="owner/repo-b"),
            role="collaboration",
            operation="read",
        )

    assert excinfo.value.code == "repository_intent_conflict"
    artifacts.read_repository_access_snapshot.assert_not_awaited()
    artifacts.read_bytes.assert_not_awaited()


@pytest.mark.asyncio
async def test_profile_conflicting_repositories_fail_before_any_host_or_token(
    monkeypatch,
):
    resolver = AsyncMock()
    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_api_key_resolve.resolve_default_github_connection_credential",
        resolver,
    )
    coordinator = object.__new__(OmnigentProfileBoundExecutionCoordinator)
    result = await coordinator.execute(
        _request("owner/repo-a", repository="owner/repo-b")
    )
    assert result.provider_error_code == "repository_intent_conflict"
    resolver.assert_not_awaited()


@pytest.mark.asyncio
async def test_profile_preflight_and_publisher_use_the_same_canonical_repository(
    monkeypatch,
):
    from moonmind.schemas.agent_runtime_models import AgentRunResult
    from tests.unit.omnigent.test_oauth_profile_lifecycle import (
        _drive_authority_chain_coordinator,
    )

    calls = {}
    initialize = OmnigentProfileBoundExecutionCoordinator.__init__

    def capture_runtime(self, *args, **kwargs):
        initialize(self, *args, **kwargs)
        runtime = kwargs["host_runtime"]
        for name in ("prepare_host", "publish_workspace"):
            calls[name] = AsyncMock(wraps=getattr(runtime, name))
            setattr(runtime, name, calls[name])

    monkeypatch.setattr(
        OmnigentProfileBoundExecutionCoordinator, "__init__", capture_runtime
    )
    *_, result = await _drive_authority_chain_coordinator(
        AsyncMock(return_value=AgentRunResult(summary="done")),
        request_parameters={"repository": "https://github.com/OWNER/REPO.git"},
    )
    assert result.failure_class is None
    assert calls["prepare_host"].await_args.kwargs["target_repository"] == "owner/repo"
    assert calls["publish_workspace"].await_args.kwargs["repository"] == "owner/repo"


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["prepare", "fresh", "resumed"])
@pytest.mark.parametrize("repository_access", ["none", "source_only"])
async def test_generic_repository_conflicts_without_collaboration_fail_before_effects(
    boundary, repository_access
):
    from moonmind.omnigent.harness_platform.execution_plan import (
        create_execution_plan_envelope,
    )
    from moonmind.omnigent.host_runtime import GenericOmnigentHostRuntime
    from moonmind.omnigent.runtime_bindings import stable_binding_id
    from tests.unit.omnigent.test_generic_platform_production_services import (
        _PUSHED_PUBLICATION,
        _exact_plan,
        _generic_publication_harness,
        _prime_attested_host_binding,
    )

    payload = _exact_plan("opencode-go/model").payload.model_dump(
        mode="json", by_alias=True
    )
    payload["resolvedTools"]["repositoryAccess"] = (
        {
            "source": {
                "snapshotRef": "repository-access-snapshot:sha256:" + "a" * 64,
                "artifactRef": "artifact:source-access",
            }
        }
        if repository_access == "source_only"
        else {}
    )
    plan = create_execution_plan_envelope(payload)
    harness = await _generic_publication_harness(_PUSHED_PUBLICATION)
    if boundary == "resumed":
        await _prime_attested_host_binding(harness, plan)
    request = harness.publish_request.model_copy(
        update={
            "parameters": {
                **harness.publish_request.parameters,
                "repository": "owner/different-repository",
            }
        }
    )
    sessions = Mock(side_effect=AssertionError("conflict reached repository access"))
    gateway = SimpleNamespace(read_repository_access_snapshot=AsyncMock())
    runtime = object.__new__(GenericOmnigentHostRuntime)
    runtime._github_credentials = OmnigentGithubCredentialService(
        None, session_factory=sessions, artifact_gateway=gateway
    )
    workspace = AsyncMock(side_effect=AssertionError("conflict prepared workspace"))
    runtime._workspace = SimpleNamespace(materialize=workspace)
    runtime.cleanup = AsyncMock()
    harness.realizer._host_runtime = runtime
    claim = AsyncMock(side_effect=AssertionError("conflict claimed delivery"))
    harness.realizer._turn_commands = SimpleNamespace(claim=claim)
    provider = AsyncMock(side_effect=AssertionError("conflict acquired provider"))
    harness.realizer._provider_leases.acquire_all = provider
    driver = AsyncMock()
    harness.realizer._session_driver = driver
    binding_id = stable_binding_id(
        execution_plan_ref=plan.planRef, idempotency_key=request.idempotency_key
    )
    retained = await harness.runtime_store.get(binding_id)
    with pytest.raises(WorkspaceIntentCompilationError, match="repository.*conflict"):
        if boundary == "prepare":
            await runtime.prepare(
                request=request,
                plan=plan,
                host_class=object(),
                launch_policy=object(),
                repository_owner_ref=request.idempotency_key,
            )
        else:
            await harness.realizer.execute(request, plan)
    assert await harness.runtime_store.get(binding_id) == retained
    sessions.assert_not_called()
    gateway.read_repository_access_snapshot.assert_not_awaited()
    workspace.assert_not_awaited()
    claim.assert_not_awaited()
    provider.assert_not_awaited()
    driver.assert_not_awaited()
    runtime.cleanup.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("host_state", ["fresh", "resumed"])
@pytest.mark.parametrize("rejection", ["widened_request", "revoked_assignment"])
async def test_generic_host_rejects_action_widening_before_delivery_or_cleanup(
    monkeypatch, tmp_path, rejection, host_state
):
    from api_service.services.repository_connections import RepositoryConnectionService
    from moonmind.omnigent.harness_platform.execution_plan import (
        create_execution_plan_envelope,
    )
    from moonmind.omnigent.host_runtime import GenericOmnigentHostRuntime
    from moonmind.omnigent.runtime_bindings import stable_binding_id
    from tests.helpers.repository_connections import github_repository_assignment
    from tests.unit.omnigent.test_generic_platform_production_services import (
        _PUSHED_PUBLICATION,
        _exact_plan,
        _generic_publication_harness,
        _prime_attested_host_binding,
    )

    repository, engine, sessions = await _configure_github_repository_plan_test(
        monkeypatch, tmp_path
    )
    artifacts = _ReadableRepositoryPlanArtifacts()
    try:
        compiled = await _compile_opencode_plan(
            monkeypatch,
            artifacts=artifacts,
            launch_policy_ref="opencode-on-demand@1",
            plan_store=_PlanStore(object()),
            session_factory=sessions,
            profile_tools=("gh",),
            extra_parameters={
                "repository": repository,
                "githubOperations": ["write", "branch_write"],
            },
        )
        payload = _exact_plan("opencode-go/model").payload.model_dump(
            mode="json", by_alias=True
        )
        payload["credentialBindings"]["collaboration"] = (
            compiled.envelope.payload.credentialBindings["collaboration"].model_dump(
                mode="json", by_alias=True
            )
        )
        payload["resolvedTools"] = compiled.envelope.payload.resolvedTools
        plan = create_execution_plan_envelope(payload)
        harness = await _generic_publication_harness(_PUSHED_PUBLICATION)
        if host_state == "resumed":
            await _prime_attested_host_binding(harness, plan)
        if rejection == "revoked_assignment":
            async with sessions() as session:
                await RepositoryConnectionService(session).set_assignment(
                    github_repository_assignment(
                        "review-repository", repository, operations=("read",)
                    ),
                    actor_ref="system:deployment",
                    request_id="narrow-after-host-ready",
                    principal_ref="system:deployment",
                    principal_scope=("system", None),
                )
        secret = AsyncMock(
            side_effect=AssertionError("rejected resume resolved a credential value")
        )
        monkeypatch.setattr(
            "moonmind.auth.github_credentials._resolve_secret_ref", secret
        )
        gateway = SimpleNamespace(
            read_repository_access_snapshot=AsyncMock(
                side_effect=lambda ref, **_kwargs: artifacts.payloads[
                    ref.removeprefix("artifact:")
                ]
            )
        )
        runtime = object.__new__(GenericOmnigentHostRuntime)
        runtime._github_credentials = OmnigentGithubCredentialService(
            None, session_factory=sessions, artifact_gateway=gateway
        )
        runtime.cleanup = AsyncMock()
        harness.realizer._host_runtime = runtime
        claim = AsyncMock(side_effect=AssertionError("widened resume claimed delivery"))
        harness.realizer._turn_commands = SimpleNamespace(claim=claim)
        provider = AsyncMock(
            side_effect=AssertionError("widened resume acquired provider")
        )
        harness.realizer._provider_leases.acquire_all = provider
        driver = AsyncMock()
        harness.realizer._session_driver = driver
        request = harness.publish_request.model_copy(
            update={
                "parameters": {
                    **harness.publish_request.parameters,
                    "publishMode": "none",
                    "githubOperations": (
                        ["merge_request"]
                        if rejection == "widened_request"
                        else ["write", "branch_write"]
                    ),
                    "requiredCapabilities": ["gh"],
                }
            }
        )
        binding_id = stable_binding_id(
            execution_plan_ref=plan.planRef, idempotency_key=request.idempotency_key
        )
        retained = await harness.runtime_store.get(binding_id)
        expected = (
            "actions.*admitted snapshot"
            if rejection == "widened_request"
            else "assignment changed"
        )
        with pytest.raises(ValueError, match=expected):
            await harness.realizer.execute(request, plan)
        assert await harness.runtime_store.get(binding_id) == retained
        claim.assert_not_awaited()
        provider.assert_not_awaited()
        driver.assert_not_awaited()
        runtime.cleanup.assert_not_awaited()
        secret.assert_not_awaited()
    finally:
        await engine.dispose()
