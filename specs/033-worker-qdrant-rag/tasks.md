# Tasks: Direct Worker Qdrant Retrieval Loop

**Input**: Design documents from `/specs/033-worker-qdrant-rag/`
**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, quickstart.md, contracts/

## Phase 1: Setup (Shared Infrastructure)

- [ ] T001 Add `qdrant-client`, `google-generativeai`, `httpx`, and Typer extras plus the `moonmind` console entry in `pyproject.toml` and regenerate `poetry.lock` so the CLI can vend rag + overlay features.
- [ ] T002 Update `.env-template`, `.env.vllm-template`, and `config.toml` with Qdrant, RetrievalGateway, overlay, and budget variables referenced in the plan so workers can configure transports consistently.
- [ ] T003 Create the `moonmind/rag/__init__.py` package and register a placeholder Typer group in `moonmind/cli.py` + `moonmind/rag/cli.py` to expose `moonmind rag` commands without runtime logic yet.

## Phase 2: Foundational (Blocking Prerequisites)

- [ ] T004 Implement `RagRuntimeSettings` + filter metadata helpers in `moonmind/rag/settings.py` to normalize env vars, transport selection, overlay policy, and budget inputs for every retrieval call.
- [ ] T005 Build `ContextPack` and `ContextItem` dataclasses plus markdown + JSON serialization helpers in `moonmind/rag/context_pack.py` following `contracts/context-pack.md`.
- [ ] T006 Create `ContextRetrievalService` scaffolding in `moonmind/rag/service.py` that wires settings, embedding adapters, overlay merge hooks, and telemetry placeholders for later story work.
- [ ] T007 Add `moonmind/rag/telemetry.py` helpers that wrap `moonmind/workflows/orchestrator/metrics.py` and queue events so later stages can emit `embedding_*` and `rag.search.*` counters deterministically.

## Phase 3: User Story 1 – Worker pulls repo context via CLI (Priority: P1) 🎯 MVP

**Goal**: Workers run `moonmind rag search` to embed queries locally, call Qdrant directly, and receive markdown + JSON context packs without hitting another LLM.

**Independent Test**: With valid Qdrant + embedding credentials, run `moonmind rag search --query "Update API auth"` and verify stdout contains the formatted context block, stderr stays quiet, and a JSON payload with `context_text` + `items[]` is written when `--output-file` is provided.

- [ ] T008 [P] [US1] Implement provider-agnostic embedding adapter in `moonmind/rag/embedding.py` (Gemini/OpenAI/Ollama) that validates credentials, reports vector dimensions, and surfaces actionable errors.
- [ ] T009 [US1] Build direct Qdrant client + overlay-aware search helpers in `moonmind/rag/qdrant_client.py`, including payload filters, deterministic ordering, and `top_k` enforcement.
- [ ] T010 [US1] Complete `ContextRetrievalService.retrieve()` in `moonmind/rag/service.py` to run embed → search → context pack generation while honoring overlay policy and budgets.
- [ ] T011 [US1] Implement the `moonmind rag search` Typer command in `moonmind/rag/cli.py` (flag parsing for `--query/--filter/--overlay/--transport/--budget/--output-file`) and ensure markdown goes to stdout while JSON persists via `ContextPack` utilities.
- [ ] T012 [US1] Update `moonmind/agents/codex_worker/worker.py` to advertise `job.capabilities.rag`, capture retrieval metadata per run, and expose the CLI command through worker doctor UX.
- [ ] T013 [US1] Add CLI + context pack unit tests in `tests/unit/moonmind/rag/test_cli.py` and `tests/unit/moonmind/rag/test_context_pack.py` covering filter serialization, overlay flags, and deterministic markdown output.

## Phase 4: User Story 2 – Guardrails block unsafe retrievals (Priority: P1)

**Goal**: Prevent unsafe vector operations by validating credentials, connectivity, and collection schemas while emitting observability signals when failures occur.

**Independent Test**: Run `moonmind rag search` with an embedding dimension mismatch and confirm it halts before querying Qdrant, prints expected vs actual dimensions, and exits non-zero with remediation guidance.

- [ ] T014 [US2] Implement guardrail routines in `moonmind/rag/guardrails.py` to check embedding credentials, Qdrant reachability, and collection dimensions before executing searches.
- [ ] T015 [US2] Integrate guardrails into CLI startup and `moonmind worker doctor` within `moonmind/rag/cli.py` and `moonmind/agents/codex_worker/worker.py`, ensuring actionable remediation text and exit codes propagate.
- [ ] T016 [US2] Wire telemetry + queue events in `moonmind/rag/telemetry.py` and call them from `ContextRetrievalService` so `embedding_*`, `qdrant_query_*`, and `rag.search.hits` metrics include job/run IDs.
- [ ] T017 [US2] Add retry/backoff + error classification layers in `moonmind/rag/qdrant_client.py` and `moonmind/rag/service.py` so transient Qdrant/gateway failures respect budget ceilings and raise typed CLI errors.
- [ ] T018 [US2] Create guardrail + telemetry unit tests in `tests/unit/moonmind/rag/test_guardrails.py` to cover missing credentials, dimension drift, StatsD routing, and retry exhaustion paths.

## Phase 5: User Story 3 – RetrievalGateway fallback (Priority: P2)

**Goal**: Offer a retrieval-only FastAPI gateway that enforces repo/tenant policies yet returns the same ContextPack contract as direct Qdrant access.

**Independent Test**: Set `MOONMIND_RETRIEVAL_URL` and run `moonmind rag search --transport gateway`; verify the CLI issues a single POST to `/retrieval/context`, receives a ContextPack with identical hits, and observes latency budget enforcement.

- [ ] T019 [US3] Implement FastAPI router `api_service/api/routers/retrieval_gateway.py` with Pydantic models that expose `POST /retrieval/context` + `GET /retrieval/health` per the contract.
- [ ] T020 [P] [US3] Register the router with the API service (`api_service/api/main.py`), add env wiring + secrets to `docker-compose.yaml`/`docker-compose.test.yaml`, and document new vars in `config.toml`.
- [ ] T021 [US3] Extend `ContextRetrievalService` with `_retrieve_via_gateway` using `httpx` (timeouts, budget headers) and allow CLI transport overrides with graceful fallback when direct credentials exist.
- [ ] T022 [US3] Enforce repo/tenant allow-lists + auth scopes inside the gateway using existing security dependencies (e.g., `api_service/api/deps/security.py`) and return structured error envelopes.
- [ ] T023 [US3] Add parity + budget tests for the gateway and CLI in `tests/unit/moonmind/rag/test_gateway.py` and `tests/unit/api/test_retrieval_gateway.py`, asserting identical ContextPack payloads for direct vs gateway paths.

## Phase 6: User Story 4 – Workspace overlay keeps context fresh (Priority: P2)

**Goal**: Allow workers to upsert run-scoped overlays and merge them deterministically with canonical Qdrant data so retrieval includes the latest edits.

**Independent Test**: Modify a local file, run `moonmind rag overlay upsert path/to/file.py`, then `moonmind rag search --overlay include` and confirm overlay chunks appear ahead of canonical ones referencing older text.

- [ ] T024 [P] [US4] Implement overlay chunker + upsert helpers in `moonmind/rag/overlay.py` that compute `(path, chunk_hash)` dedupe keys, include TTL payloads, and target run-specific collections.
- [ ] T025 [US4] Add `moonmind rag overlay upsert/clean` commands in `moonmind/rag/cli.py` with automatic run_id detection, optional `--collection`, and structured output for Codex logs.
- [ ] T026 [US4] Merge overlay + canonical hits inside `moonmind/rag/qdrant_client.py`/`moonmind/rag/service.py`, honoring `--overlay include|skip`, TTL expiry, and precedence rules.
- [ ] T027 [US4] Hook Codex worker lifecycle (`moonmind/agents/codex_worker/worker.py`) to upsert touched files and invoke overlay cleanup at job completion, emitting telemetry for each action.
- [ ] T028 [US4] Write overlay + merge tests in `tests/unit/moonmind/rag/test_overlay.py` and `tests/unit/moonmind/rag/test_service.py` covering chunk hashing, TTL expiry, precedence, and cleanup commands.

## Phase 7: Polish & Cross-Cutting Concerns

- [ ] T029 Update `docs/WorkerRag.md`, `docs/WorkerVectorEmbedding.md`, and `docs/MemoryArchitecture.md` with the guardrails, transports, telemetry events, and overlay lifecycle.
- [ ] T030 Refresh `specs/033-worker-qdrant-rag/quickstart.md`, `docs/WorkerVectorEmbedding.md`, and `contracts/context-pack.md` examples with the final CLI flags, budgets, and error envelope samples.
- [ ] T031 Add release notes/README snippets (e.g., `README.md` and `docs/WorkerRag.md` changelog sections) announcing the new CLI commands, gateway fallback, and overlay workflow for Codex workers.
- [ ] T032 Run `./tools/test_unit.sh` (and document command in `quickstart.md`) to validate CLI, guardrail, gateway, and overlay suites before handing off for integration tests.

## Dependencies & Execution Order

- Phase 1 must finish before any runtime modules compile because dependencies, env vars, and CLI scaffolding gate every other task.
- Phase 2 establishes shared settings/service/telemetry scaffolding; User Stories 1–4 may not start until T004–T007 complete.
- User Story phases can run sequentially by priority (US1 → US2 → US3 → US4) or in parallel once their prerequisites are met, but each story remains independently testable.
- Phase 7 polish tasks wait until the targeted runtime stories are code-complete so docs and final validation reflect actual behavior.

## Parallel Opportunities

- During Setup, T001–T003 can run concurrently because they touch different files (dependencies vs env vs CLI scaffolding).
- Within User Story 1, T008 and T009 may proceed in parallel, followed by T010–T012 once embedding/Qdrant helpers exist; T013 runs independently after CLI wiring.
- User Story 3 tasks T019–T022 can split between API (router + auth) and CLI (transport logic) engineers, while T023 validates both paths once code stabilizes.
- Overlay tasks T024–T027 split naturally between CLI (commands) and worker automation, with T028 executed in parallel as soon as APIs land.

## Implementation Strategy

1. Complete Phase 1–2 foundations so all transports share one `ContextRetrievalService` + `ContextPack` contract.
2. Deliver User Story 1 as the MVP to unblock the direct worker ↔ Qdrant path; ship as soon as T008–T013 pass tests to provide immediate value.
3. Layer guardrails (US2) to protect production runs, then decide whether regulated environments require the RetrievalGateway (US3) before or after overlays (US4) based on deployment pressure.
4. Finish with documentation, release comms, and a full `./tools/test_unit.sh` run (Phase 7) before requesting Spec Kit verification or integration tests.
