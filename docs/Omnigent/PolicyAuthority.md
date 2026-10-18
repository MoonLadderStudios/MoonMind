# Omnigent Policy Authority

**Status:** Canonical desired state

MoonMind owns Omnigent policy authority. A stable identity has immutable,
normalized, digest-addressed versions. Runs bind the exact identity, version,
digest, validation result, and compiled snapshot ref; later lifecycle changes
never alter historical authority.

## Canonical document

One version declares `endpoint`, `execution`, `host`, `resources`, `network`,
`workspace`, `providerProfile`, `session`, `capture`, `checkpoint`,
`remediation`, `approvals`, `retention`, and `rollout`.

MoonMind does not provide native retrieval.
An older recorded version may also carry a `rag` section; it stays readable
and executable with its recorded digest, but the policy API rejects `rag` on
new versions. Authoring uses a separate document schema that allows only an
absent or null `rag`; historical decoding retains the recorded section.
Bootstrap and release successors omit it.

These sections govern bridge-mode eligibility; execution profile, harness, and
agent identities; host/backend/architecture admission and immutable images;
CPU, memory, process, timeout, temporary storage and concurrency; named network
and enforced egress; locator/mutation/mount/UID/GID/cache/artifact/Skill/tool/
OAuth/state boundaries; Provider Profile compatibility and capacity; the full
session lifecycle; capture classes, bounded logs, redaction and completeness;
checkpoint/resume/branch/publication/promotion; retention, rollout,
diagnostics and deprecation. The `remediation` and `approvals` sections are
validated and bound into the digest, but no boundary consumes them: a
remediation never acts on its target, and nothing else resolves approval rules.

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
checkpoints and branch turns, capture manifests,
terminal summaries, audit, and conformance evidence carry the same envelope.
Every boundary consumes its compiled section and rejects missing, unknown, or
contradictory authority without widening or substitution.

For an admitted Codex execution plan, the selected launch policy is authoritative;
the execution target's built-in default is only a selection fallback. Dispatch
checks the requested policy against the plan and the target's harness against
the admitted harness, without requiring the selected policy to equal that
default. A request that omits the policy selection launches the admitted policy
rather than the target default. Policy and effective-launch snapshot digests
remain mandatory before lease acquisition or host mutation.

Stable diagnostics cover invalid images, unavailable host/backend, missing
egress, unsafe mounts, unsupported workspace, incompatible profile,
resource/capacity excess, missing capture, denied checkpoint, and
stale versions.

## Bootstrap migration

Startup seeds `omnigent-codex@1`, `codex-static@1`, and
`codex-on-demand@1` as explicit Codex policies. When OpenCode support is
enabled, startup also seeds `omnigent-on-demand@1` and
`opencode-on-demand@1` with the qualified OpenCode harness, provider, and host
image authority. Stock image tags are acquired and resolved during seeding;
only repository digests are projected into immutable active versions. Each
host-image family seeds independently, so an unavailable optional harness does
not withhold another runtime's valid authority. OpenCode policies reuse the
exact host digest already qualified by deployment image reconciliation. Codex
and Claude policies use the deployment-resolved shared host image
(`OMNIGENT_SHARED_HOST_IMAGE_REF`), which owns the MoonMind tools under
`/opt/moonmind-tools`; the upstream `omnigent-host` image does not carry them,
so bootstrap versions any bootstrap-owned Codex default still pinned to it.
A version's image refs name the image family and record the images current
when it was written: fresh admission, including each schedule-started
occurrence, binds the installed same-repository server and host images to the
attempt (an explicitly requested Host Class stays exact, and a foreign host
family fails closed), and admitted attempts keep the images they recorded.
Bootstrap therefore cuts a successor only for a resource-default migration or
an installed image of another family; a same-family digest move leaves the
recorded version, its Agent Profiles, OAuth bindings and schedules unchanged.
`codex-on-demand@1` is the normal Codex default and static hosting remains an
explicit advanced choice. Normal work uses persisted selections; a planner or
runtime may not synthesize one policy from another, and durable/environment
conflicts fail closed. Bootstrap Agent Profiles and unbound replay paths resolve
the policy identity's current persisted default, then carry that exact version;
already-bound runs continue using their recorded version.
Transient acquisition failures are retried by the API's capped reconciliation
loop; a failed first pull does not strand the deployment until restart.
`OMNIGENT_IMAGE_REF` and `OMNIGENT_HOST_IMAGE_REF` may be removed after all
supported installations have durable defaults and report no fallback for one
release.

Related: [settings](../Security/SettingsSystem.md), [Provider Profiles](../Security/ProviderProfiles.md),
[adapter](OmnigentAdapter.md), [workspaces](../Workflows/WorkspaceLocators.md),
[checkpoints](../Workflows/CheckpointBranchSystem.md), [remediation](../Workflows/WorkflowRemediation.md),
and [context assembly](../Rag/WorkflowRag.md).

## Single-user operation

Policies are instance resources: the admitted operator reads and manages
every policy without a human-owner lookup. New policies carry no
`owner_user_id`; retained `owner_user_id`/`visibility` values persist as
non-authoritative provenance while versions, compiled/runtime bindings, and
operational restrictions are preserved. Settings-permission gates and
secret-handling policy stay at their owning boundaries; raw secret access
and agent policy permissions are not broadened. See
[Single-User Application Design](../SingleUserApplicationDesign.md).
