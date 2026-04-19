# Feature Specification: Skill Runtime Observability and Verification

**Feature Branch**: `[209-skill-runtime-observability]`
**Created**: 2026-04-19
**Status**: Draft
**Input**: User description: "Use the Jira preset brief for MM-408 as the canonical Moon Spec orchestration input.

Additional constraints:


Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira brief: docs/tmp/jira-orchestration-inputs/MM-408-moonspec-orchestration-input.md

# MM-408 MoonSpec Orchestration Input

## Source

- Jira issue: MM-408
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Skill Runtime Observability and Verification
- Labels: `moonmind-workflow-mm-84523417-cb8e-4e09-a152-7267f5d213c6`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-408 from MM project
Summary: Skill Runtime Observability and Verification
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-408 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-408: Skill Runtime Observability and Verification

Source Reference
- Source Document: docs/Tools/SkillSystem.md
- Source Title: MM-319: breakdown docs\Tools\SkillSystem.md
- Source Sections:
  - AgentSkillSystem §18-§23
  - SkillInjection §15, §18-§19
  - docs/tmp/004-AgentSkillSystemPlan.md §2-§5
- Coverage IDs:
  - DESIGN-REQ-010
  - DESIGN-REQ-018
  - DESIGN-REQ-019
  - DESIGN-REQ-020

User Story
As an operator or maintainer, I can inspect which skills were selected, where they came from, how they were materialized, and whether projection succeeded, with boundary tests proving the real adapter and activity behavior rather than isolated helper behavior only.

Acceptance Criteria
- Given a task detail view or API response for a run with skills, then it exposes resolved snapshot ID, selected skill versions, source provenance, materialization mode, visible path summary, and manifest or prompt-index artifact refs where appropriate.
- Given a projection collision occurs, then operator-visible diagnostics include the path, object kind, attempted projection action, and remediation without dumping full skill bodies.
- Given proposal, schedule, or rerun metadata is inspected, then skill intent or resolved snapshot reuse is explicit and re-resolution is never silent.
- Given the skill injection implementation changes, then real adapter or activity boundary tests cover single-skill and multi-skill projections, read-only materialization, activation summary injection, collision failure, replay reuse, and repo-skill input without in-place mutation.

Requirements
- Surface resolved skill metadata in submit, detail, and debug contexts appropriate to operator permissions.
- Record active backing path, visible path, projected skills and versions, read-only state, collision failures, and activation summary evidence.
- Preserve artifact and payload discipline by linking manifests or prompt indexes instead of logging full bodies by default.
- Add or maintain boundary-level tests for adapter and activity behavior when skill injection or resolution behavior changes.

Relevant Implementation Notes
- Keep skill observability focused on metadata and artifact refs rather than full skill body content.
- Include selected skill versions, source provenance, materialization mode, runtime-visible path summary, active backing path, read-only state, and collision diagnostics where the existing submit, detail, debug, proposal, schedule, or rerun surfaces expose skill runtime state.
- Treat projection failures as operator-visible diagnostics with path, object kind, attempted projection action, and remediation guidance.
- Make skill intent or resolved snapshot reuse explicit for proposal, schedule, and rerun flows so re-resolution is not silent.
- Cover real adapter or activity boundaries when skill injection or resolution behavior changes, including single-skill projection, multi-skill projection, read-only materialization, activation summary injection, collision failure, replay reuse, and repo-skill input without in-place mutation.

Verification
- Confirm task detail or API responses for runs with skills expose resolved snapshot ID, selected skill versions, source provenance, materialization mode, visible path summary, and manifest or prompt-index artifact refs where appropriate.
- Confirm projection collision diagnostics include path, object kind, attempted projection action, and remediation without dumping full skill bodies.
- Confirm proposal, schedule, and rerun metadata make skill intent or resolved snapshot reuse explicit and do not silently re-resolve.
- Confirm boundary-level tests cover single-skill and multi-skill projections, read-only materialization, activation summary injection, collision failure, replay reuse, and repo-skill input without in-place mutation.
- Preserve MM-408 in MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.

Dependencies
- Jira link metadata at fetch time indicates MM-408 blocks MM-407."

## Classification

- Input type: single-story feature request.
- Selected mode: runtime.
- Resume decision: no existing `MM-408` Moon Spec feature directory was present, so orchestration starts at `moonspec-specify`.
- Source handling: the historical `docs/Tools/SkillSystem.md` source reference is absent in this checkout; the current canonical source is `docs/Tasks/AgentSkillSystem.md`, with `docs/Tools/SkillInjection.md` and `docs/tmp/004-AgentSkillSystemPlan.md` providing the referenced implementation-plan context.

## User Story - Inspect Skill Runtime Evidence

**Summary**: As an operator or maintainer, I want execution surfaces and verification evidence to show which skills were selected, how they were materialized, and whether projection succeeded, so I can audit skill-enabled runs without reading full skill bodies from logs or workflow history.

**Goal**: Skill-enabled executions expose enough runtime metadata, diagnostics, and lifecycle intent to answer what skill set was selected and what skill view the runtime actually saw, while keeping large skill content behind artifact references and preserving boundary-level test evidence.

**Independent Test**: Can be fully tested by creating or simulating skill-enabled executions, projection failures, rerun or scheduled metadata, and adapter/activity boundary cases, then verifying operator-visible metadata, redacted diagnostics, artifact references, and tests prove the real runtime boundary behavior.

**Acceptance Scenarios**:

1. **Given** a run with resolved skills, **When** an operator inspects the task detail view or corresponding API response, **Then** the response exposes resolved snapshot ID, selected skill versions, source provenance, materialization mode, visible path summary, and manifest or prompt-index artifact refs where appropriate.
2. **Given** skill projection collides with an incompatible path, **When** the failure is surfaced to an operator, **Then** diagnostics include the path, object kind, attempted projection action, and remediation without dumping full skill bodies.
3. **Given** a proposal, schedule, or rerun references skill-enabled execution, **When** metadata is inspected, **Then** skill intent or resolved snapshot reuse is explicit and re-resolution is never silent.
4. **Given** skill injection or resolution behavior changes, **When** the test suite runs, **Then** real adapter or activity boundary tests cover single-skill projection, multi-skill projection, read-only materialization, activation summary injection, collision failure, replay reuse, and repo-skill input without in-place mutation.

### Edge Cases

- A run has no selected skills and should show an explicit empty or default skill state rather than ambiguous missing metadata.
- A manifest or prompt-index artifact ref exists but cannot be previewed by the current operator.
- Projection fails before runtime launch and no active backing path exists yet.
- A rerun uses a previously resolved snapshot while newer skill versions are available.
- Scheduled execution stores skill selectors but resolves the concrete snapshot only when the run starts.
- Debug surfaces may expose lower-level refs, but standard views must avoid full skill body dumps.

## Assumptions

- The historical `docs/Tools/SkillSystem.md` source reference maps to the current canonical agent-skill design in `docs/Tasks/AgentSkillSystem.md`.
- `docs/Tools/SkillInjection.md` is the current repository source for manifest, observability, and boundary-test requirements referenced by the Jira brief's `SkillInjection` sections.
- This story covers runtime observability, lifecycle metadata, and verification evidence; it does not redefine the skill catalog, resolution precedence, or projection mechanics already covered by earlier MM-405 through MM-407 stories.
- MM-408 blocking MM-407 is preserved as Jira relationship context, but this spec covers only MM-408's selected single story.

## Source Design Requirements

- **DESIGN-REQ-010**: Source `docs/Tasks/AgentSkillSystem.md` sections 18 through 18.3. Operator-facing submit, detail, and debug surfaces SHOULD expose selected skill sets, source provenance, materialization mode, resolved snapshot ID, selected versions, canonical path summary, and artifact refs appropriate to the surface. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-004, and FR-005.
- **DESIGN-REQ-018**: Source `docs/Tools/SkillInjection.md` sections 15 and 18. Active projection observability SHOULD record resolved snapshot ref, backing path, visible path, projected skill names and versions, read-only state, collision or projection failures, and the activation summary while preserving manifest or prompt-index artifact discipline. Scope: in scope. Maps to FR-002, FR-003, FR-004, FR-006, and FR-007.
- **DESIGN-REQ-019**: Source `docs/Tasks/AgentSkillSystem.md` sections 19 through 19.4 and `docs/tmp/004-AgentSkillSystemPlan.md` sections 2 through 5. Proposal, schedule, rerun, retry, and replay semantics MUST preserve skill intent or the original resolved snapshot explicitly; reruns and continuations must not silently re-resolve latest skills. Scope: in scope. Maps to FR-008, FR-009, and FR-010.
- **DESIGN-REQ-020**: Source `docs/Tools/SkillInjection.md` section 19. Changes to skill injection SHOULD include real adapter or activity boundary tests covering single-skill projection, multi-skill projection, read-only materialization, activation-summary injection, collision failure, exact-snapshot replay, and repo-skill input without in-place mutation. Scope: in scope. Maps to FR-011 and FR-012.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST expose skill selection intent on submit-time or task-input surfaces when operators provide named skill sets, explicit includes or excludes, materialization mode, or repo/local overlay enablement.
- **FR-002**: System MUST expose resolved skill runtime metadata on task detail or equivalent execution inspection surfaces for runs with skills, including resolved snapshot ID, selected skill versions, source provenance, materialization mode, and canonical runtime-visible path summary.
- **FR-003**: System MUST expose manifest or prompt-index artifact refs for resolved skills where appropriate instead of embedding full skill bodies in standard operator-visible responses.
- **FR-004**: System MUST keep standard operator-visible skill diagnostics scoped to metadata and artifact refs appropriate to the operator's permissions.
- **FR-005**: System MAY expose raw resolved refs, raw manifest refs, source-trace details, or adapter materialization metadata only on advanced or debug surfaces intended for that level of detail.
- **FR-006**: System MUST record active backing path, visible path, projected skill names and versions, read-only state, and activation summary evidence for skill materialization outcomes where that data exists.
- **FR-007**: System MUST surface projection collision failures with path, object kind, attempted projection action, and remediation guidance without dumping full skill bodies.
- **FR-008**: Proposal metadata for skill-enabled work MUST either preserve explicit skill selectors or explicitly state that deployment defaults will be inherited at promotion time.
- **FR-009**: Scheduled execution metadata MUST preserve skill intent clearly and explain how the scheduled run's skill snapshot was selected when the run starts.
- **FR-010**: Rerun, retry, continue-as-new, and replay semantics MUST reuse the original resolved snapshot unless an explicit new-resolution action is taken.
- **FR-011**: Changes to skill injection or resolution behavior MUST include real adapter or activity boundary tests covering single-skill and multi-skill projection, read-only materialization, activation summary injection, collision failure, exact-snapshot replay, and repo-skill input without in-place mutation.
- **FR-012**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata for this work MUST preserve Jira issue key `MM-408` and the original Jira preset brief.

### Key Entities

- **Skill Runtime Evidence**: Operator-visible and debug-safe metadata describing the resolved snapshot, selected skills, versions, provenance, materialization mode, visible path, backing path, read-only state, activation summary, and artifact refs.
- **Projection Diagnostic**: A failure or warning record that identifies a projection path, object kind, attempted action, and remediation without exposing full skill content.
- **Skill Lifecycle Intent**: Metadata carried by proposals, schedules, reruns, retries, and continuations that explains whether execution reuses a resolved snapshot, stores selectors, or inherits deployment defaults.
- **Boundary Verification Evidence**: Unit or integration evidence proving the adapter or activity boundary behavior that selected, materialized, exposed, failed, or replayed skill runtime state.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For at least one skill-enabled run, operator inspection can identify the resolved snapshot ID, selected skill names and versions, source provenance, materialization mode, visible path summary, and manifest or prompt-index artifact refs from a single task detail or equivalent API response.
- **SC-002**: Projection collision diagnostics include all four required fields: path, object kind, attempted action, and remediation, with zero full skill bodies included in the diagnostic output.
- **SC-003**: Proposal, schedule, and rerun metadata each make skill intent or resolved snapshot reuse explicit in verification evidence.
- **SC-004**: Boundary-level tests cover all seven required cases: single-skill projection, multi-skill projection, read-only materialization, activation summary injection, collision failure, exact-snapshot replay, and repo-skill input without in-place mutation.
- **SC-005**: Verification evidence preserves `MM-408` and the original Jira preset brief as the source for the feature.
