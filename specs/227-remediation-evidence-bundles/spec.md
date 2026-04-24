# Feature Specification: Remediation Evidence Bundles

**Feature Branch**: `227-remediation-evidence-bundles`
**Created**: 2026-04-22
**Status**: Draft
**Input**: User description: "Use the Jira preset brief for MM-452 as the canonical Moon Spec orchestration input.

Additional constraints:

Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts."

**Canonical Jira Brief**: `spec.md` (Input)

## Original Preset Brief

```text
# MM-452 MoonSpec Orchestration Input

## Source

- Jira issue: MM-452
- Board scope: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Build bounded artifact-first remediation evidence bundles and tools
- Trusted fetch tool: `jira.get_issue`
- Canonical source: Synthesized from the trusted `jira.get_issue` MCP response because the response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-452 from MM board
Summary: Build bounded artifact-first remediation evidence bundles and tools
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-452 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-452: Build bounded artifact-first remediation evidence bundles and tools

User Story
As a remediation runtime, I receive a bounded MoonMind-owned evidence bundle and typed evidence tools so I can diagnose a target execution without scraping UI pages or embedding unbounded logs in workflow history.

Source Document
docs/Tasks/TaskRemediation.md

Source Title
Task Remediation

Source Sections
- 9. Evidence and context model
- 5.3 Control remains separate from observation
- 6. Core invariants

Coverage IDs
- DESIGN-REQ-006
- DESIGN-REQ-007
- DESIGN-REQ-008
- DESIGN-REQ-009
- DESIGN-REQ-022
- DESIGN-REQ-023

Acceptance Criteria
- A remediation run receives a reports/remediation_context.json artifact containing the specified v1 schema fields and artifact_type remediation.context.
- Full logs and diagnostics remain behind refs or typed read APIs; durable context contains only bounded summaries/excerpts.
- Evidence tools can read referenced artifacts/logs through normal artifact and task-run policy checks.
- Live follow is available only when target state, taskRunId support, and policy allow it; cursor state survives retries where possible.
- When live follow is unavailable, the remediator can still diagnose from merged/stdout/stderr logs, diagnostics, summaries, and artifacts with evidence degradation recorded.
- Before any side-effecting action request is submitted, the runtime re-reads current target health and target-change guard inputs.

Requirements
- The context builder is the stable entrypoint for target evidence.
- Live logs are observation only and never the source of truth or control channel.
- Missing evidence degrades the task rather than causing unbounded waits.

Implementation Notes
- Preserve MM-452 in downstream MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Scope the implementation to bounded artifact-first remediation evidence bundles and typed evidence tool access.
- Use existing task remediation, artifact, live log, diagnostics, task-run policy, and guard-input surfaces where possible.
- Do not scrape UI pages, embed unbounded logs in workflow history, or treat live logs as a source of truth or control channel.

Needs Clarification
- None
```

## User Story - Diagnose From Bounded Remediation Evidence

**Summary**: As a remediation runtime, I want a bounded MoonMind-owned evidence bundle and typed evidence tools so that I can diagnose a target execution without scraping UI pages or embedding unbounded logs in workflow history.

**Goal**: A remediation run starts diagnosis from a durable `remediation.context` artifact and can read only referenced artifacts and logs through typed, policy-checked evidence tools, with live follow used only as an optional observation surface.

**Independent Test**: Create or simulate a target execution and linked remediation execution, generate the remediation context, then verify the runtime can read the context, declared artifacts, bounded logs, and optional live-follow data while undeclared evidence, unsupported live follow, raw storage access, and stale side-effect assumptions are rejected or degraded.

**Acceptance Scenarios**:

1. **Given** a remediation run with a valid target link, **When** evidence preparation runs, **Then** it receives a `reports/remediation_context.json` artifact with `artifact_type` `remediation.context` before diagnosis begins.
2. **Given** the context references target artifacts or task-run logs, **When** the remediation runtime reads evidence, **Then** typed evidence tools allow only context-declared refs through normal artifact and task-run policy checks.
3. **Given** target logs or diagnostics are large, **When** durable context is created, **Then** full bodies remain behind refs or typed read APIs and the context stores only bounded summaries, excerpts, selectors, and policies.
4. **Given** the target supports live follow and policy allows it, **When** the runtime follows logs, **Then** the follow operation returns resumable cursor state without treating the live stream as the source of truth.
5. **Given** live follow is unavailable, unsupported, or policy-blocked, **When** diagnosis begins, **Then** the runtime can still use merged/stdout/stderr logs, diagnostics, summaries, and artifacts with explicit evidence degradation recorded.
6. **Given** the runtime is about to request a side-effecting action, **When** it prepares the action request, **Then** it re-reads the current bounded target health and target-change guard inputs instead of acting only on the pinned context snapshot.

### Edge Cases

- Missing optional artifact refs or diagnostics are recorded as bounded degradation rather than causing unbounded waits.
- Requests for undeclared artifacts or taskRunIds are rejected even if the caller knows a real identifier.
- Live follow requests fail fast when the target state, taskRunId support, cursor, or policy does not allow follow.
- Durable context and tool responses never expose presigned URLs, raw storage keys, absolute local filesystem paths, raw credentials, or unbounded log bodies.
- The log timeline is never accepted as a control channel for intervention.

## Assumptions

- The MM-452 story intentionally spans the already separated context-builder and evidence-tool runtime surfaces because the Jira brief asks for artifact-first bundles and typed evidence access as one operator-visible remediation capability.
- Side-effecting administrative action execution remains outside this story, except for verifying the required fresh target-health and target-change guard read before action requests.

## Source Design Requirements

- **DESIGN-REQ-006** (`docs/Tasks/TaskRemediation.md` section 9.2): Remediation diagnosis must start from a MoonMind-owned `reports/remediation_context.json` artifact with type `remediation.context`. Scope: in scope, mapped to FR-001 and FR-002.
- **DESIGN-REQ-007** (`docs/Tasks/TaskRemediation.md` sections 6 and 9.4): Large logs, diagnostics, provider snapshots, and evidence bodies must stay behind refs or observability APIs rather than entering workflow history or durable context unbounded. Scope: in scope, mapped to FR-003 and FR-004.
- **DESIGN-REQ-008** (`docs/Tasks/TaskRemediation.md` section 9.5): Remediation runtimes must use typed MoonMind-owned evidence tools instead of scraping Mission Control pages or receiving raw storage access. Scope: in scope, mapped to FR-005, FR-006, and FR-007.
- **DESIGN-REQ-009** (`docs/Tasks/TaskRemediation.md` sections 5.3, 9.6, and 9.7): Live logs are passive observation only; live follow is optional and resumable when allowed, and side-effecting action requests must re-read current target health and target-change guards before acting. Scope: in scope, mapped to FR-008, FR-009, and FR-010.
- **DESIGN-REQ-022** (`docs/Tasks/TaskRemediation.md` sections 6 and 9.5): Evidence access must remain server-mediated through artifact/task-run policy checks and must not expose presigned URLs, raw storage keys, raw local paths, or secrets. Scope: in scope, mapped to FR-006 and FR-011.
- **DESIGN-REQ-023** (`docs/Tasks/TaskRemediation.md` section 6): Failure to resolve evidence must degrade, escalate, or fail with a bounded reason rather than waiting indefinitely. Scope: in scope, mapped to FR-012.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST provide a remediation context preparation path that creates or resolves a `reports/remediation_context.json` artifact before remediation diagnosis begins.
- **FR-002**: The context artifact MUST identify itself as `artifact_type` `remediation.context` and include the target identity, selected evidence selectors, compact summaries, policy snapshots, and live-follow cursor state when available.
- **FR-003**: The durable context MUST keep full logs, diagnostics, provider snapshots, and evidence bodies behind artifact refs or typed observability refs.
- **FR-004**: The durable context MUST include only bounded summaries, excerpts, selectors, policies, and refs for large evidence.
- **FR-005**: The system MUST expose typed evidence operations for reading the remediation context, referenced target artifacts, bounded target logs, and optional live target logs.
- **FR-006**: Evidence operations MUST enforce the persisted remediation link, context-declared evidence refs, taskRunId selectors, and normal artifact/task-run policy checks before returning data.
- **FR-007**: Evidence operations MUST reject undeclared artifacts, undeclared taskRunIds, raw storage access, raw host shell access, raw SQL access, raw Docker access, and raw credential access.
- **FR-008**: Live follow MUST be available only when the target is follow-capable, the selected taskRunId supports follow, and policy allows follow.
- **FR-009**: Live follow results MUST include resumable cursor state when follow succeeds and MUST degrade to bounded artifact/log evidence when follow is unavailable.
- **FR-010**: Before submitting any side-effecting action request, the runtime MUST re-read current target health and target-change guard inputs.
- **FR-011**: Durable context and evidence responses MUST NOT expose presigned URLs, raw storage keys, absolute local filesystem paths, raw credentials, secret-bearing config bundles, or unbounded log bodies.
- **FR-012**: Missing optional evidence, unavailable diagnostics, historical merged-log-only evidence, and unavailable live follow MUST produce explicit bounded degradation or fail-fast validation rather than unbounded waits.

### Key Entities

- **Remediation Context Artifact**: A bounded artifact-first bundle that gives remediation runtimes stable target identity, selectors, refs, summaries, policies, and live-follow cursor state.
- **Evidence Reference**: A context-declared artifact or task-run reference that can be read only through server-mediated policy checks.
- **Live Follow Cursor**: A compact resume marker that lets live observation continue across retries without storing raw log bodies in durable context.
- **Target Health Guard Snapshot**: A fresh bounded view of target status and change guards read immediately before side-effecting action requests.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Tests prove a remediation run receives a `remediation.context` artifact before diagnosis and that the artifact is linked to the remediation execution.
- **SC-002**: Tests prove context payloads and durable workflow data contain refs and bounded summaries rather than raw logs, storage paths, URLs, secrets, or unbounded diagnostics.
- **SC-003**: Tests prove evidence tools allow declared artifact/log reads and reject undeclared artifact IDs and taskRunIds.
- **SC-004**: Tests prove live follow succeeds only under target, taskRunId, and policy support and returns cursor state when supported.
- **SC-005**: Tests prove unavailable live follow and missing optional evidence produce explicit bounded degradation or fail-fast validation.
- **SC-006**: Tests prove side-effecting action request preparation re-reads current target health or target-change guards before any action is submitted.
- **SC-007**: Traceability verification confirms MM-452 and DESIGN-REQ-006 through DESIGN-REQ-009, DESIGN-REQ-022, and DESIGN-REQ-023 are preserved in MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
