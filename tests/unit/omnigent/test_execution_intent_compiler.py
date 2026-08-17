"""Tests for the compiled Omnigent execution-intent compiler (issue #3706).

These cover the create-journey digest correlation, the typed remediation
controller that replaces the free-form ``annotations.remediationLoop``, the
bounded migration adapter, and the incident replays the issue enumerates.
"""

from __future__ import annotations

import hashlib

import pytest

from moonmind.schemas.agent_runtime_models import AgentExecutionRequest
from moonmind.omnigent.execution_intent import (
    ExecutionIntentCompilationError,
    compile_execution_intent,
    derive_execution_intent_from_request,
)
from moonmind.omnigent.workspace_intent import compile_workspace_intent
from moonmind.schemas.omnigent_execution_intent import (
    EXECUTION_INTENT_INCOMPLETE_AUTHORITY,
    EXECUTION_INTENT_SCHEMA_ID,
)

_WF = "workflow-1"
_SE = "workflow-1:run-1:step-1:execution:1"
_WS_ID = hashlib.sha256(f"{_WF}:{_SE}".encode("utf-8")).hexdigest()[:24]

_CONTROLLER = {
    "kind": "remediation_loop",
    "loopId": "loop-1",
    "remediationTool": {"type": "skill", "name": "remediator"},
    "verificationTool": {"type": "skill", "name": "verifier"},
    "workspacePolicy": "continue_from_loop_head",
    "budgets": {"hardMaxAttempts": 5},
    "terminalPolicy": {
        "fullyImplemented": "advance",
        "additionalWorkNeeded": "continue_when_allowed",
        "blocked": "stop",
        "noDetermination": "stop",
        "failedUnrecoverable": "stop",
    },
    "sideEffectPolicy": "workflow_owned",
    "publicationPolicy": "evaluate_after_terminal",
}


def _locator(**overrides):
    payload = {"kind": "sandbox", "workspaceId": _WS_ID, "relativePath": "repo"}
    payload.update(overrides)
    return payload


def _effective_launch(**overrides):
    payload = {
        "executionProfileRef": "omnigent-codex@1",
        "executionProfileDigest": "sha256:prof",
        "launchPolicyRef": "codex-on-demand@1",
        "providerProfileId": "pp-1",
        "harness": "codex-native",
        "providerRuntime": "codex_cli",
        "hostMode": "on_demand_docker",
        "serverImageRef": "server@sha256:" + "a" * 64,
        "hostImageRef": "host@sha256:" + "b" * 64,
        "networkRef": "moonmind-egress",
        "egressProfileRef": "egress",
        "snapshotRef": "omnigent-launch:sha256:deadbeef",
        "cleanup": {"mode": "remove"},
        "capabilities": {"reconnectSession": True, "replaceSession": True},
    }
    payload.update(overrides)
    return payload


def _request(*, workspace_spec=None, parameters=None, **overrides):
    spec = workspace_spec or {
        "workspaceLocator": _locator(),
        "repository": "https://github.com/acme/widgets.git",
        "startingBranch": "main",
        "targetBranch": "feature/x",
        "checkoutCommit": "abc1234",
    }
    params = parameters or {
        "repository": "https://github.com/acme/widgets.git",
        "publishMode": "pr",
        "requiredCapabilities": ["gh", "git"],
    }
    payload = dict(
        agentKind="external",
        agentId="omnigent",
        correlationId=_WF,
        idempotencyKey="idem-1",
        inputRefs=["artifact://in1"],
        workspaceSpec=spec,
        parameters=params,
    )
    payload.update(overrides)
    return AgentExecutionRequest(**payload)


def _compile(request, **overrides):
    workspace_intent = compile_workspace_intent(
        request,
        workflow_id=_WF,
        step_execution_id=_SE,
        run_id="run-1",
        logical_step_id="step-1",
    )
    kwargs = dict(
        workspace_intent=workspace_intent,
        effective_launch=_effective_launch(),
        provider_runtime="codex_cli",
        provider_profile_id="pp-1",
        workflow_id=_WF,
        step_execution_id=_SE,
        run_id="run-1",
        logical_step_id="step-1",
    )
    kwargs.update(overrides)
    return compile_execution_intent(request, **kwargs)


def test_compiles_full_execution_intent() -> None:
    intent = _compile(_request())
    assert intent.schema_id == EXECUTION_INTENT_SCHEMA_ID
    assert intent.runtime.execution_profile_ref == "omnigent-codex@1"
    assert intent.runtime.provider_runtime == "codex_cli"
    assert intent.runtime.harness == "codex-native"
    assert intent.launch.effective_launch_ref == "omnigent-launch:sha256:deadbeef"
    assert intent.launch.host_mode == "on_demand_docker"
    assert intent.workspace.operation_class == "pull_request"
    assert intent.workspace.repository == "https://github.com/acme/widgets.git"
    assert intent.session.session_mode == "fresh"
    assert intent.full_authority_proven is True


def test_create_journey_digests_are_correlated() -> None:
    # The compiled intent binds the exact workspace-intent digest and the exact
    # effective-launch snapshot ref — the digest correlation the normal create
    # journey must prove (AC10).
    request = _request()
    workspace_intent = compile_workspace_intent(
        request, workflow_id=_WF, step_execution_id=_SE
    )
    effective_launch = _effective_launch()
    intent = compile_execution_intent(
        request,
        workspace_intent=workspace_intent,
        effective_launch=effective_launch,
        provider_runtime="codex_cli",
        provider_profile_id="pp-1",
        workflow_id=_WF,
        step_execution_id=_SE,
    )
    assert (
        intent.workspace.workspace_intent_digest == workspace_intent.intent_digest
    )
    assert intent.launch.effective_launch_ref == effective_launch["snapshotRef"]
    assert intent.evidence()["workspaceIntentDigest"] == (
        workspace_intent.intent_digest
    )


def test_equivalent_requests_cannot_race_to_different_intent() -> None:
    # Two submissions authoring the same intent (reordered keys, varied casing)
    # compile to the same immutable digest, so planning and submission cannot
    # disagree about repository/launch authority.
    create = _request()
    resubmit = _request(
        workspace_spec={
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
    assert _compile(create).intent_digest == _compile(resubmit).intent_digest


def test_retry_reproduces_the_same_immutable_intent() -> None:
    request = _request()
    assert _compile(request).intent_digest == _compile(request).intent_digest


def test_read_only_operation_class_when_no_mutation() -> None:
    request = _request(
        parameters={"publishMode": "none", "requiredCapabilities": ["git"]}
    )
    intent = _compile(request)
    assert intent.workspace.operation_class == "read_only"
    assert intent.workspace.no_commit_policy is True


def test_typed_remediation_controller_is_pinned_by_digest() -> None:
    intent = _compile(_request(), remediation_controller=_CONTROLLER)
    assert intent.remediation is not None
    assert intent.remediation.loop_id == "loop-1"
    assert intent.remediation.hard_max_attempts == 5
    assert intent.remediation.verifier_owner == "verifier"
    assert intent.remediation.controller_digest.startswith("sha256:")
    # Provenance records the controller as authored typed authority, not a
    # free-form annotation.
    sources = {p.section: p.source for p in intent.provenance}
    assert sources["remediation"] == "authored"


def test_fails_closed_on_missing_launch_authority() -> None:
    with pytest.raises(ExecutionIntentCompilationError) as excinfo:
        _compile(_request(), effective_launch=_effective_launch(snapshotRef=""))
    assert excinfo.value.code == EXECUTION_INTENT_INCOMPLETE_AUTHORITY


def test_fails_closed_on_provider_profile_conflict() -> None:
    # Runtime code must never silently reconcile a different provider profile
    # than the run was admitted against.
    with pytest.raises(ExecutionIntentCompilationError) as excinfo:
        _compile(
            _request(),
            effective_launch=_effective_launch(providerProfileId="pp-OTHER"),
        )
    assert excinfo.value.code == EXECUTION_INTENT_INCOMPLETE_AUTHORITY


def test_fails_closed_on_non_omnigent_runtime_identity() -> None:
    request = _request(agentKind="managed", agentId="codex-managed")
    with pytest.raises(ExecutionIntentCompilationError):
        _compile(request)


# --- Migration adapter + incident replays ------------------------------------


def test_migration_adapter_marks_legacy_and_unproven_authority() -> None:
    request = _request(
        parameters={
            "repository": "https://github.com/acme/widgets.git",
            "publishMode": "pr",
            "requiredCapabilities": ["gh", "git"],
            "remediationLoop": _CONTROLLER,
            "remediationLoopId": "loop-1",
        }
    )
    intent = derive_execution_intent_from_request(
        request,
        effective_launch=_effective_launch(),
        provider_runtime="codex_cli",
        provider_profile_id="pp-1",
        workflow_id=_WF,
        step_execution_id=_SE,
    )
    assert intent.full_authority_proven is False
    assert intent.remediation is not None and intent.remediation.loop_id == "loop-1"
    sources = {p.section: p.source for p in intent.provenance}
    assert sources["runtime"] == "legacy_derived"
    assert sources["remediation"] == "legacy_derived"
    # The workspace subset is always compiled by the canonical compiler.
    assert sources["workspace"] == "resolved"


def test_incident_3684_stripped_remediation_annotation_fails_closed() -> None:
    # #3684: the dashboard stripped ``annotations.remediationLoop`` before
    # submission, so Temporal never initialized the controller. With the typed
    # contract, a run that still declares a loop id but lost its controller
    # mapping is rejected at admission instead of silently proceeding.
    request = _request(
        parameters={
            "repository": "https://github.com/acme/widgets.git",
            "publishMode": "pr",
            "requiredCapabilities": ["gh", "git"],
            "remediationLoopId": "loop-1",
            # remediationLoop controller mapping intentionally absent (stripped).
        }
    )
    with pytest.raises(ExecutionIntentCompilationError) as excinfo:
        derive_execution_intent_from_request(
            request,
            effective_launch=_effective_launch(),
            provider_runtime="codex_cli",
            provider_profile_id="pp-1",
            workflow_id=_WF,
            step_execution_id=_SE,
        )
    assert excinfo.value.code == EXECUTION_INTENT_INCOMPLETE_AUTHORITY


def test_incident_image_policy_drift_changes_admitted_digest() -> None:
    # Image policy vs running deployment drift: a changed launch snapshot ref
    # yields a different admitted intent digest, so drift cannot masquerade as
    # the previously admitted authority.
    base = _compile(_request())
    drifted = _compile(
        _request(),
        effective_launch=_effective_launch(
            snapshotRef="omnigent-launch:sha256:cafef00d"
        ),
    )
    assert base.intent_digest != drifted.intent_digest


def test_incident_continuation_missing_controller_fails_closed() -> None:
    # A continuation row with incomplete capability/controller authority must not
    # be admitted when it declares it requires a remediation controller.
    request = _request()
    with pytest.raises(ExecutionIntentCompilationError):
        _compile(
            request,
            source_kind="continuation",
            require_remediation=True,
            remediation_controller=None,
        )
