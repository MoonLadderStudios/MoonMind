# Managed Runtime Cleanup Story Breakdown

- Source: `docs/ManagedAgents/ManagedRuntimeCleanup.md`
- Source document class: `canonical-declarative`
- Story extraction date: `2026-06-27T00:48:13Z`
- Requested output mode: `jira`

## Design Summary

The design defines a declarative managed-runtime cleanup model that separates live-runtime leak cleanup from retained-state garbage collection. It introduces a dry-run-first workspace janitor that relies on run/session stores, Temporal ownership, Docker state, canonical paths, retention windows, and conservative timestamps before deleting old workspaces, session roots, artifacts, and optional JSON records. The design emphasizes fail-closed safety gates, shared-checkout protection, quarantine-style deletion, structured results, and operator observability.

## Coverage Points

- `DESIGN-REQ-001` **Declarative cleanup catalog** (requirement, 1. Summary; 6. Declarative cleanup catalog): Model managed-runtime cleanup as resource classes that define ownership, truth sources, eligibility, safety, authority, schedule, and observability.
- `DESIGN-REQ-002` **Managed-runtime scope boundaries** (constraint, 2. Scope and non-goals): Limit this model to managed-runtime resources and preserve separate ownership for database, Temporal history, credential, memory, deployment, and unrelated logging retention.
- `DESIGN-REQ-003` **Durable truth priority** (state-model, 3. Design goals; 4. Durable truth sources): Base deletion decisions on durable ownership and terminal state, not filesystem age alone.
- `DESIGN-REQ-004` **Live-runtime cleanup stays narrow** (constraint, 5. Cleanup systems present today; 7. Existing managed-session orphan reaping): Keep session termination, run-supervisor cleanup, and orphan reaping focused on live runtime resources rather than retained workspace garbage collection.
- `DESIGN-REQ-005` **Separate workspace janitor workflow** (integration, 8.1 Workflow and activity): Introduce a retained-state cleanup workflow/activity with independent schedule, dry-run default, and no coupling to session reconcile.
- `DESIGN-REQ-006` **Complete store visibility** (integration, 8.2 Required store APIs): Add all-record iteration and optional delete APIs for run/session stores while keeping list_active active-reconciliation focused.
- `DESIGN-REQ-007` **Canonical candidate discovery** (security, 8.3 Candidate roots): Only scan canonical managed-runtime roots and normalize artifact roots so broad agent-job directories are never treated as artifact candidates.
- `DESIGN-REQ-008` **Ownership-root grouping** (state-model, 8.4 Ownership grouping): Group candidate ownership by workspace/session root so shared checkouts survive until every owner is terminal and past retention.
- `DESIGN-REQ-009` **Terminal state and retention rules** (requirement, 8.5 Terminal states; 8.6 Retention windows; 8.7 Configuration): Use explicit terminal states, retention/grace windows, janitor enablement, dry-run settings, and deletion budgets to decide eligibility.
- `DESIGN-REQ-010` **Fail-closed safety gates** (security, 8.8 Safety gates): Skip deletion when stores, paths, ownership, activity, Docker references, age checks, rescan, budgets, or dry-run state make deletion unsafe.
- `DESIGN-REQ-011` **Two-phase delete protocol** (requirement, 8.9 Delete protocol): Acquire a lock, classify, recheck, quarantine-rename in the same parent, remove best-effort, and emit structured results.
- `DESIGN-REQ-012` **Structured cleanup result** (artifact, 8.10 Result shape): Return compact counts for scanned records, scanned paths, protected/eligible/deleted resources, bytes, skip reasons, and errors.
- `DESIGN-REQ-013` **Candidate classification and timestamps** (state-model, 9. State and path classification): Assign exactly one final classification and use the newest conservative owner/filesystem timestamp for age decisions.
- `DESIGN-REQ-014` **Operator observability and controls** (observability, 10. Observability and operator controls): Expose pass logs, metrics, dry-run samples, budget/error counters, schedule state, independent disablement, and answers for why resources were skipped or deleted.

## Candidate Stories

### STORY-001: Define managed-runtime cleanup catalog boundaries

- Short name: `cleanup-catalog`
- Source reference: `docs/ManagedAgents/ManagedRuntimeCleanup.md`
- Source sections: 1. Summary, 2. Scope and non-goals, 5. Cleanup systems present today, 6. Declarative cleanup catalog, 7. Existing managed-session orphan reaping
- Claim IDs: `CLAIM-docs-managed-runtime-cleanup-summary-001`, `CLAIM-docs-managed-runtime-cleanup-scope-001`, `CLAIM-docs-managed-runtime-cleanup-existing-001`, `CLAIM-docs-managed-runtime-cleanup-catalog-001`
- Coverage IDs: `DESIGN-REQ-001`, `DESIGN-REQ-002`, `DESIGN-REQ-004`
- Dependencies: None

As a MoonMind platform maintainer, I need managed-runtime cleanup resources modeled through one declarative catalog so each cleanup path has explicit ownership, truth sources, safety rules, and boundaries.

Independent test: Unit-test the catalog/resource-class model or equivalent configuration to prove each declared managed-runtime resource has an owner, truth source, eligibility rule, safety rule, deletion authority, and schedule, and that live-runtime orphan reaping excludes retained workspace and artifact roots.

Acceptance criteria:
- The cleanup model enumerates managed-session containers, sidecar volumes, launcher support files, skill projections, workspace roots, artifact directories, and optional JSON records.
- Each resource class records owner plane, lifecycle, candidate source, truth source, eligibility, safety behavior, deletion authority, schedule, and observability where applicable.
- The implementation keeps retained-state workspace/artifact cleanup out of managed-session termination, managed-run supervisor cleanup, and managed-session orphan reaping.
- Out-of-scope retention domains are not scanned or deleted by this managed-runtime cleanup model.

Requirements:
- Expose a single explicit cleanup-catalog representation for managed-runtime resource classes.
- Preserve the live-runtime versus retained-state boundary in code and tests.
- Document or encode non-goal domains as excluded from this cleanup system.

Assumptions:
- The catalog may be implemented as dataclasses, constants, or typed configuration as long as the resource semantics are explicit and testable.

### STORY-002: Run a separate dry-run workspace janitor

- Short name: `workspace-janitor`
- Source reference: `docs/ManagedAgents/ManagedRuntimeCleanup.md`
- Source sections: 8.1 Workflow and activity, 8.2 Required store APIs, 8.3 Candidate roots
- Claim IDs: `CLAIM-docs-managed-runtime-cleanup-janitor-workflow-001`, `CLAIM-docs-managed-runtime-cleanup-candidates-001`
- Coverage IDs: `DESIGN-REQ-005`, `DESIGN-REQ-006`, `DESIGN-REQ-007`
- Dependencies: `STORY-001`

As an operator, I need a separate retained-state cleanup workflow that scans only canonical managed-runtime roots in dry-run mode by default so disk-pressure cleanup can be evaluated without destabilizing live sessions.

Independent test: Exercise the scheduled workflow/activity entry point with temporary managed-runtime roots and fake stores, verifying dry-run is the default, list_active is not used for full retention visibility, and paths outside canonical roots are skipped.

Acceptance criteria:
- A `MoonMind.ManagedRuntimeWorkspaceCleanup` workflow or equivalent scheduleable workflow invokes an `agent_runtime.cleanup_managed_runtime_files` activity or equivalent retained-state activity.
- The janitor defaults to disabled or dry-run behavior until explicit configuration enables deletion.
- Run and session stores provide all-record iteration, and optional record deletion uses explicit delete APIs rather than overloading active-only reconciliation APIs.
- Candidate discovery is limited to configured canonical workspace, session, artifact, and record roots, with artifact root normalization preventing scans of `/work/agent_jobs/*` as artifact directories.

Requirements:
- Add a separate retained-state janitor workflow/activity surface.
- Add or use complete run/session store iteration and deletion APIs.
- Normalize and constrain candidate root discovery to the documented managed-runtime paths.

Assumptions:
- Schedule registration may be implemented separately from the activity body if existing scheduler wiring requires a staged rollout.

### STORY-003: Classify janitor eligibility from ownership and retention

- Short name: `cleanup-eligibility`
- Source reference: `docs/ManagedAgents/ManagedRuntimeCleanup.md`
- Source sections: 3. Design goals, 4. Durable truth sources, 8.4 Ownership grouping, 8.5 Terminal states, 8.6 Retention windows, 8.7 Configuration, 8.8 Safety gates, 9. State and path classification
- Claim IDs: `CLAIM-docs-managed-runtime-cleanup-goals-001`, `CLAIM-docs-managed-runtime-cleanup-truth-001`, `CLAIM-docs-managed-runtime-cleanup-candidates-001`, `CLAIM-docs-managed-runtime-cleanup-terminal-retention-001`, `CLAIM-docs-managed-runtime-cleanup-safety-001`, `CLAIM-docs-managed-runtime-cleanup-classification-observability-001`
- Coverage IDs: `DESIGN-REQ-003`, `DESIGN-REQ-008`, `DESIGN-REQ-009`, `DESIGN-REQ-010`, `DESIGN-REQ-013`
- Dependencies: `STORY-001`, `STORY-002`

As a maintainer, I need the janitor to group candidates by ownership root and classify eligibility from terminal states, active-turn state, Docker references, retention windows, and conservative timestamps so shared workspaces are never deleted while still in use.

Independent test: Run table-driven unit tests over candidate roots with combinations of active, terminal, recent, shared, ambiguous, unsafe, symlinked, and Docker-referenced owners, asserting the expected final classification and skip reason.

Acceptance criteria:
- Workspace/session candidates are grouped by ownership root, including shared workflow/correlation-key checkouts and per-run roots.
- Only documented terminal run states (`completed`, `failed`, `canceled`, `timed_out`) and session states (`terminated`, `degraded`, `failed`) are eligible for deletion consideration.
- Any active owner, active turn, unreadable store, ambiguous owner, unsafe path, symlink, live Docker container, active volume mount, recent timestamp, or failed rescan prevents deletion with a specific reason.
- Candidate age uses the newest available run, session, and filesystem timestamp, with missing timestamps handled conservatively.
- Configuration controls enablement, dry-run, workspace retention, artifact retention, optional record retention, grace window, delete path cap, delete byte cap, and lock path.

Requirements:
- Implement ownership-root grouping for candidate records and paths.
- Implement terminal-state, retention, grace, activity, Docker, and safety-gate eligibility checks.
- Return exactly one final classification per candidate with an actionable skip reason where applicable.

### STORY-004: Delete eligible runtime state with quarantine protocol

- Short name: `quarantine-delete`
- Source reference: `docs/ManagedAgents/ManagedRuntimeCleanup.md`
- Source sections: 6.5 Managed runtime workspace root, 6.6 Managed runtime artifact directory, 6.7 Managed run/session JSON records, 8.8 Safety gates, 8.9 Delete protocol, 8.10 Result shape
- Claim IDs: `CLAIM-docs-managed-runtime-cleanup-catalog-001`, `CLAIM-docs-managed-runtime-cleanup-safety-001`, `CLAIM-docs-managed-runtime-cleanup-delete-protocol-001`
- Coverage IDs: `DESIGN-REQ-010`, `DESIGN-REQ-011`, `DESIGN-REQ-012`
- Dependencies: `STORY-003`

As an operator, I need eligible workspaces, artifact directories, and optional JSON records deleted through a locked two-phase quarantine protocol so cleanup is repeatable, race-resistant, and auditable.

Independent test: Use temporary directories to verify that eligible candidates are rechecked, renamed to same-parent quarantine paths, removed best-effort, limited by path/byte budgets, and reported in structured result counts; dry-run must not rename or delete.

Acceptance criteria:
- The janitor acquires the configured lock before destructive cleanup and respects per-pass path and byte budgets.
- Immediately before deletion, records and Docker state are reloaded and all safety gates are rechecked.
- Deletion renames each eligible candidate to a `.gc-<uuid>-<name>` quarantine path in the same parent before best-effort removal.
- Artifact directories are retained longer than workspaces and skipped while referenced by retained records.
- Run/session JSON record deletion is optional and occurs only after workspace and artifact cleanup rules are satisfied.
- The activity returns structured counts for scanned, protected, eligible, deleted, skipped, errored, and estimated deleted bytes.

Requirements:
- Implement lock-protected two-phase delete behavior for eligible retained-state candidates.
- Apply artifact and optional record retention semantics separately from workspace/session root deletion.
- Produce a compact `ManagedRuntimeCleanupResult` or equivalent structured result.

### STORY-005: Expose cleanup observability and operator controls

- Short name: `cleanup-observability`
- Source reference: `docs/ManagedAgents/ManagedRuntimeCleanup.md`
- Source sections: 8.10 Result shape, 9. State and path classification, 10. Observability and operator controls
- Claim IDs: `CLAIM-docs-managed-runtime-cleanup-delete-protocol-001`, `CLAIM-docs-managed-runtime-cleanup-classification-observability-001`
- Coverage IDs: `DESIGN-REQ-012`, `DESIGN-REQ-013`, `DESIGN-REQ-014`
- Dependencies: `STORY-003`, `STORY-004`

As an operator, I need cleanup passes to report what they scanned, skipped, would delete, and actually deleted so I can operate retained-state cleanup safely and answer why a specific resource was or was not removed.

Independent test: Run dry-run and deletion-mode janitor passes against controlled fixtures, then assert structured logs/metrics/result fields expose scanned counts, classifications, skip reasons, dry-run samples, budget exhaustion, errors, schedule state, and independent disablement state without leaking unsafe paths or secrets.

Acceptance criteria:
- Each pass emits structured logs and count metrics by resource class and skip reason.
- Dry-run output includes actionable candidate samples and estimated deletion impact without deleting paths.
- Delete budget exhaustion and error counters are visible with safe path identifiers.
- Operators can see schedule state and can disable retained-state janitor cleanup independently from managed-session orphan reaping.
- Operator-facing evidence answers how many session containers or sidecar volumes were reaped, how many workspaces are eligible, why a workspace was skipped, how much dry-run would delete, and what was deleted in the last pass.

Requirements:
- Expose janitor observability through structured logs, metrics, and activity results.
- Preserve per-candidate classification and skip/delete explanations for operator diagnostics.
- Provide independent controls for cleanup systems.

## Coverage Matrix

- `CLAIM-docs-managed-runtime-cleanup-summary-001` -> `STORY-001`
- `CLAIM-docs-managed-runtime-cleanup-scope-001` -> `STORY-001`
- `CLAIM-docs-managed-runtime-cleanup-goals-001` -> `STORY-003`
- `CLAIM-docs-managed-runtime-cleanup-truth-001` -> `STORY-003`
- `CLAIM-docs-managed-runtime-cleanup-existing-001` -> `STORY-001`
- `CLAIM-docs-managed-runtime-cleanup-catalog-001` -> `STORY-001`, `STORY-004`
- `CLAIM-docs-managed-runtime-cleanup-janitor-workflow-001` -> `STORY-002`
- `CLAIM-docs-managed-runtime-cleanup-candidates-001` -> `STORY-002`, `STORY-003`
- `CLAIM-docs-managed-runtime-cleanup-terminal-retention-001` -> `STORY-003`
- `CLAIM-docs-managed-runtime-cleanup-safety-001` -> `STORY-003`, `STORY-004`
- `CLAIM-docs-managed-runtime-cleanup-delete-protocol-001` -> `STORY-004`, `STORY-005`
- `CLAIM-docs-managed-runtime-cleanup-classification-observability-001` -> `STORY-003`, `STORY-005`
- `DESIGN-REQ-001` -> `STORY-001`
- `DESIGN-REQ-002` -> `STORY-001`
- `DESIGN-REQ-003` -> `STORY-003`
- `DESIGN-REQ-004` -> `STORY-001`
- `DESIGN-REQ-005` -> `STORY-002`
- `DESIGN-REQ-006` -> `STORY-002`
- `DESIGN-REQ-007` -> `STORY-002`
- `DESIGN-REQ-008` -> `STORY-003`
- `DESIGN-REQ-009` -> `STORY-003`
- `DESIGN-REQ-010` -> `STORY-003`, `STORY-004`
- `DESIGN-REQ-011` -> `STORY-004`
- `DESIGN-REQ-012` -> `STORY-004`, `STORY-005`
- `DESIGN-REQ-013` -> `STORY-003`, `STORY-005`
- `DESIGN-REQ-014` -> `STORY-005`

## Dependencies

- `STORY-001` depends on None
- `STORY-002` depends on `STORY-001`
- `STORY-003` depends on `STORY-001`, `STORY-002`
- `STORY-004` depends on `STORY-003`
- `STORY-005` depends on `STORY-003`, `STORY-004`

## Out Of Scope

- Database execution records, Temporal workflow histories, long-term artifact service retention, provider credentials/OAuth volumes, memory indexes, deployment desired-state files, and application logs outside managed-runtime workspace roots.
- Creating `spec.md`, generating `specs/` directories, implementing the janitor, publishing Jira issues, or transitioning MM-940.

## Coverage Gate

PASS - every major design point is owned by at least one story.
