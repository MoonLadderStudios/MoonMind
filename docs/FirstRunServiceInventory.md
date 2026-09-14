# First-Run Service Inventory (MoonMind#3940)

This is the operational counterpart of the conceptual [architecture table in the
README](../README.md#architecture). The README table maps conceptual
components to roles; this document lists every service declared in the
canonical `docker-compose.yaml`, its published ports and Compose profiles, and
whether it is steady-state, init/one-shot, optional, or worker-created
on-demand. Optional, init, one-shot, and on-demand containers are not part of
the default steady-state stack.

**Document Class:** Canonical declarative
**Status:** Current
**Audience:** First-run operators validating what `docker compose up -d` starts
**Authority:** Derived from the canonical `docker-compose.yaml` with
non-sensitive default configuration. On any conflict, the Compose file wins.
**Related Docs:** [README Quick Start](../README.md#quick-start),
[Combined Stack Validation and Rollback](Omnigent/CombinedStackValidationAndRollback.md),
[Repository Access and Workspace Design](RepositoryAccessAndWorkspaceDesign.md) (Status: Proposed)

## How to re-derive this inventory

Render the Compose file with non-sensitive test configuration and compare the
service names, ports, and profiles against the tables below:

```bash
docker compose config --quiet
docker compose ps
```

Images are pulled from their configured registries (GHCR by default); the
default path builds nothing locally. Production and credentialed conformance
may pin complete immutable `*_IMAGE_REF` digest references instead of mutable
tags (see [Combined Stack Validation and Rollback](Omnigent/CombinedStackValidationAndRollback.md)).

## Steady-state defaults

These services start with a plain `docker compose up -d` and restart with the
stack (`restart: unless-stopped`). This is the default always-on footprint.

| Service | Published ports (host:container) | Role |
| --- | --- | --- |
| `api` | `127.0.0.1:7000:8000` (via `MOONMIND_API_PUBLISH_HOST` / `MOONMIND_API_HOST_PORT`) | FastAPI control plane and dashboard backend |
| `postgres` | none | Relational persistence |
| `temporal` | `7233:7233` | Durable execution engine |
| `minio` | `9000:9000`, `9001:9001` (via `MINIO_API_PORT` / `MINIO_CONSOLE_PORT`) | S3-compatible artifact storage and console |
| `temporal-worker-workflow` | none | Orchestration worker |
| `temporal-worker-artifacts` | none | Artifact worker |
| `temporal-worker-llm` | none | LLM-call worker |
| `temporal-worker-sandbox` | none | Sandbox-execution worker |
| `temporal-worker-agent-runtime` | none | Agent-runtime supervision worker |
| `temporal-worker-deployment-control` | none | Deployment-control worker |
| `temporal-worker-integrations` | none | External-integrations worker |
| `omnigent` | `8000:8000` (via `OMNIGENT_PORT`) | Omnigent server and UI |
| `docker-proxy` | none | Restricted system-Docker access for trusted MoonMind backend execution; never exposed to managed sessions or Omnigent runners |
| `sandbox-egress-proxy` | none | Sandbox egress proxy boundary |

## Init and one-shot dependencies

These services run to completion during startup or initialization
(`restart: "no"`) and are not part of the steady-state stack. Do not count
them as always-on containers.

| Service | Role |
| --- | --- |
| `temporal-namespace-init` | Temporal namespace setup |
| `omnigent-runtime-bootstrap` | Omnigent runtime bootstrap |
| `omnigent-tools-init` | Omnigent tooling setup |
| `init-db` | MoonMind database initialization |
| `omnigent-db-init` | Omnigent database initialization |
| `omnigent-agent-init` | Omnigent agent registration |
| `agent-workspaces-init` | Agent workspace directory setup |
| `codex-auth-init` | Codex credential-volume setup |
| `claude-auth-init` | Claude credential-volume setup |

## Optional profile-gated services

These services start only when their Compose profile is selected (via
`COMPOSE_PROFILES` or `--profile`). They are not defaults.

| Service | Profile | Published ports | Role |
| --- | --- | --- | --- |
| `temporal-ui` | `temporal-ui` | `8088:8080` (via `TEMPORAL_UI_HOST_PORT`) | Temporal web UI for operator inspection |
| `temporal-admin-tools` | `temporal-tools` | none | Long-running admin-tools shell for operator troubleshooting |
| `temporal-visibility-rehearsal` | `temporal-tools` | none | One-shot visibility-schema rehearsal |
| `omnigent-host` | `omnigent-host` | none | Optional generic Omnigent host (never receives MoonMind OAuth homes; never a fallback for a failed profile-bound host) |
| `omnigent-host-claude` | `omnigent-host-claude` | none | Dedicated static Claude OAuth host |
| `omnigent-host-claude-init` | `omnigent-host-claude` | none | One-shot setup for the dedicated Claude host |
| `omnigent-host-codex` | `omnigent-host-codex` | none | Dedicated static Codex OAuth host |
| `omnigent-host-codex-init` | `omnigent-host-codex` | none | One-shot setup for the dedicated Codex host |

## Worker-created on-demand containers

Workflow-requested on-demand hosts (deterministic `mm-omnigent-host-*`
container names) are created by the MoonMind worker after durable lease
acquisition. They are not Compose services, must not be pre-created by an
operator, and are never evidence that the Compose stack is ready. Their
lifecycle and cleanup evidence belong in Workflow Detail and bridge
diagnostics (see [Combined Stack Validation and Rollback](Omnigent/CombinedStackValidationAndRollback.md)).

## Conceptual components versus operational services

The README architecture table stays a short conceptual map (API Service,
Temporal Server, Worker Fleet, Omnigent Runtime Plane, Managed Compatibility
Plane, Docker Backend Service, Dashboard, MinIO, Docker Proxy). Several rows
in the tables above implement one conceptual component — for example, seven
`temporal-worker-*` services implement the Worker Fleet — and init, optional,
and on-demand containers are accounted separately, never merged into the
defaults.
