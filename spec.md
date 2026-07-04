# Feature Specification: Compare Branches and Explicitly Promote One Result

**Jira Issue:** MM-1103  
**Source Design:** `docs/Workflows/CheckpointBranchSystem.md`  
**Status:** Draft for MoonSpec planning  
**Scope Boundary:** Implement branch comparison artifact generation/API behavior and explicit branch promotion with policy gates and audit artifacts.

## Original Preset Brief

```text
MM-1103: Compare branches and explicitly promote one result

Source Reference
Source Document: docs/Workflows/CheckpointBranchSystem.md
Source Title: Checkpoint Branch System
Source Sections:
- 2.4 Branches are candidates until promoted
- 6.3 Promotion
- 9.4 Publish vs promote
- 13. Branch comparison
- 14. Security and safety
- 16. Artifact requirements
- 20. Desired end state
Canonical Claim IDs:
- docs-workflows-checkpoint-branch-system#s2-4-promotion
- docs-workflows-checkpoint-branch-system#s6-lifecycle
- docs-workflows-checkpoint-branch-system#s9-4-publish-promote
- docs-workflows-checkpoint-branch-system#s13-comparison
- docs-workflows-checkpoint-branch-system#s14-safety
- docs-workflows-checkpoint-branch-system#s16-artifacts
- docs-workflows-checkpoint-branch-system#s20-end
Coverage IDs:
- DESIGN-REQ-005
- DESIGN-REQ-014
- DESIGN-REQ-015
- DESIGN-REQ-017
- DESIGN-REQ-021
As an operator, I can compare candidate branches using durable evidence and explicitly promote one branch result into canonical workflow progress only after validation, gate, side-effect, and approval requirements pass.

Acceptance criteria
- Comparison produces artifacts containing branch ids, base checkpoint ref, diff refs, gate verdict summaries, diagnostics refs, and bounded summary refs.
- Promotion records branch id, turn id, Step Execution id, accepted output refs, git/PR refs, gate verdict refs, side-effect disposition refs, invalidation effects, and approval evidence.
- Promotion requires expected head validation, passed gates, applicable approval, and fresh branch-head validation.
- Promotion does not delete competing branches and publication remains separate from canonical acceptance.
Requirements
- Implement comparison artifact generation and compare API behavior.
- Implement promotion operation with explicit policy gates and audit artifacts.
- Fail closed on approval_required, side_effect_policy_blocked, budget_exhausted, checkpoint invalidity, or expected-head mismatch.
```

## User Story

As an operator, I can compare candidate checkpoint branches using durable evidence and explicitly promote one branch result into canonical workflow progress only after validation, gate, side-effect, and approval requirements pass.

## Functional Requirements

- **FR-001:** The system MUST expose branch comparison behavior for candidate checkpoint branches that produces a durable, artifact-backed comparison record.
- **FR-002:** A comparison record MUST include the compared branch ids, the base checkpoint ref, git diff or range-diff refs where applicable, gate verdict summaries, diagnostics refs, and a bounded summary ref.
- **FR-003:** Comparison behavior MUST keep large diffs, diagnostics, and sensitive details behind artifact refs rather than inlining them in API or UI-facing summaries.
- **FR-004:** The system MUST expose an explicit branch promotion operation that accepts one branch result as canonical workflow progress.
- **FR-005:** Promotion MUST record the promoted branch id, branch turn id, Step Execution id, accepted output refs, git commit/branch/PR refs when applicable, gate verdict refs, side-effect disposition refs, downstream invalidation or revalidation effects, and approval/policy evidence.
- **FR-006:** Promotion MUST require expected-head validation against the caller-provided expected branch head before accepting a branch result.
- **FR-007:** Promotion MUST require passed gates, applicable approval evidence, side-effect policy compliance, checkpoint validity, and fresh branch-head validation immediately before canonical acceptance.
- **FR-008:** Promotion MUST fail closed for `approval_required`, `side_effect_policy_blocked`, `budget_exhausted`, checkpoint invalidity, and expected-head mismatch.
- **FR-009:** Promotion MUST NOT delete competing branches or their evidence.
- **FR-010:** Publication MUST remain separate from promotion; pushing a branch or creating/updating a PR must not by itself mark the branch as canonical workflow progress.
- **FR-011:** Promotion and comparison artifacts MUST avoid raw secrets and must treat artifact refs as identifiers, not storage access grants.
- **FR-012:** Promotion MUST preserve enough audit evidence for later verification of validation, gate, approval, side-effect, output, git/PR, and invalidation decisions.

## Acceptance Scenarios

### Scenario 1: Compare Two Candidate Branches

**Given** two checkpoint branches share a valid base checkpoint  
**When** an operator requests a comparison between them  
**Then** the system creates a durable comparison record with branch ids, base checkpoint ref, diff refs, gate verdict summaries, diagnostics refs, and a bounded summary ref.

### Scenario 2: Promote a Validated Branch

**Given** a candidate branch has a valid checkpoint, a known expected head, passed gates, applicable approval, and compliant side-effect disposition  
**When** an operator promotes that branch  
**Then** the system records canonical acceptance with branch, turn, Step Execution, output, git/PR, gate, side-effect, invalidation, and approval evidence.

### Scenario 3: Reject Stale or Unsafe Promotion

**Given** a candidate branch promotion request has a stale expected head, missing approval, blocked side effects, exhausted budget, or invalid checkpoint  
**When** the promotion request is evaluated  
**Then** the system rejects the promotion and does not advance canonical workflow progress.

### Scenario 4: Preserve Competing Branches and Publication Separation

**Given** one branch is promoted and another branch has been published as a PR  
**When** canonical workflow progress is inspected  
**Then** only the explicitly promoted branch is canonical, competing branch evidence remains durable, and publication state is reported separately from promotion state.

## Traceability

| Requirement | Source Claims | Acceptance Criteria |
| --- | --- | --- |
| FR-001, FR-002, FR-003 | `docs-workflows-checkpoint-branch-system#s13-comparison`, `docs-workflows-checkpoint-branch-system#s16-artifacts` | Comparison artifacts include branch ids, base checkpoint ref, diff refs, gate verdict summaries, diagnostics refs, and bounded summary refs. |
| FR-004, FR-005, FR-012 | `docs-workflows-checkpoint-branch-system#s6-lifecycle`, `docs-workflows-checkpoint-branch-system#s16-artifacts` | Promotion records branch id, turn id, Step Execution id, accepted outputs, git/PR refs, gate refs, side-effect refs, invalidation effects, and approval evidence. |
| FR-006, FR-007, FR-008 | `docs-workflows-checkpoint-branch-system#s2-4-promotion`, `docs-workflows-checkpoint-branch-system#s14-safety` | Promotion requires expected head validation, passed gates, applicable approval, fresh branch-head validation, and fail-closed handling. |
| FR-009, FR-010 | `docs-workflows-checkpoint-branch-system#s9-4-publish-promote`, `docs-workflows-checkpoint-branch-system#s20-end` | Promotion does not delete competing branches, and publication remains separate from canonical acceptance. |
| FR-011 | `docs-workflows-checkpoint-branch-system#s14-safety` | Comparison and promotion evidence avoids raw secrets and uses artifact refs safely. |

## Out of Scope

- Branch creation, continuation, fork, archive, and publish implementation except where promotion or comparison must read their existing evidence.
- Multi-branch merge behavior.
- Automatic branch exploration policy.
- UI polish beyond API-visible comparison and promotion state required by this story.
