from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api_service.api.routers import executions
from moonmind.workflows.executions.omnigent_codex_rollout import (
    sign_codex_cutover_evidence,
)


SIGNING_KEY = "test-only-cutover-evidence-key"


def _flags(*, phase: str = "create_default", valid: bool = True) -> SimpleNamespace:
    evidence = {
        "generation": "v1",
        "matrixVersion": "moonmind.omnigent.codex-support/v1",
        "reportRef": "artifact://conformance/codex-v1.json",
        "reportSha256": "a" * 64,
        "recordedAt": datetime.now(timezone.utc).isoformat(),
        "liveConformancePassed": valid,
        "replayPassed": True,
        "historicalReadsPassed": True,
        "secretViolations": 0,
        "readinessSuccessRatio": .999,
        "terminalHarvestSuccessRatio": .999,
        "cleanupFailureRatio": .001,
        "replayGapRatio": 0,
        "controlDeliverySuccessRatio": .999,
        "artifactCompletenessRatio": .999,
        "checkpointSuccessRatio": .999,
        "remediationSuccessRatio": .999,
        "ragSuccessRatio": .999,
        "janitorFailureRatio": .001,
        "policyDenials": 0,
        "readinessDenials": 0,
        "qualifiedCaseIds": ["create.static.amd64", "create.ondemand.amd64"],
        "hostModes": ["static", "ondemand"],
        "architectures": ["linux/amd64"],
        "imageDigests": ["example.invalid/codex@sha256:" + "b" * 64],
    }
    evidence = sign_codex_cutover_evidence(evidence, signing_key=SIGNING_KEY)
    return SimpleNamespace(
        omnigent_codex_rollout_phase=phase,
        omnigent_codex_rollout_generation="v1",
        omnigent_codex_conformance_evidence_json=json.dumps(evidence),
        omnigent_codex_conformance_signing_key=SIGNING_KEY,
        omnigent_codex_conformance_max_age_hours=168,
    )


def test_automatic_create_is_resolved_and_immutably_stamped(monkeypatch) -> None:
    monkeypatch.setattr(executions.settings, "feature_flags", _flags())

    authored = executions._apply_codex_cutover_admission(
        {
            "targetRuntime": "codex_cli",
            "runtime": {"mode": "codex_cli", "profileId": "codex-oauth"},
            "codexPathSelection": "automatic",
        },
        submission="create",
    )

    assert authored["targetRuntime"] == "omnigent"
    assert authored["runtime"]["mode"] == "omnigent"
    assert authored["codexCutoverDecision"]["selectedPath"] == "omnigent"
    assert authored["codexCutoverDecision"]["evidenceRef"] == (
        "artifact://conformance/codex-v1.json"
    )


def test_explicit_omnigent_fails_closed_without_conformance(monkeypatch) -> None:
    monkeypatch.setattr(executions.settings, "feature_flags", _flags(valid=False))

    with pytest.raises(HTTPException) as caught:
        executions._apply_codex_cutover_admission(
            {"targetRuntime": "omnigent"},
            submission="create",
        )

    assert caught.value.status_code == 409
    assert caught.value.detail["code"] == "conformance_gate_failed"


def test_direct_launch_is_rejected_after_disable(monkeypatch) -> None:
    monkeypatch.setattr(
        executions.settings,
        "feature_flags",
        _flags(phase="direct_disabled"),
    )

    with pytest.raises(HTTPException) as caught:
        executions._apply_codex_cutover_admission(
            {"targetRuntime": "codex_cli"},
            submission="rerun",
        )

    assert caught.value.detail["code"] == "direct_launch_disabled"


def test_configured_codex_default_uses_automatic_rollout(monkeypatch) -> None:
    monkeypatch.setattr(executions.settings, "feature_flags", _flags())
    monkeypatch.setattr(executions.settings.workflow, "default_runtime", "codex_cli")

    authored = executions._apply_codex_cutover_admission(
        {},
        submission="create",
    )

    assert authored["targetRuntime"] == "omnigent"
    assert authored["codexPathSelection"] == "automatic"
    assert authored["codexCutoverDecision"]["reasonCode"] == "rollout_default"


def test_edit_replaces_stale_decision_and_stamps_current_gate(monkeypatch) -> None:
    monkeypatch.setattr(executions.settings, "feature_flags", _flags())

    patch = executions._admit_task_edit_parameters(
        current_parameters={
            "targetRuntime": "codex_cli",
            "codexPathSelection": "automatic",
            "codexCutoverDecision": {"generation": "old"},
        },
        parameters_patch={"goal": "revised"},
        submission="edit",
    )

    assert patch is not None
    assert patch["goal"] == "revised"
    assert patch["targetRuntime"] == "omnigent"
    assert patch["codexCutoverDecision"]["generation"] == "v1"


def test_exact_rerun_is_blocked_when_direct_launch_is_disabled(monkeypatch) -> None:
    monkeypatch.setattr(
        executions.settings,
        "feature_flags",
        _flags(phase="direct_disabled"),
    )

    with pytest.raises(HTTPException) as caught:
        executions._admit_task_edit_parameters(
            current_parameters={"targetRuntime": "codex_cli"},
            parameters_patch=None,
            submission="rerun",
        )

    assert caught.value.detail["code"] == "direct_launch_disabled"
