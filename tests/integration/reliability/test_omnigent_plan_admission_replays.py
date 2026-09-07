from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from api_service.services import omnigent_execution_plan_service as service
from api_service.services.omnigent_agent_profile_selection import (
    default_launch_policy_ref,
)
from moonmind.workflows.executions.execution_contract import (
    build_canonical_workflow_view,
)
from moonmind.workflows.temporal.workflows import run as run_workflow_module
from tests.integration.reliability.helpers import load_replay
from tests.unit.services.test_omnigent_execution_plan_service import (
    _ArtifactService,
    _OPENCODE_ALLOWED_LAUNCH_POLICIES,
    _PlanStore,
    _capture_plan_payload,
    _compile_opencode_plan,
    _protected_support_evidence,
    _write_deployment_evidence,
)


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.reliability_journey,
]

_SERVER_IMAGE_REF = "ghcr.io/omnigent-ai/omnigent-server@sha256:" + "6" * 64


@pytest.mark.parametrize("payload_key", ["task", "workflow"])
async def test_scheduled_skill_snapshot_reaches_agent_launch(
    monkeypatch: pytest.MonkeyPatch,
    payload_key: str,
) -> None:
    """Replay admission of the persisted schedule envelope through dispatch."""

    replay_id = "scheduled-skill-snapshot-authority"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    artifacts = _ArtifactService()
    workflow_intent = {
        **manifest["initialParameters"]["task"],
        "remediation": {"maxAttempts": 3},
    }
    monkeypatch.setattr(
        service,
        "resolve_execution_evidence",
        lambda plan_payload, **_kwargs: (
            _protected_support_evidence(plan_payload),
            "supported",
        ),
    )
    result = await _compile_opencode_plan(
        monkeypatch,
        artifacts=artifacts,
        launch_policy_ref="omnigent-on-demand@1",
        plan_store=_PlanStore(object()),
        extra_parameters={
            "workflow": None,
            payload_key: workflow_intent,
        },
    )
    snapshot = json.loads(artifacts.payloads[result.resolved_skillset_ref])
    assert [entry["skill_name"] for entry in snapshot["skills"]] == expected[
        "selectedSkills"
    ]
    assert (
        result.envelope.payload.authority.remediationPolicyRef
        == service._digest_ref("remediation-policy", workflow_intent["remediation"])
    )

    async def read_snapshot(name, request, **_kwargs):
        assert name == "artifact.read"
        assert request.artifact_ref == result.resolved_skillset_ref
        return snapshot

    async def unexpected_resolution(*_args, **_kwargs):
        pytest.fail("dispatch must retain the immutable admitted snapshot")

    monkeypatch.setattr(run_workflow_module, "execute_typed_activity", read_snapshot)
    monkeypatch.setattr(
        run_workflow_module.workflow, "execute_activity", unexpected_resolution
    )
    monkeypatch.setattr(run_workflow_module.workflow, "patched", lambda _id: True)
    monkeypatch.setattr(
        run_workflow_module.workflow,
        "info",
        lambda: SimpleNamespace(
            workflow_id=manifest["incidentWorkflowId"],
            run_id="replay-run",
            namespace="default",
            search_attributes={},
        ),
    )
    parent = run_workflow_module.MoonMindRunWorkflow()
    parent._owner_id = "user-1"
    parameters = {"resolvedSkillsetRef": result.resolved_skillset_ref}
    for skill in expected["selectedSkills"]:
        inputs = {"selectedSkill": skill, "targetRuntime": "omnigent"}
        ref = await parent._resolve_agent_node_skillset_ref(
            task_skills=None,
            node_inputs=inputs,
            node_id=skill,
            existing_skillset_ref=parent._existing_agent_skillset_ref(
                parameters=parameters,
                node={},
                node_inputs=inputs,
            ),
        )
        request = parent._build_agent_execution_request(
            node_inputs=inputs,
            node_id=skill,
            tool_name="omnigent",
            resolved_skillset_ref=ref,
            workflow_parameters=parameters,
        )
        assert request.resolved_skillset_ref == result.resolved_skillset_ref
        assert skill in parent._resolved_skill_required_capabilities_by_step


@pytest.mark.parametrize("payload_key", ["task", "workflow"])
async def test_schedule_envelope_cannot_bypass_runtime_target_validation(
    monkeypatch: pytest.MonkeyPatch,
    payload_key: str,
) -> None:
    monkeypatch.setattr(
        service,
        "resolve_execution_evidence",
        lambda plan_payload, **_kwargs: (
            _protected_support_evidence(plan_payload),
            "supported",
        ),
    )
    with pytest.raises(ValueError, match="Requested runtime target does not match"):
        await _compile_opencode_plan(
            monkeypatch,
            artifacts=_ArtifactService(),
            launch_policy_ref="omnigent-on-demand@1",
            plan_store=_PlanStore(object()),
            extra_parameters={
                "workflow": None,
                payload_key: {"runtime": {"targetId": "unselected-runtime-target"}},
            },
        )


@pytest.fixture(autouse=True)
def _ready_opencode_image_pair(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replay plan admission with resolver evidence for the selected pair."""

    import os

    from moonmind.omnigent.bootstrap import store

    monkeypatch.setenv("OMNIGENT_IMAGE_REF", _SERVER_IMAGE_REF)

    def load_state():
        host_ref = os.environ.get("OMNIGENT_OPENCODE_HOST_IMAGE_REF", "")
        if not host_ref:
            return None
        return SimpleNamespace(
            server_image_ref=_SERVER_IMAGE_REF,
            opencode_host_image_ref=host_ref,
            details={
                "opencodeHostCompatibility": {
                    "status": "ready",
                    "failureCode": None,
                    "serverImageRef": _SERVER_IMAGE_REF,
                    "hostImageRef": host_ref,
                }
            },
        )

    monkeypatch.setattr(store, "load_resolved_state", load_state)


async def test_omnigent_fanout_capability_is_platform_admitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replay_id = "omnigent-fanout-plan-admission"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")

    async def resolve_skills(**_kwargs):
        return (
            SimpleNamespace(
                skills=[
                    SimpleNamespace(
                        required_capabilities=manifest["resolvedSkill"][
                            "requiredCapabilities"
                        ]
                    )
                ]
            ),
            "art_skill_manifest",
            "sha256:" + "5" * 64,
            (),
        )

    monkeypatch.setattr(service, "_resolve_and_persist_skills", resolve_skills)
    monkeypatch.setattr(
        service,
        "resolve_execution_evidence",
        lambda plan_payload, **_kwargs: (
            _protected_support_evidence(plan_payload),
            "supported",
        ),
    )

    result = await _compile_opencode_plan(
        monkeypatch,
        artifacts=_ArtifactService(),
        launch_policy_ref=manifest["launchPolicyRef"],
        plan_store=_PlanStore(object()),
    )

    assert (
        result.envelope.payload.classAdmissionDecision
        == expected["classAdmissionDecision"]
    )
    assert result.envelope.payload.resolvedTools["tools"] == expected[
        "resolvedTools"
    ]


async def test_batch_github_capabilities_do_not_leak_jira_into_omnigent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replay_id = "omnigent-batch-github-jira-capability-leak"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")

    canonical = build_canonical_workflow_view(
        job_type="task",
        payload=manifest["authoredPayload"],
    )

    assert canonical["requiredCapabilities"] == expected[
        "normalizedRequiredCapabilities"
    ]
    assert set(expected["forbiddenCapabilities"]).isdisjoint(
        canonical["requiredCapabilities"]
    )

    monkeypatch.setattr(
        service,
        "resolve_execution_evidence",
        lambda plan_payload, **_kwargs: (
            _protected_support_evidence(plan_payload),
            "supported",
        ),
    )
    result = await _compile_opencode_plan(
        monkeypatch,
        artifacts=_ArtifactService(),
        launch_policy_ref=manifest["launchPolicyRef"],
        plan_store=_PlanStore(object()),
        extra_parameters={
            "requiredCapabilities": canonical["requiredCapabilities"],
            "workflow": canonical["workflow"],
        },
    )

    assert (
        result.envelope.payload.classAdmissionDecision
        == expected["classAdmissionDecision"]
    )
    assert result.envelope.payload.resolvedTools["tools"] == expected[
        "resolvedTools"
    ]


async def test_default_evidence_admits_opencode_zen_model(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replay_id = "opencode-default-model-evidence-admission"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    monkeypatch.setenv(
        "MOONMIND_OMNIGENT_EVIDENCE_POLICY", manifest["evidencePolicy"]
    )
    monkeypatch.setenv(
        "MOONMIND_DEPLOYMENT_EVIDENCE_KEY_PATH",
        str(tmp_path / "deployment_evidence_key"),
    )
    admitted_policy = default_launch_policy_ref(
        _OPENCODE_ALLOWED_LAUNCH_POLICIES
    )
    assert admitted_policy == manifest["launchPolicyRef"]
    qualified_plan = await _capture_plan_payload(
        launch_policy_ref=admitted_policy
    )
    _write_deployment_evidence(
        tmp_path,
        monkeypatch,
        plan_payload=qualified_plan,
        launch_policy_ref=admitted_policy,
    )

    result = await _compile_opencode_plan(
        monkeypatch,
        artifacts=_ArtifactService(),
        launch_policy_ref=admitted_policy,
        plan_store=_PlanStore(None),
        extra_parameters={
            "model": manifest["requestedModel"],
            "effort": manifest["requestedEffort"],
        },
    )

    payload = result.envelope.payload
    assert payload.admissionAuthority.supportTier == expected["supportTier"]
    assert payload.modelConfig.qualifiedId == manifest["requestedModel"]
    assert (
        payload.supportIdentity.modelConfigDigest
        != qualified_plan.supportIdentity.modelConfigDigest
    ) is expected["modelConfigDigestMayVary"]


async def test_selected_zen_materializer_has_independent_deployment_evidence(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replay_id = "opencode-selected-materializer-evidence-admission"
    manifest = load_replay(replay_id, "manifest.json")
    expected = load_replay(replay_id, "expected-outcome.json")
    monkeypatch.setenv("MOONMIND_OMNIGENT_EVIDENCE_POLICY", manifest["evidencePolicy"])
    monkeypatch.setenv(
        "MOONMIND_DEPLOYMENT_EVIDENCE_KEY_PATH",
        str(tmp_path / "deployment_evidence_key"),
    )
    launch_policy_ref = default_launch_policy_ref(_OPENCODE_ALLOWED_LAUNCH_POLICIES)

    go_plan = await _capture_plan_payload(
        launch_policy_ref=launch_policy_ref,
        provider_id=manifest["defaultProviderId"],
    )
    _write_deployment_evidence(
        tmp_path,
        monkeypatch,
        plan_payload=go_plan,
        launch_policy_ref=launch_policy_ref,
    )
    zen_plan = await _capture_plan_payload(
        launch_policy_ref=launch_policy_ref,
        provider_id=manifest["selectedProviderId"],
    )
    _write_deployment_evidence(
        tmp_path,
        monkeypatch,
        plan_payload=zen_plan,
        launch_policy_ref=launch_policy_ref,
    )

    result = await _compile_opencode_plan(
        monkeypatch,
        artifacts=_ArtifactService(),
        launch_policy_ref=launch_policy_ref,
        plan_store=_PlanStore(None),
        provider_id=manifest["selectedProviderId"],
    )

    payload = result.envelope.payload
    assert payload.admissionAuthority.supportTier == expected["supportTier"]
    assert (
        payload.credentialBindings["primary-model"].materializerRef
        == expected["selectedMaterializerRef"]
    )
