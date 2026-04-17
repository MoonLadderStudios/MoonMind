# Contract: Jules Bundle Runtime

## Purpose

Define the orchestration-owned contract for turning multiple logical Jules-targeted plan nodes into one bundled Jules execution.

## 1. Bundle Eligibility

MoonMind may bundle consecutive logical nodes only when all of the following are true:

- each node targets Jules as the effective runtime,
- nodes are consecutive in execution order,
- nodes share the same repository/workspace context,
- nodes share compatible publish semantics,
- no node requires a human approval boundary before the next node,
- later instructions do not depend on artifacts that must first be created by an earlier separate runtime boundary.

If any eligibility rule fails, MoonMind must create a separate Jules bundle node instead of falling back to standard multi-step `sendMessage` progression.

## 2. Compiled Brief Shape

Each bundled Jules execution brief must contain these sections in order:

1. Mission
2. Repository and Workspace Context
3. Execution Rules
4. Ordered Work Checklist
5. Validation Checklist
6. Deliverable Requirements

The brief must be deterministic, checklist-shaped, and suitable for one-shot provider execution.

## 3. Bundle Manifest Metadata

MoonMind must retain compact metadata for each bundle:

| Key | Meaning |
|---|---|
| `bundleId` | Stable synthetic execution identifier |
| `bundledNodeIds` | Ordered logical nodes represented by the bundle |
| `bundleManifestRef` | Artifact ref for the full manifest |
| `bundleStrategy` | Expected value `one_shot_jules` |
| `correlationId` | MoonMind correlation ID |
| `idempotencyKey` | MoonMind idempotency key |

## 4. Follow-Up Message Policy

`sendMessage` remains allowed only for:

- clarification responses,
- operator intervention,
- explicit resume flows.

`sendMessage` must not be used as the standard progression path between logical implementation steps inside a bundled Jules run.

## 5. Truthful Branch Publication

When `publishMode == "branch"`:

- MoonMind must request Jules PR automation at start.
- MoonMind must extract the resulting PR URL after provider completion.
- Jules receives a single authored `branch` reference. MoonMind must treat that branch as the intended branch-publication destination.
- MoonMind must merge the PR successfully before reporting branch publication success.
- If any of these steps fail, MoonMind must surface a non-success outcome.

For `publishMode == "pr"`, the authored `branch` is the PR base. Jules/provider automation manages the work/head branch; MoonMind must not require authored inputs to provide both base and target branches.

## 6. Truthful Bundle Result Semantics

Provider completion does not by itself guarantee MoonMind success.

MoonMind must treat a bundled run as incomplete or failed when:

- required checklist outcomes were not actually completed,
- required verification fails,
- requested branch publication does not land on the intended authored branch.
