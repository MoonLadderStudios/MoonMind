# MoonSpec Docs-First Alignment Plan

Status: proposal (2026-06-11). Execution plan — lives in `docs/tmp/` per Constitution XII.

## Goals

1. Align MoonSpec skills and presets around canonical `docs/` documents as the primary source of truth; all generated spec artifacts are temporary, derived views.
2. Strongly separate **declarative docs** (desired state) from **imperative artifacts** (checklists, status trackers, migration plans) in MoonSpec skill doctrine.
3. Add a final orchestrate step that updates the declarative doc when implementation discoveries definitely require it (best practices, function, or consistency).

## Current-state findings

- `specs/` and `artifacts/` are gitignored — spec packets are already physically temporary, but no moonspec skill says so. `moonspec-verify` even calls `spec.md` "the source of truth for final alignment," inverting the intended hierarchy.
- No moonspec skill mentions `docs/`, "canonical," or "declarative vs imperative." The doctrine exists only in Constitution XI/XII, CLAUDE.md, and the `document-update` skill.
- `jira-breakdown-orchestrate` labels its input "Declarative Design Path or Text" but nothing validates the input is declarative; an imperative migration checklist would be decomposed into stories as if its steps were requirements.
- `jira-orchestrate` ends at verify → PR → Code Review with no doc reconciliation. Discoveries made during implementation never flow back to the source doc.
- Stale guidance conflict: Constitution XII and `document-update` (line 29) still route migration notes to `specs/<feature>/`, while CLAUDE.md declares `specs/` deprecated in favor of `docs/tmp/` and gitignored handoffs.
- `jira-orchestrate` already accepts `source_design_path`, but `story.create_jira_orchestrate_tasks` does not populate it from story `sourceReference.path` — the canonical doc path is not guaranteed to reach downstream tasks.

## Decisions (confirmed)

- Doc updates land in the **same PR** as the implementation (step inserted before "Create pull request").
- A **new thin skill** `moonspec-doc-reconcile` owns the reconciliation gate; it borrows editing doctrine from `document-update`.
- The plan **includes** fixing the stale `specs/` references in the constitution and `document-update`.

---

## Phase 1 — Doctrine foundation

### 1.1 New canonical doc: `docs/Workflows/MoonSpecDocumentModel.md`

Declarative description of the MoonSpec document model (no migration framing):

- **Document classes**:
  - *Canonical declarative docs* (`docs/`, constitution): desired state — architecture, contracts, operator-visible behavior, target semantics. Long-lived, version-controlled, the primary source of truth.
  - *Temporary execution artifacts* (`specs/`, `artifacts/`, `artifacts/story-breakdowns/`): derived, run-scoped, gitignored, disposable. Never cited as authority for desired state.
  - *Imperative working documents* (`docs/tmp/`, gitignored handoffs): checklists, status trackers, migration/rollout plans. Time-bound; deleted or archived on completion.
- **Precedence rule**: when a derived artifact (spec.md, stories.json, tasks.md) conflicts with its canonical source doc, the source doc wins unless implementation evidence shows the doc itself is wrong — in which case the doc must be updated or the conflict escalated, never silently overridden in the derived artifact.
- **Classification rule**: a document is declarative if it describes what the system is/should be; it is imperative if its primary framing is steps, phases, checkboxes, or status. Mixed documents are classified by primary framing.
- **Reconciliation expectation**: implementation runs that discover canonical-doc drift end with a doc-reconciliation pass (operationalizes Constitution XI "update the owning docs/ files first").

All skill changes below reference this doc by path instead of duplicating doctrine prose.

### 1.2 Reconcile stale `specs/` guidance

- **Constitution XI/XII**: amend to state that `specs/<feature>/` packets are gitignored, run-local, temporary artifacts (not "supplemental history" in version control), and that migration narratives/rollout checklists belong in `docs/tmp/` or gitignored handoff paths. Keep the desired-state rules unchanged.
- **`document-update` SKILL.md**: replace the `specs/<feature-id>/` boundary (line 29) with `docs/tmp/` + `artifacts/`; add a pointer to `MoonSpecDocumentModel.md`.
- **CLAUDE.md**: already consistent; add a pointer to the new doctrine doc in the documentation section.

## Phase 2 — Skill text updates

All edits per Constitution XIII: replace language outright, no compatibility phrasing.

### 2.1 `moonspec-breakdown`

- **Input classification**: before extracting coverage points, classify the source as declarative or imperative per `MoonSpecDocumentModel.md`. If the input is primarily an imperative checklist/migration/status document, **fail fast** with a clear error directing the user to either supply the underlying declarative doc or explicitly confirm imperative input (constitution prefers fail-fast over hidden fallback).
- **Canonical anchoring**: when the input is a path under `docs/`, record it as the canonical source of truth; add `sourceDocumentClass` (`canonical-declarative` | `declarative-text` | `imperative-override`) to `stories.json` `source`.
- **Key rules**: add "Breakdown output is a temporary derived view of the canonical document. The canonical document remains the source of truth for desired state."

### 2.2 `moonspec-specify`

- State explicitly: `spec.md` is a temporary execution artifact derived from the request/source doc; it is never the source of truth for desired state.
- For source-backed requests, record the canonical doc path and class in the spec header alongside `**Input**`.
- Precedence: if spec drafting reveals a conflict between the request and the canonical doc, surface it as `[NEEDS CLARIFICATION]` or a flagged conflict — do not silently resolve toward either side.
- Quality checklist: add "Spec does not contradict its canonical source document, or contradictions are explicitly flagged."

### 2.3 `moonspec-plan` / `moonspec-tasks`

- One-line doctrine note each: `plan.md` / `tasks.md` are imperative, temporary execution artifacts; their content must never be copied into canonical `docs/` files.
- `moonspec-tasks`: the generated breakdown must include a final doc-reconciliation task (after `/speckit.verify`) when the spec is source-backed by a canonical doc.

### 2.4 `moonspec-implement`

- **Discovery ledger** (new requirement): when implementation deviates from the canonical source doc, or reveals the doc is wrong, incomplete, or internally inconsistent, append a structured entry to a gitignored handoff (`artifacts/doc-discoveries/<feature>.json`): `{docPath, section, claim, observed, evidence, severity: definite|possible}`. No entry when there is nothing to report.
- Discoveries do not authorize doc edits during implementation; they feed `moonspec-doc-reconcile`.

### 2.5 `moonspec-verify`

- Replace "the original request in `spec.md` is the source of truth" with: the original request as preserved in `spec.md`, **interpreted against the canonical source document**, is the alignment baseline.
- New report section **Source Document Drift**: claims in the canonical doc contradicted by verified implementation evidence. Doc drift alone does not block `FULLY_IMPLEMENTED` when the implementation is correct per agreed scope; it is structured input for reconciliation. Remains read-only.

### 2.6 `moonspec-align`

- Clarify scope: alignment operates only among temporary artifacts (spec/plan/tasks). It must never edit canonical `docs/` files and never "aligns" a canonical doc toward a spec.

### 2.7 `moonspec-orchestrate` (skill)

- Add stage 7 **Reconcile Declarative Docs** (after Verify, only on `FULLY_IMPLEMENTED`, only when a canonical source doc exists): run `moonspec-doc-reconcile`.
- Add a Doc Reconcile gate (`UPDATED` / `NO_UPDATE_REQUIRED` / `ESCALATED`) and a `Doc Reconcile:` line in the final report.
- Core rules: add the precedence rule and a pointer to `MoonSpecDocumentModel.md`.

### 2.8 `story-reconcile-implementation`

- When repository evidence shows the canonical source doc itself is stale (not just the story), record it in the markdown report as a doc-drift note. No behavior change to Jira actions.

## Phase 3 — New skill: `moonspec-doc-reconcile`

`.agents/skills/moonspec-doc-reconcile/SKILL.md`. Thin, gate-focused; delegates editing doctrine to `document-update` conventions.

- **Inputs** (required): canonical source doc path(s) from `spec.md`/breakdown `sourceReference`; latest `moonspec-verify` report (Source Document Drift section); `artifacts/doc-discoveries/<feature>.json` when present. If no canonical doc exists, exit `NO_UPDATE_REQUIRED` immediately.
- **Strict update gate** — edit only when a discovery **definitely requires** it, meaning at least one of:
  1. *Function*: the doc as written describes behavior/contracts that are now factually wrong against the verified implementation.
  2. *Consistency*: the implementation correctly resolved an internal contradiction or ambiguity in the doc, and the doc must record the resolution.
  3. *Best practices*: the implementation deliberately and correctly diverged from a documented approach for a defensible reason validated by verification.
  `possible`-severity discoveries, stylistic preferences, and speculative improvements do not pass the gate.
- **Editing rules** (inherited from `document-update`): preserve desired-state framing; never downgrade the doc to match buggy/incomplete code; never insert migration narratives, checklists, or status language into canonical docs (Constitution XII); remove superseded text outright (Constitution XIII).
- **Escalation**: if a required update conflicts with the constitution, README, or architecture direction, do not edit — create a Jira issue via `jira-issue-creator` (same fallback pattern as `document-update`).
- **Output contract** (structured, for orchestrate gating): `{action: updated | no_update_required | escalated, docPaths, gateRationale, evidence, jiraIssue?}` plus a short markdown summary for the PR body.
- **Boundaries**: read-only outside `docs/`; never edits spec/plan/tasks; never commits (the PR step owns git).

## Phase 4 — Preset updates

Implementation note (deviation from the original draft): preset `version` labels stay at `1.0.0`. `sync_seed_templates` refreshes the same version row in place when YAML steps change, and `_downstream_task_payload` pins `taskTemplate.version: "1.0.0"` for breakdown-created child runs — bumping would have made those child runs expand the stale old-version steps.

### 4.1 `jira-orchestrate.yaml` (primary target)

- Insert step **Reconcile declarative docs** between "Verify remediation 6 of 6" and "Create pull request":
  - `annotations.jiraOrchestrateRole: doc-reconciliation`
  - Instructions: run only when the controlling verdict is `FULLY_IMPLEMENTED`; consume the verify report + discovery ledger; apply the `moonspec-doc-reconcile` gate; on `escalated`, create the Jira issue and continue (escalation does not block the PR); record the structured result at `artifacts/jira-orchestrate-doc-reconcile.json`.
  - `skill.id: moonspec-doc-reconcile`, `requiredCapabilities: [git]`.
- "Create pull request" step: require the PR body to include the doc reconciliation outcome (updated paths, no-update rationale, or escalation issue key); doc edits ride the same commit/PR.

### 4.2 `moonspec-orchestrate.yaml`

- Add the same step after "Verify completion" with parallel instructions (no Jira/PR mechanics; report outcome in the final summary).

### 4.3 `jira-breakdown-orchestrate.yaml` (primary target)

- **Step 1 (Break down declarative design)**: add input-classification language — resolve the path, confirm the document is declarative per `MoonSpecDocumentModel.md`, fail fast on imperative inputs, record the canonical doc as primary source of truth and breakdown output as temporary.
- **Step 4 (Create dependent Jira Orchestrate tasks)**: require each downstream task to carry the story's `sourceReference.path` as the Jira Orchestrate `source_design_path` so the doc-reconcile step knows its canonical doc.
- Apply the same step-1 language to `jira-breakdown.yaml` and `jira-breakdown-implement.yaml` for consistency.

### 4.4 Code touchpoint

- `story.create_jira_orchestrate_tasks` handler: populate `source_design_path` on created tasks from story `sourceReference.path`. Add/extend tests at the activity/tool boundary per repo testing rules.

## Phase 5 — Tests and verification

- Update preset assertions: `tests/unit/api/test_presets_service.py`, `tests/integration/test_startup_preset_seeding.py` (step counts/titles change), `tests/unit/workflows/temporal/test_temporal_worker_runtime.py` if it enumerates steps.
- Skill resolution: confirm `moonspec-doc-reconcile` is picked up (`tests/unit/services/test_skill_resolution.py`); add coverage if skills are enumerated by name anywhere.
- Run `tools/check_terminology.sh` (new skill/preset wording must comply with the Task→Workflow rename).
- Final: `./tools/test_unit.sh`, then `./tools/test_integration.sh` for preset seeding.

## Compatibility and risks

- **In-flight runs**: presets expand at submission, so running workflows keep their old step lists — inserting a step is safe for in-flight runs. No skill payload shape changes; `stories.json` gains additive fields only.
- **Doc-edit quality risk**: mitigated by the strict gate (definite-severity evidence only), inherited desired-state rules, escalation path, and human PR review (doc changes are visible in the same PR diff).
- **Imperative-input fail-fast** may break existing habits of feeding migration plans into breakdown; the error message must name the override and the preferred alternative (write/point to the declarative doc first).
- **Constitution amendment** (Phase 1.2) is the only change touching governance text; keep it minimal and aligned with the already-ratified XIII delete-don't-deprecate posture.

## Suggested execution order

Phase 1 (doctrine + guidance reconciliation) → Phase 2 (skill text) → Phase 3 (new skill) → Phase 4 (presets + handler) → Phase 5 (tests). Phases 1–3 are mergeable independently; Phase 4 should land with Phase 5 in one change.
