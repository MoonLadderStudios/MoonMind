# Combined Stack Validation and Rollback

**Document Class:** Canonical declarative
**Status:** Current
**Updated:** 2026-06-28
**Audience:** Local and self-hosted MoonMind operators
**Authority:** Operator-facing startup, validation, rollback, and troubleshooting behavior when a local MoonMind deployment is paired with an operator-provided Omnigent Compose override
**Owning Surface:** Repository Docker Compose deployment plus any operator-provided Omnigent override files
**Related Docs:** [MoonMind vs Omnigent](MoonMindVsOmnigent.md), [MoonMind Architecture](../MoonMindArchitecture.md)
**Related Implementation:** MM-972, source issue MM-968, coverage DESIGN-REQ-019 through DESIGN-REQ-022, DESIGN-REQ-010, DESIGN-REQ-018

## Purpose

This guide defines the normal operator path for checking the repository MoonMind Compose stack and for validating or rolling back an operator-provided Omnigent Compose override without unexpectedly deleting existing MoonMind or Omnigent data.

## DOC-REQ-001 Host Access

The repository Compose configuration exposes MoonMind's API and dashboard on:

```bash
http://localhost:8000
```

When an operator-provided Omnigent Compose override is enabled, it must document its own host port mapping and avoid conflicting with MoonMind's published port. If that override publishes Omnigent on port 7000, the expected host URL is:

```bash
http://localhost:7000
```

Operators should treat the repository-published MoonMind port and the override-published Omnigent port as the documented local access points for their active Compose configuration. Inside the Compose network, services may still call MoonMind by its service-local URL, such as `http://api:8000`, when that is the container port exposed by the API service.

## DOC-REQ-002 Default Startup

For a normal combined-stack startup, run:

```bash
docker compose up -d
```

This is the default path for local and self-hosted operators. With the repository Compose file alone, it starts MoonMind and its required local dependencies. Omnigent startup requires an explicit operator-provided Compose override that defines its Omnigent services and their port mappings.

## DOC-REQ-003 Existing-Volume Validation Startup

When validating an existing PostgreSQL volume or investigating database initialization behavior with the repository Compose file, bring the MoonMind stack up in this explicit order:

```bash
docker compose up -d postgres
docker compose up init-db
docker compose up -d api
```

This sequence keeps database startup, MoonMind database initialization, and API startup separate enough to inspect failures without removing existing volumes. If an Omnigent override adds its own initialization service, run that service only by the name defined in the active override. Do not delete PostgreSQL or Omnigent volumes as part of this validation flow.

## DOC-REQ-004 Logs

Use these repository-present commands to inspect the MoonMind API and database initialization services:

```bash
docker compose logs api
docker compose logs init-db
```

Add `--tail 200` or `-f` when you need a bounded recent view or a live stream:

```bash
docker compose logs --tail 200 api
docker compose logs -f init-db
```

For Omnigent services supplied by an override, use the exact service names defined by that override.

## DOC-REQ-005 Health Checks

Check MoonMind from the host with:

```bash
curl -fsS http://localhost:8000/healthz
```

Check Omnigent from the host with the port published by the active override. For an override that publishes Omnigent on port 7000:

```bash
curl -fsS http://localhost:7000/health
```

Both commands should return successfully before treating the combined stack as ready for operator validation.

## DOC-REQ-006 Omnigent First Admin Credentials

On first startup, read Omnigent's generated first admin credentials from the Omnigent service logs:

```bash
docker compose logs <omnigent-service>
```

Those credentials persist under `/data/admin-credentials` in the `omnigent-data` volume. Restarting or recreating containers should not regenerate them while that volume is retained. If the log output no longer contains the first-start message, inspect the persisted credentials from the retained Omnigent data volume instead of deleting the volume.

## DOC-REQ-007 Host Profile

Start the optional Omnigent host-profile service using the service and profile names defined by the active override, for example:

```bash
docker compose --profile <omnigent-host-profile> up -d <omnigent-host-service>
```

The expected behavior is that the host-profile service starts under its configured profile, connects back to the Omnigent server, and registers the local host with the Omnigent UI. Confirm progress with:

```bash
docker compose logs <omnigent-host-service>
```

If host registration does not appear in the UI, troubleshoot the host-profile service logs before recreating volumes or changing authentication mode.

## DOC-REQ-008 Manual Validation

After startup, validate the combined stack in this order:

1. Open MoonMind at `http://localhost:8000` and confirm the dashboard loads.
2. Run `curl -fsS http://localhost:8000/healthz`.
3. Open Omnigent at the host URL published by the active override and sign in with the first admin credentials.
4. Run the Omnigent health check documented by the active override.
5. Run `docker compose logs <omnigent-service>` and confirm there are no repeating startup failures.
6. If using host registration, start the override-defined host-profile service, inspect `docker compose logs <omnigent-host-service>`, and confirm the host appears in Omnigent.

## DOC-REQ-009 Normal Rollback

Normal rollback should remove or disable the Omnigent override without deleting data:

1. Stop the combined stack.

   ```bash
   docker compose down
   ```

2. Remove or disable the operator-provided Omnigent override files or profiles from the active Compose configuration.
3. Keep the repository MoonMind Compose file intact unless the operator intentionally changed the MoonMind host port.
4. Start MoonMind again.

   ```bash
   docker compose up -d
   ```

5. Confirm MoonMind health on `http://localhost:8000/healthz`, or on the operator's intentionally configured MoonMind host port.

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

Existing PostgreSQL volumes can retain older schemas, databases, users, and initialization state. Use the explicit validation sequence in `DOC-REQ-003` to separate `postgres`, `init-db`, and `api` failures. If an Omnigent override defines an additional initialization service, validate it separately using the override's service name. Do not use `docker compose down -v` as a first response; it deletes data.

### Port Conflicts

If `http://localhost:8000` or the override-published Omnigent URL does not respond, check whether another local process already owns the port:

```bash
docker compose ps
```

On Linux and macOS, a host-level check such as `lsof -i :8000` can identify the conflicting process. Run the same check for the Omnigent port published by the active override. Resolve the conflict by stopping the other process or changing the host port intentionally in the operator's Compose override.

### Host Config Mount Conflicts

If the override-defined Omnigent host service fails immediately, inspect its logs and verify that any host configuration mount points exist, have the expected file-or-directory type, and are readable by Docker:

```bash
docker compose logs <omnigent-host-service>
```

A file mounted where a directory is expected, or a directory mounted where a file is expected, should be fixed in the operator override rather than worked around by deleting application data volumes.

### Built-In Accounts and OIDC

Built-in accounts mode is the expected default for a local Omnigent override unless that override deliberately configures an external OIDC provider. Operators should use the first admin credentials from `docker compose logs <omnigent-service>` unless they have deliberately configured an external OIDC provider.

OIDC is a future or operator-provided configuration path for this combined stack documentation. If sign-in behavior looks inconsistent, confirm whether the running environment is using built-in accounts or an explicit OIDC configuration before resetting credentials or deleting volumes.
