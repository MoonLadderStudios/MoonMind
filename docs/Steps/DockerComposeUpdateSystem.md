# Docker Compose Deployment Update System

**Document Class:** Canonical declarative
**Viewpoint:** Module Architecture View
**Status:** Desired State
**Owner:** MoonMind Engineering
**Authority:** One portable deployment controller, in-place Compose updates, and recoverable local deployment state.
**Last Updated:** 2026-10-04

Related: [Agent Instructions](../../AGENTS.md), [Temporal Architecture](../Temporal/TemporalArchitecture.md), [Provider Profiles](../Security/ProviderProfiles.md), [Secrets System](../Security/SecretsSystem.md).

This document describes the deployment update design. Some code from the earlier candidate/retained fleet design still exists, for example deployment maintenance recognizes leftover `mm-candidate-`/`mm-retained-` cohort resources (`moonmind/workflows/skills/deployment_maintenance.py`). Those paths are not the default update path, and removing them preserves saved work, histories, deployment settings, and the supported recovery path.

## 1. Purpose

An operator can update or repair MoonMind without depending on the healthy application they are trying to repair. The normal operation is image pull followed by changed-service recreation, with useful local progress and an honest result.

```text
resolve and verify target -> persist deployment intent
-> pull required images -> run required initialization/migrations
-> compose up changed services -> verify and record result
```

Image acquisition needed to resolve or inspect the target happens before persisting that target. Persisted intent records what the operator requested, not proof that installation succeeded.

The steady state is one installed Compose fleet per instance, not blue/green candidate and retained fleets. A bounded interruption during recreation is an accepted tradeoff. Independent deployments remain independent and need no shared release state or central coordinator.

## 2. Summary

One portable controller implements updates for the host entrypoint and the Settings Operations action. The host path stays usable when the API, Temporal workers, provider manager, or application artifact service is unhealthy, because those are the components an update may need to repair.

The controller owns image resolution, the per-stack lock, local durable state, Compose mutation, verification, and bounded recovery, using Docker/Compose and local job files. Temporal and the UI may request or observe an update, but neither is required for progress or runs a second update algorithm. There is no parallel supervisor, promotion state machine, or mandatory approval workflow.

## 3. Terminology

**Stack target:** The deployment-owned Compose project, files, paths, and settings the controller may operate. Observable defaults are resolved from the installation; explicit overrides are kept.

**Target image:** The requested allowlisted MoonMind tag or digest. A tag is a selector; the controller resolves and records the concrete image that will run.

**Controller:** The standalone deployment controller (`deploy/controller`), installed by `deploy/controller/bootstrap.py` as its own `moonmind-controller-<id>` Compose project, separate from the MoonMind stack. It owns its durable state, restart policy, direct Docker socket mount, and one authenticated local endpoint guarded by a deployment-owned secret, so it outlives the worker or API that submitted work and survives target-project shutdown. The host CLI installs, updates, and restores the controller; the controller does not replace itself. Agents receive neither sockets nor controller access.

**Update record:** The local durable request, observed progress, attempts, and result for one operation. It survives application and controller restarts and distinguishes requested from confirmed state.

## 4. Goals and non-goals

### 4.1 Goals

Default updates and recovery work without hidden switches or manual version alignment, preserving data, operator access, and explicit settings. Failures are understandable from local records even when chat and the UI fail. Each side effect has one owner, uncertain outcomes are reconciled before being repeated, and transient errors are retried within a bound.

### 4.2 Non-goals

No multi-user role system, arbitrary shell or Docker UI, zero-downtime deployment platform, permanent old-version fleets, or per-release re-admission of every schedule. No compatibility fingerprint based on SHA, patch, or major/minor equality. No separate implementations for host and workflow callers.

## 5. Core invariants

- Only the authorized operator and trusted deployment infrastructure can mutate an allowlisted stack. Agent workspaces do not gain Docker access.
- Exactly one controller mutates a stack at a time. An unreadable Docker daemon or lost acknowledgment does not prove that another updater is absent.
- Desired state, operation logs, and original errors remain readable locally without a healthy API, application database, object store, or LLM. Application artifacts and UI projections are secondary copies.
- Data volumes, saved work, histories, explicit configuration, and access protections survive updates. Cleanup does not delete the only recoverable copy.
- Image identities establish integrity and provenance. Interface behavior, schema support, and actual replay requirements establish compatibility. Those are different questions.
- Success requires observed installation and verification. Pending, failed, and unavailable checks stay distinguishable, and secondary reporting or cleanup failures do not erase a confirmed primary result.

## 6. Settings → Operations UX

The Operations surface is at `/settings/operations`. The operator chooses the target MoonMind image, sees that services may restart, and starts an update. There are no separate controller images, worker versions, routing modes, or per-harness image pins to choose.

The page shows the selected target, installed state, current action, original failure, and log references. Changed-service recreation is the default; force recreation is an explicit maintenance option, and destructive cleanup is not part of an update. The application may be unavailable while it is updated, so the host command and local record, not the dashboard, are the recovery path.

## 7. API contract

The public Operations entrypoints are:

```text
POST /api/v1/operations/deployment/update
GET  /api/v1/operations/deployment/stacks/moonmind
GET  /api/v1/operations/deployment/image-targets?stack=moonmind
POST /api/v1/operations/deployment/operations/{operationId}/retry
```

A submission identifies the stack and target image, plus supported maintenance options. The API submits or observes the same controller operation the host uses, and its response identifies that operation and its actual state. An API or workflow timeout is neither permission to launch another updater nor evidence that the operation failed.

Each caller names its own controller operation identity: `ui-<requestId>` from Settings Operations, `host-<submission>` from the host entrypoint, `wf-…` from a workflow's durable identity. A lost acknowledgment is resolved by reading that identity and resubmitting the same identity only if the controller never recorded it. The dashboard sends one `requestId` per update intent and resends it after an unanswered or server-error submission. A duplicate request for the target the stack is already applying reattaches to the running operation; a changed target is new intent and is refused, naming the owning operation, until that operation ends.

Stack state lists controller operations (requested and installed image, status, original error, attempts, verification) beside read-only history of earlier workflow-backed updates; installing an earlier image is an ordinary controller update. Retry asks the controller for a fresh bounded attempt and keeps prior errors. Responses carry the controller `operationId`, `owner` (`controller`), and status. A missing or unrecognized controller status is reported as `UNKNOWN`, not as queued or finished. Controller unavailable, credential rejected, busy, and refused are distinct errors, and none starts another updater.

The API reaches the controller over the private `deployment-controller-network` using the bearer secret in the read-only deployment state mount (`deploy/state/controller`). Only the API (read-only) and the deployment-control worker see that state; the agent runtime sees an empty view, and the secret never reaches the browser.

Without an installed controller, Settings Operations refuses an update with `deployment_controller_not_installed` (HTTP 503) and names the host repair route `./tools/update-moonmind.sh`, which installs the controller and updates through it. An installed controller without active-journal transition support is refused with `deployment_controller_refresh_required` and the same route. API submission needs neither Temporal nor the deployment worker.

The workflow's deployment observer heartbeats while reading its registry and awaiting the durable result. A 60-second heartbeat timeout detects a replaced observer independently of the longer command and release deadlines, and its retry reattaches with the same operation identity, reading an existing terminal receipt without repeating deployment.

## 8. Executable tool contract

`deployment.update_compose_stack` is the typed privileged operation; its executable definition and API schema own the field-level contract. Inputs resolve against deployment-owned policy and an allowlisted image target, and it accepts no arbitrary commands, paths, repositories, or privileged runtime settings. With an installed controller, the tool only submits the controller operation under the workflow's durable identity and observes it, so an activity retry reattaches instead of starting a second updater. The host entrypoint does not need a workflow.

## 9. Desired state storage

The controller uses the deployment-state directory and its image env overlay, JSON metadata, lock, and operation files. Mounted boundaries are a read-only host project at `/workspace/host_project` and narrowly writable state at `/workspace/deployment_state` where configured.

The operator's `.env`, Compose overrides, project identity, credentials, bindings, and network attachments are preserved. Only controller-owned image/operation fields change, and state is written safely so interruption leaves recoverable intent rather than a truncated sole copy.

Persist selected concrete images and the operation identity before recreating services. A pulled image alone is not an installed release.

The default host update (`tools/update-moonmind.sh`) updates to `latest`: it pulls `<image-repository>:latest` once and installs whatever that tag currently names. It does not derive an image from Git, a branch tip, or a `sha-<commit>` tag, does not wait for a specific build to publish, and does not fall back through ancestors or check source revisions. A commit merged moments ago is installed by the next update after its image publishes. The digest that pull resolved is recorded only so every service in the operation runs the same image. An explicit target requested by the operator is not silently substituted.

The installed Omnigent server/host selection belongs to the same deployment owner. Future managed launches resolve it from installed deployment intent, and historical attempt records retain the actual image and session that executed them.

## 10. Execution lifecycle

### 10.1 Validate request

The controller resolves the project, settings, and target through trusted deployment inputs and checks authorization, repository/path allowlists, and Compose validity. Observable values are derived rather than treated as missing flags.

A broken application health check is diagnostic input, not a prerequisite, because the controller may be repairing exactly that. Access and storage boundaries are preserved before mutation without requiring the old API, provider-manager workflow, chat, or application evidence store to work.

### 10.2 Acquire deployment lock

Host and UI submissions share one per-stack local lock; a second request reattaches to the existing operation or waits within a bound. Ownership is not transferred merely because a PID or timestamp looks old in another container namespace. Controller replacement holds the same lock, so controller lifecycle and deployment mutation share one exclusion boundary. Separate deployment projects have independent locks.

Staging/apply failures retry automatically up to the bounded per-group attempt budget, so a transient failure ends in `failed` or a later `succeeded` instead of staying open. Restart recovery reconciles before applying: only the newest open operation per stack survives, and an open operation older than a confirmed installation for the same stack is superseded rather than replayed over confirmed intent. A retried submission for an already-installed image reattaches to the recorded terminal success, including after a lost response.

### 10.3 Capture before state

The controller records enough observed configuration, selected services, concrete images, and progress to reconcile the operation, kept small and redacted. Missing observations are explicit unknowns, not permission to invent ownership or discard saved state.

### 10.4 Pull images and check runner integrity

The controller verifies the concrete artifact being executed and runs independently of the services it replaces; a lost launch acknowledgment is resolved by checking the owned process/container before another launch. Native pull and content-addressed caching are reused. Equal source revisions between caller, controller, worker, and managed runtime are not required, because a different SHA is not by itself an incompatible interface.

Host image-selection failures retain redacted Docker diagnostics and stop before recording a release submission. A read-only filesystem or exhausted disk is reported as a Docker storage failure with host/data-disk space recovery guidance, not an uncaught traceback or a claim that a release job was created. On Docker Desktop, recovery means freeing backing host space before restarting Desktop; resetting its data disk is not part of updater recovery.

### 10.5 Persist desired image

The selected deployment intent is persisted before recreation (section 9), with no candidate qualification or routing-promotion prerequisite.

### 10.6 Recreate services

Normal service replacement uses the equivalent of:

```bash
docker compose pull --policy always
docker compose up -d --pull never --no-build --remove-orphans --wait
```

under bounded timeouts, after staging every image the update needs and before any recreation. Services built from the MoonMind image pull with `--policy always`; other reconciled infrastructure services pull with `--policy missing`, so an infrastructure image the release newly pins (for example a MinIO digest change) is staged while present images are not refreshed, and `up --pull never` cannot fail on a missing image. The deployment-owned Compose file set (including `COMPOSE_FILE`) passes through unchanged, and the deployment-owned `.env` layers under the controller-generated image overlay, so operator authentication, bindings, and infrastructure versions are preserved. Privileged-endpoint submissions validate project, paths, services, and image references before persistence. Routine updates do not use `docker compose down`, fleet-wide force-recreate, or pruning.

The updater reaches Docker through `docker-proxy`, so the main pass does not recreate it, `postgres`, or `sandbox-egress-proxy`. The egress gateway is aligned before the workers that attest it. Other excluded substrate is reconciled in a staged pass after the main stack verifies, when its image or installed definition differs from the release. Definition drift compares each container's recorded Compose config hash with the release rendered under the project directory that container recorded, so a different spelling of the same checkout is not drift. The updater does not recreate its own transport: a drifted `docker-proxy` stays running and fails the release as unconverged substrate for the standalone controller to reconcile.

Dependency order and `init-db`/schema migration gating apply before dependent services start. A failed migration preserves the original error and data rather than starting an incompatible application or attempting a destructive downgrade.

**Active Omnigent journal transition.** Supported rollback targets can read only complete JSONL journals, so active journals are compacted after image staging and before service recreation, under the same owner and lock (the controller's `before_compose` step). The owner observes the project's actual `agent_runtime` writers and `artifacts` lifecycle sweepers, including overrides and one-offs, stops them with a bounded Docker stop, and inspects them again, because pausing API submissions or enumerating workflows does not prove quiescence. Durable Activities recover from saved progress, so workflows need not finish first and keep their intent.

Through `deployment_release --omnigent-journal-prepare`, the bridge store replaces committed active chunks with complete, old-reader-compatible journals and atomically updates references and event locators; original chunks stay recoverable until that commit. The controller records observed consumer image identities first, so a rollback to a target that predates the helper can use the source image's helper. Preparation starts only the `postgres`/`minio` services it needs and does not require a healthy API or worker. A failed storage connection, unavailable artifact, or unknown consumer state prevents recreation and keeps saved work and the original error. After a partial Compose failure, retry stops consumers and repeats observation and compaction, because a partially recreated writer may have committed more chunks.

### 10.7 Verify desired state

The controller observes installed services and concrete images, required health, ordinary workflow dispatch, and the operator access path. A running container does not prove that work can execute or that the dashboard serves the intended release. Post-apply verification accepts a run-to-completion service (such as `init-db`) that exited 0; any other non-running service fails its check.

An unavailable optional integration is reported separately, and missing mandatory verification is not converted to success. Verification is bounded and uses existing health/functional boundaries.

### 10.8 Migrate the singular Omnigent release

An ordinary MoonMind update also checks the configured Omnigent server and required host image channels (image/tag inputs in the operator `.env` or worker environment) for newly published artifacts. Mutable tags resolve to concrete digests, and the installed release advances when a suitable new image exists for a configured channel; channels without configured inputs keep their recorded refs, and a channel with no suitable new image leaves the installed release in place. A running server container that already carries the candidate outside the release (for example after a plain `docker compose up` rendered the mutable tag) is adopted rather than converged back, because it may already have migrated the Omnigent database forward.

Selected digests are recorded under the deployment lock before the main Compose pass renders them, so that pass installs a server that accepts the current schema. After the fleet verifies, migration finishes that selected revision without resolving channels again and refreshes the server, API, and agent runtime worker. Running static host profiles follow a changed shared host image and are recreated without draining; inactive profiles stay inactive. An explicit operator digest pin in `.env` stays in effect until changed. If a recorded candidate later fails verification, the desired state stays recorded with no automatic rollback; recovery is an explicit rerun or rollback.

The standalone controller delegates selection and migration to `deployment_release --omnigent-select` and `--omnigent-migrate` in bounded Compose one-offs from the selected MoonMind image, holding the deployment lock and giving them its own Docker transport so repair does not need the target project's proxy. The main Compose pass layers the operator `.env`, release-owned `.env.deploy`, and controller image overlay in that order. A selection failure leaves the fleet untouched; a migration failure keeps the confirmed installation and records the original error as failed verification. Lost acknowledgments reuse the saved selection, and a still-running one-off is observed before another attempt launches.

**Interrupted agent steps.** Updates do not drain or checkpoint. An agent step interrupted by a replaced host fails with host loss; its old host is confirmed stopped (or fenced for the host janitor) before any successor runs. Only that step receives a new Step Execution under the same workflow and logical step, within its remaining execution ceiling, restoring the latest verified saved workspace or restarting from its admitted inputs with that limitation recorded. Completed steps and confirmed publication effects are not repeated, because every attempt publishes under the logical step's identity. Cancellation starts no successor. A successor currently launches the server and host images its execution plan recorded; fresh attempts do not yet consume newly installed images, so a successor whose recorded images can no longer be launched or attested fails with that diagnostic.

**Recurring schedules.** Image changes are not copied into new policy/profile versions, and recurring schedules are not re-admitted merely to keep digest strings equal; historical attempts keep what they actually ran. While schedule launch pins exist, schedules consume the release rather than gate it. Migration refreshes schedules bound to the managed bootstrap profile onto the cut policy, with or without a recorded execution plan, once their provider's host binding has moved. A schedule it cannot refresh is counted in the receipt (`scheduleRefreshFailureCount`, with the first few named in `scheduleRefreshFailures` so the receipt fits the controller's bounded log tail) and retried by the API's bootstrap reconciliation; it does not fail a verified update that no rerun could fix.

### 10.9 Capture after state

Confirmed installed images, observations, and unresolved work are recorded beside the original request, and the local record stays usable if publishing an application artifact fails.

### 10.10 Release lock and report result

The primary result is persisted before secondary cleanup, and ownership is released safely. Original and later errors stay distinct. A cleanup failure does not hide a successful installation or make missing mandatory verification look passed.

### 10.11 Bind-mounted checkouts: a host `git pull` alone is not a deployment

A development bind mount can change files without restarting imported code, so affected processes are recreated through the same owner. Actual startup provenance is recorded for diagnosis but is not a runtime admission or compatibility gate.

Bind sources are spelled the way the issuing Docker client resolves them, on POSIX and Windows Docker Desktop. A Windows drive path maps to the Desktop daemon namespace at `/run/desktop/mnt/host/<drive>`; a WSL user-distro client (and containers it creates through its socket mount) resolves binds inside the distro, where `/mnt/<drive>` is the drive, so that spelling stays. No empty directory is created over a missing source mount. Controller bootstrap resolves its bind sources through this adapter and fails fast on a missing required source. It removes a stopped controller container before `up`, because Compose would otherwise restart it with network IDs that disappear once the target stack recreates the link network.

## 11. Updater runner execution model

### 11.1 Privileged deployment-control worker

The deployment-control worker is a submission/observation adapter where available, not the only recovery path. Privileged Docker operations stay in trusted deployment infrastructure and cannot be supplied by agent-authored code. A workflow caller without an installed controller reports `runner_unavailable`; the independent host entrypoint installs the controller.

### 11.2 Standalone controller project

The controller's restart policy, durable state directory, and direct Docker socket let an update replace its submitting worker and survive target-project shutdown. Ownership, target, progress, deadline, and attempt budget survive restarts; on restart the controller inspects Docker and converges only unfinished work toward the same target.

The host entrypoint derives the endpoint port from the controller's identity. The network is named `<compose project>_deployment-controller-network` unless `MOONMIND_DEPLOYMENT_CONTROLLER_NETWORK` overrides it, so independent deployments on one host never share the alias. A submission that names no Compose target uses the target the controller derives: the project recorded at bootstrap (else `COMPOSE_PROJECT_NAME` from `.env`), `COMPOSE_FILE` from `.env` (else `docker-compose.yaml` plus its override), and the services that selection renders minus the Docker transport substrate.

**Installation.** When no controller secret exists, or bootstrap left a secret but no reachable endpoint, operation record, or container, the host entrypoint installs the controller with `deploy/controller/bootstrap.py` from the selected release image, running it from that same application image (no separate controller image is published), and submits through it. A legacy writer still active from an earlier release is detected by the controller's cutover check, which defers instead of competing. `--dry-run` installs nothing.

**Refresh.** The default host command refreshes an idle controller from the selected release before submission, even when its journal-transition method is advertised, because method support admits the interface but does not justify keeping an older implementation. It extracts the stdlib bootstrap from the staged target, falling back to an observed installed application image when a rollback target lacks that lifecycle. Bootstrap runs in a temporary trusted image container with the Docker socket and writable controller state on the private controller network, so root-owned records and locks stay readable without host privilege changes; it needs no healthy API or worker and leaves the host checkout, Compose project, mounts, network, and credentials in place. If the identity file is lost, bootstrap first recovers it from the Compose installation without replacing the controller. Replacement holds the stack lock; a capable controller with open work keeps ownership, and an explicitly selected controller, endpoint, or secret keeps its release choice.

**Repair after failure.** An advertised method cannot prove a controller will apply a release whose controller fixes a defect. After a failed operation, a default host or deployment-control submission gives the requested release's controller one attempt: `bootstrap.py ensure --prefer-requested` refreshes to the requested image under the same lock and open-operation refusal. Only a confirmed change (`refreshed: true`) earns a Retry of the same operation; otherwise the original failure stands beside the refresh diagnostic. The API cannot run bootstrap, so a failed Settings Operations submission recovers through the host command or a workflow-submitted update.

An older installed API/controller may reach a newer target's `--omnigent-select` callback. The callback requires the owning controller's `active-journal-transition` method before touching release state, so an older owner is refused before any mutation and pointed to `./tools/update-moonmind.sh`, which bootstraps and retries the release.

### 11.3 Runner image policy

The controller runs trusted deployment-owned code from a verified artifact. The UI exposes no separate runner-image selection, and explicit maintenance inputs do not bypass image allowlists or grant an agent Docker access.

### 11.4 Execution availability and automatic recovery

The default has one installed fleet recreated in place, without parallel candidate/retained fleets, promotion canaries, application-owned worker-version routing gates, or a retained-cohort restoration supervisor.

Ordinary process restart belongs to Docker/Compose, and an interrupted update reconciles its local record and Docker state through the same controller; unknown state is neither success nor permission to destroy something that may still own work. Replay-sensitive changes need compatible replay or a controlled migration, because removing versioning does not make old histories safe to replay on new code.

Leftover legacy updater jobs and cohort resources keep their evidence and any active-work recovery path. An old image-owned controller is not relaunched, because it would recreate the removed architecture, and an unidentified or still-serving cohort is not deleted automatically. An unresolved ownership or data risk stays visible for operator action.

A provider-manager fault is diagnosed as its own fault and does not block installing its fix. Maintenance can expose incomplete operations but does not promote images, overwrite target intent, or restart retired controllers.

## 12. Verification model

### 12.1 Compose-level verification

Verification renders and compares the selected project's actual settings, bindings, mounts, networks, and service identities. Deployment-owned ports, external ingress networks, authentication settings, and data mounts are preserved; fresh-install defaults do not replace installed choices.

Observation distinguishes the controller's own one-off container from ordinary services, and all observation and cleanup is scoped to the owned project. There is no global prune or broad name-prefix deletion.

### 12.2 Application-level verification

Operator URLs are derived from the configured public origin and actual bindings where possible. An explicit `--operator-url` supplies a non-derivable target or overrides/adds one; it is not needed for an address the controller can determine.

Verification uses the real configured ingress and a protected read-only application path, preserving TLS and authentication. It does not forge identity headers, mint sessions, or treat a container-internal or loopback probe as proof of an external route. Missing credentials or unreachable client networks limit what can be verified and are reported as such; healthy pre-update browser access is not a prerequisite to repair.

CI qualifies a candidate implementation; deployment observations identify the artifact actually installed. Updates do not run full CI or paid-provider qualification.

CI journeys for this boundary:

- `controller-journey` runs `tests/integration/host_update/test_journal_transition.py` (journal compaction with real PostgreSQL and artifact bytes, controller interruption after partial recreation, resume, and rollback to an image lacking the helper) and `tests/integration/host_update/test_controller_refresh.py` (host refresh through a temporary bootstrap container, identity recovery, and open-operation protection).
- The reliability shards run `tests/integration/reliability/test_interrupted_step_replacement_journey.py` on real Temporal and PostgreSQL: the update interrupts one agent step, and only that step reruns on a new host restored from its archived workspace.
- The `omnigent-exact-artifact` credential-free recovery row runs that journey on the pinned Omnigent server and host images with the production host runtime and host-loss predicate, using a controlled provider instead of a model turn.

None of these proves automatic bootstrap through an older API binary or admission on a newly installed image.

### 12.3 Verification failure rule

Existing result surfaces distinguish `SUCCEEDED`, `FAILED`, and `PARTIALLY_VERIFIED` where supported. Missing mandatory evidence does not become `SUCCEEDED`. The failed check, original error, confirmed progress, and recovery entrypoint stay visible.

## 13. Security and policy model

A single admitted operator does not mean an unprotected application. Operator admission, deployment-capability boundaries, trusted ingress, image/path allowlists, and separation between privileged deployment infrastructure and agent runtimes all apply, without human role management. Credentials resolve through deployment/secret ownership; command output and state summaries are redacted, and tokens, cookies, environment dumps, and personal configuration stay out of Git, command arguments, and ordinary artifacts. An update request does not authorize unrelated production mutation.

## 14. Audit and artifacts

The local operation record is the durable source for requested target, concrete images, operation identity, timestamps, progress, original errors, attempt bounds, and observed outcome, kept in existing files rather than a database or event platform. The host entrypoint and, when available, the Operations UI expose a short summary and redacted logs; losing the UI copy does not erase the local one. Missing metadata is reported as missing rather than reconstructed as success.

## 15. Failure and rollback semantics

Unsafe input and unauthorized mutation stop before they occur. Transient failures are reconciled and retried under one operation-level deadline and bounded retry policy that restarts do not reset; uncertain Docker effects are checked before retrying. Cancellation stops further mutation while keeping recoverable state. Exhaustion is not a permanent ban: a new authorized retry reconciles current state and starts a new recorded operation after the previous writer stops, preserving prior errors and target intent. Full-workflow retries are not nested around the controller, which already owns recovery.

Rollback is an authorized update to a previous allowed image through the same controller, with actual schema/data/history compatibility checked. It does not silently downgrade a database or erase saved work, and there is no separate rollback engine. A saved old image is provenance, not proof that every reverse migration is safe.

## 16. Interaction with workflow execution

Workflow submission is one optional way to request the update; ordinary orchestration remains Temporal-owned. Deployment recovery does not depend on a provider-capacity lease, a healthy singleton workflow, or workflow dispatch, since those may be what is being repaired. Queued work and schedule identity survive recreation; missed schedule ticks are not replayed as an unbounded burst, publication intent is not rewritten, and a saved checkpoint is not replaced with an empty retry.

## 17. UI and observability

Deployment update lives in Settings Operations, described plainly: "Pull the selected images, recreate changed services, verify the deployment, and preserve a local recovery record. Services may restart." The UI shows the actual target and result, does not call a failed or partially verified update complete, and does not expose promotion or fleet-management concepts. Detailed output stays outside workflow history. The design distinguishes an unavailable observer from a failed controller, an installed image from a verified product, and a requested action from a confirmed effect, and basic diagnosis works without chat or an LLM.
