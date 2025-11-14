# Feature Specification: Codex & Spec Kit Tooling Availability

**Feature Branch**: `004-install-codex-spec`  
**Created**: 2025-11-07  
**Status**: Draft  
**Input**: User description: "Codex and Spec Kit: The api_service/Dockerfile must install Codex CLI and GitHub Spec Kit so they are usable by Celery tasks. Additionally, codex should be configured so that the ~/.codex/config.toml contains approval_policy = \"never\" so that it will not get stuck on permissions."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Containerized Codex Access (Priority: P1)

Spec workflow engineers need every Celery-driven Codex phase to run inside the api_service image without manually installing tooling.

**Why this priority**: Missing Codex binaries is the single point of failure for automation runs; packaging the CLI eliminates the most common blocker.

**Independent Test**: Build the image, start a Celery worker from it, trigger a Codex-dependent task, and confirm the run finishes without any additional package install steps.

**Acceptance Scenarios**:

1. **Given** a freshly built automation image, **When** a Celery task invokes `codex --version`, **Then** the command succeeds for the service account without downloading anything at runtime.
2. **Given** a redeployed worker that previously completed runs, **When** a new Codex task begins, **Then** it reuses the packaged Codex CLI with the same version and path as the prior run.

---

### User Story 2 - Spec Kit CLI Availability (Priority: P2)

Spec Kit maintainers need the GitHub Spec Kit CLI preinstalled so discover, submit, and publish phases can execute within Celery jobs.

**Why this priority**: Without the Spec Kit binary, automation cannot orchestrate repository checks or publish results; packaging it keeps workflows deterministic.

**Independent Test**: Start a worker from the updated image and execute the standard Spec Kit smoke test; it should complete end-to-end without fetching additional tooling.

**Acceptance Scenarios**:

1. **Given** the updated image, **When** a Celery task runs the Spec Kit CLI entrypoint, **Then** the binary is on the PATH for the worker user and completes the smoke test script.

---

### User Story 3 - Non-interactive Codex Approvals (Priority: P3)

Operations staff need Codex automation to run without approval prompts so unattended Celery jobs never stall.

**Why this priority**: Spec workflows run in headless environments; a stalled approval prompt can block the entire queue.

**Independent Test**: Trigger a Codex step that would normally request permissions, inspect logs, and confirm the run completes without pausing for approval because the policy is set to "never".

**Acceptance Scenarios**:

1. **Given** the managed Codex config file, **When** a Celery task reaches a Codex command that would request approval, **Then** the command continues automatically and logs that the "never" policy is in effect.
2. **Given** the config file is missing or corrupted, **When** the worker health check runs, **Then** it fails fast with guidance to rebuild the image before any Celery tasks are accepted.

---

### Edge Cases

- The build environment lacks network access to download the Codex or Spec Kit binaries; the process must fail fast with instructions instead of producing a partial image.
- A preexisting `.codex/config.toml` contains other settings; the automation must preserve existing values while guaranteeing `approval_policy = "never"` is present exactly once.
- Celery tasks run under a user profile without a writable home directory; the solution must define where the config lives and ensure tasks still read it consistently.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The api_service automation image MUST include a supported Codex CLI version on the default PATH for the Celery service account so commands succeed without runtime package installs.
- **FR-002**: The same image MUST bundle the GitHub Spec Kit CLI, including any dependencies it requires to execute discover, submit, and publish phases within Celery.
- **FR-003**: Image build scripts MUST install both CLIs at build time and record their versions so release notes can confirm what tooling shipped with each image tag.
- **FR-004**: The build MUST provision a `.codex/config.toml` for the worker user that contains `approval_policy = "never"`, persists across deployments, and is owned so Celery can read it.
- **FR-005**: Worker startup or health checks MUST verify that both CLIs and the managed config file exist; missing artifacts must block the worker from accepting jobs and emit actionable logs.
- **FR-006**: Documentation or runbooks MUST describe how to validate the packaged tooling inside a running container, including commands to confirm CLI presence and the enforced approval policy.

### Key Entities *(include if feature involves data)*

- **Automation Runtime Image**: The container image used by the api_service and Celery workers; it now encapsulates Codex CLI, Spec Kit CLI, and their metadata so every worker is consistent.
- **Codex CLI Configuration Profile**: The `.codex/config.toml` stored in the worker user’s home directory; it defines automation policies such as `approval_policy = "never"` that keep runs non-interactive.
- **Spec Workflow Task**: A Celery-executed step (discover, submit, publish) that depends on both CLIs; it relies on the runtime image and config profile to complete unattended.

## Assumptions

- Celery workers that execute Spec workflows run under a consistent non-root service account with a resolvable home directory.
- Build infrastructure has enough network access or cached artifacts to install Codex CLI and GitHub Spec Kit from trusted sources.
- No additional Codex configuration beyond `approval_policy = "never"` is required for the workflows described; future settings can extend the same managed file.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of Codex-dependent Celery jobs in staging complete without encountering "command not found" or on-demand installation steps during the first week after release.
- **SC-002**: At least 95% of Spec Kit automation runs finish on their first attempt without failures attributed to missing Spec Kit binaries or dependencies.
- **SC-003**: The first 20 Codex automation runs after rollout complete without approval prompts, verified by log review showing the enforced "never" policy each time.
- **SC-004**: Provisioning a new Celery worker from the updated image to a "ready" state takes under 10 minutes because no manual CLI installation or configuration steps remain.
