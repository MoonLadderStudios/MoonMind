from __future__ import annotations

from types import SimpleNamespace

import pytest

from api_service.services import omnigent_execution_plan_service as service
from api_service.services.omnigent_agent_profile_selection import (
    default_launch_policy_ref,
)
from moonmind.workflows.executions.execution_contract import (
    build_canonical_workflow_view,
)
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
