---
name: update-moonmind
description: Install one immutable MoonMind release through a durable updater that recreates the fleet in place, preserving deployment authority and operator access.
metadata:
  required-capabilities:
    - git
    - docker
    - python3
---

# Update MoonMind

## Invocation

Establish the portable path before running the entrypoint:

```bash
UPDATE_MOONMIND_SKILL_DIR="${UPDATE_MOONMIND_SKILL_DIR:-${MOONMIND_ACTIVE_SKILLS_DIR:+$MOONMIND_ACTIVE_SKILLS_DIR/update-moonmind}}"
test -n "$UPDATE_MOONMIND_SKILL_DIR" && test -f "$UPDATE_MOONMIND_SKILL_DIR/SKILL.md"
```

Inside MoonMind, `MOONMIND_ACTIVE_SKILLS_DIR` is always set and the entrypoint
resolves from it; a checked-in `.agents/skills` directory must never shadow
the selected snapshot. Outside MoonMind, set `UPDATE_MOONMIND_SKILL_DIR` to
the directory containing this `SKILL.md` (no MoonMind-only environment
variables required). Run the update entrypoint exclusively from the resolved
Skill directory: `bash "$UPDATE_MOONMIND_SKILL_DIR/scripts/run-update-moonmind.sh" --repo <deployment-checkout> --branch <branch>` (defaults: current directory and `main`). The portable script requires Python 3.10+, Git, Bash and Docker Compose V2. It checks these before fetching or changing deployment state. `tools/update-moonmind.sh` invokes this same entrypoint.

## Release authority and completion

The entrypoint fetches the selected branch without checking out or resetting local files. It selects the newest published `sha-<commit>` image on the branch's first-parent history (up to 20 commits), verifies that image's source-revision label, and pins the repository digest. The fetched tip is preferred: when its image is not published yet (for example a just-merged commit whose publish workflow is still running) the entrypoint waits a bounded interval for that exact commit, then falls back to the newest published ancestor with a printed notice naming it. Only when no ancestor has a published image is the unavailable release actionable. Never substitute `latest` or rebuild a different source under that identity.

The selected image supplies the canonical application code, migrations, and portable Skills. Deployment-owned `.env`, interfaces, authentication and explicit configuration retain their existing authority. The standalone controller (`deploy/controller`, running in its own `moonmind-controller` Compose project) is the portable semantic entrypoint for both this Skill and MoonMind's deployment tool: it stages the pinned image, recreates changed services with `pull --policy always` then `up -d --pull never --no-build --remove-orphans --wait`, and owns the local operation record and recovery independently of MoonMind health. Install and start it with `python3 deploy/controller/bootstrap.py install` (then `start`); the host CLI also updates and restores the controller, which never replaces itself. The entrypoint reads an installed controller's loopback port from its deployment-owned identity. Settings Operations and a workflow's `deployment.update_compose_stack` tool submit to and observe the same controller, so every entrypoint shares one operation per target. With no controller secret, the entrypoint uses the transitional application-owned updater. It also uses that path when bootstrap left a secret but the endpoint is unreachable and the controller has no operation record or Compose container. A controller with recorded work keeps recovery authority; explicit `--controller-url`, `--controller-secret-file`, or corresponding environment settings always select the controller. `--legacy-direct` confirms the transitional updater for a deployment without a controller and is refused once a controller owns the deployment. That updater pulls the reconciled infrastructure services with `--policy missing`, so a newly pinned infrastructure image is staged without refreshing present ones.

The controller records an immutable submission, starts one named updater with durable ownership, pulls and verifies the pinned digest, persists the desired state, recreates the installed fleet in place, verifies readiness for every affected service, and migrates the singular Omnigent release (server/host digests, launch policy versions, recurring schedule admissions) to the resolved digests. No parallel candidate or retained fleet is started, so nothing is drained afterwards and recreation has a bounded downtime window. The updater can replace the deployment-control service that launched it. A terminal release receipt and verified installed readiness establish completion. An image pull, process exit, or successful container start alone does not.

Before handing off through the transitional updater, the host verifies Docker
access from the updater's own rendered environment and networks. If its
configured `docker-proxy` transport is unavailable, it starts only the proxy
under the existing deployment kernel lock, without recreating an existing
container, retries readiness, and if necessary
recreates only that service once to refresh stale socket binds. A working proxy
stays intact, and explicit external Docker endpoints never trigger local proxy
repair. Recovery preserves the selected Compose files and deployment settings.
It rechecks transport after acquiring the lock and never repairs alongside a
running deployment owner. The probe and lock holder disable image pulls through
a service `pull_policy` overlay, preserving support for older Compose V2 releases.
An explicit worker image is acquired when absent before those checks.
Exhausted recovery stops before handoff and reports the original redacted
errors; it does not claim that the release started.

## Terminal outcomes

Completion requires a terminal release receipt naming the exact source SHA,
the pinned repository digest of the installed image, and the verified installed
readiness for every affected service. The serialized terminal result carries
the verified `sourceRevision` in its outputs alongside `resolvedDigest` and
`releaseReadinessArtifactRef`; receipt recovery validates that bound
`sourceRevision` together with the digest before granting image authority, so
the receipt is self-sufficient and never depends on an unbound second record.
Report the unfinished phase and its recorded recovery owner when bounded
recovery exhausts; preserve primary deployment success when only reporting remains.

- Verify readiness against the declared operator addresses from the immutable
  submission (`--operator-url` plus any configured base URL). An image pull,
  process exit, or successful container start alone is not completion.
- `--dry-run` is strictly non-mutating: it shows the intended release operation
  without fetching, deploying, or recording a submission. Never report a
  dry-run preview as qualification, promotion, or readiness evidence.
- Preserve deployment authority: deployment-owned `.env`, published bindings,
  authentication, and explicit configuration keep their existing values across
  the update. Do not replace that file from a template and do not widen or
  narrow operator access to satisfy a check.

## Recovery

Image selection can fail before any submission exists. For Docker's
`read-only file system` or `no space left on device` errors, inspect both the
host's free disk space and Docker's data disk, reclaim unused image/build caches,
and free host space before restarting Docker Desktop. Preserve deployment
volumes and active workflow state; do not reset the Docker data disk. Retry the
entrypoint after Docker can write again. No release recovery owner is created
by a failed image-selection pull.

If the caller disappears, resume the printed submission with `--resume <submission-id>`. Keep its original image and inputs. The submission names its controller operation (`host-<submission-id>`), so a resume or a lost acknowledgment reattaches to that operation instead of launching a competing writer. When Settings Operations is already applying the same target, the entrypoint observes that operation instead. Inspect the durable result before retrying any side effect; preserve primary deployment success if only cleanup remains. Report the exact unfinished phase and its recorded recovery owner when bounded recovery exhausts. Retry in Settings Operations starts the controller's fresh bounded attempt for the failed operation with prior diagnostics retained; rerunning this entrypoint without `--resume` records a new operation.

The standalone controller project is the replacement owner that survives target-stack shutdown: install, update, or restore it while MoonMind is unhealthy with `python3 deploy/controller/bootstrap.py [install|start|update|restore|status]`. Bootstrap also attaches it to the deployment's private `deployment-controller-network` so the API can reach it. Controller update is host-owned and never self-applied.

## Options

Optional arguments are `--compose-project <name>`, `--image-repository <repository>`, and `--dry-run` (show the intended release operation without fetching or deploying). A `--dry-run` preview never establishes completion: it writes no submission and proves nothing about the installed deployment. The deployment-owned `docker-compose.override.yaml` (or `.yml`) accompanies the image's base configuration.

`--local-build` is an explicit development-only escape hatch for exercising an
unpublished working tree (for example a feature branch awaiting its published
image). It recreates the stack on the repo's live-source development overlay,
writes no release submission, and claims no digest; it is never an immutable
release and must not be used for promotion or qualification. It preserves the
deployment-owned `.env`, requires published bindings to be unchanged
afterwards, and verifies each `--operator-url` health check. Roll back with a
plain `docker compose up -d`. Individual service restarts and source-only
image rebuilds remain outside this contract.

Use `--operator-url <existing-origin>` (repeatable) to declare the installed dashboard/API addresses for verification. It is optional: a fixed published binding verifies its own address, and a wildcard binding (`MOONMIND_API_PUBLISH_HOST=0.0.0.0` or `::`) verifies the loopback origin it publishes on, so the default invocation needs no declaration. Declare an origin to additionally verify a LAN or VPN route. The declaration belongs to the immutable release submission and does not modify `.env`, authentication, or published bindings. Use addresses resolvable from the Docker backend, such as a full VPN hostname. Resume retains the original addresses. A configured `MOONMIND_PUBLIC_BASE_URL` remains a required verification target as well.

Protected operator URLs require an existing authorized credential in the deployment-owned `deploy/state/operator-http-headers.json`, mapping each exact operator origin to its issued `Cookie` and/or `Authorization` header. Preserve this file as secret material outside Git. The updater sends credentials only to that origin, never mints a session or changes identity, and stops before replacement when authentication cannot be verified. Trusted-proxy identity remains owned by the proxy; do not supply asserted-user or forwarded headers.
