# Feature Specification: Remove mm-orchestrator

**Feature Branch**: `087-orchestrator-removal`  
**Created**: 2025-03-19  
**Status**: Draft  
**Input**: Remove the `mm-orchestrator` runtime and related code, configuration, and docs from the repository (original scratch plan lived under `docs/tmp/` and has been deleted; this spec is the retained normative source).

## Source Document Requirements

Requirements below are normative for this feature (historically aligned with the former tmp removal plan).

| ID | Source citation | Requirement summary |
| --- | --- | --- |
| **DOC-REQ-001** | §2.1 | The default Docker Compose stack MUST NOT define or start an `orchestrator` / `mm-orchestrator` service. |
| **DOC-REQ-002** | §2.1 | Test Compose MUST NOT define an `orchestrator-tests` service. |
| **DOC-REQ-003** | §2.1 | Orchestrator-specific environment variables (e.g. `ORCHESTRATOR_*`, `MOONMIND_ORCHESTRATOR_*`) MUST be removed from Compose and related config where they only served the removed service. |
| **DOC-REQ-004** | §2.2 | The HTTP API MUST NOT expose orchestrator routes; orchestrator router modules MUST be removed from application wiring. |
| **DOC-REQ-005** | §2.2 | In-repo orchestrator workflow/worker code under the documented package path MUST be removed. |
| **DOC-REQ-006** | §2.2 | Persisted orchestrator domain models and enums MUST be removed from the application model layer. |
| **DOC-REQ-007** | §2.2 | Dedicated orchestrator service package files (other than shared dependency stubs if none remain) MUST be removed. |
| **DOC-REQ-008** | §2.3 | Orchestrator integration, unit, and contract tests and the dedicated GitHub Actions workflow MUST be removed or replaced so CI no longer depends on the orchestrator. |
| **DOC-REQ-009** | §2.3 | Remaining tests and scripts that reference the orchestrator MUST be updated so the suite does not require orchestrator imports or endpoints. |
| **DOC-REQ-010** | §2.4���2.5 | Architecture and Temporal docs MUST NOT describe `mm-orchestrator` as a running component; obsolete orchestrator-specific spec/OpenAPI trees MUST be removed or superseded. |
| **DOC-REQ-011** | §3 | A database migration MUST drop orchestrator-related tables safely (correct FK ordering). |
| **DOC-REQ-012** | §4 | After removal, unit tests MUST pass and the API stack MUST start via Compose without an orchestrator container. |

### Functional Requirements

- **FR-001** (maps **DOC-REQ-001**, **DOC-REQ-002**, **DOC-REQ-003**): Operators MUST be able to bring up the standard stack without any orchestrator container or orchestrator-test service, and without orchestrator-only env knobs in the canonical compose files.
- **FR-002** (maps **DOC-REQ-004**, **DOC-REQ-005**, **DOC-REQ-007**): The running product MUST NOT ship orchestrator HTTP surfaces or in-tree orchestrator worker/command-runner packages that existed solely for `mm-orchestrator`.
- **FR-003** (maps **DOC-REQ-006**, **DOC-REQ-011**): The system MUST persist no orchestrator run/plan-step tables after migration; application code MUST not reference removed models.
- **FR-004** (maps **DOC-REQ-008**, **DOC-REQ-009**): Automated validation MUST run without orchestrator-specific jobs, tests, or broken references.
- **FR-005** (maps **DOC-REQ-010**): Published repo docs and OpenAPI/spec folders MUST reflect the absence of the orchestrator component.
- **FR-006** (maps **DOC-REQ-012**): Regression checks MUST confirm clean unit runs and successful API startup post-removal.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Run MoonMind without orchestrator service (Priority: P1)

An operator starts the project with the documented Compose path and receives a healthy API and core services without any `mm-orchestrator` process.

**Why this priority**: This is the primary outcome of the removal plan.

**Independent Test**: `docker compose up -d` (or documented equivalent) succeeds; `mm-orchestrator` is not among running services; API health responds.

**Acceptance Scenarios**:

1. **Given** a clean environment, **When** the operator starts the default stack, **Then** no orchestrator service is defined as required or started.
2. **Given** the stack is up, **When** the operator hits API health or a core endpoint, **Then** responses succeed without orchestrator routes.

---

### User Story 2 - Remove orchestrator code and persistence (Priority: P2)

Developers and migrations no longer carry orchestrator routers, workflows, models, or tables.

**Why this priority**: Prevents dead code, broken imports, and orphaned schema.

**Independent Test**: Grep/import checks and Alembic upgrade head apply; no `orchestrator_runs`-class tables remain.

**Acceptance Scenarios**:

1. **Given** the codebase after the change, **When** a full test collection runs, **Then** no module fails importing removed orchestrator packages.
2. **Given** a database at previous revision, **When** migrations run forward, **Then** orchestrator tables are dropped in a safe order.

---

### User Story 3 - Align tests, CI, and documentation (Priority: P3)

Contributors see no orchestrator in CI, tests, or architecture docs.

**Why this priority**: Avoids operational confusion and failing pipelines.

**Independent Test**: CI config and docs mention no mandatory orchestrator; `./tools/test_unit.sh` passes.

**Acceptance Scenarios**:

1. **Given** the repo, **When** CI runs on the branch, **Then** no workflow exists solely for orchestrator integration tests unless replaced by an explicit decision (default: removed).
2. **Given** architecture docs, **When** a reader checks Temporal/MoonMind architecture, **Then** `mm-orchestrator` is not described as a required runtime component.

---

### Edge Cases

- Existing databases may contain orchestrator rows; migration MUST drop child/FK-dependent structures before parent tables.
- Other features may have mentioned orchestrator in comments or feature flags; those references MUST be cleaned or redirected without breaking unrelated flows.
- If any shared code lived under `services/orchestrator` but is required elsewhere, it MUST be relocated rather than silently deleted (only if discovered during implementation).

## Key Entities

- **Orchestrator run / plan data** (being removed): Historical persisted runs and steps; must be absent after migration.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of canonical Compose-based startup paths succeed without an orchestrator container in the default profile.
- **SC-002**: Zero failing unit tests attributable to missing orchestrator modules or endpoints after the change set.
- **SC-003**: Schema at migration head contains no orchestrator-specific tables named in the removal plan.
- **SC-004**: Architecture/Temporal docs and removed spec directories contain no stale requirement that operators run `mm-orchestrator`.

## Assumptions

- No new external replacement for in-MoonMind `mm-orchestrator` is in scope; Temporal and other runtimes already cover needed execution paths.
- Data in orchestrator tables may be discarded; archival is out of scope unless required by a separate policy.
