# Remaining work: `docs/Rag/LlamaIndexManifestSystem.md`

**Source:** [`docs/Rag/LlamaIndexManifestSystem.md`](../../Rag/LlamaIndexManifestSystem.md)  
**Last synced:** 2026-03-24

## Open items

### Roadmap & versioning (§12 in source)

- **v0.1–v0.4** (from prior roadmap bullets): ingestion + retrieval baseline; scheduled jobs + lineage; multi-tenant policy; dataset registries + eval dashboards — product backlog; status per component.
- **Versioning policy:** Breaking changes bump `version` + `manifest migrate` — enforce when schema evolves.

### Elsewhere in doc

- Loader still parses legacy `apiVersion/kind/spec` in examples; proposed v0 contract migration called out — complete schema migration when ready.
