# Omnigent Authentication Adapter Contract

**Document Class:** Canonical declarative
**Viewpoint:** Cross-Cutting Concept View
**Status:** Draft
**Owners:** MoonMind Engineering
**Audience:** API, security contributors and operators
**Authority:** Supported reusable upstream authentication interfaces qualified
for MoonMind composition, and the MoonMind-side adapter seams that bind
them to MoonMind configuration, identity, persistence, and session policy.
Qualifies MoonLadderStudios/MoonMind#4118 (parent epic #4116, depends on
#4117, plan coverage K2 in `../tmp/KeycloakRemovalPlan.md`).
**Owning Surface:** `moonmind/security/omnigent_auth_qualification.py`,
conformance fixtures in `tests/unit/security/test_omnigent_auth_qualification_4118.py`
**Related Docs:** [AuthenticationContracts.md](./AuthenticationContracts.md)
(desired-state modes, identity, session, and error semantics),
[KeycloakRemovalPlan.md](../tmp/KeycloakRemovalPlan.md) (predecessor proposal;
starting evidence only), [KeycloakRemovalQualification-4118.md](../tmp/KeycloakRemovalQualification-4118.md)
(pin, artifact identities, and test results for this qualification)

> [!NOTE]
> This document declares which upstream entrypoints MoonMind reuses and
> which MoonMind-side seams are authoritative. Rollout sequencing,
> migration tasks, and backlog tickets belong in MoonSpec artifacts,
> gitignored handoffs, or `docs/tmp/` implementation plans.

---

## 1. Pinned upstream revision

- Upstream commit: `f04b0354fb5344c1ea8b92795ceb6760a9ad7595`
  (`https://github.com/omnigent-ai/omnigent` submodule).
- Packaged artifact: `omnigent==0.12.0` (`omnigent/pyproject.toml`
  `[project].version`, mirrored in `omnigent/omnigent/version.py`).
- Qualification runs against that pin. A pin change re-runs the
  conformance fixtures before any behavior is relied upon.

## 2. Reused upstream entrypoints (supported)

The adapter composes these real upstream primitives in the MoonMind API
process. None requires booting the upstream application, permission
store, runtime server, or retired embedded host transport:

| Upstream entrypoint | MoonMind use |
| --- | --- |
| `omnigent.server.oidc.mint_session_token` / `hmac_digest` | Session-JWT machinery shape and cache-key pattern probed in qualification; MoonMind mints its own purpose-bound tokens (see §4) |
| `omnigent.server.passwords.{hash_password, verify_password}` | Qualified argon2 password hashing and verification; the only password KDF MoonMind uses for built-in accounts |
| `omnigent.server.auth.UnifiedAuthProvider.get_user_id` | Cookie/header validation behavior probed; MoonMind never adopts its email/username session subjects |
| `omnigent.server.accounts_config.AccountsConfig.from_env` / `omnigent.server.oidc.OIDCConfig.from_env` | Fail-closed configuration validation pattern; MoonMind passes its own explicit config and never reads `OMNIGENT_*` ambient state |

Production packaging includes only these reusable modules. The whole
upstream application, permission store, runtime server, device-grant
store, and CLI/magic-link routes are never imported by the adapter.
Existing standalone upstream consumers are unaffected: qualification
mutates no upstream global, default, or allowlist.

## 3. Identity hook ordering (authoritative)

A validated identity resolves to exactly one existing MoonMind UUID
before any MoonMind session is minted:

1. The caller supplies a `ValidatedIdentity` carrying verified claims:
   OIDC `(issuer URI, case-sensitive subject)` or the verified account
   login in the `moonmind-accounts` namespace. The OIDC callback path
   must preserve issuer/subject claims before they are collapsed to
   email; a wrapper around the final subject string is insufficient.
2. The async account store maps `(issuer, subject)` to an existing
   `User.id`. No automatic linking by email, display name, username, or
   a matching subject from a different issuer. Email changes never
   change resource ownership.
3. The account record must be active. `User.is_active` and MoonMind
   `is_superuser` are MoonMind-owned and checked after credential
   validation on every request, including cache hits. An upstream admin
   advisory is never promoted into MoonMind superuser authority.

## 4. Session policy

- MoonMind issues its own session: `__Host-mm_session` in production
  (`mm_session_dev` only for explicitly permitted loopback HTTP),
  HttpOnly, SameSite Lax or stricter, Secure on HTTPS.
- Tokens carry explicit issuer (`moonmind-control-plane`), audience /
  purpose (`moonmind-browser-session`), HS256 algorithm allowlist,
  expiry, key rotation via distinct secrets, and a unique `jti`.
- Upstream runtime tokens (`__Host-ap_session` / `ap_session`,
  upstream issuer/provider claims) are rejected at the MoonMind user
  boundary. Control-plane and runtime cookie names, signing keys, token
  purpose, and persistence are separate.
- The TTL credential cache is keyed by HMAC digest and every hit
  re-runs full validation, so revocation, generation, scope, and
  principal-status checks are never skipped. Grant-derived tokens
  (any `grant_id` or `scope` claim) bypass the cache and require live
  revocation lookup; they are rejected at the browser boundary.
- One durable revocation mechanism lives behind the portable
  `SessionRevocationStore` interface. Logout revokes the session;
  password reset, account disablement, and administrative revocation
  bump the user generation and invalidate relevant credentials across
  replicas. Clearing a cookie alone is not revocation.

## 5. Persistence

The `AsyncAccountStore` protocol is async-safe: no synchronous remote
database work blocks the API event loop, and no parallel account/admin
tables are initialized. Production wiring uses existing MoonMind
transactions and profile authority through adapters (K3/K4 own the
concrete wiring). Password hashes must be qualified argon2; missing or
foreign hashes take the controlled enrollment/reset path, never a
silent compatibility assumption.

## 6. Explicitly rejected surfaces

Refresh grants, delegated scope tokens, runner-minted tokens, CLI
tickets, magic links, and the upstream admin roster are unsupported at
the MoonMind browser boundary. They fail closed with
`unsupported_surface` and are never accepted by broadening the upstream
delegated endpoint allowlist. Browser login never returns refresh
material in JSON.

## 7. Configuration isolation

MoonMind authentication is selected only by the explicit `AUTH_PROVIDER`
selector (`accounts` / `oidc` / `header` / `disabled`). Retired
selectors (`keycloak`, `default`, `google`) and unknown values fail
startup with migration guidance and are never silently translated.
Runtime-server `OMNIGENT_AUTH_*` variables never select MoonMind's
mode, key, or identity store, including hostile ambient values. Invalid
provider configuration fails closed.

## 8. Error semantics

Missing credentials are `auth_required` (optional only at explicitly
optional boundaries); invalid, expired, wrong-key/issuer/purpose, or
reserved-identity credentials are `auth_invalid`, including on cache
hits; conflicting cookie/bearer identities are `auth_conflict`;
inactive accounts are `inactive`/`forbidden`; store outages are
`unavailable` and never return an administrator stub.
