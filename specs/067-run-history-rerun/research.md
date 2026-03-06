# Research: Run History and Rerun Semantics

## Decision 1: Treat `workflowId` as the only durable task/detail identity for Temporal-backed work

- **Decision**: Use `workflowId` as the canonical durable identifier across execution detail, task compatibility routes, bookmarks, and rerun continuity; require `taskId == workflowId` for Temporal-backed payloads.
- **Rationale**: The current projection is already keyed by `workflow_id`, `/api/executions/{workflow_id}` already describes the latest execution view, and the dashboard can already probe Temporal detail by the task route segment. Strengthening that contract avoids leaking Temporal run-instance mechanics into product routing.
- **Alternatives considered**:
  - Use `runId` as the detail route key. Rejected because Continue-As-New rotates `runId`, which would break bookmarks and force users to understand Temporal internals.
  - Preserve separate task and workflow identifiers for Temporal rows. Rejected because it creates avoidable drift in compatibility surfaces without adding user value.

## Decision 2: Keep the application database as a latest-run projection, not a per-run ledger

- **Decision**: Preserve one mutable projection row per `workflowId` in `temporal_executions` and do not add a v1 per-run history table or historical-run route.
- **Rationale**: The current repo baseline already models latest-run state in place. The source document explicitly keeps run history conceptual/Temporal-native in v1 and treats immutable per-run evidence as artifact-backed future work.
- **Alternatives considered**:
  - Add a `workflowId + runId` read model now. Rejected because it expands scope far beyond the current compatibility and rerun-alignment objective.
  - Reconstruct historical runs from mutable row snapshots. Rejected because the row is not an immutable ledger and would produce misleading audit semantics.

## Decision 3: Preserve existing Continue-As-New rerun behavior and make it the explicit contract

- **Decision**: Keep `RequestRerun` implemented as Continue-As-New on the same logical execution: preserve `workflowId`, rotate `runId`, reset run-local state, and record rerun summary metadata.
- **Rationale**: `moonmind/workflows/temporal/service.py` already follows this model. The correct implementation path is to formalize and test the behavior rather than replace it with a new sibling execution model.
- **Alternatives considered**:
  - Start a fresh workflow for rerun. Rejected because the source document defines rerun as the same logical execution and because fresh workflow creation should represent new logical work.
  - Treat rerun as an in-place state reset without Continue-As-New. Rejected because Continue-As-New is the existing and safer history-bounding mechanism.

## Decision 4: Keep terminal rerun unsupported until there is an explicit restart surface

- **Decision**: Preserve the current terminal-state gate that returns a non-applied update response for `RequestRerun` against terminal executions.
- **Rationale**: The service currently short-circuits all updates in terminal states before `RequestRerun` handling. The source document calls this out explicitly and says any closed-execution restart behavior must be implemented deliberately, not implicitly.
- **Alternatives considered**:
  - Exempt `RequestRerun` from the terminal-state gate now. Rejected because it changes lifecycle semantics and user expectations beyond the repo-aligned baseline.
  - Silently map terminal rerun to fresh execution creation. Rejected because it would hide a new logical identity transition behind a same-execution command.

## Decision 5: Keep manual rerun distinct from lifecycle rollover and major reconfiguration

- **Decision**: Use `continue_as_new_cause` as the semantic classifier for Continue-As-New events, with `manual_rerun`, `lifecycle_threshold`, and `major_reconfiguration` as the v1 causes.
- **Rationale**: The service already records these causes in memo and search attributes. This is the correct product/operator signal because `rerun_count` is a broad Continue-As-New counter and does not prove user intent.
- **Alternatives considered**:
  - Derive rerun meaning from `rerun_count > 0`. Rejected because threshold rollover and major reconfiguration also increment the counter.
  - Hide cause data entirely from payloads. Rejected because UI and operator surfaces then cannot distinguish manual rerun from automatic rollover.

## Decision 6: Resolve artifacts from the latest `temporalRunId` returned by execution detail

- **Decision**: Keep the artifact endpoint keyed by `{namespace}/{workflowId}/{temporalRunId}` and require detail rendering to fetch the latest execution detail first, then request artifacts using the returned current run metadata.
- **Rationale**: The dashboard already follows this pattern in `renderTemporalDetailPage`, and the source document explicitly calls out stale list snapshot risk when Continue-As-New occurs between list and detail fetches.
- **Alternatives considered**:
  - Use a workflow-only artifact endpoint for v1. Rejected because the artifact system is already modeled around execution links that include run identity.
  - Drive artifact fetches from the last seen list-row `runId`. Rejected because that would fail after rerun or automatic rollover.

## Decision 7: Extend the dashboard to treat Temporal rows as stable logical rows

- **Decision**: Add or tighten temporal row normalization so task list/detail flows use `workflowId`/`taskId` as the stable row ID and link target, while displaying current `runId` only as metadata.
- **Rationale**: Current dashboard detail rendering is close to the desired contract, but list-row helper code still falls back to `runId` for some sources and the active-list integration does not yet make Temporal row identity explicit. The feature requires stable row semantics across Continue-As-New, so this must be planned as runtime work.
- **Alternatives considered**:
  - Limit the feature to backend APIs and leave dashboard list semantics implicit. Rejected because the source document and spec both require adjacent UI/runtime alignment.
  - Introduce a separate Temporal-native list page. Rejected because v1 intentionally keeps the product anchored on the task-oriented dashboard model.

## Decision 8: Keep runtime-mode scope as a hard planning gate

- **Decision**: Treat this feature as runtime implementation mode only; plans and tasks must require production code changes plus automated validation tests.
- **Rationale**: The task objective and `DOC-REQ-016` explicitly reject docs-only completion. The repo’s runtime-vs-docs validation tooling should remain aligned with that constraint.
- **Alternatives considered**:
  - Close the feature with docs/spec changes only. Rejected by the task objective and feature spec.
  - Treat dashboard or API tests as optional. Rejected because the feature is primarily about runtime behavior and compatibility semantics.
