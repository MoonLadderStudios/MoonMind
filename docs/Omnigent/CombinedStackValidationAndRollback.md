# Combined Stack Validation and Rollback

**Document Class:** Canonical declarative
**Status:** Current
**Updated:** 2026-06-28
**Audience:** Local and self-hosted MoonMind operators
**Authority:** Operator-facing startup, validation, rollback, and troubleshooting behavior for the combined MoonMind plus Omnigent Docker Compose stack
**Owning Surface:** Docker Compose deployment with MoonMind and Omnigent services
**Related Docs:** [MoonMind vs Omnigent](MoonMindVsOmnigent.md), [MoonMind Architecture](../MoonMindArchitecture.md)
**Related Implementation:** MM-972, source issue MM-968, coverage DESIGN-REQ-019 through DESIGN-REQ-022, DESIGN-REQ-010, DESIGN-REQ-018

## Purpose

This guide defines the normal operator path for starting, checking, troubleshooting, and rolling back the combined MoonMind plus Omnigent Compose stack without unexpectedly deleting existing MoonMind or Omnigent data.

## DOC-REQ-001 Host Access

The combined stack exposes MoonMind's API and dashboard on:

```bash
http://localhost:7000
```

Omnigent's server and UI are exposed on:

```bash
http://localhost:8000
```

Operators should treat these host ports as the documented local access points. Inside the Compose network, services may still call MoonMind by its service-local URL, such as `http://api:8000`, when that is the container port exposed by the API service.

## DOC-REQ-002 Default Startup

For a normal combined-stack startup, run:

```bash
docker compose up -d
```

This is the default path for local and self-hosted operators. It starts MoonMind, Omnigent, and their required local dependencies using the repository Compose configuration and built-in accounts mode unless an operator has explicitly supplied an OIDC configuration.

## DOC-REQ-003 Existing-Volume Validation Startup

When validating an existing PostgreSQL volume or investigating database initialization behavior, bring the stack up in this explicit order:

```bash
docker compose up -d postgres
docker compose up omnigent-db-init
docker compose up -d api omnigent
```

This sequence keeps database startup, Omnigent database initialization, and application startup separate enough to inspect failures without removing existing volumes. Do not delete PostgreSQL or Omnigent volumes as part of this validation flow.

## DOC-REQ-004 Logs

Use these commands to inspect the Omnigent service and the optional host-profile service:

```bash
docker compose logs omnigent
docker compose logs omnigent-host
```

Add `--tail 200` or `-f` when you need a bounded recent view or a live stream:

```bash
docker compose logs --tail 200 omnigent
docker compose logs -f omnigent-host
```

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

## DOC-REQ-006 Omnigent First Admin Credentials

On first startup, read Omnigent's generated first admin credentials from the Omnigent service logs:

```bash
docker compose logs omnigent
```

Those credentials persist under `/data/admin-credentials` in the `omnigent-data` volume. Restarting or recreating containers should not regenerate them while that volume is retained. If the log output no longer contains the first-start message, inspect the persisted credentials from the retained Omnigent data volume instead of deleting the volume.

## DOC-REQ-007 Host Profile

Start the optional Omnigent host-profile service with:

```bash
docker compose --profile omnigent-host up -d omnigent-host
```

The expected behavior is that `omnigent-host` starts under the `omnigent-host` profile, connects back to the Omnigent server, and registers the local host with the Omnigent UI. Confirm progress with:

```bash
docker compose logs omnigent-host
```

If host registration does not appear in the UI, troubleshoot the host-profile service logs before recreating volumes or changing authentication mode.

## DOC-REQ-008 Manual Validation

After startup, validate the combined stack in this order:

1. Open MoonMind at `http://localhost:7000` and confirm the dashboard loads.
2. Run `curl -fsS http://localhost:7000/healthz`.
3. Open Omnigent at `http://localhost:8000` and sign in with the first admin credentials.
4. Run `curl -fsS http://localhost:8000/health`.
5. Run `docker compose logs omnigent` and confirm there are no repeating startup failures.
6. If using host registration, run `docker compose --profile omnigent-host up -d omnigent-host`, then inspect `docker compose logs omnigent-host` and confirm the host appears in Omnigent.

## DOC-REQ-009 Normal Rollback

Normal rollback should restore MoonMind's host port and remove or disable Omnigent services without deleting data:

1. Stop the combined stack.

   ```bash
   docker compose down
   ```

2. Restore MoonMind's host port mapping to the operator's previous MoonMind-only value if the environment needs to return MoonMind to its earlier host URL.
3. Remove or disable Omnigent services from the active Compose configuration, including `omnigent`, `omnigent-db-init`, and the `omnigent-host` profile service when it was enabled.
4. Start MoonMind again.

   ```bash
   docker compose up -d
   ```

5. Confirm MoonMind health on the restored host port.

Normal rollback preserves PostgreSQL, MoonMind, and Omnigent volumes. It is safe to repeat while investigating startup issues.

## DOC-REQ-010 Optional Destructive Cleanup

Database and volume cleanup is optional, destructive, and separate from normal rollback. Only perform it when the operator has confirmed the data is disposable or has a tested backup.

Examples of destructive cleanup include removing the PostgreSQL volume, the Omnigent data volume, or all Compose-managed volumes:

```bash
docker compose down -v
docker volume rm <postgres-volume-name>
docker volume rm <omnigent-data-volume-name>
```

These commands can permanently delete MoonMind workflow state, Omnigent admin credentials, and Omnigent application data. They are not required to roll back a failed combined-stack startup.

## DOC-REQ-011 Troubleshooting

### GHCR Authentication

If image pulls from GitHub Container Registry fail, confirm the operator has access to the required image and has logged Docker in to GHCR when the image is private:

```bash
docker login ghcr.io
docker compose pull
```

Do not paste access tokens into issue comments, logs, or documentation. Use Docker's credential store or an operator-managed secret path.

### Existing PostgreSQL Volumes

Existing PostgreSQL volumes can retain older schemas, databases, users, and initialization state. Use the explicit validation sequence in `DOC-REQ-003` to separate `postgres`, `omnigent-db-init`, and `api` / `omnigent` failures. Do not use `docker compose down -v` as a first response; it deletes data.

### Port Conflicts

If `http://localhost:7000` or `http://localhost:8000` does not respond, check whether another local process already owns the port:

```bash
docker compose ps
```

On Linux and macOS, a host-level check such as `lsof -i :7000` or `lsof -i :8000` can identify the conflicting process. Resolve the conflict by stopping the other process or changing the host port intentionally in the operator's Compose override.

### Host Config Mount Conflicts

If `omnigent-host` fails immediately, inspect its logs and verify that any host configuration mount points exist, have the expected file-or-directory type, and are readable by Docker:

```bash
docker compose logs omnigent-host
```

A file mounted where a directory is expected, or a directory mounted where a file is expected, should be fixed in the operator override rather than worked around by deleting application data volumes.

### Built-In Accounts and OIDC

Built-in accounts mode is the documented default for the combined local stack. Operators should use the first admin credentials from `docker compose logs omnigent` unless they have deliberately configured an external OIDC provider.

OIDC is a future or operator-provided configuration path for this combined stack documentation. If sign-in behavior looks inconsistent, confirm whether the running environment is using built-in accounts or an explicit OIDC configuration before resetting credentials or deleting volumes.
