# Authentication Contracts

**Document Class:** Canonical declarative  
**Viewpoint:** Cross-Cutting Concept View  
**Status:** Predecessor account model; superseded target\
**Owners:** MoonMind Engineering  
**Audience:** API, dashboard, runtime, security contributors and operators  
**Authority:** Account-era authentication contracts and guidance for deployments that still use them. The [single-user application model](../MoonMindArchitecture.md#single-user-application-model) and [Single-User Application Design](../SingleUserApplicationDesign.md) govern the desired application architecture.\
**Owning Surface:** `api_service/auth.py`, `api_service/auth_providers.py`, `api_service/main.py`, `moonmind/config/settings.py` (`OIDCSettings` storage), `moonmind/security/auth_modes_4120.py` (selector owner, #4120), `moonmind/security/session_authority_4121.py` + `api_service/services/session_store.py` (session/revocation/CSRF owner, #4121), `moonmind/security/account_lifecycle_4122.py` (enrollment/administration rules, #4122) + `api_service/services/account_lifecycle_store_4122.py` + `MoonmindAccountNonce` (production User-table wiring and operated runbook, #4122), `moonmind/security/oidc_advanced_4124.py` + `trusted_proxy_4124.py` + `advanced_identity_4124.py` + `api_service/services/advanced_auth_service_4124.py` + `api_service/api/routers/auth_advanced_4124.py` (advanced identity owner, #4124), `api_service/db/models.py` (`User`)  
**Related Docs:** [SecretsSystem.md](./SecretsSystem.md), [ProviderProfiles.md](./ProviderProfiles.md), [SettingsSystem.md](./SettingsSystem.md), [KeycloakRemovalPlan.md](../tmp/KeycloakRemovalPlan.md) (predecessor proposal from #4102; starting evidence only, not the deliverable), [KeycloakRemovalResidual-4129.md](../tmp/KeycloakRemovalResidual-4129.md) (reviewed removal manifest for #4129)
**Related Tooling:** [`tools/keycloak_cutover_rehearsal.py`](../../tools/keycloak_cutover_rehearsal.py) (hermetic K6 preflight/rehearsal gate for #4131; never mutates a live deployment)

> [!IMPORTANT]
> The account-based application target below is superseded by the
> [Single-User Application Design](../SingleUserApplicationDesign.md). Its
> account selectors, enrollment, identity mappings, role checks, and human
> ownership are predecessor requirements, not features to finish or preserve in
> the target application. This includes the requirement to retain a default
> `User` and application-user profile plumbing.
>
> This document remains a reference for code and deployments that still contain
> the account mechanisms, subject to the qualification limits recorded below.
> Its use of "target" or normative account language refers to that predecessor
> design, not a parallel supported application architecture. Reading or merging
> the new design does not disable any currently enforced protection.
>
> Deployment admission, browser/origin protections, scoped machine authority,
> credential isolation, and preservation of data and in-flight work remain
> required. The single-user design replaces human identity requirements, not
> these boundaries. Exact machine contracts retain their owning authority.
> Operational cutover steps and evidence remain in issues, `docs/tmp/`, or
> run-local artifacts.

---

## 1. Summary

The predecessor account model defines one application authentication selector,
`AUTH_PROVIDER`, and one canonical principal, `User.id` (a stable MoonMind UUID).
Its modes are `accounts`, `oidc`, `header`, and an explicitly restricted local
mode `disabled`. Retired selectors (`keycloak`, `default`, `google`, `local`) are rejected
at startup with migration guidance; they are never silently translated.

Authentication establishes who presented a request. Authorization — whether that
principal may see an execution, download an artifact, use a secret, change
settings, launch work, or interact with a runtime binding — remains MoonMind's
decision, checked after credential validation on every request, including cache
hits.

## 2. Authentication modes

One selector, `AUTH_PROVIDER` (`moonmind/config/settings.py`, `OIDCSettings`).
No competing enable switch. Runtime-server `OMNIGENT_AUTH_*` variables never
select MoonMind's mode, key, or identity store.

| Mode | Intended use |
| --- | --- |
| `accounts` | Default for new installations. Built-in accounts with protected first-owner setup and invite-only enrollment. No Keycloak or mandatory SMTP service. |
| `oidc` | Advanced external identity-provider integration through a qualified generic OIDC flow (authorization code + PKCE). |
| `header` | Advanced authenticated ingress. Only a trusted proxy may assert identity; direct API connections never accept user-controlled identity headers. |
| `disabled` | Explicit local single-user mode with a stable persisted user and fail-closed restricted ingress (startup/deployment validator; loopback-only bind or documented trusted-ingress evidence; see inventory §5.3). Never an error fallback. |

Unknown or retired selectors fail startup with migration guidance. `keycloak`
must not translate to `disabled`, `default` is not an undocumented alias, and
any `google`-specific discovery branch migrates to the declared generic `oidc`
path.

A new installation starts with `docker compose up -d` and no mandatory `.env`.
Existing installations with omitted authentication settings receive a versioned
migration decision, not an unannounced default change that creates a new owner.

### Deployment exposure and existing access

`MOONMIND_API_PUBLISH_HOST` controls the operator-facing Compose binding and the
API startup exposure validator. `MOONMIND_API_HOST_PORT` controls the published
port. Both sides must consume the same resolved publish host. Fresh Compose
defaults publish on `127.0.0.1:7000`; a hostname resolving to a LAN or VPN address
is a separate access path and requires an explicit approved binding.

In `disabled` mode, a non-loopback binding requires
`MOONMIND_TRUSTED_INGRESS=1` and an operator-approved restricted ingress boundary.
This setting records trust; it does not create network restrictions or user
authentication. A specific interface binding does not imply publication on
other interfaces or localhost. Wildcard publication requires authorization for
all of the interfaces it exposes.

Existing dashboard/API URLs, publish bindings, authentication selection, and
trusted-ingress settings are deployment-owned configuration. Updates preserve
them, including when fresh-install defaults change. A security-driven cutover
provides a verified replacement access path before retiring the old one; a
healthy API reachable only inside its container does not satisfy this contract.
The deployment update verification owner is
[Docker Compose Update System](../Steps/DockerComposeUpdateSystem.md#12-verification-model).

## 3. Identity contract

- `User.id` is the canonical principal and the identifier persisted in workflow
  ownership and foreign keys. A valid external identity resolves to exactly one
  existing or explicitly provisioned MoonMind UUID before a MoonMind session is
  issued.
- OIDC maps the verified `(issuer, subject)` pair. Issuer is the full issuer URI
  (never truncated into the legacy 32-character provider field); subject is
  matched case-sensitively. No automatic linking by email, display name,
  username, or a matching subject from a different issuer. Email changes never
  change resource ownership.
- Built-in accounts resolve the login name to an existing `User` record and mint
  sessions for its UUID, not for the login name.
- Trusted-header mode uses an operator-configured identity namespace plus a
  stable proxy-asserted identifier. The ingress strips and replaces identity
  headers; direct bypass paths are blocked. A missing header never resolves to a
  reserved identity. Email-only proxy integrations require explicit enrollment
  and a documented reassignment policy, not automatic account merging.
- Prefer extending the existing identity representation where sufficient. If its
  single external-identity pair cannot support the required mapping, replace it
  with one external-identity relation referencing `User.id`. Never retain two
  competing authoritative mappings. Provider-to-issuer migration is explicit.
- Account provisioning preserves one profile per user. Existing users, disabled
  accounts, admin flags, and ownership records are never recreated or reset as a
  side effect of first login.

Authority for current status stays with MoonMind: `User.is_active` and
`User.is_superuser` are checked after credential validation. Provisioning never
imports an additive upstream admin roster that could re-promote a demoted
operator.

## 4. Credential handling: missing versus invalid

- A missing credential may be optional only at explicitly optional boundaries
  (worker-tolerant dependencies such as `get_current_user_optional()`). At
  strict boundaries a missing credential is `401 auth_required`.
- An invalid, expired, wrong-key, wrong-issuer, wrong-purpose, or
  reserved-identity credential is never silently mapped to a different
  principal. It is rejected (`401`), including on cache hits.
- Conflicting identities in one request (for example cookie and bearer resolving
  to different principals) are rejected (`401 auth_conflict`). Precedence is
  defined once by the implementation and documented with it; conflict is never
  resolved by silently preferring one side.
- Database or identity-store unavailability returns a bounded unavailable
  response (`503`) and fails closed. It never returns an administrator stub on a
  production path; test-only principals belong in explicit test dependency
  overrides.

## 5. Session, revocation, and stream contract

- MoonMind issues its own session (MoonMind-specific cookie; HttpOnly,
  appropriate SameSite, Secure on HTTPS; a distinct non-`__Host-` development
  cookie only for explicitly permitted loopback HTTP). Omnigent runtime tokens
  are rejected at the MoonMind user boundary.
- Tokens carry an explicit issuer, application-purpose binding (audience or
  equivalent), supported algorithms, expiry, and key rotation. Refresh material
  and reusable session tokens are never returned to the browser in JSON merely
  because an upstream capability offers a CLI mode; any CLI exchange is
  separately scoped and explicitly authorized.
- One durable session/revocation mechanism lives in the existing database behind
  a portable interface. Logout invalidates the current session. Password reset,
  account disablement, and administrative revocation invalidate the relevant
  credentials across API replicas within 5 minutes of the revocation commit
  (commit timestamp to final replica re-authorization check; clock starts at
  commit — see inventory §5.2). Clearing a cookie
  alone is not revocation.
- Browser logout does not revoke independent worker credentials and does not
  cancel already-admitted workflows. Account disablement blocks new user actions
  immediately; already-admitted background work keeps its stored principal ID
  and durable authority and follows the explicit per-deployment policy recorded
  in the inventory §5.1 (`drain_complete` by default versus `revoke`), never silent ownership
  transfer.
- Active browser streams (SSE, WebSocket reconnects, artifact downloads) are
  re-authorized and terminated within the same 5-minute bound after
  revocation (inventory §5.2). SSE and artifact downloads work without placing broad tokens in
  URLs.

## 6. Browser and transport security

- Cookie-authenticated mutations require CSRF protection and origin validation.
  WebSocket handshakes validate origin, authorize reconnects, and are revoked on
  the same 5-minute bound as other streams (inventory §5.2).
- OIDC login uses authorization-code flow with PKCE, state/nonce protections,
  verified issuer/audience/signature/time claims, and exact configured redirect
  destinations. Open redirects are rejected.
- The trusted ingress strips user-supplied identity headers and replaces them;
  direct-ingress bypass is blocked.

## 7. Enrollment and administration (server-owned)

- First-owner setup and invitation redemption are transactional and race-safe.
  Single first owner under concurrent setup; invites are expiring and one-use.
- No hardcoded default password and no public unauthenticated first-claim
  endpoint on a network-exposed installation. Bootstrap uses an operator-held,
  expiring, one-use capability or a local administrative setup operation, and is
  closed on an existing populated database unless the controlled migration
  grants it.
- Last-admin protection with a tested recovery path. Passwords, MFA
  enrollments, and Keycloak credentials are a separate migration problem: they
  require a tested compatible-hash path or controlled invitation/reset (or new
  IdP enrollment), never silent hash-compatibility assumptions. Deployments
  requiring MFA retain it through a qualified replacement before cutover.

Production wiring: the hermetic rules live in
`moonmind/security/account_lifecycle_4122.py` (capability mint/verify,
member-administration rules, §8 error mapping); the durable backing lives
in the existing database behind `api_service/services/account_lifecycle_store_4122.py`
(`AsyncDbLifecycleStore` over the `User` table plus the additive
`moonmind_account_nonces` single-use record, migration
`380_account_lifecycle_4122` — no second account database). The operated
fresh-install/recovery runbook is those transactional helpers:
`claim_first_owner_and_create_user` (claim plus owner creation plus profile
in one transaction), `redeem_invite_and_create_user` (redemption plus member
creation plus profile in one transaction), `redeem_recovery_for_user`
(one-use recovery without account creation or mutation),
`redeem_recovery_and_restore_access` (same-transaction nonce consumption
plus reactivation/promotion for a stranded administrator), and
`apply_member_action_transactional` (persisted administration with
last-admin protection; deactivation also revokes sessions). Member rows are
deactivated, never deleted: UUIDs and ownership records are preserved.

Request surface (`api_service/api/routers/accounts_4122.py`, prefix
`/api/v1/accounts`, only when the classified production mode is
`accounts`): `POST /setup` (operator-held bootstrap capability),
`POST /login` (password verify off the event loop, login resolved to
the existing `User` UUID, session minted through the shared #4121
authority), `POST /logout` (browser authority only — never cancels
admitted work or machine credentials), `GET /me`,
`POST /password/change` (bumps the durable revocation generation, caller
receives a fresh session), `POST /invites` (admin-only; membership
only, never administrator authority), `POST /enroll`,
`POST /recovery/request` (admin-only) and `POST /recovery/redeem`
(capability-gated, preserves active/admin flags, rotates credentials,
invalidates prior sessions, mints no session), `GET /members` and
`POST /members/action` (admin-only, last-admin safe). Unknown logins
fail exactly like wrong passwords (`401 auth_invalid`); incompatible
or missing hashes take the explicit reset path (`403
enrollment_required`), never silent hash conversion. Login, setup,
enrollment, and recovery redemption are rate-limited (`429
rate_limited`); JSON responses never carry session/refresh material
(sessions travel only as `HttpOnly` cookies) and refresh-shaped
request fields are rejected. A lost-acknowledgment enrollment retry
observes `409 email_taken` and continues via login — never a duplicate
profile.

Local operator recovery (`moonmind accounts mint-bootstrap |
mint-recovery | restore-access`): the tested path the last-admin
refusal points at. Capabilities are operator-held, expiring, one-use,
and login-bound; `restore-access` consumes the nonce and reactivates /
promotes the existing login in one transaction, preserving the UUID
and emitting only a redacted audit event.

Mode-specific limitations: accounts mode does not preserve an
existing deployment's MFA or federation features — such deployments
require a qualified replacement (typically their advanced IdP mode)
before cutover. There is no mandatory email service: invitation and
recovery capabilities travel over the authenticated operator channel,
never SMTP.

## 8. API error semantics

| Situation | Status | Code |
| --- | --- | --- |
| Missing credential at a strict boundary | `401` | `auth_required` |
| Invalid, expired, wrong-key/issuer/purpose, or reserved-identity credential | `401` | `auth_invalid` |
| Conflicting cookie/bearer identities | `401` | `auth_conflict` |
| Authenticated but inactive (`is_active=False`) or not authorized | `403` | `forbidden` / `inactive` |
| Removed legacy worker-token path presented | `410` | `worker_token_deprecated` |
| Identity/database unavailable | `503` | `unavailable` |

Machine callers (workers, MCP/container-job paths, bridge service, scoped
callbacks) succeed only on their authorized routes and resources. Runtime,
session, and worker tokens never become unrestricted MoonMind browser
credentials.

## 9. Explicitly unrelated systems (retain unchanged)

Provider Profile OAuth, repository credentials (GitHub tokens/PATs),
`moonmind.auth` profile/environment credential resolution, container-job
credentials (`MOONMIND_CONTAINER_JOBS_BEARER_TOKEN` family), Omnigent host-auth
lifecycles, and historical Alembic revisions needed to upgrade existing
databases are not application-login cleanup targets. Historical migrations stay;
the `User` table, profile data, shared PostgreSQL roles, Temporal databases,
and runtime host authentication are never deleted as part of Keycloak removal.

## 10. Observability

Record redacted authentication success/denial/unavailable events with mode,
reason, and request correlation. Never log passwords, authorization codes,
bearer tokens, cookies, reset links, refresh material, or raw IdP token
responses. No authentication material belongs in Temporal payloads, workflow
artifacts, or runtime bridge evidence.

## 11. Qualified upstream boundary (K2)

The reusable upstream authentication boundary qualified in
MoonLadderStudios/MoonMind#4118 is declared in
[OmnigentAuthAdapterContract.md](./OmnigentAuthAdapterContract.md): the
pinned upstream entrypoints MoonMind composes, the validated-identity
hook ordering before session minting, the MoonMind session-purpose
binding and durable revocation interface, the explicitly rejected
optional surfaces, and configuration isolation. That contract is the
authoritative adapter reference for K3/K4; this document remains the
authority for modes, identity, session, error, and background-work
semantics.

## 12. Support matrix and operator evidence

This section records the predecessor account design's qualification limits.
That design was **Draft**: the session, revocation, CSRF/origin, enrollment,
OIDC/proxy, and cutover semantics in §§5–8 and §§12.4–12.5 are backed
by hermetic implementation on this checkout (#4121 session authority,
#4122 account lifecycle, #4124 advanced identity, #4125 API cutover —
see the ledger), not by shipped and browser-verified product journeys.
Do not read them as proof that the journeys in §12.1 pass. §7
(enrollment and administration) is hermetic contract + implementation
(`account_lifecycle_4122` unit coverage) plus production User-table wiring
(`account_lifecycle_store_4122` with `MoonmindAccountNonce`, unit coverage
on SQLite and integration coverage on PostgreSQL), not yet browser-verified
operator procedure: end-to-end journeys await #4128
product qualification. Until that product evidence lands, the end-to-end
operator journeys remain contract text, not shipped proof.

Point-in-time execution status — the landed-ticket list, pending backlog
IDs, checkout-specific qualification results, and temporary plan lifecycle —
lives in [KeycloakRemovalStatus-4130.md](../tmp/KeycloakRemovalStatus-4130.md)
so this canonical section cannot go stale as issues land. This section keeps
only the durable support, cutover, and compatibility contracts.

### 12.1 Supported and tested versus blocked and unqualified

| Combination | Status | Evidence owner |
| --- | --- | --- |
| `disabled` clean boot and seeding, no Keycloak DNS/connect attempt, retired selectors/tokens/routes rejected, rendered Compose without the bundled service | Supported and tested (hermetic unit tests plus the residual manifest §5 open gates) | #4120 tests, #4129 manifest |
| Session authority, revocation/CSRF bounds, account lifecycle (first-owner setup, expiring one-use invites, last-admin protection, operator recovery), OIDC/proxy identity, and API user-resolution cutover (cookie/bearer precedence, conflict rejection, §8 error mapping, legacy JWT refusal) | Supported and tested hermetically (unit + Postgres integration, route enumeration); account lifecycle additionally wired to the production User table (unit on SQLite + integration on PostgreSQL) | #4121, #4122, #4124, #4125 tests |
| `accounts` / `oidc` / `header` end-to-end browser journeys (two-user login, admin/worker negative matrix, stream expiry and revocation, restart and replica consistency) | Blocked: hermetic implementation only, pending #4128 product qualification | #4128 (product evidence) |
| Live external IdP and MFA qualification, deployment-owner protected inventory, coordinated release, retirement execution, retention disposition | Blocked by design: operator-gated steps requiring named-owner approval and separately authorized live checks | Deployment-cutover issue, #4131 rehearsal gate prerequisites |
| `keycloak`, `default`, `google`, `local`, or unknown selectors; dual old/new credential issuance; whole shared PostgreSQL/Temporal restore as auth rollback; silent `disabled` fallback | Rejected, never a supported path | This contract §§2–4 plus §12.2 |

#4128 consumes the documented journey: qualification runs the real
fresh-install, migrated-data, remote OIDC/proxy, and restricted-local
paths against the coordinated implementation and records the result.
Documentation must not invent a successful result ahead of that evidence.

### 12.2 Cutover, backup, and rollback tooling

The deployment owner's actual tooling is the hermetic rehearsal gate
[`tools/keycloak_cutover_rehearsal.py`](../../tools/keycloak_cutover_rehearsal.py)
(#4131, plan coverage K6). It verifies preflight, rehearsal,
rollback-check, and retirement-check fixtures from repo files only: exact
build pins and topology, sanitized inventory survey, UUID and ownership
retention across the real-shaped cutover sequence, failure containment at
each step, backup-envelope restore verification, mutation freeze, drain
that preserves admitted Temporal work, dual-issuance refusal, and textual
retirement-plan safety. It never contacts a live IdP, mints or invalidates
real sessions, or deletes services, volumes, roles, or databases; no new
commands are invented here — run its `--help` for the exact invocation.

Operator-gated steps stay `blocked` with the missing evidence named until
the deployment owner supplies them. The durable rules the gate enforces
are:

- Encrypted, restore-tested backups of identity, configuration, and key
  material with recorded matching code and image versions. Hermetic
  envelopes prove structure and integrity; live encryption-at-rest is an
  operator-KMS deployment property.
- Version matching between the application, configuration, and session-key
  set before and after the change; freeze of account creation and
  identity, mapping, privilege, and session mutations during the bounded
  mapping and cutover window; drain or accounting of old authentication
  requests so old and new API replicas never issue incompatible
  credentials concurrently.
- Account and MFA enrollment through the qualified replacement before
  cutover; exact service retirement (precisely identified obsolete
  services only — never `down -v`, broad pruning, or shared-role and
  app/Temporal volume deletion); session invalidation on cutover; and
  encrypted recovery-snapshot retention for an explicitly bounded period.
- Rollback restores a matching application, configuration, and session-key
  set with reconciliation or forward repair and intentional invalidation
  of incompatible sessions. Restoring the entire shared PostgreSQL or
  Temporal database is not an auth rollback; falling back to `disabled`
  authentication is never a rollback. If identity data changed after
  cutover, reconcile before restoring or repair forward.

### 12.3 Upstream pin compatibility and plan lifecycle

The reusable upstream boundary follows the pin recorded in
[OmnigentAuthAdapterContract.md](./OmnigentAuthAdapterContract.md) §1
(upstream commit `f04b0354fb5344c1ea8b92795ceb6760a9ad7595`,
`omnigent==0.12.0`). A pin change re-runs the conformance fixtures
before any behavior is relied upon; qualification results name the exact
pin they ran against.

[KeycloakRemovalPlan.md](../tmp/KeycloakRemovalPlan.md) stays at
`Status: Proposed` until execution is complete (see the point-in-time
ledger in [KeycloakRemovalStatus-4130.md](../tmp/KeycloakRemovalStatus-4130.md)).
Clean it up only then: retain the accepted contracts in this document and
its adapter companion, resolve backlinks, and archive or remove the
temporary plan. Do not erase necessary operator upgrade guidance
prematurely.

### 12.4 Advanced-mode logout limitations and MFA qualification (#4124)

- OIDC logout always invalidates the local MoonMind session first, even
  when the optional IdP end-session call fails. IdP-wide (single) logout
  across all clients is not guaranteed and is never claimed; the logout
  response reports `idp_logout_ok` honestly.
- Trusted-proxy logout clears the local MoonMind session only. The
  upstream proxy may continue asserting the same identity on the next
  request; upstream revocation is owned by the proxy. Local account
  disablement still blocks every request at validation time, and
  per-resource authorization stays enforced after identity resolution in
  both advanced modes.
- No new MFA or proxy product is introduced here. Deployments requiring
  MFA retain it through verified IdP policy (OIDC) or explicitly block
  cutover until an MFA-capable path is qualified; a valid IdP account,
  matching email, verified domain, or upstream admin list never grants
  MoonMind superuser authority. Live-provider and MFA qualification is
  recorded separately from hermetic CI per §12.1.

### 12.5 Advanced-mode operator configuration (#4124)

Runnable Compose inputs (see `.env-template` for the full list):

```env
# Generic OIDC (AUTH_PROVIDER=oidc)
AUTH_PROVIDER="oidc"
MOONMIND_PUBLIC_BASE_URL="https://app.example.invalid"
MOONMIND_OIDC_ISSUER="https://idp.example.invalid/realms/moonmind"
MOONMIND_OIDC_CLIENT_ID="moonmind"
MOONMIND_OIDC_CLIENT_SECRET="<operator-held secret>"
# Optional: MOONMIND_OIDC_CALLBACK_URL defaults to
# MOONMIND_PUBLIC_BASE_URL + /api/v1/auth/oidc/callback (must be same-origin).
# Optional opt-in auto-provisioning: MOONMIND_OIDC_AUTO_PROVISION="1".

# Trusted proxy (AUTH_PROVIDER=header)
AUTH_PROVIDER="header"
MOONMIND_TRUSTED_INGRESS="1"
MOONMIND_TRUSTED_PROXIES="10.0.0.5,10.0.0.0/24"
MOONMIND_PROXY_IDENTITY_NAMESPACE="corp-sso"
# Optional: MOONMIND_PROXY_IDENTITY_HEADER="X-Moonmind-User" (default),
# MOONMIND_PROXY_ALLOW_EMAIL_IDENTITIES="1" (default "0": email-shaped
# proxy subjects need explicit enrollment and never auto-provision).
```

Selecting `oidc` or `header` without these inputs fails startup with an
actionable error; omitted values never silently select a mode.
