"""AC6: escaped incidents are generalized invariants and initial scenarios.

MoonLadderStudios/MoonMind#3709.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from moonmind.omnigent.faultkit.corpus import initial_incident_scenarios
from moonmind.omnigent.faultkit.harness import run_scenario
from moonmind.omnigent.faultkit.invariants import INVARIANTS, check_invariants
from moonmind.omnigent.faultkit.scenario import load_scenario

_REPLAY_ROOT = (
    Path(__file__).resolve().parents[3]
    / "integration"
    / "reliability"
    / "replays"
)

_INCIDENTS = initial_incident_scenarios()
_INVARIANT_KEYS = {inv.key for inv in INVARIANTS}

#: The required initial scenarios enumerated in the brief.
_REQUIRED_SOURCES = {
    "MoonLadderStudios/MoonMind#3698",  # missed terminal edge after heartbeat timeout
    "MoonLadderStudios/MoonMind#3683",  # provider idle completion vocabulary
    "MoonLadderStudios/MoonMind#3665",  # stale state rollback
    "MoonLadderStudios/MoonMind#3684",  # remediation annotation/authority loss
    "MoonLadderStudios/MoonMind#3694",  # image authority drift
    "MoonLadderStudios/MoonMind#3697",  # websocket implementation missing
}


def test_required_initial_scenarios_are_present() -> None:
    sources = {inc.source_reference for inc in _INCIDENTS}
    joined = ",".join(sources)
    for required in _REQUIRED_SOURCES:
        assert required in joined
    # scoped UI / duplicate binding (#3696 & #3685)
    assert "MoonLadderStudios/MoonMind#3696" in joined
    assert "MoonLadderStudios/MoonMind#3685" in joined
    # The three behavior-shape scenarios required by the brief.
    slugs = {inc.slug for inc in _INCIDENTS}
    assert "omnigent-fault-first-message-response-lost" in slugs
    assert "omnigent-fault-cleanup-racing-continuation" in slugs
    assert "omnigent-fault-lease-replacement-old-host-alive" in slugs


@pytest.mark.parametrize("incident", _INCIDENTS, ids=lambda inc: inc.slug)
def test_incident_scenario_is_safe_under_correct_reconciler(incident) -> None:
    assert incident.invariant in _INVARIANT_KEYS
    result = run_scenario(incident.scenario)
    violations = check_invariants(result)
    assert violations == [], [f"{v.invariant}: {v.detail}" for v in violations]


@pytest.mark.parametrize("incident", _INCIDENTS, ids=lambda inc: inc.slug)
def test_incident_manifest_on_disk_matches_code_corpus(incident) -> None:
    manifest_path = _REPLAY_ROOT / incident.slug / "manifest.json"
    assert manifest_path.exists(), f"missing corpus fixture {manifest_path}"
    on_disk = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert on_disk == incident.to_manifest()
    # The stored scenario re-parses through the versioned loader.
    reparsed = load_scenario(on_disk["scenario"])
    assert reparsed.steps == incident.scenario.steps


@pytest.mark.parametrize("incident", _INCIDENTS, ids=lambda inc: inc.slug)
def test_incident_ingestion_contract_fields_present(incident) -> None:
    manifest = incident.to_manifest()
    for field in (
        "generalizedInvariant",
        "sourceReference",
        "expectedDecision",
        "expectedClassification",
        "operationalSignal",
        "scenario",
    ):
        assert manifest[field], f"{incident.slug} missing {field}"
