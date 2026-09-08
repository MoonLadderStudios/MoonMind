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


def test_corrupt_state_fails_closed_without_overwrite(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "k6_rehearsal_state.json").write_text("{corrupt!!!")
    ok, msg = rehearsal.save_state(state_dir, "mig-1", ["a"])
    assert not ok
    assert "corrupt" in msg
    # The only durable record must survive, not be overwritten.
    assert (state_dir / "k6_rehearsal_state.json").read_text() == "{corrupt!!!"


def test_allow_replace_resets_progress_for_new_migration(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    assert rehearsal.save_state(state_dir, "mig-1", ["a"])[0]
    ok, _ = rehearsal.save_state(state_dir, "mig-2", ["b"], allow_replace=True)
    assert ok
    payload = json.loads((state_dir / "k6_rehearsal_state.json").read_text())
    assert payload["migration_id"] == "mig-2"
    assert payload["completed_steps"] == ["b"]
    assert payload["runs"] == 1


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


def test_retirement_refuses_destructive_database_actions() -> None:
    assert rehearsal.check_retirement_plan(["delete keycloak database"]).status == "failed"
    assert rehearsal.check_retirement_plan(["remove keycloak database"]).status == "failed"
    assert rehearsal.check_retirement_plan(["drop keycloak database"]).status == "failed"


def test_retirement_check_without_action_stays_blocked(tmp_path: Path) -> None:
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "tools/keycloak_cutover_rehearsal.py",
         "--mode", "retirement-check",
         "--state-dir", str(tmp_path / "state"),
         "--migration-id", "retire-no-action"],
        capture_output=True, text=True, cwd=Path(__file__).resolve().parents[3],
    )
    assert proc.returncode == 0, proc.stderr
    summary = json.loads(proc.stdout)
    assert summary["steps"][0]["status"] == "blocked"


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
    assert "abc123" not in redacted


def test_evidence_redacts_bearer_tokens_and_assignments() -> None:
    redacted = rehearsal.sanitize("Authorization: Bearer abc123-def456")
    assert "abc123-def456" not in redacted
    redacted = rehearsal.sanitize("api call with token=supersecretvalue")
    assert "supersecretvalue" not in redacted


def test_inventory_survey_collects_counts_without_identity_exports() -> None:
    survey = rehearsal.collect_inventory_survey()
    assert len(survey["sources"]) == len(rehearsal.SURVEY_SOURCES)
    assert survey["keycloak_image"] == "quay.io/keycloak/keycloak:24.0"
    assert survey["auth_provider_default"] == "disabled"
    assert survey["realm_clients"] == ["api-service", "open-webui"]
    blob = json.dumps(survey)
    assert "hunter2" not in blob
    # No identity material: only counts, pins, and file refs.
    assert "operator_user_id" not in blob


def test_inventory_survey_step_completes_hermetically() -> None:
    result = rehearsal.check_inventory_survey()
    assert result.status == "completed"


def test_prerequisites_cite_real_repo_probes() -> None:
    steps = rehearsal.check_prerequisites()
    assert len(steps) == len(rehearsal.PREREQUISITE_IDS)
    by_name = {s.name: s for s in steps}
    assert by_name["prerequisite-4120-mode-key-setup"].status == "blocked"
    # Evidence must cite the real probe, not a static list.
    assert "settings.py" in by_name["prerequisite-4120-mode-key-setup"].evidence
    assert "Repo probe" in by_name["prerequisite-4119-mapping-revision"].evidence


def test_capability_presence_detects_absent_migration_and_modes() -> None:
    presence = rehearsal.detect_capability_presence()
    assert "none present" in presence["4119-mapping-revision"]
    assert "disabled" in presence["4120-mode-key-setup"]
    assert "none present" in presence["4122-protected-recovery"]


def test_build_pins_collect_exact_versions_and_topology() -> None:
    pins = rehearsal.collect_build_pins()
    assert pins["keycloak_image"] == "quay.io/keycloak/keycloak:24.0"
    assert pins["auth_provider_default"] == "disabled"
    assert isinstance(pins["alembic_revisions"], int) and pins["alembic_revisions"] > 0
    assert pins["plan_status"] == "Proposed"
    assert pins["app_image_default"].startswith("ghcr.io/")
    assert pins["root_package_version"] == "1.0.0"
    result = rehearsal.check_build_pins()
    assert result.status == "completed"
    assert "never attempted" in result.evidence or "Never attempted" in result.evidence or "never attempted" in result.evidence.lower()


def test_backup_envelope_requires_scopes_and_restore_verifies(tmp_path: Path) -> None:
    import os
    import stat

    entries = {
        "identity:users": "sanitized-user-count=2",
        "config:auth-provider": "disabled",
        "keys:session-key-id": "sanitized-key-id",
    }
    envelope = rehearsal.create_backup_envelope(entries, "b1", state_dir=tmp_path)
    assert envelope["encryption"] == "operator-kms-required"
    assert rehearsal.verify_backup_envelope(envelope, entries)
    assert not rehearsal.verify_backup_envelope(envelope, {**entries, "identity:users": "tampered"})
    persisted = tmp_path / "b1.json"
    assert persisted.exists()
    mode = stat.S_IMODE(os.stat(persisted).st_mode)
    assert mode == 0o600, f"backup envelope must persist access-restricted, got {oct(mode)}"
    try:
        rehearsal.create_backup_envelope({"identity:users": "x"}, "b2")
    except ValueError:
        pass
    else:
        raise AssertionError("missing config/keys scopes must raise")


def test_mutation_freeze_refuses_identity_mutations_only_when_frozen() -> None:
    freeze = rehearsal.MutationFreeze()
    assert freeze.check("create identity mapping")[0]
    freeze.freeze()
    assert not freeze.check("create identity mapping")[0]
    assert not freeze.check("grant admin privilege")[0]
    # Account provisioning is an identity mutation and stays frozen.
    assert not freeze.check("create account")[0]
    assert not freeze.check("create user")[0]
    assert not freeze.check("register user")[0]
    # Unrelated operational reads stay allowed even when frozen.
    assert freeze.check("read smoke-test status")[0]


def test_rollback_accepts_only_structured_scope() -> None:
    ok = rehearsal.check_rollback_plan("matching app/config/key restore with reconciliation")
    assert ok.status == "completed"
    assert rehearsal.check_rollback_plan("nonsense").status == "blocked"
    assert rehearsal.check_rollback_plan("drop keycloak database").status == "failed"
    assert rehearsal.check_rollback_plan("delete keycloak identity rows").status == "failed"


def test_workflow_history_authority_compares_workflow_ids() -> None:
    before = [
        {"workflow_id": "wf-a", "owner_id": "o1", "commands": ["start", "poll"]},
        {"workflow_id": "wf-b", "owner_id": "o2", "commands": ["start"]},
    ]
    swapped = [
        {"workflow_id": "wf-b", "owner_id": "o2", "commands": ["start"]},
        {"workflow_id": "wf-a", "owner_id": "o1", "commands": ["start", "poll"]},
    ]
    # Same identities by key (reordered collection) still verifies.
    assert rehearsal.check_workflow_history_authority(before, swapped).status == "completed"
    # Cross-associated identities must fail even with matching owners/commands.
    other = [
        {"workflow_id": "wf-x", "owner_id": "o1", "commands": ["start", "poll"]},
        {"workflow_id": "wf-y", "owner_id": "o2", "commands": ["start"]},
    ]
    assert rehearsal.check_workflow_history_authority(before, other).status == "failed"


def test_build_pins_fail_when_any_required_pin_unusable(tmp_path: Path) -> None:
    # Malformed compose content must not yield positive topology evidence.
    (tmp_path / "api_service/migrations/versions").mkdir(parents=True)
    (tmp_path / "api_service/migrations/versions/0001_init.py").write_text("# init\n")
    result = rehearsal.check_build_pins(tmp_path)
    assert result.status == "failed"


def test_json_out_parent_created_before_state(tmp_path: Path) -> None:
    import subprocess
    import sys

    out = tmp_path / "nested" / "dir" / "out.json"
    proc = subprocess.run(
        [sys.executable, "tools/keycloak_cutover_rehearsal.py",
         "--mode", "preflight",
         "--state-dir", str(tmp_path / "state"),
         "--migration-id", "json-out-test",
         "--json-out", str(out)],
        capture_output=True, text=True, cwd=Path(__file__).resolve().parents[3],
    )
    assert proc.returncode in (0, 2), proc.stderr
    assert out.exists()


def test_inventory_survey_passes_after_realm_export_removal(tmp_path: Path) -> None:
    # After integrated removal deletes keycloak/realm-export.json, the
    # expected absence must not fail the survey.
    (tmp_path / "docker-compose.yaml").write_text(
        "services:\n  app:\n    image: app:latest\n"
    )
    for rel in (
        "moonmind/config/settings.py",
        "api_service/main.py",
        "api_service/auth_providers.py",
        "api_service/db/models.py",
        "init_db_scripts/01-create-dbs.sh",
        ".env-template",
    ):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# no auth surfaces here\n")
    assert rehearsal._capability_present("4129-removal", tmp_path)
    result = rehearsal.check_inventory_survey(tmp_path)
    assert result.status == "completed", result.evidence


def test_app_image_pin_validated_by_hostname() -> None:
    pins = rehearsal.collect_build_pins()
    from urllib.parse import urlparse

    host = urlparse("https://" + pins["app_image_default"]).hostname
    # Exact hostname validation, not a substring match.
    assert host == "ghcr.io"


def test_dual_issuance_refuses_concurrent_old_and_new() -> None:
    assert rehearsal.check_dual_issuance(True, True).status == "failed"
    assert rehearsal.check_dual_issuance(False, True).status == "completed"


def test_drain_preserves_admitted_work(tmp_path: Path) -> None:
    _ = tmp_path
    tracker = rehearsal.DrainTracker()
    tracker.admit("req-1", "wf-k6-1")
    assert tracker.drain().status == "blocked"
    tracker.settle("req-1")
    assert tracker.drain().status == "completed"
    assert "wf-k6-1" in tracker.admitted_workflows


def test_backup_freeze_drain_step_passes() -> None:
    assert rehearsal.check_backup_freeze_drain().status == "completed"


def test_failure_injection_contains_each_step_and_records_boundary() -> None:
    assert rehearsal.check_failure_injection().status == "completed"
    for step in rehearsal.CUTOVER_STEPS:
        run = rehearsal.run_cutover_sequence(fail_at=step)
        assert run["steps"][step] == "failed-injected"
        assert run["backward_compatible_point"] == rehearsal.LAST_BACKWARD_COMPATIBLE_POINT
    clean = rehearsal.run_cutover_sequence()
    assert "invalidate" in clean["rollback_rule"]
    assert "never whole shared DB" in clean["rollback_rule"]
    assert "never silent disabled-auth" in clean["rollback_rule"]
    try:
        rehearsal.run_cutover_sequence(fail_at="nope")
    except ValueError:
        pass
    else:
        raise AssertionError("unknown cutover step must raise")


def test_auth_boundary_replay_records_no_boundary_change() -> None:
    fixture = rehearsal.build_sanitized_fixture("explicit-local")
    before = fixture["workflow_owner_refs"]
    ok = rehearsal.check_auth_boundary_replay(before, before, boundary_changed=False)
    assert ok.status == "completed"
    assert "No persisted/history boundary change" in ok.evidence
    after = [dict(r, owner_id="00000000-0000-0000-0000-000000000000") for r in before]
    assert rehearsal.check_auth_boundary_replay(before, after, boundary_changed=True).status == "failed"


def test_auth_boundary_replay_with_changed_boundary_and_exact_evidence() -> None:
    # A future auth change that alters a persisted boundary must supply exact
    # replay evidence: identical owner IDs + command order completes.
    fixture = rehearsal.build_sanitized_fixture("kc-to-accounts")
    before = [dict(r, commands=list(r["commands"])) for r in fixture["workflow_owner_refs"]]
    after = [dict(r, commands=list(r["commands"])) for r in fixture["workflow_owner_refs"]]
    ok = rehearsal.check_auth_boundary_replay(before, after, boundary_changed=True)
    assert ok.status == "completed"
    assert "exact replay evidence verified" in ok.evidence


def test_capability_probes_flip_when_sibling_capability_lands(tmp_path: Path) -> None:
    # At HEAD the repo-observable prerequisites stay blocked (fail-closed).
    assert not rehearsal._capability_present("4119-mapping-revision")
    assert not rehearsal._capability_present("4120-mode-key-setup")
    assert not rehearsal._capability_present("4122-protected-recovery")
    assert not rehearsal._capability_present("4129-removal")
    # #4119: a Keycloak migration outside this gate flips the probe.
    versions = tmp_path / "api_service/migrations/versions"
    versions.mkdir(parents=True)
    (versions / "999_keycloak_identity_map.py").write_text("# sibling #4119 migration\n")
    assert rehearsal._capability_present("4119-mapping-revision", tmp_path)
    by_name = {s.name: s for s in rehearsal.check_prerequisites(tmp_path)}
    assert by_name["prerequisite-4119-mapping-revision"].status == "completed"
    # #4120: a new AUTH_PROVIDER default flips the probe.
    settings_dir = tmp_path / "moonmind/config"
    settings_dir.mkdir(parents=True)
    (settings_dir / "settings.py").write_text(
        'AUTH_PROVIDER: str = Field("local", alias="AUTH_PROVIDER")\n'
    )
    assert rehearsal._capability_present("4120-mode-key-setup", tmp_path)
    # An unrelated literal elsewhere in the module must not flip the probe.
    (settings_dir / "settings.py").write_text(
        'AUTH_PROVIDER: str = Field("disabled", alias="AUTH_PROVIDER")\n'
        "CACHE_BACKEND = 'local'\n"
    )
    assert not rehearsal._capability_present("4120-mode-key-setup", tmp_path)
    # #4122: a recovery tool outside this gate flips the probe.
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir(parents=True)
    (tools_dir / "identity_recovery.py").write_text("# sibling #4122 recovery\n")
    assert rehearsal._capability_present("4122-protected-recovery", tmp_path)


def test_capability_probes_flip_when_keycloak_surfaces_removed(tmp_path: Path) -> None:
    (tmp_path / "docker-compose.yaml").write_text("services:\n  api:\n    image: app:latest\n")
    assert rehearsal._capability_present("4129-removal", tmp_path)
    by_name = {s.name: s for s in rehearsal.check_prerequisites(tmp_path)}
    assert by_name["prerequisite-4129-removal"].status == "completed"


def test_owner_and_deployment_gates_never_flip_hermetically(tmp_path: Path) -> None:
    for pid in (
        "4117-inventory",
        "4128-qualification",
        "4130-operator-contracts",
        "named-owner-approval",
        "live-idp-mfa-qualification",
    ):
        assert not rehearsal._capability_present(pid, tmp_path)
        by_name = {s.name: s for s in rehearsal.check_prerequisites(tmp_path)}
        assert by_name[f"prerequisite-{pid}"].status == "blocked"


def test_run_gate_includes_new_hermetic_steps_and_stays_deployment_blocked() -> None:
    results = rehearsal.run_gate()
    by_name = {r.name: r for r in results}
    for name in (
        "inventory-survey", "build-pins", "backup-freeze-drain",
        "failure-injection", "dual-issuance", "auth-boundary-replay",
        "rollback-scope", "retirement-plan",
    ):
        assert by_name[name].status == "completed", (name, by_name[name].evidence)
    assert any(r.status == "blocked" for r in results)
    assert not any(r.status == "failed" for r in results)
