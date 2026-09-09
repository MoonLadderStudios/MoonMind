# Keycloak Removal Execution Status (point-in-time ledger for #4130)

> Temporary execution scaffolding, not a canonical contract. The durable
> authentication and support invariants live in
> [AuthenticationContracts.md](../Security/AuthenticationContracts.md) §12;
> this file holds the volatile implementation-status ledger (landed tickets,
> pending backlog IDs, checkout-specific qualification results, and plan
> lifecycle) so the canonical document cannot go stale as issues land.
> Archive or remove this file when execution is complete.

## What has landed

- #4117: owned-surface inventory and acceptance contract (baseline evidence).
- #4118: qualified upstream adapter boundary (AuthenticationContracts §11 and
  [OmnigentAuthAdapterContract.md](../Security/OmnigentAuthAdapterContract.md)).
- #4120: one-selector configuration and deployment defaults
  (`moonmind/security/auth_modes_4120.py`: validation, fresh-vs-upgrade
  classification, persisted migration decision, durable signing-key
  resolution, disabled-mode exposure, base-URL/proxy/cookie policy,
  redacted diagnostics; covered by `tests/unit/security/test_auth_modes_4120.py`
  and `test_auth_compose_rendered_4120.py`).
- #4129: bundled Keycloak live behavior removed (Compose service, realm
  assets, routers, helpers, fixtures); remaining `keycloak` strings are
  classified retirement, negative-test, or migration residuals per
  [KeycloakRemovalResidual-4129.md](./KeycloakRemovalResidual-4129.md).

## What is still pending

Account lifecycle implementation and recovery (#4121/#4122-era), session
contracts (#4124-era), remaining cutover slices (#4125/#4126/#4127), and
product qualification evidence (#4128) have no merged implementation on
this checkout. Until they land, AuthenticationContracts §7 (enrollment and
administration) is contract text: first-owner setup, expiring one-use
invites, logout/revocation bounds, MFA-as-migration-problem, and credential
separation are declared desired state, not verified operator procedure.

## Plan lifecycle

[KeycloakRemovalPlan.md](./KeycloakRemovalPlan.md) stays at
`Status: Proposed` until execution is complete. Clean it up only then:
retain the accepted contracts in AuthenticationContracts and its adapter
companion, resolve backlinks, and archive or remove the temporary plan.
Do not erase necessary operator upgrade guidance prematurely.
