# Data Model: Jules Provider Adapter Runtime Alignment

## Overview

This feature does not introduce a new database schema. It introduces three execution-side data structures that must stay compact, deterministic, and artifact-friendly.

## 1. Jules Bundle Node

Represents one synthetic execution unit created from one or more consecutive Jules-targeted logical plan nodes.

### Fields

| Field | Type | Description |
|---|---|---|
| `bundleId` | string | Stable identifier for the synthetic Jules bundle |
| `bundledNodeIds` | list[string] | Ordered logical node IDs represented by the bundle |
| `repository` | string | Repository context shared by the bundled work |
| `startingBranch` | string | Starting branch for the Jules run |
| `targetBranch` | string \| null | Optional branch publication target |
| `publishMode` | string | Requested publish behavior (`none`, `pr`, `branch`) |
| `compiledBrief` | string | Consolidated one-shot brief sent to Jules |
| `bundleManifestRef` | string \| null | Artifact reference for the full bundle manifest |

### Validation Rules

- `bundledNodeIds` must be non-empty and preserve original execution order.
- All bundled nodes must share compatible repo/workspace/publish context.
- `compiledBrief` must be produced deterministically from the same ordered inputs.

## 2. Jules Bundle Manifest

Artifact-backed traceability record that explains how MoonMind transformed logical nodes into one Jules provider run.

### Fields

| Field | Type | Description |
|---|---|---|
| `bundleId` | string | Primary manifest key |
| `bundleStrategy` | string | Expected value: `one_shot_jules` |
| `bundledNodeIds` | list[string] | Ordered original node IDs |
| `mission` | string | High-level objective |
| `workspaceContext` | object | Repo/branch/publish metadata used for compilation |
| `executionRules` | list[string] | Ordered execution constraints given to Jules |
| `orderedChecklist` | list[object] | Human/audit-readable work checklist entries |
| `validationChecklist` | list[string] | Expected validation steps |
| `deliverableRequirements` | list[string] | Required final-summary disclosures |
| `correlationId` | string | MoonMind correlation identifier |
| `idempotencyKey` | string | MoonMind idempotency key used for the bundled run |

### Validation Rules

- `bundleStrategy` must be explicit and stable for replay/auditability.
- Every bundled logical node must appear in `bundledNodeIds`.
- Checklist ordering must match the execution order represented by the bundle.

## 3. Bundle Result Summary

MoonMind-owned completion summary for one bundled Jules execution.

### Fields

| Field | Type | Description |
|---|---|---|
| `bundleId` | string | Bundle being summarized |
| `providerStatus` | string | Raw or normalized provider completion status |
| `publishOutcome` | string | `not_requested`, `pr_created`, `branch_merged`, `publish_failed`, or similar |
| `verificationOutcome` | string | `passed`, `incomplete`, or `failed` |
| `incompleteChecklistItems` | list[string] | Checklist items Jules did not clearly complete |
| `pullRequestUrl` | string \| null | PR URL if one exists |
| `mergeSha` | string \| null | Merge SHA for successful branch publication |
| `summary` | string | Compact operator-facing summary |

### State Transitions

| State | Meaning |
|---|---|
| `provider_completed` | Jules reached a terminal provider-success state |
| `publish_verified` | Required PR/merge outcome completed |
| `verification_failed` | MoonMind verification disproved requested outcome |
| `incomplete` | Bundle ended without satisfying all required checklist outcomes |

### Validation Rules

- `branch_merged` is valid only when a merge SHA or equivalent verified merge outcome exists.
- `verificationOutcome = passed` is invalid when `incompleteChecklistItems` is non-empty.
- Provider-reported success alone is insufficient to mark bundle success when publish or verification expectations remain unmet.
