# Feature Specification: Initial Managed-Session Retrieval Context

**Feature Branch**: `253-initial-managed-session-retrieval-context`
**Created**: 2026-04-24
**Status**: Draft
**Input**: User description: "Use the Jira preset brief for MM-505 as the canonical Moon Spec orchestration input.

Additional constraints:


Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts."

Preserved source Jira preset brief: `MM-505` from the trusted `jira.get_issue` response, reproduced verbatim in `## Original Preset Brief` below for downstream verification.

Original brief reference: trusted `jira.get_issue` MCP response for `MM-505`.
Classification: single-story runtime feature request.
Resume decision: no existing Moon Spec feature directory or later-stage artifacts matched `MM-505` under `specs/`, so `Specify` is the first incomplete stage.

## Original Preset Brief

```text
# MM-505 MoonSpec Orchestration Input

Jira issue: MM-505 from MM board
Summary: Resolve and publish initial managed-session retrieval context
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-505 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-505: Resolve and publish initial managed-session retrieval context

Source Document
docs/Rag/WorkflowRag.md

Source Title
Workflow RAG – Managed Session Retrieval

Source Sections
- 1. Summary
- 3. Architectural position in MoonMind
- 4.1 Primary model: MoonMind-owned initial context resolution
- 5.1 Retrieval path
- 5.2 Managed runtime integration today
- 6.1 Context belongs behind refs and artifacts
- 9.1 Initial retrieval flow
- 10. ContextPack contract
- 17. Recommended desired-state statement

Coverage IDs
- DESIGN-REQ-001
- DESIGN-REQ-002
- DESIGN-REQ-005
- DESIGN-REQ-006
- DESIGN-REQ-008
- DESIGN-REQ-011
- DESIGN-REQ-017
- DESIGN-REQ-025

User Story
As MoonMind orchestration, I can resolve retrieval settings before a managed step, run embedding-driven search, build a ContextPack, and publish the result as durable artifacts/refs so the managed session starts with the right context.

Acceptance Criteria
- Initial managed-step execution resolves retrieval settings and scope before runtime task execution begins.
- The retrieval path performs embedding plus vector search and packages results into a ContextPack without requiring a separate general chat/completions retrieval hop.
- Retrieved context is persisted or published behind artifacts/refs, and large bodies are not copied into durable workflow payloads.
- The managed runtime receives the retrieved context through the adapter input surface together with the existing safety framing for untrusted retrieved text.
- Current Codex-style workspace preparation remains representable by this contract rather than as a bespoke one-off implementation.

Requirements
- MoonMind owns initial context assembly for managed sessions.
- Context publication uses durable refs and artifacts as the source of truth.
- ContextPack includes the minimum shared retrieval shape needed by runtime adapters.

Implementation Notes
- Prefer MoonMind-owned retrieval assembly before managed runtime execution starts.
- Keep large retrieved bodies behind durable refs/artifacts instead of embedding them in workflow payloads.
- Deliver the resulting context through the adapter input surface together with the existing untrusted-retrieved-text safety framing.
- Preserve compatibility with current Codex-style workspace preparation by expressing it through the same contract instead of a one-off path.

Needs Clarification
- None from the trusted Jira response beyond the brief above.
```

## Classification

- Input type: Single-story feature request.
- Breakdown decision: `moonspec-breakdown` was not run because the Jira preset brief already defines one independently testable runtime story.
- Selected mode: Runtime.
- Source design: `docs/Rag/WorkflowRag.md` is treated as runtime source requirements because the brief describes system behavior, not documentation-only work.
- Source design path input: `.`
- Resume decision: No existing Moon Spec artifacts for `MM-505` were found under `specs/`; specification is the first incomplete stage.

## User Story - Publish Initial Retrieval Context For Managed Sessions

**Summary**: As a workflow operator, I want MoonMind to resolve and publish managed-session retrieval context before runtime execution starts so the managed session begins with the right durable context instead of reconstructing it ad hoc.

**Goal**: MoonMind assembles initial managed-session retrieval context through its owned retrieval path, publishes that context behind durable refs and artifacts, and delivers it to the managed runtime through the adapter input surface with the existing untrusted-retrieved-text safety framing.

**Independent Test**: Start a managed-session step with retrieval enabled and verify MoonMind resolves retrieval settings before runtime execution, performs embedding-backed search and `ContextPack` assembly without an extra chat/completions retrieval hop, persists the retrieved context behind artifacts or refs rather than large workflow payloads, injects the resulting context through the runtime adapter input surface, and preserves the Jira reference `MM-505` across generated artifacts and verification output.

**Acceptance Scenarios**:

1. **Given** a managed-session step begins with retrieval enabled, **When** MoonMind prepares the step for execution, **Then** it resolves retrieval settings and scope before the managed runtime starts doing task work.
2. **Given** retrieval settings have been resolved, **When** MoonMind executes the retrieval path, **Then** it performs embedding-backed search and packages the results into a `ContextPack` without requiring a separate general chat or completions retrieval hop.
3. **Given** a `ContextPack` has been assembled for the step, **When** MoonMind publishes retrieval output, **Then** the authoritative retrieved context is persisted behind artifacts or refs rather than copied as large bodies into durable workflow payloads.
4. **Given** retrieved context has been published for a managed-session step, **When** MoonMind hands control to the runtime adapter, **Then** the adapter receives the retrieved context through its input surface together with the established safety framing that treats retrieved text as untrusted reference data.
5. **Given** the current Codex-style workspace preparation path participates in managed-session startup, **When** initial retrieval context is delivered, **Then** that path remains representable by the same shared retrieval contract instead of relying on a bespoke one-off implementation.
6. **Given** MoonSpec artifacts and downstream implementation evidence are generated for this work, **When** traceability is reviewed, **Then** the preserved Jira issue key `MM-505` remains present in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

### Edge Cases

- Retrieval configuration is unavailable or intentionally skipped for an allowed reason at step start.
- The managed runtime needs initial context but the retrieved body would exceed compact payload boundaries if inlined.
- The runtime adapter receives retrieved text that contains instructions which must remain treated as untrusted reference material.
- Current workspace-preparation behavior and future managed runtimes need the same retrieval contract without diverging startup semantics.
- Retrieval returns no relevant matches but the managed-session step must still start with a deterministic, observable outcome.

## Assumptions

- `MM-505` is limited to initial managed-session retrieval context assembly and publication, not to redesigning the full ingest pipeline or general long-term memory architecture.
- Session-initiated follow-up retrieval remains in scope only insofar as the initial contract must stay compatible with the same MoonMind-owned retrieval surfaces and artifact discipline.
- The existing safety framing for untrusted retrieved text remains the required adapter-facing behavior for initial context delivery.

## Source Design Requirements

| ID | Source | Requirement | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-001 | `docs/Rag/WorkflowRag.md` §1, §4.1 | MoonMind owns initial context assembly for managed sessions and resolves retrieval before the managed step performs task work. | In scope | FR-001, FR-002 |
| DESIGN-REQ-002 | `docs/Rag/WorkflowRag.md` §3, §6.1 | Retrieved context for managed sessions belongs behind durable refs and artifacts rather than large inline workflow payloads. | In scope | FR-003, FR-005 |
| DESIGN-REQ-005 | `docs/Rag/WorkflowRag.md` §1, §5.1 | The retrieval path uses embedding-backed search and deterministic context packaging rather than a separate generative retrieval hop. | In scope | FR-002 |
| DESIGN-REQ-006 | `docs/Rag/WorkflowRag.md` §5.2 | Managed runtime startup consumes retrieved context through the adapter input surface together with existing safety framing for untrusted retrieved text. | In scope | FR-004 |
| DESIGN-REQ-008 | `docs/Rag/WorkflowRag.md` §4.1, §5.2 | Initial retrieval output must be persisted or published so runtime startup can consume durable context prepared by MoonMind. | In scope | FR-003, FR-004 |
| DESIGN-REQ-011 | `docs/Rag/WorkflowRag.md` §3, §6.2 | Durable artifacts, refs, and bounded retrieval metadata remain the authoritative retrieval truth for managed sessions. | In scope | FR-003, FR-005 |
| DESIGN-REQ-017 | `docs/Rag/WorkflowRag.md` §5.2, §10 | Existing Codex-style workspace preparation must remain representable through the shared retrieval contract. | In scope | FR-005 |
| DESIGN-REQ-025 | `docs/Rag/WorkflowRag.md` §1, §4.2, §7.3 | The retrieval contract must preserve MoonMind-owned policy, observability, and adapter neutrality across managed-session runtimes. | In scope | FR-001, FR-004, FR-005 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST resolve retrieval settings and retrieval scope for a managed-session step before the managed runtime begins task execution.
- **FR-002**: The system MUST execute the initial managed-session retrieval path using embedding-backed search and `ContextPack` assembly without requiring a separate general chat or completions retrieval hop solely to fetch context.
- **FR-003**: The system MUST persist or publish initial managed-session retrieval output behind durable artifacts or refs instead of embedding large retrieved bodies directly into durable workflow payloads.
- **FR-004**: The system MUST deliver the initial retrieved context to the managed runtime through the adapter input surface together with safety framing that treats retrieved text as untrusted reference data.
- **FR-005**: The system MUST keep the initial retrieval contract compatible with current Codex-style workspace preparation while remaining reusable for managed-session runtimes that consume MoonMind-owned retrieval context.
- **FR-006**: Moon Spec artifacts, implementation notes, verification output, commit text, and pull request metadata for this work MUST preserve Jira issue key `MM-505`.

### Key Entities

- **Retrieval Settings**: The bounded configuration and scope MoonMind resolves before initial managed-session retrieval begins.
- **ContextPack**: The packaged retrieval result MoonMind builds from embedding-backed search results for managed runtime consumption.
- **Retrieved Context Artifact/Ref**: The durable published representation of the initial retrieval output that keeps large context bodies out of workflow payloads.
- **Adapter Input Surface**: The managed-runtime boundary through which MoonMind delivers initial retrieved context and untrusted-text safety framing.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Validation proves managed-session steps with retrieval enabled resolve retrieval settings and scope before runtime task execution starts.
- **SC-002**: Validation proves the initial retrieval path performs embedding-backed search and `ContextPack` assembly without a separate general chat or completions retrieval hop.
- **SC-003**: Validation proves the authoritative initial retrieval output is published behind artifacts or refs and that large retrieved bodies are absent from durable workflow payloads.
- **SC-004**: Validation proves runtime adapters receive the initial retrieved context together with untrusted-retrieved-text safety framing.
- **SC-005**: Validation proves the same retrieval contract covers current Codex-style workspace preparation without introducing a bespoke startup-only path.
- **SC-006**: Traceability review confirms `MM-505` and DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-008, DESIGN-REQ-011, DESIGN-REQ-017, and DESIGN-REQ-025 remain preserved in MoonSpec artifacts and downstream verification evidence.
