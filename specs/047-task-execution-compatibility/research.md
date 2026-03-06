# Research: Task Execution Compatibility

## Decision 1: Add dedicated task compatibility APIs instead of mutating the queue alias

- **Decision**: Add `GET /api/tasks/list` and `GET /api/tasks/{taskId}` as canonical task compatibility APIs while preserving the current queue-only `GET /api/tasks` alias for backward compatibility.
- **Rationale**: The existing `/api/tasks` path is already queue-shaped. Replacing it with mixed-source semantics would create an avoidable compatibility break for current clients.
- **Alternatives considered**:
  - Mutate `GET /api/tasks` into a mixed-source API: rejected because it silently changes existing behavior.
  - Keep all list/detail merging only in `dashboard.js`: rejected because the source document requires a documented server-side compatibility contract.

## Decision 2: Use a persisted task source mapping/global task index

- **Decision**: Add a persisted `task_source_mappings` index keyed by `task_id` and make it the canonical resolver for `/tasks/{taskId}` and `/api/tasks/{taskId}/resolution`.
- **Rationale**: The current router probes queue/orchestrator/Temporal tables directly. That contradicts the source document requirement that source selection not depend on probing or ID-shape rules.
- **Alternatives considered**:
  - Continue backend probing: rejected because it is explicitly non-compliant with `DOC-REQ-004`.
  - Infer source only from identifier text shape: rejected because Temporal IDs are opaque by contract.

## Decision 3: Keep `/api/executions*` as the Temporal-native control plane

- **Decision**: Reuse `POST /api/executions`, `/update`, `/signal`, and `/cancel` as the Temporal-native transport while exposing task-facing action semantics through the compatibility layer and dashboard copy.
- **Rationale**: The source document already defines these endpoints as the current adapter endpoints for Temporal-backed task actions. Reusing them avoids redundant transport layers.
- **Alternatives considered**:
  - Add brand new unified action endpoints for every task action: rejected for v1 because it duplicates existing, well-scoped Temporal controls without improving the migration contract.

## Decision 4: Move mixed-source pagination into a compatibility-owned server contract

- **Decision**: Replace the current browser-only bounded merge as the documented contract with a server-owned mixed-source cursor/count contract on `GET /api/tasks/list`.
- **Rationale**: `DOC-REQ-009` requires mixed-source queries to own their own cursor semantics and not leak raw Temporal page tokens. A client-only merge is useful UX, but it is not a stable compatibility API.
- **Alternatives considered**:
  - Keep fetching queue/orchestrator/Temporal pages independently in the browser: rejected because cursor/count semantics remain source-specific and unverifiable.
  - Expose raw Temporal `nextPageToken` in unified results: rejected because it breaks mixed-source cursor ownership.

## Decision 5: Normalize Temporal-backed rows/details through explicit allowlists

- **Decision**: Build a shared normalization helper that emits task-compatible rows/details using an allowlist for Search Attributes (`mm_owner_type`, `mm_owner_id`, `mm_state`, `mm_updated_at`, `mm_entry`, optional bounded repo/integration keys) and Memo (`title`, `summary`, safe refs, wait metadata).
- **Rationale**: The current Temporal responses already expose `memo` and `searchAttributes`, but the source document requires bounded, secret-safe compatibility payloads and forbids blind parameter dumping.
- **Alternatives considered**:
  - Return raw `parameters`, raw Memo, and all Search Attributes as-is: rejected because it risks exposing large or secret material.
  - Drop raw metadata entirely: rejected because operators still need raw state, debug, and wait context.

## Decision 6: Preserve Temporal manifest taxonomy without relabeling queue manifests

- **Decision**: Treat Temporal-managed manifest executions as `source=temporal` + `entry=manifest`, while queue-backed manifest jobs remain `source=queue`.
- **Rationale**: The compatibility model distinguishes where the execution lives (`source`) from what product flow it represents (`entry`). Changing queue manifest jobs to `source=temporal` before runtime migration would be false labeling.
- **Alternatives considered**:
  - Introduce a new top-level `manifest` execution source: rejected because the source document explicitly forbids it for Temporal-backed work.
  - Relabel all manifest work as `temporal`: rejected because it misstates actual runtime ownership during migration.

## Decision 7: Normalize status while keeping raw lifecycle semantics available

- **Decision**: Keep the dashboard status family limited to `queued`, `running`, `awaiting_action`, `succeeded`, `failed`, and `cancelled`, while preserving `rawState`, `temporalStatus`, and `closeStatus` on compatibility details and Temporal rows.
- **Rationale**: This matches current dashboard filters and the source document’s compatibility model without hiding important operator/debug state such as `TimedOut`, `Terminated`, or `ContinuedAsNew`.
- **Alternatives considered**:
  - Add new dashboard terminal states like `timed_out`: rejected for v1 because it breaks current task status taxonomy.
  - Collapse all raw Temporal fields into one display status: rejected because it loses operator meaning.

## Decision 8: Keep runtime mode as a hard planning and delivery gate

- **Decision**: Treat this feature as runtime mode end-to-end. Planning, tasks, and final implementation must include production runtime code changes and automated validation tests.
- **Rationale**: The user input and `spec.md` both explicitly prohibit docs/spec-only completion.
- **Alternatives considered**:
  - Allow doc-only closure because the source document is already written: rejected because FR-001 and FR-002 require runtime code plus validation.
