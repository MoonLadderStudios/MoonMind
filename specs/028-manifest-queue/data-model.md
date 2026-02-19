# Data Model: Manifest Queue Plumbing (Phase 0)

## Agent Queue Manifest Payload

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | string | yes | Always `"manifest"`; used by queue filters and worker capability matching. |
| `manifest` | object | yes | Canonical manifest submission containing `name`, `action`, `source`, and `options`. Must mirror registry content when `source.kind == "registry"`. |
| `manifest.source` | object | yes | Supports `inline`, `registry`, `path`, `repo` but Phase 0 only guarantees inline + registry. Includes either `content` or reference metadata. |
| `manifest.options` | object | optional | Overrides limited to `dryRun`, `forceFull`, `maxDocs`. Applied on top of YAML `run` block for effective execution config. |
| `requiredCapabilities` | array[string] | computed | Derived by manifest contract: always includes `manifest`, vector store capability (`qdrant`), embeddings provider (`embeddings`, `openai`, `google`, etc.), and per-source capabilities (`github`, `confluence`, `gdrive`, `local_fs`). |
| `manifestHash` | string | computed | SHA-256 hash of the manifest YAML (post-interpolation metadata) recorded on the queue job for drift detection. |
| `manifestVersion` | string | computed | One of `"v0"` or `"legacy"`, determined from manifest YAML `version` or schema detection. |
| `effectiveRunConfig` | object | computed | Merge of YAML `run` block with allowed overrides, included in payload artifacts for worker consumption. |

## ManifestRecord Extensions

Existing table columns remain intact; Phase 0 ensures the API populates and surfaces the following fields for each registry entry:

| Column | Type | Behavior |
|--------|------|----------|
| `version` | text | Persisted manifest schema version (`v0` or `legacy`). Defaults to `v0` for new entries. |
| `content_hash` | text | SHA-256 of stored YAML content; updated whenever YAML changes. |
| `last_run_job_id` | uuid | Updated when `/runs` submits a queue job referencing the registry entry. |
| `last_run_status` | text | Derived from queue job status updates (Phase 0 may leave null until workers emit callbacks). |
| `state_json` | jsonb | Reserved for checkpoint state once workers write it; Phase 0 just ensures the field is preserved and exposed via GET endpoints. |
| `state_updated_at` | timestamptz | Updated when `state_json` changes; Phase 0 writes `NULL` values untouched. |

## Capability Derivation Mapping

To produce deterministic `requiredCapabilities`, the manifest contract inspects the manifest body using the following rules:

1. Always include `manifest`.
2. Include `embeddings` plus the provider capability:
   - `embeddings.provider == "google"` → `google`
   - `embeddings.provider == "openai"` → `openai`
   - `embeddings.provider == "ollama"` → `ollama`
3. Include vector store capability:
   - `vectorStore.type == "qdrant"` → `qdrant`
4. Include capabilities for each `dataSources[].type`:
   - `GithubRepositoryReader` → `github`
   - `GoogleDriveReader` → `gdrive`
   - `ConfluenceReader` → `confluence`
   - `SimpleDirectoryReader` → `local_fs`
5. Deduplicate capabilities and keep ordering stable (e.g., sorted) so queue payload diffs remain deterministic.

## API Surface Relationship

Each registry entry maps 1:1 to manifest queue jobs submitted via `/api/manifests/{name}/runs`. Inline submissions bypass the registry but still produce a persisted job record that references `manifestHash`, enabling the UI to show manifest metadata even if no registry entry exists.
