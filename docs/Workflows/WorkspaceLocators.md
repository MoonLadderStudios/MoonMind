# Workspace locators

The **Codex via Omnigent** product path applies this authority contract as specified by [`docs/Omnigent/CodexCreateToHostContract.md`](../Omnigent/CodexCreateToHostContract.md). Workflow Create never authors absolute paths or daemon bind sources.

Durable workflow payloads identify workspaces with the discriminated
`workspaceLocator` contract. A sandbox locator carries `workspaceId` and a relative
subpath; a managed-runtime locator carries `runtimeId`, `agentRunId`, and a relative
subpath; an external-state locator carries only an artifact reference. Locators are
compact identities, never host filesystem paths.

Only the owning worker resolves a locator. Sandbox resolution is rooted below
`temporal_sandbox`. Managed-runtime resolution requires the current runtime and run
identity to match the durable run-store record. Canonicalization is followed by a
root-containment check, so traversal and symlink escapes fail. External state is
resolved by its artifact owner and is rejected by local archive, git-effect, and
apply operations. Authority and identity failures use stable
`WORKSPACE_AUTHORITY_MISMATCH`, `WORKSPACE_IDENTITY_MISMATCH`, and
`WORKSPACE_LOCATOR_UNSUPPORTED` codes.

During the Temporal replay window, checkpoint activities continue to read legacy
`workspacePath` and `workspaceRootRef` fields. New producers prefer
`workspaceLocator`; they do not derive a new authority from an old absolute path.
The legacy fields can be removed after all histories recorded before this contract
have passed their retention and rollback windows. Activity workers emit
`workspace_locator.compatibility_path_usage` with an `operation` tag whenever
checkpoint capture, git-effect classification, or recovery/apply consumes a legacy
path field; operators use this counter to determine when the compatibility window
can close.
