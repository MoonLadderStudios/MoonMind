# Feature Specification: Retrieval Evidence And Trust Guardrails

**Feature Branch**: `257-retrieval-evidence-guardrails`
**Created**: 2026-04-24
**Status**: Draft
**Input**: User description: "Use the Jira preset brief for MM-509 as the canonical Moon Spec orchestration input.

Additional constraints:


Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts."

Preserved source Jira preset brief: `MM-509` from the trusted normalized Jira issue detail response, reproduced in `## Original Preset Brief` below for downstream verification.

Original brief reference: trusted `jira.get_issue` MCP response plus normalized trusted Jira issue detail `/api/jira/issues/MM-509?projectKey=MM`.
Classification: single-story runtime feature request.
Resume decision: no existing Moon Spec feature directory or later-stage artifacts matched `MM-509` under `specs/`, so `Specify` is the first incomplete stage.

## Original Preset Brief

```text
# MM-509 MoonSpec Orchestration Input

## Source

- Jira issue: MM-509
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Record retrieval evidence and enforce trust and secret-handling guardrails
- Trusted fetch tool: jira.get_issue
- Normalized detail source: /api/jira/issues/MM-509?projectKey=MM
- Canonical source: recommendedImports.presetInstructions from the normalized trusted Jira issue detail response.

## Canonical MoonSpec Feature Request

Jira issue: MM-509 from MM project
Summary: Record retrieval evidence and enforce trust and secret-handling guardrails
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-509 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-509: Record retrieval evidence and enforce trust and secret-handling guardrails

Source Reference
Source Document: docs/Rag/WorkflowRag.md
Source Title: Workflow RAG - Managed Session Retrieval
Source Sections:
- 9.3 Local fallback flow
- 10.2 Prompt safety
- 12. Managed-session enablement rules
- 13. Observability and evidence
- 14. Security and trust rules
- 15. Runtime rollout
- 17. Recommended desired-state statement
Coverage IDs:
- DESIGN-REQ-016
- DESIGN-REQ-018
- DESIGN-REQ-020
- DESIGN-REQ-021
- DESIGN-REQ-022
- DESIGN-REQ-023
- DESIGN-REQ-025
As MoonMind operations, I can audit every retrieval action with durable evidence while keeping retrieved text untrusted and preventing raw secrets or unsafe retrieval authority from leaking into durable surfaces.
```

## Classification

- Input type: Single-story feature request.
- Breakdown decision: `moonspec-breakdown` was not run because the Jira preset brief is not a broad technical or declarative design and already defines one independently testable runtime story.
- Selected mode: Runtime.
- Source design: `docs/Rag/WorkflowRag.md` is treated as runtime source requirements because the brief describes system behavior, not documentation-only work.
- Source design path input: `.`
- Resume decision: No existing Moon Spec artifacts for `MM-509` were found under `specs/`; specification is the first incomplete stage.
- Multi-spec ordering: Not applicable for `MM-509` because the trusted Jira preset brief defines one independently testable story; if a future broad design for `MM-509` requires multiple isolated specs, each generated spec must stay isolated and they must be processed in dependency order.

## User Story - Record Retrieval Evidence And Enforce Trust Guardrails

**Summary**: As MoonMind operations, I want every retrieval action to publish durable evidence and enforce trust and secret-handling guardrails so that retrieval remains auditable, policy-bounded, and safe across managed runtimes.

**Goal**: MoonMind records durable retrieval evidence, keeps retrieved text inside an explicit untrusted-reference boundary, prevents secret-bearing retrieval data from leaking into durable surfaces, and preserves the same bounded retrieval rules across Codex and future managed runtimes.

**Independent Test**: Execute retrieval through automatic and session-issued paths, including an allowed degraded or fallback case, and verify MoonMind records durable evidence for initiation mode, transport, filters, result counts, budgets, truncation, artifact/ref locations, and degraded reasons when applicable. Confirm retrieved text is injected with untrusted-reference safety framing that prefers current workspace state on conflict, raw provider keys or token-bearing config bodies are absent from durable workflow payloads and retrieval artifacts, session-issued retrieval remains bounded by authorized scope and policy controls, and traceability artifacts preserve `MM-509`.

**Acceptance Scenarios**:

1. **Given** MoonMind performs automatic or session-issued retrieval, **When** the retrieval completes or degrades, **Then** durable evidence records the initiation mode, selected transport, applied filters, result count, budgets and usage, truncation state, artifact or ref location, and degraded reason when applicable.
2. **Given** retrieved text is delivered to a runtime, **When** that text may contain stale, conflicting, or malicious content, **Then** the runtime-facing retrieval prompt framing treats it as untrusted reference material and prefers the current checked-out workspace state when retrieved content conflicts with the repository.
3. **Given** retrieval uses provider keys, OAuth tokens, or secret-bearing configuration to perform retrieval work, **When** MoonMind publishes workflow payloads, retrieval artifacts, or other durable evidence, **Then** those durable surfaces exclude raw secrets and secret-bearing config bodies.
4. **Given** a managed session issues retrieval requests after startup, **When** MoonMind evaluates the request, **Then** the retrieval remains bounded by authorized corpus scope, filters, budgets, transport policy, provider or secret policy, and audit requirements before any retrieval result is published.
5. **Given** retrieval is disabled or semantic retrieval degrades to an allowed fallback path, **When** the runtime observes retrieval state, **Then** MoonMind exposes explicit enablement, disabled, or degraded behavior through runtime-visible capability or telemetry surfaces rather than silently implying normal semantic retrieval.
6. **Given** Codex uses Workflow RAG today and another managed runtime adopts the same retrieval contract later, **When** retrieval evidence and trust behavior are compared across runtimes, **Then** they use the same observability, safety, policy, and durable-evidence model instead of runtime-specific exceptions.
7. **Given** MoonSpec artifacts and downstream implementation evidence are generated for this work, **When** traceability is reviewed, **Then** the preserved Jira issue key `MM-509` remains present in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

### Edge Cases

- Semantic retrieval is unavailable and MoonMind uses local fallback search under an explicitly allowed degraded mode, so the degraded reason and fallback transport must remain durable and visible.
- Retrieved content includes prompt-injection text or stale code snippets that conflict with the checked-out repository, so runtime framing must preserve the trust boundary and prefer current workspace state.
- Retrieval runs with zero matching items or is skipped because retrieval is disabled, but MoonMind still needs durable observability explaining what happened.
- A session-issued retrieval request asks for context outside authorized corpus scope or beyond budget and policy limits, so the request must fail or degrade safely without bypassing audit requirements.
- A future managed runtime supports the shared retrieval contract but attempts to publish different evidence fields or weaken trust and secret-handling protections.

## Assumptions

- `MM-509` is scoped to retrieval evidence, trust framing, policy bounds, and secret-handling guardrails rather than to redesigning retrieval ranking or indexing algorithms.
- The source document sections referenced in the Jira brief are the authoritative runtime requirements for this story even if implementation details are spread across multiple services.
- The same retrieval evidence and trust model should apply whether retrieval runs automatically at step start or is requested later by a managed session.

## Source Design Requirements

| ID | Source | Requirement | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-016 | `docs/Rag/WorkflowRag.md` §9.3, §13 | Retrieval must record durable evidence for initiation mode, transport, filters, result count, budgets, truncation, artifact or ref location, and degraded reason when fallback or degraded behavior occurs. | In scope | FR-001, FR-005 |
| DESIGN-REQ-018 | `docs/Rag/WorkflowRag.md` §10.2, §14.4 | Retrieved text is reference data, not instructions, and runtime injection must preserve an untrusted-reference boundary that prefers current workspace state on conflict. | In scope | FR-002 |
| DESIGN-REQ-020 | `docs/Rag/WorkflowRag.md` §14.1 | Raw provider keys, OAuth tokens, and secret-bearing configuration bodies must not be placed into durable workflow payloads, retrieval artifacts, or similar evidence surfaces. | In scope | FR-003 |
| DESIGN-REQ-021 | `docs/Rag/WorkflowRag.md` §14.2 | Session-issued retrieval remains bounded by authorized corpus scope, filters, budgets, transport policy, provider or secret policy, and audit requirements. | In scope | FR-004 |
| DESIGN-REQ-022 | `docs/Rag/WorkflowRag.md` §12.1-§12.2 | Retrieval enablement, disabled state, and degraded behavior must remain explicit to managed runtimes through capability or telemetry signals rather than silent assumptions. | In scope | FR-005 |
| DESIGN-REQ-023 | `docs/Rag/WorkflowRag.md` §15 | Codex and future managed runtimes must apply the same shared retrieval observability and trust contract rather than diverging at the runtime layer. | In scope | FR-006 |
| DESIGN-REQ-025 | `docs/Rag/WorkflowRag.md` §17 | Workflow RAG remains artifact or ref-backed, policy-bounded, and consistent across runtimes in the desired-state model. | In scope | FR-001, FR-004, FR-006 |

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST publish durable evidence for every retrieval operation that records initiation mode, selected transport, applied filters, result count, budgets and usage, truncation state, artifact or ref location, and degraded reason when applicable.
- **FR-002**: The system MUST deliver retrieved text to runtimes with safety framing that treats retrieved content as untrusted reference data and prefers the current checked-out workspace state when retrieved content conflicts with repository state.
- **FR-003**: The system MUST exclude raw provider keys, OAuth tokens, and secret-bearing retrieval configuration bodies from durable workflow payloads, retrieval artifacts, and other durable retrieval evidence.
- **FR-004**: The system MUST enforce authorized corpus scope, filters, budgets, transport policy, provider or secret policy, and audit requirements on session-issued retrieval before publishing retrieval results.
- **FR-005**: The system MUST expose retrieval enabled, disabled, and degraded or fallback behavior explicitly through runtime-visible capability or telemetry surfaces instead of silently presenting degraded retrieval as normal semantic retrieval.
- **FR-006**: The system MUST preserve the same durable evidence, trust boundary, and policy-enforcement model across Codex and future managed runtimes that support Workflow RAG.
- **FR-007**: Moon Spec artifacts, implementation notes, verification output, commit text, and pull request metadata for this work MUST preserve Jira issue key `MM-509`.

### Key Entities

- **Retrieval Evidence Record**: The durable retrieval metadata that records how retrieval ran, what limits or filters applied, what was published, and whether degraded behavior occurred.
- **Trust Framing**: The runtime-facing retrieval prompt or instruction boundary that marks retrieved text as untrusted reference material and resolves conflicts in favor of current workspace state.
- **Retrieval Policy Envelope**: The authorized corpus scope, filters, budgets, transport controls, provider or secret policy, and audit requirements that bound session-issued retrieval.
- **Degraded Retrieval State**: The explicit fallback or disabled retrieval condition that must remain visible to operators and runtimes rather than being mistaken for normal semantic retrieval.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Validation proves every retrieval operation publishes durable evidence with the required mode, transport, filter, count, budget, truncation, publication-location, and degraded-reason fields.
- **SC-002**: Validation proves runtime-facing retrieval delivery treats retrieved text as untrusted reference material and prefers current workspace state when retrieved content conflicts with repository state.
- **SC-003**: Validation proves raw provider keys, OAuth tokens, and secret-bearing retrieval configuration bodies are absent from durable workflow payloads and retrieval artifacts.
- **SC-004**: Validation proves session-issued retrieval requests cannot bypass authorized scope, filters, budgets, transport controls, provider or secret policy, or audit requirements.
- **SC-005**: Validation proves disabled and degraded retrieval states remain explicit in runtime-facing capability or telemetry surfaces and do not masquerade as normal semantic retrieval.
- **SC-006**: Validation proves Codex and at least one future managed runtime can follow the same durable evidence and trust-guardrail contract without runtime-specific weakening.
- **SC-007**: Traceability review confirms `MM-509` and DESIGN-REQ-016, DESIGN-REQ-018, DESIGN-REQ-020, DESIGN-REQ-021, DESIGN-REQ-022, DESIGN-REQ-023, and DESIGN-REQ-025 remain preserved in MoonSpec artifacts and downstream verification evidence.
