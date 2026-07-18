# Combined Stack Validation and Rollback

**Document Class:** Canonical declarative  
**Status:** Current  
**Updated:** 2026-07-18  
**Audience:** Local and self-hosted MoonMind operators  
**Authority:** Operator-facing startup, validation, rollback, and troubleshooting behavior for the combined MoonMind plus Omnigent Docker Compose stack  
**Owning Surface:** Canonical `docker-compose.yaml`, its supported profiles, and workflow-requested profile-bound Codex hosts  
**Related Docs:** [MoonMind vs Omnigent](MoonMindVsOmnigent.md), [MoonMind Architecture](../MoonMindArchitecture.md), [Omnigent Adapter](OmnigentAdapter.md), [Omnigent Host OAuth](OmnigentHostOAuth.md), [Omnigent Conformance](ConformanceAndLiveSmoke.md)  
**Related Implementation:** MM-972, source issue MM-968, coverage DESIGN-REQ-019 through DESIGN-REQ-022, DESIGN-REQ-010, DESIGN-REQ-018

## Purpose

This guide defines the normal operator path for starting, checking, troubleshooting, and rolling back the combined MoonMind plus Omnigent stack without unexpectedly deleting existing MoonMind, Omnigent, OAuth, or workflow data.

Three host classes are intentionally distinct:

| Host class | Startup owner | Credential model | Intended use |
| --- | --- | --- | --- |
| Generic `omnigent-host` | Operator / Compose profile | Configured provider API keys | General upstream host validation; never receives MoonMind OAuth homes |
| Dedicated static OAuth host | Operator / canonical Compose profile | One matching MoonMind OAuth volume | Local/bootstrap Codex or Claude host |
| Workflow-requested on-demand Codex host | MoonMind worker after durable lease acquisition | One selected Codex OAuth profile | Run-dedicated managed product path |

All three use stock compatible Omnigent server/host behavior. They do not share credential authority or cleanup semantics.

## DOC-REQ-001 Host Access

The combined stack exposes MoonMind's API and dashboard on:

```bash
http://localhost:7000
```

Omnigent's server and UI are exposed on:

```bash
http://localhost:8000
```

Operators should treat these host ports as the documented local access points. Inside the Compose network, services may call MoonMind or Omnigent by their service-local URLs.

The default Compose configuration derives local Omnigent browser-origin settings from `OMNIGENT_PORT`. The local allowlist covers `localhost`, `127.0.0.1`, `host.docker.internal`, and the Compose-time `HOSTNAME` / `COMPUTERNAME` values when present. For a different browser-visible hostname, set `OMNIGENT_ACCOUNTS_BASE_URL` to that exact origin. Set `OMNIGENT_WS_ALLOWED_ORIGINS` explicitly only when additional trusted browser origins are required.

## DOC-REQ-002 Default Startup

For a normal combined-stack startup, run:

```bash
docker compose up -d
```

This is the default path for local and self-hosted operators. It starts MoonMind, Omnigent, and required local dependencies using the repository `docker-compose.yaml` and built-in accounts mode unless an operator has deliberately supplied OIDC configuration.

Optional services are selected through `COMPOSE_PROFILES` or an explicit `--profile` flag. Do not set `COMPOSE_FILE` to retired OAuth-host overlays; the canonical single Compose file owns supported static host definitions.

Production and credentialed conformance may set complete immutable references:

```dotenv
OMNIGENT_IMAGE_REF=ghcr.io/omnigent-ai/omnigent-server@sha256:<digest>
OMNIGENT_HOST_IMAGE_REF=ghcr.io/omnigent-ai/omnigent-host@sha256:<digest>
```

These `*_IMAGE_REF` values pin the images used by Compose services and take
precedence over the bootstrap-compatible image/tag pairs. Workflow-requested
on-demand hosts do not read `OMNIGENT_HOST_IMAGE_REF`; pin those launches by
setting `OMNIGENT_HOST_IMAGE` itself to the complete digest reference:

```dotenv
OMNIGENT_HOST_IMAGE=ghcr.io/omnigent-ai/omnigent-host@sha256:<digest>
```

When on-demand hosts are enabled, production and credentialed conformance must
pin both paths. A selected policy or conformance profile that requires
immutable images must fail rather than fall back to mutable tags.

## DOC-REQ-003 Existing-Volume Validation Startup

When validating an existing PostgreSQL volume or investigating database initialization behavior, bring the stack up in this explicit order:

```bash
docker compose up -d postgres
docker compose up omnigent-db-init
docker compose up -d api omnigent
```

This sequence keeps database startup, Omnigent database initialization, and application startup separate enough to inspect failures without removing existing volumes. Do not delete PostgreSQL or Omnigent volumes as part of this validation flow.

## DOC-REQ-004 Logs

Use these commands to inspect the Omnigent service and the optional generic host-profile service:

```bash
docker compose logs omnigent
docker compose logs omnigent-host
```

Add `--tail 200` or `-f` for a bounded recent view or live stream:

```bash
docker compose logs --tail 200 omnigent
docker compose logs -f omnigent-host
```

Dedicated static OAuth hosts have separate service names:

```bash
docker compose logs --tail 200 omnigent-host-codex
docker compose logs --tail 200 omnigent-host-claude
```

On-demand hosts are not Compose services. Their operator-facing lifecycle belongs in Workflow Detail and bridge diagnostics. Docker logs are a bounded troubleshooting fallback for the exact deterministic container, not the durable evidence source.

## DOC-REQ-005 Health Checks

Check MoonMind from the host with:

```bash
curl -fsS http://localhost:7000/healthz
```

Check Omnigent from the host with:

```bash
curl -fsS http://localhost:8000/health
```

Both commands should return successfully before treating the combined stack as ready for operator validation.

Application health does not by itself prove a profile-bound host is ready. A Codex run additionally requires profile readiness, lease acquisition, credential preflight, exact host registration, `codex-native` capability, bridge reachability, and session readiness.

## DOC-REQ-006 Omnigent First Admin Setup

On first startup, open Omnigent at `http://localhost:8000` and create the first admin account in the web UI. The Omnigent service logs should explain when no admin exists:

```bash
docker compose logs omnigent
```

The account state persists in PostgreSQL and Omnigent's local account artifacts persist in the `omnigent-data` volume. Restarting or recreating containers should not reset the first-admin flow while those volumes are retained. If sign-in state looks inconsistent, inspect the retained database and `omnigent-data` volume before deleting data.

## DOC-REQ-007 Generic Host Profile

Start the optional generic Omnigent host-profile service with:

```bash
docker compose --profile omnigent-host up -d omnigent-host
```

The expected behavior is that `omnigent-host` connects to the Omnigent server and registers a general local host. Confirm progress with:

```bash
docker compose logs omnigent-host
```

If registration does not appear in the Omnigent UI, troubleshoot the service logs, host token, network, image, and config mounts before recreating volumes or changing authentication mode.

The generic host is not a Codex OAuth host. It is never a fallback when a selected profile-bound host fails.

## DOC-REQ-008 Manual Validation

After startup, validate the combined stack in this order:

1. Open MoonMind at `http://localhost:7000` and confirm the dashboard loads.
2. Run `curl -fsS http://localhost:7000/healthz`.
3. Open Omnigent at `http://localhost:8000` and create or sign in as the first admin account.
4. Run `curl -fsS http://localhost:8000/health`.
5. Run `docker compose logs omnigent` and confirm there are no repeating startup failures.
6. If validating the generic host, run `docker compose --profile omnigent-host up -d omnigent-host`, inspect `docker compose logs omnigent-host`, and confirm the host appears in Omnigent.
7. If validating a static OAuth host, use the matching canonical profile and run the exact credential/readiness check in `DOC-REQ-012`.
8. If validating an on-demand Codex host, launch it through a MoonMind workflow and verify the lifecycle and cleanup evidence in `DOC-REQ-013`; do not create an equivalent container manually.
9. If producing credentialed release evidence, run the versioned live matrix in `DOC-REQ-016` with immutable images and an operator-provisioned action adapter.

## DOC-REQ-009 Normal Rollback

Normal rollback should restore the operator's intended MoonMind-only or reduced-stack topology without deleting data:

1. Stop the combined Compose stack.

   ```bash
   docker compose down
   ```

2. Restore MoonMind's host port mapping to the operator's previous value when the environment must return to an earlier host URL.
3. Remove optional profiles from `COMPOSE_PROFILES` or disable them in the deployment policy.
4. Start MoonMind again.

   ```bash
   docker compose up -d
   ```

5. Confirm MoonMind health on the restored host port.

Normal rollback preserves PostgreSQL, MoonMind, and Omnigent volumes. It also preserves the Codex and Claude OAuth volumes. It is safe to repeat while investigating startup issues.

Before disabling a workflow-requested on-demand host path, drain or terminate active host leases through MoonMind lifecycle controls. Do not use `docker compose down` as proof that on-demand containers have been reconciled because they are worker-created, not Compose-owned.

## DOC-REQ-010 Optional Destructive Cleanup

Database and volume cleanup is optional, destructive, and separate from normal rollback. Perform it only when the operator has confirmed the data is disposable or has a tested backup.

Examples include:

```bash
docker compose down -v
docker volume rm <postgres-volume-name>
docker volume rm <omnigent-data-volume-name>
```

These commands can permanently delete MoonMind workflow state, Omnigent account state, and Omnigent application data. Explicit removal of an OAuth volume also destroys its enrolled credential state and requires a new login ceremony. Destructive cleanup is not required to roll back a failed stack or host startup.

The on-demand host janitor removes only deterministic lease-owned containers and their Omnigent state volumes. It never treats the canonical OAuth volume, PostgreSQL, `omnigent-data`, or unrelated Docker resources as disposable run state.

## DOC-REQ-011 Troubleshooting

### GHCR Authentication

If image pulls from GitHub Container Registry fail, confirm the operator has access to the required image and has logged Docker in to GHCR when the image is private:

```bash
docker login ghcr.io
docker compose pull
```

Do not paste access tokens into issue comments, logs, documentation, or workflow parameters. Use Docker's credential store or an operator-managed secret boundary.

When a digest-pinned reference fails, confirm the digest exists for the expected repository and architecture. Do not replace it with `latest` during a conformance run; use a deliberate compatible digest or classify the image evidence as failed.

### Existing PostgreSQL Volumes

Existing PostgreSQL volumes can retain older schemas, databases, users, and initialization state. Use the explicit validation sequence in `DOC-REQ-003` to separate `postgres`, `omnigent-db-init`, and `api` / `omnigent` failures. Do not use `docker compose down -v` as a first response; it deletes data.

### Port Conflicts

If `http://localhost:7000` or `http://localhost:8000` does not respond, inspect the running containers and host listeners:

```bash
docker compose ps
```

On Linux and macOS, `lsof -i :7000` or `lsof -i :8000` can identify a conflicting process. Resolve the conflict by stopping the other process or changing the host port intentionally in an operator override.

### Trusted Omnigent Origins

If Omnigent chat or upload actions report a forbidden origin, confirm that the browser URL exactly matches `OMNIGENT_ACCOUNTS_BASE_URL` or one of the comma-separated origins in `OMNIGENT_WS_ALLOWED_ORIGINS`. Restart the Omnigent service after changing those settings.

### Host Config Mount Conflicts

If `omnigent-host` fails immediately, inspect its logs and verify that host configuration mount points exist, have the expected file-or-directory type, and are readable by Docker:

```bash
docker compose logs omnigent-host
```

A file mounted where a directory is expected, or a directory mounted where a file is expected, should be fixed in the operator override rather than worked around by deleting application data volumes.

For a dedicated or on-demand OAuth host, also verify that the selected Provider Profile resolves the canonical credential mount and generation. Do not manually replace the volume name in workflow JSON or Docker arguments.

### Built-In Accounts and OIDC

Built-in accounts mode is the documented default for the combined local stack. Operators should create the first admin account in the Omnigent web UI unless they have deliberately configured an external OIDC provider.

OIDC is a future or operator-provided configuration path for this combined stack documentation. If sign-in behavior looks inconsistent, confirm whether the running environment is using built-in accounts or explicit OIDC configuration before resetting credentials or deleting volumes.

### Profile Lease Waits

A Codex host can wait even when Docker and Omnigent are healthy because the selected OAuth Provider Profile permits one active consumer globally. Inspect Workflow Detail for `profile_lease_wait`, the current holder purpose, cooldown state, and cleanup evidence. Do not start another host against the same OAuth volume.

### Credential Preflight Failures

For `CODEX_OAUTH_*` failures, verify the profile is connected and launch-ready through MoonMind Settings. Reconnect through the OAuth Session flow after active consumers are drained. Do not inspect, copy, or archive credential files as troubleshooting evidence.

### Host Registration or Harness Failures

An expected host must register exactly once and advertise `codex-native`. Check the selected image, `/home/app` working directory, host token, Omnigent endpoint, network, and bounded host logs. A different online host is not an acceptable substitute.

### On-Demand Cleanup Failures

A failed cleanup should appear as `host_cleanup=failed`, `leaseReleased=false`, and `janitorRequired=true`. Run the supported janitor/reconciliation operation for the exact profile or lease. Manual Docker removal may eliminate the container but does not repair durable host/provider lease state.

## DOC-REQ-012 Omnigent Host Workspace and Credentials

The optional `omnigent-host` service is a generic Omnigent host. It preserves configured provider API keys such as `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, and `GOOGLE_API_KEY` for runners that need them. It does not mount MoonMind's Codex OAuth volume or set `CODEX_HOME` globally, because the host may serve non-Codex runners and more than one Omnigent user.

The generic host exposes `/workspaces` through the operator-managed `./omnigent_workspaces` directory. The MoonMind workspace entry is mounted read-only from `OMNIGENT_MOONMIND_WORKSPACE`, defaulting to `./omnigent_workspaces/MoonMind`:

```bash
${OMNIGENT_MOONMIND_WORKSPACE:-./omnigent_workspaces/MoonMind}:/workspaces/MoonMind:ro
```

Do not point `OMNIGENT_MOONMIND_WORKSPACE` at a checkout that contains local deployment secrets such as `.env`, provider credentials, private keys, or unreviewed state. Use a sanitized checkout, export, or worktree containing only files that Omnigent sessions should inspect.

Generic-host operator flow:

1. Prepare the sanitized path referenced by `OMNIGENT_MOONMIND_WORKSPACE`.
2. Start or recreate the host:

   ```bash
   docker compose --profile omnigent-host up -d --force-recreate omnigent-host
   ```

3. Confirm the read-only workspace:

   ```bash
   docker compose --profile omnigent-host exec omnigent-host sh -lc 'ls -la /workspaces/MoonMind; test ! -w /workspaces/MoonMind && echo "workspace is read-only"'
   ```

Subscription OAuth remains owned by MoonMind Settings and Provider Profiles. The generic host deliberately never receives `claude_auth_volume` or `codex_auth_volume`; use the matching dedicated host or the workflow-requested Codex path.

### Dedicated OAuth Hosts

The canonical `docker-compose.yaml` defines separate Claude-only and Codex-only hosts behind `omnigent-host-claude` and `omnigent-host-codex` profiles. Complete OAuth through MoonMind first. Each dedicated host runs as UID/GID `1000:1000` from `/home/app`, mounts only its matching OAuth home read/write, clears competing provider credentials, and keeps Omnigent identity in a separate state volume.

To make one or both profiles part of normal startup, set profiles in `.env` without setting `COMPOSE_FILE`:

```dotenv
COMPOSE_PROFILES="omnigent-host-claude,omnigent-host-codex"
```

Then use the platform-neutral command:

```bash
docker compose up -d
```

For a one-time launch:

```bash
docker compose --profile omnigent-host-claude up -d omnigent-host-claude
docker compose --profile omnigent-host-codex up -d omnigent-host-codex
```

Validate canonical configuration, host state, and credential preflight with:

```bash
docker compose config --quiet
docker compose ps omnigent-host-claude omnigent-host-codex
docker compose exec omnigent-host-claude sh -lc 'test -d /home/app/.claude && test -w /home/app/.claude'
docker compose exec omnigent-host-codex /opt/moonmind/check-codex-oauth-host.sh
```

The dedicated hosts retain the sanitized `/workspaces` and read-only MoonMind workspace policy for operator-provided static content. Do not add provider API keys to these services or mount either OAuth volume into the generic host. One OAuth profile remains limited to one active runtime consumer across direct MoonMind and Omnigent execution.

## DOC-REQ-013 Workflow-Requested On-Demand Codex Hosts

An on-demand host is created by the MoonMind worker after `executionProfileRef` is validated and the shared Provider Profile lease and durable host lease are acquired. It is not declared as a per-run Compose service and must not be pre-created by an operator.

The production contract uses:

- a deterministic `mm-omnigent-host-*` container name;
- labels for profile, provider lease, host lease, credential generation, and expiry;
- the selected Codex OAuth volume at `/home/app/.codex`;
- a separate lease-owned Omnigent state volume at `/home/app/.omnigent`;
- a policy-resolved workflow workspace at `/workspaces/run`;
- read-only Skill and tool projections;
- UID/GID `1000:1000`, `/home/app`, a read-only root, and bounded temporary storage;
- the configured MoonMind/Omnigent network and separate host-registration credential.

Validate an on-demand run through durable product evidence:

1. Select an enabled, connected Codex OAuth Provider Profile in MoonMind.
2. Start an Omnigent-backed workflow that requires the on-demand host mode.
3. In Workflow Detail, verify profile resolution, profile lease, host binding, host lease, container start, credential preflight, exact host registration, `codex-native` readiness, bridge authentication, session creation, event/resource harvest, host cleanup, and Provider Profile release.
4. After terminal cleanup, verify replay remains available even though the host is gone.
5. Confirm that only the lease-owned container and Omnigent state volume were removed and that the OAuth volume remains.

A Docker-level bounded inspection may support troubleshooting:

```bash
docker ps -a --filter label=moonmind.kind=omnigent-oauth-host
docker volume ls
```

Do not treat a manually launched lookalike container as valid. It lacks durable binding, profile authorization, host-lease identity, and cleanup ownership.

## DOC-REQ-014 Capacity, Workspace, and Policy Validation

Before enabling on-demand hosts in a deployment, validate:

- the worker can reach the intended Docker daemon;
- policy-selected bind sources are visible to that daemon;
- canonical `WorkspaceLocator` authority is resolved before absolute paths are produced;
- machine capacity and runtime CPU, memory, process, timeout, and temporary-storage limits are enforceable;
- the selected network provides required service reachability;
- any claimed restricted-egress posture is enforced by a network, proxy, or firewall boundary rather than inferred from a Docker network name;
- image/tag or complete image-reference overrides resolve to an available compatible stock host image;
- required Skill/tool projections and optional GitHub capabilities pass their preflight;
- logs, diagnostics, output manifests, artifact handoff, and cleanup evidence remain bounded and redacted.

A deployment that cannot realize the selected policy must fail before host assignment. It must not fall back to the generic host, a different credential, a broader network, a writable root, or an arbitrary workspace path.

## DOC-REQ-015 Rollback by Host Mode

Use the rollback matching the active host mode:

| Mode | Non-destructive rollback |
| --- | --- |
| Generic host | Remove `omnigent-host` from active profiles and run `docker compose down` / `up` as appropriate |
| Static Codex host | Drain active profile/session authority, remove `omnigent-host-codex` from `COMPOSE_PROFILES`, and stop that service |
| Static Claude host | Drain active use, remove `omnigent-host-claude` from `COMPOSE_PROFILES`, and stop that service |
| On-demand Codex | Disable selection through the controlling policy/profile gate, drain active leases, and run supported janitor reconciliation |
| Direct Codex compatibility path | Use its feature gate only after in-flight compatibility runs are drained |

Rollback never requires deleting the OAuth volume, PostgreSQL, `omnigent-data`, or artifact data. Restore a mode only after its profile, image, network, workspace, and cleanup preconditions are again valid.

## DOC-REQ-016 Credentialed Live Conformance

The repository-owned entrypoint is:

```bash
MOONMIND_OMNIGENT_ACTION_COMMAND=/path/to/live-action-adapter \
python tools/run_omnigent_live_conformance.py --mode all \
  --server-image ghcr.io/omnigent-ai/omnigent-server@sha256:<digest> \
  --host-image ghcr.io/omnigent-ai/omnigent-host@sha256:<digest>
```

Requirements:

- the Codex OAuth profile is already enrolled and launch-ready;
- server and host images are immutable complete references;
- `MOONMIND_OMNIGENT_ACTION_COMMAND` names an operator-provisioned adapter that performs real actions against the provider environment;
- the repository semantic action backend is not accepted as an implicit live default;
- every action returns durable `https` or run-output-scoped `file` evidence refs;
- referenced JSON uses the versioned action-evidence schema, identifies scenario and action, records `observed: true`, and repeats returned durable ids;
- the runner resolves and secret-scans every evidence document and rejects missing, malformed, mismatched, opaque, or bare-boolean evidence.

The runner uses the isolated `moonmind-test-omnigent-live` Compose project. It always attempts cleanup and evidence scanning, including after failed startup or failed journeys. Cleanup removes that project's containers and networks only and intentionally never passes `--volumes`, so enrolled OAuth and unrelated volumes survive.

`--mode static` covers restart and durable Workflow Detail replay. Published stock-image proxy compatibility, on-demand lifecycle, and failure-path scenarios can be gated independently in provider environments. A passed release matrix records exact images, architecture, advertised capabilities, workflow/session/lease identities, lifecycle ordering, evidence refs, and cleanup results.
