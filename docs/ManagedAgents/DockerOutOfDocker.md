# DockerOutOfDocker — Consolidated

- **Status:** Consolidated into [`DockerBackendService.md`](./DockerBackendService.md)
- **Last updated:** 2026-07-13

The former Docker-out-of-Docker workload design is now part of the canonical
[`Docker Backend Service`](./DockerBackendService.md) contract.

MoonMind exposes governed asynchronous container jobs through its API and MCP
surfaces. A trusted MoonMind worker launches validated workloads through the
configured system Docker backend; requesting agents never receive the Docker
socket or raw daemon authority. Images are acquired on demand and retained at
daemon scope for reuse across workflows.

This file is only a tombstone for old links. It does not define a parallel
workload architecture, sidecar path, migration lane, or compatibility mode.
