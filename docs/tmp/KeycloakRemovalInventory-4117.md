# Keycloak Removal Inventory — #4117

Status: Discovery and boundary decisions for MoonLadderStudios/MoonMind#4117
(parent epic #4116). Temporary execution scaffolding under `docs/tmp/` per
AGENTS.md. Durable desired-state contracts live in
`docs/Security/AuthenticationContracts.md`. This document changes no behavior,
removes no services, and authorizes no production account changes.

Revision traced: `main` at `984feb4f9` (assessment baseline). Predecessor
proposal `KeycloakRemovalPlan.md` (#4102) is starting evidence, re-traced here —
not the deliverable.

Target contract freeze: `accounts` (default) / `oidc` (generic) / `header`
(trusted proxy) / `disabled` (explicit restricted local). Canonical principal:
`User.id` UUID. No email-based implicit linking. Full freeze in
`docs/Security/AuthenticationContracts.md`.

---

## 1. Authentication entrypoint inventory (R1)

Constructor key: **S** = startup, **R** = mounted router, **D** = direct
FastAPI Users dependency, **J** = JWT issuance/validation, **C** = dashboard
request client, **T** = stream (SSE/WebSocket/download/chat), **W** = worker /
machine path. Disposition key: **convert** (move to the new boundary),
**retain** (unrelated, unchanged), **remove-with-callers** (delete together),
**retain-narrowly** (keep only for historical upgrade/recovery).

| # | Surface (file) | Public path / constructor | Credential | Principal resolver | Resource authorization owner | Side effect | Disposition + owner |
| --- | --- | --- | --- | --- | --- | --- | --- |
| E1 | `api_service/main.py` lifespan + `_initialize_oidc_provider` [S] | startup; `google` discovery branch fetches `{OIDC_ISSUER_URL}/.well-known/openid-configuration` → `app.state.jwks_uri` | none (outbound fetch) | n/a | n/a | provider-dependent startup | **convert** (generic `oidc` discovery; reject retired literals) — K2/K4 |
| E2 | `api_service/main.py` auth-router mounting [R] | `/api/v1/auth/*`, `/api/v1/auth/users/*`; skipped entirely when `AUTH_PROVIDER == "keycloak"` | JWT bearer / registration body | `fastapi_users` + `UserManager` | per-router `current_user` consumers | mounts login/register/reset/verify/users paths | **convert** (one vertical slice; no second registration/reset path left mounted) — K4 |
| E3 | `api_service/auth_providers.py::get_current_user()` [D] | dependency factory used by ~30 routers; non-`disabled` returns `current_active_user`; `disabled` loads default user from DB with stub fallbacks | bearer JWT (non-disabled) / DB row or stub (`disabled`) | FastAPI Users `current_active_user` vs default-user lookup | each consuming router | central integration point to preserve | **convert** (keep as main integration point behind new resolver) — K4 |
| E4 | `api_service/auth_providers.py::get_current_user_optional()` [D] | optional variant; non-`disabled` returns `current_active_user_optional`, else strict default user | optional bearer | same as E3, `optional=True` | worker-tolerant routers | missing credential stays optional here only | **convert** — K4 |
| E5 | `api_service/auth_providers.py::get_auth_router()` [R] | helper router: `keycloak` → empty placeholder; `default` → `/auth/jwt` + `/auth` register routes | same as E2 | `fastapi_users` | callers (legacy prefix) |archived branch surface | **remove-with-callers** (`keycloak` placeholder and `default` branch) — K5 |
| E6 | `api_service/auth.py` FastAPI Users core [J] | `BearerTransport(tokenUrl="auth/jwt/login")`, `JWTStrategy(secret=JWT_SECRET_KEY, 3600s)`, `UserManager` + profile hooks | email+password in, bearer JWT out | `UserManager` / `SQLAlchemyUserDatabase(User)` | `current_active_user` (active check) | issues tokens; creates profile async on register/login | **convert** (replace issuance/validation; keep one-profile-per-user; JWT secret/purpose rebinding) — K2/K4 |
| E7 | `api_service/auth.py::get_or_create_default_user()` [S] | startup seeding; reserved zero UUID `00000000-…-000000` default | `DEFAULT_USER_*` settings | direct `User` row create | n/a (bootstrap) | creates local owner row | **convert** (explicit operator claim; no silent owner creation) — K3 |
| E8 | `api_service/api/routers/profile.py` [R] | `GET/PUT /me` (`Profile` router, no extra prefix) via `get_current_user()` | bearer / default user | E3 | `ProfileService` | reads/writes own profile | **convert** (preserve contract, replace implementation) — K4 |
| E9 | `api_service/api/routers/worker_auth.py::_require_worker_auth()` [W] | `X-MoonMind-Worker-Token` header + `get_current_user_optional()` | OIDC principal only; legacy worker token → `410 worker_token_deprecated`; none → `401 auth_required` | OIDC user with non-null id | manifests router mutations | machine-authority gate | **convert** (keep OIDC-only; browser logout must not revoke worker creds) — K4 |
| E10 | `api_service/api/routers/manifests.py` [R/W] | user routes via `get_current_user()`; mutation route via `_require_worker_auth` | bearer or worker auth | E3 / E9 | manifest ownership | mixed human/machine surface | **convert** — K4 |
| E11 | `api_service/api/routers/temporal_artifacts.py` [R/T] | artifact endpoints via `get_current_user_optional()` | optional bearer | E4 | Temporal artifact owner checks | preview/download surface | **convert** (no URL-broad tokens; SSE/download re-auth) — K4 |
| E12 | `moonmind/workflows/temporal/artifacts.py` owner checks [D] | `_assert_read_access` / `_assert_mutation_access` / `_raw_access_allowed`: bypass when `AUTH_PROVIDER == "disabled"`; `principal required` when non-disabled and empty | principal string | owner principal vs `service:` prefix | artifact repository | authorization bypass in `disabled` | **convert** (new modes must not inherit bypass; authenticated fixtures) — K4 |
| E13 | `api_service/api/websockets.py::get_current_user_ws()` [T] | `/ws/v1/*` + `/terminal/{session_id}`; `?token=` query → `strategy.read_token(token, user_manager)` | bearer JWT in query | `JWTStrategy.read_token` | per-socket DB + owner checks | long-lived streams | **convert** (origin check, re-auth, bounded termination) — K4 |
| E14 | `api_service/api/routers/omnigent_bridge.py` + workflow-chat router [T] | bridge mount + `WORKFLOW_CHAT_BINDINGS_MOUNT_PATH`; native Workflow Chat behind same-origin binding-scoped facade | MoonMind login (binding-scoped) | E3 | binding scope | proxied upstream sessions | **convert** (login ≠ unrestricted upstream access; no cookie/bearer forwarding) — K4 |
| E15 | `api_service/api/routers/container_jobs.py` [W] | container-job routes via `get_current_user()` | bearer | E3 | job ownership | dispatches container work | **convert** (qualify without browser cookies) — K4 |
| E16 | `api_service/api/routers/retrieval_gateway.py:468` [W] | one optional-user route; rest strict | optional/strict bearer | E4 / E3 | retrieval ownership | mixed surface | **convert** — K4 |
| E17 | All other `get_current_user()` routers [R] | provider_profiles, secrets, sessions, agent_profiles, deployment_operations, settings, timelines, bootstrap, recurring/executions/catalog/mcp/console/jira/manifests/workflows/oauth_sessions/presets/automation/system/agent_runs/policies/migration/native_ui | bearer | E3 | per-router ownership | standard API | **convert** (audit for direct FastAPI Users deps bypassing E3) — K4 |
| E18 | `frontend/src/lib/api/client.ts::fetchApi` [C] | same-origin `fetch`, JSON headers, no auth header | cookie (same-origin) | browser session | n/a (client) | all dashboard requests | **convert** (login/setup/logout UX, CSRF-aware requests, return-to-deep-link, expired-session handling) — K4 |
| E19 | `moonmind/container_job_cli.py` + `MOONMIND_CONTAINER_JOBS_BEARER_TOKEN{,_FILE}` [W/C] | CLI/container file-based bearer | scoped bearer file | container-job minting (`runtime_environment.py`, `launcher.py`) | job scope | machine credential | **retain** (unrelated machine credential; qualify in K4 journey, do not delete) — K4 |
| E20 | `moonmind/auth/` (`AuthProviderManager`, `EnvAuthProvider`, `ProfileAuthProvider`) [D] | profile/environment credential resolution for provider credentials | env/profile secrets | provider resolution | secret refs | model-credential supply | **retain** (not application login) — no owner change |
| E21 | `api_service/api/routers/oauth_sessions.py`, provider-profile OAuth [R] | model Provider Profile OAuth flows | provider OAuth | provider oauth policy | profile ownership | model access | **retain** (not application login) — no owner change |
| E22 | `api_service/api/routers/worker_auth.py` legacy token constant | `X-MoonMind-Worker-Token` → `410` | removed | n/a | n/a | documents removal | **retain-narrowly** (keep the `410` tombstone through cutover, then remove with callers) — K5 |

Direct FastAPI Users dependencies outside E3/E4 (e.g. `websockets.py` importing
`get_jwt_strategy`/`get_user_manager`) must be audited in K4 so no stale
entrypoint bypasses the new resolver.

## 2. Reference classification (R2)

Live application-login behavior: `api_service/auth.py`,
`api_service/auth_providers.py`, `api_service/main.py` (E1/E2 branches),
`moonmind/config/settings.py` (`OIDCSettings`: `AUTH_PROVIDER`,
`OIDC_ISSUER_URL/CLIENT_ID/CLIENT_SECRET`, `DEFAULT_USER_*`,
`SecuritySettings.JWT_SECRET_KEY`), `api_service/db/models.py`
(`User.oidc_provider[32]/oidc_subject[255]`, `uq_oidc_identity`),
`api_service/api/websockets.py` token-in-query, `init_db_scripts/01-create-dbs.sh`
(keycloak role+db), `docker-compose.yaml` (`keycloak` service behind
`keycloak` profile + `OIDC_ISSUER_URL` default pointing at it + `./keycloak`
mounts), `keycloak/realm-export.json`, `tools/dev-keycloak.ps1`,
`tools/dev-insecure.ps1` Keycloak comments.

Test coverage (Keycloak-named, pre-cutover): `tests/conftest.py::keycloak_mode`
fixture, `tests/integration/temporal/test_temporal_artifact_authorization.py`,
`tests/integration/temporal/test_task_shaped_submission_normalization.py:658`,
`tests/integration/reliability/test_api_startup_provider_maintenance.py:52`,
`tests/unit/workflows/temporal/test_artifacts.py:1692`,
`tests/unit/api/routers/test_executions.py:10407,10454`,
`tests/unit/test_integration_test_taxonomy.py:326` (asserts literal `keycloak`
in the authz test). K5 replaces these with authenticated-mode coverage that
exercises the new production boundary; the taxonomy assertion is updated with
the fixtures.

Unrelated, retain unchanged: model Provider Profile OAuth (`oauth_sessions`
router, `is_codex_oauth_profile`, provider `auth_state` fields in
`provider_profiles.py`/`main.py` seed logic), repository credentials
(`GITHUB_TOKEN/PAT`, `ATLASSIAN_API_KEY` managed-secret sync in `main.py`),
`moonmind.auth` profile/env resolution, container-job bearer family,
Omnigent host-auth lifecycles, `OMNIGENT_AUTH_*` runtime-server variables in
`.env-template` (never reinterpreted as MoonMind control-plane config).

Historical evidence, retain narrowly: Alembic revisions under
`api_service/migrations/versions/` (needed to upgrade existing databases),
`init_db` shared roles, Temporal databases. Never deleted for Keycloak removal.

`keycloak-db` updater reconciliation: `.agents/skills/update-moonmind/scripts/run-update-moonmind.sh`
maps `keycloak/*` to targets `keycloak` + `keycloak-db`, but the Compose
inventory has one `keycloak` service and no `keycloak-db` service — the
Keycloak data lives in the shared `postgres` database (`01-create-dbs.sh`
creates role/database `keycloak` inside it). `keycloak-db` names no deployed
service; K5 removes both updater targets after verifying no other consumer
references `keycloak-db`.

## 3. Persisted-authority dispositions (R7, part 1)

| Authority | Disposition |
| --- | --- |
| `User.id` UUIDs + workflow/artifact/schedule ownership FKs | **convert** (preserve every retained id; migration is additive + idempotent dry-run/apply) — K3 |
| `User.oidc_provider/oidc_subject` (+ `uq_oidc_identity`) | **convert** (explicit provider→issuer mapping; replace with one external-identity relation if the 32-char field cannot hold issuer URIs; never two competing mappings) — K3 |
| `User` rows, profiles, `is_active`/`is_superuser` | **convert** (no recreate/reset on first login; server-owned admin, last-admin recovery) — K3 |
| Zero/default UUID row (`00000000-…-000000`) | **convert** (explicit operator claim to protected owner; closed first-owner on populated DB) — K3 |
| Password hashes / MFA enrollments / Keycloak credentials | **convert-via-reset** (separate problem; tested compatible-hash path or invitation/reset/new-IdP enrollment; MFA retained via qualified replacement before cutover) — K3 |
| Provider Profile OAuth data, managed secrets, container-job creds, host auth | **retain** — no change |
| Alembic history, shared PG roles, Temporal DBs | **retain-narrowly** (upgrade/recovery only) — K5 keeps |
| `keycloak` Compose service/profile, realm assets, keycloak init, launch helpers, health references, updater targets, Keycloak docs | **remove-with-callers** after K2–K4 pass (reviewed removal manifest; no `down -v` on shared DB) — K5 |
| `keycloak`/`default` auth branches, old token issuance/acceptance, `google` discovery branch | **remove-with-callers** (migrate `google` → generic `oidc`; no dual acceptance) — K5 |
| Keycloak-named test fixtures | **remove-with-callers**, replaced by authenticated-mode boundary coverage — K5 |

Concurrent work: coordinate with #4103 (removal) and #3939/#3940 without treating
adjacent scope as a prerequisite; this issue owns the inventory and contracts,
not the cutover itself.

## 4. Operator inventory procedure (R5, protected)

Deployment facts and secrets must never enter public issue bodies. The
deployment owner runs these steps in a protected channel and records only
pass/fail + unknowns (as explicit deployment gates) against the issue:

1. Enumerate deployed users: count, `User.id` list, `is_active`/`is_superuser`
   flags, and the `(issuer, subject)` or login-name mapping for each. Export
   stays operator-held (encrypted, restore-tested backup before any cutover).
2. List external identity pairs: Keycloak realm(s), issuer URIs, client IDs,
   and which MoonMind UUID each `(issuer, subject)` maps to; flag duplicate
   emails, orphaned profiles, and default/zero-UUID collisions for K3 preflight.
3. List realm consumers beyond MoonMind (other apps sharing the realm) with
   migration owners; no shared consumer is silently abandoned (K6 gate).
4. List service accounts, password/hash algorithms in use, MFA/SSO
   requirements, and active sessions/tokens outstanding.
5. Record ingress and replica topology: public host ports, trusted-proxy chain,
   TLS termination, API replica count, and whether `disabled` mode is exposed
   beyond loopback (must be restricted).
6. Unknown facts (user counts, realm consumers, hash portability, MFA path,
   migration duration) remain explicit deployment gates in K6. They do not block
   hermetic implementation (K1–K5), which proceeds on fixtures.

## 5. Sanitized pre-change baseline (R6)

Fixtures (no secrets; synthetic UUIDs/emails only):
`owner` (resource owner), `non-owner` (other active user), `admin`
(`is_superuser=True`), `zero` (default `00000000-…-000000` single-user
principal), `service:worker` (service prefix principal). Executable form:
`tests/unit/security/test_auth_inventory_4117.py`.

| Path | Current behavior (traced, pre-change) |
| --- | --- |
| `disabled` mode, artifact read/mutation | bypass: any principal (including `None`/empty) passes `_assert_*` |
| non-`disabled`, owner reads own artifact | pass |
| non-`disabled`, non-owner reads/mutates foreign artifact | `TemporalArtifactAuthorizationError` |
| non-`disabled`, empty principal on list path | `TemporalArtifactAuthorizationError("principal required")` |
| non-`disabled`, `service:*` principal | passes owner checks (service bypass) |
| worker auth, legacy `X-MoonMind-Worker-Token` presented | `410 worker_token_deprecated` |
| worker auth, valid OIDC user, non-disabled | resolves `auth_source="oidc"` |
| worker auth, no credential | `401 auth_required` |
| `get_auth_router()`, `AUTH_PROVIDER=keycloak` | empty router (Keycloak placeholder; login path unmounted) |
| startup `AUTH_PROVIDER=google` without reachable issuer | `RuntimeError` from discovery |
| DB outage on strict path | degraded/unavailable (must fail closed; no admin stub on production paths) |

Escaped-regression policy: any production escape becomes a minimized required-CI
fixture (unit where possible, `integration_ci` only for Docker/compose/DB seams
per the taxonomy); a broken behavior is never preserved as the target.

## 6. Plan-section and verification-matrix ownership (R7, part 2)

Plan sections (`KeycloakRemovalPlan.md`): §1 decision → K1 (this issue, frozen
above); §2 baseline → K1 (this inventory); §3 target contracts → K1 canonical
doc, implemented K2–K4; §4 packages K1–K6 → owners below; §5 verification matrix
→ row owners below; §6 rollback → K6; §7 completion → K6 sign-off.

| Package / matrix row | Owner (epic child) | Exit evidence |
| --- | --- | --- |
| K1 inventory + acceptance contract | #4117 (this issue) | this file + canonical contracts + baseline fixtures |
| K2 upstream boundary qualification | epic child (auth adapter) | pinned commit, adapter conformance tests, supported-interface contract |
| K3 migration + account lifecycle | epic child (identity/migration) | real-PG migration/rerun tests, UUID ownership assertions, bootstrap race tests |
| K4 vertical slice (API+dashboard) | epic child (cutover slice) | two-user journey + negative matrix, old-token rejection, stream revocation, replica consistency, Keycloak-unreachable pass |
| K5 removal manifest | #4103 (coordinate) | reviewed manifest, clean boot/journeys without Keycloak, no live dependency |
| K6 deployment cutover | deployment owner + epic child | smoke results, ownership reconciliation, old-credential rejection, recovery retention |
| Matrix: Authentication / Identity / Browser security / Account lifecycle | K2/K3/K4 per row | hermetic fixtures (local OIDC + trusted-proxy fixtures, real PG for concurrency seams) |
| Matrix: Resource authorization / Machine authority / Durability / User journey / Removal | K4/K5/K6 per row | negative matrix, restart/replica tests, journey replay, removal manifest |

No unowned deletion, migration, or security requirement remains: every inventoried
path (§1) and persisted authority (§3) names a disposition and an owner above.
