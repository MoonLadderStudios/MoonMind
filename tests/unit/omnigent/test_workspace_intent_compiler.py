"""Tests for the single workspace-intent compiler (issue #3558).

Every normal authoring surface (create, edit, rerun, schedule, preset) converges
on one ``AgentExecutionRequest``; these tests prove the compiler produces
equivalent immutable intent for equivalent authored requests, reproduces the
same record on retry, and fails closed on runtime-specific shortcuts before any
host mutation.
"""

from __future__ import annotations

import hashlib

import pytest

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.omnigent.workspace_intent import (
    WORKSPACE_INTENT_LOCATOR_REQUIRED,
    WORKSPACE_INTENT_UNSAFE_INPUT,
    WorkspaceIntentCompilationError,
    authored_repository_mutation_required,
    authored_required_capabilities,
    compile_workspace_intent,
)

_WORKFLOW_ID = "workflow-1"
_STEP_EXECUTION_ID = "workflow-1:run-1:step-1:execution:1"
_WORKSPACE_ID = hashlib.sha256(
    f"{_WORKFLOW_ID}:{_STEP_EXECUTION_ID}".encode("utf-8")
).hexdigest()[:24]


def _locator(**overrides):
    payload = {
        "kind": "sandbox",
        "workspaceId": _WORKSPACE_ID,
        "relativePath": "repo",
    }
    payload.update(overrides)
    return payload


def _request(*, workspace_spec=None, parameters=None, **overrides):
    spec = {
        "workspaceLocator": _locator(),
        "repository": "https://github.com/acme/widgets.git",
        "startingBranch": "main",
        "targetBranch": "feature/x",
        "checkoutCommit": "abc1234",
        "restoreInputRefs": ["artifact://chk1", "external-state:sess-9"],
    }
    if workspace_spec is not None:
        spec = workspace_spec
    params = {
        "repository": "https://github.com/acme/widgets.git",
        "publishMode": "pr",
        "requiredCapabilities": ["gh", "git"],
    }
    if parameters is not None:
        params = parameters
    payload = dict(
        agentKind="external",
        agentId="omnigent",
        correlationId=_WORKFLOW_ID,
        idempotencyKey="idem-1",
        inputRefs=["artifact://in1"],
        resolvedSkillsetRef="artifact://skills@1",
        skill={
            "name": "pr-resolver",
            "contentDigest": "sha256:deadbeef",
            "sideEffect": {"kind": "github_pr"},
        },
        workspaceSpec=spec,
        parameters=params,
    )
    payload.update(overrides)
    return AgentExecutionRequest(**payload)


def _compile(request):
    return compile_workspace_intent(
        request,
        workflow_id=_WORKFLOW_ID,
        step_execution_id=_STEP_EXECUTION_ID,
        run_id="run-1",
        logical_step_id="step-1",
    )


def test_compiles_full_authored_intent() -> None:
    intent = _compile(_request())
    assert intent.repository == "https://github.com/acme/widgets.git"
    assert intent.repository_kind == "github_https"
    assert intent.starting_branch == "main"
    assert intent.target_branch == "feature/x"
    assert intent.checkout_commit == "abc1234"
    assert intent.publish_mode == "pr"
    assert intent.repository_mutation is True
    assert intent.required_capabilities == ("gh", "git")
    assert intent.input_refs == ("artifact://in1",)
    assert intent.workspace_locator.kind == "sandbox"
    # Artifact restore inputs and provider external-state refs stay distinct.
    assert intent.restore_input_refs == ("artifact://chk1",)
    assert intent.external_state_refs == ("external-state:sess-9",)
    assert [p.model_dump(by_alias=True) for p in intent.skill_projections] == [
        {"name": "pr-resolver", "version": None, "digest": "sha256:deadbeef"}
    ]


def test_provider_target_persists_connection_revision_and_remote_tip_axes() -> None:
    request = _request(
        workspace_spec={
            "workspaceLocator": _locator(),
            "repository": {
                "provider": "git",
                "connectionRef": "repository-connection:git-default",
                "repository": {"name": "acme/widgets"},
                "branch": {"name": "main"},
                "revision": {
                    "kind": "git_commit",
                    "commitSha": "abcdef012345",
                },
            },
        },
        parameters={"publishMode": "none", "requiredCapabilities": ["git"]},
    )
    intent = _compile(request)

    assert intent.repository == "acme/widgets"
    assert intent.connection_ref == "repository-connection:git-default"
    assert intent.starting_branch == "main"
    assert intent.checkout_commit == "abcdef012345"
    assert intent.revision_kind == "git_commit"
    assert intent.remote_tip_expectation == {"kind": "read_only"}


def test_canonical_repository_target_persists_connection_authority() -> None:
    """The Run workflow projects repository authority under repositoryTarget."""

    request = _request(
        workspace_spec={
            "workspaceLocator": _locator(),
            "repository": "acme/widgets",
            "repositoryTarget": {
                "provider": "git",
                "connectionRef": "repository-connection:git-default",
                "repository": {"name": "acme/widgets"},
                "branch": {"name": "main"},
            },
        },
        parameters={"publishMode": "none", "requiredCapabilities": ["git"]},
    )

    assert _compile(request).connection_ref == "repository-connection:git-default"


def test_rejects_caller_authored_resolved_repository_evidence() -> None:
    request = _request(
        workspace_spec={
            "workspaceLocator": _locator(),
            "repository": "acme/widgets",
            "resolvedRepositoryTarget": {"authority": "authoritative"},
        }
    )

    with pytest.raises(
        WorkspaceIntentCompilationError,
        match="resolvedRepositoryTarget is runtime-owned",
    ):
        _compile(request)


def test_owner_repo_shorthand_is_classified_as_github_not_local() -> None:
    # The canonical materialization classifier resolves ``owner/repo`` to a
    # GitHub clone, so durable evidence must not redact it as ``[local-source]``.
    request = _request(
        workspace_spec={
            "workspaceLocator": _locator(),
            "repository": "acme/widgets",
        },
        parameters={"repository": "acme/widgets", "publishMode": "none"},
    )
    intent = _compile(request)
    assert intent.repository == "acme/widgets"
    assert intent.repository_kind == "github_https"
    assert intent.evidence()["repository"] == "acme/widgets"


def test_lookalike_https_host_is_not_classified_as_github() -> None:
    # A substring test would mislabel these as GitHub; the exact-host classifier
    # must classify them as a generic remote, never ``github_https``.
    for source in (
        "https://github.com.evil.com/acme/widgets.git",
        "https://evil.com/github.com/acme/widgets.git",
    ):
        request = _request(
            workspace_spec={
                "workspaceLocator": _locator(),
                "repository": source,
            },
            parameters={"repository": source, "publishMode": "none"},
        )
        intent = _compile(request)
        assert intent.repository_kind == "remote"


def test_equivalent_authored_requests_produce_equivalent_intent() -> None:
    # Two authoring surfaces (e.g. create vs. preset-expanded) that reorder keys
    # or vary capability casing but author the same intent compile identically.
    create = _request()
    preset = _request(
        workspace_spec={
            "restoreInputRefs": ["external-state:sess-9", "artifact://chk1"],
            "checkoutCommit": "abc1234",
            "targetBranch": "feature/x",
            "startingBranch": "main",
            "repository": "https://github.com/acme/widgets.git",
            "workspaceLocator": _locator(),
        },
        parameters={
            "requiredCapabilities": ["GH", "GIT"],
            "publishMode": "pr",
            "repository": "https://github.com/acme/widgets.git",
        },
    )
    assert _compile(create).intent_digest == _compile(preset).intent_digest


def test_retry_reproduces_the_same_immutable_intent() -> None:
    request = _request()
    first = _compile(request)
    second = _compile(request)
    assert first.intent_digest == second.intent_digest


def test_read_only_job_has_no_mutation_authority() -> None:
    request = _request(
        parameters={"publishMode": "none", "requiredCapabilities": ["git"]}
    )
    intent = _compile(request)
    assert intent.repository_mutation is False
    assert intent.publish_mode == "none"


def test_fails_closed_on_smuggled_docker_authority() -> None:
    request = _request(
        workspace_spec={
            "workspaceLocator": _locator(),
            "repository": "https://github.com/acme/widgets.git",
            "dockerVolume": "operator-chosen-volume",
        }
    )
    with pytest.raises(WorkspaceIntentCompilationError) as excinfo:
        _compile(request)
    assert excinfo.value.code == WORKSPACE_INTENT_UNSAFE_INPUT


def test_fails_closed_on_credential_shaped_value() -> None:
    request = _request(
        workspace_spec={
            "workspaceLocator": _locator(),
            "repository": "https://github.com/acme/widgets.git",
            "startingBranch": "token=ghp_shouldnotbehere",
        }
    )
    with pytest.raises(WorkspaceIntentCompilationError) as excinfo:
        _compile(request)
    assert excinfo.value.code == WORKSPACE_INTENT_UNSAFE_INPUT


def test_fails_closed_when_locator_missing() -> None:
    request = _request(
        workspace_spec={"repository": "https://github.com/acme/widgets.git"}
    )
    with pytest.raises(WorkspaceIntentCompilationError) as excinfo:
        _compile(request)
    assert excinfo.value.code == WORKSPACE_INTENT_LOCATOR_REQUIRED


def test_extraction_helpers_match_compiled_record() -> None:
    request = _request()
    assert authored_required_capabilities(request) == ("gh", "git")
    assert authored_repository_mutation_required(request) is True


def test_write_operation_requires_mutation_without_publication() -> None:
    request = _request(
        parameters={
            "repository": "https://github.com/acme/widgets.git",
            "repositoryOperation": "write",
            "publishMode": "none",
        }
    )

    assert authored_repository_mutation_required(request) is True
