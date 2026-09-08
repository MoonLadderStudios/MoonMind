# Keycloak Removal Plan

Status: Proposed implementation plan. No authentication behavior or deployed infrastructure is changed by this document.

Prepared: 2026-09-07.

Reviewed baselines: MoonMind `8ff5144bd03c8f006621eabc982e12ebac944a30` and its pinned Omnigent submodule `f04b0354fb5344c1ea8b92795ceb6760a9ad7595`.

This is temporary execution guidance under `docs/tmp/`, following [AGENTS.md](../../AGENTS.md). When implementation is complete, retain the accepted authentication contracts in canonical security documentation and archive or remove this plan.

## 1. Recommended decision

Remove MoonMind's bundled Keycloak integration and replace its user-authentication responsibilities with qualified, reusable Omnigent authentication capabilities composed inside the existing MoonMind API process.

Use a thin MoonMind adapter for configuration, identity resolution, persistence, and application policy. Keep the MoonMind user database, stable user UUIDs, profiles, active/admin flags, workflow ownership, artifact authorization, and administrative permissions authoritative. Do not adopt a second upstream account database or copy the upstream authentication implementation into MoonMind.

The intended request path is:

```text
Browser / API client
  -> MoonMind API authentication boundary
     -> qualified Omnigent authentication capability
     -> MoonMind identity mapping and current User lookup
  -> existing MoonMind route and resource authorization
```

This is authentication-library reuse, not embedding the entire Omnigent application, resurrecting the retired embedded host transport, or moving MoonMind authorization into the runtime server. Ordinary authenticated API requests must not require a network call to Omnigent Server. OIDC login can still contact the operator's selected identity provider.

Built-in accounts are the proposed default for new installations. Generic OIDC and trusted-proxy authentication are advanced alternatives. Explicit local single-user mode remains available under restricted network exposure. Removing Keycloak must never mean silently turning authentication off.

This is not a configuration-only substitution. The pinned upstream code supplies useful primitives, but identity, persistence, session isolation, and revocation need qualification and some portable extension work before it can safely replace MoonMind's authentication boundary.

## 2. What the current source establishes

The following is a source-code baseline, not a claim about the user's deployed configuration, database contents, or actual use of Keycloak.

| Surface | Verified behavior and consequence |
| --- | --- |
| `api_service/auth_providers.py` | `get_current_user()` selects FastAPI Users for every non-disabled mode. Disabled mode can return an administrator stub after a database failure. `get_auth_router()` has a Keycloak placeholder and a separate `default` route branch. Replace the actual mounted and dependency paths together, not just this helper. |
| `api_service/auth.py` | Owns UUID-backed user schemas, JWT authentication, default-user creation, and profile hooks. The default local UUID is `00000000-0000-0000-0000-000000000000`. Preserve persisted UUIDs and profile relationships. |
| `api_service/main.py` and `moonmind/config/settings.py` | Contain provider-dependent startup or route behavior. Search results also expose a `google` discovery branch, while the setting description names disabled/Keycloak. Inventory every accepted literal and mounted route before choosing the final cutover. |
| `api_service/db/models.py` | Stores `oidc_provider` as a 32-character string and `oidc_subject`, with a unique pair constraint. This is not automatically a complete issuer-URI identity model. These fields are not disposable merely because Keycloak is removed. |
| `docker-compose.yaml` | Keycloak is already behind a `keycloak` profile. Removal eliminates optional topology and its maintenance burden, but does not necessarily reduce the default running-container count. |
| `init_db_scripts/01-create-dbs.sh` | Includes Keycloak database/role creation alongside other services. Remove only Keycloak-owned initialization. |
| `.agents/skills/update-moonmind/scripts/run-update-moonmind.sh` | Maps `keycloak/*` changes to `keycloak` and `keycloak-db` targets. Reconcile these names with actual Compose services rather than assuming both are deployed. |
| `moonmind/workflows/temporal/artifacts.py` | Uses the disabled-auth selector to bypass an authorization path. New authenticated modes must not inherit that bypass. |
| `api_service/api/routers/worker_auth.py` | Uses an optional user dependency so worker credentials can be evaluated. A browser-only replacement must not break this separate authority path. |
| `.env-template` | Already exposes `OMNIGENT_AUTH_*`, accounts, OIDC, and browser-origin settings for the runtime server. Do not accidentally reinterpret these as MoonMind control-plane configuration. |

Additional confirmed cleanup/test surfaces include `tools/dev-keycloak.ps1`, Keycloak comments in `tools/dev-insecure.ps1`, `tests/conftest.py`, `tests/integration/temporal/test_temporal_artifact_authorization.py`, `tests/integration/temporal/test_task_shaped_submission_normalization.py`, `tests/unit/workflows/temporal/test_artifacts.py`, and `tests/unit/test_integration_test_taxonomy.py`. The last file includes an assertion on the literal `keycloak`, so test-selection and taxonomy checks need updates as well as fixtures.

### Important upstream constraints

At the pinned revision, `omnigent/server/auth.py` provides `UnifiedAuthProvider` for accounts, OIDC, and trusted headers. It accepts an HTTP connection, including WebSocket handshakes, and handles session cookies or bearer tokens. Its behavior is not automatically the MoonMind contract:

- The accounts router mints its session subject from a normalized username. The OIDC router mints it from a lowercased email. A wrapper around the final subject string cannot recover the original OIDC issuer and subject.
- The accounts router imports `SqlAlchemyAccountStore`, calls synchronous store methods, and uses upstream administrator-roster behavior. MoonMind uses its own database and asynchronous access patterns.
- Ordinary credential-cache entries retain the resolved subject until token expiry. Current MoonMind user status and permissions still need authoritative checks.
- Grant-token revocation is supplied through an optional hook. Delegated-token path restrictions name Omnigent endpoints, not MoonMind's API.
- Upstream login can return the session token in JSON and can issue refresh material when requested and configured. Do not expose that entire response contract to the MoonMind browser by default.

These observations are integration constraints, not a complete security audit of Omnigent.

## 3. Target contracts

### Identity and account ownership

`User.id` remains MoonMind's canonical principal and the identifier persisted in workflow ownership and foreign keys. A valid external identity resolves to exactly one existing or explicitly provisioned MoonMind UUID before a MoonMind session is issued.

For OIDC, map the verified `(issuer, subject)` pair. Do not automatically link by email, display name, username, or a matching subject from a different issuer. Preserve the case-sensitive subject. Email changes must not change resource ownership. The OpenID Connect claim-stability rules explicitly distinguish issuer/subject identity from mutable email attributes [S1].

For built-in accounts, resolve the login name to an existing User record and mint sessions for its UUID, not for the login name. For trusted headers, use an operator-configured identity namespace and a stable proxy-asserted identifier. Email-only proxy integrations require explicit enrollment and a documented reassignment policy, not automatic account merging.

Prefer extending the existing identity representation where sufficient. If its single external-identity pair cannot support the required mapping, replace it with one external-identity relation referencing `User.id`. Do not retain two competing authoritative mappings. A provider-to-issuer migration must be explicit, and the existing 32-character provider field must not truncate an issuer URI.

Account provisioning must preserve one profile per user. Existing users, disabled accounts, admin flags, and ownership records must not be recreated or reset as a side effect of first login.

### Authentication versus authorization

Authentication establishes who presented the request. MoonMind continues to decide whether that principal may see an execution, download an artifact, use a secret, change settings, launch work, or interact with a runtime binding.

Retain `get_current_user()` and the optional-user boundary as the main integration points where practical. Audit direct FastAPI Users dependencies and separately mounted routes so no stale entrypoint bypasses the new resolver. Always check current `User.is_active` and MoonMind authorization after credential validation, including cache hits.

Keep Provider Profile OAuth, GitHub credentials, `moonmind.auth` profile/environment credential resolution, worker tokens, container-job credentials, and Omnigent host-auth lifecycles separate. They are not Keycloak cleanup targets. A delegated worker must not receive a full browser-user session to make an endpoint work.

Native Workflow Chat remains behind the existing same-origin, binding-scoped facade. A valid MoonMind login does not grant unrestricted access to every upstream session or host. Service credentials stay server-side, and browser authentication cookies or bearer headers are not blindly forwarded upstream.

### Configuration and defaults

Keep one MoonMind authentication-mode selector, using `AUTH_PROVIDER` rather than adding a competing enable switch. The proposed final values are:

| Mode | Intended use |
| --- | --- |
| `accounts` | Default for a new installation, with protected first-owner setup and invite-only enrollment. No Keycloak or mandatory SMTP service. |
| `oidc` | Advanced external identity-provider integration through a qualified generic OIDC flow. |
| `header` | Advanced authenticated ingress. Only a trusted proxy may assert identity. |
| `disabled` | Explicit local single-user mode with a stable persisted user and restricted ingress. Never an error fallback. |

Pass resolved control-plane settings explicitly to the reused capability. Runtime-server `OMNIGENT_AUTH_*` variables must not select MoonMind's mode, key, or identity store by import-time side effect. Keep control-plane and runtime cookie names, signing keys, token purpose, and persistence separate.

Reject unknown or retired selectors at startup with migration guidance. Do not silently translate `keycloak` to `disabled`, interpret `default` as an undocumented alias, or leave a `google` branch outside the final mode contract. The migration tool records the chosen target before deployment.

A new installation should still start with `docker compose up -d` and no mandatory `.env`. Secure onboarding replaces the unauthenticated first-run experience. Existing installations with omitted authentication settings need a versioned migration decision, not an unannounced default change that creates a new owner. Explicit local mode must validate published host ports and trusted ingress, not merely the container's internal listen address.

### Session and browser security

Use a MoonMind-specific session cookie with HttpOnly, appropriate SameSite behavior, and Secure on HTTPS. Use a distinct non-`__Host-` development cookie only for explicitly permitted loopback HTTP. Do not weaken the production cookie to make local development work.

Define token issuer, audience or equivalent application-purpose binding, supported algorithms, expiry, and key rotation. Reject Omnigent runtime tokens at the MoonMind user boundary. Reuse one durable session/revocation mechanism in the existing database, behind the portable interface. Logout must invalidate the current session, and password reset, account disablement, and administrative revocation must invalidate the relevant credentials across API replicas. Clearing a cookie alone is not that guarantee.

Cookie-authenticated mutations require CSRF protection and origin validation. Check WebSocket origins, authorize reconnects, and revoke active browser streams within a documented bounded interval. SSE and artifact downloads must work without putting broad tokens in URLs. A missing credential may be optional at a worker boundary, but an invalid presented credential must not silently become a different principal. Reject conflicting cookie/bearer identities and define precedence once.

Use authorization-code flow with PKCE, state/nonce protections, verified issuer/audience/signature/time claims, and exact configured redirect destinations. Reject open redirects [S1, S2]. Browser login must not return refresh credentials or reusable session tokens in JSON merely because upstream offers a CLI mode. Keep any required CLI exchange separately scoped and explicitly authorized.

Do not accept user-controlled identity headers on direct API connections. The trusted ingress must strip and replace them, and direct bypass paths must be blocked. Do not let a missing header resolve to the reserved upstream `local` or `__public__` identities in authenticated deployments.

## 4. Implementation packages and ordering

The package identifiers below are planning identifiers, not existing GitHub issues. Each package should become a bounded implementation PR or a small issue group. They are not separate permanent runtime modes.

### K1. Freeze the inventory and acceptance contract

Trace actual authentication startup, route mounting, dependencies, frontend requests, WebSocket/SSE entrypoints, CLI consumers, Compose profiles, database initialization, deployment automation, and optional Open WebUI integration. Search all `Keycloak`, `keycloak`, `KEYCLOAK`, provider literals, realm URLs, JWT settings, and related client configuration. Classify every match as live behavior, test coverage, unrelated provider authentication, or historical evidence.

The deployment owner inventories users, issuer/subject mappings, service accounts, active sessions, MFA/SSO requirements, and other applications using the same realm. A repository search cannot establish those facts. Capture a sanitized baseline of successful authentication and owner/non-owner authorization before changing it.

**Exit evidence:** complete owned-surface inventory, selected target modes per deployment, current route/credential contract, and a regression test for each authority boundary. No shared Keycloak consumer may be silently abandoned.

### K2. Qualify the reusable upstream authentication boundary

Build a minimal real authentication slice using the pinned upstream code and MoonMind's database contract. First prove whether supported entrypoints can supply the required seams without private monkey-patches or copied protocol logic.

Where they cannot, make narrowly scoped, generally useful upstream changes: an identity-resolution hook receiving validated OIDC claims before session issuance, store interfaces appropriate for MoonMind's persistence, and explicit session-purpose/revocation configuration. Keep upstream usable independently. Do not initialize its complete application or permission/account tables inside MoonMind.

The identity hook must map to the MoonMind UUID before minting a session. Merely wrapping the pinned email-valued `get_user_id()` result is insufficient. The account adapter must use existing hashes through a qualified password-verification interface or require enrollment/reset, not silently assume hash compatibility. Avoid synchronous database calls blocking the API event loop.

Test token-cache behavior, revoked/unknown grants, accounts/OIDC/header mode validation, first-owner races, and the isolation of MoonMind versus runtime credentials. Disable unsupported delegated/refresh flows at the boundary rather than broadening upstream's endpoint allowlist or omitting revocation checks.

**Exit evidence:** exact upstream commit and package/image provenance, passing adapter conformance tests, and a written supported-interface contract. If a needed portable change is unavailable, keep the current deployed authentication and report the blocker. Do not ship an unsafe replacement to meet a removal date.

### K3. Implement identity migration, configuration, and account lifecycle

Add the minimal additive schema changes and an idempotent dry-run/apply migration command. Use deployment-supplied issuer mappings and verified enrollment evidence. Report collisions, missing mappings, duplicate emails, orphaned profiles, and mismatched default-user IDs without printing credentials.

Preserve every retained `User.id` and resource owner. Migrate the local default account to a protected owner account only through explicit operator claim. Close first-owner setup on an existing populated database unless the controlled migration grants that operation. First-owner creation and single-use invitation redemption must be transactional and race-safe.

Provide setup, invitation, password change/recovery, account disablement, and last-admin protection. Reuse MoonMind's admin authority rather than importing an additive upstream admin roster that could re-promote a demoted operator. No hardcoded default password. No public unauthenticated first-claim endpoint on a network-exposed installation. Use an operator-held, expiring, one-use bootstrap capability or a local administrative setup operation.

Treat passwords as a separate migration problem. Keycloak credentials or MFA enrollments are not presumed portable. Require a tested compatible hash path or a controlled invitation/reset or new-IdP enrollment. Deployments requiring MFA must retain it through an appropriate replacement identity provider or another qualified mechanism before cutover.

**Exit evidence:** real-PostgreSQL migration and rerun tests, unchanged UUID/foreign-key ownership assertions, protected bootstrap/invitation race tests, and tested recovery for the last administrator.

### K4. Cut the API and dashboard over as one vertical slice

Wire the qualified capability into the actual API lifecycle and current-user dependencies. Update direct JWT/FastAPI Users consumers and auth routes together. Preserve the needed User/profile API contracts even if their old authentication implementation is replaced. Do not leave a second registration, reset, or token-issuance path unintentionally mounted.

Implement the dashboard login/setup/logout experience, login return to a safe workflow deep link, expired-session handling, account status display, and CSRF-aware requests. Test HTTP, WebSockets, SSE, artifact preview/download, settings, secrets, workflow creation, and native Workflow Chat through their production entrypoints.

Keep worker and managed-session authorization working without browser cookies. Specifically qualify MCP/container jobs, `MOONMIND_CONTAINER_JOBS_BEARER_TOKEN`, artifact worker operations, bridge service authentication, and scoped callbacks. Browser logout must not revoke independent worker credentials or cancel admitted workflows. Account disablement must block new user actions and follow an explicit policy for already-admitted background work, not transfer its ownership.

Remove production test-stub selection from the authenticated path. Database or identity-store unavailability returns a bounded unavailable response, not an administrator. Test-only principals belong in explicit test dependency overrides.

**Exit evidence:** two-user browser journey plus admin/worker negative matrix, old-token rejection, correct error behavior, stream expiry/revocation, and restart/replica consistency. The vertical slice must pass with Keycloak unreachable.

### K5. Remove obsolete integration surfaces in the cutover change

After K2-K4 pass, remove the bundled Keycloak service/profile and Keycloak-only initialization, configuration, realm assets, launch helpers, health checks, updater targets, and documentation. Inventory dependencies before deleting packages, images, volumes, or scripts.

Delete superseded `keycloak` and `default` authentication branches and obsolete provider-specific discovery/mounting behavior. Migrate any supported `google` behavior to the declared generic OIDC path. Remove old token issuance and acceptance when the replacement is enabled for release. Do not retain dual token acceptance or aliases indefinitely.

Replace Keycloak-named fixtures with authenticated-mode coverage that actually exercises the new production boundary. Update the artifact authorization, submission normalization, and taxonomy checks identified above. Ensure test-impact selection runs these tests on auth, frontend transport, Compose, and migration changes.

Retain historical Alembic revisions needed to upgrade existing databases. Keep generic identity data needed for migration and audit. Do not delete the User table, profile data, shared PostgreSQL roles, Temporal databases, Provider Profile OAuth, or runtime host authentication.

Update the current auth/config documentation, README onboarding, `.env-template`, MCP/container-job guidance, artifact authorization documentation, and combined-stack operator guidance. Place durable target semantics in canonical security documentation. Explain old-config rejection and recovery, not just fresh installation.

**Exit evidence:** a reviewed removal manifest, clean startup and authenticated journeys without Keycloak, and no live Keycloak dependency. Remaining text matches must have an explicit historical or negative-test reason. An unreviewed blanket grep deletion is not acceptable.

### K6. Rehearse and execute the deployment cutover

Rehearse fresh installation, explicit local-mode upgrade, built-in-account upgrade, and Keycloak-to-target migration against sanitized realistic data. Verify the exact built artifacts and deployment topology, not only source-level mocks.

The operator takes encrypted, restore-tested backups of the relevant identity/configuration data and records matching code/image versions. Freeze account creation and identity/admin mutations during the bounded mapping/cutover window. Drain or account for old authentication requests and prevent old/new API replicas from issuing incompatible credentials concurrently.

Run migration preflight and apply, deploy the coordinated API/dashboard/configuration change, invalidate old application sessions, and prove both an operator and a non-admin can log in. Verify pre-existing workflows, artifacts, schedules, settings, and profile ownership before reopening normal access. Existing Temporal executions retain their stored principal IDs and durable authority.

Only then stop the obsolete Keycloak service and remove its exposed routes and deployment references. Do not use `docker compose down -v` or delete a shared database to retire one service. Other realm consumers must already be migrated. Retain an encrypted recovery snapshot for an explicitly bounded period, not a publicly reachable legacy login service.

**Exit evidence:** recorded smoke-test results, ownership reconciliation, old-credential rejection, no Keycloak network requirement, and operator-approved completion of the recovery retention window.

Dependencies: `K1 -> K2 -> K3 -> K4 -> K5 -> K6`. Test design starts in K1 and grows with every package. Configuration, API, dashboard, and token-format changes form one coordinated release boundary even if preparation is split across PRs.

## 5. Required verification matrix

Use the repository's unit and hermetic integration runners. Required CI must not depend on a real external identity provider or provider credentials. Use a local OIDC fixture, a trusted-proxy fixture, and real PostgreSQL for the relevant concurrency/migration seams. Keep live-provider and deployment qualification separate.

| Boundary | Required positive and negative evidence |
| --- | --- |
| Authentication | Accounts/OIDC/header and explicitly selected local mode. Valid, missing, expired, wrong-key, wrong-issuer/purpose, reserved-identity, and conflicting cookie/bearer cases. Unknown modes fail startup. |
| Identity | Same email across issuers, email rename, changed login name, new subject with reused email, duplicate enrollment, concurrent first login, existing zero/default UUID, inactive user, demoted admin. No ownership reassignment. |
| Browser security | CSRF on unsafe methods including login/logout as applicable, invalid state/nonce, callback replay, unsafe return URL, spoofed proxy headers, direct-ingress bypass, cookie attributes, cross-origin WebSocket rejection. |
| Account lifecycle | Single first owner under concurrent setup, invite expiry/single use, password reset and session invalidation, last-admin recovery, no accidental public registration. |
| Resource authorization | User A cannot view/control user B's workflows, artifacts, chat bindings, secrets, schedules, or administrative settings. Existing administrator rules remain explicit. |
| Machine authority | Workers and MCP/container-job callers succeed only on their authorized routes and resources. Runtime/session/worker tokens cannot become unrestricted MoonMind browser credentials. |
| Durability | API restart and multiple replicas preserve session/revocation behavior. Database outage fails closed. Browser logout leaves admitted workflows intact. Retained histories remain readable without identity rewriting. |
| User journey | Login -> open an old workflow -> submit work -> stream logs -> reconnect -> native chat -> artifact access -> logout -> denied access. Repeat for a non-admin and verify cross-user denial. |
| Removal | Fresh Compose boot without Keycloak, migrated data without Keycloak, rejected legacy selectors/tokens, no unintended public ports, updater no longer targets deleted services. |

Capture redacted auth success/denial/unavailable events, mode, reason, and request correlation. Do not log passwords, authorization codes, bearer tokens, cookies, reset links, refresh material, or raw IdP token responses. Remove token-printing hooks in replaced authentication paths. No authentication material belongs in Temporal payloads, workflow artifacts, or runtime bridge evidence.

## 6. Rollback and failure handling

Prefer an additive schema phase so the last known-good application can still read the database until the cutover is accepted. Record the exact compatibility boundary before deployment. A rollback must restore a matching application/configuration/session-key set and intentionally invalidate incompatible sessions. Never fall back to disabled authentication.

Before restoring identity data, freeze identity writes and reconcile changes since the snapshot. If users or permissions have changed after cutover, a blind snapshot restore is not a safe rollback. Use a tested forward repair or an explicit reconciled restoration. Never restore the entire shared PostgreSQL/Temporal database merely to undo an authentication migration.

Keep workflow and artifact records intact. Restoring a previously vulnerable authentication mechanism is not an acceptable response to a security incident. In that case, fail closed and use the protected local administrator recovery path. Do not make legacy and replacement login endpoints simultaneously reachable as an improvised recovery mechanism.

## 7. Completion criteria

Keycloak removal is complete only when the replacement is qualified at the pinned upstream boundary, existing principals retain their data and permissions, all supported user and machine journeys pass, old credentials are rejected, and no live MoonMind behavior requires Keycloak.

The repository should contain one deliberate authentication path per selected mode, no second account database, no new always-on auth container, no remote Omnigent dependency for ordinary user resolution, and no unsupported auth-disable fallback. Any retained historical identity information or migration files must have a documented purpose.

The largest implementation uncertainty is the portable upstream interface work, not deleting the Compose entry. User counts, deployed realm consumers, password/MFA portability, and exact migration duration are unknown until K1. This plan does not claim that the current Keycloak login path or the proposed replacement has been exercised in a deployed environment.

## Evidence references

MoonMind observations above refer to the named paths at [the inspected revision](https://github.com/MoonLadderStudios/MoonMind/tree/8ff5144bd03c8f006621eabc982e12ebac944a30). Important upstream implementation references are [UnifiedAuthProvider](https://github.com/omnigent-ai/omnigent/blob/f04b0354fb5344c1ea8b92795ceb6760a9ad7595/omnigent/server/auth.py), [accounts routes](https://github.com/omnigent-ai/omnigent/blob/f04b0354fb5344c1ea8b92795ceb6760a9ad7595/omnigent/server/routes/accounts_auth.py), and [OIDC routes](https://github.com/omnigent-ai/omnigent/blob/f04b0354fb5344c1ea8b92795ceb6760a9ad7595/omnigent/server/routes/auth.py). Recheck these contracts if the pin changes during implementation.

[S1] [OpenID Connect Core 1.0 incorporating errata set 2](https://openid.net/specs/openid-connect-core-1_0.html), especially ID Token validation and section 5.7, Claim Stability and Uniqueness.

[S2] [RFC 9700, Best Current Practice for OAuth 2.0 Security](https://www.rfc-editor.org/rfc/rfc9700.html), especially redirect validation and authorization-code flow protections.
