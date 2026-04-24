# Feature Specification: Retrieval Transport and Configuration Separation

**Feature Branch**: `256-retrieval-transport-separation`
**Created**: 2026-04-24
**Status**: Draft
**Input**: User description: "Use the Jira preset brief for MM-508 as the canonical Moon Spec orchestration input.

Additional constraints:


Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts."

Preserved source Jira preset brief: `MM-508` from the trusted `jira.get_issue` response, reproduced verbatim in `## Original Preset Brief` below for downstream verification.

Original brief reference: trusted `jira.get_issue` MCP response for `MM-508`.
Classification: single-story runtime feature request.
Resume decision: no existing Moon Spec feature directory or later-stage artifacts matched `MM-508` under `specs/`, so `Specify` is the first incomplete stage.

## Original Preset Brief

```text
# MM-508 MoonSpec Orchestration Input

## Source

- Jira issue: MM-508
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Separate retrieval configuration from provider profiles and support direct, gateway, and fallback retrieval modes
- Labels: `moonmind-workflow-mm-bcca563a-dede-4ba4-b325-811ef98fc640`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: synthesized from trusted Jira issue fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-508 from MM project
Summary: Separate retrieval configuration from provider profiles and support direct, gateway, and fallback retrieval modes
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-508 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-508: Separate retrieval configuration from provider profiles and support direct, gateway, and fallback retrieval modes

Source Reference
Source Document: docs/Rag/WorkflowRag.md
Source Title: Workflow RAG - Managed Session Retrieval
Source Sections:
- 2.2 Out of scope
- 5.3 Retrieval transports
- 5.4 Overlay support
- 7. Provider Profiles, embedding configuration, and retrieval ownership
- 8.4 Direct raw database access is optional, not the default contract
- 9.3 Local fallback flow
- 11. Filters, scope, and "how much context" knobs
- 16. Environment and settings
- 17. Recommended desired-state statement
Coverage IDs:
- DESIGN-REQ-004
- DESIGN-REQ-009
- DESIGN-REQ-010
- DESIGN-REQ-014
- DESIGN-REQ-016
- DESIGN-REQ-019
- DESIGN-REQ-024
- DESIGN-REQ-025

User Story
As MoonMind retrieval configuration, I can keep embedding and vector-store settings separate from managed runtime provider profiles while selecting direct, gateway, or explicit local fallback transport under policy.

Acceptance Criteria
- Retrieval configuration for embedding provider/model, Qdrant connection, collection, budgets, overlay mode, and retrieval URL remains separate from managed-runtime provider-profile launch settings.
- Gateway transport is supported and preferred when MoonMind owns outbound retrieval or embedding credentials are not present in the session environment.
- Direct transport remains available when policy and environment permit it, without becoming the required default contract.
- Local fallback retrieval is explicit, policy gated, and recorded as degraded behavior rather than silently masquerading as semantic retrieval.
- Overlay retrieval and the documented top_k, max_context_chars, filter, token-budget, latency-budget, and auto-context settings apply coherently across supported retrieval modes.

Requirements
- Provider profiles stay focused on runtime launch and provider shaping.
- Retrieval transport selection remains policy driven and environment aware.
- Overlay and budget knobs are part of the shared Workflow RAG contract.

Implementation Notes
- Keep retrieval configuration separate from managed-runtime provider-profile launch settings.
- Prefer gateway retrieval when MoonMind owns outbound retrieval or embedding credentials are not present in the session environment.
- Keep direct retrieval available when policy and environment permit it.
- Make local fallback explicit, policy gated, and recorded as degraded behavior.
- Keep overlay retrieval behavior and budgeting knobs coherent across supported retrieval modes.

Needs Clarification
- None from the trusted Jira response beyond the brief above.
```

## Classification

- Input type: Single-story feature request.
- Breakdown decision: `moonspec-breakdown` was not run because the Jira preset brief already defines one independently testable runtime story.
- Selected mode: Runtime.
- Source design: `docs/Rag/WorkflowRag.md` is treated as runtime source requirements because the brief describes system behavior, not documentation-only work.
- Source design path input: `.`
- Resume decision: No existing Moon Spec artifacts for `MM-508` were found under `specs/`; specification is the first incomplete stage.
- Multi-spec ordering: Not applicable for `MM-508` because the trusted Jira preset brief defines one independently testable story; if a future upstream breakdown produces multiple isolated specs, they must be processed in dependency order.

## User Story - Separate Retrieval Configuration From Runtime Profiles

**Summary**: As MoonMind retrieval configuration, I want retrieval transport and embedding/vector-store settings to stay separate from managed-runtime provider profiles so that MoonMind can choose direct, gateway, or explicit fallback retrieval without overloading runtime launch profiles.

**Goal**: MoonMind owns retrieval configuration as a distinct policy and execution contract, prefers gateway retrieval when the runtime environment should not carry embedding credentials, still allows direct retrieval when policy permits it, and records any local fallback as degraded retrieval rather than semantic retrieval.

**Independent Test**: Configure a managed runtime with a provider profile and run retrieval setup under multiple environment shapes. Verify runtime launch profile data remains separate from retrieval settings, gateway retrieval becomes the preferred transport when MoonMind owns outbound retrieval or embedding credentials are absent in the session environment, direct retrieval still works when configuration and policy allow it, local fallback is explicit and labeled as degraded behavior, overlay and budget knobs flow through the selected retrieval path, and all resulting MoonSpec artifacts preserve `MM-508`.

**Acceptance Scenarios**:

1. **Given** a managed runtime provider profile is selected for launch, **When** retrieval settings are resolved, **Then** embedding provider/model, Qdrant connection, collection, retrieval URL, overlay mode, and retrieval budgets are resolved from retrieval configuration rather than from provider-profile launch settings.
2. **Given** MoonMind owns outbound retrieval or the session environment lacks embedding credentials, **When** retrieval transport is resolved, **Then** gateway transport is selected as the preferred retrieval path without requiring embedding credentials to be stored on the managed-runtime provider profile.
3. **Given** embedding credentials and Qdrant access are available in an allowed environment, **When** retrieval transport is resolved, **Then** direct transport remains available and retrieval executes without requiring the gateway to be the only supported contract.
4. **Given** semantic retrieval cannot run for an allowed degraded reason, **When** local fallback search is used, **Then** the behavior is policy gated, explicitly labeled as local fallback or degraded retrieval, and does not masquerade as normal semantic retrieval.
5. **Given** overlay policy, top-k selection, max-context limits, repository filters, and latency or token budgets are configured, **When** retrieval executes through direct, gateway, or fallback-compatible paths, **Then** the shared Workflow RAG contract applies those knobs coherently and exposes the selected transport and retrieval metadata.
6. **Given** MoonSpec artifacts and downstream implementation evidence are generated for this work, **When** traceability is reviewed, **Then** the preserved Jira issue key `MM-508` remains present in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

### Edge Cases

- A provider profile is valid for runtime launch but does not expose embedding credentials, so retrieval must still prefer gateway transport without treating the profile as a generic embedding secret source.
- A runtime environment has direct embedding credentials and Qdrant access but also has a retrieval gateway URL configured, so transport resolution must choose a deterministic preferred path.
- Retrieval cannot execute because the embedding provider is unsupported, Qdrant is unavailable, or a gateway URL is missing, and only an explicitly allowed degraded local fallback path may run.
- Overlay retrieval is enabled while the selected transport changes between direct and gateway, and the same overlay and budget contract must remain coherent.
- A future managed runtime adopts Workflow RAG and attempts to encode retrieval transport choice inside its provider profile rather than through shared retrieval configuration.

## Assumptions

- `MM-508` is scoped to retrieval configuration ownership and transport-selection behavior, not to redesigning the full provider-profile schema or the entire ingest pipeline.
- Gateway preference is evaluated from retrieval configuration and environment availability, not from runtime-provider-profile fields alone.
- Local fallback is an intentionally degraded retrieval mode and should stay observable as such even when it returns useful repository context.

## Source Design Requirements

| ID | Source | Requirement | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-004 | `docs/Rag/WorkflowRag.md` §2.2, §7.1-§7.4 | Provider profiles launch managed runtimes but are not, by default, the generic retrieval-credential model; retrieval configuration remains separate from runtime launch configuration. | In scope | FR-001, FR-002 |
| DESIGN-REQ-009 | `docs/Rag/WorkflowRag.md` §5.3, §7.2-§7.4 | Gateway transport is supported and is the preferred default when MoonMind owns outbound retrieval or embedding credentials are not otherwise available in the worker or session environment. | In scope | FR-002 |
| DESIGN-REQ-010 | `docs/Rag/WorkflowRag.md` §5.3, §7.2 | Direct transport remains available when policy and environment permit embedding and Qdrant access. | In scope | FR-003 |
| DESIGN-REQ-014 | `docs/Rag/WorkflowRag.md` §9.3 | Local fallback retrieval is explicit, policy gated, and recorded as degraded behavior rather than silent semantic retrieval. | In scope | FR-004 |
| DESIGN-REQ-016 | `docs/Rag/WorkflowRag.md` §5.4, §11 | Overlay retrieval, top_k, max_context_chars, filter, token-budget, latency-budget, and auto-context knobs apply coherently across supported retrieval modes. | In scope | FR-005 |
| DESIGN-REQ-019 | `docs/Rag/WorkflowRag.md` §16 | Retrieval environment and settings remain configuration-driven and distinct from runtime-launch concerns. | In scope | FR-001, FR-005 |
| DESIGN-REQ-024 | `docs/Rag/WorkflowRag.md` §8.4 | Managed sessions do not receive unrestricted control-plane or raw data-store authority as part of retrieval. | In scope | FR-002, FR-003 |
| DESIGN-REQ-025 | `docs/Rag/WorkflowRag.md` §17 | Workflow RAG should preserve the desired-state statement: retrieval configuration remains shared MoonMind contract logic rather than runtime-specific or profile-specific behavior. | In scope | FR-001, FR-002, FR-005 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST resolve embedding provider/model, vector-store connection, collection, retrieval URL, overlay mode, and retrieval budgets from retrieval configuration rather than from managed-runtime provider-profile launch settings.
- **FR-002**: The system MUST support and prefer gateway retrieval when MoonMind owns outbound retrieval or embedding credentials are not otherwise available in the managed-runtime environment, without treating provider profiles as a generic embedding-credential source by default.
- **FR-003**: The system MUST preserve direct retrieval as an available execution path when environment and policy permit embedding and Qdrant access.
- **FR-004**: The system MUST keep local fallback retrieval explicit, policy gated, and observable as degraded retrieval rather than allowing it to masquerade as semantic retrieval.
- **FR-005**: The system MUST apply overlay policy, top-k, max-context, filters, and latency or token budget settings coherently across the supported retrieval paths and expose the selected retrieval transport through compact retrieval metadata.
- **FR-006**: The system MUST keep retrieval configuration ownership and transport choice within the shared MoonMind Workflow RAG contract rather than moving those concerns into runtime-specific provider-profile semantics.
- **FR-007**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata for this work MUST preserve Jira issue key `MM-508`.

### Key Entities

- **Retrieval Configuration**: The MoonMind-owned settings that determine embedding provider/model, vector-store connection, collection, budgets, overlay behavior, retrieval URL, and transport policy.
- **Managed Runtime Provider Profile**: The runtime-launch profile that shapes provider identity, runtime materialization, and runtime-specific credentials without becoming the generic retrieval-credential source.
- **Retrieval Transport**: The selected execution path for retrieval, such as `direct`, `gateway`, or explicit degraded `local_fallback` behavior.
- **Retrieval Metadata Contract**: The compact metadata MoonMind records for selected transport, artifact refs, continuity hints, and degraded-mode visibility.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Validation proves retrieval settings for embedding, vector store, collection, retrieval URL, overlay mode, and budgets remain distinct from runtime provider-profile launch settings.
- **SC-002**: Validation proves gateway retrieval is selected as the preferred path when embedding credentials are unavailable in the runtime environment or MoonMind owns outbound retrieval.
- **SC-003**: Validation proves direct retrieval remains available and functional when environment and policy permit it.
- **SC-004**: Validation proves local fallback appears only as an explicit degraded mode with observable metadata and gating rather than silent semantic retrieval.
- **SC-005**: Validation proves overlay, filter, top-k, and budget knobs behave coherently across supported retrieval paths and the selected transport remains observable in compact metadata.
- **SC-006**: Validation proves runtime provider profiles do not become the implicit source of generic retrieval credentials or transport policy.
- **SC-007**: Traceability review confirms `MM-508` and DESIGN-REQ-004, DESIGN-REQ-009, DESIGN-REQ-010, DESIGN-REQ-014, DESIGN-REQ-016, DESIGN-REQ-019, DESIGN-REQ-024, and DESIGN-REQ-025 remain preserved in MoonSpec artifacts and downstream verification evidence.
