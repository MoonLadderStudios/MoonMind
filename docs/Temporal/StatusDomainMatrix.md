# Status Domain Matrix

Status: Normative
Owners: MoonMind Platform
Last updated: 2026-07-01
Source issue: MM-1080; preserves MM-1073 traceability

This document is the canonical cross-domain matrix for MoonMind status tokens. It names the owner, value set, storage/API surface, and conversion boundary for each status domain so downstream cleanup can make domain-specific changes without treating similarly named tokens as interchangeable.

The former workflow status model remains only an archival pointer. Do not use archived status notes as the source for new status decisions.

## Domain Matrix

| Domain | Owner | Canonical value set | Storage/API surface | Conversion notes |
| --- | --- | --- | --- | --- |
| Workflow lifecycle state | MoonMind workflow lifecycle logic | `scheduled`, `initializing`, `waiting_on_dependencies`, `planning`, `awaiting_slot`, `executing`, `awaiting_external`, `proposals`, `finalizing`, `no_commit`, `completed`, `failed`, `canceled` | Temporal Search Attribute `mm_state`; API `state`; projection `TemporalExecutionRecord.state`; enum `MoonMindWorkflowState` | This is the exact domain state for workflow filtering. It must align with terminal `closeStatus`; compatibility dashboard groups must not redefine it. |
| Temporal close status | Temporal lifecycle projection and MoonMind invariant checks | `completed`, `failed`, `canceled`, `terminated`, `timed_out`, `continued_as_new` | API `closeStatus`; projection `TemporalExecutionRecord.close_status`; enum `TemporalExecutionCloseStatus` | Raw Temporal close concepts are terminal-only. `terminated` and `timed_out` map to API `temporalStatus=failed`; `continued_as_new` is not exposed as a distinct `temporalStatus` in the current API shape. |
| `temporalStatus` | Executions API adapter | `running`, `completed`, `failed`, `canceled` | API JSON field `temporalStatus`; schema `ExecutionModel.temporalStatus` | Derived from `closeStatus`: `null -> running`, `completed -> completed`, `canceled -> canceled`, and failure-like close statuses -> `failed`. It is a simplified client-facing lifecycle value, not a workflow-owned state machine. |
| Step ledger status | `MoonMind.UserWorkflow` step ledger and dashboard | `pending`, `ready`, `executing`, `awaiting_external`, `reviewing`, `completed`, `failed`, `skipped`, `canceled` | Workflow query such as `GetStepLedger`; API step rows `status`; docs/Temporal/StepLedgerAndProgressModel.md | This domain describes operator-facing plan-step progress. It is not interchangeable with workflow lifecycle state or provider normalized status. |
| Step execution artifact status | Step-execution artifact manifest contract | `pending`, `preparing`, `executing`, `running`, `checking`, `completed`, `succeeded`, `failed`, `blocked`, `canceled`, `superseded` | Step execution artifact content type `application/vnd.moonmind.step-execution+json;version=1`; schemas `moonmind.schemas.step_execution_models.StepExecutionStatus` and `moonmind.schemas.temporal_models.StepExecutionStatus` | This domain describes durable step-execution evidence artifacts. Convert to step ledger status only at an explicit workflow/API projection boundary. |
| Provider normalized status | External provider adapters | Portable base: `queued`, `running`, `completed`, `failed`, `canceled`, `unknown`; provider extensions must be adapter-owned, such as Jules `awaiting_feedback` | Adapter result field `normalizedStatus` / internal `normalized_status`; provider monitoring payloads and artifacts | Provider-native tokens such as `in-progress` belong at provider boundaries and are normalized by the adapter. Do not promote provider-native spelling into workflow lifecycle or step ledger domains. |

## Audit Actions

The non-destructive status-token audit uses these action values:

| Action | Meaning |
| --- | --- |
| `keep_canonical` | Token is already canonical for its guessed domain and should remain in that domain. |
| `move_to_provider_boundary` | Token belongs to provider adapter normalization or provider evidence, not core workflow or step status. |
| `move_to_legacy_alias_map` | Token should be isolated in an explicit compatibility or legacy alias map when compatibility is required. |
| `rename_domain_specific` | Token is meaningful but too generic or cross-domain; rename or qualify it to the owning domain before behavior changes. |
| `delete_unused` | Token appears unused or stale after evidence review and can be removed in a later destructive cleanup story. |
| `historical_migration_only` | Token is valid only in migrations, archived evidence, or historical compatibility notes. |

## Formatting Standard

Machine values use lowercase snake_case, for example `awaiting_external` and `no_commit`.

Enum member names use UPPER_SNAKE_CASE, for example `AWAITING_EXTERNAL` and `NO_COMMIT`.

External JSON fields use camelCase, for example `temporalStatus`, `closeStatus`, and `normalizedStatus`.

Finish outcome codes use UPPER_SNAKE_CASE when represented as symbolic outcome codes.

UI labels use human-readable sentence or title case, for example `Awaiting external` or `No commit`.

CSS classes use kebab-case, for example `status-awaiting-external` and `status-no-commit`.

## Status Token Audit Command

Run the inventory in report mode:

```bash
python tools/audit_status_tokens.py
```

The report emits these columns:

```text
token,guessed_domain,files,canonicality,action
```

Run the CI enforcement mode:

```bash
python tools/audit_status_tokens.py --fail-on-unknown
```

The enforcement mode still emits the CSV inventory, then exits nonzero when a scanned token is unclassified or assigned to the unknown domain. Historical migration files may still contain legacy values such as `no_changes`; active code should route legacy handling through compatibility helpers.
