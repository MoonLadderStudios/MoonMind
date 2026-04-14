# Codex Managed Session Plane

Status: Desired state
Owners: MoonMind Platform
Last updated: 2026-04-14
Related:
- [`docs/ManagedAgents/CodexCliManagedSessions.md`](./CodexCliManagedSessions.md)
- [`docs/ManagedAgents/DockerOutOfDocker.md`](./DockerOutOfDocker.md)
- [`docs/Temporal/ManagedAndExternalAgentExecutionModel.md`](../Temporal/ManagedAndExternalAgentExecutionModel.md)

## Purpose

This document preserves the canonical Codex managed session plane entrypoint used
by managed-agent architecture references. The detailed Codex CLI session
contract lives in [`CodexCliManagedSessions.md`](./CodexCliManagedSessions.md).

The Codex managed session plane is the task-scoped managed runtime environment
for Codex continuity. It owns the session container, thread and turn lifecycle,
session reset boundaries, and continuity artifacts for one MoonMind task.

Managed-session steps may invoke **control-plane tools** that launch separate
workload containers as described in
[`DockerOutOfDocker.md`](./DockerOutOfDocker.md). Those workload containers remain outside session identity: they do not become `session_id`, `session_epoch`, `container_id`, `thread_id`, or `active_turn_id`, and they do not replace the task-scoped session container.

## Contract

The bounded session identity remains:

- `session_id`
- `session_epoch`
- `container_id`
- `thread_id`
- `active_turn_id`

Docker-backed workload containers are sibling workload executions launched
through MoonMind's control plane. They are not hidden Codex session children, and
they are not `MoonMind.AgentRun` executions unless the launched runtime is itself
an agent runtime.
