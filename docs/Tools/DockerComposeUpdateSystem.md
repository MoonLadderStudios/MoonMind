# Docker Compose Deployment Update System

Status: Desired State  
Owners: MoonMind Engineering  
Last Updated: 2026-04-25  
Related: `docs/UI/SettingsTab.md`, `docs/Tasks/SkillAndPlanContracts.md`, `docs/Temporal/TemporalArchitecture.md`, `docs/Temporal/ManagedAndExternalAgentExecutionModel.md`, `docs/Security/ProviderProfiles.md`, `docs/Security/SecretsSystem.md`

---

## 1. Purpose

This document defines MoonMind's desired-state design for updating a Docker Compose-managed MoonMind deployment from Mission Control.

The system gives an administrator a simple **Settings → Operations** surface where they choose the target MoonMind image reference, start an audited update operation, watch progress, and inspect the resulting before/after deployment state.

The update operation is executed as a privileged MoonMind operation, not as arbitrary user-provided shell input. The backend resolves the request into a typed executable tool invocation that performs the equivalent of:

```bash
docker compose pull
docker compose up -d --remove-orphans --wait
```

The system may use an ephemeral updater container or a privileged deployment-control worker to run the Docker commands. That runner is an implementation detail. The operator-facing control is the **target MoonMind image** to deploy.

---

## 2. Summary

The desired-state model is:

1. Mission Control exposes a **Deployment Update** card under **Settings → Operations**.
2. The operator chooses a target Docker image tag or digest from an allowlisted MoonMind image repository.
3. The backend creates an audited deployment-update run.
4. The run invokes a typed executable tool, `deployment.update_compose_stack`.
5. The tool updates the stack's desired image reference, runs Docker Compose pull/up, waits for services, verifies health, and writes structured artifacts.
6. MoonMind records before/after image IDs, service states, command output, health-check results, operator identity, and reason.
7. The updater runner terminates after verification.
8. The Operations page shows current deployment state and recent deployment-update actions.

This system is part of MoonMind's operational control plane. It is not an agent skill, not a runtime-native command, and not a general shell-command execution surface.

---

## 3. Terminology

### Compose stack target

A configured Docker Compose deployment that MoonMind is allowed to operate.

Example:

```json
{
  "stack": "moonmind",
  "projectName": "moonmind",
  "composeProjectDir": "/srv/MoonMind",
  "composeFiles": ["docker-compose.yaml"],
  "envFile": ".env.deploy"
}
```

### Target image

The MoonMind application image that the administrator wants the stack to run.

Examples:

```text
ghcr.io/moonladderstudios/moonmind:stable
ghcr.io/moonladderstudios/moonmind:20260425.1234
ghcr.io/moonladderstudios/moonmind@sha256:...
```

### Updater runner image

The internal image used to execute Docker client commands, such as `docker:29-cli` or a MoonMind-maintained updater image.

The updater runner image is not normally user-selectable. It is controlled by deployment configuration because it has access to privileged host Docker capabilities.

### Deployment update run

An audited operation that changes a configured Compose stack from one desired image reference to another.

### Deployment-control worker

A trusted worker or maintenance runtime with the capability to access the host Docker daemon and operate allowlisted Compose stacks.

---

## 4. Goals and non-goals

## 4.1 Goals

The Docker Compose deployment update system must:

1. let an administrator update MoonMind from Mission Control without SSHing into the host
2. expose the target MoonMind image tag or digest as the main operator choice
3. keep the updater runner image internal and deployment-controlled
4. perform updates through a typed executable tool contract
5. avoid arbitrary shell input from users
6. enforce admin authorization and deployment policy
7. serialize updates so only one update operates on a stack at a time
8. write durable before/after state and logs as artifacts
9. verify service health before marking the run successful
10. support both changed-service recreation and force-recreate-all modes
11. preserve enough audit data to explain who changed the deployment, when, why, and to what image

## 4.2 Non-goals

The system does not aim to:

1. expose a general-purpose Docker UI
2. expose a general-purpose shell runner
3. let non-admin users control Docker images or Compose flags
4. let operators choose arbitrary updater runner images by default
5. replace full GitOps or Kubernetes deployment systems
6. manage non-allowlisted stacks or host paths
7. treat deployment updates as agent instruction skills
8. silently roll back without an explicit, auditable policy

---

## 5. Core invariants

The following rules are fixed.

1. Deployment updates are executable MoonMind tool invocations.
2. The UI selects the **target deployment image**, not the privileged updater runner image.
3. The updater runner image is deployment configuration and must be allowlisted.
4. Compose stack targets are allowlisted by name and path.
5. The update tool must not accept arbitrary shell snippets.
6. The target image repository must be allowlisted.
7. Tags are allowed, but the resolved digest must be recorded when available.
8. Digest-pinned image references are preferred for reproducible production updates.
9. Only one update may run per Compose stack at a time.
10. Before/after service state must be captured.
11. Command output and verification output must be written to artifacts.
12. The run must fail closed when verification cannot prove the desired state.
13. The operation must remain auditable even if MoonMind services restart during the update.

---

## 6. Settings → Operations UX

## 6.1 Placement

Mission Control exposes the deployment update surface under:

```text
Settings → Operations
```

The Operations subsection remains the home for administrative and system-control surfaces. Deployment update belongs beside worker pause/resume, drain/quiesce, and operational audit controls.

## 6.2 Deployment Update card

The Operations page should include a **Deployment Update** card with the following sections.

### Current deployment

Shows:

- stack name
- Compose project name
- current configured image reference
- current running image ID or digest, when available
- current MoonMind version/build identifier, when available
- service health summary
- last deployment-update run and result

### Update target

Allows the administrator to choose:

- allowlisted image repository
- tag selector, such as `stable`, `latest`, or recent release tags
- custom tag, if policy allows
- custom digest, if policy allows

The UI should prefer digest-pinned or release-tagged updates. Mutable tags such as `latest` should show a warning that the tag may resolve differently over time.

### Update mode

Supports:

- **Restart changed services**: pull images and recreate services whose image or configuration changed
- **Force recreate all services**: pull images and recreate every service in the allowlisted Compose stack

The default mode is **Restart changed services**.

### Options

Supports policy-controlled options:

- remove orphan containers
- wait for services to become healthy
- run post-update smoke check
- prune old images after success
- pause or drain new task work before update
- resume work after successful update

### Reason and confirmation

Before execution, the operator must provide a reason and confirm the target state.

The confirmation modal should show:

- current image reference
- requested target image reference
- update mode
- stack name
- services expected to be affected
- warning for mutable tags
- warning that services may restart

## 6.3 Recent actions

The Operations page should show recent deployment-update runs with:

- status
- requested image
- resolved digest
- operator
- reason
- started/completed timestamps
- link to run detail
- link to logs artifact
- before/after summary

---

## 7. API contract

## 7.1 Submit deployment update

The UI submits a typed request to the backend.

```http
POST /api/v1/operations/deployment/update
```

Example request:

```json
{
  "stack": "moonmind",
  "image": {
    "repository": "ghcr.io/moonladderstudios/moonmind",
    "reference": "20260425.1234"
  },
  "mode": "changed_services",
  "removeOrphans": true,
  "wait": true,
  "runSmokeCheck": true,
  "pauseWork": false,
  "pruneOldImages": false,
  "reason": "Update to the latest tested MoonMind build"
}
```

Example response:

```json
{
  "deploymentUpdateRunId": "depupd_01HV...",
  "taskId": "task_01HV...",
  "workflowId": "MoonMind.DeploymentUpdate/moonmind/01HV...",
  "status": "QUEUED"
}
```

## 7.2 Read current deployment state

```http
GET /api/v1/operations/deployment/stacks/moonmind
```

Example response:

```json
{
  "stack": "moonmind",
  "projectName": "moonmind",
  "configuredImage": "ghcr.io/moonladderstudios/moonmind:20260425.1234",
  "runningImages": [
    {
      "service": "api",
      "image": "ghcr.io/moonladderstudios/moonmind:20260425.1234",
      "imageId": "sha256:...",
      "digest": "sha256:..."
    }
  ],
  "services": [
    {
      "name": "api",
      "state": "running",
      "health": "healthy"
    }
  ],
  "lastUpdateRunId": "depupd_01HV..."
}
```

## 7.3 List allowed image targets

```http
GET /api/v1/operations/deployment/image-targets?stack=moonmind
```

Example response:

```json
{
  "stack": "moonmind",
  "repositories": [
    {
      "repository": "ghcr.io/moonladderstudios/moonmind",
      "allowedReferences": ["stable", "latest"],
      "recentTags": ["20260425.1234", "20260424.0901"],
      "digestPinningRecommended": true
    }
  ]
}
```

The backend may obtain recent tags from a registry integration, from deployment metadata, or from a locally configured release catalog.

---

## 8. Executable tool contract

## 8.1 Tool name

Deployment update is represented as a typed executable tool:

```text
deployment.update_compose_stack
```

This tool is a privileged system operation and must require deployment-control capabilities.

## 8.2 ToolDefinition shape

Representative registry entry:

```yaml
name: "deployment.update_compose_stack"
version: "1.0.0"
type: "skill"
description: "Update an allowlisted Docker Compose stack to a desired MoonMind image reference."
inputs:
  schema:
    type: object
    required:
      - stack
      - image
      - reason
    properties:
      stack:
        type: string
        enum: ["moonmind"]
      image:
        type: object
        required: ["repository", "reference"]
        properties:
          repository:
            type: string
          reference:
            type: string
          resolvedDigest:
            type: string
      mode:
        type: string
        enum: ["changed_services", "force_recreate"]
        default: "changed_services"
      removeOrphans:
        type: boolean
        default: true
      wait:
        type: boolean
        default: true
      runSmokeCheck:
        type: boolean
        default: true
      pauseWork:
        type: boolean
        default: false
      pruneOldImages:
        type: boolean
        default: false
      reason:
        type: string
outputs:
  schema:
    type: object
    required:
      - status
      - stack
      - requestedImage
      - updatedServices
      - runningServices
    properties:
      status:
        type: string
        enum: ["SUCCEEDED", "FAILED", "PARTIALLY_VERIFIED"]
      stack:
        type: string
      requestedImage:
        type: string
      resolvedDigest:
        type: string
      updatedServices:
        type: array
        items:
          type: string
      runningServices:
        type: array
        items:
          type: object
      beforeStateArtifactRef:
        type: string
      afterStateArtifactRef:
        type: string
      commandLogArtifactRef:
        type: string
      verificationArtifactRef:
        type: string
executor:
  activity_type: "mm.tool.execute"
  selector:
    mode: "by_capability"
requirements:
  capabilities:
    - "deployment_control"
    - "docker_admin"
policies:
  timeouts:
    start_to_close_seconds: 900
    schedule_to_close_seconds: 1800
  retries:
    max_attempts: 1
    non_retryable_error_codes:
      - "INVALID_INPUT"
      - "PERMISSION_DENIED"
      - "POLICY_VIOLATION"
      - "DEPLOYMENT_LOCKED"
security:
  allowed_roles: ["admin"]
```

## 8.3 Representative plan node

A deployment update run may be represented as a normal MoonMind plan node:

```json
{
  "id": "update-moonmind-deployment",
  "title": "Update MoonMind deployment",
  "tool": {
    "type": "skill",
    "name": "deployment.update_compose_stack",
    "version": "1.0.0"
  },
  "inputs": {
    "stack": "moonmind",
    "image": {
      "repository": "ghcr.io/moonladderstudios/moonmind",
      "reference": "20260425.1234"
    },
    "mode": "changed_services",
    "removeOrphans": true,
    "wait": true,
    "runSmokeCheck": true,
    "reason": "Update to the latest tested MoonMind build"
  }
}
```

---

## 9. Desired state storage

## 9.1 Compose image parameterization

The Docker Compose stack should parameterize the MoonMind image reference through an environment variable rather than requiring YAML rewrites.

Example:

```yaml
services:
  api:
    image: ${MOONMIND_IMAGE:-ghcr.io/moonladderstudios/moonmind:stable}
```

The update tool writes the desired image reference to an allowlisted deployment env file:

```env
MOONMIND_IMAGE=ghcr.io/moonladderstudios/moonmind:20260425.1234
```

Digest-pinned example:

```env
MOONMIND_IMAGE=ghcr.io/moonladderstudios/moonmind@sha256:...
```

## 9.2 Desired-state persistence rule

The requested target image must be persisted before Compose is brought up so that the desired state survives service restarts.

The persisted desired state should record:

- stack
- image repository
- requested reference
- resolved digest, when available
- operator
- reason
- created timestamp
- source run ID

## 9.3 Mutable tag rule

When the requested reference is a mutable tag, the system should resolve and record the digest before or during the update when the registry makes that possible.

The UI may allow mutable tags, but audit records must distinguish:

```text
requested: ghcr.io/moonladderstudios/moonmind:latest
resolved:  ghcr.io/moonladderstudios/moonmind@sha256:...
```

---

## 10. Execution lifecycle

The deployment update workflow follows this lifecycle.

## 10.1 Validate request

The backend validates:

1. caller is authorized as an administrator
2. stack name is allowlisted
3. image repository is allowlisted
4. image reference is syntactically valid
5. update mode is permitted by policy
6. reason is present
7. requested options are permitted

## 10.2 Acquire deployment lock

The workflow acquires a lock for the stack.

If another update is running, the request fails with `DEPLOYMENT_LOCKED` or remains queued according to policy.

## 10.3 Capture before state

The tool captures and stores:

- Compose config summary
- service list
- container IDs
- image references
- image IDs and digests, when available
- service state and health
- relevant environment state

The before state is written to an immutable artifact.

## 10.4 Persist desired image

The tool writes the desired image reference into the allowlisted deployment env file or equivalent deployment-state store.

The tool must not edit arbitrary files selected by the caller.

## 10.5 Pull images

The tool runs the equivalent of:

```bash
docker compose pull --policy always --ignore-buildable
```

The exact flags are implementation-specific and policy-controlled. Pull output is captured in the command log artifact.

## 10.6 Recreate services

For `mode = changed_services`, the tool runs the equivalent of:

```bash
docker compose up -d --remove-orphans --wait
```

For `mode = force_recreate`, the tool runs the equivalent of:

```bash
docker compose up -d --force-recreate --remove-orphans --wait
```

If `removeOrphans` or `wait` are disabled by policy, the command is adjusted accordingly.

## 10.7 Verify desired state

The tool verifies:

1. expected services are running
2. services report healthy when health checks exist
3. running image IDs match the requested target or resolved digest where applicable
4. post-update smoke checks pass when requested
5. no unexpected services remain when orphan removal is enabled

Verification output is written to an immutable artifact.

## 10.8 Capture after state

The tool captures the same state collected before the update and stores it as an after-state artifact.

## 10.9 Release lock and report result

The workflow releases the deployment lock and writes a structured result containing:

- final status
- updated services
- running services
- requested image
- resolved digest
- before artifact ref
- after artifact ref
- command log artifact ref
- verification artifact ref

---

## 11. Updater runner execution model

MoonMind supports two implementation modes.

## 11.1 Privileged deployment-control worker

A trusted worker with `deployment_control` and `docker_admin` capabilities executes Compose commands directly on the deployment host.

This mode is simple when the worker already runs on the host that owns the Docker daemon.

## 11.2 Ephemeral updater container

A privileged worker starts a one-shot updater container that mounts:

- the host Docker socket
- the allowlisted Compose project directory
- any required deployment env file

Representative command shape:

```bash
docker run --rm \
  --name moonmind-updater \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "$MOONMIND_DIR:$MOONMIND_DIR" \
  -w "$MOONMIND_DIR" \
  docker:29-cli \
  sh -euc 'docker compose pull --policy always --ignore-buildable && docker compose up -d --remove-orphans --wait'
```

The updater container is ephemeral and terminates after the update and verification steps complete.

## 11.3 Runner image policy

The updater runner image must be controlled by deployment configuration.

The Operations UI must not normally ask the operator to choose the runner image because the runner has privileged Docker access.

---

## 12. Verification model

## 12.1 Compose-level verification

The system verifies Compose state using:

- `docker compose ps`
- `docker compose images`
- container inspect data
- service health status
- image IDs and repo digests where available

## 12.2 Application-level verification

The system should optionally run a MoonMind smoke check after Compose-level health succeeds.

The smoke check may include:

- API health endpoint
- frontend reachability
- Temporal worker registration or poller health
- database connectivity, if applicable
- basic task submission readiness, if safe

## 12.3 Verification failure rule

If services start but verification cannot prove the requested desired state, the run must not be marked `SUCCEEDED`.

The result should be one of:

- `FAILED`
- `PARTIALLY_VERIFIED`

The UI should show the exact failed check and link to artifacts.

---

## 13. Security and policy model

## 13.1 Authorization

Only administrators may start deployment updates.

Deployment update permissions should be distinct from ordinary task-submission permissions.

## 13.2 Allowlisted stacks

The backend stores allowlisted stack targets. Caller-provided paths are rejected.

Example deployment policy:

```json
{
  "stacks": {
    "moonmind": {
      "projectName": "moonmind",
      "composeProjectDir": "/srv/MoonMind",
      "composeFiles": ["docker-compose.yaml"],
      "envFile": ".env.deploy",
      "allowedRepositories": [
        "ghcr.io/moonladderstudios/moonmind"
      ],
      "allowMutableTags": true,
      "allowCustomDigest": true,
      "allowForceRecreate": true
    }
  }
}
```

## 13.3 No arbitrary shell

The update tool receives typed inputs and assembles known command forms from policy.

The tool must reject:

- arbitrary shell commands
- unapproved Compose files
- unapproved host paths
- unapproved image repositories
- unapproved updater runner images
- unrecognized flags

## 13.4 Docker socket risk

Any runtime with Docker socket access is trusted infrastructure.

MoonMind must not expose Docker socket access to ordinary task runtimes, agent runtimes, repo workspaces, or user-authored tools.

## 13.5 Secret handling

Registry credentials, if needed, are resolved through the existing secrets/provider-profile model.

Secrets must not be embedded in:

- image reference text
- UI form defaults
- command logs
- deployment env files unless explicitly designed as secret-bearing files with appropriate protections

---

## 14. Audit and artifacts

## 14.1 Audit record

Every deployment update run records:

- run ID
- workflow ID
- task ID, if applicable
- stack
- operator identity
- operator role
- reason
- requested image reference
- resolved digest, when available
- update mode
- options
- start timestamp
- completion timestamp
- final status
- failure reason, when applicable

## 14.2 Required artifacts

Every run writes:

- before state artifact
- command log artifact
- verification artifact
- after state artifact

## 14.3 Artifact redaction

Command logs and state captures must redact:

- secrets
- auth tokens
- registry credentials
- environment variables marked sensitive

## 14.4 Operations display

The Operations page shows a human-readable summary and links to artifacts. Raw command logs should be available only to users with operational-admin permissions.

---

## 15. Failure and rollback semantics

## 15.1 Failure behavior

The system fails fast on:

- invalid input
- authorization failure
- policy violation
- unavailable deployment lock
- Compose config validation failure
- image pull failure
- service recreation failure
- verification failure

## 15.2 Retry behavior

Deployment updates should not use automatic multi-attempt retries by default.

A failed update may leave services partially changed. Re-running the update is an explicit operator action that uses the same audited path.

## 15.3 Rollback behavior

Rollback is an explicit deployment update to a previous image reference.

The UI may offer a **Roll back to previous image** action when before-state artifacts contain enough information to construct a safe target image reference.

Rollback still requires:

- admin authorization
- reason
- confirmation
- deployment lock
- before/after artifacts
- verification

The system must not silently roll back unless a separately documented policy explicitly enables automatic rollback.

---

## 16. Interaction with task execution

Deployment update is executable operational work and should be represented through MoonMind's tool and plan system.

The update tool may be invoked by:

- direct Operations UI action
- scheduled maintenance workflow
- admin-authored operational task
- future release-management workflow

An agent may assist by explaining the update result or summarizing logs, but the privileged update itself is performed by the typed deployment tool.

Representative operational sequence:

```text
1. Pause or drain new task work, if requested.
2. Update MoonMind deployment.
3. Verify Compose and application health.
4. Resume task work, if it was paused.
5. Summarize before/after state and artifacts.
```

---

## 17. Interaction with Settings information architecture

The Settings page remains a single operator-facing location for configuration and administrative controls.

Deployment update belongs in the **Operations** subsection because it is a system-control surface. It should not become a top-level navigation item unless the broader Settings architecture is intentionally revisited.

The Settings page should continue to use subsection routing such as:

```text
/tasks/settings?section=operations
```

---

## 18. UI copy recommendations

## 18.1 Card title

```text
Deployment Update
```

## 18.2 Card description

```text
Update the MoonMind Docker Compose deployment by selecting the target image tag or digest. MoonMind will pull the image, recreate affected services, verify health, and record an audit trail.
```

## 18.3 Mutable tag warning

```text
This tag is mutable. MoonMind will record the resolved digest when available, but future uses of the same tag may deploy a different image.
```

## 18.4 Force recreate warning

```text
Force recreate restarts every service in the stack, even if its image or configuration has not changed.
```

## 18.5 Confirmation button

```text
Update MoonMind Deployment
```

---

## 19. Observability

The deployment update workflow should expose progress states suitable for Mission Control:

```text
QUEUED
VALIDATING
LOCK_WAITING
CAPTURING_BEFORE_STATE
PERSISTING_DESIRED_STATE
PULLING_IMAGES
RECREATING_SERVICES
VERIFYING
CAPTURING_AFTER_STATE
SUCCEEDED
FAILED
PARTIALLY_VERIFIED
```

Each state should include a small progress message. Detailed command output belongs in artifacts, not workflow history or ordinary UI state.

---

## 20. Locked decisions

This document locks the following design decisions.

1. The deployment update UI lives under **Settings → Operations**.
2. The operator selects the target MoonMind image, not the updater runner image.
3. Deployment update is implemented as a typed executable tool named `deployment.update_compose_stack`.
4. The tool is admin-only and capability-gated.
5. Stack names, Compose paths, image repositories, and runner images are allowlisted.
6. The tool never accepts arbitrary shell input.
7. Desired image state is persisted before Compose is brought up.
8. Before/after state and command logs are written as artifacts.
9. Verification is required before a run is marked successful.
10. Rollback is an explicit audited update to a previous target image.
11. Docker socket access is restricted to trusted deployment-control infrastructure.
12. Mutable tags are allowed only with explicit audit of the resolved digest when available.

---

## 21. Summary

MoonMind should expose Docker Compose deployment updates as a small, safe, audited Operations workflow.

The operator experience is simple: choose the target image, choose the update mode, provide a reason, and start the update. The backend handles policy, locking, Docker Compose execution, verification, and artifacts.

The architectural boundary is equally important: this is an executable deployment-control tool, not an agent skill and not an arbitrary shell surface. That boundary keeps the UX simple while preserving the safety required for a feature that can restart and replace the MoonMind deployment itself.
