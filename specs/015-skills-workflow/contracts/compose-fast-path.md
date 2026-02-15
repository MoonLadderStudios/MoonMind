# Runtime Contract: Compose Fast Path (Codex + Gemini Embeddings)

## Required Environment Variables

- `GOOGLE_API_KEY`
- `DEFAULT_EMBEDDING_PROVIDER=google`
- `GOOGLE_EMBEDDING_MODEL=gemini-embedding-001`
- `CODEX_ENV=prod`
- `CODEX_MODEL=gpt-5-codex`
- `GITHUB_TOKEN=<repo-capable PAT>`

## Worker/Auth Contract

- `celery_codex_worker` mounts `CODEX_VOLUME_NAME` at `CODEX_VOLUME_PATH`.
- One-time auth flow must persist in that volume before normal operation.
- Worker startup must run Codex preflight and fail fast on auth failure.

## Queue Contract

- `celery_codex_worker` consumes `${CELERY_DEFAULT_QUEUE:-speckit},${SPEC_WORKFLOW_CODEX_QUEUE:-codex}`.
- `celery_gemini_worker` consumes `${GEMINI_CELERY_QUEUE:-gemini}`.
- Queue names remain backward compatible during skills-first migration.

## Verification Contract

Operator validation includes:

1. Codex auth volume check passes (`codex login status`).
2. Worker logs show queue bindings and successful preflight before task processing.
3. API runtime resolves embedding provider/model to Google Gemini defaults.
