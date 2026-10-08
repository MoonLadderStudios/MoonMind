"""Authored GitHub actions survive Skill resolution and require recorded grants."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from moonmind.omnigent.host_failures import OmnigentOAuthHostError
from moonmind.omnigent.profile_bound_execution import (
    OmnigentProfileBoundExecutionCoordinator,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.schemas.agent_skill_models import SkillSelector
from moonmind.services.skill_resolution import (
    AgentSkillResolver,
    BuiltInSkillLoader,
    SkillResolutionContext,
)
from moonmind.workflows.executions.repository_contract import DEFAULT_GIT_CONNECTION_REF
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow
from tests.helpers.github_projection import projection_reservation, reserve_projection
from tests.helpers.repository_connections import (
    github_pat_connection,
    github_repository_assignment,
    record_repository_connections,
)

PATCH = "run-github-action-permissions-v1"
ROOT = Path(__file__).parents[3]


def request(**parameters):
    return AgentExecutionRequest(
        agentKind="external",
        agentId="omnigent",
        correlationId="actions",
        idempotencyKey="actions",
        executionProfileRef="codex",
        workspaceSpec={"repository": "owner/repo"},
        parameters={"requiredCapabilities": ["gh"], **parameters},
    )


async def run_request(
    skill_name=None, *, parameters=None, skill_payload=None, enabled=True, resolved=None
):
    wf = MoonMindRunWorkflow()
    info = SimpleNamespace(
        namespace="default", workflow_id="actions", run_id="run", parent=None
    )
    patches = {
        "run-agent-required-capabilities-propagation-v1",
        "run-resolved-skill-required-capabilities-v1",
    }
    if enabled:
        patches.add(PATCH)
    with patch(
        "moonmind.workflows.temporal.workflows.run.workflow.info", return_value=info
    ), patch(
        "moonmind.workflows.temporal.workflows.run.workflow.patched",
        side_effect=lambda key: key in patches,
    ):
        if skill_name:
            if resolved is None:
                resolved = await AgentSkillResolver(
                    loaders=[BuiltInSkillLoader(ROOT / ".agents/skills")]
                ).resolve(
                    SkillSelector(include=[{"name": skill_name}]),
                    SkillResolutionContext(snapshot_id="actions"),
                )
            wf._record_resolved_selected_skill(
                resolved=resolved,
                selected_skill=skill_name,
                node_id="step",
                terminal_contract_enabled=False,
            )
        node = {"targetRuntime": "omnigent"}
        if skill_name:
            node.update(
                selectedSkill=skill_name,
                skill={"name": skill_name, **(skill_payload or {})},
            )
        return wf._build_agent_execution_request(
            node_inputs=node,
            node_id="step",
            tool_name="omnigent",
            workflow_parameters={
                "workspaceSpec": {"repository": "owner/repo"},
                **(parameters or {}),
            },
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "skill_name, missing",
    [
        ("fix-ci", "branch_write"),
        ("fix-merge-conflicts", "write"),
        ("fix-comments", "review_request"),
        ("pr-resolver", "merge_request"),
    ],
)
async def test_resolved_skill_actions_deny_narrowed_assignment_before_secret(
    monkeypatch, tmp_path, skill_name, missing
):
    built = await run_request(
        skill_name, parameters={"publishMode": "auto", "githubOperations": []}
    )
    connection = github_pat_connection(
        DEFAULT_GIT_CONNECTION_REF, "ACTION_PAT"
    ).model_copy(
        update={
            "allowed_operations": (
                "read",
                "write",
                "branch_write",
                "review_request",
                "merge_request",
            )
        }
    )
    engine = await record_repository_connections(
        monkeypatch,
        tmp_path,
        connection,
        assignments=[
            github_repository_assignment(
                connection.id,
                "owner/repo",
                operations=[
                    op for op in connection.allowed_operations if op != missing
                ],
            )
        ],
    )
    secret = AsyncMock(return_value="selected-secret")
    monkeypatch.setattr("moonmind.auth.github_credentials._resolve_secret_ref", secret)
    try:
        with pytest.raises(OmnigentOAuthHostError, match=missing):
            await OmnigentProfileBoundExecutionCoordinator._github_token(built)
        secret.assert_not_awaited()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "skill_name, finish, operations",
    [
        ("fix-ci", None, ("read", "write", "branch_write")),
        ("fix-merge-conflicts", None, ("read", "write", "branch_write")),
        ("fix-comments", None, ("read", "write", "branch_write", "review_request")),
        (
            "pr-resolver",
            "fix_only",
            ("read", "write", "branch_write", "review_request"),
        ),
        (
            "pr-resolver",
            "merge",
            ("read", "write", "branch_write", "review_request", "merge_request"),
        ),
        ("batch-github-workflows", None, ("read",)),
    ],
)
async def test_resolved_skill_accepts_exact_operations(
    monkeypatch, tmp_path, skill_name, finish, operations
):
    built = await run_request(
        skill_name,
        parameters={
            "publishMode": "none" if skill_name.startswith("batch-") else "auto"
        },
        skill_payload={"inputs": {"finishMode": finish}} if finish else {},
    )
    connection = github_pat_connection(
        DEFAULT_GIT_CONNECTION_REF, "ACTION_PAT"
    ).model_copy(update={"allowed_operations": operations})
    engine = await record_repository_connections(
        monkeypatch,
        tmp_path,
        connection,
        assignments=[
            github_repository_assignment(
                connection.id, "owner/repo", operations=operations
            )
        ],
    )
    secret = AsyncMock(return_value="selected-secret")
    monkeypatch.setattr("moonmind.auth.github_credentials._resolve_secret_ref", secret)
    try:
        assert (
            await OmnigentProfileBoundExecutionCoordinator._github_token(built)
            == "selected-secret"
        )
        secret.assert_awaited_once()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "parameters",
    [
        {"publishMode": "auto"},
        {"skill": {"publish": {"mode": "auto"}}},
        {"repositoryMutationRequired": True},
        {"repositoryOperation": "write", "githubOperations": []},
        {"githubOperations": "merge_request"},
        {"githubOperations": ["admin"]},
        {"githubOperations": [False]},
        {"githubOperations": None},
        {"skill": {"sideEffect": {"kind": "unknown_action"}}},
        *[
            {
                "skill": {
                    "sideEffect": {"kind": "merge_pull_request"},
                    "inputs": {"finishMode": value},
                }
            }
            for value in (False, 0, [], "surprise", "review_only")
        ],
    ],
)
async def test_invalid_action_intent_stops_before_any_host_or_lease_work(parameters):
    coordinator = object.__new__(OmnigentProfileBoundExecutionCoordinator)
    # No dependencies exist: validation must precede even bridge/host binding.
    result = await coordinator.execute(request(**parameters))
    assert result.failure_class == "user_error"
    assert result.provider_error_code == "github_action_intent_invalid"


@pytest.mark.asyncio
async def test_runtime_and_skill_declarations_have_replay_gate():
    parameters = {
        "requiredCapabilities": ["gh"],
        "githubOperations": ["write"],
        "publishMode": "none",
    }
    current = await run_request("fix-comments", parameters=parameters)
    replay = await run_request("fix-comments", parameters=parameters, enabled=False)
    assert current.parameters["githubOperations"] == ["write"]
    assert current.parameters["skill"]["publish"]["githubOperations"] == [
        "write",
        "branch_write",
        "review_request",
    ]
    assert "githubOperations" not in replay.parameters
    assert "publish" not in replay.parameters["skill"]


@pytest.mark.asyncio
async def test_generic_actions_reach_credential_boundary(monkeypatch):
    from moonmind.auth.github_credentials import (
        GitHubCredentialSource,
        ResolvedGitHubCredential,
    )

    built = await run_request(
        parameters={
            "requiredCapabilities": ["gh"],
            "githubOperations": ["merge_request"],
        }
    )
    resolver = AsyncMock(
        return_value=ResolvedGitHubCredential(
            source=GitHubCredentialSource.UNRESOLVABLE,
            diagnostic="denied merge_request",
        )
    )
    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_api_key_resolve.resolve_default_github_connection_credential",
        resolver,
    )
    with pytest.raises(OmnigentOAuthHostError, match="merge_request"):
        await OmnigentProfileBoundExecutionCoordinator._github_token(built)
    assert resolver.await_args.kwargs["required_operations"] == (
        "read",
        "merge_request",
    )


@pytest.mark.asyncio
async def test_auto_merge_conflict_skill_projects_usable_git_credentials(
    tmp_path, monkeypatch
):
    """Resolved requirements select the gh projection used by Git's real helper."""
    import asyncio
    import os
    import shutil
    import subprocess

    from moonmind.omnigent.oauth_host_runtime import OmnigentOAuthHostRuntime
    from tests.unit.omnigent.test_gh_config_migration_suppression import (
        _static_host_github_block,
    )

    built = await run_request("fix-merge-conflicts", parameters={"publishMode": "auto"})
    assert "gh" in built.parameters["requiredCapabilities"]
    connection = github_pat_connection(DEFAULT_GIT_CONNECTION_REF, "ACTION_PAT")
    engine = await record_repository_connections(
        monkeypatch,
        tmp_path,
        connection,
        assignments=[
            github_repository_assignment(
                connection.id,
                "owner/repo",
                operations=("read", "write", "branch_write"),
            )
        ],
    )
    monkeypatch.setenv("ACTION_PAT", "selected_action_token")
    try:
        token = await OmnigentProfileBoundExecutionCoordinator._github_token(built)
    finally:
        await engine.dispose()
    gh = shutil.which("gh")
    assert gh, "This qualification requires the image's GitHub CLI"
    home = tmp_path / "host-home"
    config = home / ".cache/moonmind-xdg"
    runtime = OmnigentOAuthHostRuntime(
        client=SimpleNamespace(), workspace_root=tmp_path
    )

    async def run_projection(*argv, **kwargs):
        index = argv.index("-ceu")
        script = argv[index + 1].replace("/home/app", str(home))
        completed = await asyncio.to_thread(
            subprocess.run,
            ["/bin/sh", "-ceu", script, *argv[index + 2 :]],
            input=kwargs["input_bytes"],
            capture_output=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        return 0, "", ""

    runtime._run = run_projection
    reservation = projection_reservation("owned")
    reserve_projection(config / "gh", reservation)
    await runtime._project_github_credential(
        token,
        github_projection_reservation=reservation,
        cache_volume="owned-cache",
        host_image_ref="host",
        runtime_uid=os.getuid(),
        runtime_gid=os.getgid(),
    )
    environment = {
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(config),
        "PATH": os.environ["PATH"],
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GH_PROMPT_DISABLED": "1",
    }
    setup = (
        _static_host_github_block()
        .replace("/home/app", str(home))
        .replace("/opt/moonmind-tools/bin/gh", gh)
    )
    await asyncio.to_thread(
        subprocess.run,
        ["/bin/sh", "-c", setup],
        env=environment,
        capture_output=True,
        check=True,
    )
    filled = await asyncio.to_thread(
        subprocess.run,
        ["git", "credential", "fill"],
        env=environment,
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True,
        text=True,
        check=True,
    )
    assert "password=selected_action_token" in filled.stdout
    assert "selected_action_token" not in (config / "git/config").read_text()


@pytest.mark.asyncio
async def test_denied_action_grant_preserves_existing_host_before_binding(
    monkeypatch, tmp_path
):
    """No lease claim or cleanup can run when an existing action loses its grant."""
    built = await run_request("pr-resolver", parameters={"publishMode": "auto"})
    connection = github_pat_connection(DEFAULT_GIT_CONNECTION_REF, "ACTION_PAT")
    engine = await record_repository_connections(
        monkeypatch,
        tmp_path,
        connection,
        assignments=[github_repository_assignment(connection.id, "owner/repo")],
    )
    secret = AsyncMock(return_value="must-not-be-read")
    monkeypatch.setattr("moonmind.auth.github_credentials._resolve_secret_ref", secret)
    coordinator = object.__new__(OmnigentProfileBoundExecutionCoordinator)
    coordinator._run_store = SimpleNamespace(
        get_or_create=AsyncMock(), bind_profile_authorization=AsyncMock()
    )
    coordinator._hosts = SimpleNamespace(
        create_host_lease=AsyncMock(), claim_host_lease_cleanup=AsyncMock()
    )
    coordinator._host_release = SimpleNamespace(stop_host=AsyncMock())
    try:
        result = await coordinator.execute(built)
        assert result.provider_error_code == "github_auth_unavailable"
        assert "merge_request" in result.summary
        coordinator._run_store.get_or_create.assert_not_awaited()
        coordinator._run_store.bind_profile_authorization.assert_not_awaited()
        coordinator._hosts.create_host_lease.assert_not_awaited()
        coordinator._hosts.claim_host_lease_cleanup.assert_not_awaited()
        coordinator._host_release.stop_host.assert_not_awaited()
        secret.assert_not_awaited()
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    "mode, expected",
    [
        ("branch", ("write", "branch_write")),
        ("pr", ("write", "branch_write", "review_request")),
    ],
)
def test_saved_publication_requires_only_destination_actions(mode, expected):
    from moonmind.omnigent.workspace_intent import authored_github_operations

    built = request(publishMode=mode, requiredCapabilities=["git"])
    assert authored_github_operations(built) == ("read",)
    assert authored_github_operations(built, for_publication=True) == expected


@pytest.mark.parametrize("finish", [None, "", "merge", "fix_only"])
def test_merge_side_effect_finish_defaults_and_narrowing(finish):
    from moonmind.omnigent.workspace_intent import authored_github_operations

    built = request(
        skill={
            "sideEffect": {"kind": "merge_pull_request"},
            "inputs": {"finishMode": finish},
        }
    )
    operations = authored_github_operations(built)
    assert ("merge_request" in operations) is (finish != "fix_only")


def test_canonical_skill_actions_cannot_be_erased_by_parameter_projection():
    from moonmind.omnigent.workspace_intent import authored_github_operations

    built = request(skill={"publish": {"githubOperations": []}}).model_copy(
        update={
            "skill": {"publish": {"githubOperations": ["write", "branch_write"]}},
        }
    )
    assert authored_github_operations(built) == ("read", "write", "branch_write")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "metadata",
    [
        {"publish": {"mode": "none"}},
        {"publish": {"githubOperations": None}},
        {"publish": {"githubOperations": "write"}},
        {"sideEffect": {"kind": "enqueue_children"}},
    ],
)
async def test_resolved_skill_conflicting_or_malformed_actions_are_rejected(metadata):
    with pytest.raises(ValueError):
        await run_request(
            "pr-resolver", parameters={"publishMode": "auto"}, skill_payload=metadata
        )


@pytest.mark.asyncio
async def test_deployment_skill_content_preserves_action_requirements_to_request(
    tmp_path,
):
    from contextlib import asynccontextmanager
    from unittest.mock import MagicMock

    from api_service.services.agent_skills_service import AgentSkillsService
    from moonmind.services.skill_resolution import DeploymentSkillLoader
    from tests.unit.api.test_agent_skills_service import template_db

    artifacts = AsyncMock()
    artifact = SimpleNamespace(artifact_id="action-artifact")
    artifacts.create.return_value = (artifact, None)
    artifacts.write_complete.return_value = artifact
    async with template_db(tmp_path) as sessions, sessions() as session:
        service = AgentSkillsService(session=session, artifact_service=artifacts)
        await service.create_skill(slug="deployment-action", title="Action")
        saved = await service.update_skill_content(
            skill_slug="deployment-action",
            content="""---
name: deployment-action
metadata:
  required-capabilities: [gh]
  publish:
    mode: auto
    githubOperations: [write, branch_write]
  sideEffect:
    kind: merge_pull_request
---
# Action
""",
        )
        metadata = artifacts.create.await_args.kwargs["metadata_json"]
        definitions, metadata_rows = MagicMock(), MagicMock()
        definitions.scalars.return_value.all.return_value = [saved]
        metadata_rows.all.return_value = [("action-artifact", metadata)]
        db = SimpleNamespace(
            execute=AsyncMock(side_effect=[definitions, metadata_rows])
        )

        @asynccontextmanager
        async def recorded_artifact_boundary():
            yield db

        entries = await DeploymentSkillLoader().load_skills(
            SkillSelector(include=[{"name": "deployment-action"}]),
            SkillResolutionContext(
                snapshot_id="deployment-actions",
                async_session_maker=recorded_artifact_boundary,
            ),
        )
        built = await run_request(
            "deployment-action",
            resolved={"skills": entries},
            parameters={"publishMode": "auto"},
            skill_payload={"inputs": {"finishMode": "fix_only"}},
        )
    from moonmind.omnigent.workspace_intent import authored_github_operations

    assert authored_github_operations(built) == ("read", "write", "branch_write")
    assert built.parameters["skill"]["sideEffect"]["kind"] == "merge_pull_request"


@pytest.mark.asyncio
async def test_metadata_preflight_is_rechecked_before_secret_access(
    monkeypatch, tmp_path
):
    from api_service.services.repository_connections import RepositoryConnectionService
    from moonmind.workflows.temporal.runtime.managed_api_key_resolve import (
        validate_default_github_connection_operations,
    )

    connection = github_pat_connection(
        DEFAULT_GIT_CONNECTION_REF, "ACTION_PAT"
    ).model_copy(update={"allowed_operations": ("read", "merge_request")})
    assignment = github_repository_assignment(
        connection.id, "owner/repo", operations=connection.allowed_operations
    )
    engine = await record_repository_connections(
        monkeypatch, tmp_path, connection, assignments=[assignment]
    )
    secret = AsyncMock(return_value="selected_token")
    monkeypatch.setattr("moonmind.auth.github_credentials._resolve_secret_ref", secret)
    try:
        assert (
            await validate_default_github_connection_operations(
                repo="owner/repo", required_operations=("read", "merge_request")
            )
            is None
        )
        secret.assert_not_awaited()
        from api_service.db.base import async_session_maker

        async with async_session_maker() as session:
            await RepositoryConnectionService(session).set_assignment(
                assignment.model_copy(update={"operations": ("read",)}),
                actor_ref="system:deployment",
                request_id="narrowed-after-preflight",
                principal_ref="system:deployment",
                principal_scope=("system", None),
            )
        with pytest.raises(OmnigentOAuthHostError, match="merge_request"):
            await OmnigentProfileBoundExecutionCoordinator._github_token(
                request(githubOperations=["merge_request"])
            )
        secret.assert_not_awaited()
    finally:
        await engine.dispose()


def test_different_selected_skills_cannot_inherit_parent_merge_actions():
    from moonmind.omnigent.workspace_intent import (
        WorkspaceIntentCompilationError,
        authored_github_operations,
    )

    built = request(
        skill={"name": "pr-resolver", "sideEffect": {"kind": "merge_pull_request"}}
    ).model_copy(update={"skill": {"name": "fix-ci"}})
    with pytest.raises(WorkspaceIntentCompilationError, match="selected Skill"):
        authored_github_operations(built)


@pytest.mark.asyncio
@pytest.mark.parametrize("finish", ["merge", "fix_only"])
async def test_merge_gate_planner_carries_finish_mode_to_action_boundary(
    monkeypatch, tmp_path, finish
):
    from moonmind.omnigent.workspace_intent import authored_github_operations
    from moonmind.workflows.temporal import worker_runtime
    from moonmind.workflows.temporal.workflows.merge_gate import (
        build_resolver_run_request,
    )

    child = build_resolver_run_request(
        parent_workflow_id="merge-parent",
        jira_issue_key=None,
        merge_method="squash",
        finish_mode=finish,
        pull_request={
            "repo": "owner/repo",
            "number": 1,
            "url": "https://github.com/owner/repo/pull/1",
            "headSha": "a" * 40,
            "headBranch": "candidate",
            "baseBranch": "main",
        },
        resolver_template={"targetRuntime": "omnigent"},
    )
    # Container-backend deployment readiness is separate from this auth path.
    monkeypatch.setattr(
        worker_runtime, "_enforce_required_capability_readiness", lambda **kwargs: None
    )
    plan = worker_runtime._build_runtime_planner()(
        inputs=child["initial_parameters"],
        parameters={},
        snapshot=SimpleNamespace(
            digest="registry:test", artifact_ref="artifact://registry"
        ),
    )
    node = next(
        node
        for node in plan["nodes"]
        if node["inputs"].get("selectedSkill") == "pr-resolver"
    )
    built = await run_request(
        "pr-resolver",
        parameters=child["initial_parameters"],
        skill_payload=node["inputs"]["skill"],
    )
    assert built.parameters["skill"]["inputs"]["finishMode"] == finish
    assert ("merge_request" in authored_github_operations(built)) is (finish == "merge")
    connection = github_pat_connection(DEFAULT_GIT_CONNECTION_REF, "ACTION_PAT")
    engine = await record_repository_connections(
        monkeypatch,
        tmp_path,
        connection,
        assignments=[github_repository_assignment(connection.id, "owner/repo")],
    )
    secret = AsyncMock(return_value="selected_token")
    monkeypatch.setattr("moonmind.auth.github_credentials._resolve_secret_ref", secret)
    try:
        if finish == "merge":
            with pytest.raises(OmnigentOAuthHostError, match="merge_request"):
                await OmnigentProfileBoundExecutionCoordinator._github_token(built)
            secret.assert_not_awaited()
        else:
            assert (
                await OmnigentProfileBoundExecutionCoordinator._github_token(built)
                == "selected_token"
            )
            secret.assert_awaited_once()
    finally:
        await engine.dispose()


def test_runtime_planner_preserves_authored_skill_action_metadata():
    from moonmind.workflows.temporal.worker_runtime import _build_runtime_planner

    skill = {
        "name": "custom-action",
        "publish": {"githubOperations": ["write"]},
        "sideEffect": {
            "kind": "custom_github_action",
            "githubOperations": ["review_request"],
        },
    }
    plan = _build_runtime_planner()(
        inputs={
            "task": {
                "instructions": "Review the pull request",
                "runtime": {"mode": "codex_cli"},
                "skill": skill,
            }
        },
        parameters={},
        snapshot=SimpleNamespace(
            digest="registry:test", artifact_ref="artifact://registry"
        ),
    )
    compiled = plan["nodes"][0]["inputs"]["skill"]
    assert compiled["publish"] == skill["publish"]
    assert compiled["sideEffect"] == skill["sideEffect"]


@pytest.mark.parametrize("finish", [False, 0, [], {}, "unknown", "review_only"])
def test_resolver_child_producer_rejects_invalid_finish_authority(finish):
    from moonmind.workflows.temporal.workflows.merge_gate import (
        build_resolver_run_request,
    )

    with pytest.raises(ValueError):
        build_resolver_run_request(
            parent_workflow_id="merge-parent",
            jira_issue_key=None,
            merge_method="squash",
            finish_mode=finish,
            pull_request={
                "repo": "owner/repo",
                "number": 1,
                "url": "https://github.com/owner/repo/pull/1",
                "headSha": "a" * 40,
            },
        )


@pytest.mark.parametrize("finish", [None, "", "  ", "merge", " fix_only "])
def test_resolver_child_producer_preserves_supported_default_payload(finish):
    from moonmind.workflows.temporal.workflows.merge_gate import (
        build_resolver_run_request,
    )

    kwargs = {
        "parent_workflow_id": "merge-parent",
        "jira_issue_key": None,
        "merge_method": "squash",
        "pull_request": {
            "repo": "owner/repo",
            "number": 1,
            "url": "https://github.com/owner/repo/pull/1",
            "headSha": "a" * 40,
        },
    }
    expected = build_resolver_run_request(
        **kwargs, finish_mode="fix_only" if finish == " fix_only " else "merge"
    )
    assert build_resolver_run_request(**kwargs, finish_mode=finish) == expected


def test_canonical_skill_args_cannot_be_discarded_into_default_merge():
    from moonmind.omnigent.workspace_intent import authored_github_operations

    built = request(
        skill={
            "name": "pr-resolver",
            "sideEffect": {"kind": "merge_pull_request"},
            "args": {"finishMode": "fix_only"},
        }
    )
    assert "merge_request" not in authored_github_operations(built)


@pytest.mark.asyncio
@pytest.mark.parametrize("revocation", ["operation", "credential"])
async def test_action_revoked_after_metadata_preflight_preserves_live_ownership(
    monkeypatch, tmp_path, revocation
):
    from api_service.services.repository_connections import RepositoryConnectionService

    connection = github_pat_connection(
        DEFAULT_GIT_CONNECTION_REF, "ACTION_PAT"
    ).model_copy(update={"allowed_operations": ("read", "merge_request")})
    assignment = github_repository_assignment(
        connection.id, "owner/repo", operations=connection.allowed_operations
    )
    engine = await record_repository_connections(
        monkeypatch, tmp_path, connection, assignments=[assignment]
    )
    secret = AsyncMock(return_value="selected_token")
    if revocation == "credential":
        secret.side_effect = RuntimeError("selected credential disabled")
    monkeypatch.setattr("moonmind.auth.github_credentials._resolve_secret_ref", secret)
    from tests.unit.omnigent.test_oauth_profile_lifecycle import (
        _run_coordinator_failure_case,
    )

    retained = {}

    async def validate_then_revoke(built):
        denied = await OmnigentProfileBoundExecutionCoordinator._github_action_authority_error(
            built
        )
        assert denied is None
        from api_service.db.base import async_session_maker

        if revocation == "operation":
            async with async_session_maker() as session:
                await RepositoryConnectionService(session).set_assignment(
                    assignment.model_copy(update={"operations": ("read",)}),
                    actor_ref="system:deployment",
                    request_id="revoke-between-admission-and-secret",
                    principal_ref="system:deployment",
                    principal_scope=("system", None),
                )

    async def setup(runtime, coordinator):
        retained.update(runtime=runtime, coordinator=coordinator)
        coordinator._github_action_authority_error = validate_then_revoke
        coordinator._github_token = (
            OmnigentProfileBoundExecutionCoordinator._github_token
        )
        coordinator._run_store.reserve_github_projection = AsyncMock(
            return_value=projection_reservation(coordinator._hosts.lease.lease_id),
        )
        coordinator._run_store.validate_github_projection = AsyncMock()
        runtime.reserve_github_projection = AsyncMock()
        runtime.stop_host = AsyncMock()

    built = request(
        githubOperations=["merge_request"],
        omnigent={"launchPolicyRef": "codex-on-demand@1"},
    )
    import hashlib

    built = built.model_copy(
        update={
            "workspace_spec": {
                **built.workspace_spec,
                "workspaceLocator": {
                    "kind": "sandbox",
                    "workspaceId": hashlib.sha256(b"actions:actions").hexdigest()[:24],
                },
            }
        }
    )
    try:
        events, actions, owners = await _run_coordinator_failure_case(
            fail_at="container_start",
            code="OMNIGENT_GITHUB_PROJECTION_REFRESH_FAILED",
            request=built,
            setup=setup,
        )
        coordinator, runtime = retained["coordinator"], retained["runtime"]
        coordinator._run_store.reserve_github_projection.assert_awaited_once()
        runtime.reserve_github_projection.assert_awaited_once()
        runtime._prepare_workspace.assert_not_awaited()
        runtime._launch_on_demand.assert_not_awaited()
        runtime.stop_host.assert_not_awaited()
        assert "provider_released" not in actions
        assert not {"host_stop", "host_remove"}.intersection(owners)
        cleanup = next(payload for kind, payload in events if kind == "host_cleanup")
        assert cleanup["status"] == "waiting"
        assert cleanup["metadata"]["cleanupCompleted"] is False
        if revocation == "operation":
            secret.assert_not_awaited()
        else:
            secret.assert_awaited_once()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_action_token_is_reused_by_lazy_workspace_owner(monkeypatch, tmp_path):
    from moonmind.schemas.agent_runtime_models import AgentRunResult
    from tests.unit.omnigent.test_oauth_profile_lifecycle import (
        _drive_authority_chain_coordinator,
    )

    connection = github_pat_connection(DEFAULT_GIT_CONNECTION_REF, "ACTION_PAT")
    engine = await record_repository_connections(
        monkeypatch,
        tmp_path,
        connection,
        assignments=[
            github_repository_assignment(
                connection.id, "owner/repo", operations=("read",)
            )
        ],
    )
    secret = AsyncMock(return_value="selected_token")
    monkeypatch.setattr("moonmind.auth.github_credentials._resolve_secret_ref", secret)
    credential = AsyncMock(
        side_effect=OmnigentProfileBoundExecutionCoordinator._github_token
    )
    try:
        outcome = await _drive_authority_chain_coordinator(
            AsyncMock(return_value=AgentRunResult(summary="read completed")),
            request_parameters={"requiredCapabilities": ["gh"], "publishMode": "none"},
            credential_resolver=credential,
            resolve_source_credential=True,
        )
        assert outcome[-1].summary == "read completed"
        secret.assert_awaited_once()
        credential.assert_awaited_once()
    finally:
        await engine.dispose()


def test_portable_resolver_rejects_native_review_mode_when_config_model_expands(
    monkeypatch,
):
    """A broader automation enum cannot expand the portable Skill's contract.

    ``review_only`` has its own explicit rejection; this covers any future
    native-only mode the parent configuration may accept.
    """
    from typing import Literal

    from pydantic import Field

    from moonmind.workflows.temporal.workflows import merge_gate

    class ExpandedMergeAutomationConfig(merge_gate.MergeAutomationConfigModel):
        finish_mode: Literal["merge", "fix_only", "review_only", "native_review"] = Field(
            "merge", alias="finishMode"
        )

    assert (
        ExpandedMergeAutomationConfig.model_validate(
            {"finishMode": "native_review"}
        ).finish_mode
        == "native_review"
    )
    monkeypatch.setattr(
        merge_gate, "MergeAutomationConfigModel", ExpandedMergeAutomationConfig
    )
    with pytest.raises(ValueError, match="portable resolver"):
        merge_gate.build_resolver_run_request(
            parent_workflow_id="merge-parent",
            jira_issue_key=None,
            merge_method="squash",
            finish_mode="native_review",
            pull_request={
                "repo": "owner/repo",
                "number": 1,
                "url": "https://github.com/owner/repo/pull/1",
                "headSha": "a" * 40,
            },
        )


def _runtime_signal_request(signals):
    """The supported runtime.parameters producer preserves arbitrary JSON values."""
    info = SimpleNamespace(
        namespace="default", workflow_id="signals", run_id="run", parent=None
    )
    with patch(
        "moonmind.workflows.temporal.workflows.run.workflow.info", return_value=info
    ), patch(
        "moonmind.workflows.temporal.workflows.run.workflow.patched",
        side_effect=lambda name: name
        in {
            PATCH,
            "run-model-selection-presence-4636-v1",
            "run-agent-required-capabilities-propagation-v1",
        },
    ):
        built = MoonMindRunWorkflow()._build_agent_execution_request(
            node_inputs={
                "runtime": {
                    "mode": "omnigent",
                    "parameters": {"requiredCapabilities": ["gh"], **signals},
                }
            },
            node_id="signals",
            tool_name="omnigent",
            workflow_parameters={"workspaceSpec": {"repository": "owner/repo"}},
        )
    for key, value in signals.items():
        assert built.parameters[key] == value
    return built


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "signals",
    [
        *[
            {"repositoryMutationRequired": value}
            for value in ("true", "false", 1, 0, [], {})
        ],
        *[
            {"repositoryOperation": value}
            for value in (" WRITE ", "Write", "delete", True, 0, [], {})
        ],
        *[{"publishMode": value} for value in ("unknown", True, False, 0, [], {})],
        {"skill": {"publish": {"mode": " AUTO "}}},
        {"skill": {"publish": {"mode": True}}},
    ],
)
async def test_runtime_mutation_signals_fail_closed_before_ownership_or_token(
    monkeypatch, signals
):
    built = _runtime_signal_request(signals)
    resolver = AsyncMock()
    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_api_key_resolve.resolve_default_github_connection_credential",
        resolver,
    )
    coordinator = object.__new__(OmnigentProfileBoundExecutionCoordinator)
    outcome = await coordinator.execute(built)
    assert outcome.provider_error_code == "github_action_intent_invalid"
    with pytest.raises(OmnigentOAuthHostError) as rejected:
        await OmnigentProfileBoundExecutionCoordinator._github_token(built)
    assert rejected.value.code == "github_action_intent_invalid"
    resolver.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "signals, expected",
    [
        ({"repositoryMutationRequired": False}, ("read",)),
        ({"repositoryMutationRequired": None}, ("read",)),
        ({"repositoryOperation": " READ ", "publishMode": " NONE "}, ("read",)),
        ({"repositoryOperation": None, "publishMode": None}, ("read",)),
        ({"repositoryOperation": "", "publishMode": "  "}, ("read",)),
        (
            {
                "repositoryOperation": " WRITE ",
                "githubOperations": ["write", "branch_write"],
            },
            ("read", "write", "branch_write"),
        ),
        ({"publishMode": " BRANCH "}, ("read", "write", "branch_write")),
        (
            {"publishMode": " PR ", "repositoryMutationRequired": True},
            ("read", "write", "branch_write", "review_request"),
        ),
        (
            {"publishMode": " AUTO ", "githubOperations": ["write", "branch_write"]},
            ("read", "write", "branch_write"),
        ),
    ],
)
async def test_valid_runtime_mutation_signals_preserve_exact_action_requirements(
    monkeypatch, signals, expected
):
    from moonmind.auth.github_credentials import (
        GitHubCredentialSource,
        ResolvedGitHubCredential,
    )

    built = _runtime_signal_request(signals)
    resolver = AsyncMock(
        return_value=ResolvedGitHubCredential(
            token="selected_token", source=GitHubCredentialSource.SETTINGS_TOKEN_REF
        )
    )
    monkeypatch.setattr(
        "moonmind.workflows.temporal.runtime.managed_api_key_resolve.resolve_default_github_connection_credential",
        resolver,
    )
    assert (
        await OmnigentProfileBoundExecutionCoordinator._github_token(built)
        == "selected_token"
    )
    assert resolver.await_args.kwargs["required_operations"] == expected
