# Data Model: Managed Runtime Skill Projection

## Resolved Skill Snapshot

- **Represents**: Immutable selected skill set supplied to materialization.
- **Key fields**:
  - `snapshot_id`: stable snapshot identity.
  - `resolved_at`: timestamp for the resolved snapshot.
  - `skills[]`: selected skill entries only.
  - `manifest_ref`: optional artifact ref for the resolved manifest.
- **Validation rules**:
  - Materialization must not add unselected skills.
  - Materialization must not re-resolve source folders.
  - Snapshot identity must be preserved in `_manifest.json`.

## Resolved Skill Entry

- **Represents**: One selected skill in the snapshot.
- **Key fields**:
  - `skill_name`
  - `version`
  - `content_ref`
  - `content_digest`
  - `provenance.source_kind`
- **Validation rules**:
  - Skill names become path components only after existing model validation and safe materialization.
  - Missing content payloads must not produce misleading full skill bodies.

## Active Backing Store

- **Represents**: MoonMind-owned run-scoped directory containing the active materialized snapshot.
- **Key fields / paths**:
  - backing directory path
  - `_manifest.json`
  - selected skill directories
- **Validation rules**:
  - Directory is owned by runtime setup, not by checked-in repo content.
  - Contents are derived from the supplied snapshot only.
  - Unselected repo/local skills are absent.

## Runtime-Visible Projection

- **Represents**: `.agents/skills` path visible to the managed runtime.
- **Key fields / paths**:
  - visible path: `.agents/skills`
  - target/backing path: active backing store
  - compatibility mirrors, when present, target the same backing path
- **Validation rules**:
  - Existing non-symlink path fails before runtime launch.
  - Drifted links are corrected or rejected by validation.
  - Projection must be established before runtime launch.

## Active Manifest

- **Represents**: Compact runtime-visible summary of active skill projection.
- **Required fields**:
  - `snapshot_id`
  - `runtime_id`
  - `materialization_mode`
  - `visible_path`
  - `backing_path`
  - `resolved_at`
  - `skills[]` with `name`, `version`, `source_kind`, `content_ref`, and `content_digest`
- **Validation rules**:
  - Lives at `.agents/skills/_manifest.json`.
  - Does not embed full skill bodies.
  - Lists only selected skills.

## State Transitions

1. `ResolvedSkillSet` supplied to materialization.
2. Active backing store is created or refreshed for the run.
3. `_manifest.json` and selected skill files are written into the active store.
4. `.agents/skills` is projected to the active store.
5. Runtime instruction summary references the active visible path.
6. Incompatible path or unreadable skill content fails before runtime launch.
