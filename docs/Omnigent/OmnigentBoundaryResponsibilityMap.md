# Omnigent Boundary Responsibility Map

**Document Class:** Canonical declarative
**Status:** Current
**Owners:** MoonMind Platform
**Last updated:** 2026-09-06
**Authority:** The caller-verified inventory of suspected duplicated Omnigent
runtime behavior: who owns each state/decision, its persistence purpose, the
concrete upstream replacement status, and the evidence-based precondition for
any future removal.

**Issue:** [MoonLadderStudios/MoonMind#3928](https://github.com/MoonLadderStudios/MoonMind/issues/3928)
**Children:** #3954 (inventory), #3955 (embedded transport), #3956 (chat),
#3957 (upstream pin), #3958 (test-runner separation)

## Related documents

- [`docs/Omnigent/OmnigentModuleArchitecture.md`](./OmnigentModuleArchitecture.md) — enforced module ownership and dependency direction
- [`docs/Omnigent/OmnigentBridge.md`](./OmnigentBridge.md) — bridge authority model
- [`docs/Omnigent/OmnigentHarnessPlatformDesign.md`](./OmnigentHarnessPlatformDesign.md) — target harness-platform semantics
- [`docs/UI/WorkflowChatPanel.md`](../UI/WorkflowChatPanel.md) — facade ownership, workflow-state, idempotency, scanning, and audit controls
- [`docs/Security/SecretsSystem.md`](../Security/SecretsSystem.md) — credential ownership and audit controls

## 1. What this document owns

This document is the #3954 inventory required by #3928 boundary step 1: a
concise responsibility map built from reachable production callers and the
exact pinned upstream API. For each suspected duplicate it records the owner
of the state/decision, its persistence purpose, and the concrete upstream
replacement status.

A row marked **Retain** is not a refusal to simplify: it records that the
suspected duplicate has live production callers, a persisted-data obligation,
or an owner in a sibling program, so the epic's own preconditions (retire
only after callers and persisted consumers are resolved; replace only
demonstrated duplicated behavior with supported pinned upstream calls) are not
met. Removing such a row without meeting its stated precondition would weaken
a governance boundary the epic requires preserved.

Machine enforcement lives in
`tests/unit/omnigent/test_omnigent_boundary_3928.py`: production code must not
import test-only runners, and the production qualification gates named here
must stay in the production path. Dependency direction stays owned by
`tests/unit/omnigent/test_module_architecture.py`.

## 2. Pinned reference points

- Upstream `omnigent` gitlink pin: `f04b0354fb5344c1ea8b92795ceb6760a9ad7595`.
  Upstream upgrades are reviewed, exact-artifact changes (#3957 owns pin
  updates; this epic records the pin it was verified against).
- Default host protocol mode is `proxy`
  (`BridgeCompatibility.host_protocol_mode`, `moonmind/omnigent/bridge_config.py`).
  `embedded_omnigent_compatible_server` mode cannot be enabled without proxy
  conformance, live smoke-test, and upstream host-auth conformance evidence
  refs (fail-before-mutation, §2.4 / §16 rule 8).

## 3. Authority split

Upstream owns harness mechanics, provider-native session execution, the native
UI application, and upstream wire semantics. Transport-specific translation
stays behind MoonMind's adapter boundary (`host_protocol_adapter.py`,
`runner_protocol_adapter.py`).

MoonMind owns user/workflow authorization, immutable Agent/Provider Profile
selection, permitted credential resolution, launch policy and exact
qualification, canonical workflow/turn ownership, idempotent side effects,
artifact evidence, and product projections. A local cache/projection is not
automatically another source of lifecycle truth.

## 4. Inventory

### 4.1 Experimental embedded transport — Retain (#3955 precondition unmet)

| Module | Decision owned | Persistence purpose |
| --- | --- | --- |
| `moonmind/omnigent/bridge_embedded.py` | Embedded host-protocol facade over the pinned upstream surface | None beyond in-flight facade state; durable session/turn authority stays in `bridge_store.py` |
| `moonmind/omnigent/embedded_host_channel.py` | In-memory host/runner tunnel bindings | Ephemeral channel registry only |
| `moonmind/omnigent/embedded_evidence.py` | Embedded evidence claim validation | Reads MoonMind artifacts; persists nothing |

Reachable production callers (verified): `api_service/api/routers/omnigent_bridge.py`,
`api_service/api/routers/omnigent_bridge_composition.py`,
`api_service/api/routers/omnigent_agent_profiles.py`.

Verdict: **Retain.** Live callers in three routers; #3955 permits retirement
only after callers and persisted consumers are resolved. Removal precondition:
migrate the three routers' embedded branches to the surviving mode and resolve
historical embedded reads (`omnigent_bridge.py` historical-embedded paths)
with an explicit replay/cutover path, then delete.

### 4.2 Chat integration — Retain as distinct layers (#3956)

| Module | Decision owned |
| --- | --- |
| `moonmind/omnigent/workflow_chat_facade.py` (#3634) | HTTP + SSE route/method allowlist, per-request identity substitution, capability recompute |
| `moonmind/omnigent/native_ui.py` (#3638) | Binding-scoped serving primitives for the native app routes |
| `moonmind/omnigent/native_ui_compat.py` (#3635) | Versioned network-surface compatibility map (WebSocket, PTY, logs, workspace, browser panes) pinned to `omnigent.server.v1` |
| `frontend/src/entrypoints/WorkflowChatNative.tsx` | Embeds the upstream-maintained application; legacy read-only projection as fallback, never a second composer |
| `api_service/api/routers/omnigent_native_ui.py` | Composes serving primitives into the MoonMind-scoped routes |

Reachable production callers (verified): `omnigent_bridge.py` imports all
three backend layers; `omnigent_native_ui.py` router imports the facade and
serving primitives; `executions.py` evaluates native UI compatibility;
`workflow_chat_acceptance.py` consumes the compatibility map.

Verdict: **Retain.** The layers own distinct decisions (facade allowlist vs
serving vs transport coverage), and `docs/UI/WorkflowChatPanel.md` plus
`docs/Security/SecretsSystem.md` require the facade's ownership,
workflow-state, idempotency, scanning, and audit controls. Collapsing them is
possible only if upstream publishes a compatible SDK surface; per epic
boundary step 4 that migration is optional, and private internals must not be
used. Removal precondition for any single layer: name the surviving owner of
its decision and prove the required doc control still holds.

### 4.3 Catalog validators — Retain in production (#3958 preserve side)

| Module | Decision owned |
| --- | --- |
| `moonmind/omnigent/conformance.py` | Versioned conformance evidence contracts |
| `moonmind/omnigent/exact_artifact_conformance.py` | Tier-1 exact-artifact gate: pass/fail on the exact deployable images by immutable digest |
| `moonmind/omnigent/live_verification_health.py` | Live verification readiness projection and safe-failure diagnostics |

Reachable production callers (verified): `api_service/api/routers/omnigent_catalog.py`
imports all three. These are production qualification gates, not test runners,
despite their conformance naming.

Verdict: **Retain in the production path.** Moving them to test-only would
delete exact qualification from the launch journey the epic requires
preserved. #3958 separates *test runners* from this production evidence
validation; it does not relocate the validation.

### 4.4 Protocol adapters — Retain

| Module | Decision owned | Callers |
| --- | --- | --- |
| `moonmind/omnigent/host_protocol_adapter.py` | Upstream wire-semantics translation behind the adapter boundary | `embedded_host_channel.py`, `omnigent_bridge.py` |
| `moonmind/omnigent/runner_protocol_adapter.py` | Runner frame translation | `embedded_host_channel.py` |

Verdict: **Retain.** This is the transport-specific translation the epic keeps
behind MoonMind's existing adapter boundary, not duplicated runtime behavior.

### 4.5 Tool- and test-owned builders — Already separated (#3958)

| Module | Owner | Production importers |
| --- | --- | --- |
| `moonmind.omnigent.cutover_conformance` | `tools/build_codex_omnigent_cutover_evidence.py` | None |
| `moonmind.omnigent.remediation_matrix_conformance` | `tools/build_operator_remediation_release_evidence.py` | None |
| `moonmind.omnigent.embedded_acceptance` | `tools/build_omnigent_embedded_acceptance.py` + unit tests | None |
| `moonmind.omnigent.faultlab` (`moonmind/omnigent/faultlab/`) | Fault-injection suite (`docs/Omnigent/OmnigentFaultInjectionSuite.md`) | None |

Verdict: **No action.** Already tool/test-owned with zero production
importers; the boundary test (§1) locks this in. Note
`moonmind/omnigent/workflow_chat_acceptance.py` is *not* in this table:
production `legacy_retirement.py` consumes its manifest validation for the
retirement guard, so it is production evidence validation, not a test-only
runner.

### 4.6 Sibling-program owned — Out of scope for this epic

`retirement_drain.py`, `legacy_retirement.py`, `session_migration.py`,
`session_migration_inventory.py`, `cutover.py`,
`runtime_provider_rollout.py`, `runtime_provider_migration_status.py`, and
shared-image/runtime-pack and OAuth materializer work are owned by
#3825 / #3832–#3835 (generic-runtime qualification, rollout, Compose
consolidation, retirement). This epic must not create a contradictory second
migration program against them; removals there land under their owning issues.

## 5. Fail-before-mutation gates (preserved)

- Unknown routes: facade explicit route/method allowlist (`workflow_chat_facade.py`); never a generic open reverse proxy.
- Ambiguous identities: opaque `chatBindingId` with per-request identity substitution; browser never receives provider session ids, credentials, or workspace authority.
- Incompatible profiles/images: `compatibility.profile` must be Omnigent; Tier-1 exact-artifact gate fails closed on digest/capability mismatch.
- Missing qualification: embedded mode requires three evidence refs at config validation; live-verification health fails the rollout/readiness gate closed on stale or missing canary evidence.

## 6. Outcomes

- Removed lines of code under this epic in this pass: **0**. Every suspected
  duplicate has live production callers (§4.1, §4.2), is a production
  qualification gate (§4.3), is kept adapter translation (§4.4), is already
  tool/test-separated (§4.5), or is owned by a sibling program (§4.6).
- Removed tables/fields: **0** for the same reasons.
- The former sub-20,000-line target is not a correctness gate and did not
  justify weakening any control above.
- Evidence produced instead: this inventory (#3954 foundation) and the
  machine-enforced boundary test named in §1 (#3958 separation guard).
