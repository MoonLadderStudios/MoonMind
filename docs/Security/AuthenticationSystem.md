# Authentication System

**Document Class:** Canonical declarative
**Viewpoint:** Module Architecture View
**Status:** Draft
**Owners:** MoonMind Engineering
**Updated:** 2026-09-08
**Audience:** Operators, dashboard/API contributors, and security reviewers
**Authority:** MoonMind control-plane user authentication, identity ownership, supported modes, session lifecycle, and user-versus-machine credential boundaries. Temporary Keycloak-removal execution guidance lives in [KeycloakRemovalPlan.md](../tmp/KeycloakRemovalPlan.md) and is not authoritative for shipped behavior.
**Owning Surface:** `AUTH_PROVIDER` selector, `get_current_user()` boundary, FastAPI Users session stack, default-user provisioning, and user-facing auth routes
**Related Docs:** [SecretsSystem.md](./SecretsSystem.md), [ProviderProfiles.md](./ProviderProfiles.md), [SettingsSystem.md](./SettingsSystem.md), [ModelContextProtocol.md](../ExternalAgents/ModelContextProtocol.md), [DockerBackendService.md](../ManagedAgents/DockerBackendService.md), [WorkflowArtifactSystemDesign.md](../Temporal/WorkflowArtifactSystemDesign.md), [CombinedStackValidationAndRollback.md](../Omnigent/CombinedStackValidationAndRollback.md), [KeycloakRemovalPlan.md](../tmp/KeycloakRemovalPlan.md)
**Related Implementation:** `moonmind/config/settings.py` (`OIDCSettings`), `api_service/auth.py`, `api_service/auth_providers.py`, `api_service/main.py` (auth routers, OIDC discovery init, default-user startup), `docker-compose.yaml`, `init_db_scripts/01-create-dbs.sh`

> [!NOTE]
> This document describes the **shipped** MoonMind control-plane authentication
> contract. It is the one canonical owner of target behavior for user login,
> identity, modes, and session lifecycle. Phased migration checklists and
> unexecuted cutover steps stay in issues or `docs/tmp/`.
>
> The `accounts` / `oidc` / `header` modes proposed in the temporary
> [KeycloakRemovalPlan.md](../tmp/KeycloakRemovalPlan.md) are **not shipped**.
> Do not configure them. They become authoritative only through the coordinated
> implementation they describe (#4120–#4129) and its product evidence (#4128).

---

## 1. Summary

MoonMind's control-plane user authentication is selected by one selector,
`AUTH_PROVIDER`, resolved in the API process against the MoonMind database.
`User.id` (a stable UUID) is the canonical principal carried by workflow
ownership, artifact authorization, profiles, and permissions.

Shipped values:

| `AUTH_PROVIDER` | Shipped behavior |
| --- | --- |
| `disabled` (default) | Local single-user mode. The API loads the persisted default user (`DEFAULT_USER_ID`, default `00000000-0000-0000-0000-000000000000`) and treats it as the local administrator. No browser login is required. |
| `keycloak` | Legacy opt-in. FastAPI Users auth routers are skipped and the deployment is expected to sit behind the optional `keycloak` Compose profile. Pending removal per the temporary plan; still live in this checkout. |

The literals `default` (an auth-router branch in `get_auth_router()`) and
`google` (an OIDC discovery branch in `_initialize_oidc_provider()`) exist in
code but are **not supported selectors**. They are inventory for the cutover,
not configuration options. Unknown or retired selectors must fail visibly at
startup with migration guidance once the coordinated implementation lands; until
then, only `disabled` and `keycloak` describe shipped behavior.

## 2. Non-goals

- This document does not change authentication behavior, defaults, routes, or
  infrastructure. It records what ships.
- It does not promote the proposed `accounts` default before implementation
  qualifies it.
- It does not replace the temporary removal plan, issue tracking (#4116 parent,
  #3940/#3941, #3939 coordination), or deployment-cutover/product evidence
  (#4128).

## 3. Identity ownership

- `User.id` remains MoonMind's canonical principal. Workflow ownership, foreign
  keys, profile relations, `is_active` / admin flags, and resource authorization
  stay MoonMind-owned. There is no second account database.
- In `disabled` mode the persisted default-user row is authoritative. Startup
  (`api_service/main.py`) gets or creates it; per-request resolution
  (`get_current_user()`) loads it from the database.
- `api_service/db/models.py` stores `oidc_provider` (32 characters) and
  `oidc_subject` with a unique pair constraint. That pair is retained identity
  data for migration and audit, not a complete issuer-URI identity model, and is
  not deleted as part of routine cleanup.
- Database or identity-store unavailability fails closed with a bounded
  unavailable response. Test-only stub principals belong in explicit test
  dependency overrides, never in the production path.

## 4. Current request path

```text
Browser / API client
  -> MoonMind API authentication boundary (get_current_user())
  -> FastAPI Users JWT bearer session (non-keycloak modes)
  -> MoonMind User lookup and current authorization checks
  -> existing route and resource authorization
```

- Non-`disabled` modes resolve through the FastAPI Users `current_active_user`
  dependency; `disabled` mode loads the default database user.
- Bearer transport targets `auth/jwt/login`; the JWT strategy uses
  `JWT_SECRET_KEY` with a 3600-second lifetime (`api_service/auth.py`).
- User-facing auth routes (`/api/v1/auth`, register, reset, verify, users) are
  mounted for every mode except `keycloak`
  (`API_AUTH_PREFIX = "/api/v1/auth"` in `api_service/main.py`).
- Ordinary authenticated API requests do not require a network call to Omnigent
  Server. OIDC discovery (`_initialize_oidc_provider()`) only runs for the
  inventoried `google` branch.

## 5. Credential boundaries

Control-plane user login is independent of every credential below. A user
session never substitutes for a machine credential and vice versa.

| Credential | Authority | Scope |
| --- | --- | --- |
| MoonMind user session (JWT bearer) | MoonMind `User.id` via `get_current_user()` | Browser/API clients on user routes, MCP, artifact APIs |
| Worker / container-job bearer (`MOONMIND_CONTAINER_JOBS_BEARER_TOKEN`) | Trusted session launcher, bounded session lifetime | `POST /mcp/container/tools/call` only; claims bind owner, runtime, agent run, session |
| Provider Profile OAuth / API keys | Provider Profiles + Secrets System | Model/provider access at launch boundaries; never a user-login credential |
| GitHub / runtime / worker credentials | Owning integration or runtime | Repository, provider, or execution scope only |
| Omnigent runtime auth (`OMNIGENT_AUTH_*`, `OMNIGENT_HOST_AUTH_*`) | Omnigent Server / host | Runtime-server sessions and host tunnels, not MoonMind control-plane login |
| Signing keys (`JWT_SECRET_KEY`, cookie secrets) | Deployment operator | Never shared with users; never repurposed provider tokens |

Native Workflow Chat stays behind the existing same-origin, binding-scoped
facade. A valid MoonMind login does not grant unrestricted upstream host access;
service credentials stay server-side and browser cookies/bearer headers are not
blindly forwarded upstream.

### 5.1 Shipped transport, expiry, and error behavior

Applies to the shipped `disabled` / `keycloak` contract only. The proposed
`accounts` / `oidc` / `header` modes carry no transport, cookie, or error
contract until their implementation qualifies.

- User sessions travel in the `Authorization: Bearer` header
  (`BearerTransport(tokenUrl="auth/jwt/login")` in `api_service/auth.py`).
  Shipped auth code sets no session cookies, so there is no cookie/CSRF
  contract on this boundary.
- User JWT lifetime is 3600 seconds
  (`get_jwt_strategy()` in `api_service/auth.py`, `JWT_SECRET_KEY`-signed).
- Outside `disabled` mode, requests resolve through the FastAPI Users
  `current_active_user` dependency (`api_service/auth_providers.py`):
  a missing or invalid bearer is rejected without reaching route logic.
- In `disabled` mode, an unparseable `DEFAULT_USER_ID` fails with
  `500 "Invalid DEFAULT_USER_ID"` and a missing default-user row fails with
  `500 "Default user not found"` (`get_default_user_from_db` in
  `api_service/auth_providers.py`). A transient database lookup failure on
  the request path logs a warning and falls back to the configured
  default-user stub; it never promotes another principal.
- Worker-token endpoints use `get_current_user_optional()` so header-only
  worker credentials are not blocked by the strict bearer dependency
  first (`api_service/auth_providers.py`).

## 6. Fresh install and existing data

- Fresh local install: `docker compose up -d` with no mandatory `.env`. The
  default `AUTH_PROVIDER=disabled` boots a single-user deployment; complete
  operator setup in the dashboard (Settings → provider secrets and profiles)
  before submitting work. See `README.md` Quick Start.
- Existing databases with omitted authentication settings keep the shipped
  `disabled` default. There is no silent promotion to a new mode and no
  automatic owner creation on populated databases; first-owner claim on existing
  data requires an explicit, controlled migration operation when the replacement
  ships.
- `JWT_SECRET_KEY` defaults to a placeholder (`replace_with_a_strong_random_jwt_secret`
  in `.env-template`). Production deployments must set a strong value; sharing
  signing keys across deployments or users is not supported.
- Recovery never disables authentication and never deletes shared volumes.
  `docker compose down -v` is not an auth-recovery step. Restoring the entire
  shared PostgreSQL/Temporal database is not an auth rollback; identity recovery
  uses encrypted identity backups with version matching, freeze/reconciliation,
  and recovery-snapshot retention as defined by the deployment owner's cutover
  tooling (temporary plan §6, K6).

## 7. Keycloak status

Keycloak is legacy and pending removal, but still live in this checkout:

- `docker-compose.yaml` keeps the `keycloak` service behind the `keycloak`
  profile with its realm-export mount; `OIDC_ISSUER_URL` still defaults to the
  Keycloak realm URL and `init_db_scripts/01-create-dbs.sh` still provisions the
  Keycloak database/role.
- These references describe live topology, not stale docs. Remove them only in
  the coordinated cutover change (temporary plan K5) after K2–K4 qualify the
  replacement — not as a docs-only edit.
- Active guidance outside this section must not present Keycloak setup, realm
  URLs, or selector aliases as the recommended path. Retained historical
  migration notes, negative tests, and cutover fixtures keep an explicit
  historical/negative-test classification.

## 8. Supported, legacy, and unshipped combinations

| Combination | Status |
| --- | --- |
| `AUTH_PROVIDER=disabled`, local Compose | Supported and tested fresh-install path |
| `AUTH_PROVIDER=keycloak` with `keycloak` profile | Legacy opt-in; supported only until the coordinated cutover retires it |
| `accounts` / `oidc` / `header` | Proposed in the temporary plan; **blocked / unqualified** — not accepted by this checkout |
| `default`, `google` literals in code | Unsupported internal branches; do not configure |
| Upstream contract/pin compatibility | Governed by the pinned Omnigent revision and the reusable upstream pin policy; recheck `omnigent/server/auth.py` contracts if the pin moves |

Product evidence for the replacement journey belongs to #4128 and the
deployment-cutover issue; this document must not invent a successful result.
The temporary [KeycloakRemovalPlan.md](../tmp/KeycloakRemovalPlan.md) is
removed or archived only once execution is complete, with durable contracts
retained here and backlinks resolved. Necessary operator upgrade guidance is not
erased prematurely. Backlink audit 2026-09-08: this document is the only
canonical-doc reference to the temporary plan, held as an explicit
non-authoritative pointer (header Authority field); no other canonical doc treats
the plan as authoritative for shipped behavior.

## 9. Verification

Shipped-behavior checks for this document:

- `AUTH_PROVIDER` default is `disabled`; accepted shipped values are `disabled`
  and `keycloak` (`moonmind/config/settings.py`, `api_service/auth_providers.py`).
- Default user ID `00000000-0000-0000-0000-000000000000` and JWT lifetime
  3600 s (`api_service/auth.py`).
- Auth routes mounted under `/api/v1/auth` for non-`keycloak` modes
  (`api_service/main.py`).
- Keycloak service remains behind the `keycloak` Compose profile
  (`docker-compose.yaml`); Keycloak DB provisioning remains in
  `init_db_scripts/01-create-dbs.sh`.
- `tools/export_openapi.py` carries no Keycloak setup, realm-URL, or
  unshipped-mode claims (serializes FastAPI routes only); `moonmind/cli.py`
  exposes no auth selectors and `moonmind/container_job_cli.py` sends only the
  worker bearer machine credential documented in §5.
- `tests/unit/docs/test_authentication_system_docs.py` pins these contracts so
  future implementation PRs update the doc and the test together.
