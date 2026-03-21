# Feature Specification: Manifest Schema & Data Pipeline

**Feature Branch**: `088-manifest-schema-pipeline`
**Created**: 2026-03-20  
**Status**: Draft  
**Source Document**: `docs/RAG/LlamaIndexManifestSystem.md`  
**Input**: Implement the v0 manifest YAML schema validation, LlamaIndex reader/indexer pipeline, Qdrant upsert Activities, and evaluation framework. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests.
**Implementation Intent**: Runtime implementation. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests.

## Source Document Requirements

| DOC-REQ ID | Source Reference | Requirement Summary |
| --- | --- | --- |
| DOC-REQ-001 | `LlamaIndexManifestSystem.md` §3 (Schema) | Manifests MUST conform to the v0 JSON Schema with required fields: `version`, `metadata`, `embeddings`, `vectorStore`, `dataSources`, `indices`, `retrievers`. |
| DOC-REQ-002 | `LlamaIndexManifestSystem.md` §3 (Compatibility) | Validator MUST enforce vector dimension ↔ embedding model compatibility, auth presence when required, and retriever → index reference integrity. |
| DOC-REQ-003 | `LlamaIndexManifestSystem.md` §4 (Examples) | System MUST support `GithubRepositoryReader`, `GoogleDriveReader`, `SimpleDirectoryReader`, and `ConfluenceReader` data source types with documented `params` and `auth` schemas. |
| DOC-REQ-004 | `LlamaIndexManifestSystem.md` §5 (CLI) | CLI MUST provide `moonmind manifest validate`, `plan`, `run`, and `evaluate` commands. |
| DOC-REQ-005 | `LlamaIndexManifestSystem.md` §6 (Orchestration) | Manifest execution via `MoonMind.ManifestIngest` MUST follow the pipeline: read → parse → validate → compile → fan-out → aggregate → write summary/index artifacts. |
| DOC-REQ-006 | `LlamaIndexManifestSystem.md` §7 (Performance) | Chunking, batching, hybrid retrieval, and reranker parameters MUST be configurable via manifest YAML. |
| DOC-REQ-007 | `LlamaIndexManifestSystem.md` §8 (Security) | Secrets MUST be referenced via `${ENV}` only; `security.piiRedaction` MUST be enforced before embedding; metadata allowlists MUST restrict index-time fields. |
| DOC-REQ-008 | `LlamaIndexManifestSystem.md` §10 (Extending) | New readers MUST implement the `ReaderAdapter` interface with `plan()`, `fetch()`, and `state()` methods. |
| DOC-REQ-009 | `LlamaIndexManifestSystem.md` §11 (Testing) | CI MUST validate all YAML under `examples/` and plan them (no writes) on PRs; fail if invalid or thresholds regress. |

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Validate a manifest YAML (Priority: P1)

As an operator, I can validate a manifest YAML file before submission so that schema errors, missing auth, and dimension mismatches are caught early.

**Why this priority**: Validation is the entry point for all manifest operations; without it, invalid manifests would fail at runtime with poor diagnostics.

**Independent Test**: Run `moonmind manifest validate -f examples/readers-githubrepositoryreader-example.yaml` and verify it passes. Run against a deliberately broken YAML and verify it fails with actionable error messages.

**Acceptance Scenarios**:

1. **Given** a valid v0 manifest YAML, **When** `moonmind manifest validate` is run, **Then** it exits 0 with no errors.
2. **Given** a manifest missing required fields (e.g., no `embeddings`), **When** validation runs, **Then** it exits non-zero with a clear error identifying the missing field.
3. **Given** a manifest with embedding dimension ↔ vector store mismatch, **When** validation runs, **Then** it reports the incompatibility with remediation guidance.

---

### User Story 2 - Run a manifest locally to index data (Priority: P1)

As a developer, I can run a manifest locally to fetch documents via LlamaIndex readers, chunk/transform them, embed them, and upsert vectors to Qdrant.

**Why this priority**: Local execution is the primary development workflow and the foundation for Temporal-managed execution.

**Independent Test**: Run `moonmind manifest run -f examples/readers-githubrepositoryreader-example.yaml` against a test Qdrant instance and verify documents are indexed.

**Acceptance Scenarios**:

1. **Given** a manifest with a `GithubRepositoryReader` data source, **When** `moonmind manifest run` executes, **Then** the system fetches matching files, chunks them per the `transforms.splitter` config, embeds them, and upserts to the configured Qdrant collection.
2. **Given** a manifest with `run.dryRun: true`, **When** the `moonmind manifest plan` command runs, **Then** it outputs estimated doc counts, chunk counts, and token/cost approximations without writing to the vector store.
3. **Given** a manifest with multiple data sources, **When** execution runs with `run.concurrency: 6`, **Then** sources are processed with the specified parallelism, bounded by configurable limits (default 50, hard cap 500).

---

### User Story 3 - Evaluate retrieval quality (Priority: P2)

As a team lead, I can evaluate a manifest's retrieval pipeline against a golden dataset to verify hit rate and NDCG thresholds before deploying.

**Why this priority**: Without evaluation, there is no quantitative signal on retrieval quality.

**Independent Test**: Run `moonmind manifest evaluate -f examples/readers-full-example.yaml --dataset smoke` and verify metrics are computed and thresholds are checked.

**Acceptance Scenarios**:

1. **Given** a manifest with an `evaluation` block and a golden JSONL dataset, **When** evaluation runs, **Then** it reports `hitRate@k` and `ndcg@k` scores.
2. **Given** evaluation metrics with a `threshold` specified, **When** scores fall below the threshold, **Then** the command exits non-zero for CI gating.

---

### User Story 4 - Extend the system with a new reader (Priority: P3)

As a developer, I can add a new reader type (e.g., `ConfluenceReader`) by implementing the `ReaderAdapter` interface without modifying the manifest schema or runner core.

**Why this priority**: Extensibility is a key design goal but not required for initial launch.

**Acceptance Scenarios**:

1. **Given** a new reader implementing `plan()`, `fetch()`, and `state()`, **When** it is registered with the manifest runner, **Then** manifests specifying its `type` can validate, plan, and run using the new reader.

### Edge Cases

- Manifest YAML contains raw secret material (e.g., `githubToken: "ghp_..."`); validation must reject before submission per secret leak detection rules.
- Reader auth credentials are expired or revoked; the Activity must fail with actionable diagnostics.
- Qdrant collection does not exist; the pipeline must create it with correct dimensions or fail with guidance.
- Embedding provider quota exhausted mid-run; the pipeline must surface the quota error and support resumption.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST validate manifest YAML against the v0 JSON Schema, enforcing required fields, type constraints, and cross-field compatibility (dimension ↔ model, auth presence, retriever → index refs). (Maps: DOC-REQ-001, DOC-REQ-002)
- **FR-002**: The system MUST support `GithubRepositoryReader`, `GoogleDriveReader`, `SimpleDirectoryReader`, and `ConfluenceReader` data source types with their documented `params` and `auth` schemas. (Maps: DOC-REQ-003)
- **FR-003**: The system MUST provide CLI commands: `moonmind manifest validate`, `plan` (dry-run estimation without writes), `run` (full pipeline), and `evaluate`. (Maps: DOC-REQ-004)
- **FR-004**: The manifest run pipeline MUST execute: reader fetch → transform/chunk → embed → Qdrant upsert, with configurable parallelism (default 50, hard cap 500) and error policy (`continue` or `stopOnFirstError`). (Maps: DOC-REQ-005, DOC-REQ-006)
- **FR-005**: Security enforcement MUST reject raw secrets in manifest values, enforce PII redaction when configured, and restrict index-time metadata to allowlisted fields. (Maps: DOC-REQ-007)
- **FR-006**: New reader types MUST be addable via the `ReaderAdapter` interface (`plan()`, `fetch()`, `state()`) without modifying the manifest schema. (Maps: DOC-REQ-008)
- **FR-007**: CI MUST validate all example YAML files and plan them (no writes) on PRs, failing if schemas are invalid or evaluation thresholds regress. (Maps: DOC-REQ-009)

### Key Entities

- **ManifestYAML**: The v0 manifest document declaring data sources, transforms, embeddings, vector store, indices, and retrievers.
- **CompiledPlan**: DAG of `ManifestPlanNodeModel` entries with stable node IDs, capabilities, and dependency edges.
- **ReaderAdapter**: Interface for adding new data source types with `plan()`, `fetch()`, and `state()` methods.
- **EvaluationResult**: Metrics output including `hitRate@k`, `ndcg@k`, and optional `faithfulness` scores with threshold pass/fail.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `moonmind manifest validate` correctly accepts valid example YAMLs and rejects deliberately malformed ones with actionable errors.
- **SC-002**: `moonmind manifest run` successfully indexes a GitHub repo into Qdrant using a test manifest end-to-end.
- **SC-003**: `moonmind manifest evaluate` computes and reports retrieval metrics against a golden dataset.
- **SC-004**: CI pipeline validates all `examples/*.yaml` and gates on evaluation thresholds.
- **SC-005**: All tests pass via `./tools/test_unit.sh`.

## Assumptions & Constraints

- LlamaIndex remains the data plane runtime for readers, transforms, and vector store indexing.
- Qdrant is the primary vector store; `pgvector` and `milvus` support is deferred.
- The manifest schema is v0; breaking changes will bump `version` and include migration tooling.
- Evaluation datasets must exist as JSONL files; automated dataset generation is out of scope.
- This spec covers the data plane (schema, readers, indexing); the control plane (Temporal workflow orchestration) is covered by `070-manifest-ingest`.
