from __future__ import annotations

import json
from pathlib import Path

from tools import keycloak_cutover_rehearsal as rehearsal


def test_rehearsal_modes_preserve_uuids_ownership_and_background_work() -> None:
    for scenario in rehearsal.REHEARSAL_MODES:
        result = rehearsal.rehearse_scenario(scenario)
        assert result.status == "completed", (scenario, result.evidence)


def test_unknown_scenario_fails_closed() -> None:
    result = rehearsal.rehearse_scenario("keycloak-to-magic")
    assert result.status == "failed"


def test_identity_mapping_requires_issuer_and_subject() -> None:
    fixture = rehearsal.build_sanitized_fixture("explicit-local")
    try:
        rehearsal.apply_identity_mapping(fixture, {"issuer": "", "subject": ""})
    except ValueError:
        pass
    else:
        raise AssertionError("empty issuer/subject must raise")


def test_interrupted_restart_reconciles_same_migration(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    ok1, _ = rehearsal.save_state(state_dir, "mig-1", ["rehearsal-explicit-local"])
    ok2, _ = rehearsal.save_state(state_dir, "mig-1", ["rehearsal-explicit-local"])
    assert ok1 and ok2
    payload = json.loads((state_dir / "k6_rehearsal_state.json").read_text())
    assert payload["migration_id"] == "mig-1"
    assert payload["runs"] == 2
    # Idempotent: no duplicated step entries.
    assert payload["completed_steps"] == ["rehearsal-explicit-local"]


def test_stale_concurrent_migration_refused_without_allow_replace(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    assert rehearsal.save_state(state_dir, "mig-1", ["a"])[0]
    ok, msg = rehearsal.save_state(state_dir, "mig-2", ["b"])
    assert not ok
    assert "stale/concurrent" in msg
    ok_replace, _ = rehearsal.save_state(state_dir, "mig-2", ["b"], allow_replace=True)
    assert ok_replace


def test_rollback_refuses_whole_database_restore() -> None:
    result = rehearsal.check_rollback_plan("restore whole postgres database snapshot")
    assert result.status == "failed"


def test_rollback_refuses_silent_disabled_fallback() -> None:
    result = rehearsal.check_rollback_plan("fallback switch to disabled auth")
    assert result.status == "failed"


def test_rollback_requires_reconciliation_after_writes() -> None:
    result = rehearsal.check_rollback_plan("restore snapshot")
    assert result.status == "blocked"


def test_retirement_refuses_broad_down_v() -> None:
    result = rehearsal.check_retirement_plan(["docker compose down -v"])
    assert result.status == "failed"


def test_retirement_refuses_volume_deletion() -> None:
    result = rehearsal.check_retirement_plan(["delete app volume moonmind_data"])
    assert result.status == "failed"


def test_retirement_blocked_without_identified_service() -> None:
    result = rehearsal.check_retirement_plan(["remove old ingress routes"])
    assert result.status == "blocked"


def test_workflow_history_authority_detects_owner_rewrite() -> None:
    fixture = rehearsal.build_sanitized_fixture("kc-to-accounts")
    before = fixture["workflow_owner_refs"]
    after = [dict(r, owner_id="00000000-0000-0000-0000-000000000000") for r in before]
    result = rehearsal.check_workflow_history_authority(before, after)
    assert result.status == "failed"


def test_prerequisites_never_mark_unknown_facts_complete() -> None:
    for step in rehearsal.check_prerequisites():
        assert step.status == "blocked"
        assert "Missing" in step.evidence or "missing" in step.evidence.lower()


def test_run_gate_keeps_plan_unarchived_and_deployment_blocked(tmp_path: Path) -> None:
    # run_gate must pass hermetic rehearsal while leaving deployment blocked;
    # it must not mutate the repo (plan stays Proposed).
    results = rehearsal.run_gate()
    by_name = {r.name: r for r in results}
    assert by_name["plan-precondition"].status == "completed"
    hermetic = [r for r in results if r.name.startswith("rehearsal-")]
    assert hermetic and all(r.status == "completed" for r in hermetic)
    assert any(r.status == "blocked" for r in results)


def test_evidence_sanitizes_secrets() -> None:
    redacted = rehearsal.sanitize("login with password: hunter2 and token= abc123")
    assert "hunter2" not in redacted
