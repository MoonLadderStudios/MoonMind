# Temporal Production Routing Policy

**Implementation tracking:** [`docs/tmp/remaining-work/Temporal-RoutingPolicy.md`](../tmp/remaining-work/Temporal-RoutingPolicy.md)

**Status:** Active  
**Owner:** MoonMind Platform  
**Last updated:** 2026-03-27

## Purpose

This document defines how MoonMind routes task-shaped product actions onto the
single live execution substrate: **Temporal**.

## 1. Core rule

MoonMind no longer routes execution through parallel `queue` or `system`
backends. New execution starts, detail reads, updates, signals, cancellations,
and reruns all resolve to the Temporal execution layer.

The primary backend surface is:

- `POST /api/executions`
- `GET /api/executions`
- `GET /api/executions/{workflowId}`
- `POST /api/executions/{workflowId}/update`
- `POST /api/executions/{workflowId}/signal`
- `POST /api/executions/{workflowId}/cancel`
- `POST /api/executions/{workflowId}/reschedule`

## 2. Runtime picker exclusion

Consistent with `docs/UI/MissionControlArchitecture.md`, `temporal` is **not**
a runtime choice in the submit form. It is the execution substrate behind the
product surface, while runtimes remain choices like `codex`, `gemini_cli`,
`claude_code`, or external integrations.

## 3. Route resolution

Task-oriented routes such as `/tasks/list` and `/tasks/{taskId}` remain valid
product surfaces, but they resolve onto Temporal-backed execution data.

Rules:

- `taskId == workflowId` for Temporal-backed task views.
- `source=temporal` may be used as an explicit routing hint for debugging or
  compatibility, but it does not select between multiple live execution
  substrates.
- Server-side route resolution should prefer canonical execution metadata and
  persisted task mappings over ID-shape guessing.

## 4. Adjacent non-execution modules

The dashboard runtime config may still expose adjacent modules such as:

- `proposals`
- `schedules`
- `manifests`

These are **not** alternate execution substrates. They are product modules that
either create new Temporal executions or inspect related control-plane data.

## 5. Failure posture

If Temporal is unavailable:

- MoonMind should fail mutating execution requests explicitly.
- MoonMind should not fall back to a removed queue or system execution path.
- Read routes may use documented projection fallback behavior only where the
  route contract explicitly allows stale reads.

## 6. Debugging

Useful operator/debugging aids include:

- `?source=temporal` for explicit execution-route selection
- `workflowId` as the canonical durable execution identifier
- `temporalRunId` for latest-run detail/debug views

No documented route should imply that `queue` or `system` remain valid execution
targets.
