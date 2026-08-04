# Codex Managed Session Plane

Status: Desired state
Owners: MoonMind Platform
Last updated: 2026-08-04
Related:
- [`docs/ManagedAgents/CodexCliManagedSessions.md`](./CodexCliManagedSessions.md)
- [`docs/ManagedAgents/DockerSidecarRuntime.md`](./DockerSidecarRuntime.md)
- [`docs/ManagedAgents/DockerOutOfDocker.md`](./DockerOutOfDocker.md)
- [`docs/Temporal/ManagedAndExternalAgentExecutionModel.md`](../Temporal/ManagedAndExternalAgentExecutionModel.md)

## Purpose

This document preserves the canonical Codex managed session plane entrypoint used
by managed-agent architecture references for the Codex binding. The shared
managed session plane also has a Claude Code binding described in
[`ClaudeCodeManagedSessions.md`](./ClaudeCodeManagedSessions.md). The detailed Codex CLI session
contract lives in [`CodexCliManagedSessions.md`](./CodexCliManagedSessions.md).

The Codex managed session plane is the workflow-scoped managed runtime environment
for Codex continuity. It owns the session container, thread and turn lifecycle,
session reset boundaries, and continuity artifacts for one MoonMind workflow execution.

Ordinary repository container work that originates from the Codex session uses
the API-owned [`Docker Backend Service`](./DockerBackendService.md). The session
submits typed jobs through MoonMind and receives neither a Docker endpoint nor
daemon credentials. The deployment-selected daemon retains one image cache for
reuse across workflows.

## Contract

The bounded session identity remains:

- `session_id`
- `session_epoch`
- `container_id`
- `thread_id`
- `active_turn_id`

Container-job workload containers remain outside session identity: they do not become `session_id`,
`session_epoch`, `container_id`, `thread_id`, or `active_turn_id`, and they are
not `MoonMind.AgentRun` executions unless the launched runtime is itself an
agent runtime.
