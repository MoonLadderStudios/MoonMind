# Workspace locators

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

## Container-job workspace sources

Container jobs (MoonLadderStudios/MoonMind#3255) extend this same canonical
locator contract rather than introducing a Docker-only identity model. The
public submission carries only a typed logical `workspaceRef` with one of four
source kinds — `moonmind-run`, `moonmind-session`, `omnigent-session`, or
`artifact-workspace` — plus a normalized relative subpath. No caller ever
supplies a host path, daemon URL, or bind source.

The owning agent-runtime worker resolves the reference into a trusted,
daemon-visible mount plan. Resolution first correlates the referenced
run/session/artifact against the authenticated owner and source recorded on the
trusted workflow input, then maps the kind to an approved bind root or named
volume (the `agent_workspaces` volume and the Omnigent worktree root are the
canonical roots), then applies containment, traversal, symlink-escape, and
duplicate-target-collision checks. Every job sees its repository at the fixed
`/workspace` target, with job-owned `/artifacts` and `/scratch` targets and any
policy-approved caches on their own declared targets; output collection can only
read from the approved `/artifacts` root.

Before acquiring the requested image, a visibility probe proves the selected
daemon can read the resolved workspace and write the artifacts area using a
small probe image; the only thing the writable probe mutates is a deterministic,
job-owned marker that is removed safely. Resolution and visibility failures fail
closed with the stable, caller-visible classifications `workspace_not_found`,
`permission_denied`, and `workspace_not_visible`. The resolved host/volume source
never crosses the boundary: callers and agents only ever receive an opaque
`container-workspace://` handle, and re-resolution happens owner-side at every
step so no host path enters activity results or Temporal history.

During the Temporal replay window, checkpoint activities continue to read legacy
`workspacePath` and `workspaceRootRef` fields. New producers prefer
`workspaceLocator`; they do not derive a new authority from an old absolute path.
The legacy fields can be removed after all histories recorded before this contract
have passed their retention and rollback windows. Activity workers emit
`workspace_locator.compatibility_path_usage` with an `operation` tag whenever
checkpoint capture, git-effect classification, or recovery/apply consumes a legacy
path field; operators use this counter to determine when the compatibility window
can close.
