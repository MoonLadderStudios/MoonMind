"""Manifest registry migration gate — MR4 coverage (#4191).

Parent: MoonLadderStudios/MoonMind#4187. This module covers the MR4
preservation/disposition/verification core that sibling #4192 MR5's
unconditional drop migration does not enforce:

- caller-backed disposition completeness (REQ-1),
- retired-writer absence and the execution-time drop refusal (REQ-6, ACC-5),
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
    build_sanitized_supply_package,
    check_disposition_complete,
    check_migration_text_self_contained,
    downgrade_refusal_message,
    find_live_writers,
    is_drain_approved,
    registry_disposition_table,
    render_operator_procedure,
    require_registry_drop_approval,
    stable_disposition_digest,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
DROP_MIGRATION = (
    REPO_ROOT / "api_service/migrations/versions/376_drop_manifest_registry_4192.py"
)
INITIAL_MIGRATION = (
    REPO_ROOT
    / "api_service/migrations/versions/0b8e4befb8e5_initial_clean_migration.py"
)


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
# REQ-6, ACC-5: retired writers stay absent.
# ---------------------------------------------------------------------------


def test_live_writer_markers_are_detected() -> None:
    assert find_live_writers(["api_service/services/manifests_service.py"]) == (
        "api_service/services/manifests_service.py",
    )


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
    from api_service.db.models import TemporalWorkflowType
    from moonmind.workflows.temporal.service import WORKFLOW_ENTRY_BY_TYPE

    assert WORKFLOW_ENTRY_BY_TYPE[TemporalWorkflowType.MANIFEST_INGEST] == "manifest"


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


def test_sanitized_supply_package_is_durable_and_secret_free() -> None:
    """Pin the REQ-9 handoff: stable identity, no content, no I/O.

    The package the owning workflow publishes to #4189/integration gate
    must be reproducible (stable digest), complete (ancestry + approval
    switch + procedure), and sanitized (no YAML bytes, state payloads,
    or credentials). Building it must not touch a database or network.
    """
    package = build_sanitized_supply_package()
    assert package["contract"] == MANIFEST_REGISTRY_MIGRATION_CONTRACT
    assert package["issue"] == ISSUE_REF
    assert package["disposition_digest"] == stable_disposition_digest()
    assert package["disposition_rows"] == len(registry_disposition_table())
    assert package["drop_revision"] == DROP_REVISION
    assert package["drop_parent_revision"] == DROP_PARENT_REVISION
    assert package["drop_child_revision"] == DROP_CHILD_REVISION
    assert package["drain_approval_env_var"] == DRAIN_APPROVAL_ENV_VAR
    assert "pg_dump" in package["operator_procedure"]
    # Sanitized: procedure + identity metadata only; no registry content,
    # state payloads, or credential values travel in the handoff.
    text = json.dumps(package, sort_keys=True)
    assert "nodes:" not in text
    assert "state_json" not in text
    assert "ghp_" not in text
    assert "BEGIN PRIVATE KEY" not in text


def test_postgres_coverage_is_deployment_owned_not_sqlite() -> None:
    """Hermetic SQLite/helper tests do not certify the production upgrade.

    The logic above (digests, drain refusal, chain ancestry)
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
    assert "read_drain_export_row_count" in drop_text
    assert "MOONMIND_MANIFEST_REGISTRY_EXPORT_ROW_COUNT" in drop_text
    assert "pg_advisory_xact_lock" in drop_text
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


def test_registry_drop_approval_refuses_bare_approval_without_export() -> None:
    """A bare approved=True without bound export evidence must not drop."""
    with pytest.raises(RuntimeError, match="not bound to a verified export"):
        require_registry_drop_approval(manifest_row_count=3, approved=True)
    with pytest.raises(RuntimeError, match="not bound to a verified export"):
        require_registry_drop_approval(manifest_row_count=None, approved=True)


def test_registry_drop_approval_allows_populated_with_bound_export() -> None:
    require_registry_drop_approval(
        manifest_row_count=3,
        approved=True,
        export_row_count=3,
        export_verified=True,
    )
    require_registry_drop_approval(
        manifest_row_count=None,
        approved=True,
        export_row_count=3,
        export_verified=True,
    )


def test_registry_drop_approval_refuses_stale_export_count() -> None:
    with pytest.raises(RuntimeError, match="disagrees with the verified export"):
        require_registry_drop_approval(
            manifest_row_count=4,
            approved=True,
            export_row_count=3,
            export_verified=True,
        )


@pytest.mark.parametrize(
    "row_count,approved",
    [(3, False), (None, False), (3, True), (None, True)],
    ids=["populated", "unobservable", "unbound-approval", "unbound-unobservable"],
)
def test_registry_drop_refusal_names_a_shipped_preservation_path(
    row_count, approved
) -> None:
    """An unmigrated database is told what to run, never a removed tool."""
    with pytest.raises(RuntimeError) as refusal:
        require_registry_drop_approval(manifest_row_count=row_count, approved=approved)
    message = str(refusal.value)
    for text in (message, render_operator_procedure()):
        assert "tools/" not in text
        assert "pg_dump" in text
        assert DRAIN_APPROVAL_ENV_VAR in text


def test_drain_approval_env_parsing() -> None:
    assert DRAIN_APPROVAL_VALUE == "1"
    assert is_drain_approved({DRAIN_APPROVAL_ENV_VAR: "1"}) is True
    assert is_drain_approved({}) is False
    assert is_drain_approved({DRAIN_APPROVAL_ENV_VAR: ""}) is False
    assert is_drain_approved({DRAIN_APPROVAL_ENV_VAR: "yes"}) is False


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


@pytest.mark.asyncio
async def test_retention_raw_authorization_matrix(tmp_path) -> None:
    from api_service.db.models import TemporalArtifactRedactionLevel
    from moonmind.workflows.temporal.artifacts import (
        ArtifactAccessContext,
        LocalTemporalArtifactStore,
        TemporalArtifactAuthorizationError,
        TemporalArtifactRepository,
        TemporalArtifactService,
    )
    from tests.unit.workflows.temporal.test_artifacts import temporal_db

    service = TemporalArtifactService.__new__(TemporalArtifactService)
    open_artifact = _retention_artifact(
        redaction_level=TemporalArtifactRedactionLevel.NONE
    )
    assert (
        service._raw_access_allowed(
            open_artifact, access=ArtifactAccessContext.for_service("anyone")
        )
        is True
    )
    restricted = _retention_artifact(
        redaction_level=TemporalArtifactRedactionLevel.RESTRICTED
    )
    owner_access = ArtifactAccessContext.for_service("owner-1")
    # Owner reads restricted bytes; wrong-owner and bare service readers do not.
    assert service._raw_access_allowed(restricted, access=owner_access) is True
    assert (
        service._raw_access_allowed(
            restricted, access=ArtifactAccessContext.for_service("owner-2")
        )
        is False
    )
    assert (
        service._raw_access_allowed(
            restricted, access=ArtifactAccessContext.for_service("service:gc")
        )
        is False
    )
    # Trusted input admission persists exact raw rights for the destination.
    # A reader's owner label alone cannot stand in for that delegation.
    async with temporal_db(tmp_path) as sessions:
        async with sessions() as session:
            artifacts = TemporalArtifactService(
                TemporalArtifactRepository(session),
                store=LocalTemporalArtifactStore(tmp_path / "raw-grant"),
            )
            artifact, _ = await artifacts.create(
                principal="owner-1",
                redaction_level=TemporalArtifactRedactionLevel.RESTRICTED,
            )
            await artifacts.write_complete(
                artifact_id=artifact.artifact_id,
                principal="owner-1",
                payload=b"retained raw",
            )
            await artifacts.delegate_read_access(
                artifact_ids=[artifact.artifact_id],
                workflow_id="retained-consumer",
                access=owner_access,
            )
            _artifact, payload = await artifacts.read(
                artifact_id=artifact.artifact_id,
                access=ArtifactAccessContext.for_execution(
                    "retained-consumer", principal="service:gc"
                ),
            )
            assert payload == b"retained raw"
            with pytest.raises(TemporalArtifactAuthorizationError):
                await artifacts.read(
                    artifact_id=artifact.artifact_id,
                    access=ArtifactAccessContext.for_execution(
                        "unrelated-consumer", principal="service:gc"
                    ),
                )
    # Quarantine denies even the owner: a data-safety state, not auth.
    quarantined = _retention_artifact(
        redaction_level=TemporalArtifactRedactionLevel.RESTRICTED,
        metadata_json={"quarantine": "true"},
    )
    assert service._raw_access_allowed(quarantined, access=owner_access) is False


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


# ---------------------------------------------------------------------------
# Forward cutover for databases that already dropped the registry.
# ---------------------------------------------------------------------------


def test_forward_audit_migration_covers_applied_drops() -> None:
    audit = (
        REPO_ROOT
        / "api_service/migrations/versions/381_manifest_drain_audit_4191.py"
    )
    assert audit.exists()
    text = audit.read_text(encoding="utf-8")
    assert "380_account_lifecycle_4122" in text
    assert "require_registry_drop_approval" in text
    assert "already dropped" in text or "already applied" in text
