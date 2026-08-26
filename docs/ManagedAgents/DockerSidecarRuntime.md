# Managed Agent Docker Sidecar Runtime — Removed

- **Status:** Removed from desired state
- **Canonical replacement:** [`DockerBackendService.md`](./DockerBackendService.md)
- **Last updated:** 2026-07-13

The per-session Docker-in-Docker design is not a supported MoonMind desired state
or compatibility path. Its session-owned graph prevented arbitrary large images
from being reused across workflows.

Agent-originated container work uses the API-owned
[`Docker Backend Service`](./DockerBackendService.md). Omnigent and managed-agent
sessions submit governed asynchronous jobs through MoonMind MCP or HTTP; the
configured deployment Docker daemon executes those jobs and retains its image
cache across workflow and session boundaries.

This file is only a tombstone for old links. It does not define a runtime mode,
rollout lane, or supported implementation alternative.
