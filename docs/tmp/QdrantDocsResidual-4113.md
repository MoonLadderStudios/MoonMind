# Qdrant Docs Residual Disposition — #4113

> **Temporary migration note (not canonical).** Owned by
> MoonLadderStudios/MoonMind#4113. **Archive/delete trigger:** delete this
> file once the sibling execution/removal children of #4103 land, the
> repository-wide sweep reaches zero unjustified active matches, and the epic
> records the final disposition. Do not link it from canonical docs.

Method: `git grep -il "qdrant"` (case-insensitive) on the #4113 work tree:
77 matches before, 65 after. Every remaining match is disposed below. This
report does **not** claim a zero-match sweep.

## A. Fixed by #4113 (12 files cleared)

| File | Change |
| --- | --- |
| `README.md` | Qdrant+MinIO row split; MinIO-only artifact-storage row plus vector-free boundary statement. |
| `docs/MoonMindArchitecture.md` | Qdrant node/edge removed from data-plane diagram; data-plane and context sections state the vector-free boundary. |
| `docs/MoonMindRoadmap.md` | Vector-storage/embeddings extensibility and optional Qdrant/LlamaIndex/Mem0 profiles rewritten as vector-free exact-context contract; milestone completion no longer requires a replaceable retrieval backend. |
| `docs/Memory/MemoryArchitecture.md` | Rewritten vector-free: exact metadata history, Postgres/artifact storage, opt-in Mem0 only; no Qdrant/LlamaIndex semantic recall. |
| `docs/Rag/WorkflowRag.md` | Rewritten vector-free as exact context assembly (`ContextPack`, budgets, provenance, artifact discipline); explicit `rag`/`followUpRetrieval` rejected before execution per #4105. |
| `docs/ManagedAgents/WorkerVectorEmbedding.md` | **Deleted** (vector-only; generic container/secret boundaries already owned by DockerOutOfDocker/CodexCliManagedSessions). |
| `docs/Rag/LlamaIndexManifestSystem.md` | Rewritten vector-free: no normative embeddings/vectorStore/indices/retrievers; retired-field section; vector-free examples; compile/execute ownership preserved. |
| `docs/Rag/ManifestIngestDesign.md` | Qdrant-runtime cross-link label and remaining-work Qdrant/embedding items removed; compile/execute ownership untouched (#4108). |
| `docs/Omnigent/PolicyAuthority.md` | Policy-section enumeration no longer lists vector collections. |
| `examples/manifest-full-example.yaml`, `examples/manifest-github-reader.yaml`, `examples/readers-full-example.yaml` | Vector-free: embeddings/vectorStore/indices/retrievers/QDRANT_* removed; still pass `test_all_example_yamls_validate`. |
| `examples/eval/smoke.jsonl` | Live-Qdrant scoring rows replaced with generic evidence-traceability rows; committed-baseline scores unchanged (hitRate@10 1.0, ndcg@10 0.877). |
| `frontend/src/entrypoints/workflow-start.tsx` | Custom-capability example token `qdrant` → `playtest`. |
| `moonmind/cli.py` (help text only) | `rag search` / `--collection` / `manifest plan` help strings marked retired; commands untouched for the sibling execution track. |
| `memory/omnigent-3514-followup-retrieval-authoring.md` | Retired-policy note appended so later agents do not restore vector fields. |
| `moonmind/schemas/manifest_v0_models.py`, `moonmind/manifest/validator.py`, `moonmind/manifest/pipeline.py`, `moonmind/manifest/evaluation.py` | Minimal coordination edit: retired vector fields optional (historical reads still accepted), None-guards, PII warning reworded. Full redesign stays with #4108. |

## B. Justified non-active mentions kept in #4113 files

| File | Disposition |
| --- | --- |
| `docs/Memory/MemoryResearch.md` | Historical research survey; top banner marks it pre-removal background with no active-support purpose. Body survey mentions unchanged. |
| `docs/Rag/WorkflowRag.md`, `docs/Rag/LlamaIndexManifestSystem.md` | Narrow retired-boundary statements naming Qdrant with explicit non-active purpose (rejected before execution). |
| `docs/DocsReview.md` | Point-in-time review table referencing retired doc paths; preserved as historical record. |

## C. Time-bounded migration notes (docs/tmp, belong to sibling issues)

| File | Owner |
| --- | --- |
| `docs/tmp/QdrantCutoverRunbook-4115.md` | #4115 operator runbook. |
| `docs/tmp/QdrantRemovalInventory-4105.md` | #4105 admission-scope inventory. |
| `docs/tmp/MarkdownAssertionInventory3964.md` | Pre-existing assertion inventory. |
| `docs/tmp/QdrantDocsResidual-4113.md` (this file) | #4113; delete per trigger above. |

## D. Sibling-owned behavior/code (not #4113; removal executes there)

| Files | Owner |
| --- | --- |
| `moonmind/rag/*` (service, qdrant_client, settings, guardrails, context_injection, overlay, overlay_cleanup, cli, context_pack) | Sibling execution/cutover deletes; #4105 already retires new writes. |
| `api_service/api/routers/retrieval_gateway.py` live query/index-health paths | Sibling execution/cutover deletes; historical reads retained per #4105. |
| `moonmind/agents/codex_worker/worker.py`, `moonmind/workflows/temporal/artifacts.py`, `moonmind/workflows/skills/ops_diagnostics_execution.py`, `moonmind/workflows/executions/manifest_contract.py`, `moonmind/manifest/incremental.py`, `moonmind/memory/run_digest.py` retrieval wiring | Sibling execution/cutover. |
| `moonmind/omnigent/codex_execution_decisions.py` retired-vector error strings | #4105 (retired-vector rejection evidence; must keep until callers migrate). |
| `moonmind/config/settings.py` QdrantSettings/RAG settings, `.env-template`, `docker-compose.yaml` (volume + retirement comments) | Retired by #4115; physical deletion with sibling cutover. |
| `moonmind/cli.py` + `moonmind/rag/cli.py` vector commands, `tools/get-qdrant.py`, `tools/qdrant_cutover_rehearsal.py` | Sibling execution/cutover; rehearsal tool is #4115 evidence. |
| `pyproject.toml` + `poetry.lock` `qdrant-client` | Sibling execution/cutover removes with the last importer. |
| `.agents/skills/update-moonmind/scripts/run-update-moonmind.sh` service list | #4112 (behavior-bearing Skill script). |

## E. Test/history fixtures (no product claim; updated with owning code change)

- `frontend/src/entrypoints/workflow-start.test.tsx`: `qdrant` appears only as an arbitrary user-typed custom-capability token exercising generic CSV/chip parsing, not as MoonMind-managed retrieval.
- `tests/unit/config/test_vector_free_defaults_4115.py`, `tests/unit/tools/test_qdrant_cutover_rehearsal.py`: assert the #4115 retired-flag/cutover contracts; removed with those contracts.
- `tests/unit/**/test_*.py` rag/manifest suites with `qdrant` fixtures (clients, settings, historical manifests): exercise historical reads and retired-path rejection; deleted/rewritten alongside the owning sibling code change. Updated in this change only where the #4113 schema coordination required it (`test_validator.py` vector-free case).
- `tests/fixtures/manifests/phase0/*.yaml`, `tests/integration/reliability/replays/**/manifest.json`, `tests/provider/manifest/test_manifest_ingest_live_sources.py`: historical payload evidence and replay fixtures; preserved per the immutable-history rule (provider tests are manual/nightly, excluded from required CI).
- `tests/unit/workflows/executions/test_prepared_context.py`: `qdrant collection` used as arbitrary error-signature text for fix-pattern projection; no retrieval behavior asserted.
