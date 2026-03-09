# Research: Temporal Local Dev Bring-up Path & E2E Test

## Docker Compose for Temporal Local Testing
- **Decision**: Leverage existing `docker-compose.yaml` profiles to run Temporal and MoonMind workers together seamlessly.
- **Rationale**: Keeps the one-click local-first deployment principle intact. Temporal provides robust official docker images. MoonMind workers can be containerized and mapped to the same compose network.
- **Alternatives considered**: Running Temporal via `temporal cli` locally. Rejected because `docker compose` is the standard MoonMind operator path and accurately mirrors the production-like topology with Postgres and MinIO.

## End-to-End Testing Approach
- **Decision**: Write a Python-based E2E test script (`scripts/test_temporal_e2e.py`) utilizing the `pytest` framework and MoonMind API clients.
- **Rationale**: Python is the primary language of the API and workers. A dedicated E2E script can run via CI or locally, making HTTP requests to start a task, and continuously querying the Temporal or MoonMind API until successful completion, verifying artifacts along the way.
- **Alternatives considered**: Bash scripts with `curl`. Rejected due to complexity in parsing JSON responses, polling with backoffs, and complex assertions compared to python testing frameworks.

## Rollback Verification Strategy
- **Decision**: Document the rollback process in `DeveloperGuide.md` and include a test or script flag to run the execution locally via the legacy TemporalExecutionService (bypassing Temporal server).
- **Rationale**: Essential safety net during migration to Temporal. Proves the fallback works.
- **Alternatives considered**: Full automated rollback/recovery test. Rejected as overkill for Phase 1; manual or simple script flag is sufficient.
