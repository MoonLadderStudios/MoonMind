# Implementation Plan: Manifest Schema & Data Pipeline

**Branch**: `088-manifest-schema-pipeline` | **Date**: 2026-03-20 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `specs/088-manifest-schema-pipeline/spec.md`
**Source Document**: `docs/RAG/LlamaIndexManifestSystem.md`

## Summary

Implement the v0 manifest YAML schema validation, LlamaIndex reader/indexer Activities, Qdrant upsert pipeline, CLI commands (`moonmind manifest validate|plan|run|evaluate`), and evaluation framework as described in `docs/RAG/LlamaIndexManifestSystem.md`. This builds the data plane that the `MoonMind.ManifestIngest` Temporal workflow (spec 070) orchestrates. Primary work is: (1) formalizing the v0 JSON Schema as a Pydantic model + validator, (2) wrapping existing indexers as `ReaderAdapter` Activities callable by Temporal child workflows, (3) adding CLI entry points, and (4) plugging evaluation metrics into CI.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: LlamaIndex, Pydantic v2, qdrant-client, click (CLI), FastAPI (API integration)
**Storage**: Qdrant (vector store), MinIO/S3 (artifacts), PostgreSQL (manifest registry)
**Testing**: pytest via `./tools/test_unit.sh`
**Target Platform**: Linux server (Docker containers), macOS local dev
**Project Type**: single (Python monorepo)
**Performance Goals**: Validate manifests < 100ms; embed + upsert 1000 chunks < 60s with batching
**Constraints**: No raw secrets in manifests; LlamaIndex readers must be Activity-safe (idempotent, retriable)
**Scale/Scope**: Support manifests with up to 5000 source files across multiple data sources

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Assessment | Notes |
|-----------|-----------|-------|
| I. Orchestrate, Don't Recreate | PASS | Uses LlamaIndex readers directly; MoonMind orchestrates via Temporal |
| II. One-Click Agent Deployment | PASS | Manifest CLI and Activities run within existing Docker Compose stack |
| III. Avoid Vendor Lock-In | PASS | Manifest schema supports multiple vector stores (`qdrant`, `pgvector`, `milvus`); multiple embedding providers |
| IV. Own Your Data | PASS | All ingested data stored in operator-controlled Qdrant and MinIO; no external SaaS required |
| V. Skills Are First-Class | PASS | Manifest validate/plan/run/evaluate are CLI commands usable as skill steps |
| VI. The Bittersweet Lesson | PASS | Schema and ReaderAdapter interface designed for evolution; thin wrappers around LlamaIndex |
| VII. Powerful Runtime Configurability | PASS | All settings via env vars and manifest YAML; no hardcoded constants |
| VIII. Modular and Extensible Architecture | PASS | New readers via `ReaderAdapter` interface; new vector stores via adapter pattern |
| IX. Resilient by Default | PASS | Activities are idempotent; Temporal provides retries/timeouts; errors classified |
| X. Facilitate Continuous Improvement | PASS | Evaluation metrics feed into CI; run outcomes produce structured summaries |
| XI. Spec-Driven Development Is the Source of Truth | PASS | This spec consolidates 032+034+086 and aligns with updated `ManifestIngestDesign.md` |

## Project Structure

### Documentation (this feature)

```text
specs/088-manifest-schema-pipeline/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   └── requirements-traceability.md
├── checklists/
│   └── requirements.md
└── tasks.md             # Phase 2 output (speckit-tasks)
```

### Source Code (repository root)

```text
moonmind/
├── schemas/
│   ├── manifest_models.py          # [MODIFY] v0 Pydantic models + JSON Schema generation
│   └── manifest_ingest_models.py   # [EXISTING] Compiled plan models
├── manifest/
│   ├── loader.py                   # [MODIFY] Add v0 schema validation
│   ├── runner.py                   # [MODIFY] Wire ReaderAdapter pattern
│   ├── interpolation.py            # [EXISTING] ${ENV} interpolation
│   ├── validator.py                # [NEW] v0 schema + semantic validation
│   ├── reader_adapter.py           # [NEW] ReaderAdapter interface + registry
│   └── evaluation.py               # [NEW] hitRate@k, ndcg@k, faithfulness
├── indexers/
│   ├── github_indexer.py           # [MODIFY] Wrap as ReaderAdapter
│   ├── google_drive_indexer.py     # [MODIFY] Wrap as ReaderAdapter
│   ├── confluence_indexer.py       # [MODIFY] Wrap as ReaderAdapter
│   ├── jira_indexer.py             # [EXISTING] Lower priority
│   └── local_data_indexer.py       # [MODIFY] Wrap as ReaderAdapter
├── rag/
│   ├── cli.py                      # [MODIFY] Add manifest subcommands
│   └── retriever.py                # [EXISTING] Query-time retrieval
├── workflows/
│   ├── temporal/
│   │   ├── manifest_ingest.py      # [EXISTING] Workflow orchestration (spec 070)
│   │   └── activity_runtime.py     # [MODIFY] Register reader/embed/upsert Activities
│   └── agent_queue/
│       └── manifest_contract.py    # [EXISTING] Validation + normalization

docs/
└── schemas/
    └── manifest-v0.json            # [NEW] Generated JSON Schema

tests/
├── unit/
│   ├── manifest/
│   │   ├── test_validator.py       # [NEW] Schema + semantic validation tests
│   │   ├── test_reader_adapter.py  # [NEW] ReaderAdapter contract tests
│   │   └── test_evaluation.py      # [NEW] Metric computation tests
│   └── schemas/
│       └── test_manifest_models.py # [MODIFY] v0 model tests
└── examples/                       # CI validation of example YAMLs
```

**Structure Decision**: Single Python monorepo. New modules added to `moonmind/manifest/` for validation, reader adapters, and evaluation. CLI extended in `moonmind/rag/cli.py`. Activities registered in existing `activity_runtime.py`.

## Complexity Tracking

No constitution violations. No complexity escalation required.
