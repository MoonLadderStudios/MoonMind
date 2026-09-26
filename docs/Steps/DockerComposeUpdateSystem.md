# Docker Compose Deployment Update System

**Document Class:** Canonical declarative
**Viewpoint:** Module Architecture View
**Status:** Desired State
**Owner:** MoonMind Engineering
**Authority:** One portable deployment controller, in-place Compose updates, and recoverable local deployment state.
**Last Updated:** 2026-09-22

Related: [Agent Instructions](../../AGENTS.md), [Temporal Architecture](../Temporal/TemporalArchitecture.md), [Provider Profiles](../Security/ProviderProfiles.md), [Secrets System](../Security/SecretsSystem.md).

This is the target design, not a claim that all current code has migrated. Existing candidate/retained fleets, worker-version promotion, and availability-supervisor paths may remain until their active consumers are safely migrated. They are not permanent requirements to preserve or optimize. Removing them must preserve saved work, histories, deployment settings, and the supported recovery path.

## 1. Purpose

An operator should be able to update or repair MoonMind without depending on the healthy application they are trying to repair. The normal operation is image pull followed by changed-service recreation, with useful local progress and an honest result.

```text
resolve and verify target -> persist deployment intent
-> pull required images -> run required initialization/migrations
-> compose up changed services -> verify and record result
```

Image acquisition needed to resolve or inspect the target happens before persisting that target. Persisted intent records what the operator requested, not proof that installation succeeded.

The steady state is one installed Compose fleet per instance, not blue/green candidate and retained fleets. A bounded interruption during recreation is an accepted tradeoff. Independent deployments remain independent and do not require shared release state or a central coordinator.

## 2. Summary

One portable controller implements updates for the host entrypoint and the Settings Operations action. The host path must remain usable when the API, Temporal workers, provider manager, or application artifact service is unhealthy.

The controller owns image resolution, the per-stack lock, local durable state, Compose mutation, verification, and bounded recovery. Temporal and the UI may request or observe an update, but neither is required for the controller to keep making progress. An observer must not become a second update algorithm.

Use existing Docker/Compose, local job files, and process ownership. The controller is one small separate release service in its own Compose project (see section 11.2) and is the replacement owner, not a second deployment mode: do not add a parallel supervisor, promotion state machine, or mandatory approval workflow.

## 3. Terminology

**Stack target:** The deployment-owned Compose project, files, paths, and settings the controller may operate. Resolve observable defaults from that installation and keep explicit overrides.

**Target image:** The requested allowlisted MoonMind tag or digest. A tag is a selector. Resolve and record the concrete image that will run.

**Controller:** The standalone deployment controller in its own Compose project (`deploy/moonmind-controller`), separate from the MoonMind stack. It owns its durable state, restart policy, direct Docker socket mount, and one small authenticated local endpoint guarded by a deployment-owned secret, and is capable of outliving the worker or API that submitted work to it. Its Docker transport and command endpoint survive target-project shutdown. The host CLI installs, starts, updates, and restores the controller; the controller never replaces itself, and controller updates are serialized against active deployment mutation. Its privileged execution boundary is deployment-owned, not selectable by an agent. No agents receive sockets or unrestricted controller access; its lifecycle is host-owned.

**Update record:** The local durable request, observed progress, attempts, and result for one operation. It survives application and controller restarts and distinguishes requested from confirmed state.

## 4. Goals and non-goals

### 4.1 Goals

Make default updates and recovery work without hidden switches or manual version alignment. Preserve data, existing operator access, and explicit settings. Keep failures understandable from local records even when interactive chat and the application UI fail.

Use one owner for each side effect and reconcile uncertain outcomes before repeating them. A transient pull or observation error may be retried within a bound. Missing access authority, unsafe input, and a genuine data/history incompatibility are not ignored.

### 4.2 Non-goals

No multi-user role system, arbitrary shell or Docker UI, zero-downtime deployment platform, permanent old-version fleets, or per-release re-admission of every schedule. No new compatibility fingerprint based on SHA, patch, or major/minor equality. No independent implementations for host and workflow callers.

## 5. Core invariants

- Only the authorized operator and trusted deployment infrastructure can mutate an allowlisted stack. Agent workspaces do not gain Docker access.
- Exactly one controller mutates a stack at a time. An unreadable Docker daemon or lost acknowledgment does not prove that another updater is absent.
- Desired state, operation logs, and original errors remain readable locally without a healthy API, application database, object store, or LLM. Application artifacts and UI projections are secondary copies, not the only recovery authority.
- Data volumes, saved work, histories, explicit configuration, and access protections survive updates. Cleanup cannot delete the only recoverable copy.
- Image identities establish integrity and provenance. Interface behavior, schema support, and actual replay requirements establish compatibility. Those are different questions.
- Success requires observed installation and verification. Pending, failed, and unavailable checks stay distinguishable. Secondary reporting or cleanup failures cannot erase a confirmed primary result.

## 6. Settings → Operations UX

The existing Operations surface remains at `/settings/operations`. The operator chooses the target MoonMind image, sees that services may restart, and starts an update. Do not require independent controller images, worker versions, routing modes, or per-harness image pins.

Show the selected target, observed installed state, current action, original failure when present, and local/log artifact references. A reason can be recorded from the request or entrypoint without making a derivable default an extra mandatory question.

Changed-service recreation is the default. Force recreation is an explicit maintenance option, not a hidden requirement for ordinary correctness. Destructive cleanup is not bundled into the default update.

The application may be temporarily unavailable while it is updated. The host command and local progress record remain the recovery path rather than requiring the dashboard to supervise its own replacement.

## 7. API contract

Preserve the existing public Operations entrypoints while changing their implementation owner:

```text
POST /api/v1/operations/deployment/update
GET  /api/v1/operations/deployment/stacks/moonmind
GET  /api/v1/operations/deployment/image-targets?stack=moonmind
```

The submission identifies the stack and target image, with existing explicit maintenance options where supported. Keep current client fields usable during migration; this document does not introduce a replacement API schema or another job-status vocabulary.

The API submits or observes the same controller operation the host uses. Its response identifies that operation and its actual current state. An API/workflow timeout is not permission to launch another updater or evidence that the underlying operation failed.

## 8. Executable tool contract

### 8.1 Tool name

`deployment.update_compose_stack` remains the typed privileged operation. Inputs are resolved against deployment-owned policy and defaults. Do not accept arbitrary commands, paths, image repositories, or caller-selected privileged runtime settings.

### 8.2 ToolDefinition shape

Keep the owning executable definition and API schema authoritative rather than duplicating their full JSON schemas in prose. Retain deployment-control capability checks and an allowlisted image target. Existing mode, wait, smoke-check, and cleanup fields must not allow an unverified result to be labeled successful or turn normal updates into a combinatorial mode system.

### 8.3 Representative plan node

A workflow may request this existing operation when authorized. It does not implement its own pull/recreate loop, approval chain, or retry budget on top of the controller. The host recovery entrypoint must not need to create a `MoonMind.UserWorkflow` first.

## 9. Desired state storage

Use the existing deployment-state directory and its image env overlay, JSON metadata, lock, and operation files. The normal mounted boundaries remain a read-only host project at `/workspace/host_project` and narrowly writable state at `/workspace/deployment_state` where configured.

Preserve the operator's `.env`, Compose overrides, project identity, credentials, bindings, and network attachments. Update only controller-owned image/operation fields and preserve unrelated entries. Write state safely so interruption leaves recoverable intent, not a truncated sole copy.

Persist selected concrete images and the operation identity before recreating services. A pulled image alone is not an installed release. A requested branch tip whose image is still publishing is an availability condition: use the existing bounded resolution policy, preserve explicit target intent, and report the actual selected source. Do not silently substitute a different target for an explicit request.

The installed Omnigent server/host selection belongs to this same deployment owner. Future managed launches resolve it from installed deployment intent. Historical attempt records retain the actual image and session that executed them.

## 10. Execution lifecycle

### 10.1 Validate request

Resolve the existing project, settings, and target through trusted deployment inputs. Check authorization, repository/path allowlists, and Compose validity. Derive observable values rather than failing because an optional flag was omitted.

A broken application health check is diagnostic input, not a prerequisite that prevents the controller from repairing it. Preserve access and storage boundaries before mutation without demanding that the old API, provider-manager workflow, chat, or application evidence store already works.

### 10.2 Acquire deployment lock

Reuse the existing per-stack local lock across host and UI submissions. A second request observes/reattaches to its existing operation or waits within a bound. Do not transfer ownership merely because a PID or timestamp looks old in another container namespace.

Staging/apply failures retry automatically up to the bounded per-group attempt budget, so a transient first failure reaches a terminal `failed` (or a later `succeeded`) instead of staying indefinitely open. Controller replacement (install/update/restore) holds this same stack lock, so it shares one atomic exclusion boundary with deployment mutation rather than a separate controller-only lock.

Restart recovery reconciles before applying: only the newest open operation per stack survives, and an open operation older than a confirmed installation for the same stack is superseded, never replayed over confirmed intent. A retried submission for an already-installed image reattaches to the recorded terminal success (including after a lost response) instead of repeating the mutation.

Do not retain a second background availability owner that competes for this lock or changes the target. Locks for separate deployment projects remain independent.

### 10.3 Capture before state

Record enough observed configuration, selected services, concrete images, and progress to reconcile the operation. Keep this small and redacted. Missing observations are explicit unknowns, not permission to invent ownership or discard saved state.

### 10.4 Pull images and check runner integrity

Resolve the allowlisted target and verify the concrete artifact being executed. The controller can run independently of the service it replaces. Lost launch acknowledgment requires checking the existing owned process/container before another launch.

Reuse native image pull and content-addressed caching. Do not require equal source revisions between the caller, controller, installed worker, and managed runtime. A different SHA is not by itself an incompatible interface.

### 10.5 Persist desired image

After resolving the target, persist the selected deployment intent before recreation. There is no candidate qualification or routing-promotion prerequisite. Preserve the distinction between target selection and installed success throughout recovery.

### 10.6 Recreate services

Normal service replacement uses the equivalent of:

```bash
docker compose pull --policy always
docker compose up -d --pull never --no-build --remove-orphans --wait
```

under bounded timeouts, after staging all images needed for the requested
update and before any recreation. In the application-owned updater, services
built from the MoonMind image pull with `--policy always`; the other reconciled infrastructure services pull with
`--policy missing`, so an infrastructure image the release newly pins (for
example a MinIO digest change) is staged while present images are never
refreshed, and `up --pull never` cannot fail with a missing image. These are semantic commands after resolving
the correct deployment files, env overlays, project, and service set, not
permission to operate on an arbitrary project. The deployment-owned Compose file set (including `COMPOSE_FILE` selection) passes through unchanged, and the deployment-owned `.env` layers under the controller-generated image overlay so operator authentication, bindings, and infrastructure versions are preserved. Privileged-endpoint submissions validate the safe shape of project, paths, services, and image references before persistence. Do not use routine
`docker compose down`, force-recreate, or volume/image pruning; those are
explicit repair operations only, never automatic escalation. Recreate only
changed services by default.

Preserve dependency order and required `init-db`/schema migration gating before new dependent services start. Bring necessary infrastructure up in the existing Compose lifecycle. A failed migration preserves the original error and data rather than starting an incompatible application or attempting a destructive downgrade.

### 10.7 Verify desired state

Observe actual installed services and concrete images, required health, ordinary workflow dispatch, and the operator access path. A container running is not sufficient proof that work can execute or that the dashboard serves the intended release.

An unavailable optional integration is reported separately. Missing mandatory verification cannot be converted to success. Verification remains bounded and uses the existing health/functional boundaries, not another release qualification subsystem.

### 10.8 Migrate the singular Omnigent release

An ordinary MoonMind update also checks the deployment-configured Omnigent
server and required host image channels (image/tag inputs present in the operator `.env` or worker environment) for newly published artifacts. Resolve
mutable tags to concrete digests, assess the required runtime behavior, and
advance the installed release when a suitable new image is available for a configured channel; channels without configured inputs retain their recorded refs. Record
the selected digests in deployment-owned state before restarting consumers and refresh their running
consumers through the same Compose owner. This includes the server, API and
agent runtime worker; already-running static host profiles follow a changed
shared host image (recreated without draining, checkpointing, or deferring for active sessions -- drain or checkpoint active Codex/Claude work before updating), while inactive profiles remain inactive. A MoonMind update
with no suitable new Omnigent image for a configured channel leaves the installed release in place.
An explicit operator digest pin persisted in the operator `.env` remains authoritative until changed. When a recorded candidate later fails startup or verification, the new desired state stays recorded with no automatic rollback; recovery is an explicit operator rerun or rollback.

Reconcile uncertain recreations before repeating them. Future launches follow
the installed runtime while preserving explicit harness/provider choices and
the actual image identity of attempts already in progress. A version-number
difference by itself does not block an otherwise compatible release.

Do not copy each image change into new policy/profile versions and re-admit every recurring schedule merely to keep digest strings equal. Remove those independent launch pins through their owning migration, preserving schedule identity, cadence, paused state, publication intent, and in-flight session evidence. Do not rewrite what historical attempts actually ran.

### 10.9 Capture after state

Record confirmed installed images, service and functional observations, and unresolved work alongside the original request. The local record remains usable if publishing an application artifact fails.

### 10.10 Release lock and report result

Persist the primary result before secondary cleanup and release ownership safely. Keep original and later errors distinct. Cleanup failure is not permission to hide successful installation or to claim missing mandatory verification passed.

### 10.11 Bind-mounted checkouts: a host `git pull` alone is not a deployment

A development bind mount can change files without restarting imported code. Recreate affected processes through the same supported owner. Record actual startup provenance for diagnosis, but do not turn checkout equality into a universal runtime admission or compatibility gate.

Preserve POSIX and Windows Docker Desktop path handling. The Linux Docker daemon's host bind namespace is not the same as a WSL user-distro `/mnt/<drive>` path. Resolve existing daemon-visible mounts, including `/run/desktop/mnt/host/<drive>` where applicable, and do not create an empty directory over a missing source mount. Controller bootstrap resolves both bind sources through this adapter before rendering its Compose project, failing fast on a missing required source. This behavior is a supported deployment boundary, not a reason to add a second Windows updater.

## 11. Updater runner execution model

### 11.1 Privileged deployment-control worker

The existing deployment-control worker is a submission/observation adapter where available. It is not the sole path to recovery. Privileged Docker operations remain in trusted deployment infrastructure and cannot be supplied by arbitrary agent-authored code.

### 11.2 Standalone controller project

The controller runs as one small service in its own Compose project with a configured restart policy, a durable host state directory, and a direct Docker socket mount (a proxy is acceptable only if controller-owned in that separate project), so an update can replace its submitting worker and survive target-project shutdown. Local durable ownership, selected target, progress, deadline, and attempt budget survive restarts of MoonMind and of the controller itself: on restart the controller inspects Docker and converges only unfinished work toward the same target. A caller timing out reattaches to that operation rather than duplicating mutation. The controller exposes one small authenticated local endpoint backed by a deployment-owned secret; no agent receives the socket or unrestricted controller access. The host entrypoint derives the installed endpoint port from the controller's deployment-owned identity. The legacy ephemeral application-owned updater container is retired through the cutover in §11.4; it is not a second supported owner. Until a deployment installs a working controller, the host entrypoint updates through the application-owned updater when no controller secret exists, or when bootstrap left a secret but no reachable endpoint, operation record, or Compose container. A controller with recorded work retains recovery authority, and explicit controller selection is never bypassed. This fallback is removed once the entrypoint can install a published controller image itself.

### 11.3 Runner image policy

Use trusted deployment-owned controller code and verify its artifact. Do not expose a second independent runner-image selection in the UI. Explicit maintenance inputs do not bypass image allowlists or grant an agent Docker access.

### 11.4 Execution availability and automatic recovery

The supported default has one installed fleet recreated in place. Remove parallel candidate/retained fleets, promotion canaries, application-owned worker-version routing gates, and the retained-cohort restoration supervisor as their consumers migrate. Do not replace them with a renamed promotion or compatibility subsystem.

Ordinary process restart belongs to Docker/Compose. An interrupted authorized update reconciles its local record and Docker state through the same controller. Unknown state is neither success nor permission to destroy something that may still own work.

Preserve Temporal histories and durable work. Replay-sensitive changes require compatible replay or a controlled migration. Drain/checkpoint only where required for actual active work, not as a permanent zero-downtime fleet design. Removing versioning does not make arbitrary old histories safe to replay on new code.

Retire legacy updater jobs and cohort resources through an explicit, scoped transition. Keep their evidence and any active-work recovery path. Do not blindly relaunch an old image-owned controller that would recreate the removed architecture. Do not automatically delete an unidentified or still-serving cohort. A genuine unresolved ownership/data risk remains visible for operator action, without growing a permanent reconciliation framework around obsolete state.

A provider-manager fault remains a real fault with its own diagnosis. It must not become a precondition that prevents installing its fix. Maintenance can expose incomplete operations, but must not independently promote images, overwrite target intent, or restart retired controllers.

## 12. Verification model

### 12.1 Compose-level verification

Render and compare the selected project's actual settings, bindings, mounts, networks, and service identities. Preserve deployment-owned ports, external ingress networks, authentication settings, and data mounts. Fresh-install defaults cannot silently replace installed choices.

Inspect actual installed service containers, distinguishing the controller's own one-off container from ordinary services. Scope all observation and cleanup to the owned project. No global prune or broad name-prefix deletion is allowed.

### 12.2 Application-level verification

Derive operator URLs from the configured public origin and actual bindings where possible. An explicit `--operator-url` supplies a non-derivable target or overrides/adds a target; it is not mandatory for an address the controller can already determine.

Verify the real configured ingress and protected read-only application path. Preserve TLS and authentication. Do not forge identity headers, weaken admission, or substitute a container-internal probe as proof of an external route. A loopback probe does not prove a distinct LAN/VPN/proxy route.

Missing credentials or unreachable client networks limit what can be verified; report that limitation without making healthy pre-update browser access a universal prerequisite to repair. Do not mint a new session or identity authority in the updater. Post-update success still requires the mandatory checks applicable to the operation.

Implementation verification uses focused default-path tests plus CI for broader integration. Cover interruption, lost acknowledgment, concurrent submission, required schema initialization, repair while application orchestration is unhealthy, access preservation, supported host paths, and retained work/history behavior. Use representative real-service journeys instead of multiplying every historical release-mode combination.

CI can qualify a candidate implementation, while deployment observations identify the artifact actually installed. Neither a different candidate's green CI nor a helper returning a fabricated success proves the operator journey. Provider/hardware checks remain separately identified when unavailable. Do not require full CI or paid-provider qualification inside every update.

### 12.3 Verification failure rule

Use existing result surfaces to distinguish `SUCCEEDED`, `FAILED`, and `PARTIALLY_VERIFIED` where supported. Do not invent another lifecycle solely for reporting. Missing mandatory evidence cannot become `SUCCEEDED`. Keep the failed check, original error, confirmed progress, and recovery entrypoint visible.

## 13. Security and policy model

One admitted operator does not mean an unprotected application. Preserve the existing operator-admission and deployment-capability boundaries, trusted ingress, image/path allowlists, and separation between privileged deployment infrastructure and agent runtimes. Do not introduce human role management for this single-user operation.

Resolve credentials through existing deployment/secret ownership. Redact command output and state summaries. Tokens, cookies, full environment dumps, and personal configuration must not enter Git, command arguments, or ordinary artifacts. An update request never authorizes unrelated production mutation.

## 14. Audit and artifacts

The local operation record is the durable source for requested target, concrete images, operation identity, timestamps, progress, original errors, attempt bounds, and observed outcome. Reuse existing files and references rather than adding a database, event platform, or mandatory evidence schema for every internal phase.

Expose a small human-readable summary and redacted logs through the host entrypoint and, when available, the existing Operations UI/artifacts. Loss of the latter cannot erase the former. Missing metadata should be reported honestly rather than reconstructed as invented success.

## 15. Failure and rollback semantics

### 15.1 Failure behavior

Stop unsafe input and unauthorized mutations before they occur. Reconcile and retry transient failures within the existing bounded owner. An application outage must not disable the independent repair path. Failed verification preserves local progress and explains what remains unknown or broken.

### 15.2 Retry behavior

Reuse one operation-level deadline and bounded retry policy. Restarts do not reset the budget. Check uncertain Docker effects before retrying. Cancellation stops further mutation through the owning path while retaining recoverable state.

Exhaustion is not a permanent ban on updating the same target. A new explicitly authorized retry may reconcile current state and start a new recorded operation after the previous writer is stopped. Preserve prior errors and target intent. Do not nest full-workflow retries around a controller that already owns recovery or revive a retired controller during migration.

### 15.3 Rollback behavior

Rollback is an explicit authorized update to a previous allowed image through the same controller. Check actual schema/data/history compatibility. Do not silently downgrade a database, erase saved work, or add a separate rollback engine. A saved old image is useful provenance, not proof that every reverse migration is safe.

## 16. Interaction with workflow execution

Workflow submission is one optional way to request the existing privileged update. Ordinary orchestration remains Temporal-owned. Deployment recovery must not depend on a provider-capacity lease, a healthy singleton workflow, or workflow dispatch to repair those same components.

Preserve queued work and schedule identity during recreation. Resume through existing execution/session recovery where safe. Do not replay missed schedule ticks as an unbounded burst, rewrite publication intent, or replace a saved checkpoint with an empty retry.

## 17. Interaction with Settings information architecture

Deployment update stays in Settings Operations. Reuse its current submission and observation surfaces. No new top-level release dashboard or separate settings hierarchy is required.

## 18. UI copy recommendations

Describe the operation plainly: "Pull the selected images, recreate changed services, verify the deployment, and preserve a local recovery record. Services may restart."

Show the actual selected target and result. Do not call a failed or partially verified update complete. Do not expose promotion or fleet-management concepts that the default design removes.

## 19. Observability

Use existing operation status plus a small progress message and log reference. Keep detailed output outside workflow history. Distinguish an unavailable observer from a failed controller, an installed image from a verified product, and a requested action from a confirmed effect. Basic failure diagnosis must work without chat or an LLM.

## 20. Locked decisions

One controller serves host and UI requests from its own Compose project. Normal updates pull and recreate changed services without routine teardown. Recovery works independently of the application being repaired. Local state and original errors survive interruption. Deployment integrity, operator access, and saved work remain protected.

Permanent candidate/retained fleets, promotion qualification, and independent schedule/profile image pins are not part of the desired default. Transitional support has real consumers and an explicit removal condition. Do not preserve it solely because an older issue or test checklist described it.

## 21. Summary

Reliable updates come from a small operation with one owner, ordinary Compose behavior, preserved intent, bounded recovery, and readable evidence. Tests and documentation follow the supported outcome. They must not force the implementation to reconstruct the complicated release system this design replaces.
