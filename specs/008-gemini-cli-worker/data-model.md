# Data Model: Gemini Worker Configuration

**Feature**: Gemini CLI Worker
**Branch**: `008-gemini-cli-worker`

## Configuration Entities

These entities represent the configuration state managed by the worker and volume.

### GeminiWorkerConfig

| Field | Type | Description | Source |
|-------|------|-------------|--------|
| `gemini_queue` | String | Celery queue name | Env: `GEMINI_CELERY_QUEUE` (default: `gemini`) |
| `gemini_home` | Path | Path to config dir | Env: `GEMINI_HOME` (default: `/home/app/.gemini`) |
| `api_key` | String | API Key for Auth | Env: `GEMINI_API_KEY` |

### AuthVolumeStructure

The `gemini_auth_volume` is mounted at `GEMINI_HOME` and contains:

- `.env` (optional): Persisted environment variables.
- `config.json` (optional): CLI configuration (defaults, model preference).
- `cache/` (optional): Token or response cache.

## Database Schema Changes

*None.* This feature utilizes existing Celery task structures and Docker volumes.
