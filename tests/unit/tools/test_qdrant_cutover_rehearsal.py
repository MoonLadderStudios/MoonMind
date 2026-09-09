from __future__ import annotations

import json
from pathlib import Path

from tools import qdrant_cutover_rehearsal as rehearsal


def test_rehearsal_modes_preserve_owner_commands_and_historical_entries() -> None:
    for scenario in rehearsal.REHEARSAL_MODES:
        result = rehearsal.rehearse_scenario(scenario)
        assert result.status == "completed", (scenario, result.evidence)


def test_unknown_scenario_fails_closed() -> None:
    result = rehearsal.rehearse_scenario("qdrant-to-magic")
    assert result.status == "failed"


def test_inventory_survey_names_live_surfaces_without_payload_exports() -> None:
    survey = rehearsal.collect_inventory_survey()
    assert survey["qdrant_image"] == "qdrant/qdrant:v1.17.1"
    # qdrant-storage and moonmind_retrieval_state are distinct data.
    assert survey["qdrant_storage_volume"] is True
    assert survey["retrieval_state_volume"] is True
    assert any("qdrant service" in s for s in survey["qdrant_surfaces"])
    blob = json.dumps(survey)
    assert "payload" not in blob.lower() or "payload-provenance" in blob


def test_preflight_hermetic_default_is_blocked_not_a_guess() -> None:
    survey = rehearsal.collect_inventory_survey()
    verdict = rehearsal.evaluate_preflight(survey)
    assert verdict.status == "blocked"
    assert "operator preflight" in verdict.evidence


def test_preflight_explicit_live_blocker_is_blocked() -> None:
    survey = rehearsal.collect_inventory_survey()
    verdict = rehearsal.evaluate_preflight(
        survey, live_blockers=("active workflow wf-1 unverified",)
    )
    assert verdict.status == "blocked"
    assert "wf-1" in verdict.evidence


def test_preflight_pending_consumers_require_drain() -> None:
    survey = rehearsal.collect_inventory_survey()
    verdict = rehearsal.evaluate_preflight(
        survey, pending_consumers=("schedule sched-1", "activity act-1")
    )
    assert verdict.status == "drain"
    assert "sched-1" in verdict.evidence


def test_drainage_decision_records_finite_retirement_condition() -> None:
    result = rehearsal.document_drainage_decision(("schedule sched-9",))
    assert result.status == "completed"
    assert "drainage-on-old-release" in result.evidence
    assert "no active schedule produces vector work" in result.evidence
    assert "sched-9" in result.evidence


def test_vector_drain_blocks_in_flight_and_preserves_workflows() -> None:
    assert rehearsal.check_vector_drain().status == "completed"
    tracker = rehearsal.VectorDrainTracker()
    tracker.admit("act-1", "wf-1")
    assert tracker.drain().status == "blocked"
    tracker.settle("act-1")
    drained = tracker.drain()
    assert drained.status == "completed"
    assert "wf-1" in tracker.admitted_workflows


def test_history_rewrite_is_detected() -> None:
    before = [{"workflow_id": "wf-1", "owner_id": "o1", "commands": ["start", "poll"]}]
    after = [{"workflow_id": "wf-1", "owner_id": "o2", "commands": ["start", "poll"]}]
    assert rehearsal.check_workflow_history_authority(before, after).status == "failed"


def test_history_identity_set_change_is_detected() -> None:
    before = [{"workflow_id": "wf-1", "owner_id": "o1", "commands": ["start"]}]
    after = [{"workflow_id": "wf-2", "owner_id": "o1", "commands": ["start"]}]
    assert rehearsal.check_workflow_history_authority(before, after).status == "failed"


def test_history_reordered_by_id_still_passes() -> None:
    before = [
        {"workflow_id": "wf-1", "owner_id": "o1", "commands": ["start"]},
        {"workflow_id": "wf-2", "owner_id": "o2", "commands": ["start", "poll"]},
    ]
    after = list(reversed([dict(r) for r in before]))
    assert rehearsal.check_workflow_history_authority(before, after).status == "completed"


def _manifest_records() -> list[dict]:
    base = {"workflow_id": "wf-1", "owner_id": "o1", "commands": ["start"]}
    return [{**base, "entry": e} for e in rehearsal.HISTORICAL_MANIFEST_ENTRIES]


def test_manifest_boundary_change_without_historical_entries_fails() -> None:
    before = _manifest_records()
    after = [dict(r) for r in before if r["entry"] != rehearsal.HISTORICAL_MANIFEST_ENTRIES[1]]
    result = rehearsal.check_manifest_boundary_replay(before, after, boundary_changed=True)
    assert result.status == "failed"


def test_manifest_unchanged_boundary_completes() -> None:
    records = _manifest_records()
    result = rehearsal.check_manifest_boundary_replay(
        records, [dict(r) for r in records], boundary_changed=False
    )
    assert result.status == "completed"


def test_handoff_unknown_step_raises() -> None:
    try:
        rehearsal.run_handoff_sequence(fail_at="nope")
    except ValueError:
        pass
    else:
        raise AssertionError("unknown handoff must raise")


def test_handoff_restart_retry_cancel_contained() -> None:
    assert rehearsal.check_handoff_restart_retry_cancel().status == "completed"


def _snapshot_entries() -> dict[str, str]:
    return {
        "vector:collection-provenance": "qdrant-only:collection-kobi",
        "vector:payload-provenance": "derived:source-documents",
        "export:collection-provenance": "operator-storage-ref/sanitized",
        "config:previous-image": "qdrant/qdrant:v1.17.1",
        "config:recovery-config": "sanitized-recovery-config",
        "mounts:qdrant-storage": "sanitized-mount-ref",
    }


def test_snapshot_envelope_requires_all_scopes() -> None:
    try:
        rehearsal.create_snapshot_envelope({"vector:x": "y"}, "snap-1")
    except ValueError:
        pass
    else:
        raise AssertionError("missing scopes must raise")


def test_snapshot_envelope_restore_verifies() -> None:
    entries = _snapshot_entries()
    envelope = rehearsal.create_snapshot_envelope(entries, "snap-1")
    assert rehearsal.verify_snapshot_envelope(envelope, entries)
    tampered = dict(entries)
    tampered["config:previous-image"] = "qdrant/qdrant:v9.9.9"
    assert not rehearsal.verify_snapshot_envelope(envelope, tampered)


def test_payload_classification_never_assumes_reconstructibility() -> None:
    assert rehearsal.classify_payload("qdrant-only:collection-kobi") == "unique"
    assert rehearsal.classify_payload("unknown-blob") == "unique"
    assert rehearsal.classify_payload("") == "unique"
    assert rehearsal.classify_payload("derived:source-documents") == "reproducible"


def test_recoverability_requires_export_for_unique_payload() -> None:
    entries = _snapshot_entries()
    envelope = rehearsal.create_snapshot_envelope(entries, "snap-1")
    assert rehearsal.verify_recoverability(envelope, entries)
    without_export = {k: v for k, v in entries.items() if not k.startswith("export:")}
    envelope2 = rehearsal.create_snapshot_envelope(without_export, "snap-2")
    assert not rehearsal.verify_recoverability(envelope2, without_export)


def test_snapshot_export_recoverability_guard_passes() -> None:
    assert rehearsal.check_snapshot_export_recoverability().status == "completed"


def test_operator_storage_refuses_public_issues_and_source_control() -> None:
    assert (
        rehearsal.check_operator_storage_path("https://github.com/org/repo/issues/1").status
        == "failed"
    )
    assert rehearsal.check_operator_storage_path("docs/tmp/snapshot.json").status == "failed"


def test_operator_storage_requires_redaction_and_retention() -> None:
    assert rehearsal.check_operator_storage_path("operator-vault://snapshots/qdrant").status in (
        "blocked",
    )
    assert (
        rehearsal.check_operator_storage_path(
            "operator-vault://redacted/qdrant-snapshot/retention-90d"
        ).status
        == "completed"
    )


def test_build_pins_collectible_in_checkout() -> None:
    assert rehearsal.check_build_pins().status == "completed"


def test_retirement_refuses_broad_down_v_and_prune() -> None:
    assert rehearsal.check_retirement_plan(["docker compose down -v"]).status == "failed"
    assert rehearsal.check_retirement_plan(["docker volume prune -f"]).status == "failed"
    assert (
        rehearsal.check_retirement_plan(["docker compose up --remove-orphans"]).status
        == "failed"
    )


def test_retirement_refuses_broad_volume_deletion() -> None:
    assert (
        rehearsal.check_retirement_plan(["delete volume qdrant-storage"]).status == "failed"
    )


def test_retirement_blocked_without_identified_container() -> None:
    assert rehearsal.check_retirement_plan(["rotate unrelated credentials"]).status == "blocked"


def test_retirement_refuses_ambiguous_ownership() -> None:
    result = rehearsal.check_retirement_plan(["stop container qdrant-1"])
    assert result.status == "blocked"
    assert "moonmind" in result.evidence


def test_retirement_accepts_exact_container_under_project() -> None:
    result = rehearsal.check_retirement_plan(
        ["stop container moonmind-qdrant-1 (project moonmind, orphan verification)"]
    )
    assert result.status == "completed"


def test_exact_retirement_refuses_wrong_project() -> None:
    result = rehearsal.verify_exact_retirement(
        expected_project="moonmind", observed_project="other", container_absent=True
    )
    assert result.status == "failed"


def test_exact_retirement_blocked_while_present_and_completes_when_absent() -> None:
    assert (
        rehearsal.verify_exact_retirement(
            expected_project="moonmind",
            observed_project="moonmind",
            container_absent=False,
        ).status
        == "blocked"
    )
    assert (
        rehearsal.verify_exact_retirement(
            expected_project="moonmind",
            observed_project="moonmind",
            container_absent=True,
        ).status
        == "completed"
    )


def test_retention_refuses_automatic_deletion() -> None:
    result = rehearsal.check_retention_plan(
        ["automatically delete qdrant-storage volume as a normal-upgrade step"]
    )
    assert result.status == "failed"


def test_retention_requires_explicit_window() -> None:
    assert rehearsal.check_retention_plan(["proceed with upgrade"]).status == "blocked"


def test_retention_deletion_without_verification_stays_blocked() -> None:
    assert (
        rehearsal.check_retention_plan(["delete qdrant-storage volume now"]).status
        == "blocked"
    )


def test_retention_accepts_windowed_preservation() -> None:
    result = rehearsal.check_retention_plan(
        ["retain qdrant-storage volume and exports through 90d recovery window"]
    )
    assert result.status == "completed"


def test_rollback_refuses_qdrant_profile_or_adapter() -> None:
    assert (
        rehearsal.check_rollback_plan("restore with optional qdrant profile").status
        == "failed"
    )
    assert (
        rehearsal.check_rollback_plan("add qdrant adapter for rollback").status == "failed"
    )


def test_rollback_refuses_whole_database_restore() -> None:
    assert (
        rehearsal.check_rollback_plan("restore whole postgres database snapshot").status
        == "failed"
    )


def test_rollback_unstructured_scope_stays_blocked() -> None:
    assert rehearsal.check_rollback_plan("restore snapshot").status == "blocked"


def test_rollback_matching_revision_with_compat_completes() -> None:
    result = rehearsal.check_rollback_plan(
        "previous matching app/compose/image revision with preserved data "
        "after schema compatibility check with reconciliation"
    )
    assert result.status == "completed"


def test_rollback_rehearsal_completes() -> None:
    assert rehearsal.check_rollback_rehearsal().status == "completed"


def test_interrupted_restart_reconciles_same_migration(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    ok1, _ = rehearsal.save_state(state_dir, "mig-1", ["rehearsal-fresh-vector-free"])
    ok2, _ = rehearsal.save_state(state_dir, "mig-1", ["rehearsal-fresh-vector-free"])
    assert ok1 and ok2
    payload = json.loads((state_dir / rehearsal.STATE_FILE_NAME).read_text())
    assert payload["migration_id"] == "mig-1"
    assert payload["runs"] == 2
    assert payload["completed_steps"] == ["rehearsal-fresh-vector-free"]


def test_stale_concurrent_migration_refused_without_allow_replace(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    assert rehearsal.save_state(state_dir, "mig-1", ["a"])[0]
    ok, msg = rehearsal.save_state(state_dir, "mig-2", ["b"])
    assert not ok
    assert "stale/concurrent" in msg


def test_corrupt_state_fails_closed_without_overwrite(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / rehearsal.STATE_FILE_NAME).write_text("{corrupt!!!")
    ok, msg = rehearsal.save_state(state_dir, "mig-1", ["a"])
    assert not ok
    assert "corrupt" in msg
    assert (state_dir / rehearsal.STATE_FILE_NAME).read_text() == "{corrupt!!!"


def test_allow_replace_resets_progress_for_new_migration(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    assert rehearsal.save_state(state_dir, "mig-1", ["a"])[0]
    ok, _ = rehearsal.save_state(state_dir, "mig-2", ["b"], allow_replace=True)
    assert ok
    payload = json.loads((state_dir / rehearsal.STATE_FILE_NAME).read_text())
    assert payload["migration_id"] == "mig-2"
    assert payload["completed_steps"] == ["b"]
    assert payload["runs"] == 1


def test_sanitize_redacts_secret_like_assignments() -> None:
    redacted = rehearsal.sanitize("QDRANT_API_KEY=supersecret123 host=qdrant")
    assert "supersecret123" not in redacted
    assert "[redacted]" in redacted


def test_cli_preflight_reports_blocked_verdict_without_failures(tmp_path: Path) -> None:
    import subprocess
    import sys

    repo_root = Path(__file__).resolve().parents[3]
    proc = subprocess.run(
        [
            sys.executable,
            "tools/qdrant_cutover_rehearsal.py",
            "--mode",
            "preflight",
            "--state-dir",
            str(tmp_path / "state"),
            "--migration-id",
            "qdrant-preflight-4115",
        ],
        capture_output=True,
        text=True,
        cwd=repo_root,
    )
    assert proc.returncode == 0, proc.stderr
    summary = json.loads(proc.stdout)
    by_name = {s["name"]: s for s in summary["steps"]}
    assert by_name["plan-precondition"]["status"] == "completed"
    assert by_name["preflight-verdict"]["status"] == "blocked"
    # Sibling handler/field deletion has not landed: stays honestly blocked.
    assert by_name["prerequisite-4106-4109-removal"]["status"] == "blocked"
    # #4105 admission retirement has landed: completed.
    assert by_name["prerequisite-4105-admission"]["status"] == "completed"
