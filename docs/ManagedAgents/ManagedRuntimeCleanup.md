# Managed Runtime Cleanup Model

- **Status:** Canonical desired state
- **Owners:** MoonMind Platform
- **Last updated:** 2026-07-11
- **Audience:** Contributors, operators, runtime authors, and infrastructure maintainers
- **Purpose:** Declarative cleanup design for managed-runtime resources, including managed-session orphan reaping and automatic retained workspace/artifact cleanup.

**Related:**

- [`docs/ManagedAgents/ManagedAgentArchitecture.md`](./ManagedAgentArchitecture.md)
- [`docs/ManagedAgents/DockerSidecarRuntime.md`](./DockerSidecarRuntime.md)
- [`docs/ManagedAgents/CodexManagedSessionPlane.md`](./CodexManagedSessionPlane.md)
- [`docs/ManagedAgents/CodexCliManagedSessions.md`](./CodexCliManagedSessions.md)
- [`docs/ManagedAgents/LiveLogs.md`](./LiveLogs.md)
- [`docs/Temporal/ManagedAndExternalAgentExecutionModel.md`](../Temporal/ManagedAndExternalAgentExecutionModel.md)

---

## 1. Summary

MoonMind has several cleanup paths today, but they do not share one declarative model:

- managed-session termination removes live runtime resources for one session;
- managed-run supervision cleans short-lived launcher support files;
- managed-session reconciliation reaps orphaned Docker containers and sidecar volumes;
- run and session stores keep JSON records and expose active-record views;
- managed-runtime workspaces and artifact directories under `/work/agent_jobs` are automatically retained and expired according to the policies in this document.

The desired model is a **declarative cleanup catalog**: each resource class declares its owner, durable truth source, terminal states, retention policy, safety gates, deletion authority, and observability. Cleanup implementations reconcile that catalog instead of embedding unrelated deletion rules in hot lifecycle paths.

The **managed runtime workspace janitor** removes old terminal workspaces, session roots, and artifact directories after configurable retention windows. Run/session JSON records remain indefinitely unless record retention is explicitly configured. Active sessions, active runs, and recent workflow-shared checkouts remain protected.

---

## 2. Scope and non-goals

### 2.1 In scope

This document covers managed-runtime cleanup resources under the managed-agent subsystem:

- managed run process launcher support files,
- managed session containers,
- managed session Docker sidecar containers,
- managed session Docker socket and graph volumes,
- managed session Docker config files,
- workspace-local skill projections,
- managed runtime workspace roots,
- managed runtime session roots,
- managed runtime artifact directories,
- managed run JSON records,
- managed session JSON records.

### 2.2 Out of scope

This document does not define retention for unrelated MoonMind state, such as:

- database execution records,
- Temporal workflow histories,
- long-term artifact service retention outside the managed runtime local filesystem,
- provider-profile credentials and OAuth auth volumes,
- memory indexes,
- deployment desired-state files,
- application logs outside the managed-runtime workspace root.

Those systems may adopt the same cleanup-catalog shape later, but they remain separate ownership domains.

---

## 3. Design goals

1. **Declarative resource ownership.** Every cleanable resource class must state who owns it and what durable truth determines whether it is live.
2. **Terminal-state awareness.** Durable workspaces and artifacts are never deleted solely because their filesystem mtime is old.
3. **Shared-checkout safety.** A workflow/correlation-key workspace can be shared by multiple child runs; it must survive until every owner is terminal and past retention.
4. **Hot-path restraint.** Session termination and run completion should clean live runtime resources, not perform broad retained-state garbage collection.
5. **Safe working default.** Retained-state cleanup runs automatically and destructively after retention; dry-run remains an explicit diagnostic override with actionable skip reasons.
6. **Fail closed.** Missing stores, corrupt records, unsafe paths, symlinks, and ambiguous ownership must prevent deletion.
7. **Idempotent operations.** Cleanup may run repeatedly and concurrently with lifecycle reconciliation without corrupting active execution state.
8. **Operator visibility.** Each cleanup pass should report scanned, eligible, skipped, deleted, errored, and dry-run counts.

---

## 4. Durable truth sources

Cleanup decisions must be derived from durable truth sources in this priority order:

1. **Temporal workflow ownership** when deciding whether a non-terminal session record is stale.
2. **ManagedSessionStore JSON records** for managed session status and session-level paths.
3. **ManagedRunStore JSON records** for managed run status, workflow ownership, workspace paths, and artifact refs.
4. **Docker labels and active mounts** for live container and volume safety gates.
5. **Canonical filesystem layout** for candidate discovery and path safety.
6. **Filesystem timestamps** only as an age signal after ownership has been resolved.

Filesystem age alone is never enough to delete a workspace, artifact directory, or record.

---

## 5. Cleanup systems present today

| System | Trigger | Resources cleaned | Durable truth | Current boundary |
|---|---|---|---|---|
| Managed session termination | Explicit session control action | Session container, Docker sidecar resources, Docker config, GitHub auth broker, skill projections | Session locator and `ManagedSessionStore` record | Per-session live resource cleanup only |
| Managed run supervisor cleanup | Run cancel, process exit, startup reconciliation | Launcher support files and deferred runtime files | `ManagedRunStore` active records plus supervised process state | Does not remove durable workspace roots |
| Managed-session orphan reaping | `MoonMind.ManagedSessionReconcile` schedule | Orphaned session containers and sidecar graph/socket volumes | Active session records, Docker labels, Temporal owner status, grace windows | Does not remove `/work/agent_jobs` workspaces or artifact directories |
| Run/session store active reconciliation | Store list-active calls | Active-record discovery and stale-process/session marking | JSON record status fields | No retention delete pass |
| Workspace janitor | Hourly operational workflow | Old terminal workspace roots, session roots, artifact dirs, and optionally old JSON records | All run/session records plus canonical paths and live Docker safety gates | Bounded retained-state cleanup system |

---

## 6. Declarative cleanup catalog

MoonMind should model cleanup as resource classes. The examples below are normative for ownership and safety semantics; implementation may use Python dataclasses or plain configuration instead of literal YAML.

### 6.1 Managed session container

```yaml
kind: CleanupResourceClass
name: managed-session.container
ownerPlane: managed-session-controller
lifecycle: live-runtime
candidateSource:
  dockerLabels:
    moonmind.session_id: required
truthSource:
  store: ManagedSessionStore
  activePredicate: status not in [terminated, degraded, failed]
eligibility:
  deleteWhen:
    - session record is absent or terminal
    - container age >= MOONMIND_MANAGED_SESSION_REAP_GRACE_SECONDS
  forceTerminalWhen:
    - session record is active
    - Temporal owner workflow is terminal
    - or ready session has no activeTurnId beyond max age
safety:
  failClosedWhenStoreUnavailable: true
  skipWhenSessionActive: true
  skipWhenYoungerThanGrace: true
deletionAuthority: DockerCodexManagedSessionController.reap_orphan_session_containers
schedule: MoonMind.ManagedSessionReconcile
observability:
  counters:
    - scanned_containers
    - reaped_containers
    - skipped_active
    - skipped_recent
    - forced_stale
```

### 6.2 Managed session Docker sidecar volumes

```yaml
kind: CleanupResourceClass
name: managed-session.sidecar-volume
ownerPlane: managed-session-controller
lifecycle: live-runtime
candidateSource:
  dockerVolumes:
    names: session socket/graph volume names
truthSource:
  store: ManagedSessionStore
  dockerActiveMounts: true
eligibility:
  deleteWhen:
    - volume session id is not active
    - volume is not mounted by an active container
    - volume age >= MOONMIND_MANAGED_SESSION_REAP_GRACE_SECONDS
safety:
  skipWhenActiveMountExists: true
  skipWhenSessionActive: true
  skipWhenCreatedAtUnknown: true
  skipWhenYoungerThanGrace: true
deletionAuthority: DockerCodexManagedSessionController._reap_orphan_sidecar_volumes
schedule: MoonMind.ManagedSessionReconcile
observability:
  counters:
    - scanned_volumes
    - reaped_volumes
    - skipped_active_volumes
    - skipped_recent_volumes
```

### 6.3 Managed run launcher support files

```yaml
kind: CleanupResourceClass
name: managed-run.launcher-support-file
ownerPlane: managed-run-supervisor
lifecycle: process-runtime
candidateSource:
  supervisorMemory:
    cleanupPaths: true
    deferredCleanupPaths: true
truthSource:
  processSupervisor: true
  store: ManagedRunStore
eligibility:
  deleteWhen:
    - run process is canceled, exited, or lost during reconcile
safety:
  bestEffort: true
  ignoreMissingPaths: true
  scopeToRegisteredCleanupPathsOnly: true
deletionAuthority: ManagedRunSupervisor._cleanup_runtime_files
schedule: run lifecycle and supervisor startup reconcile
```

### 6.4 Workspace-local skill projections

```yaml
kind: CleanupResourceClass
name: managed-session.skill-projection
ownerPlane: managed-session-controller
lifecycle: session-runtime
candidateSource:
  sessionRecord:
    workspacePath: required
truthSource:
  store: ManagedSessionStore
eligibility:
  deleteWhen:
    - terminate_session runs
    - session record is terminal or being terminated
safety:
  scopeToOwnedRootsOnly: true
  bestEffort: true
deletionAuthority: DockerCodexManagedSessionController._cleanup_skill_projections_for_session
schedule: terminate_session
```

### 6.5 Managed runtime workspace root

```yaml
kind: CleanupResourceClass
name: managed-runtime.workspace-root
ownerPlane: managed-runtime-workspace-janitor
lifecycle: retained-state
candidateSource:
  filesystem:
    roots:
      - ${MOONMIND_AGENT_RUNTIME_STORE:-/work/agent_jobs}/workspaces/*
      - ${MOONMIND_AGENT_RUNTIME_STORE:-/work/agent_jobs}/${agent_run_id}
truthSource:
  stores:
    - ManagedRunStore
    - ManagedSessionStore
  dockerActiveContainers: true
eligibility:
  deleteWhen:
    - every referencing run is terminal
    - every referencing session is terminal
    - no referencing record has activeTurnId
    - newest owner activity is older than workspace retention
    - no live Docker container or volume references the session/workspace
safety:
  dryRunDefault: false
  canonicalPathOnly: true
  skipSymlinks: true
  skipWhenAnyOwnerActive: true
  skipWhenAnyOwnerRecent: true
  skipWhenOwnershipAmbiguous: true
  rescanBeforeDelete: true
  deletionLockRequired: true
deletionAuthority: ManagedRuntimeWorkspaceJanitor
schedule: MoonMind.ManagedRuntimeWorkspaceCleanup
```

### 6.6 Managed runtime artifact directory

```yaml
kind: CleanupResourceClass
name: managed-runtime.artifact-dir
ownerPlane: managed-runtime-workspace-janitor
lifecycle: retained-state
candidateSource:
  filesystem:
    root: normalized_managed_runtime_artifact_root()/*
truthSource:
  stores:
    - ManagedRunStore artifact refs
    - ManagedSessionStore artifact refs
eligibility:
  deleteWhen:
    - all referencing runs/sessions are terminal
    - artifact retention window has elapsed
    - no retained record still needs the artifact directory for UI/log/audit lookup
safety:
  retainLongerThanWorkspaces: true
  skipWhenReferencedByRetainedRecord: true
  dryRunDefault: true
deletionAuthority: ManagedRuntimeWorkspaceJanitor
schedule: MoonMind.ManagedRuntimeWorkspaceCleanup
```

### 6.7 Managed run/session JSON records

```yaml
kind: CleanupResourceClass
name: managed-runtime.record
ownerPlane: managed-runtime-workspace-janitor
lifecycle: retained-metadata
candidateSource:
  filesystem:
    roots:
      - ${MOONMIND_AGENT_RUNTIME_STORE:-/work/agent_jobs}/managed_runs/*.json
      - ${MOONMIND_AGENT_RUNTIME_STORE:-/work/agent_jobs}/managed_sessions/*.json
truthSource:
  self: JSON status and timestamps
eligibility:
  deleteWhen:
    - record is terminal
    - record retention window has elapsed
    - referenced workspace/artifacts were already deleted or intentionally retained elsewhere
safety:
  deleteAfterWorkspaceAndArtifactPasses: true
  optionalFeature: true
  dryRunDefault: true
deletionAuthority: ManagedRuntimeWorkspaceJanitor
schedule: MoonMind.ManagedRuntimeWorkspaceCleanup
```

---

## 7. Existing managed-session orphan reaping

Managed-session orphan reaping is a **live-runtime cleanup** path. It must stay focused on leaked Docker resources, not retained workspace deletion.

### 7.1 Schedule

The recurring workflow is:

```text
MoonMind.ManagedSessionReconcile
```

The schedule should continue to run every 10 minutes by default. Its activity resolves to `agent_runtime.reconcile_managed_sessions` and performs bounded reconciliation work.

### 7.2 Eligibility model

A managed-session container is eligible for orphan reaping when:

1. it carries the managed-session Docker labels required for ownership discovery;
2. its session is not active in `ManagedSessionStore`, or its active record was proven stale;
3. it is older than the configured grace window unless it is being force-reaped as stale;
4. the session store was readable for the pass.

A sidecar volume is eligible when:

1. it is a managed-session sidecar graph/socket volume;
2. its session id is not active;
3. the volume is not mounted by any active Docker container;
4. its create timestamp is known;
5. it is older than the configured grace window.

### 7.3 Stale active record handling

If a non-terminal session record points at an owning workflow that has reached a terminal Temporal status, reconcile may mark the session terminated and then reap its containers/sidecar volumes.

As a final guardrail, an old `ready` session without `activeTurnId` may be force-marked terminal after the max-age window. This is a live-runtime leak guard, not a workspace retention policy.

### 7.4 Existing reaper configuration

| Env var | Default | Meaning |
|---|---:|---|
| `MOONMIND_MANAGED_SESSION_REAP_ENABLED` | `1` | Enables orphan reaping. Falsey values disable it. |
| `MOONMIND_MANAGED_SESSION_REAP_GRACE_SECONDS` | `900` | Minimum age before an orphan container or sidecar volume is eligible. |
| `MOONMIND_MANAGED_SESSION_REAP_MAX_AGE_SECONDS` | `172800` | Maximum age for stale `ready` active-store sessions with no active turn. Falsey disables this guardrail. |

### 7.5 Non-goal

The orphan reaper must not delete:

- `/work/agent_jobs/workspaces/*`,
- `/work/agent_jobs/<agent_run_id>` session roots,
- `/work/agent_jobs/artifacts/*`,
- managed run JSON records,
- managed session JSON records.

Those are retained-state cleanup resources and belong to the workspace janitor.

---

## 8. Managed runtime workspace janitor

The workspace janitor is a **retained-state cleanup** system. It removes old durable local runtime state after every known owner is terminal and after retention windows have elapsed.

### 8.1 Workflow and activity

Workflow:

```text
MoonMind.ManagedRuntimeWorkspaceCleanup
```

Activity:

```text
agent_runtime.cleanup_managed_runtime_files
```

Default schedule:

- hourly (`0 * * * *` in UTC), so a backlog continues converging without manual triggering;
- overlap mode `skip`;
- catchup mode `last`;
- enabled whenever `MOONMIND_MANAGED_RUNTIME_JANITOR_ENABLED` is true;
- destructive after retention unless dry-run is explicitly enabled.

This should be separate from `MoonMind.ManagedSessionReconcile` so broad filesystem deletion cannot slow or destabilize live session leak cleanup.

### 8.2 Required store APIs

The janitor needs complete terminal-state visibility. Add store methods rather than overloading active-only reconciliation APIs:

```python
class ManagedRunStore:
    def iter_all(self) -> Iterable[ManagedRunRecord]: ...
    def delete(self, run_id: str) -> None: ...

class ManagedSessionStore:
    def iter_all(self) -> Iterable[CodexManagedSessionRecord]: ...
    def delete(self, session_id: str) -> None: ...
```

`list_active()` should remain active-reconciliation focused.

### 8.3 Candidate roots

The janitor may discover candidates only under canonical managed-runtime roots:

```text
${MOONMIND_AGENT_RUNTIME_STORE:-/work/agent_jobs}/workspaces/<workspace_key>
${MOONMIND_AGENT_RUNTIME_STORE:-/work/agent_jobs}/<agent_run_id>
normalized_managed_runtime_artifact_root()/<job_id>
${MOONMIND_AGENT_RUNTIME_STORE:-/work/agent_jobs}/managed_runs/<run_id>.json
${MOONMIND_AGENT_RUNTIME_STORE:-/work/agent_jobs}/managed_sessions/<session_id>.json
```

A candidate path outside those roots is unsafe and must be skipped.

Artifact roots must be normalized before scanning. The janitor must use the same normalization semantics as `managed_runtime_artifact_root()`: when `MOONMIND_AGENT_RUNTIME_ARTIFACTS` is unset, blank, or set to the agent-jobs root such as `/work/agent_jobs`, the artifact candidate root is `/work/agent_jobs/artifacts`. The janitor must never scan `/work/agent_jobs/*` as artifact directories.

### 8.4 Ownership grouping

The janitor must group records by **ownership root**, not only by run id.

Examples:

- `/work/agent_jobs/workspaces/mm:workflow-123/repo` belongs to `/work/agent_jobs/workspaces/mm:workflow-123`.
- `/work/agent_jobs/workspaces/resolver:abc/repo` belongs to `/work/agent_jobs/workspaces/resolver:abc`.
- `/work/agent_jobs/<agent_run_id>/repo` belongs to `/work/agent_jobs/<agent_run_id>`.
- `/work/agent_jobs/<agent_run_id>/session` belongs to `/work/agent_jobs/<agent_run_id>`.
- `/work/agent_jobs/<agent_run_id>/artifacts` belongs to `/work/agent_jobs/<agent_run_id>`.

The workspace root is eligible only when every run/session that maps to that ownership root is terminal and past retention.

### 8.5 Terminal states

Managed run terminal states:

```text
completed
failed
canceled
timed_out
```

Managed session terminal states:

```text
terminated
degraded
failed
```

All other states are active or ambiguous for deletion purposes.

### 8.6 Retention windows

Defaults:

| Resource | Default retention | Reason |
|---|---:|---|
| Workspace/session roots | 30 days | Main disk-pressure target while keeping recent debugging state. |
| Artifact directories | 90 days | Logs, diagnostics, summaries, and continuity artifacts are more operator-visible than checkouts. |
| Run/session JSON records | Disabled | Records are small and useful for audit/debugging; deletion is opt-in. |
| Grace window | 1 hour | Avoid racing newly completed runs or just-written records. |

### 8.7 Configuration

The API and agent-runtime worker receive the same effective values. The API reconciles the Temporal schedule to `JANITOR_ENABLED` on every startup; manual Temporal pause state is not configuration authority and does not survive reconciliation. Persist an opt-out with `JANITOR_ENABLED=false`.

Environment variables:

| Env var | Default | Meaning |
|---|---:|---|
| `MOONMIND_MANAGED_RUNTIME_JANITOR_ENABLED` | `true` | Enables both the retained-state activity and recurring schedule. `false` disables the activity and pauses the schedule. |
| `MOONMIND_MANAGED_RUNTIME_JANITOR_DRY_RUN` | `false` | When `true`, leaves the schedule active and reports eligible deletes without deleting. |
| `MOONMIND_MANAGED_RUNTIME_WORKSPACE_RETENTION_DAYS` | `30` | Workspace/session-root retention. |
| `MOONMIND_MANAGED_RUNTIME_ARTIFACT_RETENTION_DAYS` | `90` | Local managed-runtime artifact retention. |
| `MOONMIND_MANAGED_RUNTIME_RECORD_RETENTION_DAYS` | unset | Optional run/session JSON record retention; unset means no record deletion. |
| `MOONMIND_MANAGED_RUNTIME_JANITOR_GRACE_SECONDS` | `3600` | Minimum delay after newest owner activity before deletion. |
| `MOONMIND_MANAGED_RUNTIME_JANITOR_MAX_DELETE_PATHS` | `100` | Per-pass deletion cap. Hourly repetition lets larger backlogs converge. |
| `MOONMIND_MANAGED_RUNTIME_JANITOR_MAX_DELETE_BYTES` | unset | Optional per-pass estimated byte cap. |
| `MOONMIND_MANAGED_RUNTIME_JANITOR_LOCK_PATH` | `/work/agent_jobs/.janitor.lock` | Process-level janitor lock. |

### 8.7.1 Upgrade behavior

An installation without explicit janitor settings adopts enabled, non-dry-run cleanup on upgrade. Startup also enables a schedule that was created paused solely under the former default. The first and every later pass remain bounded by the path and optional byte budgets.

Before upgrading, operators may set `MOONMIND_MANAGED_RUNTIME_JANITOR_DRY_RUN=true` to inspect candidates. Operators requiring indefinite local retention must set `MOONMIND_MANAGED_RUNTIME_JANITOR_ENABLED=false`. Otherwise, terminal workspaces older than 30 days and eligible unreferenced local artifact directories older than 90 days may be deleted after startup. There is no implicit first-run dry run.

### 8.8 Safety gates

Before deleting a workspace or artifact root, the janitor must verify all of the following:

1. The janitor is enabled.
2. The candidate path resolves under an allowed canonical root.
3. The candidate path is not a symlink.
4. Both run and session stores were readable.
5. All referencing run records are terminal.
6. All referencing session records are terminal.
7. No referencing record has `activeTurnId`.
8. No live Docker container or active volume mount references the session/workspace.
9. The newest relevant timestamp is older than both retention and grace windows.
10. The candidate is still eligible after a second just-before-delete scan.
11. The per-pass path and byte budgets allow deletion.
12. Dry-run mode is disabled.

If any gate fails, the candidate must be skipped with a reason.

### 8.9 Delete protocol

Use a two-phase filesystem protocol:

1. Acquire the janitor lock.
2. Scan stores and filesystem candidates.
3. Build ownership groups.
4. Classify candidates as protected, eligible, skipped, or errored.
5. Re-load records and re-check live Docker state immediately before delete.
6. Rename the candidate to a quarantine path such as `.gc-<uuid>-<name>` in the same parent.
7. Delete the quarantine path best-effort.
8. Emit structured pass results.

The rename step narrows races: a newly launched run will recreate or use the canonical path, not a partially deleted tree.

### 8.10 Result shape

The activity should return a compact structured result:

```python
@dataclass(frozen=True)
class ManagedRuntimeCleanupResult:
    disabled: bool
    dry_run: bool
    scanned_run_records: int
    scanned_session_records: int
    scanned_workspace_roots: int
    scanned_artifact_dirs: int
    protected_roots: int
    eligible_roots: int
    deleted_roots: int
    deleted_artifact_dirs: int
    deleted_record_files: int
    estimated_deleted_bytes: int
    skipped_active: int
    skipped_recent: int
    skipped_unsafe_path: int
    skipped_ambiguous_owner: int
    errors: tuple[str, ...]
```

---

## 9. State and path classification

### 9.1 Candidate classification

Each candidate should receive exactly one final classification:

| Classification | Meaning |
|---|---|
| `protected_active` | At least one owner is active or has `activeTurnId`. |
| `protected_recent` | All owners are terminal, but retention/grace has not elapsed. |
| `protected_shared` | A shared workspace has at least one recent or active owner. |
| `eligible` | All safety gates pass, but dry-run may prevent deletion. |
| `deleted` | Candidate was renamed and removed. |
| `skipped_unsafe_path` | Path is outside canonical roots, symlinked, or traversal-like. |
| `skipped_ambiguous_owner` | Ownership cannot be derived safely. |
| `error` | Candidate was safe to consider, but an unexpected error occurred. |

### 9.2 Activity timestamp

Use the newest available timestamp from all owners and the filesystem:

1. run `finished_at`,
2. run `last_heartbeat_at`,
3. run `last_log_at`,
4. run `started_at`,
5. session `updated_at`,
6. session `last_log_at`,
7. session `started_at`,
8. candidate path mtime.

A missing timestamp should make a candidate more conservative, not less.

---

## 10. Observability and operator controls

The cleanup systems should expose:

- per-pass structured logs,
- count metrics by resource class and skip reason,
- dry-run candidate samples,
- delete budget exhaustion counters,
- error counters with safe path identifiers,
- current schedule state in Temporal,
- a way to disable each cleanup system independently.

Operators should be able to answer:

- How many managed-session containers were reaped?
- How many sidecar volumes were reaped?
- How many workspaces are eligible for deletion?
- Why was a specific workspace skipped?
- How much disk would dry-run delete?
- What was actually deleted in the last pass?

---

## 11. Acceptance tests

The implementation should include tests for:

1. old terminal unique workspace is eligible;
2. old terminal workspace is protected when another active run shares it;
3. old terminal workspace is protected when another recent terminal run shares it;
4. active managed session protects its session root;
5. terminal managed session becomes eligible only after retention and grace;
6. orphan reaper continues to delete containers and sidecar volumes but never workspaces;
7. dry-run reports candidates without deleting;
8. corrupt run/session JSON records fail closed;
9. paths outside `/work/agent_jobs` are skipped;
10. symlink candidates are skipped;
11. live Docker references protect otherwise eligible roots;
12. second scan prevents deletion if a new active owner appears;
13. delete path and byte budgets stop a pass cleanly;
14. artifact directories are retained longer than workspace roots;
15. record deletion is disabled when record retention is unset.

---

## 12. Desired-state statement

MoonMind cleanup should converge on this rule:

> Live-runtime cleanup removes leaked containers, volumes, sockets, config, and short-lived support files as soon as they are no longer active. Retained-state cleanup removes workspaces, artifacts, and records only after all durable owners are terminal, retention has elapsed, and canonical path safety gates pass.

This preserves fast leak cleanup without turning lifecycle paths into broad filesystem garbage collectors, while making bounded ownership-aware retention an operational default and keeping dry-run and opt-out controls explicit.
