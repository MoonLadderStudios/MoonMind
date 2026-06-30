# Temporal Visibility and UI Query Model

Status: Active design guidance
Owners: MoonMind Platform
Last updated: 2026-06-30

This document defines MoonMind's Temporal Visibility query model and the custom Search Attribute budget we use for workflow-list, dashboard, and operational views.

It is intentionally provider-neutral. External integrations such as Omnigent, Jules, Codex Cloud, OpenClaw, or future execution substrates must not add provider-specific indexed fields unless the dimension is promoted to a generic MoonMind query concept first.

---

## 1. Goals

Temporal Visibility is used for bounded, operationally important workflow queries:

- list the current user's workflow executions;
- filter by owner, entry type, status, repository, integration, schedule, runtime, and skill when supported;
- sort by stable built-in or registered indexed fields;
- compute facets and metrics where exact counts are operationally safe;
- recover gracefully when optional Search Attributes are absent during migration.

Temporal Visibility is **not** the source of truth for full workflow details, step ledgers, artifacts, prompts, raw logs, provider-native metadata, or external session internals.

---

## 2. Query ownership boundaries

| Concern | Owner | Query surface |
|---|---|---|
| Workflow orchestration | Temporal | Workflow ids, run ids, status, visibility queries |
| MoonMind workflow lifecycle | MoonMind workflow code | `mm_state`, `mm_updated_at`, `mm_started_at` |
| User/security scoping | MoonMind API/workflow start path | `mm_owner_type`, `mm_owner_id` |
| Workflow list grouping | MoonMind API/workflow start path | `mm_entry`, `WorkflowType` |
| Repository/integration filters | MoonMind API/workflow start path | `mm_repo`, `mm_integration` |
| Runtime/skill facets | MoonMind API/workflow start path and workflow lifecycle repair | `mm_target_runtime`, `mm_target_skill` |
| Schedules/deferred starts | MoonMind API/Temporal start path | `mm_scheduled_for` |
| Full evidence | MoonMind artifacts | Artifact refs, not Search Attributes |
| Provider-native session metadata | Provider adapter / diagnostics | Mapping tables and artifacts, not Search Attributes |

---

## 3. Search Attribute naming rules

All MoonMind-owned Search Attributes follow these rules:

- prefix with `mm_` unless the attribute belongs to a non-MoonMind operational workflow family that predates this convention;
- use lowercase snake_case for new MoonMind workflow attributes;
- store bounded values only;
- store no secrets;
- store no large free text;
- store no raw prompts, transcripts, stack traces, logs, or provider error bodies;
- store no step-ledger rows or attempt history;
- store no display-only user prose;
- add documentation here before adding a new Search Attribute to bootstrap.

---

## 4. Current registered custom Search Attributes

MoonMind currently registers 20 custom Search Attributes in the Temporal namespace bootstrap. Exactly 10 are `Keyword`, which is the important SQL Visibility keyword ceiling guarded by integration tests.

### 4.1 Keyword attributes — current 10-slot budget

| Name | Type | Owner | Keep? | Purpose |
|---|---:|---|---|---|
| `mm_entry` | Keyword | API/workflow start path | Keep | Execution category for UI/query surfaces. |
| `mm_owner_id` | Keyword | API/workflow start path | Keep | Principal identifier for user/security scoping. |
| `mm_owner_type` | Keyword | API/workflow start path | Keep | `user`, `system`, or `service` owner class. |
| `mm_state` | Keyword | Workflow lifecycle logic | Keep | Exact MoonMind lifecycle state. |
| `mm_repo` | Keyword | API/workflow start path | Keep if repo filtering remains core | Stable bounded repo identifier. |
| `mm_integration` | Keyword | API/workflow start path | Conditional keep | Integration-centric workflow filter/facet. |
| `AgentRunId` | Keyword | Managed session workflows | Re-evaluate | Operational managed-session correlation. |
| `RuntimeId` | Keyword | Managed session workflows | Re-evaluate | Managed-session runtime id; overlaps conceptually with generic runtime dimensions. |
| `SessionId` | Keyword | Managed session workflows | Conditional keep | Direct Temporal lookup/correlation for managed sessions. |
| `SessionStatus` | Keyword | Managed session / operational workflows | Strongest managed-session keep | Managed-session and cleanup/reconcile operational status. |

### 4.2 Non-Keyword attributes

| Name | Type | Owner | Purpose |
|---|---:|---|---|
| `mm_updated_at` | Datetime | Workflow lifecycle logic | Canonical user-visible recency/update time. |
| `mm_started_at` | Datetime | Workflow lifecycle logic | Semantic “real work began” timestamp distinct from Temporal start time. |
| `mm_scheduled_for` | Datetime | API/start path | Expected/deferred start time. |
| `mm_target_runtime` | KeywordList | API/workflow start path; workflow repair | One-item list containing canonical runtime id for filtering/facets. |
| `mm_target_skill` | KeywordList | API/workflow start path; workflow repair | One-item list containing primary skill id/slug/name for filtering/facets. |
| `mm_title` | KeywordList | Workflow start/lifecycle logic | Tokenized title word search. Must be materialized before relying on title search. |
| `mm_has_dependencies` | Bool | Dependency lifecycle | Dependency presence signal. Re-evaluate if unused by production queries. |
| `mm_dependency_count` | Int | Dependency lifecycle | Bounded dependency count. Re-evaluate if unused by production queries. |
| `SessionEpoch` | Int | Managed session workflows | Managed-session clear/reset/epoch correlation. |
| `IsDegraded` | Bool | Managed session / operational workflows | Operational degraded marker. |

---

## 5. Required workflow-list attributes

These are required for the primary Temporal-backed workflow list.

| Name | Type | Required | Lifecycle owner | Update rule | Notes |
|---|---:|---:|---|---|---|
| `mm_owner_type` | Keyword | Yes | API/workflow start path | Set at start; immutable in v1 | `user`, `system`, or `service`. |
| `mm_owner_id` | Keyword | Yes | API/workflow start path | Set at start; immutable in v1 | Principal identifier. |
| `mm_state` | Keyword | Yes | Workflow lifecycle logic | Update on every domain-state transition | Exact MoonMind lifecycle state. |
| `mm_updated_at` | Datetime | Yes | Workflow lifecycle logic | Update on meaningful user-visible mutation | Default recency/sort key. |
| `mm_entry` | Keyword | Yes | Workflow start path | Set at start; immutable | Execution category for UI/query surfaces. |

### 5.1 `mm_state` value set

Allowed values for v1:

```text
scheduled
initializing
waiting_on_dependencies
planning
awaiting_slot
executing
awaiting_external
proposals
finalizing
no_commit
completed
failed
canceled
```

Rules:

- `mm_state` must be set immediately on workflow start.
- Terminal mapping must remain consistent with MoonMind `closeStatus`:
  - `no_commit` / `completed` -> `completed`
  - `failed` / `terminated` / `timed_out` -> `failed`
  - `canceled` -> `canceled`
- Graceful workflow cancellation is represented by workflow-owned terminal `mm_state=canceled` / `closeStatus=canceled` even when raw Temporal `ExecutionStatus` is `Completed` because the workflow finalized and returned normally.

### 5.2 `mm_owner_type` value set

Allowed values for v1:

```text
user
system
service
```

Rules:

- `mm_owner_type` and `mm_owner_id` must always be populated together.
- `unknown` is not an allowed owner type.
- Standard end-user views should only show executions where `mm_owner_type = user` and `mm_owner_id` matches the authenticated principal unless a product surface explicitly says otherwise.

### 5.3 `mm_entry` value set

Allowed values for v1:

```text
user_workflow
manifest
provider_profile
```

Rules:

- `mm_entry` is required.
- `mm_entry` should not be inferred by parsing raw workflow type strings in UI code.
- Compatibility surfaces may collapse multiple `entry` values into one broader product grouping, but the exact value must remain queryable.

---

## 6. Optional workflow-list attributes

| Name | Type | Required | When to set | Notes |
|---|---:|---:|---|---|
| `mm_repo` | Keyword | No | Repo-scoped executions when filtering is needed | Stable bounded repo identifier. |
| `mm_integration` | Keyword | No | Integration-centric execution where filtering is useful | Examples: `jules`, `github`, `openclaw`, `omnigent`. |
| `mm_target_runtime` | KeywordList | No | Workflow start path when canonical runtime is known; workflow lifecycle logic if it resolves later | One-item list containing canonical runtime ID such as `codex_cli`, `claude_code`, `gemini_cli`, `codex_cloud`, `jules`, or a generic provider-backed runtime id. Never a display label, model, profile name, prompt, or free text. |
| `mm_target_skill` | KeywordList | No | Workflow start path when primary skill is known; workflow lifecycle logic if it resolves later | One-item list containing singular primary skill slug/name/id used by `targetSkill`; future multi-skill faceting requires a separate explicitly documented attribute. |
| `mm_scheduled_for` | Datetime | No | Delayed start / schedule-backed execution | Queryable expected start time. |
| `mm_title` | KeywordList | No | Workflow start/lifecycle title materialization | Tokenized title word search. Verify materialization before relying on this filter. |

Runtime and skill Search Attributes are optional during migration. The API must confirm that `mm_target_runtime` and `mm_target_skill` are registered as `KeywordList` before issuing Temporal Visibility queries that reference them. When they are unavailable, runtime/skill filters are not authoritative and facets return degraded metadata rather than failing the list page.

Runtime/skill filters use Temporal KeywordList membership queries:

```text
mm_target_runtime = "codex_cli"
mm_target_skill = "jira-implement"
```

They are not sortable through Temporal Visibility because SQL Visibility rejects `ORDER BY` for KeywordList fields.

Blank or unknown runtime/skill values are omitted, not written as empty strings or placeholders. Existing executions that cannot be updated in Temporal Visibility remain blank/unknown for these dimensions; historical facet counts must not be faked from projection-only display values. Open executions may repair these attributes from canonical parameters or workflow-owned metadata when they next perform a bounded lifecycle Search Attribute update.

---

## 7. Exact filters for Temporal-backed queries

Allowed exact-match filters for Temporal-managed list queries:

- `workflowType`
- `ownerType`
- `ownerId`
- `state`
- `entry`
- `repo`
- `integration`
- `targetRuntime` when `mm_target_runtime` is registered as `KeywordList`
- `targetRuntimeIn` / `targetRuntimeNotIn` when `mm_target_runtime` is registered as `KeywordList`
- `targetSkillIn` / `targetSkillNotIn` when `mm_target_skill` is registered as `KeywordList`

### 7.1 Filter priorities

Required now:

- `workflowType`
- `ownerId`
- `ownerType`
- `state`
- `entry`

Required when product surface uses it:

- `repo`
- `integration`
- `targetRuntime`
- `targetSkill`

Do not add a new Search Attribute merely because a display field exists. A field should consume Search Attribute budget only when MoonMind needs server-side filtering, sorting, faceting, metrics, or operational lookup.

---

## 8. Sort fields

Supported Temporal-backed sort fields must use built-in fields or registered sortable Search Attributes.

Sortable examples:

- `WorkflowId`
- `StartTime`
- `CloseTime`
- `mm_state`
- `mm_repo`
- `mm_integration`
- `mm_scheduled_for`
- `mm_updated_at` when used by supported query path

Not sortable through Temporal Visibility:

- `mm_target_runtime`
- `mm_target_skill`
- `mm_title`
- any KeywordList field
- progress derived from step ledger or workflow queries

KeywordList filters may be authoritative while sort remains current-page/projection-only.

---

## 9. Count and facet degradation

List row retrieval is the primary payload. Counts and facets are enrichments.

If `list_workflows` succeeds and `count_workflows` fails or times out, the API should return rows with:

```json
{
  "count": null,
  "countMode": "estimated_or_unknown",
  "degradedCount": true
}
```

If an optional facet Search Attribute is unavailable, the facet endpoint should return degraded metadata, not 503:

```json
{
  "items": [],
  "countMode": "estimated_or_unknown",
  "source": "current_page_fallback"
}
```

A missing optional Search Attribute is not the same as an exact zero-result query. Avoid returning `countMode="exact"` for a result that was produced by skipping unavailable authoritative filters.

---

## 10. Provider-neutral indexing rules

External providers and runtime substrates must not add provider-specific Search Attributes by default.

Do not add provider-specific attributes such as:

```text
mm_omnigent_session_id
mm_omnigent_agent_id
mm_omnigent_host_type
mm_omnigent_harness
mm_jules_session_id
mm_codex_cloud_thread_id
mm_external_provider_run_id
```

Use generic dimensions when they serve a cross-provider product query:

```text
mm_integration
mm_target_runtime
mm_target_skill
mm_repo
mm_state
mm_owner_id
mm_owner_type
mm_entry
```

Provider-native details belong in:

- provider-specific durable mapping tables;
- `AgentRunResult.metadata`;
- diagnostics artifacts;
- artifact links;
- step/run evidence;
- local projection database columns if needed for non-Temporal API reads.

### 10.1 Omnigent-specific posture

Omnigent integration must not make Temporal Visibility an Omnigent session browser.

Use:

```text
mm_integration = "omnigent"        # only if integration-level filtering/faceting is needed
mm_target_runtime = ["omnigent"]   # or another canonical provider-backed runtime id, if product uses runtime filtering
mm_target_skill = ["<primary skill>"]
```

Store Omnigent-native fields outside Temporal Search Attributes:

```text
omnigent_session_id
omnigent_agent_id
omnigent_agent_name
omnigent_host_type
omnigent_endpoint_ref
first_message_state
first_message_digest
sse_events_ref
final_snapshot_ref
```

Preferred locations:

```text
omnigent_external_runs
AgentRunResult.metadata
diagnostics artifacts
capture manifests
step/run evidence links
```

This keeps the custom Search Attribute budget useful for MoonMind-wide workflow queries and leaves a future MoonMind API / Omnigent Server merger path open without coupling Temporal Visibility to Omnigent's internal session model.

---

## 11. Deferred Search Attributes

These are intentionally **not** required in v1:

- `mm_stage`
- `mm_error_category`
- `mm_external_session_id`
- `mm_external_provider_run_id`
- provider-specific session ids
- child-workflow/activity-level indexing fields
- free-text search attributes
- unbounded tag arrays
- multi-skill global facet arrays

If one becomes necessary, update this document first rather than adding it ad hoc in code.

---

## 12. Memo registry

### 12.1 Required Memo fields

| Field | Required | Purpose | Rules |
|---|---:|---|---|
| `title` | Yes | Human-readable execution title | Small, display-safe, mutable. |
| `summary` | Yes | Compact current summary for list/detail surfaces | Small, display-safe, mutable. |

### 12.2 Optional Memo fields

| Field | Required | Purpose | Rules |
|---|---:|---|---|
| `input_ref` | No | Safe reference to input artifact | Reference only. |
| `manifest_ref` | No | Safe reference for manifest-driven workflows | Reference only. |
| `error_category` | No | Debug/detail classification for failures | Display/debug only. |
| `entry_label` | No | Optional human-friendly display label | Small, bounded. |
| `progress_hint` | No | Compact execution-level progress hint when useful | Small, bounded, never a step ledger. |

Memo rules:

- keep Memo small and human-readable;
- never store secrets, full prompts, manifests, or large payloads in Memo;
- never store step-ledger rows, attempts, `checks[]`, or long error bodies in Memo;
- Memo is for display metadata, not filtering;
- list views should rely on `title` and `summary`, not raw artifact payloads.

---

## 13. Operational review checklist for new Search Attributes

Before adding a new Search Attribute, answer these questions in the PR description:

1. Which user or operator query requires server-side Temporal filtering/faceting/sorting?
2. Is the dimension generic across providers, or provider-specific?
3. Can the value be bounded and secret-free?
4. Is the type correct for intended queries?
5. Does it consume one of the 10 `Keyword` slots?
6. If it consumes a `Keyword` slot, which current Keyword becomes less important?
7. Can the same need be met by projection DB, artifact metadata, or provider mapping table instead?
8. Is the value set documented here?
9. Is bootstrap idempotent and type-safe?
10. Are missing/unregistered-attribute states handled without taking down the Workflows list?

---

## 14. Recommended next cleanup

The current registry is serviceable, but the managed-session Keyword fields should be periodically reviewed:

```text
AgentRunId
RuntimeId
SessionId
SessionStatus
```

Recommended stance:

- Keep `SessionStatus` if managed-session operations rely on Temporal Web/Visibility.
- Keep `SessionId` only if direct Temporal lookup by session id is operationally common.
- Re-evaluate `AgentRunId` and `RuntimeId` because app DB/store lookups and generic runtime dimensions may cover those use cases without consuming Keyword slots.
- Re-evaluate `mm_has_dependencies` and `mm_dependency_count` if no production query uses them.

Do not replace these with provider-specific fields. Free slots should be reserved for future generic MoonMind workflow dimensions.
