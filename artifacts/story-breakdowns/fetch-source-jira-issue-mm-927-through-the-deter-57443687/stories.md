# Story Breakdown: MM-927 Moon Spec Doc Architecture Alignment

- Source design: MM-927: Moon Spec Doc Architecture Alignment
- Source reference path: null (trusted Jira issue brief, no repo file)
- Source document class: declarative-text
- Story extraction date: 2026-06-26T01:46:08Z
- Requested output mode: jira

## Design Summary

The design pivots MoonSpec from a Spec-Kit-shaped execution path toward a docs-native architecture where canonical docs are indexed, sliced into independently testable work, used to generate temporary implementation packets, verified through stable claim coverage, reconciled by authority scope, and summarized in PR documentation conformance. It also identifies a near-term stabilization pass for the Documentation Architecture Standard so future automation is built on consistent naming, metadata, and working-document rules.

## Coverage Points

- DESIGN-REQ-001 (constraint) - Canonical docs remain source of truth: MoonSpec operates on durable desired-state docs rather than generated specs as authority.
- DESIGN-REQ-002 (artifact) - Temporary artifacts stay disposable: Breakdown, spec, plan, task, and discovery outputs are derived working material, not durable guidance.
- DESIGN-REQ-003 (constraint) - Breakdown rejects imperative inputs by default: MoonSpec classifies inputs and fails fast on plans, checklists, or migration trackers unless explicitly overridden.
- DESIGN-REQ-004 (requirement) - Documentation standard naming is consistent: Module Architecture View guidance consistently prefers <ModuleName>ModuleArchitecture.md.
- DESIGN-REQ-005 (requirement) - System documents and feature designs are distinct: Durable <SystemName>System.md docs and transitional <FeatureName>Design.md docs are clearly distinguished.
- DESIGN-REQ-006 (requirement) - Viewpoint templates match metadata standard: Templates include Authority, Owning Surface, Related Docs, and Related Implementation.
- DESIGN-REQ-007 (migration) - Stale imperative authority notes are reconciled: Conflicting docs/tmp status or migration notes are updated, deleted, or archived.
- DESIGN-REQ-008 (state-model) - MoonSpec uses docs architecture vocabulary: Architecture Description, Viewpoint, View, Module Doc Set, contracts, claims, slices, packets, and reconciliation become native concepts.
- DESIGN-REQ-009 (requirement) - Canonical docs have stable claim IDs: Docs support stable IDs such as DOC-REQ, CONTRACT, INV, NON-GOAL, QUALITY, and TEST.
- DESIGN-REQ-010 (artifact) - MoonSpec doc index maps documents and claims: A machine-readable index captures path, documentClass, viewpoint, authority, owningSurface, and claim metadata.
- DESIGN-REQ-011 (constraint) - Doc index validation starts advisory-only: Duplicate authority, duplicate claim IDs, missing metadata, and ambiguous module ownership warn and exit zero by default.
- DESIGN-REQ-012 (requirement) - Docs-native authoring creates canonical docs: moonspec-doc-author chooses viewpoint template, location, filename, metadata, stable claims, rationale, and validation.
- DESIGN-REQ-013 (requirement) - Docs-native improvement keeps docs declarative: moonspec-doc-improve repairs metadata, authority, rationale, duplicate contracts, imperative leakage, and verification gaps.
- DESIGN-REQ-014 (requirement) - Docs-native review reports by authority ladder: moonspec-doc-review groups findings by system, cross-cutting, module, contract, design, imperative leakage, and verification-hook categories.
- DESIGN-REQ-015 (requirement) - Doc slicing replaces prompt-centered spec generation: moonspec-doc-slice emits temporary story artifacts with stable canonical source references.
- DESIGN-REQ-016 (constraint) - spec.md becomes a compatibility adapter: Doc-backed spec.md declares itself derived from canonical claims rather than serving as authority.
- DESIGN-REQ-017 (artifact) - Implementation packets derive from doc slices: Temporary packets include source path, viewpoint, owning surface, claim IDs, code/test surfaces, TDD checklist, verification commands, and reconciliation expectations.
- DESIGN-REQ-018 (requirement) - Task generation requires verification and reconciliation finals: moonspec-tasks validates final /speckit.verify and moonspec-doc-reconcile tasks when a canonical source exists.
- DESIGN-REQ-019 (observability) - Verification becomes canonical claim-aware: moonspec-verify reports Canonical Claim Coverage with source, evidence, status, and notes.
- DESIGN-REQ-020 (requirement) - Verification separates gaps and drift: Implementation gaps, verification gaps, and doc drift are distinguished; doc drift feeds reconciliation.
- DESIGN-REQ-021 (requirement) - Doc reconciliation is authority-scope aware: moonspec-doc-reconcile uses the precedence ladder to update the owning canonical doc or escalate.
- DESIGN-REQ-022 (artifact) - Reconciliation output supports multi-doc decisions: Structured results include updated, noUpdateRequired, and escalated entries with path, viewpoint, claim IDs, and reason.
- DESIGN-REQ-023 (observability) - PRs report documentation conformance: PR bodies include canonical sources, temporary artifacts, claim coverage, and reconciliation outcomes.
- DESIGN-REQ-024 (constraint) - TDD and final verification remain downstream defaults: Docs-native packet generation preserves test-first execution and final verification against the original design.

## Ordered Story Candidates

### STORY-001: Stabilize Documentation Architecture Standard

- Short name: standard-stabilization
- Source reference: MM-927: Moon Spec Doc Architecture Alignment; path: null; sections: Immediate cleanup before the next evolution, Concrete next Jira issues #1
- Description: As a MoonSpec documentation author, I need the standard and templates to give one unambiguous golden path so later automation does not encode conflicting naming, metadata, or working-document rules.
- Independent test: Run targeted documentation validator and template tests showing corrected naming, metadata headers, and stale working-doc status.
- Dependencies: none
- Acceptance criteria:
  - Section 3.2 and examples consistently prefer <ModuleName>ModuleArchitecture.md.
  - The standard distinguishes durable <SystemName>System.md from transitional <FeatureName>Design.md.
  - Canonical viewpoint templates include the required metadata fields.
  - Stale docs/tmp authority-conflict notes are no longer contradictory or active without purpose.
  - Tests cover naming and template metadata.
- Owned coverage:
  - DESIGN-REQ-001: STORY-001 explicitly owns DESIGN-REQ-001 for Stabilize Documentation Architecture Standard.
  - DESIGN-REQ-004: STORY-001 explicitly owns DESIGN-REQ-004 for Stabilize Documentation Architecture Standard.
  - DESIGN-REQ-005: STORY-001 explicitly owns DESIGN-REQ-005 for Stabilize Documentation Architecture Standard.
  - DESIGN-REQ-006: STORY-001 explicitly owns DESIGN-REQ-006 for Stabilize Documentation Architecture Standard.
  - DESIGN-REQ-007: STORY-001 explicitly owns DESIGN-REQ-007 for Stabilize Documentation Architecture Standard.
- Needs clarification: none

### STORY-002: Add stable canonical claim ID conventions

- Short name: claim-id-conventions
- Source reference: MM-927: Moon Spec Doc Architecture Alignment; path: null; sections: Phase 1 - Make canonical docs addressable, Concrete next Jira issues #3
- Description: As a MoonSpec maintainer, I need canonical docs and templates to define stable claim ID conventions so docs can be indexed, sliced, verified, and reconciled without relying on run-local coverage IDs.
- Independent test: Run documentation validation tests with sample docs containing valid, malformed, and duplicate claim IDs.
- Dependencies: STORY-001
- Acceptance criteria:
  - Guidance defines DOC-REQ, CONTRACT, INV, NON-GOAL, QUALITY, and TEST IDs.
  - Templates show concise stable claim heading examples.
  - Malformed and duplicate IDs produce advisory warnings by default.
  - Guidance distinguishes canonical claim IDs from temporary DESIGN-REQ IDs.
- Owned coverage:
  - DESIGN-REQ-008: STORY-002 explicitly owns DESIGN-REQ-008 for Add stable canonical claim ID conventions.
  - DESIGN-REQ-009: STORY-002 explicitly owns DESIGN-REQ-009 for Add stable canonical claim ID conventions.
  - DESIGN-REQ-011: STORY-002 explicitly owns DESIGN-REQ-011 for Add stable canonical claim ID conventions.
- Needs clarification: none

### STORY-003: Implement advisory MoonSpec doc index

- Short name: doc-index
- Source reference: MM-927: Moon Spec Doc Architecture Alignment; path: null; sections: Phase 1 - Make canonical docs addressable, Concrete next Jira issues #2
- Description: As a MoonSpec operator, I need a machine-readable index of canonical documents and stable claims so automation can find authorities, detect overlaps, and prepare doc-backed slices.
- Independent test: Run unit tests against fixture docs and verify emitted index plus advisory warnings match expected JSON.
- Dependencies: STORY-001, STORY-002
- Acceptance criteria:
  - Index output is valid JSON under an artifact path.
  - Document entries include path, documentClass, viewpoint, authority, owningSurface, related docs, and related implementation.
  - Claim entries include id, heading, type, anchor, and digest.
  - Warnings are structured and advisory-only by default.
  - docs/tmp imperative documents are not treated as canonical docs.
- Owned coverage:
  - DESIGN-REQ-001: STORY-003 explicitly owns DESIGN-REQ-001 for Implement advisory MoonSpec doc index.
  - DESIGN-REQ-008: STORY-003 explicitly owns DESIGN-REQ-008 for Implement advisory MoonSpec doc index.
  - DESIGN-REQ-010: STORY-003 explicitly owns DESIGN-REQ-010 for Implement advisory MoonSpec doc index.
  - DESIGN-REQ-011: STORY-003 explicitly owns DESIGN-REQ-011 for Implement advisory MoonSpec doc index.
- Needs clarification: none

### STORY-004: Add docs-native author, improve, and review skills

- Short name: doc-skills
- Source reference: MM-927: Moon Spec Doc Architecture Alignment; path: null; sections: Phase 2 - Add docs-native authoring and improvement skills
- Description: As a MoonSpec documentation maintainer, I need docs-native authoring, improvement, and review workflows so canonical docs can be created and maintained directly instead of using spec.md as the center.
- Independent test: Use fixture or temporary-workspace tests to verify each workflow creates, improves, or reviews canonical docs without creating spec.md.
- Dependencies: STORY-001, STORY-002, STORY-003
- Acceptance criteria:
  - Doc authoring chooses location, filename, viewpoint template, metadata, stable claims, and rationale.
  - Doc improvement fixes or reports missing metadata, authority, rationale, duplicate contracts, imperative leakage, and unverifiable claims.
  - Broad work is routed to docs/tmp improvement plans.
  - Doc review groups findings by the authority ladder.
  - No docs-native authoring workflow creates spec.md.
- Owned coverage:
  - DESIGN-REQ-001: STORY-004 explicitly owns DESIGN-REQ-001 for Add docs-native author, improve, and review skills.
  - DESIGN-REQ-012: STORY-004 explicitly owns DESIGN-REQ-012 for Add docs-native author, improve, and review skills.
  - DESIGN-REQ-013: STORY-004 explicitly owns DESIGN-REQ-013 for Add docs-native author, improve, and review skills.
  - DESIGN-REQ-014: STORY-004 explicitly owns DESIGN-REQ-014 for Add docs-native author, improve, and review skills.
- Needs clarification: none

### STORY-005: Implement docs-native MoonSpec doc slicing

- Short name: doc-slice
- Source reference: MM-927: Moon Spec Doc Architecture Alignment; path: null; sections: Phase 3 - Replace spec generation with doc slicing, Concrete next Jira issues #4
- Description: As a MoonSpec workflow runner, I need to slice canonical docs and stable claim IDs into independently testable story candidates so implementation work starts from desired-state docs.
- Independent test: Run doc-slice against canonical and imperative fixture docs; verify source references and imperative fail-fast behavior.
- Dependencies: STORY-002, STORY-003
- Acceptance criteria:
  - Each story carries sourceReference.path, sections, and stable claim IDs for file-backed docs.
  - Outputs remain temporary artifacts and do not create spec.md or specs/ directories.
  - Coverage matrix maps every selected claim or extracted point to a story.
  - Imperative docs are rejected without explicit override.
  - Specify handoff can mark spec.md as derived from canonical claims.
- Owned coverage:
  - DESIGN-REQ-002: STORY-005 explicitly owns DESIGN-REQ-002 for Implement docs-native MoonSpec doc slicing.
  - DESIGN-REQ-003: STORY-005 explicitly owns DESIGN-REQ-003 for Implement docs-native MoonSpec doc slicing.
  - DESIGN-REQ-015: STORY-005 explicitly owns DESIGN-REQ-015 for Implement docs-native MoonSpec doc slicing.
  - DESIGN-REQ-016: STORY-005 explicitly owns DESIGN-REQ-016 for Implement docs-native MoonSpec doc slicing.
- Needs clarification: none

### STORY-006: Generate docs-native implementation packets

- Short name: doc-packets
- Source reference: MM-927: Moon Spec Doc Architecture Alignment; path: null; sections: Phase 4 - Generate temporary implementation packets directly from doc slices, Concrete next Jira issues #5
- Description: As a MoonSpec implementer, I need temporary implementation packets generated from doc slices so plan and task artifacts carry canonical claim provenance, TDD expectations, verification commands, and required doc reconciliation work.
- Independent test: Generate a packet from a fixture doc slice and assert provenance, claim mappings, TDD tasks, verify task, and reconciliation task are present.
- Dependencies: STORY-005
- Acceptance criteria:
  - Packet records source document path, viewpoint, owning surface, and stable claim IDs.
  - Every implementation task maps to stable claim IDs or explains why none apply.
  - Test tasks precede production code tasks.
  - Task list includes final /speckit.verify and moonspec-doc-reconcile tasks when source is canonical.
  - spec.md is marked as a temporary adapter if generated downstream.
- Owned coverage:
  - DESIGN-REQ-002: STORY-006 explicitly owns DESIGN-REQ-002 for Generate docs-native implementation packets.
  - DESIGN-REQ-016: STORY-006 explicitly owns DESIGN-REQ-016 for Generate docs-native implementation packets.
  - DESIGN-REQ-017: STORY-006 explicitly owns DESIGN-REQ-017 for Generate docs-native implementation packets.
  - DESIGN-REQ-018: STORY-006 explicitly owns DESIGN-REQ-018 for Generate docs-native implementation packets.
  - DESIGN-REQ-024: STORY-006 explicitly owns DESIGN-REQ-024 for Generate docs-native implementation packets.
- Needs clarification: none

### STORY-007: Expand MoonSpec verify to canonical claim coverage

- Short name: claim-verify
- Source reference: MM-927: Moon Spec Doc Architecture Alignment; path: null; sections: Phase 5 - Expand verification from story complete to docs embodied, Concrete next Jira issues #6
- Description: As a MoonSpec reviewer, I need verification reports to evaluate canonical claim coverage so a PR is judged against desired-state document claims, code evidence, and test evidence.
- Independent test: Run verification fixtures for verified, partially verified, missing evidence, and doc-drifted claims; assert statuses and drift output.
- Dependencies: STORY-006
- Acceptance criteria:
  - Reports include Canonical Claim Coverage.
  - Each claim status includes code, test, artifact evidence, or a clear gap reason.
  - Implementation gaps, verification gaps, and doc drift are classified separately.
  - Doc drift alone does not block FULLY_IMPLEMENTED when behavior is correct.
  - Structured claim-level drift is available for reconciliation.
- Owned coverage:
  - DESIGN-REQ-001: STORY-007 explicitly owns DESIGN-REQ-001 for Expand MoonSpec verify to canonical claim coverage.
  - DESIGN-REQ-019: STORY-007 explicitly owns DESIGN-REQ-019 for Expand MoonSpec verify to canonical claim coverage.
  - DESIGN-REQ-020: STORY-007 explicitly owns DESIGN-REQ-020 for Expand MoonSpec verify to canonical claim coverage.
  - DESIGN-REQ-024: STORY-007 explicitly owns DESIGN-REQ-024 for Expand MoonSpec verify to canonical claim coverage.
- Needs clarification: none

### STORY-008: Expand reconciliation and PR documentation conformance

- Short name: reconcile-report
- Source reference: MM-927: Moon Spec Doc Architecture Alignment; path: null; sections: Phase 6 - Evolve reconciliation from single-source-doc updates to authority-scope reconciliation, Phase 7 - Add a PR-level documentation conformance report, Concrete next Jira issues #7
- Description: As a MoonSpec PR closer, I need reconciliation to update the correct owning canonical documents and report documentation conformance in the PR so implementation and desired-state docs close aligned.
- Independent test: Run reconciliation and PR-body generation fixtures with single-doc updates, multi-doc updates, no-update cases, and unclear ownership escalations.
- Dependencies: STORY-007
- Acceptance criteria:
  - Reconciliation can update the owning canonical doc even when it is not the original source doc.
  - Ambiguous ownership conflicts are escalated rather than guessed.
  - Structured artifact lists updated, noUpdateRequired, and escalated decisions with reasons.
  - PR conformance section lists canonical sources, temporary artifacts, claim coverage, and reconciliation outcomes.
  - Required canonical doc edits are included in the implementation PR.
- Owned coverage:
  - DESIGN-REQ-001: STORY-008 explicitly owns DESIGN-REQ-001 for Expand reconciliation and PR documentation conformance.
  - DESIGN-REQ-021: STORY-008 explicitly owns DESIGN-REQ-021 for Expand reconciliation and PR documentation conformance.
  - DESIGN-REQ-022: STORY-008 explicitly owns DESIGN-REQ-022 for Expand reconciliation and PR documentation conformance.
  - DESIGN-REQ-023: STORY-008 explicitly owns DESIGN-REQ-023 for Expand reconciliation and PR documentation conformance.
- Needs clarification: none

## Coverage Matrix

- DESIGN-REQ-001: STORY-001, STORY-003, STORY-004, STORY-007, STORY-008
- DESIGN-REQ-002: STORY-005, STORY-006
- DESIGN-REQ-003: STORY-005
- DESIGN-REQ-004: STORY-001
- DESIGN-REQ-005: STORY-001
- DESIGN-REQ-006: STORY-001
- DESIGN-REQ-007: STORY-001
- DESIGN-REQ-008: STORY-002, STORY-003
- DESIGN-REQ-009: STORY-002
- DESIGN-REQ-010: STORY-003
- DESIGN-REQ-011: STORY-002, STORY-003
- DESIGN-REQ-012: STORY-004
- DESIGN-REQ-013: STORY-004
- DESIGN-REQ-014: STORY-004
- DESIGN-REQ-015: STORY-005
- DESIGN-REQ-016: STORY-005, STORY-006
- DESIGN-REQ-017: STORY-006
- DESIGN-REQ-018: STORY-006
- DESIGN-REQ-019: STORY-007
- DESIGN-REQ-020: STORY-007
- DESIGN-REQ-021: STORY-008
- DESIGN-REQ-022: STORY-008
- DESIGN-REQ-023: STORY-008
- DESIGN-REQ-024: STORY-006, STORY-007

## Dependencies

- STORY-001: none
- STORY-002: STORY-001
- STORY-003: STORY-001, STORY-002
- STORY-004: STORY-001, STORY-002, STORY-003
- STORY-005: STORY-002, STORY-003
- STORY-006: STORY-005
- STORY-007: STORY-006
- STORY-008: STORY-007

## Out Of Scope

- Creating or modifying spec.md during breakdown.
- Creating directories under specs/ during breakdown.
- Implementing the stories during breakdown.
- Publishing Jira issues, PRs, or external comments from this step.

## Coverage Gate

PASS - every major design point is owned by at least one story.

## Recommended First Story

STORY-001: Stabilize Documentation Architecture Standard
