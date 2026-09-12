# Docker Compose Deployment Update System

Status: Desired State  
Owners: MoonMind Engineering  
Last Updated: 2026-09-12
Related: `docs/UI/SettingsTab.md`, `docs/Workflows/SkillAndPlanContracts.md`, `docs/Temporal/TemporalArchitecture.md`, `docs/Temporal/ManagedAndExternalAgentExecutionModel.md`, `docs/Security/ProviderProfiles.md`, `docs/Security/SecretsSystem.md`

---

## 1. Purpose

This document defines MoonMind's desired-state design for updating a Docker Compose-managed MoonMind deployment from the dashboard.

The system gives an administrator a simple **Settings → Operations** surface where they choose the target MoonMind image reference, start an audited update operation, watch progress, and inspect the resulting before/after deployment state.

The update operation is executed as a privileged MoonMind operation, not as arbitrary user-provided shell input. The backend resolves the request into a typed executable tool invocation that performs the equivalent of:

```bash
docker compose pull
docker compose up -d --remove-orphans --wait
```

The existing deployment-control worker submits one durable, ephemeral updater that executes from the selected immutable image. The updater can replace the service that submitted it and survives that replacement. All seven normal worker fleets use the same release identity. The operator-facing control is the **target MoonMind image** to deploy.

---

## 2. Summary

The desired-state model is:

1. The dashboard exposes a **Deployment Update** card under **Settings → Operations**.
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

The same allowlisted, digest-pinned MoonMind image selected for the release. It supplies the portable release controller, Docker client, and image-owned Compose definition.

The updater has no independent image override. Deployment policy controls its repository and privileged Docker access.

### Deployment update run

An audited operation that changes a configured Compose stack from one desired image reference to another.

### Deployment-control worker

A trusted worker or maintenance runtime with the capability to access the host Docker daemon and operate allowlisted Compose stacks.

---

## 4. Goals and non-goals

## 4.1 Goals

The Docker Compose deployment update system must:

1. let an administrator update MoonMind from the dashboard without SSHing into the host
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
3. The updater uses the target image digest, subject to the deployment repository allowlist.
4. Compose stack targets are allowlisted by name and path.
5. The update tool must not accept arbitrary shell snippets.
6. The target image repository must be allowlisted.
7. Tags are allowed as selectors; the repository digest and source identity must be verified before launch.
8. Every release executes from that pinned image, including application code, migrations, Compose and the controller.
9. Only one update may run per Compose stack at a time.
10. Before/after service state must be captured.
11. Command output and verification output must be written to artifacts.
12. The run must fail closed when verification cannot prove the desired state.
13. The operation must remain auditable even if MoonMind services restart during the update.
14. Existing operator URLs, published interfaces and ports, authentication mode, and trusted ingress remain usable across updates. Candidate Compose bindings are compared with the running deployment before recreation; fresh-install defaults cannot silently redefine an installed deployment's access.
15. Deployment-owned `.env` and override settings survive updates. A required ingress or authentication migration has a verified replacement access path before cutover; it cannot declare success from container health while locking out the operator.

---

## 6. Settings → Operations UX

## 6.1 Placement

The dashboard exposes the deployment update surface under:

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
- pause or drain new workflow work before update
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
  "buildId": "20260425.1234",
  "currentImage": {
    "requestedImage": "ghcr.io/moonladderstudios/moonmind:20260425.1234",
    "deployedImage": "ghcr.io/moonladderstudios/moonmind@sha256:...",
    "repository": "ghcr.io/moonladderstudios/moonmind",
    "reference": "20260425.1234",
    "resolvedDigest": "sha256:...",
    "sourceRunId": "depupd_01HV...",
    "updatedAt": "2026-04-25T18:04:00Z",
    "evidence": "desired_state"
  },
  "latestAction": {
    "kind": "success",
    "status": "SUCCEEDED",
    "requestedImage": "ghcr.io/moonladderstudios/moonmind:20260425.1234",
    "operator": "operator@example.com",
    "completedAt": "2026-04-25T18:04:00Z",
    "beforeBuildId": "20260424.0901",
    "afterBuildId": "20260425.1234"
  },
  "recentActions": [],
  "policy": {
    "repository": "ghcr.io/moonladderstudios/moonmind",
    "defaultReference": "stable",
    "allowedReferences": ["stable", "latest"],
    "recentTags": ["20260425.1234", "20260424.0901"],
    "mutableReferences": ["latest", "stable"],
    "allowedModes": ["changed_services", "force_recreate"]
  }
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
      "digestPinningRecommended": true,
      "allowedModes": ["changed_services", "force_recreate"]
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

The default Compose deployment-control worker uses a read-only checkout mount
plus a narrow writable deployment-state mount:

```text
/workspace/host_project        # read-only Compose project
/workspace/deployment_state    # writable desired-state files
```

By default, the allowlisted env file is:

```text
/workspace/deployment_state/.env.deploy
```

The Compose runner passes that file with `docker compose --env-file` whenever
it exists. This keeps the active deployment target durable outside the worker
process while avoiding arbitrary caller-selected file paths.

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

The env file is the Compose-consumed desired state. A JSON sidecar stores the
full audit payload, including fields that should not be projected into process
environment variables.

## 9.3 Mutable tag rule

Before mutation, the system resolves every requested tag to an immutable digest and verifies its release identity. Missing or ambiguous digest or source identity fails admission with actionable diagnostics.

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

The default deployment-control worker uses an atomic file lock under the
allowlisted deployment-state mount, for example:

```text
/workspace/deployment_state/locks/moonmind.lock
```

This lock is shared by worker processes that mount the same deployment-state
directory and is released when the update lifecycle exits.

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

## 10.4 Pull images and check runner integrity

The tool runs the equivalent of:

```bash
docker compose pull --policy always --ignore-buildable
```

The exact flags are implementation-specific and policy-controlled. Pull output is captured in the command log artifact.

The submitting worker verifies the image and records immutable inputs, owner,
image ID and a fixed deadline. It launches or reattaches to the named updater
using the existing deployment service's mounts, credentials and Docker boundary.
A lost launch response requires a daemon ownership check before retry. An
unreadable daemon never proves that the updater is absent. Legacy direct runners
still reject self-replacement; production release jobs use the detached owner.

## 10.5 Persist desired image

After candidate qualification and routing promotion, the tool writes the desired
image reference into the allowlisted deployment env file before normal service
recreation. Candidate startup alone never changes desired state. Compose loads
the operator's `.env` before the image-only desired-state overlay and retains
the deployment-owned override. Explicit image selection wins for this job.

The tool must not edit arbitrary files selected by the caller. If service
recreation or verification fails after desired state persistence, the run records
the failed state and command diagnostics.

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

## 10.10 Bind-mounted checkouts: a host `git pull` alone is not a deployment

The default production Compose deployment does not overlay application source.
Its immutable image provides release identity. Source mounts are explicit
development configuration in `docker-compose.development.yaml`; the following
freshness rules apply to those development overlays.

Development Compose deployments bind-mount `./moonmind:/app/moonmind:ro` (and
similar source mounts) into long-lived worker containers. A host `git pull`
rewrites the files on disk while the running Python processes keep the old
imported modules, silently creating a mixed-version deployment: one run can be
admitted under old rules and finalized under new ones
(MoonLadderStudios/MoonMind#4224).

Operator rule: **a host `git pull` alone is not a deployment.** After pulling
source changes on the host, the operator must prove the running workers match
the checkout before new runs are admitted:

1. Check the readiness detail: each Temporal worker exposes its
   startup-recorded code identity on `/readyz` (`codeRevision`,
   `codeIdentityStatus`, and — when stale — `reasonCode: stale_code` with the
   worker name plus both revisions). The API aggregates the same detail under
   `/healthz` → `workerCodeFreshness` (per-worker entries plus a `staleCode`
   list when any worker is stale).
2. Or run the CLI from the checkout: `moonmind worker code-readiness`
   (uses `MOONMIND_WORKER_READINESS_URLS` / `TEMPORAL_WORKFLOW_READINESS_URL`;
   exits non-zero with `reasonCode=stale_code` naming each stale worker and
   both revisions).
3. Restart stale workers: the deployment update path
   (`deployment.update_compose_stack`) probes worker `/readyz` endpoints after
   recreating services, restarts idle stale workers immediately, drains busy
   ones (bounded graceful restart — never killed mid-activity), and fails the
   update loudly with `reasonCode=stale_code` when a stale worker cannot be
   restarted or when a restarted worker stays
   unreachable/`unknown` after bounded rechecks (absence of evidence is not
   proof of freshness). Bare readiness URLs take their hostname as the worker
   name so restart planning stays addressable. New `MoonMind.UserWorkflow`
   admissions are
   refused with `stale_code` while every known worker serving the queue is
   stale. The API and the deployment-control worker ship the same default
   workflow readiness target as the workflow worker, so neither gate is
   silently disabled on a default Compose installation.
4. Every worker records its startup code identity in AgentRun/UserWorkflow
   metadata (`workerCodeRevision`) so post-incident analysis can tell which
   revision executed each step.

---

## 11. Updater runner execution model

Production releases use one durable detached job on the existing deployment
substrate. Host and workflow callers execute the same image-owned controller.

## 11.1 Privileged deployment-control worker

A trusted worker with `deployment_control` and `docker_admin` capabilities executes Compose commands directly on the deployment host.

This mode is simple when the worker already runs on the host that owns the Docker daemon.

The worker supplies submission, observation and maintenance for the detached
job. It is configured with:

- `MOONMIND_DEPLOYMENT_LOCAL_PROJECT_DIR` for the read-only Compose checkout
- `MOONMIND_DEPLOYMENT_DESIRED_STATE_ENV_FILE` for the allowlisted env file
- `MOONMIND_DEPLOYMENT_DESIRED_STATE_JSON_FILE` for the audit sidecar
- `MOONMIND_DEPLOYMENT_LOCK_DIR` for durable per-stack lock files
- `MOONMIND_DEPLOYMENT_EXCLUDED_SERVICES` for explicit specialized maintenance;
  a coherent release rejects exclusion of the deployment-control worker

On Windows Docker Desktop, the Linux worker resolves Compose files through its
local checkout mount and maps checkout bind sources into the daemon's
`/run/desktop/mnt/host/<drive>/...` namespace. WSL user-distro `/mnt/<drive>` paths
are not daemon-visible host mounts. Rewritten checkout binds disable automatic
host-directory creation, so an unavailable source fails instead of mounting an
empty directory over application files. POSIX host paths retain their configured
namespace.

## 11.2 Ephemeral updater container

A deployment service one-off runs `python -m
moonmind.workflows.skills.deployment_release` from the selected image. Its
request, attempts, routing decision, primary result and cleanup receipts live
under the deployment-owned `release-jobs` directory. Kernel locks serialize
stack changes and job ownership; PID age cannot transfer authority across
container namespaces. The updater has a two-hour cumulative deadline and at
most three attempts, preserved across restarts.

Candidate workers first register all workflow and Activity queues. A stable,
pinned canary verifies their image identity through each queue. The controller
also qualifies a candidate API's health, dashboard, assets and read-only API.
Temporal's compare-and-set routing update promotes only that candidate. A lost
response reuses the same canary run and verifies the server's current decision.

Before promotion, the controller retains pollers from the exact previous image.
Pinned work remains owned by that version after normal Compose services change.
The existing maintenance schedule retires those temporary pollers only when
Temporal reports the version drained. Inactive private candidates require a
terminal release owner, closed canary and server-confirmed inactive status.
Unknown drainage or ownership keeps the cohort. Candidate pollers may retire
after the normal fleet verifies the same image. These containers exist only for
bounded release work and drainage; they add no idle deployment service.

The primary result is persisted before auxiliary cleanup. Failed cleanup records
its pending owner for `release.reconcile` without replacing verified deployment
success. Default scheduled maintenance continues release reconciliation even
when another storage maintenance Activity fails.

## 11.3 Runner image policy

The updater must use the verified target image digest and deployment-owned Docker policy.

The Operations UI must not normally ask the operator to choose the runner image because the runner has privileged Docker access.

---

## 12. Verification model

## 12.1 Compose-level verification

Before replacement, the host scripts and deployment runner execute the shared
`moonmind/deployment_access.py` preflight. It compares rendered Compose with
installed API containers (including stopped containers) in the selected project.
Changed published interfaces, ports, authentication mode, OIDC login settings,
trusted ingress, public URL, trusted proxies, or CORS origins stop an ordinary
update before recreation. Existing API network attachment names must remain
available, including external ingress networks for an API behind a reverse
proxy. A renamed Compose network key preserves access when its resolved Docker
network name stays the same.
The operator preserves existing settings in the deployment-owned `.env` or
override; intentional access migrations are applied separately and verified
through the operator URL. The preflight never infers ingress authorization or
prints rendered environment/inspect payloads, including OIDC credentials.

The host scripts require Python 3.10+ and Docker Compose V2, validated before
deployment changes. Fetching a branch selects its published source-SHA image
without changing the checkout. Failed qualification preserves current routing
and normal services. Explicit specialized maintenance skips the API check only
when its dependency closure and orphan removal cannot affect the API.

Direct Docker Compose commands
remain an explicit operator path and do not invoke this updater guard.

The system verifies Compose state using:

- `docker compose ps`
- `docker compose images`
- container inspect data
- service health status
- image IDs and repo digests where available

## 12.2 Application-level verification

The system must verify the actual operator dashboard/API URL after Compose-level
health succeeds. The hostname, published port, and ingress path are the ones the
operator uses; substituting localhost or a container-internal address does not
prove LAN, VPN, or proxy access. Every supported access path has before/after
evidence, or an explicit unverified result when the verifier cannot reach that
client network.

Required checks include:

- `/healthz` through the operator URL, including authentication readiness
- dashboard reachability and coherent entry/lazy assets, using
  `python tools/verify_deployed_ui_assets.py --base-url <operator-url>`
- a read-only API request through the same ingress, with the configured auth
  flow when required

Additional checks may include:

- Temporal worker registration or poller health
- database connectivity, if applicable
- basic workflow submission readiness, if safe

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

Deployment update permissions should be distinct from ordinary workflow-submission permissions.

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

MoonMind must not expose Docker socket access to ordinary workflow runtimes, agent runtimes, repo workspaces, or user-authored tools.

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
- workflow ID, if applicable
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

The durable owner resumes incomplete work within its original deadline and
three-attempt budget. It never changes the selected image, resets a canary or
silently rolls back. The caller can reattach using the recorded submission ID;
scheduled maintenance can resume a stopped owned updater. Exhaustion preserves
receipts and retained worker ownership and reports the exact failure. A new
release is a distinct audited operation.

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

## 16. Interaction with workflow execution

Deployment update is executable operational work and should be represented through MoonMind's tool and plan system.

The update tool may be invoked by:

- direct Operations UI action
- scheduled maintenance workflow
- admin-authored operational workflow
- future release-management workflow

An agent may assist by explaining the update result or summarizing logs, but the privileged update itself is performed by the typed deployment tool.

Representative operational sequence:

```text
1. Pause or drain new workflow work, if requested.
2. Update MoonMind deployment.
3. Verify Compose and application health.
4. Resume workflow work, if it was paused.
5. Summarize before/after state and artifacts.
```

---

## 17. Interaction with Settings information architecture

Settings remains the operator-facing configuration area.

Deployment update belongs on the **Operations** page because it is a system-control surface. It should not become a top-level navigation item unless the broader Settings architecture is intentionally revisited.

Use the canonical Operations route:

```text
/settings/operations
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
This tag is mutable. MoonMind pins and verifies its resolved digest before updating; future uses of the same tag may select a different image.
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

The deployment update workflow should expose progress states suitable for the dashboard:

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
12. Mutable tags are selectors only; a verified repository digest is mandatory before release execution.

---

## 21. Summary

MoonMind should expose Docker Compose deployment updates as a small, safe, audited Operations workflow.

The operator experience is simple: choose the target image, choose the update mode, provide a reason, and start the update. The backend handles policy, locking, Docker Compose execution, verification, and artifacts.

The portable `update-moonmind` Skill and the executable deployment tool use the same image-owned semantic entrypoint. The native host supplies durable execution and policy boundaries; it does not maintain a second update algorithm.
