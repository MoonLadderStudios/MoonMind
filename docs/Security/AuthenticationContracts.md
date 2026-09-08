# Authentication Contracts

**Document Class:** Canonical declarative  
**Viewpoint:** Cross-Cutting Concept View  
**Status:** Draft  
**Owners:** MoonMind Engineering  
**Audience:** API, dashboard, runtime, security contributors and operators  
**Authority:** Application authentication modes, identity mapping, session/revocation, CSRF/origin, error semantics, and background-work policy for MoonMind user authentication. Freezes the target contract inventoried in `[KeycloakRemovalInventory-4117.md](../tmp/KeycloakRemovalInventory-4117.md)` (MoonLadderStudios/MoonMind#4117, parent epic #4116).  
**Owning Surface:** `api_service/auth.py`, `api_service/auth_providers.py`, `api_service/main.py`, `moonmind/config/settings.py` (`OIDCSettings`), `api_service/db/models.py` (`User`)  
**Related Docs:** [SecretsSystem.md](./SecretsSystem.md), [ProviderProfiles.md](./ProviderProfiles.md), [SettingsSystem.md](./SettingsSystem.md), [KeycloakRemovalPlan.md](../tmp/KeycloakRemovalPlan.md) (predecessor proposal from #4102; starting evidence only, not the deliverable)

> [!NOTE]
> This document defines desired-state MoonMind application-authentication contracts.
> It is a declarative contract, not an implementation checklist. Rollout sequencing,
> migration tasks, and backlog tickets belong in MoonSpec artifacts, gitignored
> handoffs, or `docs/tmp/` implementation plans.

---

## 1. Summary

MoonMind has exactly one application authentication selector, `AUTH_PROVIDER`, and
exactly one canonical principal, `User.id` (a stable MoonMind UUID). The supported
target modes are `accounts`, `oidc`, `header`, and an explicitly restricted local
mode `disabled`. Retired selectors (`keycloak`, `default`, `google`) are rejected
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
