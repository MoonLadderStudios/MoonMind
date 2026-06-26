# Documentation Architecture Migration Plan

> **Imperative working document — not canonical architecture.**
> **Type:** Migration Plan (per `docs/DocumentationArchitecture.md` §4 and the MoonSpec Document Model "imperative working documents" class).
> **Status:** Superseded / closed by MM-928 (opened 2026-06-24; closed 2026-06-26). Time-bound; lives in `docs/tmp/` per Constitution **XV. Canonical Documentation Separates Desired State from Migration Backlog**.
> **Authority:** This plan describes *steps to align existing docs with the standard*. It is **not** authoritative desired-state architecture and must never be cited as the source of truth for what the documentation tree *should be*. The authoritative desired state lives in `docs/DocumentationArchitecture.md` (the Standard) and `docs/Workflows/MoonSpecDocumentModel.md` (the Document Model). Where this plan and either of those disagree, **they win**.
> **Traceability:** MM-907 (*Create a separate migration plan for docs classification and renames*), MM-927 (Moon Spec Doc Architecture Alignment), MM-928 (stabilization cleanup), DESIGN-REQ-017. Source design **MM-900** ("Implement MoonSpec Documentation Architecture Standard"); the canonical standard it applies was authored under **MM-902** (commit `64fa5d0d1`).
> **Closure note:** This file is retained only as historical `docs/tmp/` working evidence. It no longer records active authority conflicts or active migration direction; the current desired-state rules live in the Standard and Document Model.

---

## 1. Purpose and explicit non-goals

This plan exists so that classification and rename cleanup of the **existing** `docs/` tree is tracked as bounded, imperative work — *not* smuggled into the canonical architecture documents as construction-diary prose (Constitution XV; Standard §4 separation rule).

**Goals**

1. Identify existing docs that are clearly **imperative** but currently live beside canonical architecture docs, and move/relabel them.
2. Identify the **high-value** canonical docs to classify (against the five viewpoints) and, where needed, rename **first**.
3. Record the **naming divergences** between the upstream MoonSpec naming conventions, MoonMind's actual `docs/` tree, and downstream project conventions — so renames are deliberate, not ad hoc.
4. Capture any **unresolved documentation-authority conflict** found while doing the above (§7).

**Non-goals (do not do these as part of this plan)**

1. **No full backfill.** The standard is useful immediately without classifying or renaming every doc. This plan deliberately does **not** require a tree-wide sweep, a "100% of docs carry a viewpoint label" gate, or a mass rename as a prerequisite for the standard to take effect. New and edited docs follow the standard from now on; legacy docs are reclassified opportunistically and by priority only.
2. **No rewriting of canonical content.** Classification and renaming are structural/labeling actions. This plan does not authorize editing the substance of architecture views (that is normal doc work or doc reconciliation, governed elsewhere).
3. **No compatibility shims for paths.** When a doc is renamed or moved, update every in-repo reference and delete the old path in the same change (Constitution XVI). Do not leave redirect stubs or aliased copies.
4. **Not authoritative.** This document does not introduce new architecture, viewpoints, or rules. It only sequences cleanup against the already-ratified standard.

---

## 2. How to classify (reference, not redefinition)

Apply the existing rules; this plan does not restate them in full:

- **Document class** — declarative vs imperative by *primary framing* (Document Model, classification rule). Imperative = primary framing is steps, phases, checkboxes, or status.
- **Viewpoint (for canonical declarative docs)** — exactly one of the five in Standard §3: System Architecture View, Module Architecture View, System / Feature Design View, Module Contract Specification, Cross-Cutting Concept View.
- **Imperative type (for working docs)** — one of the four in Standard §4: Migration Plan, Implementation Plan, Rollout Plan, Status / Checklist Tracker. These belong under `docs/tmp/` or a gitignored handoff path, never in the canonical tree.

A doc is a **classification target** when its current placement/label is ambiguous against these rules. It is a **rename target** when its filename/title diverges from the preferred naming for its (already clear) viewpoint or type.

---

## 3. Imperative docs that currently live beside canonical architecture docs

These are concrete examples found in the current tree (2026-06-24) whose **primary framing is imperative** yet which sit inside canonical `docs/` module/concern directories. They violate the §4 separation rule and should be relocated to `docs/tmp/` (or archived/deleted if the work is complete). This is an enumeration of known offenders, **not** a claim that the list is exhaustive.

| Doc | Why it is imperative | Action |
|-----|----------------------|--------|
| `docs/Temporal/WorkflowLanguageHardSwitchPlan.md` | Header is `Status: Proposed hard-switch implementation plan`; primary framing is a phased cutover plan. | Move to `docs/tmp/WorkflowLanguageHardSwitchPlan.md`; if the hard switch is already complete, archive/delete per §6 and promote any durable target semantics into the relevant Temporal view first. |
| `docs/ReleaseNotes/MM-730-hard-switch-cutover.md` | A cutover release note: operational sequencing + breaking-change instructions for a specific migration boundary. | Keep release notes as historical record **or** relocate the cutover/rollout sequencing portion to `docs/tmp/`. Decide release-note placement policy (see §7, conflict C2). |
| `docs/ReleaseNotes/2026-06-02-jira-implement-workflow-batch.md` | Dated batch release note; not desired-state. | Same disposition decision as the row above. |
| `docs/Rag/ManifestIngestDesign.md` | Self-described "Design **& Implementation**" with implementation-level detail mixed into a canonical Design doc. | Split: keep the design intent as a System / Feature Design View (with a proper `Status:` field), move implementation/rollout detail to `docs/tmp/`. |

The bottom of `docs/Rag/ManifestIngestDesign.md` already includes the correct pointer pattern ("Rollout and backlog notes live under `docs/tmp/`…") — confirm the imperative content actually lives there and is not inline.

---

## 4. High-value docs to classify first

Classification effort is prioritized, not uniform. Do these **first** because they are the most-referenced entry points (cited by `CLAUDE.md`, the Standard, or many other docs) and the most likely to be mis-read if their class/viewpoint is ambiguous. Lower-traffic docs are reclassified later or on-touch.

**Tier 1 — entry points named in `CLAUDE.md` / the Standard (classify first):**

1. `docs/MoonMindArchitecture.md` — should read cleanly as the **System Architecture View** (Standard §3.1). Confirm label.
2. `docs/Steps/SkillSystem.md` — canonical entry for Agent Skills; classify as Module Architecture View or Cross-Cutting Concept View and confirm its naming.
3. `docs/Workflows/SkillAndPlanContracts.md` — canonical **Module Contract Specification** (Standard §3.4); confirm it is owned by the Workflows module doc set and that consumers link, not copy (Standard §6).
4. `docs/Temporal/ManagedAndExternalAgentExecutionModel.md` — runtime-boundary doc; classify as Module Architecture View vs Cross-Cutting Concept View (the Standard cites it as an example of *both* — resolve which, see §7 conflict C3).
5. `docs/Workflows/MoonSpecDocumentModel.md` and `docs/DocumentationArchitecture.md` — the governing docs themselves; confirm they are explicitly **not** classified as views subject to reclassification (they define the taxonomy).

**Tier 2 — module "Architecture" hubs (each module doc set's anchor):**

- `docs/Temporal/TemporalArchitecture.md`, `docs/Workflows/WorkflowArchitecture.md`, `docs/ManagedAgents/ManagedAgentArchitecture.md`, `docs/UI/WorkflowConsoleArchitecture.md`, `docs/Memory/MemoryArchitecture.md` — each is the Module Architecture View for its directory; confirm naming against §5 below.

**Tier 3 — design docs needing a `Status:` field (Standard §3.3 requirement):**

- `docs/Workflows/StepReviewGateSystem.md` (currently `Status: Design Draft`), `docs/Rag/ManifestIngestDesign.md` (`Status: Draft`), and any other `*Design.md`. The standard requires the status be exactly one of `Proposed | Accepted | Implemented | Superseded`. Normalize these.

Everything not in Tier 1–3 is explicitly **deferred** and is not part of any completion gate for this plan.

---

## 5. Naming divergences to record (MoonSpec ↔ MoonMind ↔ downstream)

These divergences are recorded so renames are intentional. **Recording a divergence is not a mandate to rename every instance now** — most are handled on-touch. The standard (§7) already declares that the taxonomy is identical regardless of these surface differences.

| # | Divergence | Upstream/Standard convention | MoonMind actual | Downstream projects | Disposition |
|---|------------|------------------------------|-----------------|---------------------|-------------|
| N1 | **Docs root capitalization** | Standard §7 supports both `docs/` and `Docs/`. | `docs/` (lowercase). | Some downstream trees use `Docs/`. | No action — taxonomy is capitalization-independent; only the path prefix changes. Record only. |
| N2 | **`…System.md` suffix proliferation** | The five viewpoints have no "System View"; preferred names are `Architecture.md`, `<Feature>Design.md`, `<Surface>Contracts.md`, concept-named. | Many files use `…System.md` (e.g. `SkillSystem.md`, `TemporalSignalsSystem.md`, `WorkflowStepSystem.md`, `SettingsSystem.md`, `StepReviewGateSystem.md`). | Downstream may not use `…System`. | Do **not** mass-rename. When a `…System.md` doc is next substantively edited, decide its viewpoint and rename to the preferred form for that viewpoint. Record the pattern; defer the sweep. |
| N3 | **Module Architecture View naming** | §3.2 now prefers `<ModuleName>ModuleArchitecture.md` inside the module dir. | MoonMind repeats the module name in older files such as `TemporalArchitecture.md`, `WorkflowArchitecture.md`, and `ManagedAgentArchitecture.md`. | Varies. | Historical note only. New module architecture docs use `<ModuleName>ModuleArchitecture.md`; existing filenames are handled on-touch per the standard's incremental adoption policy. |
| N4 | **Contract placement / naming** | §3.4 + §6: contracts named `<Contract>Contract.md` / `<Surface>Contracts.md`, owned **inside the providing module's doc set**. | `docs/Api/ExecutionsApiContract.md` and `docs/Artifacts/ArtifactPresentationContract.md` sit under surface/concern dirs rather than the providing module's doc set. | Varies. | Tier-2 review: confirm the owning module, and either move the contract into that module's doc set with back-links from consumers, or document why `Api/` is itself the owning surface. See §7 conflict C1. |
| N5 | **Design `Status:` enum** | §3.3: `Proposed \| Accepted \| Implemented \| Superseded`. | Free-form: `Design Draft`, `Draft (date)`. | Normalize Tier-3 design docs to the enum (§4 Tier 3). |
| N6 | **Lowercase-hyphen filenames** | MoonMind doc convention is PascalCase `.md`. | `docs/Temporal/ops-runbook.md` (and similar) break the convention. | n/a | On-touch rename to PascalCase (`OpsRunbook.md`) and update references; defer if untouched. |
| N7 | **Research reports as canonical docs** | The five viewpoints have no "Research" type; research is closer to a temporary/derived artifact than desired-state. | `docs/Temporal/TemporalSignalsResearch.md`, `docs/Memory/MemoryResearch.md` are deep-research reports living in canonical dirs. | n/a | Classify: either fold durable conclusions into the relevant view and archive the report to `docs/tmp/` or a handoff path, or explicitly mark them as retained reference research. See §7 conflict C4. |

---

## 6. Delete / archive criteria for **this** plan

This plan is itself an imperative working document and must not outlive its usefulness (Constitution XV; Standard §4).

**Delete or archive this file when any of the following holds:**

- **Done:** The Tier-1 entry-point docs (§4) are classified, the §3 imperative-in-canonical offenders are relocated/archived, and the Tier-3 `Status:` normalizations are applied. Remaining items are explicitly deferred (on-touch) and need no standing plan.
- **Superseded:** The classification/rename guidance is absorbed into a durable place (e.g. a short "applying the standard to existing docs" note that the standard or `CLAUDE.md` points to), making this scratch plan redundant.
- **Stale:** The plan has had no activity for one release cycle and its open items are either complete or no longer relevant. A stale plan is a tech-debt bug (Constitution XVI), not a kindness — delete it.

**Archive vs delete:** Prefer **delete** (the standard and Document Model are the durable record). Archive only if an unresolved §7 conflict still needs a home and no Jira issue captures it; in that case move the residual conflict notes to the owning canonical doc's tracking issue or a fresh issue, then delete this file.

When closing this plan, remove it in the same change that lands the last tracked action — do not leave a completed plan in `docs/tmp/`.

---

## 7. Historical documentation-authority conflicts (closed)

The items below were conflicts surfaced while drafting this historical plan. They are **not active authority conflicts** after MM-928: the Standard and Document Model are the desired-state authorities, and unresolved follow-up should be tracked in Jira or in the owning canonical document's review, not by reviving this stale `docs/tmp/` plan.

- **C1 — Contract ownership for shared surfaces.** Closed as a historical classification question; apply Standard §6 when a contract document is next substantively edited.
- **C2 — Release notes vs imperative working docs.** Closed as a historical classification question; release-note policy is outside this plan's active scope.
- **C3 — Dual-classified example in the standard.** Closed by MM-928's standard cleanup: §3.2 now uses a module-architecture filename example rather than the cross-cutting execution-model example.
- **C4 — "Research" reports have no viewpoint.** Closed as a deferred on-touch classification question, not an active conflict in this plan.

Escalation path for any future conflict: do **not** resolve it by editing canonical docs unilaterally. Raise it with the standard's owner (MM-900/MM-902 lineage) via a Jira issue or PR comment, following the same escalation posture as `moonspec-doc-reconcile` / `document-update`.

---

## 8. Working sequence (bounded)

1. Resolve the Tier-1 classifications in §4 (label-only; no content rewrites).
2. Relocate the §3 imperative-in-canonical offenders to `docs/tmp/` (or archive if complete), updating all references and deleting old paths in the same change.
3. Normalize Tier-3 design `Status:` fields (N5).
4. Record decisions for conflicts C1–C4 (issue/PR comment); apply N4 contract moves only after C1 is decided.
5. Apply N2/N6 renames **on-touch** only; do not schedule a sweep.
6. When §6 delete/archive criteria are met, delete this file in the closing change.

No step here is a prerequisite for the standard itself being in force — the standard governs all new and edited docs from MM-902 onward regardless of this backlog's progress.
