# Upstream Duplication Audit

**Document Class:** Canonical declarative
**Status:** Current
**Owners:** MoonMind Platform
**Last updated:** 2026-09-08
**Authority:** Caller-backed ownership audit of suspected MoonMind/Omnigent
duplication: which side owns each mutation or decision, and what must be
proven before anything is removed.
**Issue:** [MoonLadderStudios/MoonMind#3954](https://github.com/MoonLadderStudios/MoonMind/issues/3954).

Parent: #3928. Coordinates with #3925 (runtime convergence), #3935 (session
ownership), #3946 (projection writes), and #3956 (native UI).

## Related documents

- [`OmnigentAdapter.md`](./OmnigentAdapter.md) — ownership boundary principle
- [`ContractOwnership.md`](./ContractOwnership.md) — per-file ownership map
- [`OmnigentModuleArchitecture.md`](./OmnigentModuleArchitecture.md) — retirement inventory
- [`OmnigentBridge.md`](./OmnigentBridge.md) — transport and native UI contract

## Scope correction

Reviewed against `63bce9852ffa33e33cb0b416bc24a654b1b6f92b`. Module and table
counts are candidates for investigation, not a deletion list. A MoonMind
workflow binding, immutable profile snapshot, credential lease, event journal,
catalog projection, or artifact manifest is not redundant simply because
Omnigent also has a session, agent, provider, or event object.

The code-owned record is
`moonmind/omnigent/upstream_duplication_audit.py` (`OWNERSHIP_TABLE`,
fail-closed `check_*` guards, `evaluate_removal_eligibility`). This document
states the durable disposition; the module enforces it in CI.

## Ownership table

| Suspected duplicate | Production entrypoint | State/decision owned | Surviving owner | Upstream replacement at pinned commit |
| --- | --- | --- | --- | --- |
| Workflow binding vs upstream session | `bridge_store.py` + `bridge_proxy.py` | Bridge authorization, idempotency ownership, attach ordering, session.created journal | MoonMind binding | None demonstrated: audit baseline differs from implementation pin, submodule not checked out |
| Immutable profile snapshot vs launch args | `execution_adapters.py` + `profile_bound_execution.py` | Profile selection, policy snapshot, effectiveLaunch evidence | MoonMind adapters | None demonstrated: no audited upstream policy/snapshot API |
| Credential lease vs runner token | `provider_leases.py` + `host_auth_adapter.py` | Profile lease, OAuth-home exclusivity, generation fencing, release-last | MoonMind lease | Pinned verifier proves host identity only, not lease ownership |
| Event journal vs provider stream | `bridge_store.py` event index + `execute.py` | Durable pages, normalized events, raw bounded journal | MoonMind journal | None demonstrated: provider SSE has no durable cursors |
| Catalog projection vs agent inventory | `harness_platform/catalog.py` + `omnigent_catalog.py` router | Trust classification, profile support evidence, readiness gating | MoonMind projection | None demonstrated: stock inventory carries no trust semantics |
| Artifact manifest vs session files | `bridge_artifacts.py` + `workspace_publication.py` | Terminal result, capture manifests, redacted diagnostics, ArtifactRefs | MoonMind manifest | None demonstrated: upstream files lack provenance/retention |
| Native UI facade vs upstream app | `native_ui.py` + `workflow_chat_facade.py` | Binding-scoped routes, allowlist, bootstrap contract, version gate | MoonMind facade | Version-gated only; unknown versions fail closed, never proxied directly |
| Wire transport vs host protocol | `omnigent_client.py` + `bridge_proxy.py` | Facade validation, bounds, redacted codes, harvest-before-delete | MoonMind facade | None demonstrated: raw wire handling has no idempotency/ordering |

Every row's persisted consumers, removal criteria, and proving test live in
`OWNERSHIP_TABLE`. Disposition for all eight rows is **preserve**.

## Boundary rule

Upstream owns runtime mechanics (live runner, harness protocol, raw
inventory/stream observations). MoonMind owns orchestration and governance
(authorization, immutable policy, bounded evidence, canonical mutation
identity, credential ownership). Cached projections and immutable evidence are
downstream read models with provenance, freshness, and retention; only
independently mutable duplicate authority is ever removed, never every local
representation.

## Fail-closed rejections

Upstream drift, wrong owner, stale generation, unknown route, missing
evidence, and credential mismatch are rejected with stable audit codes by the
`check_*` guards. Unknown upstream fields, routes, and incompatible
capabilities fail closed. No unrestricted proxy or direct upstream browser
path replaces the binding-scoped facade.

## Removals and residual dependencies

No table or field is removed by this audit: no verified replacement contract
exists, so every `evaluate_removal_eligibility` verdict is blocked with its
persisted-consumer and removal-criteria blockers. Residual dependencies are
the eight preserved candidates above; each names its removal criteria in the
code-owned row rather than in a generic compatibility framework. Reduction is
measured after preserving behavior (`audit_reduction_summary`: 8 examined, 8
preserved, 0 removed); no LOC or table quota forces removal of the governance
layer.
