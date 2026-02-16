# Data Model: Unified Agent Skills Directory

## Enum: SkillSourceType

Location type used to fetch one skill artifact version.

- `git`
- `object_bundle`
- `local_mirror`

## Enum: MaterializationStatus

Run-level status of resolver/materializer execution.

- `pending`
- `resolving`
- `fetching`
- `verifying`
- `activating`
- `ready`
- `failed`

## Value Object: SkillRegistryEntry

Canonical metadata record for one immutable skill version.

- **Fields**:
  - `skill_name` (string)
  - `version` (string)
  - `source_type` (SkillSourceType)
  - `source_uri` (string)
  - `content_hash` (string, sha256 hex)
  - `signature` (string | null)
  - `compatibility_notes` (list[string])
  - `enabled` (bool)
- **Validation rules**:
  - `skill_name` must match Agent Skills naming constraints and folder name.
  - `content_hash` must be non-empty and unique per `skill_name + version`.
  - Disabled entries cannot be selected for new runs.

## Value Object: RunSkillSelection

Effective skill selection resolved for one run.

- **Fields**:
  - `run_id` (UUID/string)
  - `selected_skills` (list[SkillSelectionItem])
  - `selection_source` (string; e.g., `job_override|queue_profile|global_default`)
  - `resolved_at` (timestamp)
- **Validation rules**:
  - `selected_skills` cannot be empty.
  - Selected skill names must be unique.

## Value Object: SkillSelectionItem

Selected skill descriptor used by materializer.

- **Fields**:
  - `skill_name` (string)
  - `version` (string)
  - `required` (bool)
- **Validation rules**:
  - `skill_name + version` must exist in SkillRegistryEntry.

## Value Object: SkillCacheRecord

Immutable local cache record for a verified skill artifact.

- **Fields**:
  - `content_hash` (string)
  - `cache_path` (path)
  - `verified` (bool)
  - `verified_at` (timestamp | null)
  - `source_uri` (string)
- **Validation rules**:
  - `cache_path` must be content-addressed and read-only after verification.
  - `verified=true` required before linking into active workspace.

## Value Object: RunSkillWorkspace

Filesystem paths for one run’s active skills.

- **Fields**:
  - `run_root` (path; `/work/runs/<run_id>`)
  - `skills_active_path` (path; `<run_root>/skills_active`)
  - `codex_adapter_path` (path; `<run_root>/.agents/skills`)
  - `gemini_adapter_path` (path; `<run_root>/.gemini/skills`)
- **Validation rules**:
  - Both adapter paths must be symlinks to `skills_active_path`.
  - `skills_active_path` entries must be symlinks to immutable cache paths.

## Value Object: MaterializationAuditEvent

Structured event payload for observability and debugging.

- **Fields**:
  - `run_id` (string)
  - `status` (MaterializationStatus)
  - `selected_skills` (list[string])
  - `resolved_versions` (dict[string, string])
  - `resolved_hashes` (dict[string, string])
  - `errors` (list[string])
  - `duration_ms` (int)
- **Validation rules**:
  - On `ready`, `errors` must be empty.
  - On `failed`, at least one actionable error message must be present.

## State Transitions

1. `pending` -> `resolving` when run policy is loaded.
2. `resolving` -> `fetching` when registry entries are resolved.
3. `fetching` -> `verifying` when artifacts are present in cache candidate paths.
4. `verifying` -> `activating` when hashes/signatures pass.
5. `activating` -> `ready` when `skills_active` and adapter symlinks pass invariant checks.
6. Any non-terminal state -> `failed` on registry miss, integrity mismatch, invalid metadata, or symlink invariant failure.
