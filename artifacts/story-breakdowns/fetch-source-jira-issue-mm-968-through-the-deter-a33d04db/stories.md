# Story Breakdown: MM-968: Omnigent Docker Compose

- Source: MM-968: Omnigent Docker Compose
- Source path: null
- Source reference path: null
- Source document class: imperative-override
- Extracted at: 2026-06-27T21:15:23Z
- Story output mode: jira

## Design Summary

The design adds Omnigent to MoonMind's Docker Compose deployment while preserving MoonMind's internal service contract. MoonMind's API moves to host port 7000 so Omnigent can own host port 8000, and both services share the existing Postgres container with separate Omnigent database credentials. The integration also defines persistent Omnigent storage, optional host registration through a Compose profile, namespaced environment configuration, tests, documentation, validation, troubleshooting, and rollback guidance. Non-goals keep MoonMind's existing service topology intact, avoid schema merging, avoid mandatory Omnigent host/cloud sandbox setup, and defer SSO/OIDC implementation beyond exposed configuration.

## Coverage Points

- DESIGN-REQ-001 (requirement): MoonMind host API port moves to 7000 - The api service must publish ${MOONMIND_API_HOST_PORT:-7000}:8000, preserving the container port and internal api:8000 URLs. Source: Implementation Plan / Change MoonMind API host port.
- DESIGN-REQ-002 (configuration): MoonMind port is configurable from .env-template - .env-template must include MOONMIND_API_HOST_PORT=7000 and docs/scripts that describe localhost:8000 for MoonMind must move to localhost:7000. Source: Implementation Plan / Change MoonMind API host port.
- DESIGN-REQ-003 (integration): Omnigent service owns host port 8000 - The omnigent server service must be reachable from the host at http://localhost:8000 while listening on container port 8000. Source: Summary / Goals / Omnigent server.
- DESIGN-REQ-004 (configuration): Omnigent configuration uses prefixed environment variables - Omnigent settings must live in the existing .env flow with OMNIGENT_* names to avoid collisions with MoonMind's shared Postgres settings. Source: Implementation Plan / Omnigent-specific env vars.
- DESIGN-REQ-005 (integration): Omnigent reuses the existing postgres service - The Compose topology must not add a second Postgres service for Omnigent and must use the existing postgres hostname on local-network. Source: Goals / Shared Postgres.
- DESIGN-REQ-006 (state-model): Omnigent database and role are separate - Omnigent must use its own database and login role rather than MoonMind's primary database/schema. Source: Goals / Separate database and role.
- DESIGN-REQ-007 (migration): Database initialization is idempotent for existing volumes - A one-shot omnigent-db-init service must create the Omnigent role/database only when missing, depend on a healthy Postgres service, and be safe to rerun. Source: Implementation Plan / omnigent-db-init.
- DESIGN-REQ-008 (security): Omnigent DATABASE_URL is explicit and prefixed - The server must build DATABASE_URL from OMNIGENT_POSTGRES_* variables and fail through Compose interpolation when the Omnigent password is not supplied. Source: Implementation Plan / Omnigent server service.
- DESIGN-REQ-009 (artifact): Omnigent server persists artifacts and admin credentials - omnigent-data must mount at /data so artifacts and /data/admin-credentials survive container restarts. Source: Goals / Server persistence.
- DESIGN-REQ-010 (security): Omnigent auth defaults to built-in accounts - Built-in accounts mode is the default, OIDC/header auth is only exposed through environment variables, and SSO/OIDC implementation is not part of this issue. Source: Non-goals / Auth configuration.
- DESIGN-REQ-011 (artifact): Optional server config example is non-secret - deploy/omnigent/server-config.example.yaml should document non-secret server settings and future sandbox placeholders without requiring a config file for first pass startup. Source: Implementation Plan / Optional server config.
- DESIGN-REQ-012 (requirement): Omnigent host is optional and profile-gated - The omnigent-host service must exist behind the omnigent-host Compose profile so default deployments do not require it. Source: Goals / Optional host.
- DESIGN-REQ-013 (integration): Omnigent host starts the host process - Because the host image defaults to an inert command, Compose must override it with ["omnigent", "host"]. Source: Background / Host image command.
- DESIGN-REQ-014 (configuration): Host server URL comes from config file - deploy/omnigent/host-config.yaml must set server: http://omnigent:8000 and be mounted into OMNIGENT_CONFIG_HOME. Source: Implementation Plan / Host config.
- DESIGN-REQ-015 (constraint): Host config and state mounts do not conflict - The host config bind mount must not be hidden by a named volume over the same directory; state belongs under a separate path/volume. Source: Implementation Plan / Host mount note.
- DESIGN-REQ-016 (integration): Host supports local workspace and optional provider keys - The host service should pass optional LLM provider keys from .env and mount ./omnigent_workspaces for runtime workspaces without configuring managed cloud sandboxes. Source: Implementation Plan / Host service env.
- DESIGN-REQ-017 (test): Compose topology and env-template have regression tests - Tests must parse docker-compose.yaml and .env-template to verify ports, services, dependencies, profiles, volumes, and prefixed Omnigent env vars. Source: Implementation Plan / Add tests.
- DESIGN-REQ-018 (constraint): Existing MoonMind topology stays intact - The change must not remove MoonMind's existing Postgres, Temporal, Keycloak, MinIO, worker setup, or internal API assumptions. Source: Non-goals.
- DESIGN-REQ-019 (observability): Operator documentation covers startup and runtime checks - Docs must cover ports, default startup, optional host profile startup, logs, health checks, and first admin credential discovery. Source: Implementation Plan / Update docs.
- DESIGN-REQ-020 (migration): Manual validation covers fresh, existing, and host-profile deployments - Validation steps must prove fresh stacks, existing Postgres volumes, and profile-enabled host registration work as intended. Source: Manual Validation Plan.
- DESIGN-REQ-021 (migration): Rollback and cleanup avoid accidental data loss - Rollback must restore MoonMind's host port, remove/disable Omnigent services, and keep destructive DB/volume cleanup optional and explicit. Source: Rollback Plan.
- DESIGN-REQ-022 (observability): Troubleshooting covers expected operational risks - Docs or validation guidance must address GHCR auth, existing-volume init behavior, port conflicts, host config mount conflicts, and auth-mode confusion. Source: Risks and Mitigations.

## Story Candidates

### STORY-001: Move MoonMind API host port to 7000

- Short name: moonmind-port-scaffolding
- Source reference: MM-968: Omnigent Docker Compose; path: null; claim IDs: []
- Sections: Summary, Goals, Implementation Plan / Change MoonMind API host port
- Dependencies: None
- Independent test: Run docker compose config and the targeted compose/env unit tests, then start the API stack and verify curl -f http://localhost:7000/healthz succeeds while internal service references still use http://api:8000.
- Needs clarification: None

Description:
As a local MoonMind operator, I need MoonMind's API and dashboard host entrypoint to move from localhost:8000 to localhost:7000 without changing internal service communication, so Omnigent can use host port 8000 in the same Compose stack.

Acceptance criteria:
- docker-compose.yaml publishes the api service as ${MOONMIND_API_HOST_PORT:-7000}:8000.
- The api container port, health check, and internal api:8000 service URLs are unchanged.
- .env-template contains MOONMIND_API_HOST_PORT=7000.
- Developer-facing docs, onboarding references, and tests that identify MoonMind's host URL use http://localhost:7000.
- No existing Postgres, Temporal, Keycloak, MinIO, worker, or internal MoonMind service topology is removed or renamed.

Requirements:
- Expose MoonMind API on host port 7000 by default through a namespaced environment variable.
- Preserve container-local port 8000 and service-to-service communication through http://api:8000.
- Update env-template, documentation, and port regression tests in the same story.

Owned coverage:
- DESIGN-REQ-001: Implements the required host-port remap while preserving the internal API contract.
- DESIGN-REQ-002: Adds the new .env-template variable and updates operator/developer references for the new host URL.
- DESIGN-REQ-018: Guards against accidental changes to MoonMind's existing internal topology while moving only the host mapping.

### STORY-002: Add Omnigent server using shared Postgres

- Short name: omnigent-shared-postgres
- Source reference: MM-968: Omnigent Docker Compose; path: null; claim IDs: []
- Sections: Summary, Goals, Implementation Plan / Omnigent env vars, Implementation Plan / omnigent-db-init, Implementation Plan / Omnigent server service
- Dependencies: STORY-001
- Independent test: With STORY-001 applied, run docker compose config, run omnigent-db-init repeatedly against an existing postgres-data volume, start omnigent, and verify curl -f http://localhost:8000/health plus compose topology tests for dependencies, env vars, and volumes.
- Needs clarification: None

Description:
As a local MoonMind operator, I need an Omnigent server service to run beside MoonMind on localhost:8000 while reusing MoonMind's existing Postgres container with a dedicated Omnigent database, role, and persistent data volume.

Acceptance criteria:
- docker-compose.yaml defines omnigent-db-init as a one-shot service that depends on postgres service_healthy and uses restart: "no".
- omnigent-db-init creates the OMNIGENT_POSTGRES_USER role and OMNIGENT_POSTGRES_DB database only when missing and grants privileges without dropping data.
- docker-compose.yaml defines an omnigent service that depends on postgres service_healthy and omnigent-db-init service_completed_successfully.
- omnigent uses env_file: .env and constructs DATABASE_URL from OMNIGENT_POSTGRES_USER, OMNIGENT_POSTGRES_PASSWORD, and OMNIGENT_POSTGRES_DB against postgres:5432.
- No Omnigent-specific postgres service is added; the existing postgres service remains the only Postgres container in the stack.
- omnigent publishes ${OMNIGENT_PORT:-8000}:8000 and mounts omnigent-data at /data for artifacts and admin credentials.
- .env-template includes OMNIGENT_IMAGE, OMNIGENT_IMAGE_TAG, OMNIGENT_PORT, OMNIGENT_POSTGRES_*, built-in accounts variables, optional OIDC variables, OMNIGENT_CONFIG, and Omnigent host image variables.
- deploy/omnigent/server-config.example.yaml exists with non-secret sample settings and future sandbox placeholders, but the server can start without requiring a real server config file.
- Tests parse docker-compose.yaml and .env-template to validate the Omnigent server topology, prefixed database env contract, idempotent init service, and omnigent-data volume.

Requirements:
- Provide Omnigent server on host port 8000 after MoonMind vacates that port.
- Share MoonMind's existing postgres service and local-network while isolating Omnigent into its own database and role.
- Use OMNIGENT_* variables for all Omnigent-specific configuration and avoid generic POSTGRES_* reuse for Omnigent settings.
- Persist Omnigent artifacts and admin credentials in a dedicated Docker volume.
- Expose built-in accounts and OIDC configuration without implementing SSO/OIDC behavior in this issue.

Owned coverage:
- DESIGN-REQ-003: Adds the host-facing Omnigent server on port 8000.
- DESIGN-REQ-004: Defines namespaced .env-template settings and uses env_file: .env.
- DESIGN-REQ-005: Requires Omnigent to depend on and connect to the existing postgres service only.
- DESIGN-REQ-006: Creates a separate Omnigent database and role.
- DESIGN-REQ-007: Implements the repeatable omnigent-db-init service for existing and fresh volumes.
- DESIGN-REQ-008: Builds the server DATABASE_URL from prefixed Omnigent variables.
- DESIGN-REQ-009: Mounts omnigent-data at /data for artifacts and credentials.
- DESIGN-REQ-010: Defaults auth to built-in accounts and only exposes OIDC knobs.
- DESIGN-REQ-011: Adds the optional non-secret server config example.
- DESIGN-REQ-017: Adds tests for compose topology and env-template server settings.
- DESIGN-REQ-018: Preserves existing MoonMind infrastructure while adding Omnigent alongside it.

### STORY-003: Add optional Omnigent host profile service

- Short name: omnigent-host-profile
- Source reference: MM-968: Omnigent Docker Compose; path: null; claim IDs: []
- Sections: Goals, Implementation Plan / Optional Omnigent host service, Risks and Mitigations / Mount conflict
- Dependencies: STORY-002
- Independent test: After STORY-002, run docker compose --profile omnigent-host config and docker compose --profile omnigent-host up -d omnigent-host, then inspect logs for the host process connecting to http://omnigent:8000 and verify tests assert the profile and config mount contract.
- Needs clarification: None

Description:
As a local MoonMind operator, I need an optional Omnigent host service that starts only when the omnigent-host Compose profile is enabled and registers with the Omnigent server through a mounted config file.

Acceptance criteria:
- docker-compose.yaml defines omnigent-host with profiles: ["omnigent-host"].
- omnigent-host depends on omnigent with condition service_started and joins local-network.
- omnigent-host command is ["omnigent", "host"] rather than the image's inert default command.
- deploy/omnigent/host-config.yaml exists and contains server: http://omnigent:8000.
- The host config file is bind-mounted read-only into /root/.omnigent/config.yaml, while omnigent-host-state is mounted at a separate state path that does not hide the config file.
- omnigent-host receives optional OPENAI_API_KEY, ANTHROPIC_API_KEY, GEMINI_API_KEY, and GOOGLE_API_KEY values from .env without making cloud sandbox providers mandatory.
- ./omnigent_workspaces is mounted at /workspaces for host workspaces.
- Tests parse docker-compose.yaml to verify the omnigent-host profile, command, config mount, and state volume.

Requirements:
- Keep Omnigent host disabled by default and enabled only through the omnigent-host Compose profile.
- Configure the host's server URL through Omnigent's config file support rather than hardcoding it in the command.
- Keep config and state storage on separate mount paths to avoid Docker mount ordering issues.
- Allow local workspace and optional model-provider credentials while deferring managed cloud sandbox configuration.

Owned coverage:
- DESIGN-REQ-012: Defines the profile-gated optional host service.
- DESIGN-REQ-013: Overrides the host image default command with the active host process.
- DESIGN-REQ-014: Creates and mounts the host config file with the internal Omnigent URL.
- DESIGN-REQ-015: Separates config and state mounts to prevent the config file from being hidden.
- DESIGN-REQ-016: Passes optional provider keys and mounts local workspaces without configuring cloud sandbox providers.
- DESIGN-REQ-017: Adds tests for the host profile, command, mounts, and volume.

### STORY-004: Document combined stack validation and rollback

- Short name: omnigent-operator-docs
- Source reference: MM-968: Omnigent Docker Compose; path: null; claim IDs: []
- Sections: Implementation Plan / Update docs, Manual Validation Plan, Rollback Plan, Risks and Mitigations, Acceptance Criteria
- Dependencies: STORY-001, STORY-002, STORY-003
- Independent test: Review docs against a fresh .env-template copy and execute documented non-destructive validation commands: docker compose config, health checks for localhost:7000 and localhost:8000, logs commands, omnigent-db-init rerun guidance, and profile-enabled host startup guidance.
- Needs clarification: None

Description:
As a local or self-hosted MoonMind operator, I need documentation and validation guidance for starting, checking, troubleshooting, and rolling back the combined MoonMind plus Omnigent Compose stack without losing existing MoonMind or Omnigent data unexpectedly.

Acceptance criteria:
- Documentation states MoonMind API/dashboard host access at http://localhost:7000 and Omnigent server/UI at http://localhost:8000.
- Documentation includes default startup guidance for the combined stack and the explicit postgres plus omnigent-db-init plus api/omnigent sequence for existing-volume validation.
- Documentation includes docker compose logs commands for omnigent and omnigent-host.
- Documentation includes health checks for http://localhost:7000/healthz and http://localhost:8000/health.
- Documentation explains how to find Omnigent first admin credentials from docker compose logs omnigent and notes they persist under /data/admin-credentials in the omnigent-data volume.
- Documentation includes docker compose --profile omnigent-host up -d omnigent-host and expected host registration behavior.
- Documentation includes rollback steps that restore MoonMind's host port, remove/disable Omnigent services, and make database/volume cleanup explicitly optional and destructive.
- Troubleshooting covers GHCR auth, existing Postgres volumes, port conflicts, host config mount conflicts, and built-in accounts versus OIDC auth confusion.

Requirements:
- Provide operator-facing startup, logs, health-check, credentials, host-profile, manual validation, rollback, and troubleshooting documentation.
- Keep destructive cleanup clearly separate from normal rollback guidance.
- Make built-in accounts mode the documented default while identifying OIDC as future/operator-provided configuration.

Owned coverage:
- DESIGN-REQ-019: Documents ports, startup, logs, health checks, admin credentials, and host profile usage.
- DESIGN-REQ-020: Captures fresh-volume, existing-volume, and optional-host validation flows.
- DESIGN-REQ-021: Documents rollback and data-preserving cleanup boundaries.
- DESIGN-REQ-022: Documents expected troubleshooting cases from the risk list.
- DESIGN-REQ-010: Explains built-in accounts as default and OIDC as exposed configuration only.
- DESIGN-REQ-018: Warns rollback and validation must preserve MoonMind's existing infrastructure unless explicitly removed by the operator.

## Coverage Matrix

- DESIGN-REQ-001: STORY-001
- DESIGN-REQ-002: STORY-001
- DESIGN-REQ-003: STORY-002
- DESIGN-REQ-004: STORY-002
- DESIGN-REQ-005: STORY-002
- DESIGN-REQ-006: STORY-002
- DESIGN-REQ-007: STORY-002
- DESIGN-REQ-008: STORY-002
- DESIGN-REQ-009: STORY-002
- DESIGN-REQ-010: STORY-002, STORY-004
- DESIGN-REQ-011: STORY-002
- DESIGN-REQ-012: STORY-003
- DESIGN-REQ-013: STORY-003
- DESIGN-REQ-014: STORY-003
- DESIGN-REQ-015: STORY-003
- DESIGN-REQ-016: STORY-003
- DESIGN-REQ-017: STORY-002, STORY-003
- DESIGN-REQ-018: STORY-001, STORY-002, STORY-004
- DESIGN-REQ-019: STORY-004
- DESIGN-REQ-020: STORY-004
- DESIGN-REQ-021: STORY-004
- DESIGN-REQ-022: STORY-004

## Dependencies

- STORY-001: None
- STORY-002: STORY-001
- STORY-003: STORY-002
- STORY-004: STORY-001, STORY-002, STORY-003

## Out Of Scope

- Merging MoonMind and Omnigent schemas into one database.
- Removing MoonMind's existing Postgres, Temporal, Keycloak, MinIO, or worker setup.
- Making Omnigent host or managed cloud sandboxes mandatory for all deployments.
- Implementing SSO/OIDC behavior beyond exposing configuration variables.
- Performing destructive database or volume cleanup during normal rollback.

Rationale: these items are explicit non-goals or destructive cleanup paths from the source design and should not be pulled into the implementation stories unless a later issue changes the scope.

## Coverage Gate

PASS - every major design point is owned by at least one story.

## Original Source Text

```text
MM-968: Omnigent Docker Compose

Add Omnigent to MoonMind Docker Compose using shared Postgres and non-conflicting ports

Summary

Add Omnigent to MoonMind's Docker Compose deployment so MoonMind and Omnigent can run side-by-side in the same local/self-hosted stack.

The integration should:

- Move the MoonMind API host port from 8000:8000 to 7000:8000, so MoonMind remains internally unchanged but is reachable from the host at http://localhost:7000.
- Add an Omnigent server service reachable from the host at http://localhost:8000.
- Reuse MoonMind's existing Postgres container rather than starting a second Postgres service for Omnigent.
- Create a separate Omnigent Postgres database and user inside the existing Postgres instance.
- Add an optional omnigent-host service, behind a Docker Compose profile, that can register with the Omnigent server.
- Prefer using MoonMind's existing .env file for both MoonMind and Omnigent configuration, with Omnigent-specific variables prefixed to avoid collisions.
- Add documentation and validation steps for bringing up the combined stack.

Background

MoonMind currently publishes the api service as 8000:8000, while the service itself listens on container port 8000. The internal health check also targets localhost:8000, so only the host-side port mapping needs to change.

MoonMind already uses a single Postgres container for multiple databases and services. The compose file mounts ./init_db_scripts into Postgres' /docker-entrypoint-initdb.d, and the existing init script creates additional Temporal and Keycloak databases/users.

Omnigent's stock Docker deployment normally includes its own Postgres service and an Omnigent server service. The server is configured with DATABASE_URL, ARTIFACT_DIR, HOST=0.0.0.0, and PORT=8000. Omnigent's Docker entrypoint explicitly supports an externally supplied DATABASE_URL, runs migrations, and starts the server, which makes reusing MoonMind's Postgres container feasible.

Omnigent's host image is a prebuilt runtime image, but its default command is inert (sleep infinity), so the compose integration must explicitly run omnigent host or otherwise configure the container to start the host process.

Goals

- MoonMind API is reachable on host port 7000.
- Omnigent server is reachable on host port 8000.
- MoonMind and Omnigent share the existing postgres service.
- Omnigent uses a separate database and role, not MoonMind's primary database.
- Omnigent server persists artifacts and account credentials in a dedicated Docker volume.
- Omnigent host can be optionally enabled with a Compose profile.
- The integration can be configured from the existing MoonMind .env.
- The default stack remains backwards-compatible except for the intentional MoonMind host port change.

Non-goals

- Do not merge MoonMind and Omnigent schemas into the same database.
- Do not remove MoonMind's existing Postgres, Temporal, Keycloak, MinIO, or worker setup.
- Do not make Omnigent mandatory for all MoonMind deployments unless explicitly requested.
- Do not configure Modal/Daytona/E2B/OpenShell managed cloud sandboxes in this issue, beyond leaving room for future Omnigent config.
- Do not implement SSO/OIDC integration in this issue, beyond exposing the relevant env vars.

Implementation Plan

1. Change MoonMind API host port to 7000

Update docker-compose.yaml:

services:
  api:
    ports:
      - "${MOONMIND_API_HOST_PORT:-7000}:8000"

Current state:

ports:
  - "8000:8000"

Do not change the API container's internal port, health check, or internal service URLs. MoonMind workers and services should continue using http://api:8000 internally.

Add to .env-template:

MOONMIND_API_HOST_PORT=7000

Update any local developer docs, onboarding docs, scripts, or README references that say MoonMind is available at http://localhost:8000; they should say http://localhost:7000.

2. Add Omnigent-specific env vars to .env-template

Add a new section to .env-template:

# Omnigent
OMNIGENT_IMAGE="ghcr.io/omnigent-ai/omnigent-server"
OMNIGENT_IMAGE_TAG="latest"
OMNIGENT_PORT=8000

# Reuse MoonMind's existing Postgres container, but isolate Omnigent.
OMNIGENT_POSTGRES_DB="omnigent"
OMNIGENT_POSTGRES_USER="omnigent"
OMNIGENT_POSTGRES_PASSWORD="replace_with_strong_omnigent_db_password"

# Built-in accounts mode is the default local/self-hosted mode.
OMNIGENT_AUTH_ENABLED=1
OMNIGENT_AUTH_PROVIDER=""
OMNIGENT_ACCOUNTS_COOKIE_SECRET="replace_with_64_hex_chars"
OMNIGENT_ACCOUNTS_BASE_URL="http://localhost:8000"
OMNIGENT_ACCOUNTS_INIT_ADMIN_PASSWORD=""
OMNIGENT_ACCOUNTS_AUTO_OPEN=0

# Optional OIDC mode.
OMNIGENT_DOMAIN=""
OMNIGENT_OIDC_ISSUER=""
OMNIGENT_OIDC_CLIENT_ID=""
OMNIGENT_OIDC_CLIENT_SECRET=""
OMNIGENT_OIDC_COOKIE_SECRET=""
OMNIGENT_OIDC_SCOPES=""
OMNIGENT_OIDC_ALLOWED_DOMAINS=""
OMNIGENT_OIDC_SESSION_TTL_HOURS=8

# Optional Omnigent server config file path inside the container.
OMNIGENT_CONFIG="/data/config.yaml"

# Optional Omnigent host image.
OMNIGENT_HOST_IMAGE="ghcr.io/omnigent-ai/omnigent-host"
OMNIGENT_HOST_IMAGE_TAG="latest"

Important: do not reuse generic POSTGRES_USER, POSTGRES_DB, or POSTGRES_PASSWORD for Omnigent. MoonMind already uses those names for the shared Postgres container, while Omnigent's stock compose also uses generic Postgres variable names. Prefixing with OMNIGENT_ avoids accidental reconfiguration of MoonMind's database.

Generate OMNIGENT_ACCOUNTS_COOKIE_SECRET with:

openssl rand -hex 32

Generate OMNIGENT_POSTGRES_PASSWORD with the project's preferred secret-generation process.

3. Add an idempotent Omnigent database initialization service

Add a one-shot Compose service that creates the Omnigent database and role in MoonMind's existing Postgres container.

Do this instead of relying only on /docker-entrypoint-initdb.d, because Postgres init scripts only run when the data directory is first initialized. Existing developers and deployments will already have a postgres-data volume.

The service should use postgres:${POSTGRES_VERSION:-17}, depend on postgres with condition service_healthy, load .env, connect to the existing postgres service as the MoonMind postgres admin user, create OMNIGENT_POSTGRES_USER if missing, create OMNIGENT_POSTGRES_DB if missing, grant privileges, attach to local-network, and use restart: "no".

Notes:

- This service is intentionally idempotent.
- It can be run repeatedly without dropping Omnigent data.
- It should depend on postgres being healthy.
- The Omnigent server should depend on this service completing successfully.

4. Add the Omnigent server service

Add an omnigent service using image ${OMNIGENT_IMAGE:-ghcr.io/omnigent-ai/omnigent-server}:${OMNIGENT_IMAGE_TAG:-latest}. It should restart unless stopped, depend on postgres service_healthy and omnigent-db-init service_completed_successfully, use env_file: .env, build DATABASE_URL from OMNIGENT_POSTGRES_USER, OMNIGENT_POSTGRES_PASSWORD, postgres:5432, and OMNIGENT_POSTGRES_DB, set ARTIFACT_DIR=/data/artifacts, HOST=0.0.0.0, PORT=8000, OMNIGENT_ADMIN_CREDENTIALS_PATH=/data/admin-credentials, expose account/OIDC env vars, mount omnigent-data:/data, publish ${OMNIGENT_PORT:-8000}:8000, and join local-network.

Add volume:

volumes:
  omnigent-data:

The Omnigent server should use the same local-network as MoonMind's Postgres service. The internal database hostname should be postgres.

5. Add optional Omnigent host service behind a Compose profile

Add a config-driven omnigent-host service. The preferred pattern is to avoid hardcoding the server URL in the command and instead use Omnigent's config file support.

Create a new tracked file:

deploy/omnigent/host-config.yaml

with:

server: http://omnigent:8000

Add an omnigent-host service using image ${OMNIGENT_HOST_IMAGE:-ghcr.io/omnigent-ai/omnigent-host}:${OMNIGENT_HOST_IMAGE_TAG:-latest}, profiles: ["omnigent-host"], restart: unless-stopped, depends_on omnigent service_started, env_file: .env, OMNIGENT_CONFIG_HOME=/root/.omnigent, OMNIGENT_DATA_DIR=/root/.omnigent-state, optional provider API key environment variables, volumes for ./deploy/omnigent/host-config.yaml:/root/.omnigent/config.yaml:ro, omnigent-host-state:/root/.omnigent-state, and ./omnigent_workspaces:/workspaces, command ["omnigent", "host"], and local-network.

Add volume:

volumes:
  omnigent-host-state:

Implementation note: do not mount a named volume over /root/.omnigent if also bind-mounting /root/.omnigent/config.yaml, because the named volume can hide the bind-mounted config path depending on mount ordering and Docker behavior. Use separate config and state paths.

6. Add optional Omnigent server config file support

Create directory deploy/omnigent/ and add deploy/omnigent/server-config.example.yaml with non-secret Omnigent server settings, empty admins and allowed_domains examples, and future managed sandbox support comments. Secrets stay in .env.

Consider bind-mounting a real config file later, but do not require this for the first pass. Omnigent can run without a server config file when basic accounts mode is used.

7. Update docs

Add or update documentation covering:

- New ports: MoonMind API http://localhost:7000 and Omnigent server/UI http://localhost:8000.
- Default startup using either docker compose up -d postgres, docker compose run --rm omnigent-db-init, docker compose up -d api omnigent, or simply docker compose up -d if the full stack includes Omnigent by default.
- Optional host startup: docker compose --profile omnigent-host up -d omnigent-host.
- Logs: docker compose logs -f omnigent and docker compose logs -f omnigent-host.
- Health checks: curl -f http://localhost:7000/healthz and curl -f http://localhost:8000/health.
- First admin password discovery through docker compose logs omnigent, noting that Omnigent stores admin credentials under /data/admin-credentials in the omnigent-data volume.

8. Add tests

Add or update tests that parse docker-compose.yaml and .env-template.

Recommended test coverage:

- MoonMind API host port defaults to 7000.
- Omnigent service exists.
- Omnigent does not define its own postgres service and depends on the existing postgres service being healthy.
- Omnigent uses a prefixed DB env contract through DATABASE_URL containing OMNIGENT_POSTGRES_USER and OMNIGENT_POSTGRES_DB.
- omnigent-db-init exists and depends on postgres, with restart: "no".
- omnigent-host is profile-gated.
- .env-template contains MOONMIND_API_HOST_PORT=7000, OMNIGENT_POSTGRES_DB, and OMNIGENT_ACCOUNTS_COOKIE_SECRET.
- Existing Temporal/Postgres topology tests continue to pass.

Manual Validation Plan

Fresh checkout / fresh volumes:

cp .env-template .env
# Fill POSTGRES_PASSWORD, OMNIGENT_POSTGRES_PASSWORD, OMNIGENT_ACCOUNTS_COOKIE_SECRET

docker compose config
docker compose up -d
curl -f http://localhost:7000/healthz
curl -f http://localhost:8000/health
docker compose logs omnigent

Expected:

- MoonMind API responds on localhost:7000.
- Nothing is listening for MoonMind on localhost:8000.
- Omnigent responds on localhost:8000.
- Omnigent migrations complete successfully.
- Omnigent admin bootstrap succeeds or prompts/logs credentials.

Existing MoonMind volume:

docker compose up -d postgres
docker compose run --rm omnigent-db-init
docker compose up -d omnigent
curl -f http://localhost:8000/health

Expected:

- omnigent-db-init completes successfully.
- Omnigent database and user are created without dropping existing data.
- Existing MoonMind, Temporal, and Keycloak databases remain intact.

Optional host profile:

docker compose --profile omnigent-host up -d omnigent-host
docker compose logs -f omnigent-host

Expected:

- omnigent-host starts omnigent host.
- The host connects to http://omnigent:8000 using deploy/omnigent/host-config.yaml.
- The Omnigent UI can see the host as available.

Rollback Plan

Revert the MoonMind API port mapping to:

ports:
  - "8000:8000"

Remove or disable these services:

- omnigent
- omnigent-db-init
- omnigent-host

Leave the Omnigent database in Postgres unless a destructive cleanup is explicitly requested.

Optional cleanup:

DROP DATABASE omnigent;
DROP ROLE omnigent;

Remove unused volumes only if data loss is acceptable:

docker volume rm <project>_omnigent-data
docker volume rm <project>_omnigent-host-state

Risks and Mitigations

- Risk: Postgres env var collision. Use OMNIGENT_POSTGRES_* for all Omnigent-specific DB values and construct DATABASE_URL explicitly.
- Risk: Existing Postgres volumes do not run new init scripts. Use the idempotent omnigent-db-init service rather than relying solely on init_db_scripts.
- Risk: Omnigent host image does not auto-register. Override the command to ["omnigent", "host"] and provide the server URL through /root/.omnigent/config.yaml.
- Risk: Omnigent server image pull may require GHCR auth. Document docker login ghcr.io guidance depending on package visibility and permissions.
- Risk: Mount conflict for host config. Do not mount a named volume over the same directory that contains the bind-mounted host config file. Use separate config and state paths.
- Risk: Auth confusion. Default to Omnigent built-in accounts mode. OIDC/header auth can be configured later through the exposed env vars.

Acceptance Criteria

- docker compose config succeeds.
- MoonMind API is reachable at http://localhost:7000/healthz.
- Omnigent server is reachable at http://localhost:8000/health.
- Omnigent uses MoonMind's existing postgres service.
- No second Postgres service is added for Omnigent.
- Omnigent has its own Postgres database and user.
- omnigent-db-init is idempotent and works with existing postgres-data volumes.
- .env-template includes MoonMind API port config and Omnigent config.
- omnigent service uses env_file: .env.
- omnigent-data volume persists Omnigent artifacts and admin credentials.
- Optional omnigent-host service is behind the omnigent-host profile.
- omnigent-host can be configured via deploy/omnigent/host-config.yaml.
- Tests validate the compose topology and env-template additions.
- Docs explain startup, ports, logs, health checks, and host profile usage.

Suggested PR Breakdown

PR 1: Port and env scaffolding

- Change MoonMind API host port to ${MOONMIND_API_HOST_PORT:-7000}:8000.
- Add .env-template values.
- Update tests for port change.
- Update docs for new MoonMind API URL.

PR 2: Omnigent server with shared Postgres

- Add omnigent-db-init.
- Add omnigent service.
- Add omnigent-data volume.
- Add compose topology tests.
- Add docs for Omnigent server startup.

PR 3: Optional Omnigent host

- Add deploy/omnigent/host-config.yaml.
- Add omnigent-host profile service.
- Add omnigent-host-state volume.
- Add tests for profile-gated host service.
- Add docs for host startup and validation.

PR 4: Polish and troubleshooting

- Add troubleshooting for GHCR auth.
- Add troubleshooting for existing volumes.
- Add troubleshooting for port conflicts.
- Add rollback notes.
```
