# Docker Compose Deployment Update System

**Document Class:** Canonical declarative
**Viewpoint:** Module Architecture View
**Status:** Desired State
**Owner:** MoonMind Engineering
**Authority:** One portable deployment controller, in-place Compose updates, and recoverable local deployment state.
**Last Updated:** 2026-10-16

Related: [Agent Instructions](../../AGENTS.md), [Temporal Architecture](../Temporal/TemporalArchitecture.md), [Provider Profiles](../Security/ProviderProfiles.md), [Secrets System](../Security/SecretsSystem.md).

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

One portable controller serves the host entrypoint and Settings Operations. It owns image resolution, the per-stack lock, local durable state, Compose mutation, verification, and bounded recovery. Docker/Compose and local job files keep host repair independent of the API, Temporal, provider manager, and application artifact service. Temporal and the UI submit or observe; neither runs another update algorithm.

## 3. Terminology

**Stack target:** The deployment-owned Compose project, files, paths, and settings the controller may operate. Observable defaults are resolved from the installation; explicit overrides are kept.

**Target image:** The requested allowlisted MoonMind tag or digest. A tag is a selector; the controller resolves and records the concrete image that will run.

**Controller:** The standalone deployment controller (`deploy/controller`), installed by `deploy/controller/bootstrap.py` as its own `moonmind-controller-<id>` Compose project, separate from the MoonMind stack. It owns its durable state, restart policy, direct Docker socket mount, and one authenticated local endpoint guarded by a deployment-owned secret, so it outlives the worker or API that submitted work and survives target-project shutdown. The host CLI installs, updates, and restores the controller; the controller does not replace itself. Agents receive neither sockets nor controller access.

**Update record:** The local durable request, observed progress, attempts, and result for one operation. It survives application and controller restarts and distinguishes requested from confirmed state.

## 4. Goals and non-goals

### 4.1 Goals

Default updates preserve data, operator access, and explicit settings without hidden switches or manual version alignment. Local records explain failures independently of chat and the UI. One owner reconciles uncertain effects before bounded retries.

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

Stack state lists controller operations (requested/installed image, status, errors, attempts, verification) beside read-only workflow-backed history. Retry starts a fresh bounded attempt, retaining prior errors. Responses carry `operationId`, `owner` (`controller`), and status; missing or unrecognized status is `UNKNOWN`. Controller unavailable, credential rejected, busy, and refused remain distinct errors; none starts another updater.

The API reaches the controller over the private `deployment-controller-network` using the bearer secret in the read-only deployment state mount (`deploy/state/controller`). Only the API (read-only) and the deployment-control worker see that state; the agent runtime sees an empty view, and the secret never reaches the browser.

Without a controller, Settings Operations returns `deployment_controller_not_installed` (HTTP 503) and the host repair route `./tools/update-moonmind.sh`. Submitters require `/v1/healthz` capabilities `active-journal-transition` and `request-identity`: without request identity, a new `latest` update could return an earlier success without pulling. The API returns `deployment_controller_refresh_required` and the same repair route for an incompatible controller. Only the host entrypoint and deployment-control worker refresh it; the worker does so before submission. API submission needs neither Temporal nor the worker.

The workflow's deployment observer heartbeats while reading its registry and awaiting the durable result. A 60-second heartbeat timeout detects a replaced observer independently of the longer command and release deadlines, and its retry reattaches with the same operation identity, reading an existing terminal receipt without repeating deployment.

## 8. Executable tool contract

`deployment.update_compose_stack` is the typed privileged operation; its executable definition and API schema own the field-level contract. Inputs resolve against deployment-owned policy and an allowlisted image target, and it accepts no arbitrary commands, paths, repositories, or privileged runtime settings. With an installed controller, the tool only submits the controller operation under the workflow's durable identity and observes it, so an activity retry reattaches instead of starting a second updater. The host entrypoint does not need a workflow.

## 9. Desired state storage

The controller uses the deployment-state directory and its image env overlay, JSON metadata, lock, and operation files. Mounted boundaries are a read-only host project at `/workspace/host_project` and narrowly writable state at `/workspace/deployment_state` where configured.

The operator's `.env`, Compose overrides, project identity, credentials, bindings, and network attachments are preserved. Only controller-owned image/operation fields change, and state is written safely so interruption leaves recoverable intent rather than a truncated sole copy.

Persist selected concrete images and the operation identity before recreating services. A pulled image alone is not an installed release.

The default host update (`tools/update-moonmind.sh`) pulls `<image-repository>:latest` once and installs what it currently names, without Git-derived selection, source-revision checks, ancestor fallback, or waiting for a build. Newly merged code becomes installable after its image publishes. The resolved digest keeps services on the same artifact within the operation; an explicit operator target is never silently substituted.

The installed Omnigent server/host selection belongs to the same deployment owner. Future managed launches resolve it from installed deployment intent, and historical attempt records retain the actual image and session that executed them.

## 10. Execution lifecycle

### 10.1 Validate request

The controller resolves the project, settings, and target through trusted deployment inputs and checks authorization, repository/path allowlists, and Compose validity. Observable values are derived rather than treated as missing flags.

A broken application health check is diagnostic input, not a prerequisite, because the controller may be repairing exactly that. Access and storage boundaries are preserved before mutation without requiring the old API, provider-manager workflow, chat, or application evidence store to work.

### 10.2 Acquire deployment lock

Host and UI submissions share one per-stack kernel lock, never inferred from a PID or timestamp. A submission takes it before recording intent, so a second request reattaches to the open operation or is refused without a record. Controller update and restore check open work and recreate the controller only under it; the restored controller's restart recovery waits, bounded, for restore to release the stack. Separate deployment projects have independent locks.

Staging/apply failures retry automatically up to the bounded per-group attempt budget, so a transient failure ends in `failed` or a later `succeeded` instead of staying open. Restart recovery reconciles before applying: only the newest open operation per stack survives, and an open operation older than a confirmed installation for the same stack is superseded rather than replayed over confirmed intent. Resubmitting the same operation identity, including after a lost response, observes that operation's terminal result instead of repeating it. Any other request is new work even when its image is already installed: `latest` names whatever was published last, so each update to it pulls again.

### 10.3 Capture before state

The controller records enough observed configuration, selected services, concrete images, and progress to reconcile the operation, kept small and redacted. Missing observations are explicit unknowns, not permission to invent ownership or discard saved state.

### 10.4 Pull images and check runner integrity

The controller verifies the concrete artifact being executed and runs independently of the services it replaces; a lost launch acknowledgment is resolved by checking the owned process/container before another launch. Native pull and content-addressed caching are reused.

Host image-selection failures retain redacted Docker diagnostics and stop before recording a release submission. A read-only filesystem or exhausted disk is reported as a Docker storage failure with host/data-disk space recovery guidance, not an uncaught traceback or a claim that a release job was created. On Docker Desktop, recovery means freeing backing host space before restarting Desktop; resetting its data disk is not part of updater recovery.

### 10.5 Persist desired image

The selected deployment intent is persisted before recreation (section 9).

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

Through `deployment_release --omnigent-journal-prepare`, the bridge store replaces committed active chunks with complete, old-reader-compatible journals and atomically updates references and event locators; original chunks stay recoverable until that commit. The controller records observed consumer image identities first, so a rollback to a target that predates the helper can use the source image's helper. Preparation starts only the `postgres`/`minio` services it needs and does not require a healthy API or worker. A failed storage connection, unavailable artifact, or unknown consumer state prevents recreation and keeps saved work and the original error. After a failed Compose apply, the owner stops every consumer, because a partially recreated writer may sit beside an old reader. A retry repeats observation and compaction, since that writer may have committed more chunks. When the controller's automatic attempts are exhausted and no retry follows, it recreates the consumer services on the release that apply selected, so agent launches keep a worker; stopped one-offs stay stopped, and a recovery failure is recorded beside the original apply error.

### 10.7 Verify desired state

The controller observes installed services and concrete images, required health, ordinary workflow dispatch, and the operator access path. A running container does not prove that work can execute or that the dashboard serves the intended release. Post-apply verification accepts a run-to-completion service (such as `init-db`) that exited 0; any other non-running service fails its check.

An unavailable optional integration is reported separately, and missing mandatory verification is not converted to success. Verification is bounded and uses existing health/functional boundaries.

### 10.8 Migrate the singular Omnigent release

An ordinary MoonMind update also checks the configured Omnigent server and required host image channels (image/tag inputs in the operator `.env` or worker environment) for newly published artifacts. Mutable tags resolve to concrete digests, and the installed release advances when a suitable new image exists for a configured channel; channels without configured inputs keep their recorded refs. A running server container that already carries the candidate outside the release (for example after a plain `docker compose up` rendered the mutable tag) is adopted rather than converged back, because it may already have migrated the Omnigent database forward.

Selected digests are recorded under the deployment lock before the main Compose pass renders them, so that pass installs a server that accepts the current schema. After the fleet verifies, migration finishes that selected revision without resolving channels again: it recreates the server, waits until the recorded images resolve (with the OpenCode pair qualified when that family is installed), requires the running server to carry the recorded digest, and only then persists them as the resolved state, so a candidate that fails verification never becomes the installed release fresh attempts bind. Running static host profiles follow a changed shared host image and are recreated without draining; inactive profiles stay inactive. The API and workers are not recreated: image resolution reads the installed record, and Host Class selection reads the resolved state, rather than refs a process inherited at start. Migration needs no API database, policy, schedule, or catalog service, so it can repair a stack whose application is down. An explicit operator digest pin in `.env` stays in effect until changed. If a recorded candidate later fails verification, the desired state stays recorded with no automatic rollback; recovery is an explicit rerun or rollback.

The standalone controller delegates selection and migration to `deployment_release --omnigent-select` and `--omnigent-migrate` in bounded Compose one-offs from the selected MoonMind image, holding the deployment lock and giving them its own Docker transport so repair does not need the target project's proxy. The main Compose pass layers the operator `.env`, release-owned `.env.deploy`, and controller image overlay in that order. A selection failure leaves the fleet untouched; a migration failure keeps the confirmed installation and records the original error as failed verification. Lost acknowledgments reuse the saved selection, and a still-running one-off is observed before another attempt launches.

**Interrupted agent steps.** Updates do not drain or checkpoint. An agent step interrupted by a replaced host fails with host loss; its old host is confirmed stopped (or fenced for the host janitor) before any successor runs. Only that step receives a new Step Execution under the same workflow and logical step, within its remaining execution ceiling, restoring the latest verified saved workspace or restarting from its admitted inputs with that limitation recorded. Completed steps and confirmed publication effects are not repeated, because every attempt publishes under the logical step's identity. Cancellation starts no successor. A successor is admitted afresh like a schedule occurrence (below), a merge-automation resolver child from its own frozen inputs and within its recorded repository authority, never with changed Harness, Provider Profile, model and effort, launch or capture policy, source, repository route, or publication intent; a refused admission keeps the interrupted attempt's plan, whose launch-time checks report an image that can no longer launch.

**Policies and schedules.** Neither migration nor the API cuts policy or profile versions, re-plans recurring schedules, or fences completion on policy images for an image update: admission binds the installed images to each fresh attempt. A schedule-started occurrence is a fresh attempt: before planning, it compiles its schedule's frozen inputs (task input, Agent Profile snapshot, Provider Profile, model and Skills) against the installed host and server, without rewriting the schedule's definition or action; the stored plan stays as the fallback when that compile is refused (for example a foreign host family), and the occurrence logs why. Schedules consume the release rather than gate it. A bootstrap-owned policy version keeps its recorded images as its image family; bootstrap versions it only for a resource-default migration or a move to another image family.

### 10.9 Capture after state

Confirmed installed images, observations, and unresolved work are recorded beside the original request, and the local record stays usable if publishing an application artifact fails.

### 10.10 Release lock and report result

The primary result is persisted before secondary cleanup, and ownership is released safely. Original and later errors stay distinct. A cleanup failure does not hide a successful installation or make missing mandatory verification look passed.

### 10.11 Bind-mounted checkouts: a host `git pull` alone is not a deployment

A development bind mount can change files without restarting imported code, so affected processes are recreated through the same owner. Actual startup provenance is recorded for diagnosis but is not a runtime admission or compatibility gate.

Bind sources follow the Docker client: Windows drives use Desktop's `/run/desktop/mnt/host/<drive>`, while WSL keeps `/mnt/<drive>`. Bootstrap rejects missing sources and removes stopped controllers with stale network IDs before `up`. No-pull refresh shares a startup budget, stops owned process groups, and reconciles the controller project before reporting a timeout; uncertainty prevents retry. Native Windows prerequisite refresh uses the Linux bootstrap container while retaining the selected image. Cold startup allows unbounded removal/download time; Compose readiness times out after 30 seconds.

## 11. Updater runner execution model

### 11.1 Privileged deployment-control worker

The deployment-control worker is a submission/observation adapter where available, not the only recovery path. Privileged Docker operations stay in trusted deployment infrastructure and cannot be supplied by agent-authored code. A workflow caller without an installed controller reports `runner_unavailable`; the independent host entrypoint installs the controller.

### 11.2 Standalone controller project

The controller's restart policy, durable state directory, and direct Docker socket let an update replace its submitting worker and survive target-project shutdown. Ownership, target, progress, deadline, and attempt budget survive restarts; on restart the controller resumes unfinished work under the stack lock once its earlier one-offs have exited.

The host entrypoint derives the endpoint port from the controller's identity. The network is named `<compose project>_deployment-controller-network` unless `MOONMIND_DEPLOYMENT_CONTROLLER_NETWORK` overrides it, so independent deployments on one host never share the alias. A submission that names no Compose target uses the target the controller derives: the project recorded at bootstrap (else `COMPOSE_PROJECT_NAME` from `.env`), `COMPOSE_FILE` from `.env` (else `docker-compose.yaml` plus its override), and the services that selection renders minus the Docker transport substrate.

**Installation.** When no controller secret exists, or bootstrap left a secret but no reachable endpoint, operation record, or container, the host entrypoint installs the controller with `deploy/controller/bootstrap.py` from the selected release image, running it from that same application image (no separate controller image is published), and submits through it. A still-active legacy writer defers the controller; a controller from the retired shell installer is frozen while its state is checked and, only when idle, killed before it can accept more work, keeping its volume and secret. `--dry-run` installs nothing.

**Refresh.** The default host command refreshes an idle controller from the selected release before submission. It extracts the stdlib bootstrap from the staged target, falling back to an observed installed application image when a rollback target lacks that lifecycle. Bootstrap runs in a temporary trusted image container with the Docker socket and writable controller state on the private controller network, so root-owned records and locks stay readable without host privilege changes; it needs no healthy API or worker and leaves the host checkout, Compose project, mounts, network, and credentials in place. If the identity file is lost, bootstrap first recovers it from the Compose installation without replacing the controller. Replacement holds the stack lock; a capable controller with open work keeps ownership, and an explicitly selected controller, endpoint, or secret keeps its release choice.

**Handoff.** Before applying a release, a managed controller runs its `controller-handoff` one-off to replace a controller running other code. The old process and kernel lock end; the replacement resumes once the one-off exits. Only that operation may be open, and each attempt group hands off once. This lets Settings Operations install new controller behavior without a host step. Older controllers enter through the requested image's `--omnigent-select` callback; otherwise an owner lacking `active-journal-transition` or `unversioned-routing-cutover` is refused before mutation and directed to `./tools/update-moonmind.sh`.

Handoff shares one monotonic deadline within the caller's 300-second allowance, reserving recovery time. Subprocesses consume the remaining budget. Linux process groups are terminated, killed if necessary, and reaped before recovery reconciles the controller project and attempts restoration from saved configuration. New replacements require zero restarts; recovery accepts a historical count only if the same container stays running without another restart during settling. Receipts retain the original failure and distinguish verified restoration from failed or unverified recovery. An available controller can continue applying, including targets predating handoff.

**Repair after failure.** A failed default host or deployment-control submission also tries `bootstrap.py ensure --prefer-requested` under the same lock and open-operation refusal; only a confirmed change (`refreshed: true`) earns a Retry.

### 11.3 Runner image policy

The controller runs trusted deployment-owned code from a verified artifact. The UI exposes no separate runner-image selection, and explicit maintenance inputs do not bypass image allowlists or grant an agent Docker access.

### 11.4 Execution availability and automatic recovery

Workers poll ordinary task queues: no Worker Versioning, promotion canary, startup routing repair, or cohort restoration supervisor.

An installation still routed by a Temporal Worker Deployment crosses once: after `up`, before confirming the operation, the selected image's cutover one-off retires identified release canaries, preserves other pinned executions, and makes ordinary workers current once every routed queue has a live ordinary poller. Unavailable Temporal or a missing poller is retried briefly within the operation, because a terminal operation is not resumed; older-image workers or a persistent failure leave it pending and the operation `partially_verified`. Each run rereads routing, so a lost controller resumes the same operation. After a `completed` receipt, routing never changes again. Once routing is cleared (`completed`, or `attention` with pinned work left to report), images whose versioned workers would re-promote themselves are refused.

Ordinary process restart belongs to Docker/Compose. Replay-sensitive changes need compatible replay or a controlled migration, because removing versioning does not make old histories safe to replay on new code.

Leftover legacy updater jobs and `mm-candidate-`/`mm-retained-` cohort resources (`deployment_maintenance.py`) keep their evidence and any active-work recovery path. An old image-owned controller is not relaunched, because it would recreate the removed architecture, and an unidentified or still-serving cohort is not deleted automatically. An unresolved ownership or data risk stays visible for operator action.

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

- `controller-journey` runs `tests/integration/host_update/test_journal_transition.py` (journal compaction with real PostgreSQL and artifact bytes, controller interruption after partial recreation, resume, and rollback to an image lacking the helper), `tests/integration/host_update/test_controller_refresh.py` (host refresh through a temporary bootstrap container, identity recovery, and open-operation protection), and `test_controller_compose_journeys.py` (disposable-stack repair without the application: pull failure, registry loss after staging, restart, init-db failure).
- The reliability shards run `tests/integration/reliability/test_interrupted_step_replacement_journey.py` on real Temporal and PostgreSQL: the update interrupts one agent step and installs a rebuilt host image, and only that step reruns, on that image and a new host restored from its archived workspace.
- The `omnigent-exact-artifact` credential-free recovery row runs that journey on the pinned Omnigent server and host images with the production host runtime and host-loss predicate, using a controlled provider instead of a model turn; its one pinned host image is also the successor's.

None of these proves automatic bootstrap through an older API binary.

### 12.3 Verification failure rule

Existing result surfaces distinguish `SUCCEEDED`, `FAILED`, and `PARTIALLY_VERIFIED` where supported. Missing mandatory evidence does not become `SUCCEEDED`. The failed check, original error, confirmed progress, and recovery entrypoint stay visible.

## 13. Security and policy model

A single admitted operator does not mean an unprotected application. Operator admission, deployment-capability boundaries, trusted ingress, image/path allowlists, and separation between privileged deployment infrastructure and agent runtimes all apply, without human role management. Credentials resolve through deployment/secret ownership; command output and state summaries are redacted, and tokens, cookies, environment dumps, and personal configuration stay out of Git, command arguments, and ordinary artifacts. An update request does not authorize unrelated production mutation.

## 14. Audit and artifacts

Local operation files retain the requested target, concrete images, identity, timestamps, progress, errors, attempt bounds, and observed outcome. The host entrypoint and available Operations UI expose summaries and redacted logs. Losing the UI copy does not erase the local record; missing metadata is not reconstructed as success.

## 15. Failure and rollback semantics

Unsafe input and unauthorized mutation stop before they occur. Transient failures are reconciled and retried under one operation-level deadline and bounded retry policy that restarts do not reset; uncertain Docker effects are checked before retrying. Cancellation stops further mutation while keeping recoverable state. Exhaustion is not a permanent ban: a new authorized retry reconciles current state and starts a new recorded operation after the previous writer stops, preserving prior errors and target intent. Full-workflow retries are not nested around the controller, which already owns recovery.

Rollback is an authorized update to a previous allowed image through the same controller, with actual schema/data/history compatibility checked. It does not silently downgrade a database or erase saved work, and there is no separate rollback engine. A saved old image is provenance, not proof that every reverse migration is safe.

## 16. Interaction with workflow execution

Workflow submission is one optional way to request the update; ordinary orchestration remains Temporal-owned. Deployment recovery does not depend on a provider-capacity lease, a healthy singleton workflow, or workflow dispatch, since those may be what is being repaired. Queued work and schedule identity survive recreation; missed schedule ticks are not replayed as an unbounded burst, publication intent is not rewritten, and a saved checkpoint is not replaced with an empty retry.

## 17. UI and observability

Settings Operations shows the actual target and outcome, including restart expectations and incomplete verification, without promotion or fleet-management concepts. Detailed output stays outside workflow history. An unavailable observer is not a failed controller, and an installed image is not necessarily a verified product.
