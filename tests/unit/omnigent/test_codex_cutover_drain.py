"""MoonLadderStudios/MoonMind#3931 drain-and-delete path tests.

Covers the minimal verified cutover contract: exact generic support evidence
(linked, never inferred), retired-lane admission rejection behind one
deployment-owned cutoff, bounded drain inventory with four-state reporting,
explicit retained-history disposition with per-branch removal conditions, and
boundary coverage (restart/cancel/retry/reset/credential/publication/cleanup).
"""

from datetime import datetime, timezone

import pytest

from moonmind.omnigent.codex_cutover_drain import (
    BOUNDARY_COVERAGE,
    DEPLOYMENT_CUTOFF_ENV,
    DRAIN_INVENTORY_CATEGORIES,
    RETAINED_BRANCHES,
    assert_new_admission_allowed,
    build_drain_report,
    direct_retired_by_cutoff,
    require_exact_generic_support,
    retained_disposition,
)


def _selection(**overrides):
    base = {
        "image": "example/host@sha256:" + "a" * 64,
        "runtime_pack": "codex-native-pack@1",
        "materializer": "generic-omnigent-host@1",
        "ownership_mode": "provider-profile-ledger",
        "capabilities": ["execute", "checkpoint-resume"],
    }
    base.update(overrides)
    return base


def _qualification(selection, **overrides):
    base = {
        "linked_qualification_ref": "artifacts/qualification/generic-codex.json",
        "qualification_digest": "b" * 64,
        "observed_result": "passed",
        "dimensions": {
            "image": selection["image"],
            "runtime_pack": selection["runtime_pack"],
            "materializer": selection["materializer"],
            "ownership_mode": selection["ownership_mode"],
            "capabilities": list(selection["capabilities"]),
        },
    }
    base.update(overrides)
    return base


def test_exact_support_requires_linked_qualification_not_registry():
    selection = _selection()
    with pytest.raises(ValueError, match="linked_qualification_ref_required"):
        require_exact_generic_support(
            selection, {"registry_member": True, "shared_image_build": True}
        )


def test_exact_support_rejects_dimension_mismatch_without_fallback():
    selection = _selection()
    qualification = _qualification(selection)
    qualification["dimensions"] = dict(qualification["dimensions"])
    qualification["dimensions"]["materializer"] = "different-materializer@9"
    with pytest.raises(ValueError, match="support_dimension_mismatch"):
        require_exact_generic_support(selection, qualification)


def test_exact_support_accepts_linked_evidence():
    selection = _selection()
    bound = require_exact_generic_support(selection, _qualification(selection))
    assert bound["image"] == selection["image"]
    assert bound["qualification_ref"] == "artifacts/qualification/generic-codex.json"
    # No fallback: the bound evidence never names an alternate runtime/path.
    assert "fallback" not in bound
    assert "alternate" not in str(bound)


def test_cutoff_rejects_new_direct_work_into_retired_lane():
    env = {DEPLOYMENT_CUTOFF_ENV: "2026-01-01T00:00:00Z"}
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    assert direct_retired_by_cutoff(env=env, now=now) is True
    with pytest.raises(ValueError, match="codex_direct_retired_by_deployment_cutoff"):
        assert_new_admission_allowed("codex_cli", env=env, now=now)
    # The surviving path still admits new work; the cutoff retires one lane.
    assert_new_admission_allowed("omnigent", env=env, now=now)


def test_no_cutoff_preserves_usable_direct_path():
    env = {}
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    assert direct_retired_by_cutoff(env=env, now=now) is False
    assert_new_admission_allowed("codex_cli", env=env, now=now)


def _inventory(state="clean", count=1):
    return {
        category: [
            {"id": f"{category}-{i}", "state": state} for i in range(count)
        ]
        for category in DRAIN_INVENTORY_CATEGORIES
    }


def test_drain_covers_durable_and_resource_authority_categories():
    assert set(DRAIN_INVENTORY_CATEGORIES) >= {
        "schedules",
        "queued_starts",
        "open_parent_workflows",
        "open_child_workflows",
        "retries",
        "pending_interventions",
        "serialized_launch_inputs",
        "resource_leases",
        "publication_work",
        "cleanup_work",
    }
    report = build_drain_report(_inventory("clean"))
    assert report.deletable is True
    assert report.as_dict()["states"]["clean"] == len(DRAIN_INVENTORY_CATEGORIES)


def test_drain_distinguishes_four_states_and_never_auto_deletes():
    inventory = _inventory("clean")
    inventory["schedules"] = [{"id": "s-1", "state": "active"}]
    inventory["resource_leases"] = [{"id": "l-1", "state": "unknown"}]
    inventory["publication_work"] = [{"id": "p-1", "state": "blocked"}]
    report = build_drain_report(inventory)
    assert report.deletable is False
    states = report.as_dict()["states"]
    assert states["active"] == 1
    assert states["unknown"] == 1
    assert states["blocked"] == 1
    # Unknown visibility is not permission to delete; the report performs no
    # termination and never authorizes credential-volume deletion.
    payload = report.as_dict()
    assert payload["authorizesDeletion"] is False
    assert "delete credential" not in str(payload).lower()
    assert "terminate" not in str(payload).lower()


def test_drain_missing_category_fails_closed():
    inventory = _inventory("clean")
    del inventory["schedules"]
    report = build_drain_report(inventory)
    assert report.deletable is False
    assert any("drain_inventory_category_missing" in b for b in report.blockers)


def test_retained_branches_have_consumer_and_removal_condition():
    assert RETAINED_BRANCHES, "retained disposition must name its branches"
    for branch in RETAINED_BRANCHES:
        assert branch["consumer"], branch["branch"]
        assert branch["removal_condition"], branch["branch"]
        assert branch["mechanism"] in {
            "retain_minimum_implementation",
            "supported_worker_routing",
        }
        resolved = retained_disposition(branch["branch"])
        assert resolved["removal_condition"] == branch["removal_condition"]
    with pytest.raises(ValueError, match="unknown_retained_branch"):
        retained_disposition("no-such-branch")


def test_boundary_coverage_names_changed_boundaries():
    assert set(BOUNDARY_COVERAGE) >= {
        "restart",
        "cancellation",
        "retry",
        "completed_workflow_reset",
        "credential_preservation",
        "publication_recovery",
        "final_cleanup",
    }
    for boundary, coverage in BOUNDARY_COVERAGE.items():
        assert coverage, boundary
