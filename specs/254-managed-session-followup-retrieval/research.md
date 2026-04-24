# Research: Managed-Session Follow-Up Retrieval

## FR-001 / DESIGN-REQ-019 - Runtime-facing capability signal

Decision: missing; add a runtime-visible capability signal before treating follow-up retrieval as implemented.
Evidence: no capability signal describing retrieval availability, request method, reference-data framing, or budgets was found in `moonmind/agents/codex_worker/handlers.py`, `moonmind/workflows/temporal/activity_runtime.py`, or other managed-runtime preparation paths; `docs/Rag/WorkflowRag.md` §12.2 requires such a signal.
Rationale: The current runtime instruction flow handles initial retrieved context framing, but it does not tell a running managed session that follow-up retrieval is available or how to invoke it.
Alternatives considered: Infer enablement from environment variables alone. Rejected because the source design requires an explicit runtime-facing signal, not an implicit implementation detail.
Test implications: add unit coverage for runtime note composition and at least one integration/runtime-boundary test proving the capability signal reaches the managed runtime surface.

## FR-002 / DESIGN-REQ-003 / DESIGN-REQ-007 - MoonMind-owned follow-up retrieval surface

Decision: partial; reuse the existing retrieval service and gateway rather than inventing a second retrieval path, but add a managed-session-facing invocation surface and restore/replace the worker/session authorization boundary.
Evidence: `api_service/api/routers/retrieval_gateway.py` already exposes `/retrieval/context`; `moonmind/rag/service.py` already supports `direct` and `gateway` transports; worker-token auth in `authorize_retrieval_request()` is intentionally stubbed to `401`, and no managed-session tool/adapter exposure was found.
Rationale: The existing retrieval primitives are the right building blocks, but they are not yet sufficient to prove that a managed session can request more context through MoonMind-owned surfaces during execution.
Alternatives considered: Allow runtime-specific direct Qdrant access. Rejected because the design explicitly prefers a MoonMind-owned tool or gateway surface.
Test implications: add router/service unit tests for authorized success paths and add runtime-boundary verification that managed sessions use the owned surface instead of an unmanaged bypass.

## FR-003 / DESIGN-REQ-015 - Bounded request contract and policy validation

Decision: partial; preserve the current request shape but tighten it as the managed-session contract with explicit validation and policy exposure.
Evidence: `RetrievalQuery` in `api_service/api/routers/retrieval_gateway.py` already includes `query`, `top_k`, `filters`, `overlay_policy`, and `budgets`; `moonmind/rag/service.py` normalizes budgets and enforces token/latency bounds; current tests focus on auth and budget handling rather than the full managed-session contract.
Rationale: The foundational fields match the design, but the repo does not yet prove that session-facing policy validation is explicit and stable enough for a managed runtime contract.
Alternatives considered: Introduce a new request model unrelated to the current gateway. Rejected because that would duplicate the existing retrieval surface instead of hardening it.
Test implications: add unit tests for accepted contract fields, rejected invalid contract fields, and bounded budget overrides; add integration proof that the same contract shape survives the managed-session boundary.

## FR-004 / DESIGN-REQ-025 - Response shape and observability evidence

Decision: implemented_unverified; verify the current `ContextPack` response shape first, then change code only if that proof fails.
Evidence: `moonmind/rag/context_pack.py` already defines `ContextPack` with `context_text`, `items`, `filters`, `budgets`, `usage`, `transport`, `retrieved_at`, and `telemetry_id`; `api_service/api/routers/retrieval_gateway.py` returns `pack.to_dict()`; the current router tests do not include a success-path assertion for that response.
Rationale: The likely response contract already exists, but the required proof for managed-session follow-up retrieval is missing.
Alternatives considered: Preemptively redesign `ContextPack`. Rejected because the existing shape already matches the source brief closely and should be verified before it is changed.
Test implications: add unit tests for successful retrieval responses and a focused runtime-boundary test that a managed session receives machine-readable and text outputs together.

## FR-005 / DESIGN-REQ-020 - Disabled and invalid follow-up retrieval must fail fast

Decision: partial; extend existing low-level fail-fast behavior into a stable managed-session contract.
Evidence: `tests/unit/api/routers/test_retrieval_gateway.py` verifies unauthenticated and worker-token requests fail fast; `moonmind/rag/service.py` fails for missing gateway URL and exceeded budgets; the runtime contract does not yet expose a deterministic disabled/not-enabled denial for follow-up retrieval requests.
Rationale: The low-level primitives already fail fast in some cases, but the story requires an explicit session-facing disabled/unsupported response rather than implicit lower-level errors.
Alternatives considered: Allow disabled follow-up retrieval to degrade silently to no-op or local search. Rejected because the source design explicitly requires a clear reason when retrieval is disabled.
Test implications: add unit tests for disabled and invalid request cases and integration proof that the runtime receives a stable denial reason.

## FR-006 / DESIGN-REQ-023 - Runtime-neutral contract across Codex and future runtimes

Decision: partial; keep the contract runtime-neutral and verify it first at one managed runtime boundary without baking in Codex-only semantics.
Evidence: retrieval primitives are runtime-neutral in `moonmind/rag/service.py` and `moonmind/rag/context_pack.py`; existing managed-runtime tests center on initial retrieval and Codex instruction composition rather than explicit follow-up retrieval reuse by multiple runtimes.
Rationale: The right approach is to define a stable runtime-neutral contract now, prove it at the first managed runtime boundary, and preserve it for later adopters rather than proliferating runtime-specific variants.
Alternatives considered: Limit MM-506 to Codex-specific follow-up retrieval behavior. Rejected because the spec and source design intentionally keep the concept runtime-neutral.
Test implications: add unit tests that assert runtime-neutral contract content and at least one boundary test that avoids Codex-only hardcoding in externally visible behavior.

## FR-007 - Traceability and MM-506 preservation

Decision: implemented_verified.
Evidence: `spec.md` preserves the original preset brief and MM-506; the new `plan.md`, `research.md`, `data-model.md`, `quickstart.md`, and `contracts/managed-session-followup-retrieval-contract.md` retain the issue key.
Rationale: Planning artifacts themselves satisfy the traceability requirement at this phase.
Alternatives considered: None.
Test implications: traceability review only.

## Test Strategy

Decision: use verification-first planning with explicit unit and integration lanes.
Evidence: the current repository already contains retrieval service, context injection, gateway router, and managed-runtime boundary tests that can be extended without inventing a new test harness; `AGENTS.md` requires unit and integration strategies to be explicit.
Rationale: Much of MM-506 exists as lower-level infrastructure already. The safest approach is to add failing verification tests at the router and runtime boundary first, then implement only where those tests expose real gaps.
Alternatives considered: implementation-first planning. Rejected because it would risk changing already-correct retrieval primitives without proving the contract gap first.
Test implications:
- Unit: extend `tests/unit/api/routers/test_retrieval_gateway.py`, `tests/unit/rag/test_service.py`, `tests/unit/rag/test_context_injection.py`, `tests/unit/agents/codex_worker/test_handlers.py`, `tests/unit/workflows/temporal/test_agent_runtime_activities.py`, and `tests/unit/services/temporal/runtime/test_launcher.py`.
- Integration: add or extend a focused boundary test under `tests/integration/workflows/temporal/` to prove session-facing capability signalling, MoonMind-owned retrieval routing, and fail-fast disabled behavior when unit tests are insufficient.

## Planning Tooling Constraint

Decision: generate planning artifacts manually in the active feature directory.
Evidence: the skill’s documented setup helper path `scripts/bash/setup-plan.sh --json` does not exist in this repository, and no `update-agent-context` helper was found either.
Rationale: The active feature directory is already known from `.specify/feature.json`, so manual artifact generation is sufficient and avoids introducing repo changes unrelated to MM-506.
Alternatives considered: stop the planning run or fabricate helper behavior. Rejected because neither is necessary.
Test implications: none beyond documenting the constraint.
