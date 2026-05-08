# MoonSpec Breakdown — Task Architecture (Control Plane)

- **Source design title:** Task Architecture (Control Plane)
- **Source design path:** `docs/Tasks/TaskArchitecture.md`
- **Reference path (preserved on every story):** `docs/Tasks/TaskArchitecture.md`
- **Extracted at:** 2026-05-08T21:35:22Z
- **Output mode:** jira
- **Coverage gate:** `PASS - every major design point is owned by at least one story.`

## Design summary

`docs/Tasks/TaskArchitecture.md` defines MoonMind's desired-state task control plane. Mission Control authors task intent (objective text, step instructions, objective- and step-scoped attachments, runtime/publish, repository + single authored branch, preset bindings, Jira imports, dependencies). The control plane normalizes that intent into a canonical task-shaped contract, persists an authoritative task input snapshot, resolves recursive presets at compile time, and submits a fully resolved execution payload to Temporal. The execution plane (`MoonMind.Run`, optional `MoonMind.AgentRun` children) owns lifecycle progression, prepare-time attachment materialization with target-aware context, step ledger durability, and resume checkpoint production. Failed tasks expose three distinct recovery actions — Edit task (edited full retry), Rerun (exact full rerun), and Resume (failed-step recovery from durable checkpoints) — with backend-computed Resume eligibility pinned to the original `sourceWorkflowId` and `sourceRunId`. Architectural invariants forbid binary bytes in workflow history, silent attachment loss, hidden retargeting, live preset lookup at execution time, semantic drift in compatibility shims, browser access to non-MoonMind APIs, and silent re-execution of preserved steps.

## Coverage points (DESIGN-REQ-*)

| ID | Title | Section |
|---|---|---|
| DESIGN-REQ-001 | Task-first control plane translates user intent into execution-plane contracts | 3.1 |
| DESIGN-REQ-002 | Artifact-first binary handling: refs not bytes in workflow history | 3.2; Inv 1 |
| DESIGN-REQ-003 | Explicit attachment target binding (objective vs step) survives full lifecycle | 3.3; Inv 2 |
| DESIGN-REQ-004 | Durable snapshot-based reconstruction; no silent attachment loss | 3.4; Inv 3, 5 |
| DESIGN-REQ-005 | Text remains text; images remain structured inputs | 3.5; Inv 4 |
| DESIGN-REQ-006 | Failed-step Resume is distinct from full rerun | 3.6; Inv 13 |
| DESIGN-REQ-007 | Authoring & validation incl. Steps-card placement of Branch/Publish Mode | 5.1 |
| DESIGN-REQ-008 | Browser artifact upload orchestration submits only structured refs | 5.2 |
| DESIGN-REQ-009 | Task contract normalization preserves attachments, step identity/order, runtime/publish, preset/Jira metadata | 5.3 |
| DESIGN-REQ-010 | Compile-time preset composition; no live preset catalog lookup at execution | 5.4; Inv 6 |
| DESIGN-REQ-011 | Authoritative task input snapshot durability with full preset provenance | 5.5; Section 7; Inv 5, 7 |
| DESIGN-REQ-012 | User-facing reads expose previews/downloads and per-target attachment metadata | 5.6 |
| DESIGN-REQ-013 | Distinct failed-task recovery actions Edit / Rerun / Resume with backend eligibility | 5.7; Inv 13 |
| DESIGN-REQ-014 | Single authored branch field `task.git.branch` (no `targetBranch`) | Section 6 rules |
| DESIGN-REQ-015 | TaskRecoveryProvenance and ResumeFromFailedStepRef contracts | Section 6 |
| DESIGN-REQ-016 | Editable full retry workflow | 7.1 |
| DESIGN-REQ-017 | Exact full rerun workflow | 7.2 |
| DESIGN-REQ-018 | Resume from failed step workflow with resume checkpoint artifact | 7.3; Inv 14, 15, 17 |
| DESIGN-REQ-019 | MoonMind.Run owns durable progression, retries, ledger preservation | 8.1; 12.1 |
| DESIGN-REQ-020 | Prepare activity downloads, manifests, target-aware materialization with explicit failure | 8.2 |
| DESIGN-REQ-021 | Target-aware step execution: only own step-scoped + objective context | 8.3; Inv 10 |
| DESIGN-REQ-022 | MoonMind.AgentRun child workflow inherits parent target binding | 8.4; 12.2 |
| DESIGN-REQ-023 | Resume checkpoint durable evidence; idempotent writes | 8.5 |
| DESIGN-REQ-024 | Resume execution semantics: validated checkpoint, preserved prior steps, retried failed step, no silent fallback | 8.6; Inv 16 |
| DESIGN-REQ-025 | Artifact + authorization boundary: no long-lived browser creds; execution-scoped links | Section 9 |
| DESIGN-REQ-026 | Runtime/prompt boundary: text-first vs multimodal adapters | Section 10 |
| DESIGN-REQ-027 | Server-defined attachment policy enforced by both browser and API | Inv 8 |
| DESIGN-REQ-028 | Browser talks only to MoonMind APIs (no direct Jira/object store/provider endpoints) | Inv 9 |
| DESIGN-REQ-029 | No hidden retargeting from reorder/preset apply/text edits | Inv 11 |
| DESIGN-REQ-030 | Compatibility shims preserved without semantic drift | Inv 12 |
| DESIGN-REQ-031 | Operator-facing observability for attachments, recovery, and Resume diagnostics | Section 13 |

## Story candidates (in ordered, dependency-aware sequence)

Each story carries `sourceReference.path = docs/Tasks/TaskArchitecture.md`.

### STORY-001 — Define canonical task-shaped contract & server-side normalization
- **Source sections:** 3.3, 3.5, 5.3, 6
- **Owns:** DESIGN-REQ-001, 003, 005, 009, 014
- **Independent test:** Round-trip a representative create payload through the normalizer and assert: attachments stay bound to their original targets; step identity/order preserved; `targetBranch` rejected; `authoredPresets` and `steps[].source` preserved; recovery/resume kinds round-trip; payloads with inline binary bytes are rejected.

### STORY-002 — Authoritative task input snapshot durability for edit/rerun/resume
- **Source sections:** 3.4, 5.5, 7
- **Owns:** DESIGN-REQ-004, 011
- **Depends on:** STORY-001
- **Independent test:** Submit a task with attachments, recursive presets, dependencies, runtime/publish, branch. Mutate the live preset catalog and reconstruct edit/rerun browser state from the snapshot; verify attachment bindings, step provenance/order, pinned preset bindings, include-tree summary, detachment state, repo/branch and runtime/publish are all restored verbatim and the live catalog is not consulted.

### STORY-003 — Browser artifact upload orchestration & MoonMind-only API boundary
- **Source sections:** 5.2, 9, Inv 8, Inv 9
- **Owns:** DESIGN-REQ-008, 025, 027, 028
- **Depends on:** STORY-001
- **Independent test:** Browser test that uploads multiple files via MoonMind upload-intent/finalize endpoints; submission only carries structured refs; partial upload failure blocks submission; network traces show no long-lived object-store credentials and no direct Jira/S3/provider endpoints.

### STORY-004 — Create page authoring & validation with Steps-card branch/publish placement
- **Source sections:** 3.1, 5.1
- **Owns:** DESIGN-REQ-001, 007
- **Depends on:** STORY-001
- **Independent test:** Render the Create page, confirm Repository/Branch/Publish Mode controls live inside the Steps card, exercise validation paths (missing repo, invalid runtime, conflicting publish mode, attachment policy violation, malformed dependency), and verify a valid draft submits a canonical TaskPayload (no `targetBranch`).

### STORY-005 — Compile-time preset composition with provenance preservation
- **Source sections:** 5.4, Inv 6, 7
- **Owns:** DESIGN-REQ-010 (and contributes to 011)
- **Depends on:** STORY-001
- **Independent test:** Author nested presets with detached overrides, compile, then mutate or remove the preset in the catalog; verify the resolved execution payload still executes against worker contracts; `authoredPresets` and `steps[].source` preserved; cycles and missing includes rejected at compile time.

### STORY-006 — Distinct failed-task recovery actions with backend-computed Resume eligibility
- **Source sections:** 3.6, 5.7, Inv 13
- **Owns:** DESIGN-REQ-006, 013, 015
- **Depends on:** STORY-001, STORY-002
- **Independent test:** Compose three failed executions with varying Resume-evidence states; verify capability endpoint flips Resume on/off correctly with operator-readable rejection reasons; verify each action submits the correct recovery/resume payload with pinned `sourceWorkflowId`/`sourceRunId`.

### STORY-007 — Editable full retry workflow
- **Source sections:** 7.1
- **Owns:** DESIGN-REQ-016
- **Depends on:** STORY-002, STORY-006
- **Independent test:** From a failed execution, choose Edit task; Create page hydrates from snapshot; modify fields and submit; new execution starts from the beginning with its own new snapshot; original execution's snapshot/ledger/artifacts/checkpoints remain immutable; no completed progress imported.

### STORY-008 — Exact full rerun workflow
- **Source sections:** 7.2
- **Owns:** DESIGN-REQ-017
- **Depends on:** STORY-002, STORY-006
- **Independent test:** From a failed execution, choose Rerun; no edit form is presented; submission carries `kind=exact_full_rerun` with pinned source ids; new execution reuses the original snapshot unchanged and runs the full pipeline from the beginning; no progress imported.

### STORY-009 — Step ledger & resume checkpoint durability in MoonMind.Run
- **Source sections:** 8.1, 8.5, 12.1, Inv 15
- **Owns:** DESIGN-REQ-019, 023
- **Depends on:** STORY-001
- **Independent test:** Run a multi-step `MoonMind.Run` with retries injected; verify prepared input refs are written once after prepare; per-step bounded state + semantic output refs recorded; workspace/branch/commit checkpoints recorded around mutating steps; idempotent under retry; large refs stored as artifacts; steps lacking evidence are marked Resume-ineligible.

### STORY-010 — Resume execution semantics in MoonMind.Run
- **Source sections:** 7.3, 8.6, Inv 14, 16, 17
- **Owns:** DESIGN-REQ-018, 024
- **Depends on:** STORY-002, STORY-006, STORY-009
- **Independent test:** Trigger Resume with a valid checkpoint and verify preserved prior steps render with source provenance and never re-execute; failed step retried as first new step; downstream steps run normally. Trigger Resume with stale plan digest, missing workspace checkpoint, unauthorized user, or tampered snapshot ref; each must produce explicit failure and never start the failed step.

### STORY-011 — Prepare-time target-aware attachment materialization without retargeting
- **Source sections:** 3.2, 8.2, Inv 1, 3, 11
- **Owns:** DESIGN-REQ-002, 020, 029
- **Depends on:** STORY-001, STORY-003
- **Independent test:** Run prepare against a task with objective and step attachments; verify per-target stable workspace locations, manifest correctness, target-aware image context artifacts, and explicit failure on missing/invalid attachments. Reorder steps and reapply a preset and assert no attachment silently moves between targets. Inspect Temporal history for absence of raw bytes.

### STORY-012 — Target-aware step execution & AgentRun child-workflow scope inheritance
- **Source sections:** 8.3, 8.4, 12.2, Inv 10
- **Owns:** DESIGN-REQ-021, 022
- **Depends on:** STORY-011
- **Independent test:** Run a multi-step task with distinct attachments per step; assert each step receives only objective context plus its own step-scoped context. Repeat with one step executed via `MoonMind.AgentRun`; assert child input is scoped to that step and child logs/diagnostics do not redefine target binding semantics.

### STORY-013 — Runtime/prompt boundary for text-first vs multimodal adapters
- **Source sections:** 10
- **Owns:** DESIGN-REQ-026
- **Depends on:** STORY-001, STORY-011
- **Independent test:** Run the same task through a text-first runtime and a multimodal runtime; text-first must consume INPUT ATTACHMENTS-formatted generated context; multimodal must consume raw image refs through its adapter; assert no adapter introduces a new target kind or routing rule.

### STORY-014 — Operator observability for attachments, recovery, and Resume diagnostics
- **Source sections:** 5.6, 13, Inv 12
- **Owns:** DESIGN-REQ-012, 030, 031
- **Depends on:** STORY-002, STORY-009, STORY-010, STORY-011
- **Independent test:** Open task detail for a failed attachment-aware execution and verify attachments grouped by target with metadata; diagnostics expose manifest/context refs; failed target and phase identified. Open a resumed execution and verify preserved prior steps render as reused with source provenance, and Resume failure diagnostics identify failing phase. Run a compatibility shim test that loads pre-canonical-target metadata and assert canonical objective vs step semantics are preserved.

## Coverage matrix

| Coverage point | Owning stories |
|---|---|
| DESIGN-REQ-001 | STORY-001, STORY-004 |
| DESIGN-REQ-002 | STORY-001, STORY-011 |
| DESIGN-REQ-003 | STORY-001, STORY-011, STORY-012 |
| DESIGN-REQ-004 | STORY-002 |
| DESIGN-REQ-005 | STORY-001, STORY-013 |
| DESIGN-REQ-006 | STORY-006, STORY-010 |
| DESIGN-REQ-007 | STORY-004 |
| DESIGN-REQ-008 | STORY-003 |
| DESIGN-REQ-009 | STORY-001 |
| DESIGN-REQ-010 | STORY-005 |
| DESIGN-REQ-011 | STORY-002, STORY-005 |
| DESIGN-REQ-012 | STORY-014 |
| DESIGN-REQ-013 | STORY-006 |
| DESIGN-REQ-014 | STORY-001, STORY-004 |
| DESIGN-REQ-015 | STORY-006 |
| DESIGN-REQ-016 | STORY-007 |
| DESIGN-REQ-017 | STORY-008 |
| DESIGN-REQ-018 | STORY-010 |
| DESIGN-REQ-019 | STORY-009 |
| DESIGN-REQ-020 | STORY-011 |
| DESIGN-REQ-021 | STORY-012 |
| DESIGN-REQ-022 | STORY-012 |
| DESIGN-REQ-023 | STORY-009 |
| DESIGN-REQ-024 | STORY-010 |
| DESIGN-REQ-025 | STORY-003 |
| DESIGN-REQ-026 | STORY-013 |
| DESIGN-REQ-027 | STORY-003 |
| DESIGN-REQ-028 | STORY-003 |
| DESIGN-REQ-029 | STORY-011 |
| DESIGN-REQ-030 | STORY-014 |
| DESIGN-REQ-031 | STORY-014 |

## Dependencies

- STORY-002 → STORY-001
- STORY-003 → STORY-001
- STORY-004 → STORY-001
- STORY-005 → STORY-001
- STORY-006 → STORY-001, STORY-002
- STORY-007 → STORY-002, STORY-006
- STORY-008 → STORY-002, STORY-006
- STORY-009 → STORY-001
- STORY-010 → STORY-002, STORY-006, STORY-009
- STORY-011 → STORY-001, STORY-003
- STORY-012 → STORY-011
- STORY-013 → STORY-001, STORY-011
- STORY-014 → STORY-002, STORY-009, STORY-010, STORY-011

For Jira export with `linear_blocker_chain` mode, the ordered chain is STORY-001 → STORY-002 → STORY-003 → STORY-004 → STORY-005 → STORY-006 → STORY-007 → STORY-008 → STORY-009 → STORY-010 → STORY-011 → STORY-012 → STORY-013 → STORY-014; this respects every explicit dependency above.

## Out of scope (and rationale)

- **Detailed page-level Create-page behavior** beyond Steps-card placement and validation — Section 14 defers this to `docs/UI/CreatePage.md`.
- **Detailed image-input materialization, preview, and context generation behavior** — Section 14 defers this to `docs/Tasks/ImageSystem.md` (covered architecturally by STORY-011 only at the prepare-activity contract level).
- **Skill selection and resolution mechanics** — Section 14 defers this to `docs/Tasks/AgentSkillSystem.md`.
- **Workflow lifecycle and worker topology** — Section 14 defers this to `docs/Temporal/TemporalArchitecture.md`.
- **Workflow ID / Run ID identity rules and durable rerun naming** — Section 14 defers this to `docs/Temporal/RunHistoryAndRerunSemantics.md`.
- **Step ledger schema and progress projection details** beyond what Resume eligibility requires — Section 14 defers this to `docs/Temporal/StepLedgerAndProgressModel.md`.
- **Failed Task Detail Page presentation specifics** beyond exposing distinct Edit/Rerun/Resume actions — Section 14 defers this to `docs/UI/TaskDetailsPage.md`.
- **Other workflow types** — Section 12.3 explicitly notes they may reuse artifact infrastructure but do not redefine the Create-page attachment contract; this breakdown therefore does not propose stories for those workflow types.

## Recommended first story

**STORY-001 — Define canonical task-shaped contract & server-side normalization.** It has no dependencies, unblocks every other story, and locks the ground-truth shape that snapshot, preset compilation, recovery, prepare, step execution, and observability all reference.

## Stories with unresolved clarifications

None. No story carries `[NEEDS CLARIFICATION]` markers.

## Coverage gate

```text
PASS - every major design point is owned by at least one story.
```

## Confirmations

- No `spec.md` files were created during breakdown.
- No directories under `specs/` were created during breakdown.
- TDD remains the default strategy for downstream `/speckit.plan`, `/speckit.tasks`, and `/speckit.implement`.
- `/speckit.verify` should be run after implementation to compare final behavior against the original design preserved through specify.
