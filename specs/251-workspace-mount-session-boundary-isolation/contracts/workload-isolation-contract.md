# Contract: Workspace, Mount, and Session-Boundary Isolation

## Purpose

Define the runtime contract for MM-502: Docker-backed workload launches stay inside MoonMind-owned task paths and remain isolated from managed-session identity and provider authentication state unless explicit policy says otherwise.

## In-Scope Execution Surface

This story governs Docker-backed workload execution through the control-plane-owned workload path, including:

- profile-backed workload tools such as `container.run_workload`
- bounded helper tools such as `container.start_helper` and `container.stop_helper`
- session-assisted workload launches that attach traceability metadata to the workload request

It does not redefine managed session lifecycle or widen session-side Docker authority.

## Workspace And Output Rules

Contract rules:

- `repoDir` must resolve under the MoonMind-owned shared workspace root
- `artifactsDir` must resolve under the MoonMind-owned shared workspace root
- `scratchDir`, when present, must resolve under the MoonMind-owned shared workspace root
- `declaredOutputs` values must remain relative paths under `artifactsDir`
- workload launch preparation must fail before launch if any required path escapes the approved workspace or mount roots

Expected outcome:

- out-of-bounds workspace or output paths are rejected deterministically before workload launch
- successful requests remain confined to approved workspace and artifact roots

## Session Association Rules

Contract rules:

- `sessionId`, `sessionEpoch`, and `sourceTurnId` are optional association metadata only
- `sessionEpoch` and `sourceTurnId` are invalid unless `sessionId` is present
- workload metadata may record `sessionContext` for traceability
- session association must not convert a workload into a managed session or a session continuity artifact

Expected outcome:

- session-assisted workload launches remain workload-plane executions
- returned artifacts and metadata preserve traceability without changing workload identity

## Credential Mount Rules

Contract rules:

- workload profiles must not implicitly mount auth, credential, or secret volumes through ordinary required or optional mounts
- credential-bearing mounts require explicit `credentialMounts` declarations with justification and approval reference
- absent an explicit credential-sharing policy, workload launches omit provider auth volumes by default

Expected outcome:

- implicit auth inheritance is rejected by the workload contract
- explicit credential sharing remains exceptional, reviewable, and bounded

## Policy Alignment Rules

Contract rules:

- tool routing and runtime enforcement must apply the same workload-isolation rules
- disabled or forbidden Docker modes must deny requests deterministically instead of launching them
- session-assisted launches must use the same workload policy path as non-session-assisted launches

Expected outcome:

- policy enforcement remains consistent across tool registration, activity runtime, and launcher behavior
- workload isolation rules do not weaken when session association metadata is present

## Testing Requirements

Unit coverage must verify:

- workspace-root and declared-output rejection
- association metadata validation and bounded metadata output
- explicit credential-mount requirements
- deterministic runtime denial for forbidden Docker modes

Hermetic integration coverage must verify:

- dispatcher/runtime execution of a session-associated workload request
- workload results remain workload artifacts with bounded `sessionContext`
- policy alignment between tool routing and runtime denial behavior
