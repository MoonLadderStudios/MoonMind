"""Persistence disposition and drain gate for the retired Manifest registry.

Source issue: MoonLadderStudios/MoonMind#4191 (MR4 in the pinned Manifest
removal plan). The native Manifest product's ``manifest`` registry table is
dropped by Alembic revision ``376_drop_manifest_registry_4192`` (owned by
sibling #4192 MR5); this module supplies MR4's gated preservation/drain
contract that the unconditional drop migration itself does not enforce:

- a caller-backed disposition table for every dedicated registry column,
  constraint/index, callback-writer link, and shared execution field,
- the execution-time drop approval that refuses a populated table until the
  operator binds a verified ``pg_dump`` snapshot's row count to it,
- the irreversible-boundary definition and migration-chain self-containment
  checks.

This module depends on the standard library only and lives in the
lightweight ``moonmind.gates`` namespace (whose ``__init__`` chain imports
nothing) so the gate stays importable from workflow-adjacent and tooling
contexts without pulling Temporal, database, or settings dependencies.
Do not add heavy imports here and do not re-export this module through a
heavy package ``__init__``.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any

#: Versioned contract name for the Manifest registry migration gate.
#: Bump only with an explicit cutover plan; the gate is closed by default.
MANIFEST_REGISTRY_MIGRATION_CONTRACT = "manifest-registry-migration-4191-v1"

#: Issue that owns this contract.
ISSUE_REF = "MoonLadderStudios/MoonMind#4191"

#: Dedicated registry table removed by the forward migration.
MANIFEST_TABLE = "manifest"

#: Forward migration that drops the registry (owned by sibling #4192 MR5).
DROP_REVISION = "376_drop_manifest_registry_4192"

#: Ancestry the drop migration must keep: merge head, drop, then follow-up.
DROP_PARENT_REVISION = "376_merge_375_heads"
DROP_CHILD_REVISION = "377_repository_connections_4005"

#: Operator approval switch consulted by the forward drop migration at
#: execution time. A populated ``manifest`` table refuses to drop unless this
#: environment variable carries the approved value; an empty table (fresh
#: install) proceeds without approval. The switch names the existing
#: migration locking/versioning path — concurrent migrators stay serialized
#: by Alembic's transactional DDL — and never imports removed runtime
#: modules.
DRAIN_APPROVAL_ENV_VAR = "MOONMIND_MANIFEST_REGISTRY_DRAIN_APPROVED"

#: Exact approved value for :data:`DRAIN_APPROVAL_ENV_VAR`.
DRAIN_APPROVAL_VALUE = "1"

#: Operator-supplied verified export row count bound to the drain approval.
#: ``require_registry_drop_approval`` refuses a bare ``approved=True`` for a
#: populated or unobservable table unless this variable carries the verified
#: export's row count and it matches the live pre-drop count, so a stale
#: approval or rows inserted/changed after the snapshot cannot authorize the
#: drop.
DRAIN_EXPORT_ROW_COUNT_ENV_VAR = "MOONMIND_MANIFEST_REGISTRY_EXPORT_ROW_COUNT"

#: Modules whose presence proves an old writer/callback is still active.
#: Each was deleted with the retired product; any survivor blocks the drain.
RETIRED_WRITER_MARKERS = (
    "api_service/services/manifests_service.py",
    "api_service/services/manifest_sync_service.py",
    "api_service/api/routers/manifests.py",
    "moonmind/manifest/",
    "manifest_ingest",
)

#: Runtime-module imports that must never appear in the drop migration or
#: its ancestors' Manifest handling: the migration chain stays self-contained
#: with inline ``op.create_table``/``op.drop_table`` calls.
FORBIDDEN_MIGRATION_IMPORTS = (
    "api_service.services.manifests_service",
    "api_service.services.manifest_sync_service",
    "api_service.db.models",
    "moonmind.manifest",
)

#: Exact ``manifest`` columns at plan baseline
#: ``7bd159dafb44770278e8c5563d47667de6e7c491``
#: (``0b8e4befb8e5_initial_clean_migration.py``). The disposition table
#: below must cover every one of them; ``check_disposition_complete`` fails
#: closed when a column is missing.
MANIFEST_TABLE_COLUMNS = (
    "id",
    "name",
    "content",
    "content_hash",
    "version",
    "created_at",
    "updated_at",
    "last_indexed_at",
    "last_run_job_id",
    "last_run_source",
    "last_run_status",
    "last_run_workflow_id",
    "last_run_temporal_run_id",
    "last_run_manifest_ref",
    "last_run_started_at",
    "last_run_finished_at",
    "state_json",
    "state_updated_at",
)


@dataclass(frozen=True, slots=True)
class RegistryFieldDisposition:
    """Ownership verdict for one persisted registry field or constraint."""

    name: str
    kind: str
    disposition: str
    owner: str
    preserve: str
    caller: str


def registry_disposition_table() -> tuple[RegistryFieldDisposition, ...]:
    """Return the caller-backed persistence disposition table.

    Every dedicated ``manifest`` column, constraint, and index is marked
    ``delete_after_verified_export`` with the removed writer that owned it;
    shared execution columns/enums consumed by #4189's generic historical
    read path are marked ``retain_readonly`` and must survive the drop.
    A field name alone is never proof that data is disposable: each row
    names its caller and its owner.
    """
    delete = "delete_after_verified_export"
    retain = "retain_readonly"
    return (
        RegistryFieldDisposition(
            name="manifest.id",
            kind="dedicated_registry_state",
            disposition=delete,
            owner="#4191",
            preserve="row identity for export row counts; not reused after drop",
            caller="manifests_service registry CRUD",
        ),
        RegistryFieldDisposition(
            name="manifest.name",
            kind="dedicated_registry_state",
            disposition=delete,
            owner="#4191",
            preserve="exact string in protected export index",
            caller="manifests_service registry CRUD (UniqueConstraint name)",
        ),
        RegistryFieldDisposition(
            name="manifest.content",
            kind="dedicated_registry_state",
            disposition=delete,
            owner="#4191",
            preserve="exact YAML bytes in protected export; sha256 digest in report",
            caller="manifests_service registry writes",
        ),
        RegistryFieldDisposition(
            name="manifest.content_hash",
            kind="dedicated_registry_state",
            disposition=delete,
            owner="#4191",
            preserve="exact stored hash in protected export; recomputed digest must match",
            caller="manifests_service registry writes",
        ),
        RegistryFieldDisposition(
            name="manifest.version",
            kind="dedicated_registry_state",
            disposition=delete,
            owner="#4191",
            preserve="exact version string in protected export",
            caller="manifests_service registry writes",
        ),
        RegistryFieldDisposition(
            name="manifest.created_at",
            kind="dedicated_registry_state",
            disposition=delete,
            owner="#4191",
            preserve="exact timestamp in protected export",
            caller="manifests_service registry writes",
        ),
        RegistryFieldDisposition(
            name="manifest.updated_at",
            kind="dedicated_registry_state",
            disposition=delete,
            owner="#4191",
            preserve="exact timestamp in protected export",
            caller="manifests_service registry writes",
        ),
        RegistryFieldDisposition(
            name="manifest.last_indexed_at",
            kind="dedicated_registry_state",
            disposition=delete,
            owner="#4191",
            preserve="exact timestamp (nullable) in protected export",
            caller="manifest_sync_service pipeline callbacks",
        ),
        RegistryFieldDisposition(
            name="manifest.last_run_job_id",
            kind="callback_writer_link",
            disposition=delete,
            owner="#4191",
            preserve="exact UUID (nullable) last-run link in protected export",
            caller="manifest_sync_service run linkage",
        ),
        RegistryFieldDisposition(
            name="manifest.last_run_source",
            kind="callback_writer_link",
            disposition=delete,
            owner="#4191",
            preserve="exact source string (nullable) in protected export",
            caller="manifest_sync_service run linkage",
        ),
        RegistryFieldDisposition(
            name="manifest.last_run_status",
            kind="callback_writer_link",
            disposition=delete,
            owner="#4191",
            preserve="exact status string (nullable) in protected export",
            caller="worker state callback via manifests router",
        ),
        RegistryFieldDisposition(
            name="manifest.last_run_workflow_id",
            kind="callback_writer_link",
            disposition=delete,
            owner="#4191",
            preserve="exact workflow id (nullable) last-run link in protected export",
            caller="worker state callback via manifests router",
        ),
        RegistryFieldDisposition(
            name="manifest.last_run_temporal_run_id",
            kind="callback_writer_link",
            disposition=delete,
            owner="#4191",
            preserve="exact run id (nullable) last-run link in protected export",
            caller="worker state callback via manifests router",
        ),
        RegistryFieldDisposition(
            name="manifest.last_run_manifest_ref",
            kind="callback_writer_link",
            disposition=delete,
            owner="#4191",
            preserve="exact artifact ref (nullable) in protected export",
            caller="worker state callback via manifests router",
        ),
        RegistryFieldDisposition(
            name="manifest.last_run_started_at",
            kind="callback_writer_link",
            disposition=delete,
            owner="#4191",
            preserve="exact timestamp (nullable) in protected export",
            caller="worker state callback via manifests router",
        ),
        RegistryFieldDisposition(
            name="manifest.last_run_finished_at",
            kind="callback_writer_link",
            disposition=delete,
            owner="#4191",
            preserve="exact timestamp (nullable) in protected export",
            caller="worker state callback via manifests router",
        ),
        RegistryFieldDisposition(
            name="manifest.state_json",
            kind="dedicated_registry_state",
            disposition=delete,
            owner="#4191",
            preserve="exact incremental/state payload (nullable) in protected export",
            caller="manifest_sync_service state updates",
        ),
        RegistryFieldDisposition(
            name="manifest.state_updated_at",
            kind="dedicated_registry_state",
            disposition=delete,
            owner="#4191",
            preserve="exact timestamp (nullable) in protected export",
            caller="manifest_sync_service state updates",
        ),
        RegistryFieldDisposition(
            name="uq_manifest_name",
            kind="constraint",
            disposition=delete,
            owner="#4191",
            preserve="uniqueness guaranteed by export index; not recreated",
            caller="initial migration UniqueConstraint(name)",
        ),
        RegistryFieldDisposition(
            name="ix_manifest_id",
            kind="index",
            disposition=delete,
            owner="#4191",
            preserve="lookup index only; dropped before the table per 376",
            caller="initial migration op.create_index",
        ),
        RegistryFieldDisposition(
            name="temporal_executions.manifest_ref",
            kind="shared_execution_column",
            disposition=retain,
            owner="#4189 generic historical reads",
            preserve="exact ref string; read-only lineage fallback after drop",
            caller="executions projection (not a registry writer)",
        ),
        RegistryFieldDisposition(
            name="temporal_execution_sources.manifest_ref",
            kind="shared_execution_column",
            disposition=retain,
            owner="#4189 generic historical reads",
            preserve="exact ref string; read-only lineage fallback after drop",
            caller="executions projection (not a registry writer)",
        ),
        RegistryFieldDisposition(
            name="TemporalWorkflowType.MANIFEST_INGEST",
            kind="shared_execution_enum",
            disposition=retain,
            owner="#4189 generic historical reads",
            preserve="original 'MoonMind.ManifestIngest' type string; required to decode old rows",
            caller="old-release execution rows (immutable evidence)",
        ),
        RegistryFieldDisposition(
            name="WORKFLOW_ENTRY_BY_TYPE[MANIFEST_INGEST]",
            kind="historical_serializer",
            disposition=retain,
            owner="#4189 generic historical reads",
            preserve="'manifest' entry mapping; new launches rejected before the mapping",
            caller="old-release list/detail serialization",
        ),
        RegistryFieldDisposition(
            name="manifest status lineage fallback",
            kind="historical_serializer",
            disposition=retain,
            owner="#4189 generic historical reads",
            preserve="record-attribute fallback in executions router; read-only drain support",
            caller="executions router historical detail path",
        ),
    )


def check_disposition_complete(
    table: tuple[RegistryFieldDisposition, ...] = (),
) -> list[str]:
    """Fail closed unless the disposition covers every registry column.

    Returns a list of problems (empty means complete): missing dedicated
    columns, rows without an owner, deletes without a preserve rule, or
    shared fields incorrectly marked for deletion.
    """
    rows = table or registry_disposition_table()
    problems: list[str] = []
    by_name = {row.name: row for row in rows}
    for column in MANIFEST_TABLE_COLUMNS:
        key = f"manifest.{column}"
        row = by_name.get(key)
        if row is None:
            problems.append(f"disposition missing dedicated column: {key}")
        elif row.disposition != "delete_after_verified_export":
            problems.append(f"dedicated column must delete after export: {key}")
    for row in rows:
        if not row.owner.strip():
            problems.append(f"disposition row has no owner: {row.name}")
        if row.disposition == "delete_after_verified_export" and not row.preserve.strip():
            problems.append(f"delete row has no preserve rule: {row.name}")
        if not row.caller.strip():
            problems.append(f"disposition row has no caller: {row.name}")
    for shared in (
        "temporal_executions.manifest_ref",
        "temporal_execution_sources.manifest_ref",
        "TemporalWorkflowType.MANIFEST_INGEST",
    ):
        row = by_name.get(shared)
        if row is None:
            problems.append(f"disposition missing shared field: {shared}")
        elif row.disposition != "retain_readonly":
            problems.append(f"shared field must be retained readonly: {shared}")
    return problems


def sha256_hex(data: bytes) -> str:
    """Return the hex sha256 digest of raw bytes."""
    return hashlib.sha256(data).hexdigest()


def find_live_writers(present_files: list[str]) -> tuple[str, ...]:
    """Return the retired-writer markers still present in the checkout."""
    found: list[str] = []
    for marker in RETIRED_WRITER_MARKERS:
        for name in present_files:
            if marker in name:
                found.append(marker)
                break
    return tuple(sorted(set(found)))


def downgrade_refusal_message() -> str:
    """Return the irreversible-boundary message the drop migration raises."""
    return (
        f"{DROP_REVISION} is irreversible: the native Manifest registry was "
        f"retired by {ISSUE_REF} (drop landed via sibling #4192). Restore "
        "retained rows from a pre-upgrade protected export instead of "
        "recreating an empty table. Schema downgrade alone cannot recreate "
        "lost content: use verified protected restoration or forward repair."
    )


def check_migration_text_self_contained(
    *,
    drop_migration_text: str,
    initial_migration_text: str,
) -> list[str]:
    """Check the migration chain stays self-contained without rewrites.

    The drop migration must only drop ``ix_manifest_id`` and the
    ``manifest`` table and must raise (not recreate) on downgrade; neither
    text may import soon-deleted runtime modules; the initial revision must
    create the table inline. Applied business outcomes are never rewritten.
    """
    problems: list[str] = []
    for forbidden in FORBIDDEN_MIGRATION_IMPORTS:
        if forbidden in drop_migration_text:
            problems.append(f"drop migration imports removed runtime module: {forbidden}")
        if forbidden in initial_migration_text and "op.create_table('manifest'" not in (
            initial_migration_text
        ):
            problems.append(
                f"initial migration depends on removed runtime module: {forbidden}"
            )
    if 'op.drop_table("manifest")' not in drop_migration_text and (
        "op.drop_table('manifest')" not in drop_migration_text
    ):
        problems.append("drop migration does not drop the manifest table")
    if "raise RuntimeError" not in drop_migration_text:
        problems.append("drop migration downgrade must fail closed with RuntimeError")
    if "op.create_table('manifest'" not in initial_migration_text:
        problems.append("initial migration must create the manifest table inline")
    return problems


#: What a refused drop asks the operator to do, using only ``pg_dump`` and
#: ``psql`` from the deployment's own PostgreSQL tooling.
_PRESERVE_THEN_APPROVE = (
    "after writers stop, snapshot the manifest table with a single-transaction "
    "pg_dump into operator-owned protected storage, restore it into an "
    "isolated database with psql and count its rows, then re-run with "
    f"{DRAIN_APPROVAL_ENV_VAR}={DRAIN_APPROVAL_VALUE} and "
    f"{DRAIN_EXPORT_ROW_COUNT_ENV_VAR}=<verified row count>."
)


def render_operator_procedure() -> str:
    """Render the bounded preservation/verification procedure.

    The procedure uses the deployment's own ``pg_dump``/``psql`` and an
    operator-owned protected directory; it creates no new export database or
    secret system and never uploads snapshots to public GitHub, normal
    broadly visible artifacts, or Temporal history.
    """
    return "\n".join(
        [
            f"{MANIFEST_REGISTRY_MIGRATION_CONTRACT} ({ISSUE_REF})",
            "",
            "1. Stop writers: retire producers/callbacks per #4188 inventory and",
            "   confirm no RETIRED_WRITER_MARKERS remain in the release under test.",
            "2. Snapshot consistently: single-transaction pg_dump of the manifest",
            "   table AFTER writers stop, into an operator-owned directory (mode",
            "   0700, files 0600) preserving original access restrictions and",
            "   retention. Never upload snapshots to public GitHub, normal broadly",
            "   visible artifacts, or Temporal history.",
            "3. Verify usable restore: restore the snapshot with psql into an",
            "   isolated database without touching newer work and confirm its",
            "   manifest row count. File creation alone is not verification.",
            f"4. Apply {DROP_REVISION} with",
            f"   {DRAIN_APPROVAL_ENV_VAR}={DRAIN_APPROVAL_VALUE} and",
            f"   {DRAIN_EXPORT_ROW_COUNT_ENV_VAR} set to the verified row",
            "   count; a live count that differs refuses the drop. Existing",
            "   migration locking/versioning plus the transaction-scoped advisory",
            "   lock serializes concurrent migrators.",
            "5. After the boundary, rollback uses the matching compatible release",
            "   plus verified protected restoration or forward repair; schema",
            "   downgrade alone raises RuntimeError and stops actionably.",
        ]
    )


def stable_disposition_digest() -> str:
    """Return a stable digest of the disposition table for evidence refs."""
    canonical = json.dumps(
        [
            {
                "name": row.name,
                "kind": row.kind,
                "disposition": row.disposition,
                "owner": row.owner,
            }
            for row in registry_disposition_table()
        ],
        sort_keys=True,
    )
    return sha256_hex(canonical.encode("utf-8"))


def build_sanitized_supply_package() -> dict[str, Any]:
    """Build the sanitized schema/upgrade/restore evidence handoff.

    This is the durable, reviewable supply package for #4189 and the
    integration gate (REQ-9): disposition identity, drop-migration
    identity/ancestry, drain-approval switch name, and the operator
    procedure text. It carries counts, names, refs, and digests only —
    never registry YAML bytes, state payloads, or other sensitive values —
    and performs no database, Temporal, or network I/O, so preparing the
    handoff never applies a production migration/export/delete. The
    owning workflow controls any external publication of this package.
    """
    table = registry_disposition_table()
    return {
        "contract": MANIFEST_REGISTRY_MIGRATION_CONTRACT,
        "issue": ISSUE_REF,
        "disposition_digest": stable_disposition_digest(),
        "disposition_rows": len(table),
        "drop_revision": DROP_REVISION,
        "drop_parent_revision": DROP_PARENT_REVISION,
        "drop_child_revision": DROP_CHILD_REVISION,
        "drain_approval_env_var": DRAIN_APPROVAL_ENV_VAR,
        "drain_export_row_count_env_var": DRAIN_EXPORT_ROW_COUNT_ENV_VAR,
        "operator_procedure": render_operator_procedure(),
    }


def is_drain_approved(environ: dict[str, str] | None = None) -> bool:
    """Return True when the operator approved destructive registry removal.

    Approval is explicit and out-of-band: the operator exports and verifies
    the protected snapshot, evaluates the drain gate to ``proceed``, then
    sets :data:`DRAIN_APPROVAL_ENV_VAR` to :data:`DRAIN_APPROVAL_VALUE` for
    the single migration invocation. Anything else (missing, blank, or any
    other value) is not approval.
    """
    source = environ if environ is not None else os.environ
    return str(source.get(DRAIN_APPROVAL_ENV_VAR, "")).strip() == DRAIN_APPROVAL_VALUE


def read_drain_export_row_count(
    environ: dict[str, str] | None = None,
) -> int | None:
    """Read the verified export row count bound to the drain approval.

    Returns the integer from :data:`DRAIN_EXPORT_ROW_COUNT_ENV_VAR`, or
    ``None`` when missing/unparseable so callers fail closed instead of
    treating an unbound approval as sufficient.
    """
    source = environ if environ is not None else os.environ
    raw = str(source.get(DRAIN_EXPORT_ROW_COUNT_ENV_VAR, "")).strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value >= 0 else None


def require_registry_drop_approval(
    *,
    manifest_row_count: int | None,
    approved: bool,
    export_row_count: int | None = None,
    export_verified: bool = False,
    environ: dict[str, str] | None = None,
) -> None:
    """Enforce the migration-execution drain gate for the forward drop.

    The Alembic ``upgrade()`` calls this before dropping. Fresh installs (zero rows) proceed without
    approval. A populated or unobservable table requires both explicit
    operator approval AND the verified export evidence bound to that
    approval: ``export_verified`` must be true and ``export_row_count``
    must be present, matching the live ``manifest_row_count`` when the
    live count is observable. A bare ``approved=True`` with no export
    evidence, or with a count that disagrees with the live set, raises
    ``RuntimeError`` instead of silently dropping unpreserved rows. When
    ``export_row_count``/``export_verified`` are omitted, the approval
    binding is read from the environment
    (:data:`DRAIN_EXPORT_ROW_COUNT_ENV_VAR`; verified is implied by the
    approval workflow only when an explicit count is bound).
    """
    if manifest_row_count is not None and manifest_row_count <= 0:
        return
    bound_export_count = export_row_count
    bound_verified = export_verified
    if bound_export_count is None and environ is not None:
        bound_export_count = read_drain_export_row_count(environ)
        # An explicitly bound count implies the operator completed the
        # verified-export workflow for this invocation; without a bound
        # count there is no evidence to imply.
        bound_verified = bound_verified or bound_export_count is not None
    if approved:
        if not bound_verified or bound_export_count is None:
            raise RuntimeError(
                f"{DROP_REVISION} refused: drain approval is not bound to a "
                f"verified export; {_PRESERVE_THEN_APPROVE} A stale approval "
                "or rows changed after the snapshot must never authorize the "
                "drop."
            )
        if manifest_row_count is not None and bound_export_count != manifest_row_count:
            raise RuntimeError(
                f"{DROP_REVISION} refused: live manifest row count "
                f"({manifest_row_count}) disagrees with the verified export "
                f"({bound_export_count}); re-snapshot consistently after "
                "stopping writers, re-verify, and re-approve before dropping."
            )
        return
    if manifest_row_count is None:
        raise RuntimeError(
            f"{DROP_REVISION} refused: manifest registry row count is "
            f"unobservable; {_PRESERVE_THEN_APPROVE} Concurrent migrators stay "
            "serialized by existing migration locking/versioning."
        )
    raise RuntimeError(
        f"{DROP_REVISION} refused: manifest registry still holds "
        f"{manifest_row_count} row(s); {_PRESERVE_THEN_APPROVE} Concurrent "
        "migrators stay serialized by existing migration locking/versioning."
    )
