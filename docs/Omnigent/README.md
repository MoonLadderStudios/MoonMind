# Omnigent

**Document Class:** Module entrypoint
**Status:** Current
**Owners:** MoonMind Platform
**Last updated:** 2026-10-16
**Authority:** Short architecture/operator entrypoint for the Omnigent module. Each contract below has one owner; this page routes, it does not restate.

Omnigent is MoonMind's agent backend: one generic execution plane over approved
harnesses (Codex, Claude Code, OpenCode), while MoonMind keeps Temporal
orchestration, Provider Profiles, OAuth enrollment, policy, workspaces,
evidence, publication, and cleanup. Provider-native execution and
MoonMind-owned credentials, policy, workflow control, and evidence are not
interchangeable responsibilities.

## Startup path

1. Select a Harness and one Provider Profile; admission resolves the profile's
   execution configuration.
2. Admission compiles one immutable execution plan (harness, Provider Profile,
   runtime pack, credential materializer, Host Class, launch policy, model,
   realizer) through the shared selection boundary.
3. The trusted planner binds the plan to a fenced runtime binding: acquired
   credential generation, host lease, exact shared-image digest, and
   exact-host attestation.
4. The generic host lifecycle launches the Host Class image, attaches only the
   selected runtime's credential material, and runs the canonical session and
   turn-command path.
5. Terminal, event, resource, checkpoint, and publication evidence is harvested;
   run-owned state is destroyed and profile-owned state is detached but
   preserved.

## Limitations

- A shared image never authorizes every installed runtime. Each Host Class
  declares only its own harness, runtime pack, and materializers.
- Codex uses the generic realizer by default. Explicit
  `MOONMIND_OMNIGENT_GENERIC_CODEX_QUALIFIED=false` preserves the profile-bound
  compatibility choice. Rollout stops, custom policy and recorded plans retain
  their meaning; see [Runtime Provider Rollout](RuntimeProviderRollout.md).
- Generic Claude Code has no profile-bound realizer, so it is admitted as an
  explicit choice by default: a connected Anthropic OAuth Provider Profile
  needs no manual toggle. Image, policy, inventory, credential readiness, and
  exact-host attestation still gate each launch, and
  `MOONMIND_OMNIGENT_GENERIC_CLAUDE_QUALIFIED=false` stops new admission.
- An unavailable explicit selection never falls back to another runtime,
  profile, or host mode.
- Ordinary admission (`MOONMIND_OMNIGENT_EVIDENCE_POLICY` omitted, blank, or
  `either`) needs no historical certificate; explicit `protected` or
  `deployment` keeps strict certification. Code presence alone does not certify
  a live journey. See `ConformanceAndLiveSmoke.md`.
- Direct `codex_cli` and `claude_code` remain labeled compatibility Backends,
  offered only under Advanced.

## Status vocabulary

| Term | Meaning |
| --- | --- |
| registered | Harness is registration data, not lifecycle code. |
| discovered | Seen in provider inventory; no trust implied. |
| installed | Binary present in the image; not support. |
| admitted | A plan selected it through the shared selection boundary. |
| qualified | Exact-combination evidence passed for this deployment. |
| selected-by-default | A target selected by the rollout policy; not proof of live certification. |

## Contract owners

| Contract | Owner |
| --- | --- |
| Pinned upstream transport and native UI contract | [`OmnigentBridge.md`](./OmnigentBridge.md) |
| Harness / Provider Profile selection and backend strategy | [`PrimaryRuntimeProviderStrategy.md`](./PrimaryRuntimeProviderStrategy.md) §9 |
| Selection and rollout controls in code | [`RuntimeProviderRollout.md`](./RuntimeProviderRollout.md) |
| Execution configurations and deployment-managed defaults | [`AgentProfiles.md`](./AgentProfiles.md) |
| Host Classes and runtime packs | [`SharedHostImage.md`](./SharedHostImage.md) §§2–3 |
| Credential materializers and enrollment | [`OmnigentHostOAuth.md`](./OmnigentHostOAuth.md); ownership table in [`SharedHostImage.md`](./SharedHostImage.md) §4 |
| Session and turn control | [`CanonicalTurnCommandBoundary.md`](./CanonicalTurnCommandBoundary.md); lifecycle decisions in [`OmnigentLifecycleReconciler.md`](./OmnigentLifecycleReconciler.md) |
| Exact support evidence | [`ConformanceAndLiveSmoke.md`](./ConformanceAndLiveSmoke.md); Codex rows in [`CodexSupportAndCutover.md`](./CodexSupportAndCutover.md) |
| Credential cleanup (materializer teardown, ownership) | [`SharedHostImage.md`](./SharedHostImage.md) §4 (ownership) with launch-order detail in [`OmnigentHostOAuth.md`](./OmnigentHostOAuth.md) §13+ |
| Session recovery (retry, checkpoint restore, terminal evidence) | [`PrimaryRuntimeProviderStrategy.md`](./PrimaryRuntimeProviderStrategy.md) §5.11 with transition decisions in [`OmnigentLifecycleReconciler.md`](./OmnigentLifecycleReconciler.md) §§3–5 |
| Module packages and dependency direction | [`OmnigentModuleArchitecture.md`](./OmnigentModuleArchitecture.md) |
| Retirement of duplicate architecture | Code-owned `moonmind/omnigent/legacy_retirement.py`, described in [`OmnigentModuleArchitecture.md`](./OmnigentModuleArchitecture.md) §5 |

[`ContractOwnership.md`](./ContractOwnership.md) maps every `docs/Omnigent/`
path and formerly duplicated section to its owner.

Run-owned credential material (for example `opencode-auth-json@1`) may be
destroyed on cleanup; profile-owned OAuth homes (for example
`codex-oauth-home@1`) are generation-fenced and detached but preserved.

## ManagedAgents boundary

Omnigent hosts and MoonMind managed sessions share the container-job and MCP
tool contract but not execution ownership. The shared contract is owned from
the ManagedAgents side by
[`../ManagedAgents/DockerBackendService.md`](../ManagedAgents/DockerBackendService.md)
§15; provider-native session semantics stay in their owning ManagedAgents docs
(for example `CodexCliManagedSessions.md`, `ClaudeCodeManagedSessions.md`).
