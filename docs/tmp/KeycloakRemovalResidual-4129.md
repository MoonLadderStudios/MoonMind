# Keycloak Removal Residual Manifest — #4129

Status: deletion candidate for MoonLadderStudios/MoonMind#4129 (parent epic
#4116, plan coverage K5). Temporary execution scaffolding under `docs/tmp/`
per AGENTS.md. This manifest records the reviewed residual state after the
bundled Keycloak integration was removed from live behavior. Production
container/volume/database retirement stays in the separately operator-gated
cutover issue: no `docker compose down -v`, broad orphan pruning, or
shared-role deletion was performed here.

Revision traced: `main` at `1a546dd90` (#4117 inventory) plus this change.

## 1. Deleted live behavior

- `docker-compose.yaml`: `keycloak` service (profile, image, realm mount, host
  port `8085:8080`), `./keycloak` API mount, `KC_DB_PW` forwarding, and the
  `OIDC_ISSUER_URL` default pointing at `http://keycloak:8080/realms/moonmind`.
  OIDC variables now default empty for generic external OIDC (#4124 contract).
- `init_db_scripts/01-create-dbs.sh`: `keycloak` role/database provisioning.
  Shared PostgreSQL and Temporal databases/roles preserved.
- `keycloak/realm-export.json` + `keycloak/Dockerfile` (directory removed):
  realm assets including the MoonMind-owned `open-webui` and `api-service`
  client contracts. No live Compose consumer referenced them (no `open-webui`
  service in any Compose file); external Open WebUI users migrate to generic
  external OIDC or an explicit retirement before deployment. No third-party
  submodule was edited.
- `api_service/main.py`: provider-conditional fastapi-users router mounting
  (`/api/v1/auth/*`, `/api/v1/auth/users/*`) deleted; no legacy
  login/register/reset route is mounted in any mode. Provider-specific
  (`google`) OIDC discovery fetch deleted; startup performs selector validation
  with no outbound DNS/connect attempt.
- `api_service/auth_providers.py`: `keycloak`/`default` branches of
  `get_auth_router()` deleted; helper returns an empty router. No permanent
  `default`/`keycloak` alias, dual token acceptance, or success-shaped
  deprecated stub remains.
- `tools/dev-keycloak.ps1` deleted; stale Keycloak TODO removed from
  `tools/dev-insecure.ps1`; `keycloak`/`keycloak-db` updater targets removed
  from `.agents/skills/update-moonmind/scripts/run-update-moonmind.sh`
  (the nonexistent `keycloak-db` service confirmed the missing reconciliation).
- Tests: `keycloak_mode` fixture renamed to `authenticated_mode` (`oidc`);
  `keycloak`/`default`/`local` AUTH_PROVIDER literals replaced with `oidc`/
  `accounts` production fixtures; taxonomy assertion now requires `oidc`;
  registration-flow test converted to a removal gate (legacy routes 404 +
  unmounted).

## 2. Retired selectors fail fast

`moonmind/config/settings.py::OIDCSettings.validate_auth_provider()` rejects
`keycloak`, `default`, `google`, `local` (and any unknown value) with migration
guidance at API startup via `_initialize_oidc_provider()`; values are never
silently translated. Supported modes: `accounts`, `oidc`, `header`, `disabled`.

## 3. Justified residuals (no active Keycloak dependency)

| Residual match | Reason |
| --- | --- |
| Retirement comments in `docker-compose.yaml`, `init_db_scripts/01-create-dbs.sh`, `run-update-moonmind.sh`, `settings.py`, `auth.py` | Temporary migration/recovery guidance, not behavior. |
| `RETIRED_AUTH_PROVIDERS` + rejection messages in `settings.py` | Negative-path contract: retired selectors must stay rejected. |
| `BearerTransport`/`JWTStrategy`/`FastAPIUsers` validation in `api_service/auth.py`, `current_active_user` in `auth_providers.py`, `get_jwt_strategy` in `websockets.py` | Retained until the #4124-era session contracts replace bearer validation (K4). No issuance route remains; `fastapi-users`/`PyJWT` stay as dependencies because the `User` model/migrations couple to the former and managed-session/fanout/proxy capabilities use JWT-secret-backed tokens. |
| `User.oidc_provider`/`oidc_subject` columns + `uq_oidc_identity`, Alembic revisions under `api_service/migrations/versions/` | Historical readers for upgrade/recovery (K3 owns the provider→issuer mapping; never rewritten here). |
| `oidc_provider="default"` marker for disabled-mode seeded rows in `auth.py` | Historical row marker for existing databases; identity migration is K3-owned, not rewritten here. |
| `410 worker_token_deprecated` tombstone in `worker_auth.py` | Retained-narrowly through cutover per inventory E22 (K5 keeps, later removal with callers). |
| Negative tests asserting retired literals (`test_auth_inventory_4117.py`, taxonomy, removal gate) | Required K5 evidence that old selectors/tokens/routes stay rejected. |
| `docs/Security/AuthenticationContracts.md` Keycloak mentions | Canonical durable contract describing retired selectors (desired state, not live behavior). |
| `docs/tmp/KeycloakRemovalPlan.md`, `docs/tmp/KeycloakRemovalInventory-4117.md` | #4102/#4117 execution scaffolding and pre-change baseline evidence; archived separately, not live behavior. |

## 4. Preserved (explicitly not deleted)

Existing `User`/profile rows and UUIDs, shared PostgreSQL/Temporal databases
and roles, unrelated credentials (Provider Profile OAuth, `GITHUB_TOKEN`/PATs,
`moonmind.auth` resolution, container-job bearers, Omnigent host auth,
`OMNIGENT_AUTH_*` runtime-server variables), historical Alembic chain, and the
portable Skill implementation in `run-update-moonmind.sh`.

## 5. Open gates for #4128 qualification

Replacement `accounts`/`oidc`/`header` session contracts (K2–K4) land
separately; this candidate proves clean boot/seeding in `disabled` mode, no
Keycloak DNS/connect attempt, rejected legacy selectors/tokens/routes, and
rendered Compose without the bundled service. Fresh/upgrade migration replay
against the retained chain is the qualification entry point.
