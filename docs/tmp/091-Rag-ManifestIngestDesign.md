# Remaining work: `docs/Rag/ManifestIngestDesign.md`

**Source:** [`docs/Rag/ManifestIngestDesign.md`](../../Rag/ManifestIngestDesign.md)  
**Last synced:** 2026-03-24

## Open items

### §19 Delivery plan

- **Phase 0:** Marked complete in source (strike-through items) — verify in code.
- **Phase 1 — Engine pipeline (in progress):** Fetcher activities (GitHub, Drive, Confluence, local FS) behind child `MoonMind.Run` workflows; chunking/embeddings; Qdrant IDs + delete-by-filter; checkpoint incremental sync.
- **Phase 2 — User interface:** Mission Control list/detail, launch form, node status, interactive Updates (pause/resume/cancel/retry/update manifest).

### §20 Open questions

- Dependency expressiveness, inline activity nodes, failure semantics in `BEST_EFFORT`, manifest lineage search attribute, sharding/CAN thresholds — decide and implement or defer with tickets.
