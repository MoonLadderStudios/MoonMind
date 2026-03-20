# Research: Manifest Schema & Data Pipeline

**Date**: 2026-03-20
**Feature**: `088-manifest-schema-pipeline`

## Research Topics

### 1. Existing Manifest Schema Models

**Decision**: Extend existing `moonmind/schemas/manifest_models.py` with v0 Pydantic models.

**Rationale**: The file already defines legacy `apiVersion/kind/spec` manifest models (1985 bytes). The v0 schema from `LlamaIndexManifestSystem.md` is a superset. Extending this file maintains backward compatibility while adding structured validation.

**Alternatives considered**:
- New file `moonmind/manifest/models_v0.py` — rejected because schema models belong in `moonmind/schemas/` for consistency with `manifest_ingest_models.py`.

### 2. Reader Adapter Pattern

**Decision**: Create a `ReaderAdapter` protocol class with `plan()`, `fetch()`, `state()` methods.

**Rationale**: Four indexers already exist (`github_indexer.py`, `google_drive_indexer.py`, `confluence_indexer.py`, `local_data_indexer.py`) but use ad-hoc interfaces. A protocol class unifies them and makes them registrable by manifest `dataSources[].type` string.

**Alternatives considered**:
- Direct LlamaIndex reader instantiation in the workflow — rejected because it bypasses the adapter layer and makes testing harder. Existing indexers already wrap LlamaIndex readers with MoonMind-specific auth and error handling.

### 3. CLI Entry Points

**Decision**: Add `moonmind manifest` subcommands to the existing CLI in `moonmind/rag/cli.py`.

**Rationale**: The RAG CLI already exists and is the entry point for `moonmind rag search`. Adding `moonmind manifest validate|plan|run|evaluate` keeps CLI tooling co-located. The manifest commands share embedding and Qdrant dependencies with the RAG CLI.

**Alternatives considered**:
- Separate `moonmind/manifest/cli.py` — viable but fragments CLI registration. Could refactor later if manifest CLI grows significantly.

### 4. Evaluation Framework

**Decision**: Implement `hitRate@k` and `ndcg@k` in `moonmind/manifest/evaluation.py` using LlamaIndex's evaluation utilities where available.

**Rationale**: These are the two metrics specified in the source document's evaluation block. `faithfulness` (LLM-as-judge) is optional and deferred to Phase 2.

**Alternatives considered**:
- Using external evaluation library (RAGAS, DeepEval) — deferred because it adds a dependency; LlamaIndex's built-in evaluation or a simple custom implementation suffices for v0.

### 5. Manifest Contract Integration

**Decision**: Reuse existing `manifest_contract.py` for validation/normalization; add v0-specific validation in a new `validator.py`.

**Rationale**: `manifest_contract.py` already handles secret leak detection, normalization, and capability derivation. Schema-level validation (JSON Schema, field type checks, cross-field compatibility) is orthogonal and belongs in a dedicated validator module that runs before the contract layer.

**Alternatives considered**:
- Merging all validation into `manifest_contract.py` — rejected because it would bloat an already complex module (contract normalization vs schema validation are different concerns).
