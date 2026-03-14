# Quickstart: Temporal Local Artifact System

## 1. Confirm selected orchestration mode

- This feature is **runtime implementation mode**.
- Required deliverables include production runtime code changes and validation tests.
- Docs-only updates are not sufficient for completion.

Docs-mode note for consistency:

```bash
./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode docs
```

Expected result: docs mode skips runtime scope checks, but this feature remains runtime-gated.

## 2. Review implementation surfaces

- Runtime/config:
  - `docker-compose.yaml`
  - `.env-template`
  - `moonmind/config/settings.py`
- Artifact runtime and API:
  - `moonmind/workflows/temporal/artifacts.py`
  - `moonmind/schemas/temporal_artifact_models.py`
  - `api_service/api/routers/temporal_artifacts.py`
  - `api_service/db/models.py`
  - `api_service/migrations/versions/202603050001_temporal_artifact_system.py`
- Validation:
  - `tests/unit/workflows/temporal/test_artifacts.py`
  - `tests/unit/api/routers/test_temporal_artifacts.py`
  - `tests/integration/temporal/test_temporal_artifact_local_dev.py` (planned)

## 3. Start local stack with Temporal + MinIO baseline

```bash
docker compose up -d api-db temporal-db temporal temporal-namespace-init minio api celery-worker
```

Expected baseline:

- MinIO is reachable from API/worker on internal Docker network.
- API artifact operations default to MinIO-backed blob flow.
- `AUTH_PROVIDER=disabled` local mode allows user-facing artifact metadata/presign calls with default-principal attribution.

## 4. Validate core artifact API flow

1. Create artifact (`POST /api/artifacts`) with optional execution link metadata.
2. Upload bytes:
   - direct path (`PUT /api/artifacts/{artifact_id}/content`) for small payloads, or
   - multipart presign path for larger payloads.
3. Complete upload (`POST /api/artifacts/{artifact_id}/complete`).
4. Read metadata/download:
   - `GET /api/artifacts/{artifact_id}?include_download=true`
   - `POST /api/artifacts/{artifact_id}/presign-download`
5. List by execution:
   - `GET /api/executions/{namespace}/{workflow_id}/{run_id}/artifacts`

Expected result: payloads exchanged in Temporal/runtime contracts are `ArtifactRef` + small JSON; no large blob payloads in workflow history.

## 5. Validate auth mode behavior

### Local no-auth mode (`AUTH_PROVIDER=disabled`)

- Artifact metadata/presign endpoints succeed without end-user login.
- Audit attribution resolves to configured default local principal.

### Authenticated modes

- Unauthorized principals are denied metadata/presign access.
- Authorized principals succeed with execution-linked checks.

## 6. Validate lifecycle and retention behavior

1. Create artifacts across retention classes and link types.
2. Run lifecycle cleanup repeatedly.
3. Verify:
   - soft-delete behavior is idempotent,
   - expired non-pinned artifacts transition consistently,
   - pinned artifacts are preserved until unpinned.

## 7. Run repository-standard validation

```bash
./tools/test_unit.sh
```

Notes:

- Use `./tools/test_unit.sh` as the required unit-test command.
- Do not substitute direct `pytest` invocation for acceptance.

## 8. Run runtime scope guards

```bash
./.specify/scripts/bash/validate-implementation-scope.sh --check tasks --mode runtime
./.specify/scripts/bash/validate-implementation-scope.sh --check diff --base-ref origin/main --mode runtime
```

Expected result: runtime scope gates pass with production runtime changes and validation tests represented.

## 9. Planning artifact completion gate

Before `/agentkit.tasks`, ensure these artifacts exist:

- `specs/058-temporal-artifact-local-dev/plan.md`
- `specs/058-temporal-artifact-local-dev/research.md`
- `specs/058-temporal-artifact-local-dev/data-model.md`
- `specs/058-temporal-artifact-local-dev/quickstart.md`
- `specs/058-temporal-artifact-local-dev/contracts/temporal-artifacts.openapi.yaml`
- `specs/058-temporal-artifact-local-dev/contracts/requirements-traceability.md`
