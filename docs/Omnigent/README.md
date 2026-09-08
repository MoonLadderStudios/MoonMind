# Omnigent

**Document Class:** Module entrypoint
**Status:** Current
**Owners:** MoonMind Platform
**Last updated:** 2026-09-07
**Authority:** Short architecture/operator entrypoint for the Omnigent module. Each contract below has one surviving owner; this page routes, it does not restate.
**Issue:** [MoonLadderStudios/MoonMind#3962](https://github.com/MoonLadderStudios/MoonMind/issues/3962).

Omnigent is becoming MoonMind's primary runtime provider: one generic execution
plane over approved harnesses (Codex, Claude Code, OpenCode), while MoonMind
keeps Temporal orchestration, Provider Profiles, OAuth enrollment, policy,
workspaces, evidence, publication, and cleanup. Provider-native execution and
MoonMind-owned credentials, policy, workflow control, and evidence are not
interchangeable responsibilities.

## Supported startup path

1. Select Runtime (`external/omnigent`) and one Provider Profile; resolve its
   pinned execution configuration.
2. Admission compiles one immutable execution plan (harness, Provider Profile,
   runtime pack, credential materializer, Host Class, launch policy, model,
   realizer) through the one shared selection boundary.
3. The trusted planner binds the plan to a fenced runtime binding: acquired
   credential generation, host lease, exact shared-image digest, and
   exact-host attestation.
4. The generic host lifecycle launches the Host Class image, attaches only the
   selected runtime's credential material, and runs the canonical session and
   turn-command path.
5. Terminal, event, resource, checkpoint, and publication evidence is harvested;
   run-owned state is destroyed and profile-owned state is detached but
   preserved.

## Exact limitations

- A shared image never authorizes every installed runtime. Each Host Class
  declares only its own harness, runtime pack, and materializers.
- Generic Codex and Claude Code combinations are `disabled` until their
  exact protected-live evidence passes and the qualification flag promotes
  them (see `RuntimeProviderRollout.md`); selection before qualification
  fails closed and never falls back to another runtime, profile, or host mode.
- "Supported" means repository evidence or a protected live artifact proves the
  exact combination (image digest, harness, pack, materializer, model, policy,
  realizer). Code presence alone is "implemented" or "unverified".
- Direct Codex / direct Claude paths remain labeled compatibility paths until
  their retirement rows close.

## Status vocabulary

| Term | Meaning |
| --- | --- |
| registered | Harness is registration data, not lifecycle code. |
| discovered | Seen in provider inventory; no trust implied. |
| installed | Binary present in the image; not support. |
| admitted | A plan selected it through the shared selection boundary. |
| qualified | Exact-combination evidence passed for this deployment. |
| selected-by-default | A qualified combination promoted per combination by rollout policy. |

## Contract owners

| Contract | Surviving owner |
| --- | --- |
| Pinned upstream transport and native UI contract | [`OmnigentBridge.md`](./OmnigentBridge.md) |
| Agent / Provider Profile selection and defaults | [`PrimaryRuntimeProviderStrategy.md`](./PrimaryRuntimeProviderStrategy.md) §9, enforced by [`RuntimeProviderRollout.md`](./RuntimeProviderRollout.md) |
| Host Classes and runtime packs | [`SharedHostImage.md`](./SharedHostImage.md) §§2–3 |
| Credential materializers and enrollment | [`OmnigentHostOAuth.md`](./OmnigentHostOAuth.md); ownership table in [`SharedHostImage.md`](./SharedHostImage.md) §4 |
| Session and turn control | [`CanonicalTurnCommandBoundary.md`](./CanonicalTurnCommandBoundary.md); lifecycle decisions in [`OmnigentLifecycleReconciler.md`](./OmnigentLifecycleReconciler.md) |
| Exact support evidence | [`ConformanceAndLiveSmoke.md`](./ConformanceAndLiveSmoke.md); Codex rows in [`CodexSupportAndCutover.md`](./CodexSupportAndCutover.md) |
| Credential cleanup (materializer teardown, ownership) | [`SharedHostImage.md`](./SharedHostImage.md) §4 (ownership) with launch-order detail in [`OmnigentHostOAuth.md`](./OmnigentHostOAuth.md) §13+ |
| Session recovery (retry, checkpoint restore, terminal evidence) | [`PrimaryRuntimeProviderStrategy.md`](./PrimaryRuntimeProviderStrategy.md) §5.11 with transition decisions in [`OmnigentLifecycleReconciler.md`](./OmnigentLifecycleReconciler.md) §§3–5 |
| Module packages and dependency direction | [`OmnigentModuleArchitecture.md`](./OmnigentModuleArchitecture.md) |
| Retirement of duplicate architecture | Code-owned `moonmind/omnigent/legacy_retirement.py`, described in [`OmnigentModuleArchitecture.md`](./OmnigentModuleArchitecture.md) §5 |

The full per-file map — every `docs/Omnigent/` path and duplicated section
mapped to its surviving owner, with design status and outstanding issue
references — lives in [`ContractOwnership.md`](./ContractOwnership.md).

## Credential ownership (summary, not a copy)

Run-owned material (for example `opencode-auth-json@1`) may be destroyed on
cleanup. Profile-owned OAuth homes (for example `codex-oauth-home@1`) are
generation-fenced and detached but preserved. The binding table is
[`SharedHostImage.md`](./SharedHostImage.md) §4; the mechanisms are
[`OmnigentHostOAuth.md`](./OmnigentHostOAuth.md) §§6–9.

## ManagedAgents boundary

Omnigent hosts and MoonMind managed sessions share the container-job and MCP
tool contract but not execution ownership. The shared contract is owned from
the ManagedAgents side by
[`../ManagedAgents/DockerBackendService.md`](../ManagedAgents/DockerBackendService.md)
§15; provider-native session semantics stay in their owning ManagedAgents docs
(for example `CodexCliManagedSessions.md`, `ClaudeCodeManagedSessions.md`).
