# Research: codex-managed-session-plane-phase4

## Decision 1: Use `codex app-server` stdio transport inside the session container

- **Decision**: The container-side bridge will drive Codex through `codex app-server` over stdio, not `codex exec`.
- **Rationale**: The canonical managed-session doc freezes Codex App Server as the session protocol. The current CLI already exposes line-delimited JSON-RPC on stdio, which allows a simple bridge without adding websocket dependencies.
- **Alternatives considered**:
  - `codex exec --json`: rejected because the canonical desired-state doc explicitly forbids it as the primary session protocol.
  - direct websocket client from the worker: rejected for Phase 4 because the worker environment does not currently ship websocket client dependencies, and the transport belongs behind the container-side boundary anyway.

## Decision 2: Use a Docker-backed controller that executes control commands in the launched container

- **Decision**: The worker-side controller will launch and inspect the session container with Docker CLI and run control actions inside the container boundary.
- **Rationale**: This keeps the new path container-first and independent from the worker-local managed-runtime launcher.
- **Alternatives considered**:
  - reuse `ManagedRuntimeLauncher`: rejected because that would collapse the new path back into a worker-local subprocess loop.
  - defer worker wiring until a later phase: rejected because the Phase 4 deliverable is the first real managed-session launcher, not just a standalone helper.

## Decision 3: Keep MoonMind logical thread ids separate from vendor-native Codex thread ids

- **Decision**: Persist a logical-to-vendor thread mapping in the mounted session workspace.
- **Rationale**: Phase 3 contracts already expose MoonMind-owned `threadId` fields, while Codex app-server generates its own thread ids. A mapping lets MoonMind keep a stable orchestration-facing identity without pretending it controls Codex's native ids.
- **Alternatives considered**:
  - expose vendor thread ids directly as MoonMind thread ids: rejected because it leaks runtime-native identity details into MoonMind contracts and makes clear/reset flows harder to reason about.

## Decision 4: Treat the current MoonMind image as transitional-only input

- **Decision**: The launcher will accept the image ref from the typed request and avoid hardcoding `ghcr.io/moonladderstudios/moonmind:latest`.
- **Rationale**: Phase 4 must prove the session-launch model while leaving the Stage A to Stage B image swap as a packaging change.
