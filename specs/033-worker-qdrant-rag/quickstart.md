# Quickstart: Direct Worker Qdrant Retrieval Loop

## Prerequisites
- Python environment with MoonMind installed (`poetry install` or container image).
- Qdrant endpoint reachable from worker host with credentials: `QDRANT_URL` or `QDRANT_HOST` + `QDRANT_PORT`, optional `QDRANT_API_KEY`.
- Embedding provider credentials (default `GOOGLE_API_KEY` + `GOOGLE_EMBEDDING_MODEL=gemini-embedding-001`).
- Optional RetrievalGateway URL (`MOONMIND_RETRIEVAL_URL`) when workers cannot reach Qdrant directly.

## CLI Installation
1. Reinstall MoonMind package after feature lands so new `moonmind` console script becomes available.
2. Confirm CLI wiring:
   ```bash
   moonmind --help
   moonmind rag --help
   ```

## Direct Retrieval Flow
```bash
export GOOGLE_API_KEY=...
export QDRANT_URL=https://qdrant.internal:6333
moonmind rag search --query "How do Codex workers access Qdrant?" \
  --filter repo=moonmind \
  --filter tenant=prod \
  --top-k 8 \
  --overlay include \
  --budget tokens=1200 \
  --budget latency_ms=800 \
  --output-file var/context-pack.json
```
- Stdout prints formatted markdown for immediate prompt injection.
- `var/context-pack.json` contains structured payload for logging.
- Repeat `--budget` to set multiple ceilings (tokens + latency); CLI enforces limits locally before calling Qdrant and the RetrievalGateway echoes actual usage.

## RetrievalGateway Flow
```bash
export MOONMIND_RETRIEVAL_URL=https://api.moonmind.dev/retrieval
moonmind rag search --query "Overlay cleanup" \
  --transport gateway \
  --filter repo=moonmind \
  --budget latency_ms=600
```
- CLI posts query to gateway; all other flags remain the same.

## Overlay Workflow
```bash
moonmind rag overlay upsert services/api/routes.py
moonmind rag overlay upsert moonmind/rag/cli.py
moonmind rag search --query "Add statsd" --overlay include
moonmind rag overlay clean --run-id $(cat .moonmind/run_id)
```
- Overlay commands upload new vectors for modified files and remove them after the run completes.

## Worker Doctor
```bash
moonmind worker doctor
```
- Validates embedding credentials, Qdrant reachability, collection dimensions, and RetrievalGateway fallback before a worker claims tasks. Blocks execution with actionable errors if guardrails fail.

## Testing
- Run `./tools/test_unit.sh -k rag` to execute new unit tests covering CLI, overlay, and gateway behaviors.
- For manual smoke tests, run a local Qdrant container (`docker run -p 6333:6333 qdrant/qdrant`) and point env vars accordingly.
