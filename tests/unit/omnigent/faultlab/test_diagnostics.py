"""Diagnostic bundles are reproduction-complete and secret-safe.

Source issue: MoonLadderStudios/MoonMind#3709 (acceptance criteria 9 and 11).
"""

from __future__ import annotations

import pytest

from moonmind.omnigent.faultlab import FaultPlan, run_plan
from moonmind.omnigent.faultlab.diagnostics import (
    SecretLeakError,
    _assert_no_secrets,
    build_diagnostic_bundle,
)
from moonmind.omnigent.faultlab.harness import ObservationFault
from moonmind.omnigent.faultlab.scenario import ResponseBehavior


def test_bundle_contains_reproduction_evidence():
    plan = FaultPlan(
        seed=555,
        submit_response=ResponseBehavior.DROP,
        observation_faults=(ObservationFault.MISSED_EDGE,),
        recovery_round=4,
    )
    trace = run_plan(plan)
    bundle = build_diagnostic_bundle(trace, scenario_id="x", source_ref="#3698")
    data = bundle.to_dict()

    # Seed + declarative scenario reproduce the run without network/credentials.
    assert data["seed"] == 555
    assert data["scenario"]["schemaVersion"].startswith("moonmind.omnigent-fault-scenario")
    # Decision journal and provider request log are present and bounded.
    assert data["decisionJournal"]
    assert data["providerRequestLog"]
    assert all(r["payloadDigest"].startswith("sha256:") for r in data["providerRequestLog"])


def test_bundle_rejects_secret_shaped_content():
    with pytest.raises(SecretLeakError):
        _assert_no_secrets({"note": "token=ghp_abcdefghijklmnopqrstuvwxyz0123456789"})


def test_bundle_from_clean_run_has_no_violations():
    trace = run_plan(FaultPlan())
    bundle = build_diagnostic_bundle(trace)
    assert bundle.invariant_violations == []
    # A clean bundle still round-trips through the secret scan.
    assert bundle.to_dict()["seed"] == 0
