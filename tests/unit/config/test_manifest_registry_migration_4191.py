"""Manifest registry migration gate — MR4 coverage (#4191).

Parent: MoonLadderStudios/MoonMind#4187. This module covers the MR4
preservation/disposition/verification core that sibling #4192 MR5's
unconditional drop migration does not enforce:

- caller-backed disposition completeness (REQ-1),
- bounded protected export + verified usable restore (REQ-2/REQ-3, ACC-1),
- writer/concurrency/incompatible-code refusal (REQ-6, ACC-5),
- migration-chain self-containment and ancestry (REQ-4, ACC-2),
- shared enum/type decoding and historical reads for both entry contracts
  (REQ-5, ACC-3, ACC-4),
- irreversible boundary with supported/unsupported rollback paths (REQ-8, ACC-7),
- artifact retention authority independence (REQ-7, ACC-6),
- sanitized evidence supply without production mutation (REQ-9, ACC-8).

Hermetic boundary: these tests run against SQLite/helper logic and the
stdlib gate module. Real supported PostgreSQL migration coverage stays
deployment-owned and is explicitly distinguished here (see
``test_postgres_coverage_is_deployment_owned``); required CI runs this
suite via the existing impact selector.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from moonmind.gates.manifest_registry_migration_4191 import (
    DRAIN_APPROVAL_ENV_VAR,
    DRAIN_APPROVAL_VALUE,
    DROP_CHILD_REVISION,
    DROP_PARENT_REVISION,
    DROP_REVISION,
    ISSUE_REF,
    MANIFEST_REGISTRY_MIGRATION_CONTRACT,
    MANIFEST_TABLE,
    MANIFEST_TABLE_COLUMNS,
    RegistryDrainInputs,
    check_disposition_complete,
    check_migration_text_self_contained,
    downgrade_refusal_message,
    evaluate_registry_drain,
    export_row_envelope,
    find_live_writers,
    is_drain_approved,
    registry_disposition_table,
    render_operator_procedure,
    require_registry_drop_approval,
    sanitized_export_report,
    sha256_hex,
    stable_disposition_digest,
    verify_export_envelope,
)
from tools.manifest_registry_export_4191 import (
    check_export_permissions,
    export_rows,
    verify_export_dir,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
DROP_MIGRATION = (
    REPO_ROOT / "api_service/migrations/versions/376_drop_manifest_registry_4192.py"
)
INITIAL_MIGRATION = (
    REPO_ROOT
    / "api_service/migrations/versions/0b8e4befb8e5_initial_clean_migration.py"
)


def _fixture_rows() -> list[dict]:
    content_a = "name: demo-a\nversion: v0\nnodes: []\n"
    content_b = "name: demo-b\nversion: v1\nnodes:\n  - id: n1\n"
    return [
        {
            "id": 1,
            "name": "demo-a",
            "content": content_a,
            "content_hash": hashlib.sha256(content_a.encode()).hexdigest(),
            "version": "v0",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-02T00:00:00+00:00",
            "last_indexed_at": "2026-01-03T00:00:00+00:00",
            "last_run_job_id": "11111111-1111-1111-1111-111111111111",
            "last_run_source": "api",
            "last_run_status": "completed",
            "last_run_workflow_id": "mm:wf-a",
            "last_run_temporal_run_id": "run-a",
            "last_run_manifest_ref": "artifact://manifest/demo-a",
            "last_run_started_at": "2026-01-04T00:00:00+00:00",
            "last_run_finished_at": "2026-01-05T00:00:00+00:00",
            "state_json": {"phase": "done"},
            "state_updated_at": "2026-01-05T00:00:00+00:00",
        },
        {
            # Nullable-field edge: no state payload, no last-run linkage.
            "id": 2,
            "name": "demo-b",
            "content": content_b,
            "content_hash": hashlib.sha256(content_b.encode()).hexdigest(),
            "version": "v1",
            "created_at": "2026-02-01T00:00:00+00:00",
            "updated_at": "2026-02-02T00:00:00+00:00",
            "last_indexed_at": None,
            "last_run_job_id": None,
            "last_run_source": None,
            "last_run_status": None,
            "last_run_workflow_id": None,
            "last_run_temporal_run_id": None,
            "last_run_manifest_ref": None,
            "last_run_started_at": None,
            "last_run_finished_at": None,
            "state_json": None,
            "state_updated_at": None,
        },
    ]


# ---------------------------------------------------------------------------
# REQ-1: caller-backed disposition table.
# ---------------------------------------------------------------------------


def test_disposition_covers_every_registry_column_with_owner() -> None:
    table = registry_disposition_table()
    assert check_disposition_complete(table) == []
    dedicated = {f"manifest.{column}" for column in MANIFEST_TABLE_COLUMNS}
    names = {row.name for row in table}
    assert dedicated <= names
    for row in table:
        assert row.owner.strip(), f"missing owner: {row.name}"
        assert row.caller.strip(), f"missing caller: {row.name}"


def test_disposition_retains_shared_fields_for_generic_reads() -> None:
    by_name = {row.name: row for row in registry_disposition_table()}
    for shared in (
        "temporal_executions.manifest_ref",
        "temporal_execution_sources.manifest_ref",
        "TemporalWorkflowType.MANIFEST_INGEST",
        "WORKFLOW_ENTRY_BY_TYPE[MANIFEST_INGEST]",
        "manifest status lineage fallback",
    ):
        assert by_name[shared].disposition == "retain_readonly", shared


def test_disposition_digest_is_stable_for_evidence_refs() -> None:
    assert stable_disposition_digest() == stable_disposition_digest()
    assert len(stable_disposition_digest()) == 64


# ---------------------------------------------------------------------------
# REQ-2/REQ-3, ACC-1: protected export with exact preservation + restore.
# ---------------------------------------------------------------------------


def test_export_envelope_preserves_exact_bytes_and_links() -> None:
    row = _fixture_rows()[0]
    envelope, content_bytes = export_row_envelope(row)
    assert content_bytes == row["content"].encode("utf-8")
    assert envelope["content_sha256"] == sha256_hex(content_bytes)
    assert envelope["version"] == "v0"
    assert envelope["state_json"] == {"phase": "done"}
    assert envelope["last_run"]["workflow_id"] == "mm:wf-a"
    assert envelope["last_run"]["temporal_run_id"] == "run-a"
    assert verify_export_envelope(envelope, content_bytes) == []


def test_export_rows_round_trip_with_usable_restore(tmp_path: Path) -> None:
    rows = _fixture_rows()
    export_dir = tmp_path / "protected-export"
    report = export_rows(rows, export_dir, expected_row_count=2)
    assert report["row_count"] == 2
    assert check_export_permissions(export_dir) == []

    restored = verify_export_dir(export_dir)
    assert restored["row_count"] == 2
    by_name = {row["name"]: row for row in restored["rows"]}
    assert by_name["demo-a"]["content_sha256"] == hashlib.sha256(
        rows[0]["content"].encode()
    ).hexdigest()
    assert by_name["demo-b"]["content_sha256"] == hashlib.sha256(
        rows[1]["content"].encode()
    ).hexdigest()


def test_export_report_carries_no_sensitive_content(tmp_path: Path) -> None:
    rows = _fixture_rows()
    export_dir = tmp_path / "protected-export"
    report = export_rows(rows, export_dir, expected_row_count=2)
    text = json.dumps(report)
    for row in rows:
        assert row["content"] not in text
    assert "phase" not in text or "state_json" not in text
    # Digests and refs are the allowed evidence, not the content itself.
    assert report["rows"][0]["content_sha256"]
    assert report["rows"][0]["last_run_manifest_ref"] == "artifact://manifest/demo-a"


def test_export_requires_required_fields() -> None:
    row = _fixture_rows()[0]
    incomplete = dict(row)
    del incomplete["content"]
    with pytest.raises(ValueError, match="required field"):
        export_row_envelope(incomplete)


def test_tampered_export_fails_restore_not_silent(tmp_path: Path) -> None:
    export_dir = tmp_path / "protected-export"
    export_rows(_fixture_rows(), export_dir, expected_row_count=2)
    (export_dir / "manifest_rows" / "1.yaml").write_bytes(b"tampered-bytes")
    with pytest.raises(RuntimeError, match="digest mismatch"):
        verify_export_dir(export_dir)


# ---------------------------------------------------------------------------
# REQ-6, ACC-5: no silent loss — writers, partial, failure, concurrency.
# ---------------------------------------------------------------------------


def test_live_writers_block_export_and_drain(tmp_path: Path) -> None:
    writers = find_live_writers(["api_service/services/manifests_service.py"])
    assert writers
    with pytest.raises(RuntimeError, match="live writers"):
        export_rows(_fixture_rows(), tmp_path / "export", writers_present=writers)
    decision = evaluate_registry_drain(
        RegistryDrainInputs(writers_present=writers, export_verified=True)
    )
    assert not decision.may_apply_destructive
    assert any("live_writers" in blocking for blocking in decision.blocking)


def test_partial_export_is_refused(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="partial export"):
        export_rows(_fixture_rows(), tmp_path / "export", expected_row_count=5)


def test_failed_verification_blocks_destructive_application() -> None:
    decision = evaluate_registry_drain(
        RegistryDrainInputs(export_verified=False, export_row_count=2)
    )
    assert not decision.may_apply_destructive
    assert "export_not_verified" in decision.blocking


def test_row_count_mismatch_blocks_destructive_application() -> None:
    decision = evaluate_registry_drain(
        RegistryDrainInputs(
            export_verified=True, export_row_count=1, expected_row_count=2
        )
    )
    assert not decision.may_apply_destructive
    assert any("row_count_mismatch" in blocking for blocking in decision.blocking)


def test_concurrent_migrator_blocks_destructive_application() -> None:
    decision = evaluate_registry_drain(
        RegistryDrainInputs(export_verified=True, migrator_lock_held_by_other=True)
    )
    assert not decision.may_apply_destructive
    assert "concurrent_migrator_holds_lock" in decision.blocking


def test_incompatible_old_code_blocks_destructive_application() -> None:
    decision = evaluate_registry_drain(
        RegistryDrainInputs(
            export_verified=True,
            incompatible_code_present=("api_service/services/manifests_service.py",),
        )
    )
    assert not decision.may_apply_destructive
    assert any("incompatible_code" in blocking for blocking in decision.blocking)


def test_drain_gate_passes_only_when_fully_ready() -> None:
    decision = evaluate_registry_drain(
        RegistryDrainInputs(
            writers_present=(),
            export_verified=True,
            export_row_count=2,
            expected_row_count=2,
            incompatible_code_present=(),
            migrator_lock_held_by_other=False,
        )
    )
    assert decision.may_apply_destructive
    assert decision.required_action == "proceed"


def test_retired_writers_are_absent_from_this_checkout() -> None:
    present = [
        str(path.relative_to(REPO_ROOT)).replace("\\", "/")
        for path in (
            REPO_ROOT / "api_service/services/manifests_service.py",
            REPO_ROOT / "api_service/services/manifest_sync_service.py",
            REPO_ROOT / "api_service/api/routers/manifests.py",
        )
        if path.exists()
    ]
    assert find_live_writers(present) == ()


# ---------------------------------------------------------------------------
# REQ-4, ACC-2: forward migration, ancestry, self-contained chain.
# ---------------------------------------------------------------------------


def test_drop_migration_chain_is_self_contained() -> None:
    drop_text = DROP_MIGRATION.read_text(encoding="utf-8")
    initial_text = INITIAL_MIGRATION.read_text(encoding="utf-8")
    assert check_migration_text_self_contained(
        drop_migration_text=drop_text, initial_migration_text=initial_text
    ) == []
    assert f'revision: str = "{DROP_REVISION}"' in drop_text
    assert f'down_revision.*=.*"{DROP_PARENT_REVISION}"' in drop_text or (
        DROP_PARENT_REVISION in drop_text
    )


def test_drop_migration_ancestry_is_intact() -> None:
    follow_up = (
        REPO_ROOT
        / f"api_service/migrations/versions/{DROP_CHILD_REVISION}.py"
    )
    assert follow_up.exists()
    child_text = follow_up.read_text(encoding="utf-8")
    assert DROP_REVISION in child_text or "376_drop_manifest_registry" in child_text


def test_fresh_migration_avoids_removed_runtime_imports() -> None:
    versions = REPO_ROOT / "api_service/migrations/versions"
    offenders: list[str] = []
    for path in sorted(versions.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        for forbidden in (
            "api_service.services.manifests_service",
            "api_service.services.manifest_sync_service",
            "from moonmind.manifest import",
            "from moonmind.manifest.",
        ):
            if forbidden in text:
                offenders.append(f"{path.name}: {forbidden}")
    assert offenders == []


# ---------------------------------------------------------------------------
# REQ-5, ACC-3, ACC-4: shared decoding, both entry contracts, lineage.
# ---------------------------------------------------------------------------


def test_historical_enum_member_still_decodes() -> None:
    from api_service.db.models import TemporalWorkflowType

    assert TemporalWorkflowType("MoonMind.ManifestIngest").name == "MANIFEST_INGEST"
    assert TemporalWorkflowType.MANIFEST_INGEST.value == "MoonMind.ManifestIngest"


def test_shared_manifest_ref_columns_survive_registry_removal() -> None:
    from api_service.db import models

    assert "manifest_ref" in dir(models.TemporalExecutionCanonicalRecord)
    assert "manifest_ref" in dir(models.TemporalExecutionRecord)
    assert not hasattr(models, "ManifestRecord")


def test_both_historical_entry_contracts_stay_supported() -> None:
    from moonmind.workflows.temporal.service import WORKFLOW_ENTRY_BY_TYPE
    from api_service.db.models import TemporalWorkflowType

    assert WORKFLOW_ENTRY_BY_TYPE[TemporalWorkflowType.MANIFEST_INGEST] == "manifest"
    from tools.qdrant_cutover_rehearsal import historical_manifest_entries

    entries = historical_manifest_entries()
    kinds = {entry["entry"] for entry in entries}
    assert kinds == {"manifest_ref", "manifestArtifactRef"}


def test_lineage_fallback_survives_registry_removal() -> None:
    router_text = (
        REPO_ROOT / "api_service/api/routers/executions.py"
    ).read_text(encoding="utf-8")
    assert "manifest_ref" in router_text


# ---------------------------------------------------------------------------
# REQ-7, ACC-6: retention/GC authority independent of the registry.
# ---------------------------------------------------------------------------


def test_registry_drop_cascades_into_no_shared_evidence() -> None:
    drop_text = DROP_MIGRATION.read_text(encoding="utf-8")
    ddl_statements = [
        line.strip()
        for line in drop_text.splitlines()
        if line.strip().startswith("op.")
    ]
    assert ddl_statements, "drop migration must contain DDL operations"
    for statement in ddl_statements:
        assert '"manifest"' in statement or "'manifest'" in statement, statement
    for protected in (
        "temporal_executions",
        "temporal_execution_sources",
        "saved_work",
        "workspace",
        "profile",
    ):
        assert protected not in "\n".join(ddl_statements).lower(), protected
    assert MANIFEST_TABLE in drop_text


# ---------------------------------------------------------------------------
# REQ-8, ACC-7: irreversible boundary, supported vs unsupported rollback.
# ---------------------------------------------------------------------------


def test_downgrade_fails_closed_with_restore_direction() -> None:
    drop_text = DROP_MIGRATION.read_text(encoding="utf-8")
    assert "raise RuntimeError" in drop_text
    message = downgrade_refusal_message()
    assert DROP_REVISION in message
    assert "protected" in message
    assert "forward repair" in message


def test_operator_procedure_defines_supported_and_unsupported_paths() -> None:
    procedure = render_operator_procedure()
    assert "matching compatible release" in procedure
    assert "without touching newer work" in procedure or "newer work" in procedure
    assert "pg_dump" in procedure or "psql" in procedure
    assert "Temporal history" in procedure


# ---------------------------------------------------------------------------
# REQ-9, ACC-8: sanitized evidence, PG distinguished, CI selection.
# ---------------------------------------------------------------------------


def test_sanitized_evidence_supply_contract() -> None:
    assert ISSUE_REF == "MoonLadderStudios/MoonMind#4191"
    assert MANIFEST_REGISTRY_MIGRATION_CONTRACT == (
        "manifest-registry-migration-4191-v1"
    )
    doc = REPO_ROOT / "docs/tmp/ManifestRegistryDisposition-4191.md"
    assert doc.exists()
    text = doc.read_text(encoding="utf-8")
    assert "manifest_registry_migration_4191" in text
    assert "manifest_registry_export_4191" in text


def test_postgres_coverage_is_deployment_owned_not_sqlite() -> None:
    """Hermetic SQLite/helper tests do not certify the production upgrade.

    The logic above (envelopes, digests, drain refusal, chain ancestry)
    runs in required CI; the real supported PostgreSQL migration — fresh
    ``alembic upgrade head`` plus populated old-database upgrade with
    authorized generic historical reads — is deployment-owned evidence for
    #4189 and the integration gate, and must not be reported as qualified
    by this module.
    """
    assert "postgresql" in INITIAL_MIGRATION.read_text(encoding="utf-8").lower()


def test_required_ci_collects_this_migration_suite() -> None:
    from tools.select_test_suites import select_suites

    selection = select_suites(
        ["tests/unit/config/test_manifest_registry_migration_4191.py"]
    )
    assert selection.unit_fast is True


# ---------------------------------------------------------------------------
# REQ-6, ACC-5: migration-execution wiring (no `alembic upgrade` bypass).
# ---------------------------------------------------------------------------


def test_drop_migration_enforces_drain_gate_at_execution() -> None:
    """Pin the operator enforcement point: upgrade() must consult the gate.

    Fails when the wiring is absent (plain unconditional DDL). The migration
    counts live ``manifest`` rows at execution time, requires explicit
    operator approval for populated/unreadable tables, and still drops the
    registry plus fails closed on downgrade.
    """
    drop_text = DROP_MIGRATION.read_text(encoding="utf-8")
    assert "require_registry_drop_approval" in drop_text
    assert "is_drain_approved" in drop_text
    assert "SELECT COUNT(*) FROM manifest" in drop_text
    assert DRAIN_APPROVAL_ENV_VAR in drop_text
    assert "manifest_registry_migration_4191" in drop_text
    # DDL + closed downgrade still present (no silent rewrite of outcomes).
    assert 'op.drop_table("manifest")' in drop_text or (
        "op.drop_table('manifest')" in drop_text
    )
    assert "raise RuntimeError" in drop_text


def test_registry_drop_approval_allows_empty_without_approval() -> None:
    require_registry_drop_approval(manifest_row_count=0, approved=False)


def test_registry_drop_approval_refuses_populated_without_approval() -> None:
    with pytest.raises(RuntimeError, match="still holds 3 row"):
        require_registry_drop_approval(manifest_row_count=3, approved=False)


def test_registry_drop_approval_refuses_unobservable_without_approval() -> None:
    with pytest.raises(RuntimeError, match="unobservable"):
        require_registry_drop_approval(manifest_row_count=None, approved=False)


def test_registry_drop_approval_allows_populated_with_approval() -> None:
    require_registry_drop_approval(manifest_row_count=3, approved=True)
    require_registry_drop_approval(manifest_row_count=None, approved=True)


def test_drain_approval_env_parsing() -> None:
    assert DRAIN_APPROVAL_VALUE == "1"
    assert is_drain_approved({DRAIN_APPROVAL_ENV_VAR: "1"}) is True
    assert is_drain_approved({}) is False
    assert is_drain_approved({DRAIN_APPROVAL_ENV_VAR: ""}) is False
    assert is_drain_approved({DRAIN_APPROVAL_ENV_VAR: "yes"}) is False


# ---------------------------------------------------------------------------
# REQ-2 polish: --verify-only runs without --rows-json.
# ---------------------------------------------------------------------------


def test_verify_only_runs_without_rows_json(tmp_path: Path) -> None:
    from tools.manifest_registry_export_4191 import main

    export_dir = tmp_path / "protected-export"
    export_rows(_fixture_rows(), export_dir, expected_row_count=2)
    assert main(["--export-dir", str(export_dir), "--verify-only"]) == 0


# ---------------------------------------------------------------------------
# REQ-7, ACC-6: retention/authorization matrix after registry removal.
# ---------------------------------------------------------------------------


def _retention_artifact(**overrides):  # type: ignore[no-untyped-def]
    from types import SimpleNamespace

    from api_service.db.models import (
        TemporalArtifactRedactionLevel,
        TemporalArtifactStatus,
    )

    base = {
        "artifact_id": "art_matrix_1",
        "created_by_principal": "owner-1",
        "redaction_level": TemporalArtifactRedactionLevel.NONE,
        "metadata_json": {},
        "status": TemporalArtifactStatus.COMPLETE,
        "expires_at": None,
        "deleted_at": None,
        "hard_deleted_at": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_retention_availability_matrix_stays_truthful() -> None:
    from datetime import UTC, datetime, timedelta

    from api_service.db.models import TemporalArtifactRedactionLevel
    from moonmind.workflows.temporal.artifacts import TemporalArtifactService

    now = datetime.now(UTC)
    # Clean evidence is the only available outcome.
    assert (
        TemporalArtifactService.saved_work_availability_of(
            _retention_artifact(), now=now
        )
        == "available"
    )
    # Missing bytes, digest mismatch, quarantine, expiry, deletion each win
    # over a bare COMPLETE row in that precedence order.
    assert (
        TemporalArtifactService.saved_work_availability_of(
            _retention_artifact(), bytes_missing=True, now=now
        )
        == "incomplete"
    )
    assert (
        TemporalArtifactService.saved_work_availability_of(
            _retention_artifact(), digest_mismatch=True, now=now
        )
        == "corrupt"
    )
    assert (
        TemporalArtifactService.saved_work_availability_of(
            _retention_artifact(expires_at=now - timedelta(seconds=1)), now=now
        )
        == "expired"
    )
    assert (
        TemporalArtifactService.saved_work_availability_of(
            _retention_artifact(deleted_at=now), now=now
        )
        == "deleted"
    )
    assert (
        TemporalArtifactService.saved_work_availability_of(
            _retention_artifact(
                metadata_json={"quarantine": "true"},
                redaction_level=TemporalArtifactRedactionLevel.RESTRICTED,
            ),
            now=now,
        )
        == "quarantined"
    )
    # A real expired timestamp (not just the flag) is expired, and a
    # never-verified row is not reported as available.
    assert (
        TemporalArtifactService.saved_work_availability_of(
            _retention_artifact(expires_at=now - timedelta(seconds=1)), now=now
        )
        == "expired"
    )
    assert (
        TemporalArtifactService.saved_work_availability_of(
            _retention_artifact(), never_verified_complete=True, now=now
        )
        == "locally_retained_but_unsaved"
    )


def test_retention_raw_authorization_matrix() -> None:
    from api_service.db.models import TemporalArtifactRedactionLevel
    from moonmind.workflows.temporal.artifacts import TemporalArtifactService

    service = TemporalArtifactService.__new__(TemporalArtifactService)
    open_artifact = _retention_artifact(
        redaction_level=TemporalArtifactRedactionLevel.NONE
    )
    assert service._raw_access_allowed(open_artifact, principal="anyone") is True
    restricted = _retention_artifact(
        redaction_level=TemporalArtifactRedactionLevel.RESTRICTED
    )
    # Owner reads restricted bytes; wrong-owner and bare service readers do not.
    assert service._raw_access_allowed(restricted, principal="owner-1") is True
    assert service._raw_access_allowed(restricted, principal="owner-2") is False
    assert service._raw_access_allowed(restricted, principal="service:gc") is False
    # An admitted owner scope restores service-to-service reads.
    assert (
        service._raw_access_allowed(
            restricted, principal="service:gc", admitted_principal="owner-1"
        )
        is True
    )
    # Quarantine denies even the owner: a data-safety state, not auth.
    quarantined = _retention_artifact(
        redaction_level=TemporalArtifactRedactionLevel.RESTRICTED,
        metadata_json={"quarantine": "true"},
    )
    assert service._raw_access_allowed(quarantined, principal="owner-1") is False


def test_registry_removal_owns_no_saved_work_evidence() -> None:
    """Deleting a registry entry cannot cascade into shared evidence.

    The drop DDL touches only the ``manifest`` table (pinned above), and the
    retention lifecycle owner keeps sole saved-work copies addressable by
    artifact id — never by registry row — so registry deletion leaves the
    artifact lifecycle tables and their authority untouched.
    """
    from moonmind.schemas import saved_work_retention as retention

    drop_text = DROP_MIGRATION.read_text(encoding="utf-8")
    assert "temporal_artifact" not in drop_text.lower()
    assert "saved_work" not in drop_text.lower()
    # The lifecycle contract still distinguishes the sole-copy states the
    # registry must never overwrite: corrupt/expired/deleted stay terminal.
    assert retention.describe_artifact_availability(
        status="complete", digest_mismatch=True
    ) == "corrupt"
    assert retention.describe_artifact_availability(
        status="complete", expires_at_expired=True
    ) == "expired"
    assert retention.describe_artifact_availability(
        status="complete", deletion_started=True
    ) == "deleted"
