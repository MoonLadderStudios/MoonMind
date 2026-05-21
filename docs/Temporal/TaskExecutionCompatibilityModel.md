# Workflow Execution Product Model Bridge

Status: **Superseded by Workflow Execution product model**
Owner: MoonMind Platform
Last updated: 2026-05-21
Audience: backend, dashboard, API, workflow authors

This document is retained only as a pointer for readers who arrive through older links. The normative product model is now [`WorkflowExecutionProductModel.md`](./WorkflowExecutionProductModel.md).

MoonMind does not define a separate product entity named Task.

A user-submitted unit of work is a Workflow Execution. In informal UI copy, it may be called a Workflow. The exact API and documentation term is Workflow Execution.

The term Task is reserved for Temporal Tasks, Temporal Workflow Tasks, Temporal Activity Tasks, Temporal Task Queues, and explicitly qualified external systems such as Jira tasks or Codex provider tasks.

Use the product model document for:

- Workflow Execution identity and routing semantics
- `workflowId` as the stable product identity and route key
- `runId` as the current/latest Temporal run instance
- `workflowType` and `entry` semantics
- Step and Step Execution semantics
- artifacts and `externalRefs`

Implementation rollout notes and cleanup sequencing belong in MoonSpec artifacts under `specs/<feature>/`, not in canonical documentation.
