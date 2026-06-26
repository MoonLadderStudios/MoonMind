# Story Breakdown: MM-927 Moon Spec Doc Architecture Alignment

- Source: MM-927: Moon Spec Doc Architecture Alignment
- Source reference path: null (trusted Jira issue brief, no readable repo source document path)
- Source document class: declarative-text
- Story extraction date: 2026-06-26T23:03:58Z
- Requested story output mode: jira

## Design Summary

MM-927 describes a MoonSpec evolution from Spec-Kit-shaped temporary execution artifacts toward canonical documentation as the first-class object that is generated, indexed, sliced, verified, and reconciled. The selected Jira brief is declarative text rather than a file-backed canonical document, so no canonical claim IDs are fabricated. The breakdown keeps immediate documentation-standard stabilization separate from the larger doc-index, doc-slice, workflow, and reconciliation capabilities so each story can be specified and tested independently.

## Coverage Points

- DESIGN-REQ-001 (requirement): Canonical docs remain higher authority — MoonSpec must treat docs/ canonical documents as desired-state authority while temporary artifacts remain disposable derived views.
- DESIGN-REQ-002 (constraint): Document classes and read order stay explicit — The Document Model, Documentation Architecture Standard, project docs, and optional scratch material must be ordered and classified consistently.
- DESIGN-REQ-003 (requirement): Breakdown rejects unconfirmed imperative input — moonspec-breakdown must classify input, fail fast on imperative checklists without override, and write derived stories under artifacts/story-breakdowns.
- DESIGN-REQ-004 (artifact): Downstream skills preserve source traceability — specify, implement, verify, and doc-reconcile must keep canonical source references, discovery ledgers, source drift reporting, and verified reconciliation behavior.
- DESIGN-REQ-005 (integration): Orchestration reconciles docs before PR completion — moonspec-orchestrate and the Jira preset must run doc reconciliation before PR creation and commit required doc edits with implementation work.
- DESIGN-REQ-006 (state-model): MoonSpec domain becomes doc-native — MoonSpec’s native concepts should include architecture descriptions, viewpoints, views, module doc sets, contracts, claims, slices, implementation packets, and reconciliation results.
- DESIGN-REQ-007 (requirement): Stabilize module architecture naming — The documentation standard must consistently prefer <ModuleName>ModuleArchitecture.md where that convention has been chosen.
- DESIGN-REQ-008 (requirement): Clarify system documents versus feature designs — Durable <SystemName>System.md documents should be distinguished from transitional <FeatureName>Design.md proposal documents.
- DESIGN-REQ-009 (requirement): Templates match canonical metadata headers — Documentation templates must use the standard header fields such as Authority, Owning Surface, Related Docs, and Related Implementation.
- DESIGN-REQ-010 (migration): Stale temporary plans are reconciled or removed — Imperative working docs with conflicting status, such as unresolved conflict records versus no-conflict records, should be reconciled and archived or deleted when served.
- DESIGN-REQ-011 (requirement): Canonical docs expose stable addressable claims — Canonical docs need durable claim identifiers such as DOC-REQ, CONTRACT, INV, and NON-GOAL rather than only run-local DESIGN-REQ IDs.
- DESIGN-REQ-012 (artifact): Doc index emits machine-readable authority graph — A doc-index capability should produce a machine-readable index over stable claims and authority relationships for downstream slicing and verification.

## Story Candidates

### STORY-001 - Stabilize MoonSpec documentation standard conventions

Short name: `doc-standard-stabilization`

As a MoonSpec maintainer, I want the documentation standard and templates to use one coherent naming and metadata model so future doc-native automation starts from unambiguous canonical guidance.

Source reference: MM-927: Moon Spec Doc Architecture Alignment; path null; sections: Immediate cleanup before the next evolution; claimIds: []; coverageIds: DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-009, DESIGN-REQ-010

Independent test: Run a documentation-focused test or review that checks DocumentationArchitecture.md and templates for the chosen module architecture filename, system/design distinction, canonical header fields, and absence or resolved status of stale temporary conflict plans.

Acceptance criteria:
- Section 3.2 and downstream examples consistently use <ModuleName>ModuleArchitecture.md as the preferred module architecture filename.
- The standard distinguishes durable <SystemName>System.md documents from transitional <FeatureName>Design.md proposal documents without weakening existing viewpoint rules.
- Canonical documentation templates include the metadata fields required by the standard: Authority, Owning Surface, Related Docs, and Related Implementation.
- Conflicting temporary planning records about documentation-authority conflicts are reconciled, archived, or removed while preserving desired-state framing in canonical docs.
- No new durable spec.md file or specs/ directory is created as part of this stabilization story.

Requirements:
- Update canonical documentation and templates only where they define target-state documentation conventions.
- Keep imperative migration or cleanup notes out of canonical docs except as stable declarative rules.
- Preserve the Document Model as the lifecycle and precedence authority refined by the Documentation Architecture Standard.

Source design coverage:
- DESIGN-REQ-001: Keeps canonical docs as higher authority while editing only durable target-state guidance.
- DESIGN-REQ-002: Maintains document class and read-order consistency.
- DESIGN-REQ-007: Owns the module architecture filename inconsistency.
- DESIGN-REQ-008: Owns the system document versus feature design clarification.
- DESIGN-REQ-009: Owns template/header alignment.
- DESIGN-REQ-010: Owns stale temporary plan reconciliation.

Dependencies: None

Assumptions:
- Existing documentation files and templates are the implementation surface for this story.

Needs clarification: None

### STORY-002 - Index stable canonical documentation claims

Short name: `stable-doc-claims`

As a MoonSpec workflow author, I want canonical docs to expose stable claim identifiers and a machine-readable index so story slices and verification can reference durable doc claims instead of run-local coverage IDs.

Source reference: MM-927: Moon Spec Doc Architecture Alignment; path null; sections: Phase 1 - Make canonical docs addressable; claimIds: []; coverageIds: DESIGN-REQ-011, DESIGN-REQ-012

Independent test: Run the doc-index capability against representative canonical docs and assert that stable DOC-REQ, CONTRACT, INV, and NON-GOAL claims are discovered with source path, section, authority, and relationship metadata.

Acceptance criteria:
- Canonical documentation supports a lightweight stable claim convention for requirement, contract, invariant, and non-goal identifiers.
- A doc-index capability emits machine-readable JSON for discovered claims without embedding large document bodies into workflow payloads.
- The index includes enough source metadata for downstream breakdown, slicing, verification, and reconciliation to cite the owning document and section.
- Docs without stable claim IDs either fail with an actionable message or are reported as needing claim assignment; the tool does not silently invent canonical IDs for non-file Jira or inline text.

Requirements:
- Define and parse stable claim ID prefixes used by canonical docs.
- Represent claim identity, source path, section, summary, class, and authority relationships in a portable JSON artifact.
- Keep generated index output as a temporary artifact or compact reference, not as the canonical source of truth.

Source design coverage:
- DESIGN-REQ-011: Owns stable canonical claim conventions.
- DESIGN-REQ-012: Owns the doc-index machine-readable authority artifact.

Dependencies: STORY-001

Assumptions:
- Documentation standard stabilization should land first so the index parses the intended canonical conventions.

Needs clarification: None

### STORY-003 - Create doc-native MoonSpec slices and implementation packets

Short name: `doc-native-slices`

As a MoonSpec operator, I want MoonSpec to slice canonical documentation claims into independently testable story candidates and implementation packets so spec.md is no longer the conceptual center of the workflow.

Source reference: MM-927: Moon Spec Doc Architecture Alignment; path null; sections: Recommended MoonSpec evolution model; claimIds: []; coverageIds: DESIGN-REQ-006, DESIGN-REQ-011, DESIGN-REQ-012

Independent test: Given an indexed canonical doc set with stable claims, run the slice generation path and verify that each output story candidate references source claim IDs, carries an implementation packet, preserves coverage, and treats any generated spec.md as temporary adapter output only if still required.

Acceptance criteria:
- MoonSpec models architecture descriptions, viewpoints, views, module doc sets, module contracts, claims, slices, implementation packets, and reconciliation results as native workflow concepts.
- A doc slice maps one or more stable canonical claims to one independently testable story candidate with explicit dependencies and coverage.
- Generated implementation packets preserve source document and claim references for downstream specify, plan, tasks, implementation, and verify stages.
- spec.md, if produced for compatibility, is clearly marked and handled as a temporary derived artifact rather than the authoritative design object.

Requirements:
- Consume doc-index output as the source of durable claim identity.
- Emit doc slices and implementation packets under artifact paths, with compact references suitable for workflow payloads.
- Maintain the coverage gate from canonical claims and run-local coverage points to story candidates.

Source design coverage:
- DESIGN-REQ-006: Owns the doc-native MoonSpec domain model and spec.md inversion.
- DESIGN-REQ-011: Uses stable claims as slice input.
- DESIGN-REQ-012: Uses the machine-readable authority graph as slice input.

Dependencies: STORY-002

Assumptions:
- The first implementation can keep adapter compatibility with existing temporary spec artifacts while shifting authority to doc slices.

Needs clarification: None

### STORY-004 - Align MoonSpec skills around doc-source execution

Short name: `doc-source-workflow`

As a MoonSpec maintainer, I want breakdown, specify, implement, verify, and reconcile to consume doc-native source references consistently so every downstream artifact can be checked against the original canonical document claims.

Source reference: MM-927: Moon Spec Doc Architecture Alignment; path null; sections: Current state, Recommended MoonSpec evolution model; claimIds: []; coverageIds: DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-006

Independent test: Run a controlled MoonSpec workflow from a canonical doc slice through specify/plan/tasks/implement/verify/reconcile and assert that each artifact preserves source document, source class, stable claim IDs, run-local coverage, discovery ledger, and source-drift verdicts.

Acceptance criteria:
- moonspec-breakdown keeps imperative-input fail-fast behavior and emits temporary breakdown artifacts with source document class and coverage ownership.
- moonspec-specify records canonical source document and claim references while treating spec.md as temporary execution material.
- moonspec-implement writes a discovery ledger for doc drift using source references rather than uncited observations.
- moonspec-verify reports Source Document Drift against the preserved original source and claim coverage.
- moonspec-doc-reconcile updates canonical docs only when verified evidence definitely requires it.

Requirements:
- Preserve sourceReference.path and claim IDs for file-backed docs; preserve Jira title/key with null path and empty claim IDs for trusted Jira text.
- Keep large source content out of workflow history by passing artifact references or compact metadata at workflow boundaries.
- Add or update workflow/activity or adapter-boundary tests for changed MoonSpec skill contracts.

Source design coverage:
- DESIGN-REQ-003: Owns breakdown classification, fail-fast behavior, and temporary breakdown output.
- DESIGN-REQ-004: Owns source traceability through specify, implement, verify, and reconcile.
- DESIGN-REQ-006: Applies doc-native source references across the skill chain.

Dependencies: STORY-003

Assumptions:
- Existing MoonSpec skill names remain canonical and are updated directly rather than through compatibility aliases.

Needs clarification: None

### STORY-005 - Enforce doc reconciliation evidence before PR completion

Short name: `reconcile-before-pr`

As a MoonMind operator, I want orchestrated Jira MoonSpec runs to reconcile canonical docs and commit required doc edits before PR creation so completed implementation work closes with docs and code aligned.

Source reference: MM-927: Moon Spec Doc Architecture Alignment; path null; sections: Current state, Recommended MoonSpec evolution model; claimIds: []; coverageIds: DESIGN-REQ-001, DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-006

Independent test: Run or unit-test the Jira MoonSpec orchestration preset with a completed implementation result and verify that doc reconciliation runs before PR creation, writes a structured reconciliation artifact, and includes required doc edits in the same commit boundary.

Acceptance criteria:
- moonspec-orchestrate includes doc reconciliation as a final stage after implementation verification succeeds.
- The Jira orchestrate preset runs reconciliation before PR creation and records a structured reconciliation artifact.
- Required reconciliation doc edits are committed with the implementation changes in the same PR boundary.
- The workflow fails or reports a clear blocked state when reconciliation evidence is missing, ambiguous, or conflicts with higher-authority docs.
- Operator-visible output confirms that /speckit.verify compared final behavior against the original preserved design before reconciliation is trusted.

Requirements:
- Preserve reconciliation as an evidence-backed stage rather than an implementation diary in canonical docs.
- Keep PR publishing and commit behavior idempotent and policy-gated.
- Expose reconciliation artifacts through durable artifact references.

Source design coverage:
- DESIGN-REQ-001: Keeps canonical docs authoritative at PR completion.
- DESIGN-REQ-004: Uses verify and reconcile evidence from the skill chain.
- DESIGN-REQ-005: Owns orchestration and Jira preset reconciliation-before-PR behavior.
- DESIGN-REQ-006: Completes the doc-native loop from docs to reconciliation result.

Dependencies: STORY-004

Assumptions:
- PR creation itself is a later workflow step and is outside this breakdown story execution.

Needs clarification: None

## Coverage Matrix

- DESIGN-REQ-001 -> STORY-001, STORY-005
- DESIGN-REQ-002 -> STORY-001
- DESIGN-REQ-003 -> STORY-004
- DESIGN-REQ-004 -> STORY-004, STORY-005
- DESIGN-REQ-005 -> STORY-005
- DESIGN-REQ-006 -> STORY-003, STORY-004, STORY-005
- DESIGN-REQ-007 -> STORY-001
- DESIGN-REQ-008 -> STORY-001
- DESIGN-REQ-009 -> STORY-001
- DESIGN-REQ-010 -> STORY-001
- DESIGN-REQ-011 -> STORY-002, STORY-003
- DESIGN-REQ-012 -> STORY-002, STORY-003

## Dependencies

- STORY-001: None
- STORY-002: STORY-001
- STORY-003: STORY-002
- STORY-004: STORY-003
- STORY-005: STORY-004

## Out Of Scope

- No spec.md files are created during this breakdown.
- No specs/ directories are created during this breakdown.
- No Jira issues, implementation plans, tasks, code changes, PRs, or Jira transitions are produced by this breakdown step.
- Provider-native Jira or Atlassian connectors are not used; the trusted MoonMind previous-step Jira context is the selected source.

## Coverage Gate

PASS - every major design point is owned by at least one story.

## Downstream Notes

- Recommended first story for `/speckit.specify`: STORY-001 - Stabilize MoonSpec documentation standard conventions.
- TDD remains the default strategy for downstream `/speckit.plan`, `/speckit.tasks`, and `/speckit.implement`.
- After implementation, run `/speckit.verify` to compare final behavior against the original design preserved through specify.
