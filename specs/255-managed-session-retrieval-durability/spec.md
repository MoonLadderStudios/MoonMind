# Feature Specification: Managed-Session Retrieval Durability Boundaries

**Feature Branch**: `255-managed-session-retrieval-durability`
**Created**: 2026-04-24
**Status**: Draft
**Input**: User description: "Use the Jira preset brief for MM-507 as the canonical Moon Spec orchestration input.

Additional constraints:


Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts."

Preserved source Jira preset brief: `MM-507` from the trusted `jira.get_issue` response, reproduced verbatim in `## Original Preset Brief` below for downstream verification.

Original brief reference: trusted `jira.get_issue` MCP response for `MM-507`.
Classification: single-story runtime feature request.
Resume decision: no existing Moon Spec feature directory or later-stage artifacts matched `MM-507` under `specs/`, so `Specify` is the first incomplete stage.

## Original Preset Brief

```text
# MM-507 MoonSpec Orchestration Input

## Source

- Jira issue: MM-507
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Keep retrieval durability, reset semantics, and session continuity cache boundaries explicit
- Labels: `moonmind-workflow-mm-bcca563a-dede-4ba4-b325-811ef98fc640`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: synthesized from trusted Jira issue fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-507 from MM project
Summary: Keep retrieval durability, reset semantics, and session continuity cache boundaries explicit
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-507 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-507: Keep retrieval durability, reset semantics, and session continuity cache boundaries explicit

Source Reference
Source Document: docs/Rag/WorkflowRag.md
Source Title: Workflow RAG – Managed Session Retrieval
Source Sections:
- 3. Architectural position in MoonMind
- 6. Managed-session contract for RAG
- 9.1 Initial retrieval flow
- 10. ContextPack contract
- 15. Runtime rollout
Coverage IDs:
- DESIGN-REQ-005
- DESIGN-REQ-011
- DESIGN-REQ-012
- DESIGN-REQ-013
- DESIGN-REQ-017
- DESIGN-REQ-023

User Story
As MoonMind durability logic, I can treat session retrieval memory as a convenience cache while preserving authoritative retrieval truth in artifacts, refs, and bounded metadata that survive resets and new session epochs.

Acceptance Criteria
- Retrieval truth is recoverable from artifacts, refs, vector index state, and bounded metadata without depending on in-session cache state.
- Large retrieved bodies continue to live behind artifacts or refs rather than inside durable workflow payloads.
- Session reset is treated as a continuity boundary only; it does not delete authoritative retrieval state.
- After reset, MoonMind can rerun retrieval or reattach the latest context pack ref for the next step.
- Runtime adapters consume the same durable retrieval truth model rather than inventing runtime-specific persistence semantics.

Requirements
- Session continuity must never become the sole source of retrieval truth.
- Reset behavior preserves durable retrieval evidence and rebuildability.
- The shared ContextPack contract remains the authoritative retrieval result shape.

Implementation Notes
- Treat in-session retrieval memory as a convenience cache, not durable truth.
- Preserve authoritative retrieval state in artifacts, refs, vector index state, and bounded metadata that survive resets and new session epochs.
- Keep large retrieved bodies behind artifacts or refs rather than durable workflow payloads.
- Treat session reset as a continuity boundary only; do not delete authoritative retrieval state on reset.
- After reset, support either rerunning retrieval or reattaching the latest context pack ref for the next step.
- Keep runtime adapters on the shared durable retrieval truth model rather than runtime-specific persistence semantics.

Needs Clarification
- None from the trusted Jira response beyond the brief above.
```

## Classification

- Input type: Single-story feature request.
- Breakdown decision: `moonspec-breakdown` was not run because the Jira preset brief already defines one independently testable runtime story.
- Selected mode: Runtime.
- Source design: `docs/Rag/WorkflowRag.md` is treated as runtime source requirements because the brief describes system behavior, not documentation-only work.
- Source design path input: `.`
- Resume decision: No existing Moon Spec artifacts for `MM-507` were found under `specs/`; specification is the first incomplete stage.
- Multi-spec ordering: Not applicable for `MM-507` because the trusted Jira preset brief defines one independently testable story; if a future upstream breakdown produces multiple isolated specs, they must be processed in dependency order.

## User Story - Preserve Durable Retrieval Truth Across Session Resets

**Summary**: As MoonMind durability logic, I want managed-session retrieval state to remain authoritative in durable refs, artifacts, and bounded metadata so that session resets and new epochs do not turn transient session memory into the source of truth.

**Goal**: MoonMind preserves retrieval truth outside managed-session cache state, keeps large retrieved content behind durable publication surfaces, and lets runtimes resume or rebuild retrieval context after reset without inventing runtime-specific persistence semantics.

**Independent Test**: Start a managed-session workflow that performs retrieval, publish retrieval output behind artifacts or refs, then reset or replace the session epoch. Verify authoritative retrieval truth remains recoverable from durable MoonMind surfaces rather than session-local cache state, large retrieved bodies stay out of durable workflow payloads, the next step can rerun retrieval or reattach the latest context pack ref, and generated evidence preserves the Jira reference `MM-507`.

**Acceptance Scenarios**:

1. **Given** MoonMind has published retrieval results for a managed-session step, **When** retrieval state is reviewed after the step completes, **Then** authoritative retrieval truth remains recoverable from artifacts, refs, vector index state, and bounded metadata rather than from in-session cache alone.
2. **Given** retrieved context includes large text bodies, **When** MoonMind persists retrieval output, **Then** the large bodies remain behind artifact or ref surfaces instead of being copied into durable workflow payloads or treated as session-local durable truth.
3. **Given** a managed session is reset or a new session epoch begins, **When** MoonMind evaluates retrieval continuity, **Then** the reset is treated as a continuity boundary only and durable retrieval evidence remains intact.
4. **Given** a new step starts after a reset, **When** MoonMind prepares retrieval context for that step, **Then** it can either rerun retrieval or reattach the latest context pack ref without depending on the previous session cache contents.
5. **Given** Codex uses this retrieval durability model today and another managed runtime adopts it later, **When** retrieval persistence behavior is compared across runtimes, **Then** they use the same durable retrieval truth model instead of runtime-specific persistence semantics.
6. **Given** MoonSpec artifacts and downstream implementation evidence are generated for this work, **When** traceability is reviewed, **Then** the preserved Jira issue key `MM-507` remains present in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

### Edge Cases

- A session reset happens after retrieval has been published but before the next step consumes the prior context pack ref.
- Retrieval artifacts or refs remain available while the previous session-local cache has been discarded entirely.
- A runtime attempts to treat session continuity state as authoritative after durable retrieval evidence has diverged.
- Retrieved text contains stale or conflicting content and must remain governed by the existing untrusted-reference safety boundary after reset as well as before it.
- Future managed runtimes adopt the retrieval contract but attempt to introduce their own top-level persistence model for retrieval continuity.

## Assumptions

- `MM-507` is limited to retrieval durability, reset semantics, and continuity-cache boundaries for managed sessions rather than expanding the scope to new retrieval transports or unrelated retrieval features.
- The vector index, published refs, artifacts, and bounded retrieval metadata are already recognized durable retrieval surfaces within MoonMind even if the exact storage implementation evolves.
- The same durable retrieval truth model applies to current Codex behavior and future managed runtimes unless a later story explicitly changes the shared contract.

## Source Design Requirements

| ID | Source | Requirement | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-005 | `docs/Rag/WorkflowRag.md` §3, §9.1 | MoonMind owns retrieval context assembly and publishes retrieval results through artifacts, refs, and runtime input injection rather than relying on managed-session-local state as the primary source of truth. | In scope | FR-001, FR-004 |
| DESIGN-REQ-011 | `docs/Rag/WorkflowRag.md` §6.1, §10 | Managed-session retrieval results must remain referenced through artifact or context-ref surfaces, and large retrieved bodies must stay behind those durable publication boundaries. | In scope | FR-002 |
| DESIGN-REQ-012 | `docs/Rag/WorkflowRag.md` §6.2 | Session-local retrieval memory is a continuity cache only; authoritative retrieval truth remains in the vector index, persisted `ContextPack` artifacts, bounded metadata, and published refs. | In scope | FR-001 |
| DESIGN-REQ-013 | `docs/Rag/WorkflowRag.md` §6.3 | Session reset or new session epoch is a continuity boundary that must preserve durable retrieval state rather than delete it. | In scope | FR-003 |
| DESIGN-REQ-017 | `docs/Rag/WorkflowRag.md` §6.3, §9.1 | After reset, MoonMind must be able to rebuild retrieval context by rerunning retrieval or by reattaching the latest durable context pack reference for the next step. | In scope | FR-004 |
| DESIGN-REQ-023 | `docs/Rag/WorkflowRag.md` §15 | The same durable retrieval truth model must apply across Codex and future managed runtimes rather than becoming runtime-specific persistence behavior. | In scope | FR-005 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST keep authoritative managed-session retrieval truth recoverable from durable MoonMind surfaces, including artifacts, refs, vector index state, and bounded retrieval metadata, without depending on session-local cache state.
- **FR-002**: The system MUST keep large retrieved bodies behind artifact or ref publication surfaces rather than copying them into durable workflow payloads or treating them as durable session state.
- **FR-003**: The system MUST treat managed-session reset or session-epoch replacement as a continuity boundary only and MUST preserve previously published durable retrieval evidence across that boundary.
- **FR-004**: The system MUST support preparing the next step after reset by either rerunning retrieval or reattaching the latest durable context pack reference without requiring the prior session cache contents.
- **FR-005**: The system MUST enforce the same durable retrieval truth model for Codex and future managed runtimes without allowing runtime-specific persistence semantics to replace the shared MoonMind contract.
- **FR-006**: Moon Spec artifacts, implementation notes, verification output, commit text, and pull request metadata for this work MUST preserve Jira issue key `MM-507`.

### Key Entities

- **Durable Retrieval Truth**: The authoritative retrieval state preserved in artifacts, refs, vector index state, and bounded metadata rather than in session-local memory.
- **Session Continuity Cache**: The managed-session-local retrieval memory that may improve continuity but cannot become the durable source of truth.
- **Context Pack Reference**: The durable ref or artifact pointer that lets MoonMind reattach previously published retrieval context after reset.
- **Reset Continuity Boundary**: The transition where a session is cleared or replaced while durable retrieval evidence must remain intact and reusable.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Validation proves authoritative retrieval truth remains recoverable from durable MoonMind surfaces after retrieval runs and does not require session-local cache state.
- **SC-002**: Validation proves large retrieved bodies remain behind artifacts or refs rather than being copied into durable workflow payloads.
- **SC-003**: Validation proves resetting or replacing a managed session preserves previously published retrieval evidence and does not delete durable retrieval state.
- **SC-004**: Validation proves the next step after reset can continue by rerunning retrieval or reattaching the latest context pack ref.
- **SC-005**: Validation proves Codex and at least one future managed-runtime integration path can follow the same durable retrieval truth model without runtime-specific persistence divergence.
- **SC-006**: Traceability review confirms `MM-507` and DESIGN-REQ-005, DESIGN-REQ-011, DESIGN-REQ-012, DESIGN-REQ-013, DESIGN-REQ-017, and DESIGN-REQ-023 remain preserved in MoonSpec artifacts and downstream verification evidence.
