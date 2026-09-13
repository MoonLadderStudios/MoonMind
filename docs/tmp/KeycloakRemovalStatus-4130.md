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
- #4121: application-bound session authority with durable revocation and
  CSRF/origin protection (`moonmind/security/session_authority_4121.py`,
  `api_service/services/session_store.py`, migration
  `375_session_authority_4121.py`; covered by
  `tests/unit/security/test_session_authority_4121.py` and
  `tests/integration/security/test_session_authority_postgres_4121.py`).
- #4124: generic OIDC (authorization code + PKCE) and trusted-proxy identity
  through the shared auth boundary
  (`moonmind/security/oidc_advanced_4124.py`,
  `moonmind/security/trusted_proxy_4124.py`,
  `moonmind/security/advanced_identity_4124.py`,
  `api_service/services/advanced_auth_service_4124.py`,
  `api_service/api/routers/auth_advanced_4124.py`; covered by
  `tests/unit/security/test_oidc_trusted_proxy_4124.py` and
  `test_advanced_identity_4124.py`). AuthenticationContracts §§12.4–12.5
  describe this landed hermetic behavior; live-IdP/MFA qualification stays
  pending per the support matrix.
- #4125: production API user resolution cut over to the qualified #4121
  session authority on every user-facing API/WebSocket path
  (`get_current_user()`/`get_current_user_optional()` boundary, §8 error
  mapping, legacy FastAPI Users JWT acceptance removed; covered by
  `tests/unit/api/test_auth_session_cutover_4125.py`). This partly
  supersedes the `BearerTransport`/`JWTStrategy` retention row in
  [KeycloakRemovalResidual-4129.md](./KeycloakRemovalResidual-4129.md).
- #4129: bundled Keycloak live behavior removed (Compose service, realm
  assets, routers, helpers, fixtures); remaining `keycloak` strings are
  classified retirement, negative-test, or migration residuals per
  [KeycloakRemovalResidual-4129.md](./KeycloakRemovalResidual-4129.md).

## What is still pending

Account lifecycle implementation and operator recovery (#4122-era),
remaining cutover slices (#4126/#4127), and product qualification evidence
(#4128) have no merged implementation on this checkout. Until they land,
AuthenticationContracts §7 (enrollment and administration) is contract
text: first-owner setup, expiring one-use invites, and local operator
recovery are declared desired state backed by the #4121 session/revocation
and #4124 identity primitives, not verified operator procedure. Likewise
the `accounts`/`oidc`/`header` end-to-end browser journeys are hermetic
contract + implementation, not browser-verified product behavior.

## Reconciliation audits recorded here (#4130 RW-4/RW-5)

- Native-chat/bridge scope (RW-4): `docs/Omnigent/OmnigentBridge.md` and
  `docs/Api/ChatInstructionsApiContract.md` carry no user-authentication
  model claims (the only `reauth` mention is the unrelated native
  `codex_reauth_required` wire code), so there was nothing to reconcile;
  no Keycloak setup or two-state language exists there.
- Residual sweep (RW-5): no active `docs/` example, troubleshooting, or
  Skill instruction claims a retired Keycloak path still works; remaining
  `keycloak` mentions are retired-selector rejection or classified
  historical/migration/negative-test residuals. Generated help is clean:
  `frontend/src/generated/openapi.ts` and `.github/` contain no `keycloak`
  references; `moonmind/cli.py` and the `api_service/main.py` OpenAPI
  description name all four modes plus the full retired
  `keycloak/default/google/local` inventory with the #4128 draft
  disclaimer. The retired-selector enumerations in
  `OmnigentAuthAdapterContract.md` §7 and `docker-compose.yaml` now name
  `local` to match `OIDCSettings.RETIRED_AUTH_PROVIDERS`.

## Plan lifecycle

[KeycloakRemovalPlan.md](./KeycloakRemovalPlan.md) stays at
`Status: Proposed` until execution is complete. Clean it up only then:
retain the accepted contracts in AuthenticationContracts and its adapter
companion, resolve backlinks, and archive or remove the temporary plan.
Do not erase necessary operator upgrade guidance prematurely.
