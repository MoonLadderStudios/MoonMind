# Feature Specification: Gemini CLI Worker

**Feature Branch**: `008-gemini-cli-worker`  
**Created**: 2025-11-30  
**Status**: Draft  
**Input**: User description: "create a spec to implement any missing or changed features from @docs/GeminiCliWorkers.md"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Deploy Dedicated Gemini Worker (Priority: P1)

As a System Operator, I want to deploy a dedicated worker service that handles only Gemini-related tasks, so that Gemini operations do not block general system tasks and can be scaled independently.

**Why this priority**: Critical for system stability and performance isolation.

**Independent Test**: Can be tested by inspecting the running services and verifying a worker is listening specifically on the `gemini` queue and not processing default queue tasks.

**Acceptance Scenarios**:

1. **Given** a configured MoonMind environment, **When** the system starts, **Then** a `celery_gemini_worker` service is running.
2. **Given** the `celery_gemini_worker` is running, **When** inspected, **Then** it is listening on the `gemini` queue.
3. **Given** a task submitted to the `gemini` queue, **When** the worker is active, **Then** it processes the task.

---

### User Story 2 - Persistent Authentication (Priority: P1)

As a System Operator, I want to authenticate the Gemini CLI once using a persistent volume, so that the worker can execute authenticated commands indefinitely without requiring manual setup for each container restart.

**Why this priority**: Essential for automation; prevents the worker from stalling on auth/config setup after restarts.

**Independent Test**: Can be tested by manually setting up the volume, restarting the worker container, and verifying it can still make authenticated/configured API calls.

**Acceptance Scenarios**:

1. **Given** a fresh environment, **When** I create the `gemini_auth_volume` and perform any necessary setup, **Then** the state is saved to the volume.
2. **Given** a configured `gemini_auth_volume`, **When** the `celery_gemini_worker` starts with this volume mounted, **Then** it passes the pre-flight check.
3. **Given** a running worker with the auth volume, **When** a task invokes the Gemini CLI, **Then** the CLI executes successfully without interactive prompts.

---

### User Story 3 - Public Dependency Installation (Priority: P2)

As a Developer, I want the Gemini CLI to be installed from the public npm registry during the image build, so that I can build the project without needing access to private package feeds.

**Why this priority**: Ensures build reproducibility and ease of contribution.

**Independent Test**: Can be tested by building the Docker image with `INSTALL_GEMINI_CLI=true` and verifying the installation log shows a fetch from the public registry.

**Acceptance Scenarios**:

1. **Given** the Docker build context, **When** the image is built with `INSTALL_GEMINI_CLI=true`, **Then** `@google/gemini-cli` is installed from the public npm registry.
2. **Given** the built image, **When** I run `gemini --version`, **Then** it returns a valid version number.

---

### Edge Cases

- What happens if the `gemini_auth_volume` is missing or empty? (Worker should fail fast or run in a limited mode if API key env var is sufficient).
- What happens if the Gemini API Key is invalid? (Tasks should fail gracefully with a clear error).
- What happens if multiple workers try to use the same volume? (Multiple workers will concurrently mount the same `gemini_auth_volume`; CLI must handle concurrent reads).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST define a `gemini` Celery queue in the worker configuration and routing rules.
- **FR-002**: The system MUST provision a persistent Docker volume named `gemini_auth_volume` (or similar) for storing Gemini credentials and config.
- **FR-003**: The `celery_gemini_worker` service MUST mount the `gemini_auth_volume` to the `GEMINI_HOME` directory inside the container.
- **FR-004**: The `celery_gemini_worker` service MUST use a container image that includes the Gemini CLI installed from the public npm registry.
- **FR-005**: The worker startup process MUST perform a pre-flight check (e.g., verifying CLI version and auth status) and exit with a non-zero status code if the environment is invalid.
- **FR-006**: The system MUST allow the `celery_gemini_worker` service to be scaled, with all replicas sharing the same `gemini_auth_volume`.

### Key Entities *(include if feature involves data)*

- **Gemini Worker Service**: A specialized Celery worker instance dedicated to the `gemini` queue.
- **Gemini Auth Volume**: A persistent storage volume holding CLI configuration and auth state.
- **Gemini Queue**: A logical job queue for routing Gemini-specific tasks.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The `celery_gemini_worker` successfully starts and connects *only* to the `gemini` queue (verified by logs).
- **SC-002**: Gemini CLI commands executed by the worker succeed without user interaction 100% of the time when valid credentials are present.
- **SC-003**: Configuration/Auth state persists across container restarts/re-deployments.
- **SC-004**: Docker build successfully installs the Gemini CLI from public sources.