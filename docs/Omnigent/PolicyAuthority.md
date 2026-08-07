# Omnigent Policy Authority

**Status:** Canonical desired state
**Issue:** MoonLadderStudios/MoonMind#3515

MoonMind owns Omnigent policy authority. A stable identity has immutable,
normalized, digest-addressed versions. Runs bind the exact identity, version,
digest, validation result, and compiled snapshot ref; later lifecycle changes
never alter historical authority.

## Canonical document

One version declares `endpoint`, `execution`, `host`, `resources`, `network`,
`workspace`, `providerProfile`, `session`, `capture`, `checkpoint`,
`remediation`, `rag`, `approvals`, `retention`, and `rollout`.

These sections govern bridge-mode eligibility; execution profile, harness, and
agent identities; host/backend/architecture admission and immutable images;
CPU, memory, process, timeout, temporary storage and concurrency; named network
and enforced egress; locator/mutation/mount/UID/GID/cache/artifact/Skill/tool/
OAuth/state boundaries; Provider Profile compatibility and capacity; the full
session lifecycle; capture classes, bounded logs, redaction and completeness;
checkpoint/resume/branch/publication/promotion; remediation allowlists, risks,
locks, limits and autonomy; retrieval scopes, collections, budgets, fallback and
credential refs; reviewer requirements; retention, rollout, diagnostics and
deprecation.

Documents contain references only. Secret bodies, OAuth-volume content, Docker
socket access, arbitrary Docker options, and raw machine paths are invalid.

## Persistence and lifecycle

`omnigent_policies` owns stable id, name, owner, visibility, and selected
default. `omnigent_policy_versions` owns monotonically increasing version,
normalized document, digest, lifecycle state, parent/clone/supersedes lineage,
actors/times, validation, compatibility, rollout, and environment-fallback
evidence.

Edits and clones append versions. Optimistic concurrency requires the latest
parent ref. Activation requires successful validation. Rollback atomically
selects an already-active version. Referenced versions remain readable.

## Compilation, enforcement, and evidence

Validation precedes credentials, leases, sessions, mounts, or host mutation.
Compilation canonicalizes the complete document into
`omnigent-policy:sha256:<digest>`. Workflow/step manifests, bridge sessions and
first messages, profiles and leases, host diagnostics and cleanup, ContextPacks,
checkpoints and branch turns, remediation and approvals, capture manifests,
terminal summaries, audit, and conformance evidence carry the same envelope.
Every boundary consumes its compiled section and rejects missing, unknown, or
contradictory authority without widening or substitution.

Stable diagnostics cover invalid images, unavailable host/backend, missing
egress, unsafe mounts, unsupported workspace, incompatible profile,
resource/capacity excess, missing capture, denied remediation/RAG/checkpoint,
and stale versions.

## Approvals

Actions resolve under the bound snapshot to `allow`, `approval_required`, or
`deny`. Approval-required rules name an approval class and reviewer rule.
Requests bind policy ref/digest, target expected state, and snapshot ref. Policy
changes do not authorize pending requests; mismatch fails stale and is
re-evaluated.

The API-owned `remediation_approvals` store is the decision authority. An opaque
caller string or approval-shaped agent input has no authority. Dispatch resolves
the stored, actor-attributed and unexpired decision and compares its exact
action/parameter digest plus run, state, checkpoint, bridge/session/host/lease,
credential-generation, policy, and security-profile bindings before any owning
adapter runs. Approved records are consumed once (with replay-safe reuse only
for the identical action idempotency key); all mismatches fail closed.

## Bootstrap migration

Startup seeds `omnigent-codex@1`, `codex-static@1`, and
`codex-on-demand@1` as explicit policies. Stock image tags are acquired and
resolved during seeding; only repository digests are projected into the
immutable active versions. `codex-on-demand@1` is the normal default and static
hosting remains an explicit advanced choice. Normal work uses persisted
selections; durable/environment conflicts fail closed.
Transient acquisition failures are retried by the API's capped reconciliation
loop; a failed first pull does not strand the deployment until restart.
`OMNIGENT_IMAGE_REF` and `OMNIGENT_HOST_IMAGE_REF` may be removed after all
supported installations have durable defaults and report no fallback for one
release.

Related: [settings](../Security/SettingsSystem.md), [Provider Profiles](../Security/ProviderProfiles.md),
[adapter](OmnigentAdapter.md), [workspaces](../Workflows/WorkspaceLocators.md),
[checkpoints](../Workflows/CheckpointBranchSystem.md), [remediation](../Workflows/WorkflowRemediation.md),
and [RAG](../Rag/WorkflowRag.md).
