# RAG Doc ↔ Spec Consolidation Plan

**Status:** Proposed
**Date:** 2026-03-20

## Goal

Achieve a clean 1:1 mapping between each doc in `docs/RAG/` and one authoritative spec in `specs/`. Merge overlapping specs and delete obsolete ones.

---

## Current State

### Docs in `docs/RAG/`

| Doc | Role |
|-----|------|
| `LlamaIndexManifestSystem.md` | v0 manifest YAML schema, data sources, examples, operator guide |
| `ManifestIngestDesign.md` | Temporal workflow architecture & design decisions for `MoonMind.ManifestIngest` |
| `ManifestTaskSystem.md` | Implementation details, code locations, delivery phases for manifest ingest |
| `WorkflowRag.md` | Agent retrieval from Qdrant at runtime (`moonmind rag search`, overlay, transport) |

### Existing Specs

| Spec | Title | Maps to Doc(s) | Status |
|------|-------|----------------|--------|
| `036-worker-qdrant-rag` | Direct Worker Qdrant Retrieval Loop | `WorkflowRag.md` | Draft — good coverage, closely aligned |
| `070-manifest-ingest-runtime` | Manifest Ingest Runtime | `ManifestIngestDesign.md` | Draft — implements the design doc |
| `086-manifest-phase0` | Manifest Phase 0 Temporal Alignment | `ManifestTaskSystem.md` | Draft — overlaps significantly with 070 |

### Problems

1. **No spec** for `LlamaIndexManifestSystem.md` (schema, readers, indexing pipeline)
2. **Two specs** (070, 086) cover the same workflow (`MoonMind.ManifestIngest`) from different angles — 070 from architecture, 086 from implementation. This causes DOC-REQ duplication.
3. **Old task specs** (`specs/task/20260301/`, `specs/task/20260302/`) reference deleted doc paths (`docs/ManifestTaskSystem.md`) — these are historical and should be left as-is (they're completed task artifacts).

---

## Proposed 1:1 Mapping

| Doc | Spec (keep/create) | Action |
|-----|---------------------|--------|
| `WorkflowRag.md` | **`036-worker-qdrant-rag`** | **Keep as-is** — already well-aligned. Update `spec.md` header to reference `docs/RAG/WorkflowRag.md` as source doc. |
| `ManifestIngestDesign.md` | **`070-manifest-ingest-runtime`** | **Keep** — this is the architecture-to-runtime spec. Absorb any unique requirements from 086 that aren't covered. |
| `ManifestTaskSystem.md` | ~~`086-manifest-phase0`~~ | **Merge into 070, then delete 086.** The 086 spec was created to consolidate older specs (032, 034) and align with ManifestTaskSystem.md. Since ManifestTaskSystem.md is the implementation companion to ManifestIngestDesign.md, the spec authority should be 070. |
| `LlamaIndexManifestSystem.md` | **`088-manifest-schema-pipeline`** (NEW) | **Create new spec.** Covers the data plane: YAML schema validation, LlamaIndex reader/indexer pipeline, Qdrant upsert Activities, chunking, and evaluation. |

---

## Detailed Actions

### 1. Merge spec 086 → 070

086's unique contributions that 070 doesn't already cover:

| 086 Requirement | 070 Coverage | Merge Action |
|-----------------|-------------|--------------|
| DOC-REQ-001: `manifest_compile` Activity → `CompiledManifestPlanModel` | 070 FR-004/FR-015 cover pipeline | Already covered — no action |
| DOC-REQ-002: Stable node IDs via SHA-256 | 070 DOC-REQ-008/FR-009 cover plan compilation | Already covered — no action |
| DOC-REQ-003: `manifestHash` + `manifestVersion` tracking | Not explicit in 070 | **Add to 070** as new FR |
| DOC-REQ-005: 6 Temporal Updates | Not explicit in 070 | **Add to 070** as new FR |
| DOC-REQ-010: Deterministic normalization via manifest contract | 070 DOC-REQ-008 partially covers | Strengthen language in 070 |
| DOC-REQ-011: Secret leak detection/rejection | 070 DOC-REQ-013 covers security | Already covered — no action |

After merging, add a deprecation notice to `086-manifest-phase0/spec.md`:
```
> [!WARNING]
> This spec has been merged into `070-manifest-ingest-runtime`. See that spec for the authoritative requirements.
```

### 2. Create spec 088-manifest-schema-pipeline

New spec for `LlamaIndexManifestSystem.md`, covering:

- **Schema validation**: v0 manifest YAML parsing, JSON Schema enforcement, Pydantic model validation
- **Reader pipeline**: GitHub, Google Drive, Confluence, Local FS reader Activities
- **Transform pipeline**: chunking, HTML-to-text, metadata enrichment, PII redaction  
- **Index pipeline**: Qdrant upsert/delete via LlamaIndex `VectorStoreIndex`
- **Retrieval config**: named retrievers, hybrid search, rerankers, postprocessors
- **Evaluation**: hitRate@k, ndcg@k, faithfulness metrics against golden datasets
- **CLI**: `moonmind manifest validate`, `plan`, `run`, `evaluate`

User stories:
1. Operator validates a manifest YAML → schema errors surface before submission
2. Operator runs a manifest locally → readers fetch, chunk, embed, upsert to Qdrant
3. Operator evaluates retrieval quality → hit rate and NDCG thresholds pass/fail CI
4. Operator extends the system with a new reader type → adapter pattern documented

### 3. Update spec 036 (minor)

- Update header to reference `docs/RAG/WorkflowRag.md` as source document
- Verify all DOC-REQ section numbers still align after WorkflowRag.md updates
- No structural changes needed — spec is well-aligned

### 4. Update spec 070 (after merge)

- Add `docs/RAG/ManifestTaskSystem.md` as a second source document alongside `docs/RAG/ManifestIngestDesign.md`
- Add merged requirements from 086 (manifest hash tracking, 6 Updates, normalization)
- Update any references to old doc paths

---

## Final State

```
docs/RAG/
├── LlamaIndexManifestSystem.md  → specs/088-manifest-schema-pipeline/
├── ManifestIngestDesign.md      → specs/070-manifest-ingest-runtime/
├── ManifestTaskSystem.md        → specs/070-manifest-ingest-runtime/ (secondary source)
└── WorkflowRag.md               → specs/036-worker-qdrant-rag/

specs/086-manifest-phase0/       → DEPRECATED (merged into 070)
```

---

## Open Questions

1. Should 070 be renamed from `manifest-ingest-runtime` to something broader like `manifest-ingest` now that it absorbs 086?
2. Should `ManifestTaskSystem.md` remain as a separate doc or be merged into `ManifestIngestDesign.md`? They cover the same workflow from slightly different angles (architecture vs implementation).
3. Are the old task specs (`specs/task/20260301/`, `specs/task/20260302/`) worth updating or should they be treated as frozen historical artifacts?
