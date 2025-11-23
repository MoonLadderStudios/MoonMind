# Feature Specification: Integration of Gemini CLI into Orchestrator and Worker Environments

**Feature Branch**: `006-add-gemini-cli`
**Created**: 2025-11-23
**Status**: Draft
**Input**: User description: "Gemini CLI: Gemini CLI should be added to my Dockerfile so that Gemini can be used both for the orchestrator to convert natural language into actions and so that the celery workers can use Gemini to execute tasks"

## User Scenarios & Testing

### User Story 1 - Orchestrator Natural Language Processing (Priority: P1)

The orchestrator needs to process natural language commands and convert them into actionable steps. By having the Gemini CLI available, the orchestrator can offload this cognitive processing to the Gemini model via a command-line interface.

**Why this priority**: This is a foundational capability for the orchestrator to function as an intelligent agent.

**Independent Test**: 
1. Access the orchestrator container shell.
2. Execute a Gemini CLI command with a sample natural language prompt.
3. Verify that the output is a structured response (e.g., JSON) describing the action.

**Acceptance Scenarios**:

1. **Given** the orchestrator container is running, **When** the `gemini` command is invoked with `--version` (or equivalent), **Then** the version number is displayed, confirming installation.
2. **Given** the orchestrator container is running, **When** a text prompt is piped to the Gemini CLI, **Then** a coherent text response is returned from the model.

---

### User Story 2 - Celery Worker Task Execution (Priority: P1)

Celery workers execute asynchronous tasks, some of which require AI reasoning or content generation. Providing the Gemini CLI ensures these workers can directly leverage the Gemini model to complete these tasks.

**Why this priority**: Enables the background processing system to handle AI-driven workloads.

**Independent Test**:
1. Access the celery worker container shell.
2. Execute a Gemini CLI command to summarize a text or generate code.
3. Verify the output is correct.

**Acceptance Scenarios**:

1. **Given** the celery worker container is running, **When** the `gemini` command is invoked, **Then** it executes without "command not found" errors.
2. **Given** a background task requires code generation, **When** the worker invokes the Gemini CLI with the spec, **Then** code is generated and returned.

### Edge Cases

- **Network Connectivity**: What happens if the container cannot reach the Gemini API endpoint? The CLI should report a connection error, not hang indefinitely.
- **Authentication**: What happens if the API key is missing? The CLI should exit with a clear authentication error.

## Requirements

### Functional Requirements

- **FR-001**: The Gemini CLI tool MUST be installed in the Docker image(s) used by the Orchestrator service.
- **FR-002**: The Gemini CLI tool MUST be installed in the Docker image(s) used by the Celery Worker service.
- **FR-003**: The Gemini CLI MUST be executable by the service user (non-root) running the processes.
- **FR-004**: The Docker environment MUST be configured to pass necessary environment variables (e.g., `GEMINI_API_KEY`, `GOOGLE_API_KEY`) to the container to support CLI authentication.
- **FR-005**: The installed Gemini CLI version MUST be compatible with the existing Python environment (if Python-based) or system libraries.

### Key Entities

- **Gemini CLI**: The command-line interface tool used to interact with the Gemini large language model.
- **Orchestrator Container**: The Docker container hosting the main application logic/orchestrator.
- **Celery Worker Container**: The Docker container hosting the asynchronous task workers.

## Success Criteria

### Measurable Outcomes

- **SC-001**: The `gemini` command is present in the system `$PATH` of both Orchestrator and Celery Worker containers.
- **SC-002**: Execution of `gemini --help` (or standard help flag) returns status code 0 in both environments.
- **SC-003**: A test prompt sent to the Gemini CLI from within the container returns a valid response in under 10 seconds (assuming standard network latency).

### Assumptions

- The Gemini CLI is a distinct installable tool (binary or package) that can be added via standard package managers (pip, apt, or curl download).
- The base operating system of the Docker images is Linux-based (e.g., Debian, Alpine, or distroless with shell).
- Users have valid API keys to use with the Gemini CLI.