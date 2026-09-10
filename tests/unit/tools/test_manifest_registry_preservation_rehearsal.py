"""Hermetic preservation gate tests for #4191 (no live deployment mutation)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tools import manifest_registry_preservation_rehearsal as rehearsal

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_runbook_precondition_present_with_required_sections() -> None:
    result = rehearsal.check_runbook_precondition(REPO_ROOT)
    assert result.status == "completed", result.evidence


def test_prerequisites_block_without_sibling_markers_and_owner() -> None:
    results = {r.name: r for r in rehearsal.check_prerequisites(REPO_ROOT)}
    assert results["prerequisite-4188-writer-inventory"].status == "blocked"
    assert "#4188" in results["prerequisite-4188-writer-inventory"].evidence
    assert results["prerequisite-4189-historical-read-cutover"].status == "blocked"
    assert results["prerequisite-4190-integration"].status == "blocked"
    assert results["prerequisite-named-owner-approval"].status == "blocked"
    assert results["prerequisite-live-deployment-qualification"].status == "blocked"


def test_disposition_table_covers_dedicated_shared_and_temporary() -> None:
    result = rehearsal.check_disposition_table(REPO_ROOT)
    assert result.status == "completed", result.evidence


def test_inventory_survey_confirms_retired_sources_and_retained_history() -> None:
    survey = rehearsal.collect_inventory_survey(REPO_ROOT)
    assert survey["sources"]["models"] == "present"
    assert survey["findings"]["manifest_table_model_present"] is False
    assert survey["findings"]["manifest_record_present"] is False
    assert survey["findings"]["writers_active"] is False
    assert survey["sources"]["manifests_service"] == "retired-absent"
    assert survey["sources"]["registry_retirement_migration"] == "present"
    assert survey["findings"]["shared_enum_present"] is True
    assert survey["findings"]["old_revisions_import_manifest_runtime"] == []
    assert len(survey["findings"]["migration_heads"]) == 1
    result = rehearsal.check_inventory_survey(REPO_ROOT)
    assert result.status == "completed", result.evidence


@pytest.mark.parametrize("remaining_path,content", [
    ("api_service/services/manifests_service.py", "def upsert_manifest(): pass\n"),
    ("api_service/services/manifest_sync_service.py", "def sync_manifest(): pass\n"),
    ("api_service/api/routers/manifests.py", "# Residual router module\n"),
    ("api_service/db/models.py", 'class ManifestRecord: __tablename__ = "manifest"\n'),
])
def test_partial_retirement_fails_inventory_and_migration_gate(
    tmp_path: Path, remaining_path: str, content: str,
) -> None:
    for source in (
        "api_service/db/models.py", "moonmind/workflows/temporal/service.py",
    ):
        destination = tmp_path / source
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text((REPO_ROOT / source).read_text())
    shutil.copytree(REPO_ROOT / "api_service/migrations", tmp_path / "api_service/migrations")
    residual = tmp_path / remaining_path
    residual.parent.mkdir(parents=True, exist_ok=True)
    with residual.open("a") as handle:
        handle.write("\n" + content)
    for check in (rehearsal.check_inventory_survey, rehearsal.check_migration_gate):
        result = check(tmp_path)
        assert result.status == "failed", result.evidence
        assert "Incomplete registry retirement" in result.evidence


def test_retirement_requires_migration_and_retained_history(tmp_path: Path) -> None:
    for source in ("api_service/db/models.py", "moonmind/workflows/temporal/service.py"):
        destination = tmp_path / source
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text((REPO_ROOT / source).read_text())
    result = rehearsal.check_migration_gate(tmp_path)
    assert result.status == "failed"
    assert "registry_retirement_migration" in result.evidence
    shutil.copytree(REPO_ROOT / "api_service/migrations", tmp_path / "api_service/migrations")
    models = tmp_path / "api_service/db/models.py"
    models.write_text(models.read_text().replace("MoonMind.ManifestIngest", "removed"))
    result = rehearsal.check_migration_gate(tmp_path)
    assert result.status == "failed"
    assert "historical evidence broken" in result.evidence


def test_preflight_verdicts_derive_from_fixture_not_field_names() -> None:
    assert rehearsal.evaluate_preflight(
        rehearsal.build_sanitized_fixture("fresh-empty")
    ).status == "proceed"
    assert rehearsal.evaluate_preflight(
        rehearsal.build_sanitized_fixture("populated-stopped-verified")
    ).status == "proceed"
    assert rehearsal.evaluate_preflight(
        rehearsal.build_sanitized_fixture("populated-active")
    ).status == "drain"
    assert rehearsal.evaluate_preflight(
        rehearsal.build_sanitized_fixture("partial-export")
    ).status == "blocked"
    assert rehearsal.evaluate_preflight(
        rehearsal.build_sanitized_fixture("incompatible-version")
    ).status == "blocked"


def test_unknown_scenario_fails_closed() -> None:
    result = rehearsal.rehearse_scenario("drop-everything")
    assert result.status == "failed"


def test_rehearsal_modes_pass_hermetically() -> None:
    for scenario in rehearsal.REHEARSAL_MODES:
        result = rehearsal.rehearse_scenario(scenario)
        assert result.status == "completed", (scenario, result.evidence)


def test_preservation_envelope_verifies_restore_and_refuses_partial(tmp_path: Path) -> None:
    fixture = rehearsal.build_sanitized_fixture("populated-stopped-verified")
    envelope = rehearsal.create_preservation_envelope(fixture, state_dir=tmp_path)
    ok, msg = rehearsal.verify_preservation_envelope(envelope, fixture)
    assert ok, msg
    assert envelope["row_count"] == 2
    assert len(envelope["content_digests"]) == 2
    assert len(envelope["state_digests"]) == 2
    # Last-run links cover both run-source shapes.
    sources = {link["source"] for link in envelope["last_run_links"]}
    assert sources == {"temporal", "queue"}
    # Partial export must not verify.
    tampered = dict(envelope)
    tampered["row_count"] = tampered["row_count"] - 1
    ok_partial, _ = rehearsal.verify_preservation_envelope(tampered, fixture)
    assert not ok_partial
    # File creation alone is not verification.
    ok_unverified, _ = rehearsal.verify_preservation_envelope(
        envelope, fixture, restore_into_isolated_namespace=False)
    assert not ok_unverified


def test_preservation_envelope_contains_no_sensitive_values(tmp_path: Path) -> None:
    fixture = rehearsal.build_sanitized_fixture("populated-stopped-verified")
    envelope = rehearsal.create_preservation_envelope(fixture, state_dir=tmp_path)
    blob = json.dumps(envelope)
    assert "api_key" not in blob
    assert "password" not in blob
    assert "yaml-bytes-temporal" not in blob
    assert "state-temporal" not in blob
    for digest in envelope["content_digests"] + envelope["state_digests"]:
        assert digest.startswith("sha256:")


def test_preservation_envelope_uses_consistent_snapshot_or_reconciliation(tmp_path: Path) -> None:
    fixture = rehearsal.build_sanitized_fixture("populated-stopped-verified")
    envelope = rehearsal.create_preservation_envelope(
        fixture, snapshot_mode="bogus-snapshot", state_dir=tmp_path)
    ok, msg = rehearsal.verify_preservation_envelope(envelope, fixture)
    assert not ok
    assert "snapshot" in msg.lower()
    reconciled = rehearsal.create_preservation_envelope(
        fixture, snapshot_mode="proven-final-reconciliation", state_dir=tmp_path)
    ok2, _ = rehearsal.verify_preservation_envelope(reconciled, fixture)
    assert ok2


def test_execution_history_preserved_verbatim_for_both_contracts() -> None:
    entries = rehearsal.historical_execution_entries()
    shapes = {e["entry_shape"] for e in entries}
    assert shapes == {"manifest_ref", "manifestArtifactRef"}
    assert all(e["workflow_type"] == "MoonMind.ManifestIngest" for e in entries)
    result = rehearsal.check_execution_history_authority(entries, entries)
    assert result.status == "completed", result.evidence
    # Rewritten run ID must fail (no silent hash/identity rewrite).
    tampered = [dict(e) for e in entries]
    tampered[1] = {**tampered[1], "run_id": "rewritten-run-id"}
    assert rehearsal.check_execution_history_authority(entries, tampered).status == "failed"
    # Coercion to UserWorkflow must fail.
    coerced = [dict(e) for e in entries]
    coerced[0] = {**coerced[0], "workflow_type": "MoonMind.UserWorkflow"}
    assert rehearsal.check_execution_history_authority(entries, coerced).status == "failed"
    # Dropped execution must fail.
    assert rehearsal.check_execution_history_authority(entries, entries[:-1]).status == "failed"


def test_shared_history_preservation_keeps_enum_and_columns() -> None:
    result = rehearsal.check_shared_history_preservation(REPO_ROOT)
    assert result.status == "completed", result.evidence


def test_failure_injection_refuses_silent_completion() -> None:
    result = rehearsal.check_failure_injection()
    assert result.status == "completed", result.evidence


def test_migration_gate_keeps_destructive_step_gated() -> None:
    result = rehearsal.check_migration_gate(REPO_ROOT)
    assert result.status == "completed", result.evidence
    assert "graph check only" in result.evidence
    assert "owner authorization remain blocked" in result.evidence


def test_artifact_retention_stays_independent() -> None:
    result = rehearsal.check_artifact_retention(REPO_ROOT)
    assert result.status == "completed", result.evidence


def test_rollback_supported_rehearsed_and_unsupported_stops() -> None:
    supported = rehearsal.check_rollback_plan(
        "matching compatible release before the boundary; verified protected restoration "
        "or forward repair after it preserving unrelated newer executions, credentials and "
        "shared database changes; unsupported rollback stops actionably")
    assert supported.status == "completed", supported.evidence
    incomplete = rehearsal.check_rollback_plan("restore snapshot")
    assert incomplete.status == "blocked"
    broad = rehearsal.check_rollback_plan(
        "broad backup restore over newer work with compatible release and "
        "verified protected restoration and forward repair preserving newer work and stop")
    # Broad restore over newer work is forbidden even when other words match.
    assert broad.status in ("failed", "blocked")


def test_retention_keeps_unrelated_data_intact() -> None:
    result = rehearsal.check_retention_policy(
        "explicit recovery retention window; unrelated users, profiles, workflow history, "
        "workspaces and saved-work data remain intact")
    assert result.status == "completed", result.evidence
    incomplete = rehearsal.check_retention_policy("keep exports for a while")
    assert incomplete.status == "blocked"


def test_retirement_refuses_forbidden_patterns() -> None:
    assert rehearsal.check_retirement_plan(["drop manifest table without preservation"]).status == "failed"
    assert rehearsal.check_retirement_plan(["delete whole executions"]).status == "failed"
    assert rehearsal.check_retirement_plan(["coerce old records to UserWorkflow"]).status == "failed"
    assert rehearsal.check_retirement_plan(["rewrite immutable hashes"]).status == "failed"
    assert rehearsal.check_retirement_plan(["add a permanent second registry"]).status == "failed"
    assert rehearsal.check_retirement_plan(["docker compose down -v"]).status == "failed"
    assert rehearsal.check_retirement_plan(["delete minio-data volume"]).status == "failed"
    assert rehearsal.check_retirement_plan(
        ["automatic production data deletion on upgrade"]).status == "failed"


def test_retirement_blocked_without_exact_ownership() -> None:
    result = rehearsal.check_retirement_plan(
        ["drop manifest table after verified preservation + stopped writers + owner authorization"],
        None,
    )
    assert result.status == "blocked"
    result = rehearsal.check_retirement_plan([], {"head": "x", "table": "manifest"})
    assert result.status == "blocked"


def test_retirement_exact_resource_with_ownership_completes() -> None:
    result = rehearsal.check_retirement_plan(
        ["drop manifest table after verified preservation + stopped writers + owner authorization"],
        {"head": "376_merge_375_heads", "table": "manifest",
         "issue": "MoonLadderStudios/MoonMind#4191"},
    )
    assert result.status == "completed", result.evidence


def test_build_pins_distinguish_postgres_from_sqlite_helpers() -> None:
    pins = rehearsal.collect_build_pins(REPO_ROOT)
    assert len(pins["migration_heads"]) == 1
    assert pins["initial_manifest_table"] == "present"
    assert "PostgreSQL" in pins["postgres_vs_sqlite"]
    assert "SQLite/helper" in pins["postgres_vs_sqlite"]
    result = rehearsal.check_build_pins(REPO_ROOT)
    assert result.status == "completed", result.evidence


def test_sanitized_evidence_for_integration_gate() -> None:
    result = rehearsal.check_sanitized_evidence(REPO_ROOT)
    assert result.status == "completed", result.evidence


def test_concurrent_migrator_state_refused_without_replace(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    assert rehearsal.save_migration_state(state_dir, "mm4191", ["a"])[0]
    ok, msg = rehearsal.save_migration_state(state_dir, "other", ["b"])
    assert not ok
    assert "stale/concurrent" in msg


def test_corrupt_state_fails_closed_without_overwrite(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "manifest_registry_rehearsal_state.json").write_text("{corrupt!!!")
    ok, msg = rehearsal.save_migration_state(state_dir, "mm4191", ["a"])
    assert not ok
    assert "corrupt" in msg
    assert (state_dir / "manifest_registry_rehearsal_state.json").read_text() == "{corrupt!!!"


def test_allow_replace_resets_for_new_migration(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    assert rehearsal.save_migration_state(state_dir, "mm4191", ["a"])[0]
    ok, _ = rehearsal.save_migration_state(state_dir, "mm4191-b", ["b"], allow_replace=True)
    assert ok
    payload = json.loads((state_dir / "manifest_registry_rehearsal_state.json").read_text())
    assert payload["migration_id"] == "mm4191-b"
    assert payload["completed_steps"] == ["b"]


def test_cli_all_reports_honest_blocked_without_failure(tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, "tools/manifest_registry_preservation_rehearsal.py",
         "--mode", "all",
         "--state-dir", str(tmp_path / "state"),
         "--migration-id", "test-4191"],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    assert proc.returncode == 0, proc.stderr
    summary = json.loads(proc.stdout)
    assert summary["issue"] == "MoonLadderStudios/MoonMind#4191"
    assert summary["verdict"] == "REHEARSAL_PASS_DEPLOYMENT_BLOCKED"
    assert not [r for r in summary["steps"] if r["status"] == "failed"]
    assert any(r["status"] == "blocked" for r in summary["steps"])


def test_cli_retirement_without_action_stays_blocked(tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, "tools/manifest_registry_preservation_rehearsal.py",
         "--mode", "retirement-check",
         "--state-dir", str(tmp_path / "state"),
         "--migration-id", "retire-no-action"],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    assert proc.returncode == 0, proc.stderr
    summary = json.loads(proc.stdout)
    assert summary["steps"][0]["status"] == "blocked"


def test_no_new_export_database_or_secret_system() -> None:
    tool_text = (REPO_ROOT / "tools/manifest_registry_preservation_rehearsal.py").read_text()
    assert "from alembic import" not in tool_text
    assert "import sqlalchemy" not in tool_text
    # The only create_table reference is a read-only string probe of the
    # initial migration; the gate never executes DDL.
    assert "op.create_table" not in tool_text.replace(
        '"op.create_table(\'manifest\'"', '"probe"').replace(
        '"op.create_table(\'manifest\'"', '"probe"')
    assert "secret" in tool_text.lower()  # redaction discussed, never minted
    runbook = (REPO_ROOT / "docs/tmp/ManifestRegistryPreservationRunbook-4191.md").read_text()
    assert "no new export database" in runbook.lower()
    assert "no new secret system" in runbook.lower()
    assert "public GitHub" in runbook
    assert "Temporal" in runbook and "history" in runbook.lower()
