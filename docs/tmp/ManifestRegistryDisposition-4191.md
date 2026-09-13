# Manifest Registry Disposition and Preservation (MR4, #4191)

Parent: MoonLadderStudios/MoonMind#4187. Plan MR4 in the pinned removal plan
(`docs/tmp/ManifestSystemRemovalPlan.md` §4). This is temporary execution
scaffolding under `docs/tmp/`; delete or archive it when the MR4
implementation and cutover obligations are resolved.

**Authoritative implementation:** `moonmind/gates/manifest_registry_migration_4191.py`
(disposition table, export envelopes, drain gate, irreversible boundary) and
`tools/manifest_registry_export_4191.py` (protected export/verify rehearsal).
This note describes the procedure; on any conflict the tested module wins.

## 1. Disposition

`registry_disposition_table()` marks every dedicated `manifest` column,
constraint, and index `delete_after_verified_export` with its removed
writer/caller, and every shared execution column/enum/serializer
(`temporal_executions.manifest_ref`,
`temporal_execution_sources.manifest_ref`,
`TemporalWorkflowType.MANIFEST_INGEST`, the `manifest` entry mapping, the
record-attribute lineage fallback) `retain_readonly` owned by #4189's
generic historical read path. `check_disposition_complete()` fails closed on
missing columns, missing owners, or shared fields marked for deletion.

## 2. Preservation procedure

Rendered by `render_operator_procedure()`: stop writers per the #4188
inventory, take a consistent snapshot after writers stop (or a proven final
reconciliation), export with `tools/manifest_registry_export_4191.py` into
an operator-owned directory (0700/0600, original access restrictions and
retention preserved), and verify a usable restore — row counts plus
content/digest checks — before applying `376_drop_manifest_registry_4192`.
Historical YAML/state is potentially sensitive: never commit exports, never
upload to public GitHub, normal artifacts, or Temporal history, and never
print content bytes (reports carry counts, names, refs, and digests only).
No new export database or secret system is created.

## 3. Drain gate and irreversible boundary

`evaluate_registry_drain()` refuses destructive application while live
writer markers remain, the export is unverified or partial, incompatible
old code is present, or a competing migrator holds the migration lock
(existing locking/versioning serializes migrators). Before the boundary,
rollback uses the matching compatible release; after it, schema downgrade
alone raises `RuntimeError` and stops actionably — recovery requires
verified protected restoration or forward repair without overwriting
unrelated newer executions, credentials, or shared database changes.

## 4. Evidence

Sanitized schema/upgrade/restore evidence (disposition digest, sanitized
export reports, drain decisions) is supplied to #4189 and the integration
gate. Hermetic unit coverage lives in
`tests/unit/config/test_manifest_registry_migration_4191.py`; real
supported PostgreSQL migration coverage stays deployment-owned and is
distinguished from SQLite/helper tests. No production migration, export, or
delete is performed merely because the issue exists.
