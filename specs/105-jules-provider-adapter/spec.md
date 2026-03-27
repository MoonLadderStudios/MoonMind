# Feature Specification: Jules Provider Adapter Runtime Alignment

**Feature Branch**: `105-jules-provider-adapter`  
**Created**: 2026-03-27  
**Status**: Draft  
**Input**: User description: "Implement the updated Jules Provider Adapter docs/ExternalAgents/JulesAdapter.md"

## Source Document Requirements

Extracted from `docs/ExternalAgents/JulesAdapter.md`:

| Requirement ID | Source Citation | Requirement Summary |
|---|---|---|
| DOC-REQ-001 | §3.2, §4.2 | Jules transport must remain a thin provider client that owns schema mapping, auth, retry policy, and scrubbed failures without absorbing workflow orchestration semantics. |
| DOC-REQ-002 | §3.3, §5.1 | Jules must plug into the shared external-agent boundary through the shared adapter contract and translate request, status, result, and cancel semantics through that contract. |
| DOC-REQ-003 | §3.3, §6.3 | When Jules publish mode is `pr` or `branch`, MoonMind must start Jules with `automationMode = AUTO_CREATE_PR`. |
| DOC-REQ-004 | §6.2, §6.3, §6.4 | For `publishMode == "branch"`, MoonMind must treat PR URL extraction, optional base-branch retargeting, and merge completion as part of successful completion semantics. |
| DOC-REQ-005 | §6.4, §7.11 | If PR extraction, base update, merge, or post-provider verification fails, MoonMind must not report branch publication as successful. |
| DOC-REQ-006 | §7.2, §7.4 | Multi-step Jules work must execute as one bundled Jules execution node and one provider session rather than step-by-step session reuse. |
| DOC-REQ-007 | §7.4, §7.6, §7.7 | MoonMind must compile bundled Jules work into one consolidated, checklist-shaped execution brief with mission, workspace context, execution rules, ordered work, validation, and deliverable requirements. |
| DOC-REQ-008 | §7.9, §7.10 | Bundle metadata must preserve the represented logical node IDs, bundle identity, and idempotency/correlation context so auditability survives bundling. |
| DOC-REQ-009 | §7.11, §10.4 | `sendMessage` must remain available for clarification, operator intervention, and explicit resume flows, but not as the normal multi-step workflow progression path. |
| DOC-REQ-010 | §7.11, §12 | If Jules reports success but leaves bundled checklist items incomplete, MoonMind must surface that incomplete state truthfully in result handling rather than silently treating the whole bundle as complete. |
| DOC-REQ-011 | §7.11, §11 | MoonMind-owned verification and publication checks must be able to override provider-reported success when the requested outcome was not actually achieved. |
| DOC-REQ-012 | §3.4, §7.9, §10.1 | Generic workflow orchestration for Jules must live in MoonMind workflow layers, with clarification auto-answer staying in `MoonMind.AgentRun` rather than moving transport logic into provider-specific code. |

## User Scenarios & Testing *(mandatory)*

### User Story 1 - One-Shot Bundled Jules Execution (Priority: P1)

A workflow designer wants MoonMind to hand Jules one coherent repo task instead of a brittle chain of follow-up messages, so that Jules plans and executes the whole change in one stable session.

**Why this priority**: This is the primary architecture change in the updated design and removes the biggest reliability risk in the current Jules execution path.

**Independent Test**: Can be fully tested by executing a plan with multiple consecutive Jules-targeted nodes and verifying MoonMind dispatches one bundled Jules run with one consolidated brief and one result boundary.

**Acceptance Scenarios**:

1. **Given** a plan contains multiple consecutive Jules-targeted implementation nodes in the same repo and publish context, **When** MoonMind prepares execution, **Then** it bundles that work into one Jules execution node and starts exactly one Jules session for the bundle.
2. **Given** a bundled Jules run is created, **When** the provider request is sent, **Then** the request includes one consolidated checklist-shaped brief and bundle metadata that preserves the original represented node IDs for auditability.

---

### User Story 2 - Truthful Branch Publication (Priority: P1)

An operator chooses Jules with `publishMode: "branch"` and expects MoonMind to report success only if the changes actually land on the requested target branch rather than merely creating a PR.

**Why this priority**: Branch publication semantics are operator-visible and billing-relevant; falsely reporting success would violate the documented contract and create hidden delivery failures.

**Independent Test**: Can be fully tested by driving a Jules branch-publication flow through workflow boundary tests that cover successful merge, missing PR URL, base-update failure, and merge rejection outcomes.

**Acceptance Scenarios**:

1. **Given** a Jules run completes with `publishMode: "branch"` and a target branch different from the starting branch, **When** MoonMind finalizes the run, **Then** it retargets the PR base if needed, merges the PR, and reports branch publication as successful only after those steps succeed.
2. **Given** a Jules branch-publication run cannot produce a PR URL or the merge path fails, **When** MoonMind finalizes the run, **Then** it returns a MoonMind-owned non-success outcome instead of claiming the requested branch publication completed.

---

### User Story 3 - Clarification-Only Follow-Up Messaging (Priority: P2)

An operator still needs Jules clarification handling and auto-answer support, but wants follow-up provider messages reserved for exception flows instead of being the normal way MoonMind advances multi-step work.

**Why this priority**: The updated design explicitly keeps clarification support while removing the brittle step-to-step progression model.

**Independent Test**: Can be tested by verifying normal bundled Jules execution never uses the continuation path, while clarification and explicit resume flows still use `sendMessage` safely.

**Acceptance Scenarios**:

1. **Given** MoonMind is executing standard bundled Jules work, **When** later logical plan items in that bundle are reached, **Then** MoonMind does not reuse a previous Jules session through normal step-progression follow-up messages.
2. **Given** Jules explicitly asks for clarification or an operator resumes a blocked run, **When** MoonMind responds, **Then** it may still use the follow-up messaging path without reviving normal multi-step session chaining.

---

### User Story 4 - Truthful Bundle Results and Verification (Priority: P2)

An operator reviewing a completed Jules-backed run wants MoonMind to show whether the entire bundled checklist actually finished, including any incomplete items or MoonMind verification failures that should prevent a success claim.

**Why this priority**: Bundling only works safely if the system preserves truthful completion semantics instead of hiding partial provider completion behind a generic success state.

**Independent Test**: Can be tested by simulating a Jules result that omits required checklist outcomes and verifying MoonMind surfaces incomplete or failed completion details in bundle result handling.

**Acceptance Scenarios**:

1. **Given** Jules returns a provider-success state but MoonMind verification detects incomplete bundled checklist outcomes, **When** the run result is assembled, **Then** MoonMind records the run as incomplete or failed according to policy instead of silently reporting full success.

### Edge Cases

- What happens when a bundled Jules brief exceeds safe provider limits? MoonMind should fail early or deterministically split the work into multiple bundle nodes rather than silently truncating the brief.
- What happens when only some consecutive Jules nodes are safe to bundle together? MoonMind should create multiple deterministic Jules bundle nodes instead of reviving step-by-step continuation.
- What happens when Jules reports success but MoonMind cannot extract a PR URL for `publishMode: "branch"`? The run must not be reported as successful branch publication.
- What happens when a clarification cycle occurs during a bundled run? The clarification path may use follow-up messaging, but normal task progression must still remain bundle-driven rather than step-driven.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001** (DOC-REQ-006, DOC-REQ-007): System MUST bundle eligible consecutive Jules-targeted plan work into one execution node and one provider session rather than executing standard multi-step Jules progression through session reuse.
- **FR-002** (DOC-REQ-007): System MUST compile each bundled Jules execution into one consolidated brief that includes mission, repository/workspace context, execution rules, ordered work checklist, validation checklist, and deliverable requirements.
- **FR-003** (DOC-REQ-008): System MUST persist bundle metadata that identifies the bundle, the represented logical node IDs, the bundle manifest reference, the bundle strategy, and the correlation/idempotency context used for that bundled run.
- **FR-004** (DOC-REQ-009, DOC-REQ-012): System MUST remove normal workflow progression through `jules_session_id` or equivalent step-chaining state while preserving follow-up messaging only for clarification, intervention, or explicit resume flows.
- **FR-005** (DOC-REQ-001, DOC-REQ-002, DOC-REQ-012): System MUST keep Jules transport and provider adapter responsibilities separated from workflow orchestration so transport remains thin and the shared adapter contract remains the Jules integration boundary.
- **FR-006** (DOC-REQ-003): System MUST request Jules PR automation automatically whenever the requested publish mode is `pr` or `branch`.
- **FR-007** (DOC-REQ-004, DOC-REQ-005): System MUST treat `publishMode: "branch"` as successful only when MoonMind can extract the Jules-created PR, optionally retarget its base branch, merge it, and confirm the requested branch outcome.
- **FR-008** (DOC-REQ-005, DOC-REQ-011): System MUST map missing PR URLs, base-update failures, merge failures, or post-provider verification failures to MoonMind-owned non-success outcomes instead of reporting successful branch publication.
- **FR-009** (DOC-REQ-010, DOC-REQ-011): System MUST surface incomplete bundled checklist completion or verification mismatches in final result summaries and metadata rather than treating all provider-reported completions as fully successful.
- **FR-010** (DOC-REQ-012): System MUST keep Jules clarification auto-answer behavior in the generic `MoonMind.AgentRun` workflow path and must not move that workflow choreography into Jules transport code.
- **FR-011** (DOC-REQ-003, DOC-REQ-004): System MUST preserve existing Jules adapter/request translation behavior needed for provider start, status, result, and truthful cancel/best-effort cancel semantics while applying the updated publish and bundling rules.
- **FR-012** (DOC-REQ-006, DOC-REQ-007, DOC-REQ-008, DOC-REQ-009, DOC-REQ-010, DOC-REQ-011): System MUST add or update workflow-boundary regression coverage for bundle dispatch, clarification exception handling, bundle-result truthfulness, and branch publication outcomes.

### Key Entities *(include if feature involves data)*

- **Jules Bundle Node**: The synthetic execution node that represents one or more logical Jules-targeted plan nodes as one provider run.
- **Jules Bundle Manifest**: The durable metadata record that explains which logical nodes were bundled, how the one-shot brief was assembled, and what verification expectations apply.
- **Bundle Result Summary**: The MoonMind-owned completion record that combines provider status, publication outcome, verification outcome, and incomplete-checklist reporting for one bundled Jules run.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Workflow tests covering consecutive Jules plan nodes show one bundled child execution and one provider session instead of step-by-step `sendMessage` progression.
- **SC-002**: Workflow tests for `publishMode: "branch"` pass for successful merge, missing PR URL, base-retarget failure, and merge rejection scenarios with truthful final statuses.
- **SC-003**: Normal bundled Jules execution paths contain no continuation-state handoff equivalent to `jules_session_id`, while clarification-only follow-up messaging coverage still passes.
- **SC-004**: Bundle result metadata and summaries expose incomplete bundled checklist outcomes or verification failures in automated tests rather than reporting unconditional success.
- **SC-005**: Updated Jules adapter/workflow tests pass through the repo’s standard unit test path for the touched runtime and workflow files.

## Assumptions

- Consecutive Jules-targeted plan nodes can be identified deterministically from existing plan-node runtime metadata without changing the external plan artifact contract.
- Existing clarification and auto-answer behavior remains valid as an exception path and should be preserved unless it conflicts with the updated one-shot execution rules.
- Existing Jules transport, schema, and runtime gate pieces are largely reusable; the primary work is in orchestration, bundle metadata, truthful result handling, and regression coverage.
