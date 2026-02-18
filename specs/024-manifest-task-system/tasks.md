# Tasks: Manifest Task System Documentation

**Input**: Design documents from `/specs/024-manifest-task-system/`  
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Establish documentation workspace and gather references needed for the Manifest Task System write-up.

- [X] T001 Capture reference excerpts from `docs/LlamaIndexManifestSystem.md` and `docs/WorkerVectorEmbedding.md` to ensure terminology alignment for manifests and worker embeddings.
- [X] T002 Create initial stub for `docs/ManifestTaskSystem.md` with front matter (status, owners, last updated) and Purpose/Background sections.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Define architecture skeleton and shared artifacts referenced by every user story.

- [X] T003 Add the high-level architecture overview (control plane vs data plane narrative) plus Mermaid diagram covering UI, API, Postgres, worker, embeddings, and Qdrant inside `docs/ManifestTaskSystem.md`.
- [X] T004 Document the key concept definitions (Manifest v0, Manifest Run, Manifest Worker, Control Plane vs Data Plane) so later sections can reference consistent terminology.
- [X] T005 Outline the Goals, Non-Goals, and namespaced section structure (Queue Job Type, Execution Engine, Worker, UI, Security, Delivery Plan) for the remainder of `docs/ManifestTaskSystem.md`.

**Checkpoint**: Architecture scaffold exists; user story phases can now fill detailed content.

---

## Phase 3: User Story 1 - Queue-Aligned Manifest Runs (Priority: P1) 🎯 MVP

**Goal**: Describe how manifests submit through Agent Queue, including payload contract, manifest source kinds, actions, and capability derivation so engineers can operate the system end-to-end.

**Independent Test**: A platform engineer can craft and submit a manifest queue job using only this documentation.

### Implementation for User Story 1

- [X] T006 [US1] Write the Queue Job Type section covering the new `type="manifest"`, worker capability filtering, and statement that codex/gemini/claude workers do not claim it.
- [X] T007 [US1] Document the canonical `ManifestJobPayload` JSON structure, including `requiredCapabilities` requirements and sample payload referencing inline manifest content.
- [X] T008 [US1] Detail manifest source kinds (`inline`, `registry`, `path`, `repo`) and explicitly mark inline + path as Phase 1 scope inside `docs/ManifestTaskSystem.md`.
- [X] T009 [US1] Describe manifest actions (`plan`, `run`, `evaluate`) plus worker expectations for each, clarifying that `evaluate` is future scope.
- [X] T010 [US1] Capture capability derivation logic (vector store, embedding provider, source readers) so API job creation enforces token-free payloads and correct worker selection.

**Checkpoint**: Manifest run submission path is fully documented.

---

## Phase 4: User Story 2 - Worker Implementation Guide (Priority: P2)

**Goal**: Provide a prescriptive blueprint for the `moonmind-manifest-worker`, including execution engine layout, stage events, artifacts, and environment configuration.

**Independent Test**: A developer can implement the worker and execution engine using only this doc.

### Implementation for User Story 2

- [X] T011 [US2] Author the Manifest Execution Engine section describing package layout (`moonmind/manifest_v0/*`), reader adapters, transforms, embeddings, and Qdrant behaviors.
- [X] T012 [US2] Specify the ReaderAdapter protocol, initial adapters (GitHub, Google Drive, SimpleDirectory), and transform options (htmlToText, splitter, enrichMetadata) within the doc.
- [X] T013 [US2] Document embedding provider expectations (OpenAI, Google, Ollama) and Qdrant vector store rules including collection provisioning and metadata allowlists.
- [X] T014 [US2] Detail the manifest worker service (entry command, module path, env vars, worker capability advertisement) in Section 9 of `docs/ManifestTaskSystem.md`.
- [X] T015 [US2] Enumerate stage events (`moonmind.manifest.validate` ... `finalize`), artifact uploads (logs, manifest input/resolved, reports), and cancellation handling.

**Checkpoint**: Worker teams have a clear blueprint for implementation and observability.

---

## Phase 5: User Story 3 - Tasks Dashboard & Security Visibility (Priority: P3)

**Goal**: Show how manifest runs appear in the Tasks Dashboard and enforce a security model that prevents raw secrets in queue payloads, logs, and artifacts.

**Independent Test**: A designer can mock the new category/submit flow and a security reviewer can audit payload/secret handling using this doc.

### Implementation for User Story 3

- [X] T016 [US3] Create the Tasks Dashboard section covering the new "Manifests" category, submit form fields, and detail view expectations referencing SSE event streams.
- [X] T017 [US3] Describe the manifest registry CRUD endpoints (`GET/PUT/POST /api/manifests...`) and how submission returns queue job IDs for UI linking.
- [X] T018 [US3] Write the security model detailing token-free payload rules, env-variable fast path, future Vault references, and logging/artifact redaction requirements.

**Checkpoint**: UI and security reviewers understand their responsibilities and constraints.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Finalize documentation polish and ensure references/future work are covered.

- [X] T019 Update Related Documents list to link `docs/LlamaIndexManifestSystem.md`, `docs/TaskArchitecture.md`, `docs/TaskUiArchitecture.md`, and `docs/WorkerVectorEmbedding.md`.
- [X] T020 Add Delivery Plan outlining Phase 1 (queue/job/worker/UI), Phase 2 (registry + secrets), and Phase 3 (adapter/eval expansion) plus ensure Goals/Non-Goals are accurate.
- [X] T021 Proofread entire doc for clarity, consistency, and confirm no raw secrets/examples violate policies.

---

## Dependencies & Execution Order

- **Setup (Phase 1)** → **Foundational (Phase 2)** → **US1 (Phase 3)** → **US2 (Phase 4)** → **US3 (Phase 5)** → **Polish (Phase 6)**.
- US1 must complete before US2 since worker content references queue payload decisions; US3 depends on both for context.
- Parallel work: individual tasks within a phase touching different subsections of `docs/ManifestTaskSystem.md` can run in parallel once preceding dependencies are met.

## Parallel Opportunities

- Post-foundational, US2 tasks T011–T015 and US3 tasks T016–T018 can run concurrently with careful doc merge coordination because they operate on distinct sections.
- Setup T001 and T002 may proceed simultaneously once references are identified.

## MVP Recommendation

- Deliver through Phase 3 (US1) to unlock immediate manifest queue submissions while subsequent phases mature worker and UI/security guidance.
