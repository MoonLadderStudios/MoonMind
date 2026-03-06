# Requirements Traceability: Temporal Local Artifact System

| DOC-REQ ID | Source Reference | Mapped FR(s) | Planned Implementation Surfaces | Validation Strategy |
|---|---|---|---|---|
| DOC-REQ-001 | `docs/Temporal/WorkflowArtifactSystemDesign.md` §2, §15.1 | FR-003, FR-016 | `moonmind/workflows/temporal/artifacts.py`, Temporal workflow/activity call sites, `moonmind/schemas/temporal_artifact_models.py` | Unit tests verify workflow-facing contracts carry `ArtifactRef` and small JSON only; integration checks assert no large payload state usage |
| DOC-REQ-002 | `docs/Temporal/WorkflowArtifactSystemDesign.md` §2, §7.4 | FR-004 | `api_service/db/models.py`, `moonmind/workflows/temporal/artifacts.py` | Unit tests verify completed artifacts are immutable and mutations require new artifact IDs |
| DOC-REQ-003 | `docs/Temporal/WorkflowArtifactSystemDesign.md` §6.2, §6.3 | FR-005 | `docker-compose.yaml`, `.env-template`, `moonmind/config/settings.py`, `moonmind/workflows/temporal/artifacts.py` | Integration tests verify MinIO-default wiring in compose/local runtime and explicit override behavior |
| DOC-REQ-004 | `docs/Temporal/WorkflowArtifactSystemDesign.md` §6.2, §8 | FR-006 | `api_service/db/models.py`, `api_service/migrations/versions/202603050001_temporal_artifact_system.py`, artifact storage adapter in `moonmind/workflows/temporal/artifacts.py` | Unit/integration checks verify metadata in Postgres while blob bytes are persisted in object storage only |
| DOC-REQ-005 | `docs/Temporal/WorkflowArtifactSystemDesign.md` §6.3, §9.6 | FR-005 | `docker-compose.yaml`, `.env-template`, runtime service env wiring | Compose integration checks confirm MinIO service is reachable on internal network by API/worker by default |
| DOC-REQ-006 | `docs/Temporal/WorkflowArtifactSystemDesign.md` §9.5 | FR-007 | `api_service/api/routers/temporal_artifacts.py`, `api_service/auth.py`, auth provider dependency resolution | Router/unit tests verify no-auth local mode success and default-principal attribution |
| DOC-REQ-007 | `docs/Temporal/WorkflowArtifactSystemDesign.md` §9.2, §9.5 | FR-008 | `api_service/api/routers/temporal_artifacts.py`, authorization checks in `moonmind/workflows/temporal/artifacts.py` | Auth-mode tests verify access denial for unauthorized principals in authenticated modes |
| DOC-REQ-008 | `docs/Temporal/WorkflowArtifactSystemDesign.md` §7.1-§7.3 | FR-009 | `moonmind/workflows/temporal/artifacts.py`, `moonmind/schemas/temporal_artifact_models.py`, DB models | Unit tests verify `art_<ULID>` IDs, storage-key safety, and digest/size validation |
| DOC-REQ-009 | `docs/Temporal/WorkflowArtifactSystemDesign.md` §8.1, §8.2, §11 | FR-010 | `api_service/db/models.py`, `moonmind/workflows/temporal/artifacts.py`, `api_service/api/routers/temporal_artifacts.py` | Unit/integration tests verify deterministic latest-output query behavior by execution + link type |
| DOC-REQ-010 | `docs/Temporal/WorkflowArtifactSystemDesign.md` §9.1-§9.4, §12.2 | FR-011 | `api_service/api/routers/temporal_artifacts.py`, service presign methods, audit logging paths | Router/service tests verify short-lived scoped presign issuance and authorization gating before grant |
| DOC-REQ-011 | `docs/Temporal/WorkflowArtifactSystemDesign.md` §10.1-§10.3 | FR-012 | `moonmind/workflows/temporal/artifacts.py`, API multipart endpoints/models, stream download endpoint | Unit tests verify direct-vs-multipart threshold behavior; integration tests cover large upload and streaming download |
| DOC-REQ-012 | `docs/Temporal/WorkflowArtifactSystemDesign.md` §12.3 | FR-013 | `moonmind/workflows/temporal/artifacts.py` preview generation, router metadata/download behavior | Unit tests verify preview redaction output and restricted raw behavior policy paths |
| DOC-REQ-013 | `docs/Temporal/WorkflowArtifactSystemDesign.md` §13.1-§13.4 | FR-014 | retention mapping logic in `moonmind/workflows/temporal/artifacts.py`, lifecycle cleanup workflow/schedule integration (planned) | Integration tests seed retention classes and verify idempotent soft/hard deletion semantics across repeated runs |
| DOC-REQ-014 | `docs/Temporal/WorkflowArtifactSystemDesign.md` §14 | FR-015 | `api_service/api/routers/temporal_artifacts.py`, `moonmind/schemas/temporal_artifact_models.py`, `specs/045-temporal-artifact-local-dev/contracts/temporal-artifacts.openapi.yaml` | Contract tests validate request/response schema and endpoint coverage for create/presign/complete/get/list/link/pin/delete |
| DOC-REQ-015 | `docs/Temporal/WorkflowArtifactSystemDesign.md` §15.2, §16 | FR-001, FR-002, FR-016 | Temporal artifact activities in `moonmind/workflows/temporal/artifacts.py`, API/runtime integration points, test suites | `./tools/test_unit.sh` plus targeted integration tests validate runtime-ready API, reference format, and activity-bounded side effects |

## Runtime Mode Alignment Gate

- Selected orchestration mode: **runtime**.
- Required implementation scope includes production code paths and validation tests.
- Docs mode behavior remains documented only for scope-check semantics (`validate-implementation-scope.sh --mode docs` skip behavior).

## Coverage Gate

- Source requirements tracked: **15** (`DOC-REQ-001` through `DOC-REQ-015`).
- Every requirement maps to FR coverage, planned implementation surfaces, and explicit validation strategy.
- Planning fails if any requirement becomes unmapped or loses validation coverage.
