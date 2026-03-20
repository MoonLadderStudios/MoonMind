# Tasks: Manifest Schema & Data Pipeline

**Feature**: `088-manifest-schema-pipeline`
**Branch**: `088-manifest-schema-pipeline`
**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

## Phase 1: Setup

- [ ] T001 Create v0 Pydantic models in `moonmind/schemas/manifest_models.py` — `ManifestV0`, `ManifestMetadata`, `EmbeddingsConfig`, `VectorStoreConfig`, `DataSourceConfig`, `IndexConfig`, `RetrieverConfig`, `TransformsConfig`, `EvaluationConfig`, `RunConfig`, `SecurityConfig` (DOC-REQ-001)
- [ ] T002 Generate JSON Schema file at `docs/schemas/manifest-v0.json` from the Pydantic models via `ManifestV0.model_json_schema()` (DOC-REQ-001)
- [ ] T003 [P] Create `ReaderAdapter` protocol class in `moonmind/manifest/reader_adapter.py` with `plan()`, `fetch()`, `state()` methods and a type-string registry (DOC-REQ-008)

## Phase 2: Foundational

- [ ] T004 Create `moonmind/manifest/validator.py` — validate manifest YAML against v0 Pydantic models with schema validation, cross-field semantic checks (dimension ↔ model, auth presence, retriever → index refs), and integration with existing `manifest_contract.py` secret leak detection (DOC-REQ-001, DOC-REQ-002, DOC-REQ-007)
- [ ] T005 [P] Add unit tests for validator in `tests/unit/manifest/test_validator.py` — valid schemas, missing required fields, type mismatches, dimension incompatibility, missing auth, broken index refs, raw secret rejection (DOC-REQ-001, DOC-REQ-002, DOC-REQ-007)
- [ ] T006 [P] Add unit tests for v0 Pydantic models in `tests/unit/schemas/test_manifest_models.py` — model serialization, deserialization, defaults, field constraints (DOC-REQ-001)

## Phase 3: User Story 1 — Validate a manifest YAML (P1)

**Goal**: Operators can validate manifest YAML before submission, catching schema errors, missing auth, and dimension mismatches.
**Independent Test**: Run `moonmind manifest validate -f examples/readers-githubrepositoryreader-example.yaml` and verify exit code and output.

- [ ] T007 [US1] Add `moonmind manifest validate` CLI subcommand in `moonmind/rag/cli.py` that loads, interpolates, and validates a manifest YAML file against the v0 schema + semantic checks (DOC-REQ-004)
- [ ] T008 [US1] Add unit tests for CLI validate command in `tests/unit/manifest/test_cli_manifest.py` — valid input exits 0, invalid input exits non-zero with actionable messages (DOC-REQ-004, DOC-REQ-009)
- [ ] T009 [US1] Create example manifest YAML files under `examples/` — minimal GitHub reader and full kitchen-sink example matching `LlamaIndexManifestSystem.md` examples (DOC-REQ-009)
- [ ] T010 [US1] [P] Add CI validation step in `./tools/test_unit.sh` or a test file that validates all `examples/*.yaml` during test runs (DOC-REQ-009)

## Phase 4: User Story 2 — Run a manifest to index data (P1)

**Goal**: Developers can run a manifest locally to fetch, chunk, embed, and upsert documents to Qdrant.
**Independent Test**: Run `moonmind manifest run -f examples/readers-githubrepositoryreader-example.yaml` against test Qdrant.

- [ ] T011 [US2] Wrap `github_indexer.py` as `GithubRepositoryReader` ReaderAdapter in `moonmind/indexers/github_indexer.py` — implement `plan()`, `fetch()`, `state()` (DOC-REQ-003)
- [ ] T012 [US2] [P] Wrap `google_drive_indexer.py` as `GoogleDriveReader` ReaderAdapter (DOC-REQ-003)
- [ ] T013 [US2] [P] Wrap `local_data_indexer.py` as `SimpleDirectoryReader` ReaderAdapter (DOC-REQ-003)
- [ ] T014 [US2] [P] Wrap `confluence_indexer.py` as `ConfluenceReader` ReaderAdapter (DOC-REQ-003)
- [ ] T015 [US2] Update `moonmind/manifest/runner.py` to use ReaderAdapter registry: look up adapter by `dataSources[].type`, call `fetch()`, apply transforms, embed, and upsert to Qdrant (DOC-REQ-005, DOC-REQ-006)
- [ ] T016 [US2] Add `moonmind manifest plan` CLI subcommand (dry-run: doc count, chunk estimates, no writes) in `moonmind/rag/cli.py` (DOC-REQ-004)
- [ ] T017 [US2] Add `moonmind manifest run` CLI subcommand (full pipeline: fetch → chunk → embed → upsert) in `moonmind/rag/cli.py` (DOC-REQ-004)
- [ ] T018 [US2] Add ReaderAdapter contract tests in `tests/unit/manifest/test_reader_adapter.py` — verify plan/fetch/state interface for each adapter type (DOC-REQ-003, DOC-REQ-008)
- [ ] T019 [US2] [P] Register reader/embed/upsert Activities in `moonmind/workflows/temporal/activity_runtime.py` for Temporal-managed execution (DOC-REQ-005)

## Phase 5: User Story 3 — Evaluate retrieval quality (P2)

**Goal**: Team leads can evaluate manifest retrieval pipelines against golden datasets.
**Independent Test**: Run `moonmind manifest evaluate -f examples/readers-full-example.yaml --dataset smoke`.

- [ ] T020 [US3] Create `moonmind/manifest/evaluation.py` with `hitRate@k` and `ndcg@k` metric implementations (DOC-REQ-006)
- [ ] T021 [US3] Add `moonmind manifest evaluate` CLI subcommand in `moonmind/rag/cli.py` — loads dataset JSONL, runs queries, reports metrics, exits non-zero if thresholds fail (DOC-REQ-004)
- [ ] T022 [US3] Add unit tests for evaluation metrics in `tests/unit/manifest/test_evaluation.py` (DOC-REQ-006)
- [ ] T023 [US3] [P] Create sample evaluation dataset `examples/eval/smoke.jsonl` for CI gating (DOC-REQ-009)

## Phase 6: User Story 4 — Extend with new readers (P3)

**Goal**: Developers can add new reader types via ReaderAdapter without modifying schema or runner.
**Independent Test**: Register a test adapter and verify it works end-to-end.

- [ ] T024 [US4] Document ReaderAdapter extension pattern in `moonmind/manifest/reader_adapter.py` docstrings (DOC-REQ-008)
- [ ] T025 [US4] Add extensibility test: register a mock adapter, validate a manifest referencing it, and run plan (DOC-REQ-008)

## Phase 7: Polish & Cross-Cutting

- [ ] T026 Ensure PII redaction enforcement in validator when `security.piiRedaction: true` (DOC-REQ-007)
- [ ] T027 Ensure metadata allowlist enforcement in validator when `security.allowlistMetadata` is set (DOC-REQ-007)
- [ ] T028 Run full test suite via `./tools/test_unit.sh` and fix any failures
- [ ] T029 Commit all changes to `088-manifest-schema-pipeline` branch

## Dependencies

```text
T001 → T002, T004
T003 → T011–T014, T015
T004 → T007
T007 → T008, T009, T010
T015 → T016, T017
T020 → T021, T022
All → T028 → T029
```

## Parallel Execution Opportunities

- T002, T003 can run in parallel with T001 complete
- T005, T006 can run in parallel once T004 is complete
- T011, T012, T013, T014 can all run in parallel (independent adapters)
- T020, T023 can run in parallel

## Implementation Strategy

**MVP**: Phases 1–3 (schema validation + CLI validate command). Delivers immediate value by catching bad manifests before submission.

**Increment 2**: Phase 4 (run pipeline). Core indexing capability.

**Increment 3**: Phases 5–7 (evaluation + polish). Quality gates and extensibility.

## DOC-REQ Coverage Summary

| DOC-REQ | Implementation Tasks | Validation Tasks |
|---------|---------------------|------------------|
| DOC-REQ-001 | T001, T002, T004 | T005, T006 |
| DOC-REQ-002 | T004 | T005 |
| DOC-REQ-003 | T011, T012, T013, T014 | T018 |
| DOC-REQ-004 | T007, T016, T017, T021 | T008 |
| DOC-REQ-005 | T015, T019 | T018 |
| DOC-REQ-006 | T015, T020 | T022 |
| DOC-REQ-007 | T004, T026, T027 | T005 |
| DOC-REQ-008 | T003, T024 | T025 |
| DOC-REQ-009 | T009, T010, T023 | T010 |
