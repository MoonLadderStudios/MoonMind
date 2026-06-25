# MoonSpec Documentation Architecture — Self-Conformance Review

Status: evidence / self-conformance review (2026-06-25). Time-bound guardrail record — lives in `docs/tmp/` per Constitution XV and the standard's own Imperative Working Document placement rule (§4). This is **not** a canonical declarative view and introduces no new documentation authority.

> **Traceability:** This review is the deliverable of **MM-909** (source design **MM-900**, "Implement MoonSpec Documentation Architecture Standard"). It covers **DESIGN-REQ-019** and **DESIGN-REQ-020** (advisory validation and self-conformance). It reviews, but does not redefine, the standard authored by **MM-902** at [`docs/DocumentationArchitecture.md`](../DocumentationArchitecture.md), which extends [`docs/Workflows/MoonSpecDocumentModel.md`](../Workflows/MoonSpecDocumentModel.md).

## Purpose

MM-909 is the guardrail story for the MoonSpec Documentation Architecture Standard. It owns the issue's **non-goals** and **self-conformance** acceptance criteria. This document records the result of:

1. A self-conformance review of every new doc/template/plan introduced by this issue against the declarative/imperative split and the non-goals.
2. The available docs validation / lint / link checks that were run.
3. Any unresolved documentation-authority conflicts (recorded here and cross-referenced from the migration plan).

It is a one-time evidence record; the durable, repeatable form of these guardrails lives in `tests/unit/docs/test_documentation_architecture_conformance.py`.

## Documents in scope

The docs introduced or owned by the MoonSpec Documentation Architecture Standard effort (source MM-900):

| Document | Class | Role |
|----------|-------|------|
| [`docs/DocumentationArchitecture.md`](../DocumentationArchitecture.md) | Canonical declarative view (Cross-Cutting Concept / standard) | The standard itself (MM-902). |
| [`docs/tmp/MoonSpecDocsFirstAlignmentPlan.md`](MoonSpecDocsFirstAlignmentPlan.md) | Imperative working document (Migration/Implementation Plan) | The execution plan for the docs-first alignment effort. |
| `docs/tmp/MoonSpecDocsArchitectureConformanceReview.md` (this file) | Imperative working document (evidence) | This self-conformance record (MM-909). |

The standard layers on the pre-existing [`docs/Workflows/MoonSpecDocumentModel.md`](../Workflows/MoonSpecDocumentModel.md), which predates MM-900 and is not a doc "introduced by this issue"; it is reviewed only as the authority the standard must not contradict.

## Self-conformance review against the non-goals

Each row is an acceptance-criteria condition this guardrail must confirm, the verdict, and the evidence.

| # | Non-goal / condition | Verdict | Evidence |
|---|----------------------|---------|----------|
| 1 | No new **canonical** doc is primarily framed as a migration, checklist, rollout, or status tracker. | PASS | `docs/DocumentationArchitecture.md` carries `Status: Current standard and target direction` and is structured as desired-state taxonomy (umbrella vocabulary, five declarative viewpoints, module boundary policy, contract ownership). It has no checklist/phase/status primary framing. The two migration/evidence docs are confined to `docs/tmp/` and are explicitly labelled non-canonical. |
| 2 | No new **imperative plan** is presented as authoritative desired-state architecture. | PASS | `MoonSpecDocsFirstAlignmentPlan.md` and this review live under `docs/tmp/` and self-label as time-bound execution/evidence material; neither is cited as canonical architecture. The standard (§4) explicitly states imperative working documents are **not** part of the canonical Architecture Description. |
| 3 | The standard introduces no separate ADRs, decision logs, or `decisions/` directories. | PASS | No `docs/adr/`, `docs/ADR/`, `docs/decisions/`, or `docs/Decisions/` directory exists. `docs/DocumentationArchitecture.md` defines exactly five canonical viewpoints and four imperative types — none of which is an ADR/decision-log type — and does not mandate decision records. |
| 4 | The standard does not force every module to be a DDD bounded context, adds no hard-blocking validation, and adds no heavyweight ceremony. | PASS | §5 makes "architectural boundary / ownership surface" the **default**, with Bounded Context as a subtype permitted **only** when all three boundary tests pass ("If any test fails, the directory is an architectural boundary or ownership surface — not a Bounded Context"). The validation added by this issue is **advisory** (terminology check + this review + read-only unit guardrails); nothing newly blocks merge or commit. No new required process ceremony is introduced. |
| 5 | The work does not rewrite all docs, rename every existing doc, mass-backfill metadata, or move all plans in one pass. | PASS | The MM-909 change set adds this review, a guardrail test, and a recorded authority-conflict outcome in the migration plan. No existing canonical doc is renamed, rewritten, or mass-backfilled; no bulk plan migration is performed. |
| 6 | The standard does not treat `specs/` as durable documentation and does not rely on roadmap text alone as implementation evidence. | PASS | `docs/DocumentationArchitecture.md` never names `specs/` as durable/canonical. Constitution XIV/XV and the standard treat `specs/` as gitignored, run-local, disposable. Implementation evidence for this story is the conformance review plus the validation runs and the guardrail test — not roadmap prose. The terminology gate (`tools/check_terminology.sh`) actively bans `specs/<feature>/` durable-doc framing in canonical docs and passes. |
| 7 | Owner/module matrices are preserved as canonical state/evidence surfaces where applicable. | PASS | No owner/module matrix is removed or downgraded by this issue. §5–§6 of the standard preserve module-owned doc sets and the contract-authority/ownership rules as the canonical state surface for module ownership. |
| 8 | New docs do not contradict MoonSpec, Tactics/constitution rules, or docs-first traceability. | PASS | `docs/DocumentationArchitecture.md` §1 explicitly defers to `MoonSpecDocumentModel.md` for the underlying class/precedence/reconciliation rules and states its terms are additive refinements, never substitutes. It is consistent with Constitution XIV (Docs-First) and XV (desired state vs. migration backlog). No contradiction found. |

## Docs validation / lint / link checks

Available repository docs validation was run; results recorded here as DESIGN-REQ-019 evidence.

| Check | Command | Outcome |
|-------|---------|---------|
| Docs terminology gate | `bash tools/check_terminology.sh` | **PASS** — "Docs terminology check passed (141 files scanned)." Includes the `specs/<feature>/` durable-doc-guidance ban in canonical docs. |
| Workflow terminology guardrails | `python tools/verify_workflow_terminology.py --mode all` | **PASS** — "MM-731 workflow terminology check passed (all)." |
| Markdown lint (markdownlint/remark) | n/a | **Not configured** in this repository; no markdownlint/remark/link-check tooling is present. Recorded as unavailable rather than skipped silently (no new heavyweight validation is introduced by this issue — non-goal honored). |
| Link check | n/a | **Not configured**; intra-repo links in this review and the standard were verified by hand to resolve to existing paths. |

## Unresolved documentation-authority conflicts

**None.** The self-conformance review found no unresolved documentation-authority conflict. `docs/DocumentationArchitecture.md` defers to `MoonSpecDocumentModel.md` for the base classes and to the constitution for the desired-state/migration-backlog separation, so it creates no second authority. This outcome is cross-referenced from the migration plan (`docs/tmp/MoonSpecDocsFirstAlignmentPlan.md`, "MM-909 self-conformance and authority-conflict record"). Should a future conflict surface, it must be recorded in that migration plan or in the MM-909 issue comments per the acceptance criteria.

## Cleanup

This review and the migration plan are time-bound working documents. When the MoonSpec Documentation Architecture Standard effort (MM-900 and its child stories) is fully completed and merged, delete or archive both rather than leaving them as stale canonical-looking material (Constitution XV; standard §4).
