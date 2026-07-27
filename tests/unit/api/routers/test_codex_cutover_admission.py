from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api_service.api.routers import executions


def _flags(*, phase: str = "create_default", valid: bool = True) -> SimpleNamespace:
    evidence = {
        "generation": "v1",
        "matrixVersion": "moonmind.omnigent.codex-support/v1",
        "reportRef": "artifact://conformance/codex-v1.json",
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
    }
    return SimpleNamespace(
        omnigent_codex_rollout_phase=phase,
        omnigent_codex_rollout_generation="v1",
        omnigent_codex_conformance_evidence_json=json.dumps(evidence),
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
