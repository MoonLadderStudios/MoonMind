# Managed Agent Docker Sidecar Runtime — Superseded

- **Status:** Superseded
- **Superseded by:** [`DockerBackendService.md`](./DockerBackendService.md)
- **Last updated:** 2026-07-13

The per-session Docker-in-Docker sidecar design is no longer the MoonMind desired
state. Its private session graph prevented arbitrary large images from being
reused across workflows.

The canonical design is now the API-owned
[`Docker Backend Service`](./DockerBackendService.md): Omnigent and managed-agent
sessions submit governed asynchronous container jobs through MoonMind MCP or
HTTP tools; one deployment-selected Docker daemon executes those jobs and keeps
its image cache across workflow and session boundaries.

Existing sidecar implementation paths may remain temporarily during migration,
but new architecture and product work should target `DockerBackendService`.
