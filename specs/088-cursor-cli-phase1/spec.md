# Feature Specification: Cursor CLI Phase 1 — Binary Integration

**Feature Branch**: `088-cursor-cli-phase1`
**Created**: 2026-03-20
**Status**: Draft
**Input**: User description: "Implement Phase 1 from docs/ManagedAgents/CursorCli.md"
**Source Contract**: `docs/ManagedAgents/CursorCli.md` (Section 13, Phase 1; Sections 2–3)

## Source Document Requirements

| Requirement ID | Source Citation | Requirement Summary |
|---------------|----------------|---------------------|
| DOC-REQ-001 | CursorCli.md §2 "Docker Image Strategy" | Cursor CLI binary MUST be installed in the worker Docker image so managed runtime workers can invoke `agent` without manual setup |
| DOC-REQ-002 | CursorCli.md §3 "Auth Script" | A provisioning script `tools/auth-cursor-volume.sh` MUST exist supporting `--api-key`, `--login`, and `--check` modes for Cursor CLI credential management |
| DOC-REQ-003 | CursorCli.md §13 Phase 1, item 3 | The `agent status` and `agent -p` commands MUST function correctly inside the worker container environment |
| DOC-REQ-004 | CursorCli.md §13 Phase 1, item 4 | The `CURSOR_API_KEY` environment variable MUST be documented in `.env.example` with clear usage instructions |
| DOC-REQ-005 | CursorCli.md §2 "Auto-Update Consideration" | Auto-update behavior MUST be addressed: either disabled for deterministic builds or documented as accepted behavior |
| DOC-REQ-006 | CursorCli.md §3 "Authentication Modes" | The system MUST support API key authentication via `CURSOR_API_KEY` as the primary mode for managed runtime execution |
| DOC-REQ-007 | CursorCli.md §9 "Docker Compose Integration" | Docker Compose MUST define a `cursor_auth_volume` and `cursor-auth-init` service following existing auth-init patterns |

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Worker Image Includes Cursor CLI (Priority: P1)

A MoonMind operator builds the worker Docker image and the Cursor CLI binary (`agent`) is available on the PATH without any additional manual steps. The operator can verify this by running `agent --version` or `agent status` inside any worker container.

**Why this priority**: Without the binary installed, no other Cursor CLI functionality can work. This is the foundational prerequisite.

**Independent Test**: Build the Docker image and run `docker run --rm <image> agent --version` — the command must succeed and return a version string.

**Acceptance Scenarios**:

1. **Given** a freshly built worker Docker image, **When** `agent --version` is executed inside a container, **Then** it returns a valid Cursor CLI version string and exits with code 0
2. **Given** a freshly built worker Docker image, **When** `which agent` is executed, **Then** it returns a valid path (e.g. `/usr/local/bin/agent`)

---

### User Story 2 - Auth Provisioning Script (Priority: P1)

An operator provisions Cursor CLI authentication credentials using `tools/auth-cursor-volume.sh`. The script supports setting an API key, interactive login, and verifying current auth status — matching the UX of existing auth scripts for Gemini, Claude, and Codex.

**Why this priority**: Authentication is required before any headless execution. Operators need a consistent provisioning path.

**Independent Test**: Run `tools/auth-cursor-volume.sh --check` against a pre-configured environment — the script must report the current auth state without errors.

**Acceptance Scenarios**:

1. **Given** a valid `CURSOR_API_KEY`, **When** the operator runs `tools/auth-cursor-volume.sh --api-key`, **Then** the key is stored in the appropriate location and `agent status` reports authenticated
2. **Given** no credentials, **When** the operator runs `tools/auth-cursor-volume.sh --check`, **Then** the script reports unauthenticated status clearly
3. **Given** an existing auth volume, **When** the operator runs `tools/auth-cursor-volume.sh --login`, **Then** interactive login is initiated inside a container

---

### User Story 3 - Headless Execution Verification (Priority: P2)

A developer or CI pipeline verifies that the Cursor CLI runs headless tasks successfully inside the worker container by executing a simple print-mode command.

**Why this priority**: Validates the end-to-end runtime stack (binary + auth + headless mode) before higher-level adapter integration occurs in Phase 2.

**Independent Test**: Run `agent -p "echo hello" --output-format json` inside the container with valid credentials and verify it produces structured JSON output.

**Acceptance Scenarios**:

1. **Given** a container with Cursor CLI installed and `CURSOR_API_KEY` set, **When** `agent -p "say hello" --output-format json` is executed, **Then** the output contains valid JSON with a result
2. **Given** a container with Cursor CLI installed and no credentials, **When** `agent -p "say hello"` is executed, **Then** the command fails with a clear authentication error (not a crash)

---

### User Story 4 - Docker Compose Volume Setup (Priority: P2)

A MoonMind operator runs `docker compose up` and the Cursor auth volume and init service are created automatically, following the same pattern as Gemini, Claude, and Codex auth volumes.

**Why this priority**: Prepares the infrastructure needed for Phase 2 (adapter wiring) and Phase 3 (auth profiles).

**Independent Test**: Run `docker compose up cursor-auth-init` and verify the volume exists with correct permissions.

**Acceptance Scenarios**:

1. **Given** the updated `docker-compose.yaml`, **When** `docker compose up cursor-auth-init` is run, **Then** the `cursor_auth_volume` is created with UID 1000 ownership and mode 0775
2. **Given** the updated `docker-compose.yaml`, **When** the sandbox worker starts, **Then** the cursor auth volume is mounted at `/home/app/.cursor`

---

### Edge Cases

- What happens when the Cursor CLI installer URL is unreachable during Docker build? → Build should fail fast with a clear error.
- What happens when `CURSOR_API_KEY` is set but invalid? → `agent status` and `agent -p` should report an auth error, not crash.
- What happens when the worker image already has a different version of `agent` on the PATH? → The Cursor CLI `agent` binary must not conflict with other binaries; may need a unique name or explicit PATH ordering.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST install the Cursor CLI binary (`agent`) during Docker image build so it is available on the PATH in all worker containers (DOC-REQ-001)
- **FR-002**: System MUST provide a `tools/auth-cursor-volume.sh` script with `--api-key`, `--login`, and `--check` modes matching the interface pattern of existing auth scripts (DOC-REQ-002)
- **FR-003**: The `agent status` command MUST work correctly inside the worker container to verify authentication (DOC-REQ-003)
- **FR-004**: The `agent -p` command MUST execute headless tasks inside the worker container when valid credentials are provided (DOC-REQ-003)
- **FR-005**: The `.env.example` file MUST document `CURSOR_API_KEY` with usage instructions (DOC-REQ-004)
- **FR-006**: Auto-update behavior MUST be documented and handled appropriately for deterministic builds (DOC-REQ-005)
- **FR-007**: API key authentication via `CURSOR_API_KEY` MUST be the primary supported authentication mode (DOC-REQ-006)
- **FR-008**: Docker Compose MUST define `cursor_auth_volume` and a `cursor-auth-init` service (DOC-REQ-007)
- **FR-009**: The Cursor CLI `agent` binary MUST NOT conflict with existing binaries on the PATH (binary naming collision avoidance)

Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests.

### Key Entities

- **Cursor CLI binary** (`agent`): The command-line tool downloaded from `cursor.com/install`, installed during Docker build
- **Auth provisioning script** (`tools/auth-cursor-volume.sh`): Shell script managing Cursor CLI credentials in Docker volumes
- **Auth volume** (`cursor_auth_volume`): Docker named volume storing persistent Cursor CLI state at `/home/app/.cursor`

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Worker Docker image build completes successfully with Cursor CLI installed and `agent --version` returns a valid version
- **SC-002**: All three auth script modes (`--api-key`, `--login`, `--check`) execute without errors
- **SC-003**: A headless `agent -p` command produces valid output when run inside the container with proper credentials
- **SC-004**: `CURSOR_API_KEY` is present and documented in `.env.example`
- **SC-005**: Docker Compose `cursor-auth-init` service creates the volume with correct ownership (UID 1000, mode 0775)
- **SC-006**: Existing worker functionality is not regressed — no binary conflicts, no Dockerfile build breakage
