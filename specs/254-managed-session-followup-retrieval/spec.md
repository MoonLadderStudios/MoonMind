# Feature Specification: Managed-Session Follow-Up Retrieval

**Feature Branch**: `254-managed-session-followup-retrieval`
**Created**: 2026-04-24
**Status**: Draft
**Input**: User description: "Use the Jira preset brief for MM-506 as the canonical Moon Spec orchestration input.

Additional constraints:


Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts."

Preserved source Jira preset brief: `MM-506` from the trusted `jira.get_issue` response, reproduced verbatim in `## Original Preset Brief` below for downstream verification.

Original brief reference: trusted `jira.get_issue` MCP response for `MM-506`.
Classification: single-story runtime feature request.
Resume decision: no existing Moon Spec feature directory or later-stage artifacts matched `MM-506` under `specs/`, so `Specify` is the first incomplete stage.

## Original Preset Brief

```text
# MM-506 MoonSpec Orchestration Input

## Source

- Jira issue: MM-506
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Enable managed sessions to request additional retrieval through MoonMind-owned surfaces
- Labels: `moonmind-workflow-mm-bcca563a-dede-4ba4-b325-811ef98fc640`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: synthesized from trusted Jira issue fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-506 from MM project
Summary: Enable managed sessions to request additional retrieval through MoonMind-owned surfaces
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-506 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-506: Enable managed sessions to request additional retrieval through MoonMind-owned surfaces

Source Reference
Source Document: docs/Rag/WorkflowRag.md
Source Title: Workflow RAG – Managed Session Retrieval
Source Sections:
- 2.1 In scope
- 4.2 Secondary model: session-initiated follow-up retrieval
- 8. Retrieval execution modes
- 9.2 Session-initiated retrieval flow
- 11. Filters, scope, and “how much context” knobs
- 12. Managed-session enablement rules
- 15. Runtime rollout
- 17. Recommended desired-state statement
Coverage IDs:
- DESIGN-REQ-003
- DESIGN-REQ-007
- DESIGN-REQ-015
- DESIGN-REQ-019
- DESIGN-REQ-020
- DESIGN-REQ-023
- DESIGN-REQ-025

User Story
As a managed session runtime, I can request additional authorized retrieval during execution through a MoonMind-owned tool or gateway and receive a ContextPack plus text output for the next turn.

Acceptance Criteria
- Managed sessions can issue follow-up retrieval requests only through a MoonMind-owned tool, adapter surface, or gateway.
- The request contract supports query, filters, top_k, overlay policy, and bounded budget overrides, and returns both ContextPack metadata and text output.
- The runtime receives an explicit capability signal when retrieval is enabled, including how to request more context and what budgets or scope constraints apply.
- When retrieval is disabled, follow-up retrieval requests fail fast with a clear reason instead of silently degrading to an undefined behavior.
- The same follow-up retrieval model remains valid for Codex now and future managed runtimes later.

Requirements
- Session-initiated retrieval is secondary to but consistent with initial MoonMind-owned context assembly.
- The retrieval surface remains runtime neutral and policy bounded.
- Enablement and capability signalling are explicit runtime inputs.
```

## Classification

- Input type: Single-story feature request.
- Breakdown decision: `moonspec-breakdown` was not run because the Jira preset brief already defines one independently testable runtime story.
- Selected mode: Runtime.
- Source design: `docs/Rag/WorkflowRag.md` is treated as runtime source requirements because the brief describes system behavior, not documentation-only work.
- Source design path input: `.`
- Resume decision: No existing Moon Spec artifacts for `MM-506` were found under `specs/`; specification is the first incomplete stage.
- Multi-spec ordering: Not applicable for `MM-506` because the trusted Jira preset brief defines one independently testable story; if a future upstream breakdown produces multiple isolated specs, they must be processed in dependency order.

## User Story - Allow Managed Sessions To Request Follow-Up Retrieval

**Summary**: As a managed session runtime, I want to request additional retrieval through MoonMind-owned surfaces during execution so that later turns can receive authorized context without bypassing MoonMind policy and runtime boundaries.

**Goal**: Managed runtimes can invoke a MoonMind-owned follow-up retrieval surface that returns bounded retrieval outputs and explicit enablement guidance while preserving policy, adapter neutrality, and durable retrieval semantics.

**Independent Test**: Start a managed-session run with follow-up retrieval enabled and verify the runtime receives explicit capability guidance, issues a retrieval request through the MoonMind-owned retrieval surface, gets `ContextPack` metadata plus text output within the allowed policy bounds, and fails fast with a clear reason when the feature is disabled or the request exceeds the permitted retrieval contract. Confirm generated artifacts and verification output preserve the Jira reference `MM-506`.

**Acceptance Scenarios**:

1. **Given** a managed-session runtime starts with follow-up retrieval enabled, **When** MoonMind prepares runtime capabilities, **Then** the runtime receives explicit instructions describing how to request more context and which retrieval budgets or scope limits apply.
2. **Given** a managed-session runtime needs additional context mid-execution, **When** it issues a follow-up retrieval request, **Then** the request is handled only through a MoonMind-owned tool, adapter surface, or gateway rather than a direct provider-specific bypass.
3. **Given** a valid follow-up retrieval request is submitted, **When** MoonMind performs the retrieval, **Then** it returns both bounded `ContextPack` metadata and text output for the next turn using the shared retrieval contract.
4. **Given** a follow-up retrieval request includes query, filters, `top_k`, overlay policy, and bounded budget overrides, **When** MoonMind validates the request, **Then** it accepts only the supported contract fields and applies retrieval behavior within the allowed bounds.
5. **Given** follow-up retrieval is disabled for a managed-session runtime, **When** the runtime attempts to request more context, **Then** MoonMind rejects the request immediately with a clear, deterministic reason instead of silently degrading or falling back to undefined behavior.
6. **Given** Codex uses the follow-up retrieval contract today and another managed runtime adopts it later, **When** the contract is reviewed across runtimes, **Then** the runtime-visible behavior remains consistent and MoonMind-owned rather than diverging into runtime-specific retrieval semantics.
7. **Given** MoonSpec artifacts and downstream implementation evidence are generated for this work, **When** traceability is reviewed, **Then** the preserved Jira issue key `MM-506` remains present in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

### Edge Cases

- Follow-up retrieval is disabled after runtime startup metadata has already been materialized for a session.
- A runtime requests unsupported retrieval parameters or bounded budget overrides outside the allowed range.
- Retrieval returns no relevant matches but the managed session must continue with a deterministic response shape.
- Retrieved text contains instructions that must remain treated as untrusted reference material by the runtime.
- Multiple managed runtimes need the same follow-up retrieval semantics without exposing provider-specific gateway behavior as the source of truth.

## Assumptions

- `MM-506` is limited to session-initiated follow-up retrieval during managed runtime execution and does not redefine initial retrieval assembly beyond requiring consistency with it.
- The canonical request contract already has or will define bounded inputs such as query, filters, `top_k`, overlay policy, and budget overrides without changing the user-visible story captured here.
- MoonMind remains the sole authority for enabling, denying, and shaping follow-up retrieval behavior for managed runtimes.

## Source Design Requirements

| ID | Source | Requirement | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-003 | `docs/Rag/WorkflowRag.md` §2.1, §9.2 | Managed-session follow-up retrieval is an in-scope runtime behavior that must support additional retrieval during execution. | In scope | FR-001, FR-002 |
| DESIGN-REQ-007 | `docs/Rag/WorkflowRag.md` §4.2, §9.2 | Follow-up retrieval requests must go through a MoonMind-owned tool, adapter surface, or gateway rather than unmanaged runtime bypasses. | In scope | FR-002 |
| DESIGN-REQ-015 | `docs/Rag/WorkflowRag.md` §8, §11 | The follow-up retrieval contract supports bounded retrieval inputs including query, filters, result limits, overlay policy, and constrained budget overrides. | In scope | FR-003, FR-004 |
| DESIGN-REQ-019 | `docs/Rag/WorkflowRag.md` §11, §12 | Managed runtimes must receive explicit enablement and policy guidance describing when follow-up retrieval is available and what limits apply. | In scope | FR-001, FR-004 |
| DESIGN-REQ-020 | `docs/Rag/WorkflowRag.md` §12 | When follow-up retrieval is disabled, MoonMind must fail fast with an explicit denial instead of silently degrading behavior. | In scope | FR-005 |
| DESIGN-REQ-023 | `docs/Rag/WorkflowRag.md` §15 | The same follow-up retrieval model must remain valid across Codex and future managed runtimes rather than becoming runtime-specific behavior. | In scope | FR-006 |
| DESIGN-REQ-025 | `docs/Rag/WorkflowRag.md` §4.2, §17 | Follow-up retrieval remains MoonMind-owned, policy bounded, and observable as part of the desired managed-runtime contract. | In scope | FR-001, FR-002, FR-004, FR-006 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST provide managed runtimes with an explicit capability signal that states whether follow-up retrieval is enabled and describes how additional context can be requested within the allowed policy bounds.
- **FR-002**: The system MUST accept managed-session follow-up retrieval requests only through a MoonMind-owned retrieval tool, adapter surface, or gateway.
- **FR-003**: The system MUST support a bounded follow-up retrieval request contract that includes query text, filters, `top_k`, overlay policy, and allowed budget override inputs.
- **FR-004**: The system MUST return follow-up retrieval results in a consistent response shape that includes both `ContextPack` metadata and text output for the next runtime turn.
- **FR-005**: The system MUST fail fast with a clear, deterministic reason when follow-up retrieval is disabled or when the runtime submits a request outside the supported bounded contract.
- **FR-006**: The system MUST keep the follow-up retrieval model valid for Codex and future managed runtimes without changing the MoonMind-owned semantics of enablement, request routing, or returned retrieval outputs.
- **FR-007**: Moon Spec artifacts, implementation notes, verification output, commit text, and pull request metadata for this work MUST preserve Jira issue key `MM-506`.

### Key Entities

- **Follow-Up Retrieval Capability Signal**: The runtime-visible guidance that states whether retrieval is enabled and what request path and policy limits apply.
- **Follow-Up Retrieval Request**: The bounded managed-runtime request containing query, filters, result limit, overlay policy, and allowed budget override inputs.
- **ContextPack Metadata**: The structured retrieval summary returned alongside retrieved text so runtimes can consume follow-up retrieval results consistently.
- **MoonMind Retrieval Surface**: The MoonMind-owned tool, adapter boundary, or gateway that authoritatively handles follow-up retrieval for managed runtimes.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Validation proves managed runtimes can determine at startup whether follow-up retrieval is enabled and how to invoke it within policy bounds.
- **SC-002**: Validation proves follow-up retrieval requests are accepted only through MoonMind-owned surfaces and not through unmanaged runtime-specific bypass paths.
- **SC-003**: Validation proves the supported request contract accepts bounded retrieval inputs and returns both `ContextPack` metadata and text output for the next turn.
- **SC-004**: Validation proves disabled or invalid follow-up retrieval requests fail immediately with clear reasons rather than silently degrading behavior.
- **SC-005**: Validation proves the same follow-up retrieval semantics remain applicable to Codex and at least one future managed runtime integration path.
- **SC-006**: Traceability review confirms `MM-506` and DESIGN-REQ-003, DESIGN-REQ-007, DESIGN-REQ-015, DESIGN-REQ-019, DESIGN-REQ-020, DESIGN-REQ-023, and DESIGN-REQ-025 remain preserved in MoonSpec artifacts and downstream verification evidence.
