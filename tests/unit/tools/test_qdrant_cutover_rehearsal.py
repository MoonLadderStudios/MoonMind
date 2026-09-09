"""Hermetic cutover gate tests for #4115 (no live deployment mutation)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools import qdrant_cutover_rehearsal as rehearsal

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_runbook_precondition_present_with_required_sections() -> None:
    result = rehearsal.check_runbook_precondition(REPO_ROOT)
    assert result.status == "completed", result.evidence


def test_prerequisites_admission_landed_and_owner_live_blocked() -> None:
    results = {
        r.name: r for r in rehearsal.check_prerequisites(REPO_ROOT)
    }
    assert results["prerequisite-4105-admission"].status == "completed"
    assert results["prerequisite-4106-4109-handler-field-removal"].status == "completed"
    assert results["prerequisite-named-owner-approval"].status == "blocked"
    assert results["prerequisite-live-deployment-qualification"].status == "blocked"
    assert results["prerequisite-4107-retrieval-state-classification"].status == "blocked"


def test_inventory_survey_names_no_live_surfaces_and_preserves_fixtures() -> None:
    survey = rehearsal.collect_inventory_survey(REPO_ROOT)
    assert survey["qdrant_surfaces"] == [], survey["qdrant_surfaces"]
    assert survey["qdrant_image"] == "absent"
    assert "qdrant-storage" in str(survey.get("qdrant_storage_volume", ""))
    assert "moonmind_retrieval_state" in str(survey.get("retrieval_state_volume", ""))
    # Historical fixtures are preserved evidence, never removal failures.
    assert survey["historical_fixture_count"] > 0
    result = rehearsal.check_inventory_survey(REPO_ROOT)
    assert result.status == "completed", result.evidence


def test_preflight_verdicts_derive_from_fixture_not_service_names() -> None:
    assert rehearsal.evaluate_preflight(
        rehearsal.build_sanitized_fixture("fresh-vector-free")
    ).status == "proceed"
    assert rehearsal.evaluate_preflight(
        rehearsal.build_sanitized_fixture("omitted-upgrade")
    ).status == "proceed"
    assert rehearsal.evaluate_preflight(
        rehearsal.build_sanitized_fixture("explicit-retired")
    ).status == "drain"
    assert rehearsal.evaluate_preflight(
        rehearsal.build_sanitized_fixture("legacy-qdrant-drain")
    ).status == "drain"
    assert rehearsal.evaluate_preflight(
        rehearsal.build_sanitized_fixture("rollback-restore")
    ).status == "blocked"
    # Ambiguous ownership blocks even with no pending work.
    fixture = rehearsal.build_sanitized_fixture("fresh-vector-free")
    fixture["ownership_ambiguous"] = True
    assert rehearsal.evaluate_preflight(fixture).status == "blocked"


def test_unknown_scenario_fails_closed() -> None:
    with pytest.raises(ValueError, match="unknown rehearsal scenario"):
        rehearsal.build_sanitized_fixture("qdrant-to-substitute")
    assert rehearsal.rehearse_scenario("qdrant-to-substitute").status == "failed"


def test_all_rehearsal_modes_pass_with_manifest_evidence() -> None:
    for scenario in rehearsal.REHEARSAL_MODES:
        result = rehearsal.rehearse_scenario(scenario)
        assert result.status == "completed", (scenario, result.evidence)


def test_historical_manifest_entries_are_distinct_preserved_contracts() -> None:
    entries = rehearsal.historical_manifest_entries()
    assert len(entries) == 2
    by_entry = {e["entry"]: e for e in entries}
    assert set(by_entry) == {"manifest_ref", "manifestArtifactRef"}
    assert by_entry["manifest_ref"]["commands"] == [
        "manifest.compile", "manifest.write_summary",
    ]
    assert set(by_entry["manifestArtifactRef"]["commands"]) == {
        "manifest_read", "manifest_compile", "manifest_write_summary",
    }


def test_manifest_replay_preserves_owner_and_command_order() -> None:
    entries = rehearsal.historical_manifest_entries()
    ok = rehearsal.check_manifest_boundary_replay(entries, entries, boundary_changed=False)
    assert ok.status == "completed", ok.evidence
    changed = rehearsal.check_manifest_boundary_replay(
        entries, entries, boundary_changed=True
    )
    assert changed.status == "completed", changed.evidence
    rewritten = [dict(e) for e in entries]
    rewritten[0] = {**rewritten[0], "commands": ["manifest.compile"]}
    bad = rehearsal.check_manifest_boundary_replay(entries, rewritten, boundary_changed=True)
    assert bad.status == "failed"
    dropped = entries[:-1]
    assert rehearsal.check_manifest_boundary_replay(
        entries, dropped, boundary_changed=True
    ).status == "failed"


def test_restart_retry_cancel_contained_at_each_cutover_handoff() -> None:
    assert rehearsal.check_failure_injection().status == "completed"
    clean = rehearsal.run_cutover_sequence()
    assert list(clean["steps"]) == list(rehearsal.CUTOVER_STEPS)
    assert all(v == "completed" for v in clean["steps"].values())
    with pytest.raises(ValueError, match="unknown cutover step"):
        rehearsal.run_cutover_sequence(fail_at="no-such-step")
    # Restart reconciles the same migration instead of forking it.
    tracker = rehearsal.DrainTracker()
    tracker.admit("vector-activity-1", "wf-qdrant-1")
    assert tracker.drain().status == "blocked"
    tracker.settle("vector-activity-1")
    drained = tracker.drain()
    assert drained.status == "completed"
    assert "wf-qdrant-1" in tracker.admitted_workflows


def test_upgrade_fixture_tolerates_old_env_without_rescues() -> None:
    fixture = rehearsal.build_old_env_fixture()
    assert fixture["VECTOR_STORE_PROVIDER"] == "qdrant"
    assert fixture["QDRANT_ENABLED"] == "true"
    result = rehearsal.check_upgrade_fixture(fixture, REPO_ROOT)
    assert result.status == "completed", result.evidence


def test_preservation_envelope_roundtrip_and_classification() -> None:
    entries = {
        "provenance:image-version": "qdrant/qdrant:v1.17.1 (recorded)",
        "provenance:mounts": "qdrant-storage:/qdrant/storage (recorded)",
        "provenance:recovery-config": "retention-window (recorded)",
        "snapshot:content": "sanitized-collection-count=2",
        "export:logical-payload": "sanitized-document-count=4",
    }
    envelope = rehearsal.create_preservation_envelope(entries, "test-pres-1", "unique")
    assert envelope["classification"] == "unique"
    assert envelope["storage"] == "operator-controlled-authorized-storage"
    assert rehearsal.verify_preservation_envelope(envelope, entries) is True
    assert rehearsal.verify_preservation_envelope(envelope, {"other": "x"}) is False
    with pytest.raises(ValueError, match="classification"):
        rehearsal.create_preservation_envelope(entries, "test-pres-2", "derived")
    with pytest.raises(ValueError, match="missing scopes"):
        rehearsal.create_preservation_envelope({"snapshot:x": "y"}, "test-pres-3", "unique")


def test_preservation_plan_refuses_derived_disposable_and_public_storage() -> None:
    assert rehearsal.check_preservation_plan(
        "vectors are derived so the payloads are disposable"
    ).status == "failed"
    assert rehearsal.check_preservation_plan(
        "assume all payloads reconstructible from source"
    ).status == "failed"
    assert rehearsal.check_preservation_plan(
        "publish snapshot to public issue for review"
    ).status == "failed"
    assert rehearsal.check_preservation_plan("just take a snapshot").status == "blocked"
    assert rehearsal.check_preservation_plan(
        "record collection/payload provenance (image/version, mounts, recovery "
        "config); preserve recoverable snapshot plus logical export; verify "
        "recoverability; classify reproducible vs unique; operator-controlled "
        "storage with redaction/retention, independent moonmind_retrieval_state evidence"
    ).status == "completed"


def test_retirement_refuses_broad_cleanup_and_volume_deletion() -> None:
    assert rehearsal.check_retirement_plan(["docker compose down -v"]).status == "failed"
    assert rehearsal.check_retirement_plan(
        ["docker system prune -f"]
    ).status == "failed"
    assert rehearsal.check_retirement_plan(
        ["delete volume qdrant-storage"]
    ).status == "failed"
    assert rehearsal.check_retirement_plan(
        ["remove qdrant-storage exports"]
    ).status == "failed"
    assert rehearsal.check_retirement_plan(["rm -rf /data/qdrant"]).status == "failed"
    assert rehearsal.check_retirement_plan(
        ["delete postgres-data to reclaim space"]
    ).status == "failed"
    assert rehearsal.check_retirement_plan(
        ["remove old ingress routes"]
    ).status == "blocked"


def test_retirement_requires_exact_ownership_and_is_idempotent() -> None:
    ownership = {
        "project": "moonmind",
        "service": "qdrant",
        "candidates": [
            {"project": "moonmind", "service": "qdrant", "container_id": "moonmind-qdrant-1"}
        ],
    }
    first = rehearsal.check_retirement_plan(
        ["stop/remove exact qdrant container moonmind-qdrant-1"], ownership
    )
    assert first.status == "completed", first.evidence
    # Repeated checks are safe: absence re-verifies.
    second = rehearsal.check_retirement_plan(
        ["verify absence of qdrant container moonmind-qdrant-1"], ownership
    )
    assert second.status == "completed", second.evidence
    # Wrong project and ambiguous candidates stay blocked, never broadened.
    wrong = dict(ownership, project="other-project")
    assert rehearsal.check_retirement_plan(
        ["stop/remove exact qdrant container"], wrong
    ).status == "blocked"
    ambiguous = {
        "project": "moonmind",
        "service": "qdrant",
        "candidates": [
            {"project": "moonmind", "service": "qdrant", "container_id": "a"},
            {"project": "moonmind", "service": "qdrant", "container_id": "b"},
        ],
    }
    assert rehearsal.check_retirement_plan(
        ["stop/remove exact qdrant container"], ambiguous
    ).status == "blocked"
    with pytest.raises(ValueError, match="required"):
        rehearsal.resolve_exact_container([], "", "")


def test_retention_requires_window_and_exact_resource_disposition() -> None:
    assert rehearsal.check_retention_policy(
        "automatically delete old volumes on upgrade"
    ).status == "failed"
    assert rehearsal.check_retention_policy("keep the volume a while").status == "blocked"
    assert rehearsal.check_retention_policy(
        "explicit recovery retention window; eventual deletion is a separate "
        "exact-resource operator action after export/restore verification; "
        "unrelated state preserved"
    ).status == "completed"


def test_rollback_refuses_qdrant_profile_back_and_whole_db() -> None:
    assert rehearsal.check_rollback_plan(
        "restore with optional Qdrant profile enabled"
    ).status == "failed"
    assert rehearsal.check_rollback_plan(
        "add the qdrant adapter back for rollback"
    ).status == "failed"
    assert rehearsal.check_rollback_plan(
        "restore whole postgres database snapshot"
    ).status == "failed"
    assert rehearsal.check_rollback_plan(
        "fallback switch to disabled search"
    ).status == "failed"
    assert rehearsal.check_rollback_plan("restore snapshot").status == "blocked"
    assert rehearsal.check_rollback_plan(
        "previous matching app/compose/image revision with preserved data, "
        "schema-compatibility check, rehearsed"
    ).status == "completed"


def test_rehearsal_state_is_idempotent_and_forks_closed() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        state_dir = Path(tmp) / "state"
        ok1, _ = rehearsal.save_state(state_dir, "mig-1", ["rehearsal-fresh-vector-free"])
        ok2, _ = rehearsal.save_state(state_dir, "mig-1", ["rehearsal-fresh-vector-free"])
        assert ok1 and ok2
        payload = json.loads((state_dir / "qdrant_rehearsal_state.json").read_text())
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
    (state_dir / "qdrant_rehearsal_state.json").write_text("{corrupt!!!")
    ok, msg = rehearsal.save_state(state_dir, "mig-1", ["a"])
    assert not ok
    assert "corrupt" in msg
    assert (state_dir / "qdrant_rehearsal_state.json").read_text() == "{corrupt!!!"


def test_allow_replace_resets_progress_for_new_migration(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    assert rehearsal.save_state(state_dir, "mig-1", ["a"])[0]
    ok, _ = rehearsal.save_state(state_dir, "mig-2", ["b"], allow_replace=True)
    assert ok
    payload = json.loads((state_dir / "qdrant_rehearsal_state.json").read_text())
    assert payload["migration_id"] == "mig-2"
    assert payload["completed_steps"] == ["b"]
    assert payload["runs"] == 1


def test_sanitize_redacts_secret_like_values() -> None:
    assert "[redacted]" in rehearsal.sanitize('QDRANT_API_KEY="live-secret-value"')
    assert "live-secret-value" not in rehearsal.sanitize('QDRANT_API_KEY="live-secret-value"')


def test_retirement_check_without_action_stays_blocked(tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, "tools/qdrant_cutover_rehearsal.py",
         "--mode", "retirement-check",
         "--state-dir", str(tmp_path / "state"),
         "--migration-id", "retire-no-action"],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    assert proc.returncode == 0, proc.stderr
    summary = json.loads(proc.stdout)
    assert summary["steps"][0]["status"] == "blocked"


def test_full_gate_passes_fixtures_with_deployment_blocked(tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, "tools/qdrant_cutover_rehearsal.py",
         "--mode", "all",
         "--state-dir", str(tmp_path / "state"),
         "--migration-id", "qdrant-full-gate"],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    assert proc.returncode == 0, proc.stderr
    summary = json.loads(proc.stdout)
    assert summary["verdict"] == "REHEARSAL_PASS_DEPLOYMENT_BLOCKED"
    assert not [s for s in summary["steps"] if s["status"] == "failed"]
    assert any(s["status"] == "blocked" for s in summary["steps"])
