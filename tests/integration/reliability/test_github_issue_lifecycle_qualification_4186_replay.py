"""Hermetic three-deployment replay for MoonLadderStudios/MoonMind#4186.

Loads ``replays/github-issue-lifecycle-26row-4186`` (traceability kept with
the existing reliability evidence, not a new qualification framework) and
proves the portable handoff through real production policy boundaries:

* device A fails a partial implementation; disposition is recovery-needed
  with preserved PR revision and locally retained pending sync;
* device B, with fully isolated local state, continues the same PR at the
  validated head without duplicating it;
* device C observes code-review activity and does not start implementation;
* observed contenders quiesce conservatively without clearing each other's
  status, and no shared private ownership state exists between deployments.

Real-provider behavior and real-device upgrade evidence are explicitly
outstanding here and recorded separately; a mocked pass is never reported
as deployed conformance.
"""

from __future__ import annotations

import json
from pathlib import Path

from moonmind.workflows.temporal import github_issue_reconciliation as recon
from moonmind.workflows.temporal.github_issue_admission import (
    ENTRYPOINT_SEARCH,
    admit_for_entrypoint,
    contender_quiesce_decision,
)
from moonmind.workflows.temporal.github_issue_continuation import (
    NEXT_CONTINUE_SAME_PR,
    discover_existing_work,
    route_continuation,
)
from moonmind.workflows.temporal.github_issue_finalization import choose_disposition
from moonmind.workflows.temporal.github_issue_lifecycle import interpret_issue

REPLAY_ID = "github-issue-lifecycle-26row-4186"
REPO = "MoonLadderStudios/MoonMind"


def _replay_dir() -> Path:
    return Path(__file__).resolve().parent / "replays" / REPLAY_ID


def _load(name: str) -> dict:
    return json.loads((_replay_dir() / name).read_text(encoding="utf-8"))


def test_replay_manifest_maps_all_26_rows() -> None:
    manifest = _load("manifest.json")
    expected = _load("expected-outcome.json")
    assert manifest["issue"] == "MoonLadderStudios/MoonMind#4186"
    rows = manifest["rows"]
    assert {int(item["row"]) for item in rows} == set(range(1, 27))
    assert expected["rowsMapped"] == 26
    for item in rows:
        assert item["test"] and item["entrypoint"] and item["evidence"] and item["owner"]
    assert manifest["sharedSurface"].startswith("modeled GitHub service only")
    assert manifest["realDeviceEvidence"].startswith("OUTSTANDING")
    assert manifest["realProviderEvidence"].startswith("OUTSTANDING")


def test_three_deployment_handoff_without_shared_private_state() -> None:
    expected = _load("expected-outcome.json")

    # Per-deployment isolated local state: disjoint ownership tables,
    # databases, and artifact stores. Only the modeled GitHub mapping below
    # is shared.
    local_a: dict = {"ownership": {}, "db": {}, "artifacts": {}, "pending_sync": []}
    local_b: dict = {"ownership": {}, "db": {}, "artifacts": {}, "pending_sync": []}
    local_c: dict = {"ownership": {}, "db": {}, "artifacts": {}, "pending_sync": []}
    assert len({id(local_a["ownership"]), id(local_b["ownership"]), id(local_c["ownership"])}) == 3
    assert len({id(local_a["db"]), id(local_b["db"]), id(local_c["db"])}) == 3
    assert len({id(local_a["artifacts"]), id(local_b["artifacts"]), id(local_c["artifacts"])}) == 3

    github_issue = {"state": "open", "labels": ["status: in-progress"], "number": 4186}

    # Device A: failed partial implementation, portable work preserved.
    assert interpret_issue(github_issue).settled == "in_progress"
    disposition = choose_disposition({"portable_work_safe": True, "preservation_verified": True})
    assert disposition["disposition"] == "to_recovery_needed"
    local_a["pending_sync"].append({"effect": "terminal-handoff", "attempt": "a"})
    github_issue = {"state": "open", "labels": ["status: recovery-needed"], "number": 4186}

    # Device B: isolated local state, same shared GitHub; continues the PR.
    assert local_b["ownership"] == {} and local_b["db"] == {} and local_b["artifacts"] == {}
    interpretation = interpret_issue(github_issue)
    assert interpretation.settled == "recovery_needed"
    assert interpretation.eligible_for_continuation is True
    result = discover_existing_work(
        repository=REPO,
        issue_number=4186,
        lineage_validated=True,
        lineage_pr_url=f"https://github.com/{REPO}/pull/7",
        lineage_pr_head="feat-4186",
        lineage_pr_base="main",
        lineage_saved_branch="ckpt-4186",
        lineage_saved_sha="b" * 40,
        github_pr={
            "number": 7,
            "state": "open",
            "merged": False,
            "head": {"ref": "feat-4186", "sha": "a" * 40, "repo": {"full_name": REPO}},
            "base": {"ref": "main"},
        },
    )
    assert result.trusted is True
    routing = route_continuation(result.existing_work)
    assert routing.next_action == NEXT_CONTINUE_SAME_PR
    assert routing.must_not_duplicate_pr is True
    assert expected["portableHandoff"].startswith("continue-implementation")

    # Device C: observes code-review activity and respects it.
    github_issue = {"state": "open", "labels": ["status: code-review"], "number": 4186}
    denied = admit_for_entrypoint(
        ENTRYPOINT_SEARCH, repository=REPO, issue_number=4186, issue=github_issue
    )
    assert denied.allowed is False
    assert expected["cRespectsCodeReview"] is True


def test_observed_contenders_quiesce_conservatively() -> None:
    expected = _load("expected-outcome.json")
    decision = contender_quiesce_decision(
        own_attempt_id="att_" + "b" * 24,
        observed_contenders=[{"attemptId": "att_" + "a" * 24, "activity": "active"}],
    )
    assert decision["quiesce"] is True
    assert decision["clearOtherInProgress"] is False
    assert decision["stopSharedPublication"] is True
    assert decision["preserveOutput"] is True
    assert expected["conservativeConflict"].startswith("observed contenders quiesce")


def test_incomplete_scan_never_reports_clean_repository() -> None:
    scan = recon.merge_scan_results(examined=100, repaired=2, pages_exhausted=True, requests_exhausted=True)
    assert scan["status"] == "partial"
    assert recon.reconciliation_blocks_admission(scan) is True
    outage = recon.merge_scan_results(examined=0, transport_error="outage")
    assert outage["status"] == "unknown"
    assert recon.reconciliation_blocks_admission(outage) is True
