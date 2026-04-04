# Task Steps System

Status: Active  
Owners: MoonMind Engineering  
Last Updated: 2026-04-04

## 1. Purpose

This document defines the current MoonMind step-execution model at the task level.

It is intentionally task-oriented and high level. The detailed owned contracts live elsewhere:

- `docs/Tasks/SkillAndPlanContracts.md` owns executable plan structure
- `docs/Temporal/StepLedgerAndProgressModel.md` owns the operator-facing step ledger, status vocabulary, attempts, checks, and refs
- `docs/Temporal/ManagedAndExternalAgentExecutionModel.md` owns `MoonMind.AgentRun` lifecycle and observability boundaries

## 2. Canonical model

MoonMind no longer models steps as a raw `task.steps` loop inside an `AgentTaskWorkflow`.

The current architecture is:

- `MoonMind.Run` is the root task workflow
- the canonical planned step list comes from the resolved **plan artifact**
- direct executable steps run as activities
- true agent-runtime steps run as child `MoonMind.AgentRun` workflows

This preserves one task-oriented operator surface while giving agent steps their own durable lifecycle boundary.

## 3. Step structure and execution

### 3.1 Planned steps

Planned steps are plan nodes with:

- stable node ID
- display-safe title
- tool metadata
- dependency structure

The plan artifact is the canonical source once planning completes.

### 3.2 Live step state

`MoonMind.Run` maintains a compact live step ledger for the current/latest run.

That ledger tracks:

- exact step status
- attempt count
- waiting/attention state
- structured checks/review results
- bounded summaries
- refs to child workflows, managed runs, and artifacts

MoonMind exposes that state through a dedicated step-ledger query/API surface rather than by asking the UI to infer step state from logs, heartbeats, or timeline text.

### 3.3 Rich evidence

Large per-step outputs stay outside workflow state:

- stdout/stderr
- merged logs
- diagnostics
- provider snapshots
- large result bodies
- detailed review payloads

Those belong in artifacts and `/api/task-runs/*`.

## 4. Retry and resilience

Because steps are distinct activity or child-workflow executions:

- step progress is durable across worker restarts
- failed steps can be retried without rerunning earlier successful expensive steps
- retry history is represented as step attempts scoped to `(workflowId, runId, logicalStepId, attempt)`
- current task detail shows the latest/current run's steps only by default

## 5. Observability posture

MoonMind does **not** use terminal-widget output blocks as the canonical step UX.

Instead:

- task detail shows a first-class **Steps** section
- each step row shows exact status, summary, attempt count, blockers, and evidence links
- expanded rows group **Summary**, **Checks**, **Logs & Diagnostics**, **Artifacts**, and **Metadata**
- agent-runtime step rows deep-link or embed `/api/task-runs/*` when `taskRunId` is present

## 6. What this document supersedes

The following older ideas are no longer canonical:

- `AgentTaskWorkflow` as the root step workflow name
- step progress derived primarily from Search Attributes or heartbeats
- terminal-widget output blocks as the main step observability surface

The authoritative replacement is:

- `MoonMind.Run` + `MoonMind.AgentRun`
- plan artifact for planned structure
- workflow-owned step ledger for live state
- artifact-first and managed-run observability for durable evidence
