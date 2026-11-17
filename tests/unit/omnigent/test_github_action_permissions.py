"""Authored GitHub actions survive Skill resolution and require recorded grants."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from moonmind.omnigent.workspace_intent import (
    WorkspaceIntentCompilationError,
    authored_github_operations,
    authored_repository_mutation_required,
)
from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.schemas.agent_skill_models import SkillSelector
from moonmind.services.skill_resolution import (
    AgentSkillResolver,
    BuiltInSkillLoader,
    SkillResolutionContext,
)
from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow

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
        "run-execution-fanout-authorization-v1",
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
async def test_resolved_skill_requires_exact_operations(skill_name, finish, operations):
    built = await run_request(
        skill_name,
        parameters={
            "publishMode": "none" if skill_name.startswith("batch-") else "auto"
        },
        skill_payload={"inputs": {"finishMode": finish}} if finish else {},
    )
    assert authored_repository_mutation_required(built) is (operations != ("read",))
    assert authored_github_operations(built) == operations


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
def test_invalid_action_intent_is_rejected_at_action_compilation(parameters):
    with pytest.raises(WorkspaceIntentCompilationError) as rejected:
        authored_github_operations(request(**parameters))
    assert rejected.value.code == "github_action_intent_invalid"


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
    monkeypatch, finish
):
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
def test_runtime_mutation_signals_fail_closed_at_action_compilation(signals):
    built = _runtime_signal_request(signals)
    with pytest.raises(WorkspaceIntentCompilationError) as rejected:
        authored_github_operations(built)
    assert rejected.value.code == "github_action_intent_invalid"


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
def test_valid_runtime_mutation_signals_preserve_exact_action_requirements(
    signals, expected
):
    built = _runtime_signal_request(signals)
    assert authored_github_operations(built) == expected
