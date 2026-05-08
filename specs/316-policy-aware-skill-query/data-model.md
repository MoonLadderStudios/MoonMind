# Data Model: Policy-Aware Skill Query

## Entity: SkillsOnDemandQueryRequest

Represents a managed-runtime request to discover Skill metadata.

Fields:
- `query`: required non-blank search text after trimming.
- `runtime_id`: optional runtime identifier used for runtime compatibility summaries.
- `current_snapshot_ref`: optional compact reference to the active snapshot.
- `max_results`: bounded integer result limit.
- `active_snapshot`: optional compact resolved snapshot context for boundary calls that already have it available.

Validation:
- `query` must not be blank when enabled query mode is used.
- `max_results` must remain within the accepted public bounds.
- Unsupported or unknown runtime/snapshot context must not broaden visibility.

## Entity: SkillCatalogSearchResult

Represents one metadata-only query result.

Fields:
- `name`: Skill name.
- `title`: optional display title.
- `description`: optional short description.
- `latest_version`: optional latest version string.
- `source_kind`: one of `built_in`, `deployment`, `repo`, or `local`.
- `supported_runtimes`: optional list of runtime identifiers.
- `eligible`: boolean policy/runtime eligibility for the caller.
- `in_current_snapshot`: boolean indicating active snapshot membership.
- `eligibility_summary`: optional compact diagnostic text.

Rules:
- Must not include Skill body text.
- Must not include content refs, source paths, artifact ids, checksums, or unrestricted read handles.
- Must remain compact and serializable through workflow/activity payloads.

## Entity: SkillsOnDemandQueryResult

Represents the whole query outcome.

Fields:
- `status`: `ok` or `denied`.
- `code`: optional denial or validation code.
- `message`: human-readable compact summary.
- `results`: bounded list of `SkillCatalogSearchResult`.
- `metadata`: compact outcome metadata such as result count and whether the query was denied.

State transitions:
- Disabled feature: request returns `denied` with `feature_disabled` and no results.
- Invalid request: request returns `denied` with validation code and no catalog results.
- Enabled valid query: request returns `ok` with bounded metadata-only results.

## Entity: Active Snapshot Context

Represents the immutable Skill snapshot currently visible to the managed runtime.

Fields used by this story:
- `snapshot_id`
- `skills[].skill_name`

Rules:
- Query may read compact membership context but must not mutate or materialize snapshots.
