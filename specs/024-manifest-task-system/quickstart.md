# Quickstart: Manifest Task System

1. **Create/locate a v0 manifest**
   - Author YAML per `docs/LlamaIndexManifestSystem.md` or load existing entries under `examples/readers-*.yaml`.
   - Ensure `vectorStore.type = qdrant` and embeddings provider align on dimensions.

2. **Submit a manifest run (Phase 1 path)**
   - Compose ManifestJobPayload with `type="manifest"`, `requiredCapabilities` (manifest, embeddings, qdrant, source connectors), and a `manifest.source` of kind `inline` or `path`.
   - `curl -X POST /api/queue/jobs` with the payload; confirm response returns `jobId`.

3. **Monitor the run**
   - Open Tasks Dashboard → `Manifests` category (new tab) to view status.
   - Stream events via `/api/queue/jobs/{jobId}/events` and download artifacts once available.

4. **Implement worker configuration**
   - Launch `moonmind-manifest-worker` with env vars: `MOONMIND_URL`, `MOONMIND_WORKER_ID`, `MOONMIND_WORKER_TOKEN`, `MOONMIND_WORKDIR`, `MOONMIND_WORKER_CAPABILITIES`, embedding provider keys, and `QDRANT_*` settings.
   - Verify the worker advertises capabilities superset and respects cancellation requests.

5. **Review outputs**
   - Validate `reports/plan.json`, `reports/run_summary.json`, and `manifest/resolved.yaml` artifacts for counts + redacted secrets.
   - Update manifest registry entries via `/api/manifests/{name}` once CRUD is enabled (Phase 2).
