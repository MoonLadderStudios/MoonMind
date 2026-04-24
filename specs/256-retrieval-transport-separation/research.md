# Research: Retrieval Transport and Configuration Separation

## FR-001 / DESIGN-REQ-004 / DESIGN-REQ-019 - Retrieval settings stay separate from runtime provider profiles

Decision: partial; preserve the existing retrieval-settings path in `moonmind/rag/settings.py`, but add boundary proof that execution creation, runtime launch, and provider-profile resolution do not silently turn runtime profiles into the source of retrieval configuration.
Evidence: `moonmind/rag/settings.py` resolves embedding provider/model, collection, retrieval URL, overlay settings, and budgets from environment or app settings; `api_service/api/routers/provider_profiles.py` models runtime launch profile state separately; `api_service/api/routers/executions.py` loads provider profiles for runtime launch and model resolution, not for retrieval configuration.
Rationale: The code already suggests a clean separation, but MM-508 needs explicit proof that retrieval settings remain independent when a runtime profile is present.
Alternatives considered: treat the current separation as fully complete without new tests. Rejected because the story is specifically about defending this boundary, not assuming it from file layout.
Test implications: add unit and integration coverage around execution creation, launcher behavior, and retrieval settings resolution.

## FR-002 / DESIGN-REQ-009 / DESIGN-REQ-024 - Gateway is preferred when MoonMind owns outbound retrieval or runtime embedding creds are absent

Decision: partial; keep the existing gateway-preference logic, then add verification that the preference is reachable and policy-safe for managed runtimes.
Evidence: `RagRuntimeSettings.resolved_transport()` in `moonmind/rag/settings.py` prefers `gateway` when `MOONMIND_RETRIEVAL_URL` exists; `ContextRetrievalService._retrieve_via_gateway()` in `moonmind/rag/service.py` supports gateway retrieval; `api_service/api/routers/retrieval_gateway.py` exposes the gateway surface, but worker-token auth currently returns `401` with a temporary-unavailable message.
Rationale: The preference logic exists, but the managed-runtime path needs verification and likely gateway-auth hardening before MM-508 can be considered satisfied.
Alternatives considered: weaken the requirement to “gateway supported but not preferred.” Rejected because the source brief explicitly makes gateway the preferred path under the documented conditions.
Test implications: add gateway-preference tests and, if they fail, implement the minimum auth or runtime-boundary changes needed to make the preferred path defensible.

## FR-003 / DESIGN-REQ-010 - Direct transport remains available when policy and environment permit

Decision: implemented_unverified; verify the existing direct path first and avoid code changes unless the tests show a regression.
Evidence: `moonmind/rag/settings.py` resolves `direct` when no gateway URL is present and provider-specific credentials exist; `moonmind/rag/service.py` embeds directly and queries Qdrant; unit tests already exist for `moonmind/rag/test_settings.py` and `moonmind/rag/test_service.py`.
Rationale: The direct path looks present and coherent already; MM-508 mainly needs explicit proof that this remains a supported path rather than an accidental fallback.
Alternatives considered: rework transport resolution immediately. Rejected because the existing logic appears aligned and should be verified before it is changed.
Test implications: add or extend tests for direct transport selection and execution.

## FR-004 / DESIGN-REQ-014 - Local fallback is explicit, policy gated, and degraded

Decision: partial; preserve the current local fallback structure, but strengthen proof that fallback is only entered for allowed degraded reasons and is always labeled as `local_fallback` rather than semantic retrieval.
Evidence: `moonmind/rag/context_injection.py` uses `_LOCAL_FALLBACK_ALLOWED_SKIP_REASONS`, `_should_use_local_fallback()`, and `transport="local_fallback"`; retrieved context is wrapped in untrusted-reference framing and compact metadata records the selected transport.
Rationale: Most of the required behavior exists, but MM-508 needs stronger assurance that degraded fallback remains explicit at the user-visible and runtime-visible boundary.
Alternatives considered: remove local fallback entirely. Rejected because the source brief keeps it in scope as explicit degraded behavior.
Test implications: add unit coverage for allowed/blocked fallback reasons and runtime metadata expectations.

## FR-005 / DESIGN-REQ-016 / DESIGN-REQ-025 - Overlay and budget knobs remain coherent across supported transports

Decision: partial; keep the shared knob model in `RagRuntimeSettings` and the retrieval gateway request schema, then add cross-transport proof for consistency.
Evidence: `moonmind/rag/settings.py` resolves top-k, max-context, overlay mode, and budgets; `moonmind/rag/service.py` applies those values to direct and gateway retrieval; `api_service/api/routers/retrieval_gateway.py` validates supported budget keys and forwards overlay, filters, and budgets.
Rationale: The same knob names are already present across transport paths, but MM-508 needs tests that prove no path drifts from the shared contract.
Alternatives considered: introduce transport-specific settings. Rejected because the design document and Jira brief explicitly require a shared Workflow RAG contract.
Test implications: add tests for direct versus gateway knob propagation and compact retrieval metadata.

## FR-006 / DESIGN-REQ-025 - Retrieval ownership stays in the shared Workflow RAG contract

Decision: partial; verify retrieval transport and configuration remain MoonMind-owned concerns at execution and runtime boundaries rather than profile-specific semantics.
Evidence: provider profiles travel through execution creation and runtime launch as `profileId`, `credential_source`, and runtime materialization data, while retrieval metadata is recorded separately under `parameters.metadata.moonmind` in `moonmind/rag/context_injection.py`.
Rationale: The separation is conceptually present, but no targeted regression test currently guards against future profile-driven retrieval coupling.
Alternatives considered: rely on current architecture docs only. Rejected because the story is about turning that architecture into enforced runtime behavior.
Test implications: add execution/router and launcher tests that prove profile metadata and retrieval metadata remain distinct.

## FR-007 - Traceability and MM-508 preservation

Decision: implemented_verified.
Evidence: `spec.md` preserves the original MM-508 preset brief and issue key; planning artifacts continue that traceability.
Rationale: Artifact traceability is already satisfied at the planning stage.
Alternatives considered: None.
Test implications: traceability review only.

## Test Strategy

Decision: use verification-first planning with separate unit and integration lanes.
Evidence: the repo already has focused retrieval settings, service, gateway, context-injection, launcher, and workflow activity tests; `AGENTS.md` requires explicit unit and integration strategies for workflow and runtime boundaries.
Rationale: Much of MM-508 appears partly implemented already. The safest path is to add failing verification tests for transport preference, fallback degradation, and retrieval-versus-profile ownership before making code changes.
Alternatives considered: implementation-first planning. Rejected because it risks rewriting already-correct settings and transport behavior without proving the actual gap.
Test implications:
- Unit: extend `tests/unit/rag/test_settings.py`, `tests/unit/rag/test_service.py`, `tests/unit/rag/test_context_injection.py`, `tests/unit/api/routers/test_retrieval_gateway.py`, `tests/unit/services/temporal/runtime/test_launcher.py`, `tests/unit/workflows/temporal/test_agent_runtime_activities.py`, and `tests/unit/api_service/api/routers/test_provider_profiles.py` as needed.
- Integration: extend `tests/integration/workflows/temporal/test_managed_session_retrieval_context.py` or add a focused retrieval-transport scenario under `tests/integration/workflows/temporal/` to prove transport resolution and knob propagation at the managed-runtime boundary.

## Planning Tooling Constraint

Decision: generate planning artifacts manually in the active feature directory.
Evidence: the helper paths described by the MoonSpec skill exist under `.specify/scripts/bash/`, but the current repository branch is not a numbered feature branch, so the scripts require an explicit `SPECIFY_FEATURE` override rather than direct branch-based execution.
Rationale: `.specify/feature.json` can already point to the active feature directory, so manual artifact generation is sufficient and avoids unnecessary branch switching.
Alternatives considered: stop the planning run. Rejected because the required inputs and templates are already available.
Test implications: none beyond documenting the constraint.
