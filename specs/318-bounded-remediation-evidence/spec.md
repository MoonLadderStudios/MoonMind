# Feature Specification: Bounded Remediation Evidence Context

**Feature Branch**: `318-bounded-remediation-evidence`
**Created**: 2026-05-08
**Status**: Draft
**Input**: User description: """
Use the Jira preset brief for MM-618 as the canonical Moon Spec orchestration input.

Additional constraints:


Jira Orchestrate always runs as a runtime implementation workflow.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira preset brief:

# MM-618 MoonSpec Orchestration Input

## Source

- Jira issue: MM-618
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Build bounded remediation evidence context and live follow tools
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, `presetInstructions`, or recommended preset instructions.
- Trusted response artifact: `/work/agent_jobs/mm:a6d63116-cfbf-4474-90db-6af6f461079b/artifacts/moonspec-inputs/MM-618-trusted-jira-get-issue-sanitized.json`

## Canonical MoonSpec Feature Request

Jira issue: MM-618 from MM project
Summary: Build bounded remediation evidence context and live follow tools
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-618 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-618: Build bounded remediation evidence context and live follow tools

Source Reference
Source Document: docs/Tasks/TaskRemediation.md
Source Title: Task Remediation
Source Sections:
- 9. Evidence and context model
- 10.5 Artifact and log access mediation
- 15.4 Evidence presentation
- 15.5 Live follow behavior
- 16.3 Historical target has only merged logs
- 16.4 Missing or partial artifact refs
- 16.5 Live follow unavailable
Coverage IDs:
- DESIGN-REQ-008
- DESIGN-REQ-009
- DESIGN-REQ-010
- DESIGN-REQ-025

As a remediation task, I receive a bounded artifact-first context bundle plus typed evidence and live-follow access so that I can diagnose a target without scraping Mission Control, importing unbounded logs, or receiving raw storage access.

Acceptance Criteria
- reports/remediation_context.json is generated up front and linked to the remediation task.
- The context artifact contains refs and compact summaries, not unbounded logs, presigned URLs, raw storage keys, local paths, or secrets.
- Live follow starts only when target activity, taskRun support, and policy permit it; otherwise logs and artifacts remain available.
- Evidence access is through typed MoonMind-owned surfaces, and every missing evidence class is recorded without deadlock.

Requirements
- Evidence is artifact-first and server-mediated.
- Live follow is optional, cursor-resumable, and not authoritative.
- Remediation can diagnose historical or degraded targets.

## Relevant Linked Issues

- MM-617 (blocks): Create canonical remediation submissions with durable target links [Story, Done]
- MM-619 (is blocked by): Enforce remediation authority, policy profiles, and secret-safe access [Story, Backlog]

## Orchestration Constraints

Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.
"""

## Classification

Input classification: single-story runtime feature request. The Jira brief selects one independently testable remediation behavior from `docs/Tasks/TaskRemediation.md`: provide a bounded evidence context and optional live-follow access for remediation tasks. It does not require story splitting.

Resume decision: no existing Moon Spec feature directory or later-stage artifacts matched `MM-618` under `specs/`, so Specify is the first incomplete stage.

## User Story - Diagnose With Bounded Evidence

**Summary**: As a remediation task, I want a bounded artifact-first context bundle with typed evidence access and optional live-follow state so I can diagnose a target execution without scraping Mission Control, importing unbounded logs, or receiving raw storage access.

**Goal**: Remediation starts with a stable context artifact that identifies the target, selected evidence, policy snapshots, compact summaries, and degraded evidence state, while all detailed artifacts and logs remain available only through server-mediated references or typed access surfaces.

**Independent Test**: Create a remediation run for a target execution that has step evidence, artifact refs, and managed-run observability, then verify the remediation context artifact is linked before the remediation task begins, contains only bounded refs and summaries, exposes typed evidence access including optional live-follow state when allowed, and records missing evidence without blocking diagnosis.

**Acceptance Scenarios**:

1. **Given** a valid remediation task is created for a target execution, **When** the remediation task starts, **Then** it has a linked `reports/remediation_context.json` context artifact containing target identity, selected steps, compact summaries, evidence refs, policy snapshots, and current evidence availability.
2. **Given** target logs, diagnostics, provider snapshots, and continuity artifacts are available, **When** the context artifact is generated, **Then** it stores refs and bounded summaries instead of unbounded log bodies, presigned URLs, storage backend keys, absolute local paths, or secret-bearing config.
3. **Given** the target is active, its task run supports live follow, and policy permits live observation, **When** remediation evidence is prepared, **Then** the context includes live-follow support and a resumable cursor while durable logs and artifacts remain available as the authoritative fallback.
4. **Given** live follow is unavailable, disconnected, unsupported, or denied by policy, **When** the remediation task reads evidence, **Then** it can continue through durable logs, diagnostics, summaries, and artifact refs, with the unavailable evidence class recorded.
5. **Given** the target is historical or evidence is partial, **When** the remediation task diagnoses the target, **Then** degraded evidence is represented explicitly and does not deadlock remediation.
6. **Given** an operator inspects remediation evidence in Mission Control, **When** the detail view is rendered, **Then** the operator can reach the context artifact, referenced target logs and diagnostics, decision log, action result evidence, verification evidence, and live observation state when present.

### Edge Cases

- Historical targets with only merged logs are still diagnosable and are marked as degraded evidence.
- Missing stdout, stderr, diagnostics, continuity, or provider snapshot refs are recorded by evidence class while available evidence remains usable.
- Live follow can disconnect, restart, or cross a managed-session epoch boundary; the user-visible state preserves the last durable sequence position when available.
- Unauthorized artifact or log access does not leak raw storage details or target existence beyond the requester's permitted scope.
- A remediation task that later considers a side-effecting action must refresh the target health view instead of relying only on the initial context snapshot.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST generate and link a remediation context artifact before the remediation task starts diagnostic work.
- **FR-002**: The remediation context artifact MUST identify the remediation run, target execution, target run, selected steps, evidence refs, compact summaries, live-follow capability state, action policy snapshot, approval policy snapshot, and lock policy snapshot when those values are available.
- **FR-003**: The remediation context artifact MUST remain bounded by storing refs, compact summaries, compact diagnosis hints, and availability metadata instead of unbounded full logs or raw diagnostic bodies.
- **FR-004**: The remediation context artifact MUST NOT contain presigned storage URLs, storage backend keys, absolute local filesystem paths, raw secret-bearing config bundles, or secret values.
- **FR-005**: Remediation evidence access MUST be artifact-first and server-mediated through typed MoonMind-owned surfaces rather than Mission Control page scraping or raw storage access.
- **FR-006**: Remediation tasks MUST be able to read referenced target artifacts and bounded target logs through policy-enforced access surfaces.
- **FR-007**: Live follow MUST start only when the target run is active, the target task run supports live follow, and policy permits live observation.
- **FR-008**: Live follow MUST be optional, best effort, cursor-resumable when possible, and never the only available evidence path.
- **FR-009**: When live follow is unavailable, System MUST fall back to durable merged logs, stdout logs, stderr logs, diagnostics, summaries, or artifact refs as available.
- **FR-010**: System MUST record unavailable, missing, partial, or denied evidence classes in the remediation context or evidence read result without blocking diagnosis when enough other evidence remains available.
- **FR-011**: Historical target diagnosis MUST support merged logs and partial artifacts, and MUST expose an explicit degraded-evidence indicator when evidence is incomplete.
- **FR-012**: Mission Control MUST present direct operator access to the remediation context artifact, referenced target logs and diagnostics, remediation decision log, action request or result artifacts, verification artifacts, and live observation state when available.
- **FR-013**: Before a remediation task performs a side-effecting action, System MUST require a fresh bounded target health view so actions are not based only on stale initial evidence.
- **FR-014**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key `MM-618` and this canonical Jira preset brief.

### Key Entities

- **Remediation Context Artifact**: The stable evidence entrypoint linked to a remediation task; contains target identity, selected steps, evidence refs, bounded summaries, policy snapshots, live-follow state, and evidence availability.
- **Evidence Ref**: A server-mediated reference to target logs, diagnostics, summaries, provider snapshots, continuity artifacts, decision logs, action results, or verification evidence.
- **Live Follow State**: Best-effort observation metadata for an active target task run, including support state, policy allowance, task-run identity, reconnect state, epoch boundary information when relevant, and resume cursor when available.
- **Evidence Availability Record**: A compact record of evidence classes that are available, missing, partial, denied, degraded, or replaced by fallback evidence.

## Source Design Requirements

- **DESIGN-REQ-008** (`docs/Tasks/TaskRemediation.md` section 9, Evidence and context model): Remediation must receive a MoonMind-owned context bundle containing target identity, selected steps, observability refs, bounded summaries, live-follow state when applicable, and policy snapshots. Scope: in scope. Mapped to FR-001, FR-002, FR-003, FR-010.
- **DESIGN-REQ-009** (`docs/Tasks/TaskRemediation.md` section 10.5, Artifact and log access mediation): Remediation evidence and logs must remain server-mediated and must not expose raw storage URLs, backend keys, local paths, or secret-bearing config. Scope: in scope. Mapped to FR-004, FR-005, FR-006.
- **DESIGN-REQ-010** (`docs/Tasks/TaskRemediation.md` sections 9.6 and 15.5, Live follow semantics and behavior): Live follow must be optional, policy-gated, cursor-resumable when possible, clearly observable, and backed by durable evidence fallbacks. Scope: in scope. Mapped to FR-007, FR-008, FR-009, FR-012.
- **DESIGN-REQ-025** (`docs/Tasks/TaskRemediation.md` sections 15.4, 16.3, 16.4, and 16.5, Evidence presentation and degraded evidence): Operators and remediation tasks must be able to use context artifacts, logs, diagnostics, decision evidence, partial artifacts, and merged-log fallbacks while recording degraded or missing evidence. Scope: in scope. Mapped to FR-010, FR-011, FR-012, FR-013.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of accepted remediation tasks receive a linked remediation context artifact before diagnostic work starts.
- **SC-002**: Context artifacts generated for targets with logs contain refs and bounded summaries, and contain zero unbounded log bodies, presigned URLs, storage backend keys, absolute local paths, or secret values.
- **SC-003**: Live-follow setup reports one of the explicit states `active`, `unavailable`, `unsupported`, or `policy_denied`, and every non-active state includes durable fallback evidence when available.
- **SC-004**: Historical or partial-evidence targets produce an explicit degraded-evidence indicator and list each missing evidence class without blocking diagnosis.
- **SC-005**: Operator-facing evidence presentation links the context artifact and every available referenced evidence class for the remediation task.
- **SC-006**: Traceability evidence preserves `MM-618`, the canonical Jira preset brief, and DESIGN-REQ-008, DESIGN-REQ-009, DESIGN-REQ-010, and DESIGN-REQ-025 in downstream MoonSpec artifacts.
