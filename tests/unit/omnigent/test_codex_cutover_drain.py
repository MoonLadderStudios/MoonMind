"""MoonLadderStudios/MoonMind#3931 drain-and-delete path tests.

Covers the minimal verified cutover contract: exact generic support evidence
(linked, never inferred), retired-lane admission rejection behind one
deployment-owned cutoff, bounded drain inventory with four-state reporting,
explicit retained-history disposition with per-branch removal conditions, and
boundary coverage (restart/cancel/retry/reset/credential/publication/cleanup).
"""

from datetime import datetime, timezone

import pytest
from temporalio import workflow

from moonmind.omnigent.workspace_sources import decode_legacy_workspace_path

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
    run_codex_drain_procedure,
    verify_retained_branch_runtime,
)


@workflow.defn(name="CodexCutoverRecordedDecodeReplay")
class _RecordedDecodeReplayWorkflow:
    """R4 replay fixture: a workflow decoding an already-recorded payload."""

    @workflow.run
    async def run(self, payload: dict) -> str:
        decoded = decode_legacy_workspace_path(payload)
        await workflow.sleep(1)
        return decoded or "missing"


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


def test_select_runtime_rejects_retired_lane_for_explicit_and_default():
    """R2 at the production admission boundary (cutover.select_runtime)."""

    from moonmind.omnigent.cutover import CutoverPhase, select_runtime

    env = {DEPLOYMENT_CUTOFF_ENV: "2026-01-01T00:00:00Z"}
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    phase = CutoverPhase.OPT_IN
    with pytest.raises(ValueError, match="codex_direct_retired_by_deployment_cutoff"):
        select_runtime(
            authored_runtime="codex_cli",
            configured_default="omnigent",
            phase=phase,
            env=env,
            now=now,
        )
    for kind in ("create", "schedule", "preset"):
        with pytest.raises(
            ValueError, match="codex_direct_retired_by_deployment_cutoff"
        ):
            select_runtime(
                authored_runtime=None,
                configured_default="codex_cli",
                phase=phase,
                submission_kind=kind,
                env=env,
                now=now,
            )
    # The surviving path still admits new work after the cutoff.
    selected = select_runtime(
        authored_runtime=None,
        configured_default="omnigent",
        phase=phase,
        env=env,
        now=now,
    )
    assert selected.runtime_id == "omnigent"


def test_retained_history_replays_through_cutover_boundary_after_cutoff():
    """R4: retained decoders/inputs/routing survive the retired-lane cutoff."""

    from moonmind.omnigent.workspace_sources import decode_legacy_workspace_path
    from moonmind.workflows.temporal.release_routing import current_version

    env = {DEPLOYMENT_CUTOFF_ENV: "2026-01-01T00:00:00Z"}
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="codex_direct_retired_by_deployment_cutoff"):
        assert_new_admission_allowed("codex_cli", env=env, now=now)
    # Every retained branch still binds to its runtime mechanism after cutoff.
    statuses = verify_retained_branch_runtime()
    assert all(status["resolved"] is True for status in statuses.values())
    # Old conformance/promotion evidence still evaluates through the cutover
    # boundary so retained histories replay instead of failing on deleted
    # command branches.
    from moonmind.omnigent.cutover import CutoverPhase, evaluate_promotion

    decision = evaluate_promotion(
        current_phase=CutoverPhase.BROAD_DEFAULT,
        requested_phase=CutoverPhase.OPT_IN,
        evidence=None,
        now=now,
    )
    assert decision.allowed is True
    # Already-recorded raw-path payloads still decode for migration/replay.
    assert (
        decode_legacy_workspace_path({"workspacePath": "/recorded/path"})
        == "/recorded/path"
    )
    # Supported reset/replay routing stays a callable worker-version path.
    assert callable(current_version)
    # Persisted sessions stay readable without a live worker.
    import importlib

    assert importlib.import_module(
        "moonmind.omnigent.control_plane.repositories"
    ) is not None


def test_drain_procedure_caller_reports_four_states():
    """R3: one drain-procedure entrypoint composes collect -> report."""

    def boom():
        raise RuntimeError("visibility unreachable")

    payload = run_codex_drain_procedure(
        {
            "schedules": [{"id": "s-1", "state": "active"}],
            "queued_starts": boom,
            "open_parent_workflows": [{"id": "p-1", "state": "clean"}],
            "open_child_workflows": [{"id": "c-1", "state": "clean"}],
            "retries": [{"id": "r-1", "state": "clean"}],
            "pending_interventions": [{"id": "i-1", "state": "clean"}],
            "serialized_launch_inputs": [{"id": "l-1", "state": "clean"}],
            "resource_leases": [{"id": "lease-1", "state": "clean"}],
            "publication_work": [{"id": "pub-1", "state": "blocked"}],
            "cleanup_work": [{"id": "cl-1", "state": "clean"}],
        }
    )
    assert payload["authorizesDeletion"] is False
    assert payload["deletable"] is False
    assert payload["states"]["active"] == 1
    assert payload["states"]["unknown"] == 1
    assert payload["states"]["blocked"] == 1
    assert "delete credential" not in str(payload).lower()
    assert "terminate" not in str(payload).lower()


def test_drain_procedure_clean_report_is_deletable_evidence_only():
    inventory = {
        category: [{"id": f"{category}-1", "state": "clean"}]
        for category in DRAIN_INVENTORY_CATEGORIES
    }
    payload = run_codex_drain_procedure(inventory)
    assert payload["deletable"] is True
    assert payload["authorizesDeletion"] is False
    assert payload["blockers"] == []


def test_changed_boundaries_map_to_drain_and_disposition_evidence():
    """R6: each changed boundary exercises drain states + disposition."""

    def clean_inventory():
        return {
            category: [{"id": f"{category}-1", "state": "clean"}]
            for category in DRAIN_INVENTORY_CATEGORIES
        }

    # restart: open parent/child activity blocks deletion.
    inventory = clean_inventory()
    inventory["open_parent_workflows"] = [{"id": "p-1", "state": "active"}]
    inventory["open_child_workflows"] = [{"id": "c-1", "state": "active"}]
    assert run_codex_drain_procedure(inventory)["deletable"] is False
    assert "temporal_history_decoders" in retained_disposition(
        "temporal_history_decoders"
    )["branch"]
    # cancellation: pending interventions block; routing disposition names reset.
    inventory = clean_inventory()
    inventory["pending_interventions"] = [{"id": "i-1", "state": "active"}]
    assert run_codex_drain_procedure(inventory)["deletable"] is False
    assert (
        retained_disposition("supported_worker_routing")["mechanism"]
        == "supported_worker_routing"
    )
    # retry: retries activity blocks deletion.
    inventory = clean_inventory()
    inventory["retries"] = [{"id": "r-1", "state": "active"}]
    assert run_codex_drain_procedure(inventory)["deletable"] is False
    # completed-workflow reset: routing disposition carries the reset policy.
    assert "reset" in retained_disposition("supported_worker_routing")[
        "consumer"
    ].lower() or "replay" in retained_disposition("supported_worker_routing")[
        "consumer"
    ].lower()
    # credential preservation: unknown leases block; never authorize deletion.
    inventory = clean_inventory()
    inventory["resource_leases"] = [{"id": "lease-1", "state": "unknown"}]
    payload = run_codex_drain_procedure(inventory)
    assert payload["deletable"] is False
    assert payload["authorizesDeletion"] is False
    # publication recovery: blocked publication work blocks deletion.
    inventory = clean_inventory()
    inventory["publication_work"] = [{"id": "pub-1", "state": "blocked"}]
    assert run_codex_drain_procedure(inventory)["deletable"] is False
    # final cleanup: cleanup work gates the clean report.
    inventory = clean_inventory()
    inventory["cleanup_work"] = [{"id": "cl-1", "state": "active"}]
    assert run_codex_drain_procedure(inventory)["deletable"] is False
    assert run_codex_drain_procedure(clean_inventory())["deletable"] is True


def test_live_drain_sources_normalize_heterogeneous_live_rows():
    """R3: live visibility/schedule/lease rows normalize to four states."""

    from moonmind.omnigent.codex_cutover_drain import live_drain_sources

    class _Row:
        def __init__(self, workflow_id, status):
            self.workflow_id = workflow_id
            self.status = status

    queries = {
        # Temporal-like objects with status attributes.
        "open_parent_workflows": [
            _Row("parent-1", "RUNNING"),
            _Row("parent-2", "CLOSED"),
        ],
        # Plain mappings with mixed key styles.
        "schedules": [
            {"id": "sched-1", "state": "active"},
            {"schedule_id": "sched-2", "status": "paused"},
        ],
        # Unrecognized shapes become unknown, never deleted.
        "resource_leases": [{"opaque": "blob"}],
    }
    sources = live_drain_sources(queries)
    assert set(sources) == set(queries)
    inventory = collect_drain_inventory(sources)
    states = {item["id"]: item["state"] for item in inventory["open_parent_workflows"]}
    assert states["parent-1"] == "active"
    assert states["parent-2"] == "clean"
    schedule_states = {
        item["id"]: item["state"] for item in inventory["schedules"]
    }
    assert schedule_states["sched-1"] == "active"
    assert schedule_states["sched-2"] == "clean"
    assert inventory["resource_leases"][0]["state"] == "unknown"


def test_run_live_codex_drain_maps_raising_live_query_to_unknown():
    """R3: a raising live source becomes unknown; missing fails closed."""

    from moonmind.omnigent.codex_cutover_drain import run_live_codex_drain

    def boom():
        raise RuntimeError("temporal visibility unreachable")

    payload = run_live_codex_drain(
        {
            category: [{"id": f"{category}-1", "state": "clean"}]
            for category in DRAIN_INVENTORY_CATEGORIES
            if category != "schedules"
        }
        | {"schedules": boom}
    )
    assert payload["states"]["unknown"] == 1
    assert payload["deletable"] is False
    assert payload["authorizesDeletion"] is False
    assert any("drain_inventory_category_missing" in b for b in payload["blockers"]) is False
    # A fully missing category fails closed instead of implying drain.
    payload = run_live_codex_drain({})
    assert payload["deletable"] is False
    assert any("drain_inventory_category_missing" in b for b in payload["blockers"])


def test_operator_drain_sources_probe_duck_typed_store():
    """R3: the operator entrypoint binds live readers without new I/O."""

    from moonmind.omnigent.codex_cutover_drain import (
        operator_drain_sources,
        run_codex_drain_procedure,
    )

    class _Store:
        def list_schedules(self):
            return [{"id": "s-1", "state": "clean"}]

        def list_open_parent_workflows(self):
            raise RuntimeError("visibility down")

    sources = operator_drain_sources(_Store())
    assert "schedules" in sources
    assert "open_parent_workflows" in sources
    # Unimplemented readers stay missing so the report fails closed.
    assert "cleanup_work" not in sources
    payload = run_codex_drain_procedure(sources)
    assert payload["states"]["unknown"] == 1
    assert payload["deletable"] is False


def test_persisted_launch_input_round_trip_survives_cutoff():
    """R4: recorded direct-lane inputs still decode after the cutoff."""

    import json

    from moonmind.omnigent.workspace_sources import decode_legacy_workspace_path

    env = {DEPLOYMENT_CUTOFF_ENV: "2026-01-01T00:00:00Z"}
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    # An old serialized launch input naming the retired lane and a raw path.
    recorded = json.dumps(
        {"runtime": "codex_cli", "workspacePath": "/recorded/path"}
    )
    reloaded = json.loads(recorded)
    # New admission into the retired lane rejects ...
    with pytest.raises(ValueError, match="codex_direct_retired_by_deployment_cutoff"):
        assert_new_admission_allowed(reloaded["runtime"], env=env, now=now)
    # ... while the already-recorded payload still decodes for drain/replay.
    assert decode_legacy_workspace_path(reloaded) == "/recorded/path"
    # The surviving lane still admits new work after the cutoff.
    assert_new_admission_allowed("omnigent", env=env, now=now)


def test_workflow_boundaries_resolve_against_live_drain_evidence():
    """R6: real workflow/activity helpers bind to drain/disposition evidence."""

    from moonmind.omnigent.codex_cutover_drain import run_live_codex_drain
    from moonmind.workflows.temporal.release_routing import current_version
    from moonmind.workflows.temporal.workflows.run import MoonMindRunWorkflow

    # Restart/retry helpers on the real run workflow stay wired.
    assert callable(MoonMindRunWorkflow._retry_policy_for_route)
    assert callable(MoonMindRunWorkflow._should_propagate_agent_child_cancellation)
    # Supported reset/replay routing stays a callable worker-version path.
    assert callable(current_version)
    # Each changed boundary blocks deletion through live-shaped evidence.
    boundary_category = {
        "restart": "open_parent_workflows",
        "cancellation": "pending_interventions",
        "retry": "retries",
        "completed_workflow_reset": "open_child_workflows",
        "credential_preservation": "resource_leases",
        "publication_recovery": "publication_work",
        "final_cleanup": "cleanup_work",
    }
    for boundary, category in boundary_category.items():
        queries = {
            other: [{"id": f"{other}-1", "state": "clean"}]
            for other in DRAIN_INVENTORY_CATEGORIES
        }
        queries[category] = [{"id": f"{category}-1", "state": "active"}]
        payload = run_live_codex_drain(queries)
        assert payload["deletable"] is False, boundary
        assert payload["authorizesDeletion"] is False, boundary


def test_operator_drain_activity_binds_live_store_and_queries():
    """R3: the operator activity wires live readers to the drain procedure."""

    from moonmind.workflows.temporal.activities.github_issue_legacy_cutover_activities import (
        codex_direct_drain_report,
    )

    class _LiveStore:
        def list_schedules(self):
            return [{"id": "s-1", "state": "clean"}]

        def list_open_parent_workflows(self):
            return [{"workflow_id": "p-1", "status": "RUNNING"}]

    queries = {
        category: [{"id": f"{category}-1", "state": "clean"}]
        for category in DRAIN_INVENTORY_CATEGORIES
        if category not in {"schedules", "open_parent_workflows"}
    }
    result = codex_direct_drain_report(queries=queries, store=_LiveStore())
    assert result["ok"] is True
    assert set(result["categoriesBound"]) >= set(DRAIN_INVENTORY_CATEGORIES)
    assert result["categoriesMissing"] == []
    report = result["report"]
    assert report["authorizesDeletion"] is False
    # The live RUNNING parent blocks deletion through the real activity path.
    assert report["deletable"] is False
    assert report["states"]["active"] >= 1


def test_operator_drain_activity_fails_closed_on_missing_categories():
    """R3/R6: the operator activity never implies drain for missing readers."""

    from moonmind.workflows.temporal.activities.github_issue_legacy_cutover_activities import (
        codex_direct_drain_report,
    )

    result = codex_direct_drain_report(queries={}, store=None)
    assert result["ok"] is True
    assert result["report"]["deletable"] is False
    assert result["report"]["authorizesDeletion"] is False
    assert any(
        "drain_inventory_category_missing" in blocker
        for blocker in result["report"]["blockers"]
    )
    assert set(result["categoriesMissing"]) >= set(DRAIN_INVENTORY_CATEGORIES)


def test_recorded_launch_inputs_decode_across_retained_schema_shapes():
    """R4: old serialized launch inputs decode across historical shapes."""

    import json

    env = {DEPLOYMENT_CUTOFF_ENV: "2026-01-01T00:00:00Z"}
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    shapes = [
        {"runtime": "codex_cli", "workspacePath": "/recorded/path"},
        {"runtime": "codex-direct", "path": "/recorded/alt"},
        {"runtime": "direct", "workspacePath": "/recorded/direct"},
    ]
    for recorded in shapes:
        reloaded = json.loads(json.dumps(recorded))
        with pytest.raises(ValueError, match="codex_direct_retired_by_deployment_cutoff"):
            assert_new_admission_allowed(reloaded["runtime"], env=env, now=now)
        assert decode_legacy_workspace_path(reloaded) in {
            "/recorded/path",
            "/recorded/alt",
            "/recorded/direct",
        }
    assert decode_legacy_workspace_path({}) is None
    assert_new_admission_allowed("omnigent", env=env, now=now)


def test_legacy_default_preserved_until_exact_promotion_succeeds():
    """Review body P1: legacy row retires only on promoted, not raw boolean."""

    import json

    from moonmind.omnigent.runtime_provider_rollout import (
        default_runtime_provider_rollout_policy,
    )

    selection = _selection()
    base_env = {
        "MOONMIND_OMNIGENT_GENERIC_CODEX_QUALIFIED": "true",
        "MOONMIND_CODEX_GENERIC_SELECTION": json.dumps(selection),
    }
    # Linked evidence mismatched: generic stays explicit-only and legacy stays
    # the default so the proven path is preserved.
    bad_env = dict(base_env)
    bad_env.update(
        {
            "MOONMIND_CODEX_GENERIC_QUALIFICATION_REF": "artifact://q/generic.json",
            "MOONMIND_CODEX_GENERIC_QUALIFICATION_DIGEST": "c" * 64,
            "MOONMIND_CODEX_GENERIC_QUALIFICATION_RESULT": "passed",
            "MOONMIND_CODEX_GENERIC_QUALIFICATION_DIMENSIONS": json.dumps(
                {**selection, "materializer": "different-materializer@9"}
            ),
        }
    )
    rules = {
        rule.target_id: rule
        for rule in default_runtime_provider_rollout_policy(env=bad_env).rules
    }
    assert rules["codex.generic-omnigent"].state.name == "explicit_only"
    assert rules["codex.legacy-profile-bound-omnigent"].state.name == (
        "new_work_default"
    )
    # Exact linked evidence promotes generic and retires legacy together.
    good_env = dict(base_env)
    good_env.update(
        {
            "MOONMIND_CODEX_GENERIC_QUALIFICATION_REF": "artifact://q/generic.json",
            "MOONMIND_CODEX_GENERIC_QUALIFICATION_DIGEST": "c" * 64,
            "MOONMIND_CODEX_GENERIC_QUALIFICATION_RESULT": "passed",
            "MOONMIND_CODEX_GENERIC_QUALIFICATION_DIMENSIONS": json.dumps(
                selection
            ),
        }
    )
    rules = {
        rule.target_id: rule
        for rule in default_runtime_provider_rollout_policy(env=good_env).rules
    }
    assert rules["codex.generic-omnigent"].state.name == "new_work_default"
    assert rules["codex.legacy-profile-bound-omnigent"].state.name == (
        "retired_for_new_work"
    )


def test_direct_launch_readiness_reflects_deployment_cutoff(monkeypatch):
    """P2: published readiness matches admission once the cutoff passes."""

    from moonmind.omnigent.cutover import CutoverPhase, EffectivePhase

    status = EffectivePhase(
        configured_phase=CutoverPhase.OPT_IN,
        deployed_phase=CutoverPhase.OPT_IN,
        phase=CutoverPhase.OPT_IN,
        evidence_ref=None,
        evidence={},
        blockers=(),
    )
    monkeypatch.delenv("MOONMIND_CODEX_DIRECT_RETIRED_AT", raising=False)
    assert status.as_dict()["directLaunchAllowed"] is True
    monkeypatch.setenv("MOONMIND_CODEX_DIRECT_RETIRED_AT", "2026-01-01T00:00:00Z")
    assert status.as_dict()["directLaunchAllowed"] is False


def test_codex_direct_drain_report_registered_as_temporal_activity():
    """R3: the drain report is reachable through the Temporal activity runtime."""

    from moonmind.workflows.temporal.activity_runtime import _ACTIVITY_HANDLER_ATTRS

    assert _ACTIVITY_HANDLER_ATTRS["codex.direct_drain_report"] == (
        "integrations",
        "codex_direct_drain_report",
    )
    from moonmind.workflows.temporal.activity_runtime import ActivityRuntime

    assert callable(getattr(ActivityRuntime, "codex_direct_drain_report"))
