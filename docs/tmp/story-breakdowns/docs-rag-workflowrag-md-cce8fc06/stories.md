# Story Breakdown: docs/Rag/WorkflowRag.md

- Source design: `docs/Rag/WorkflowRag.md`
- Source title: Workflow RAG – Managed Session Retrieval
- Story extraction date: `2026-04-24T06:34:33Z`
- Output mode: `jira`
- Coverage gate: `PASS - every major design point is owned by at least one story.`

## Design Summary

Workflow RAG defines MoonMind-managed document and code retrieval for managed sessions. MoonMind remains the owner of initial context assembly, durable publication, and policy enforcement, while managed sessions may request additional retrieval through bounded MoonMind-owned surfaces. The design is embedding-first, artifact/ref-backed, gateway-first when MoonMind owns outbound retrieval, and explicit about reset semantics, degraded local fallback, observability evidence, and trust boundaries.

## Coverage Points

### DESIGN-REQ-001: Managed-session-first retrieval purpose
- Type: `requirement`
- Source section: `1. Summary`
- Explanation: Workflow RAG exists to let MoonMind assemble relevant document and code context, publish it durably, and deliver it at managed-step boundaries rather than relying on ad hoc chat retrieval.

### DESIGN-REQ-002: Lean retrieval path without upstream chat hop
- Type: `constraint`
- Source section: `1. Summary; 4.3 No additional general chat-model hop required for retrieval`
- Explanation: Retrieval remains embedding-first and vector-search-driven, producing a ContextPack without a separate generative summarization hop.

### DESIGN-REQ-003: Scope includes initial and follow-up retrieval for managed sessions
- Type: `requirement`
- Source section: `2.1 In scope`
- Explanation: The design covers initial context resolution, session-initiated follow-up retrieval, direct and gateway transport, overlays, filtering, budgeting, and provider-profile interaction.

### DESIGN-REQ-004: Out-of-scope boundaries remain explicit
- Type: `non-goal`
- Source section: `2.2 Out of scope`
- Explanation: The design intentionally excludes full ingest details, full managed-session contracts, full provider-profile schema, raw secret backend behavior, long-term memory beyond document/code retrieval, and unrestricted DB administration.

### DESIGN-REQ-005: MoonMind owns context assembly and durable refs
- Type: `state-model`
- Source section: `3. Architectural position in MoonMind`
- Explanation: Retrieval is one context plane within the managed-session system, and large bodies must live behind refs such as contextRefs and artifacts rather than durable payload inline blobs.

### DESIGN-REQ-006: Primary initial retrieval pipeline
- Type: `workflow`
- Source section: `4.1 Primary model: MoonMind-owned initial context resolution`
- Explanation: MoonMind resolves settings and scope, embeds the query, searches the vector index, builds a ContextPack, persists artifacts/refs, and injects retrieved context into the managed runtime before execution.

### DESIGN-REQ-007: Session-initiated follow-up retrieval path
- Type: `workflow`
- Source section: `4.2 Secondary model: session-initiated follow-up retrieval`
- Explanation: Managed sessions may request more context during execution to refine searches, compare code paths, retrieve overlay content, or adjust scope and budgets.

### DESIGN-REQ-008: Current reference implementation through Codex workspace preparation
- Type: `integration`
- Source section: `5.2 Managed runtime integration today`
- Explanation: Codex currently prepares workspace retrieval via ContextInjectionService, publishes artifacts under artifacts/context, prepends instruction refs, and applies safety framing.

### DESIGN-REQ-009: Retrieval transports direct and gateway
- Type: `integration`
- Source section: `5.3 Retrieval transports`
- Explanation: Both direct and gateway modes exist, but gateway is the preferred default when MoonMind owns outbound retrieval or embedding credentials are unavailable in session environments.

### DESIGN-REQ-010: Workspace overlay retrieval support
- Type: `artifact`
- Source section: `5.4 Overlay support`
- Explanation: Retrieval must be able to include workspace changes made during a run before those changes are indexed canonically.

### DESIGN-REQ-011: Context belongs behind refs and durable artifacts
- Type: `artifact`
- Source section: `6.1 Context belongs behind refs and artifacts`
- Explanation: Large retrieved bodies must be referenced through artifacts or contextRefs, while durable truth remains bounded metadata plus published refs.

### DESIGN-REQ-012: Session state is convenience cache only
- Type: `state-model`
- Source section: `6.2 Session state is a continuity cache, not durable truth`
- Explanation: Local session memory is non-authoritative; MoonMind must be able to rebuild retrieval context from the vector index, persisted ContextPack artifacts, and bounded metadata after resets.

### DESIGN-REQ-013: Reset semantics preserve durable retrieval truth
- Type: `state-model`
- Source section: `6.3 Reset semantics`
- Explanation: Session resets clear continuity but must not delete durable retrieval state, and MoonMind should be able to rerun or reattach context after reset.

### DESIGN-REQ-014: Provider profiles are separate from retrieval credentials
- Type: `constraint`
- Source section: `7. Provider Profiles, embedding configuration, and retrieval ownership`
- Explanation: Managed runtime launch settings must remain distinct from embedding/retrieval configuration, with gateway transport bridging them when needed.

### DESIGN-REQ-015: Session-facing retrieval surface is MoonMind-owned and bounded
- Type: `integration`
- Source section: `8. Retrieval execution modes`
- Explanation: Sessions can request arbitrary authorized semantic retrieval queries through a MoonMind tool or gateway, not unrestricted raw vector-database admin access.

### DESIGN-REQ-016: Initial, follow-up, and local fallback flows remain explicit
- Type: `workflow`
- Source section: `9. Retrieval flow details`
- Explanation: The design specifies explicit automatic retrieval, session-initiated retrieval, and degraded local fallback flows with metadata describing fallback behavior.

### DESIGN-REQ-017: ContextPack minimum and strengthening contract
- Type: `artifact`
- Source section: `10. ContextPack contract`
- Explanation: ContextPack must include context_text, items, filters, budgets, usage, transport, retrieved_at, telemetry_id, and provenance-bearing item metadata, with future strengthening around trust, overlay distinction, truncation, and artifact refs.

### DESIGN-REQ-018: Prompt safety boundary for retrieved text
- Type: `security`
- Source section: `10.2 Prompt safety`
- Explanation: Runtime injection must frame retrieved content as untrusted reference data and prefer current repository state when retrieved content conflicts with the checked-out workspace.

### DESIGN-REQ-019: Filters, top-k, context-size, budgets, and overlay knobs
- Type: `requirement`
- Source section: `11. Filters, scope, and “how much context” knobs`
- Explanation: Workflow RAG needs top_k, max_context_chars, filters, token and latency budgets, and overlay policy controls for both automatic and session-initiated retrieval subject to policy.

### DESIGN-REQ-020: RAG enablement and capability signalling
- Type: `requirement`
- Source section: `12. Managed-session enablement rules`
- Explanation: When disabled, retrieval fails fast and is not assumed available; when enabled, automatic retrieval, follow-up retrieval, publication, and runtime-neutral capability signals must be present.

### DESIGN-REQ-021: Durable observability evidence for every retrieval operation
- Type: `observability`
- Source section: `13. Observability and evidence`
- Explanation: Each retrieval operation must leave durable evidence covering initiation mode, transport, filters, item count, budgets, truncation, artifact refs, and degraded reasons.

### DESIGN-REQ-022: Security and trust guardrails for retrieval
- Type: `security`
- Source section: `14. Security and trust rules`
- Explanation: No raw secrets may land in durable payloads or artifacts; retrieval remains policy-bounded, gateway-first when MoonMind owns outbound calls, and retrieved text must be treated as stale or malicious until proven otherwise.

### DESIGN-REQ-023: Shared runtime rollout model
- Type: `migration`
- Source section: `15. Runtime rollout`
- Explanation: Codex is the current reference implementation, but Claude Code, Gemini CLI, and future runtimes must adopt the same MoonMind-owned retrieval model and shared ContextPack/observability contract at the adapter boundary.

### DESIGN-REQ-024: Environment settings support the retrieval contract
- Type: `integration`
- Source section: `16. Environment and settings`
- Explanation: The contract depends on configuration for embedding provider/model, Qdrant location and auth, collection, budgets, overlay mode, retrieval gateway URL, and auto-context enablement, while Mission Control exposure stays separate.

### DESIGN-REQ-025: Desired-state statement anchors the implementation shape
- Type: `requirement`
- Source section: `17. Recommended desired-state statement`
- Explanation: The target model is embedding-first, managed-session-centered, MoonMind-owned, artifact/ref-backed, gateway-first when appropriate, follow-up capable, policy bounded, and consistent across runtimes.

## Story Candidates

### STORY-001: Resolve and publish initial managed-session retrieval context
- Short name: `initial-rag-pack`
- Source document: `docs/Rag/WorkflowRag.md`
- Source sections: `1. Summary`, `3. Architectural position in MoonMind`, `4.1 Primary model: MoonMind-owned initial context resolution`, `5.1 Retrieval path`, `5.2 Managed runtime integration today`, `6.1 Context belongs behind refs and artifacts`, `9.1 Initial retrieval flow`, `10. ContextPack contract`, `17. Recommended desired-state statement`
- Coverage IDs: `DESIGN-REQ-001`, `DESIGN-REQ-002`, `DESIGN-REQ-005`, `DESIGN-REQ-006`, `DESIGN-REQ-008`, `DESIGN-REQ-011`, `DESIGN-REQ-017`, `DESIGN-REQ-025`
- Why this story exists: This is the primary value path in the design and establishes MoonMind, not the runtime, as the owner of initial context assembly and durable retrieval truth.
- Independent test: Start a managed step with Workflow RAG enabled and verify MoonMind resolves retrieval before the runtime begins work, persists a ContextPack artifact/ref with required fields, and injects the retrieved context into the runtime input without an upstream chat-model summarization hop.
- Scope:
  - Resolve retrieval settings and scope for a managed step before substantive runtime work begins.
  - Embed the retrieval query, search the vector index, build a ContextPack, and publish the result as artifacts or refs.
  - Inject or otherwise deliver the retrieved context into the managed runtime while preserving artifact-backed durable truth.
- Out of scope:
  - Full ingest-pipeline design and long-term memory features outside document/code retrieval.
  - Session-initiated follow-up retrieval tooling beyond whatever is necessary to avoid blocking the initial path.
- Acceptance criteria:
  - Initial managed-step execution resolves retrieval settings and scope before runtime task execution begins.
  - The retrieval path performs embedding plus vector search and packages results into a ContextPack without requiring a separate general chat/completions retrieval hop.
  - Retrieved context is persisted or published behind artifacts/refs, and large bodies are not copied into durable workflow payloads.
  - The managed runtime receives the retrieved context through the adapter input surface together with the existing safety framing for untrusted retrieved text.
  - Current Codex-style workspace preparation remains representable by this contract rather than as a bespoke one-off implementation.
- Dependencies: None
- Assumptions:
  - Existing retrieval services and artifact publication surfaces can be extended without redefining the ingest system.
- Needs clarification: None

### STORY-002: Enable managed sessions to request additional retrieval through MoonMind-owned surfaces
- Short name: `followup-rag-tool`
- Source document: `docs/Rag/WorkflowRag.md`
- Source sections: `2.1 In scope`, `4.2 Secondary model: session-initiated follow-up retrieval`, `8. Retrieval execution modes`, `9.2 Session-initiated retrieval flow`, `11. Filters, scope, and “how much context” knobs`, `12. Managed-session enablement rules`, `15. Runtime rollout`, `17. Recommended desired-state statement`
- Coverage IDs: `DESIGN-REQ-003`, `DESIGN-REQ-007`, `DESIGN-REQ-015`, `DESIGN-REQ-019`, `DESIGN-REQ-020`, `DESIGN-REQ-023`, `DESIGN-REQ-025`
- Why this story exists: The design explicitly allows sessions to ask for more context, but only through MoonMind-controlled contracts that preserve policy, observability, and portability across runtimes.
- Independent test: Within an active managed session with RAG enabled, request additional retrieval through the supported MoonMind surface and verify the runtime receives a bounded ContextPack plus text output, with the request failing fast and clearly when RAG is disabled.
- Scope:
  - Expose a runtime-neutral retrieval request surface for managed sessions when RAG is enabled.
  - Support query, filters, top_k, overlay policy, and bounded budget overrides.
  - Return both machine-readable ContextPack/JSON output and text output for immediate use in the next turn.
- Out of scope:
  - Unrestricted raw vector-database administration from sessions.
  - New runtime-specific RAG architectures that diverge from the shared managed-session contract.
- Acceptance criteria:
  - Managed sessions can issue follow-up retrieval requests only through a MoonMind-owned tool, adapter surface, or gateway.
  - The request contract supports query, filters, top_k, overlay policy, and bounded budget overrides, and returns both ContextPack metadata and text output.
  - The runtime receives an explicit capability signal when retrieval is enabled, including how to request more context and what budgets or scope constraints apply.
  - When retrieval is disabled, follow-up retrieval requests fail fast with a clear reason instead of silently degrading to an undefined behavior.
  - The same follow-up retrieval model remains valid for Codex now and future managed runtimes later.
- Dependencies: `STORY-001`
- Needs clarification: None

### STORY-003: Keep retrieval durability, reset semantics, and session continuity cache boundaries explicit
- Short name: `rag-durable-state`
- Source document: `docs/Rag/WorkflowRag.md`
- Source sections: `3. Architectural position in MoonMind`, `6. Managed-session contract for RAG`, `9.1 Initial retrieval flow`, `10. ContextPack contract`, `15. Runtime rollout`
- Coverage IDs: `DESIGN-REQ-005`, `DESIGN-REQ-011`, `DESIGN-REQ-012`, `DESIGN-REQ-013`, `DESIGN-REQ-017`, `DESIGN-REQ-023`
- Why this story exists: The retrieval design depends on clear durable truth boundaries so resets, retries, and managed-session continuity do not erase or silently fork context state.
- Independent test: Persist retrieval context for a managed session, reset or replace the session epoch, and verify MoonMind can reattach or recompute retrieval context from durable state while session-local cache contents are treated as non-authoritative.
- Scope:
  - Define and enforce authoritative retrieval truth in artifacts, refs, vector index state, and bounded metadata.
  - Ensure session-local retrieval memory is treated as a rebuildable continuity cache only.
  - Support reset behavior that can reattach or recompute retrieval state without deleting durable evidence.
- Out of scope:
  - Redefining the full managed-session contract outside retrieval continuity and reset semantics.
  - Long-term memory product work outside document/code retrieval.
- Acceptance criteria:
  - Retrieval truth is recoverable from artifacts, refs, vector index state, and bounded metadata without depending on in-session cache state.
  - Large retrieved bodies continue to live behind artifacts or refs rather than inside durable workflow payloads.
  - Session reset is treated as a continuity boundary only; it does not delete authoritative retrieval state.
  - After reset, MoonMind can rerun retrieval or reattach the latest context pack ref for the next step.
  - Runtime adapters consume the same durable retrieval truth model rather than inventing runtime-specific persistence semantics.
- Dependencies: `STORY-001`
- Needs clarification: None

### STORY-004: Separate retrieval configuration from provider profiles and support direct, gateway, and fallback retrieval modes
- Short name: `rag-transport-policy`
- Source document: `docs/Rag/WorkflowRag.md`
- Source sections: `2.2 Out of scope`, `5.3 Retrieval transports`, `5.4 Overlay support`, `7. Provider Profiles, embedding configuration, and retrieval ownership`, `8.4 Direct raw database access is optional, not the default contract`, `9.3 Local fallback flow`, `11. Filters, scope, and “how much context” knobs`, `16. Environment and settings`, `17. Recommended desired-state statement`
- Coverage IDs: `DESIGN-REQ-004`, `DESIGN-REQ-009`, `DESIGN-REQ-010`, `DESIGN-REQ-014`, `DESIGN-REQ-016`, `DESIGN-REQ-019`, `DESIGN-REQ-024`, `DESIGN-REQ-025`
- Why this story exists: This preserves the architectural boundary between runtime launch and retrieval ownership, while allowing retrieval to work in environments that lack embedding credentials inside the session container.
- Independent test: Configure a managed runtime with RAG enabled under gateway and direct-capable environments, verify retrieval mode selection honors policy and settings, and confirm that degraded local fallback is only used when allowed and is recorded explicitly.
- Scope:
  - Represent retrieval transport selection between direct, gateway, and explicit local fallback modes.
  - Keep embedding and vector-store configuration distinct from managed-runtime provider-profile launch configuration.
  - Support overlay retrieval and policy-bounded filter and budget knobs across retrieval modes.
- Out of scope:
  - Changing Mission Control settings UX beyond whatever fields are needed to honor the runtime contract.
  - Treating provider profiles as an implicit or automatic source of embedding credentials.
- Acceptance criteria:
  - Retrieval configuration for embedding provider/model, Qdrant connection, collection, budgets, overlay mode, and retrieval URL remains separate from managed-runtime provider-profile launch settings.
  - Gateway transport is supported and preferred when MoonMind owns outbound retrieval or embedding credentials are not present in the session environment.
  - Direct transport remains available when policy and environment permit it, without becoming the required default contract.
  - Local fallback retrieval is explicit, policy gated, and recorded as degraded behavior rather than silently masquerading as semantic retrieval.
  - Overlay retrieval and the documented top_k, max_context_chars, filter, token-budget, latency-budget, and auto-context settings apply coherently across supported retrieval modes.
- Dependencies: `STORY-001`
- Needs clarification: None

### STORY-005: Record retrieval evidence and enforce trust and secret-handling guardrails
- Short name: `rag-observability-safety`
- Source document: `docs/Rag/WorkflowRag.md`
- Source sections: `9.3 Local fallback flow`, `10.2 Prompt safety`, `12. Managed-session enablement rules`, `13. Observability and evidence`, `14. Security and trust rules`, `15. Runtime rollout`, `17. Recommended desired-state statement`
- Coverage IDs: `DESIGN-REQ-016`, `DESIGN-REQ-018`, `DESIGN-REQ-020`, `DESIGN-REQ-021`, `DESIGN-REQ-022`, `DESIGN-REQ-023`, `DESIGN-REQ-025`
- Why this story exists: The design treats operator trust, debugging evidence, and security boundaries as first-class requirements rather than adapter-specific polish.
- Independent test: Run retrieval in normal, disabled, and degraded/fallback cases and verify durable evidence records the initiation mode, transport, budgets, truncation, filters, artifacts/refs, and degraded reasons, while prompt injection framing and secret-handling rules remain enforced.
- Scope:
  - Capture durable evidence for automatic, session-initiated, and fallback retrieval actions.
  - Enforce prompt-safety framing and current-workspace precedence when retrieved text conflicts with checkout state.
  - Prevent raw secrets, unrestricted authority, or policy violations from entering durable workflow payloads or artifacts.
- Out of scope:
  - Replacing UI rendering work as the primary evidence source.
  - Building a separate runtime-specific security model for each managed runtime.
- Acceptance criteria:
  - Every retrieval operation records durable evidence covering initiation mode, transport, filters, result count, budgets, truncation, artifact/ref location, and degraded reason when applicable.
  - Retrieved text is always delivered with safety framing that marks it as untrusted reference material and prefers current workspace state on conflict.
  - Raw provider keys, OAuth tokens, and secret-bearing config bodies are excluded from durable workflow payloads and retrieval artifacts.
  - Session-issued retrieval remains bounded by authorized corpus scope, filters, budgets, transport policy, provider/secret policy, and audit requirements.
  - The same observability and trust rules apply across the Codex reference implementation and future managed runtimes.
- Dependencies: `STORY-001`, `STORY-002`, `STORY-004`
- Needs clarification: None

## Dependencies

- STORY-001 depends on: None
- STORY-002 depends on: STORY-001
- STORY-003 depends on: STORY-001
- STORY-004 depends on: STORY-001
- STORY-005 depends on: STORY-001, STORY-002, STORY-004

## Out-of-Scope Items

- Full ingest-pipeline design remains out of scope for this breakdown because the source design explicitly excludes it.
- Full managed-session contract, full Provider Profile schema, and raw secret backend behavior remain out of scope because Workflow RAG only defines the document/code retrieval lane.
- Unrestricted direct database administration from managed sessions remains out of scope because the contract requires MoonMind-owned bounded retrieval surfaces instead.
- Mission Control settings UI details remain a separate concern from the retrieval contract.

## Coverage Matrix

- DESIGN-REQ-001 -> `STORY-001`
- DESIGN-REQ-002 -> `STORY-001`
- DESIGN-REQ-003 -> `STORY-002`
- DESIGN-REQ-004 -> `STORY-004`
- DESIGN-REQ-005 -> `STORY-001`, `STORY-003`
- DESIGN-REQ-006 -> `STORY-001`
- DESIGN-REQ-007 -> `STORY-002`
- DESIGN-REQ-008 -> `STORY-001`
- DESIGN-REQ-009 -> `STORY-004`
- DESIGN-REQ-010 -> `STORY-004`
- DESIGN-REQ-011 -> `STORY-001`, `STORY-003`
- DESIGN-REQ-012 -> `STORY-003`
- DESIGN-REQ-013 -> `STORY-003`
- DESIGN-REQ-014 -> `STORY-004`
- DESIGN-REQ-015 -> `STORY-002`
- DESIGN-REQ-016 -> `STORY-004`, `STORY-005`
- DESIGN-REQ-017 -> `STORY-001`, `STORY-003`
- DESIGN-REQ-018 -> `STORY-005`
- DESIGN-REQ-019 -> `STORY-002`, `STORY-004`
- DESIGN-REQ-020 -> `STORY-002`, `STORY-005`
- DESIGN-REQ-021 -> `STORY-005`
- DESIGN-REQ-022 -> `STORY-005`
- DESIGN-REQ-023 -> `STORY-002`, `STORY-003`, `STORY-005`
- DESIGN-REQ-024 -> `STORY-004`
- DESIGN-REQ-025 -> `STORY-001`, `STORY-002`, `STORY-004`, `STORY-005`

## Coverage Gate Result

PASS - every major design point is owned by at least one story.

