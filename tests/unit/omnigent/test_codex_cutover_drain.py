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
    BRANCH_RUNTIME_BINDINGS,
    DEPLOYMENT_CUTOFF_ENV,
    DRAIN_INVENTORY_CATEGORIES,
    RETAINED_BRANCHES,
    assert_new_admission_allowed,
    build_drain_report,
    collect_drain_inventory,
    cutover_authorities,
    direct_retired_by_cutoff,
    generic_codex_promotion_permitted,
    require_exact_generic_support,
    resolve_linked_generic_qualification,
    retained_disposition,
    verify_retained_branch_runtime,
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


def _linked_env(selection, **overrides):
    import json

    env = {
        "MOONMIND_OMNIGENT_GENERIC_CODEX_QUALIFIED": "true",
        "MOONMIND_CODEX_GENERIC_QUALIFICATION_REF": "artifacts/qualification/generic-codex.json",
        "MOONMIND_CODEX_GENERIC_QUALIFICATION_DIGEST": "b" * 64,
        "MOONMIND_CODEX_GENERIC_QUALIFICATION_RESULT": "passed",
        "MOONMIND_CODEX_GENERIC_QUALIFICATION_DIMENSIONS": json.dumps(
            {
                "image": selection["image"],
                "runtime_pack": selection["runtime_pack"],
                "materializer": selection["materializer"],
                "ownership_mode": selection["ownership_mode"],
                "capabilities": list(selection["capabilities"]),
            }
        ),
    }
    env.update(overrides)
    return env


def test_linked_qualification_unconfigured_defers_to_boolean_gate():
    assert resolve_linked_generic_qualification({}) is None
    assert resolve_linked_generic_qualification(
        {"MOONMIND_OMNIGENT_GENERIC_CODEX_QUALIFIED": "true"}
    ) is None


def test_linked_qualification_rejects_bad_digest():
    selection = _selection()
    env = _linked_env(selection, MOONMIND_CODEX_GENERIC_QUALIFICATION_DIGEST="zz")
    with pytest.raises(ValueError, match="linked_qualification_digest_required"):
        resolve_linked_generic_qualification(env)


def test_generic_promotion_requires_linked_evidence_for_new_defaults():
    selection = _selection()
    # No linked evidence declared: preserve the existing boolean promotion so
    # the usable path is not removed before its qualified replacement exists.
    assert (
        generic_codex_promotion_permitted(
            selection, {"MOONMIND_OMNIGENT_GENERIC_CODEX_QUALIFIED": "true"}
        )
        is True
    )
    # Linked exact evidence promotes.
    assert (
        generic_codex_promotion_permitted(selection, _linked_env(selection)) is True
    )
    # Dimension mismatch never falls back to another runtime/path.
    bad = _linked_env(selection)
    import json

    dims = json.loads(bad["MOONMIND_CODEX_GENERIC_QUALIFICATION_DIMENSIONS"])
    dims["materializer"] = "different-materializer@9"
    bad["MOONMIND_CODEX_GENERIC_QUALIFICATION_DIMENSIONS"] = json.dumps(dims)
    assert generic_codex_promotion_permitted(selection, bad) is False
    # Declared linked evidence with no verifiable selection fails closed.
    assert generic_codex_promotion_permitted(None, _linked_env(selection)) is False
    # Boolean false stays disabled even with linked evidence present.
    env = _linked_env(selection)
    env["MOONMIND_OMNIGENT_GENERIC_CODEX_QUALIFIED"] = "false"
    assert generic_codex_promotion_permitted(selection, env) is False


def test_collect_drain_inventory_maps_source_failure_to_unknown():
    def ok():
        return [{"id": "s-1", "state": "clean"}]

    def boom():
        raise RuntimeError("visibility unreachable")

    inventory = collect_drain_inventory({"schedules": ok, "queued_starts": boom})
    assert inventory["schedules"] == [{"id": "s-1", "state": "clean"}]
    assert inventory["queued_starts"][0]["state"] == "unknown"
    # Missing categories stay missing so build_drain_report fails closed.
    report = build_drain_report(inventory)
    assert report.deletable is False
    assert any("drain_inventory_category_missing" in b for b in report.blockers)


def test_retained_branches_bind_to_runtime_mechanisms():
    assert set(BRANCH_RUNTIME_BINDINGS) == {
        branch["branch"] for branch in RETAINED_BRANCHES
    }
    statuses = verify_retained_branch_runtime()
    assert set(statuses) == set(BRANCH_RUNTIME_BINDINGS)
    for branch, status in statuses.items():
        assert status["resolved"] is True, branch
        assert status["target"], branch
    with pytest.raises(ValueError, match="unknown_retained_branch"):
        verify_retained_branch_runtime("no-such-branch")


def test_cutover_authorities_name_single_deployment_cutoff():
    authorities = cutover_authorities()
    assert authorities["deployment_cutoff_env"] == DEPLOYMENT_CUTOFF_ENV
    assert "rollout" in authorities["authorities"]
    assert "retirement" in authorities["authorities"]
    assert "deployment_cutoff" in authorities["authorities"]
    assert authorities["adds_state_machine"] is False


def test_recorded_history_decoding_survives_retired_lane_cutoff():
    from moonmind.omnigent.workspace_sources import decode_legacy_workspace_path

    env = {DEPLOYMENT_CUTOFF_ENV: "2026-01-01T00:00:00Z"}
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="codex_direct_retired_by_deployment_cutoff"):
        assert_new_admission_allowed("codex_cli", env=env, now=now)
    # Already-recorded payloads still decode; the cutoff guards new
    # admission only and never erases historical evidence.
    assert (
        decode_legacy_workspace_path({"workspacePath": "/recorded/path"}) == "/recorded/path"
    )
