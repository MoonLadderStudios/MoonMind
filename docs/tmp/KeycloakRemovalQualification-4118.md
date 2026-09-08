# Keycloak Removal Qualification — #4118 (K2)

Status: Qualification evidence for MoonLadderStudios/MoonMind#4118 (parent epic
#4116, depends on #4117). Temporary execution scaffolding under `docs/tmp/`
per AGENTS.md. Durable desired-state contracts live in
`docs/Security/AuthenticationContracts.md` §11. This document records evidence;
it changes no behavior.

## 1. Upstream pin and packaged artifact identities

- Upstream repository: `https://github.com/omnigent-ai/omnigent` (submodule `omnigent/`).
- Pinned commit: `f04b0354fb5344c1ea8b92795ceb6760a9ad7595` (verified:
  `git -C omnigent rev-parse HEAD` matches `UPSTREAM_PIN` in
  `moonmind/omnigent_qualification.py`; `upstream_provenance()["pin_match"]`
  is asserted in `tests/unit/security/test_omnigent_qualification_4118.py`).
- Packaging: pinned submodule, no PyPI indirection, no mutable-main pin, no
  private monkey-patch. Production packaging includes only the reusable
  modules below; the whole upstream application, permission store, route
  factories, runtime server, device-grant store, synchronous concrete account
  store, and embedded host transport are never imported by the adapter.
- Relevant image provenance: no new auth microservice or auth container. Only
  the existing Omnigent host/runtime images remain pinned in
  `docker-compose.yaml` (`omnigent-host`, `omnigent-host-moonmind`); ordinary
  authenticated API requests require no network call to Omnigent Server.
- File identities (sha256 at the pinned commit):

| File | sha256 |
| --- | --- |
| `omnigent/server/auth.py` | `64b7aa4a1f759e3fa75da23787c0e53fdfab2ac7d71f1bcc13a9f78c0d2a1045` |
| `omnigent/server/oidc.py` | `f139995b464c208b210a9abf5c28b9320691cbc3cfdc0afe3b69df6feb2cd70d` |
| `omnigent/server/passwords.py` | `f0a6e41a86bfa9e1731ec9853e1cdc32c5a6511e318abeb0f4dedb3fb03233bb` |
| `omnigent/server/accounts_config.py` | `72be98ae6650d0af40566275b3da4592e8e3338b0876b0edafe512883ed732fb` |

MoonMind baseline at qualification: `70e90df4a` (post-#4129 removal; `main`
after #4138 inventory and #4140 removal).

## 2. Supported interface contract

Reusable upstream entrypoints (the only upstream surface the adapter touches):

- `omnigent.server.auth.UnifiedAuthProvider` — constructed explicitly with an
  explicit `source`, explicit `header_name`, `local_single_user=False`, and an
  explicit cookie-config shape. `create_auth_provider()` and
  `resolve_auth_source()` are never used for MoonMind decisions.
- `omnigent.server.oidc.mint_session_token` / `hmac_digest` — real session
  issuance and cache-key derivation for the qualification slice.
- `omnigent.server.passwords.verify_password` / `hash_password` — qualified
  password handling; a missing or incompatible hash yields a controlled
  enrollment/reset requirement (`EnrollmentRequiredError`).

MoonMind-owned seams (`moonmind/omnigent_qualification.py`):

- `QualifiedAuthConfig`: explicit mode/cookie/key/TTL/issuer/audience, no env
  reads, fail-closed validation (32-byte minimum secret, distinct cookie name,
  non-blank issuer/audience, positive TTL, closed mode vocabulary).
- `resolve_validated_identity()`: verified `(issuer, subject)` resolution
  before minting; case-sensitive subject, full issuer URI, reserved identities
  rejected, email carried as an opaque attribute only.
- `AsyncAccountStore`: async protocol resolving a `ValidatedIdentity` to an
  existing MoonMind UUID; sync verification offloaded with
  `asyncio.to_thread`; no parallel account/admin tables.
- `MoonmindQualifiedAuth`: MoonMind-purpose sessions (`iss`/`aud` bound,
  HS256 only, distinct cookie/key, `token_use="moonmind-session"`, `jti`
  revocation handle); live revocation checks on every validation including
  cache hits (principal status enforced at authenticate/mint time and on
  full validation; cache-hit status staleness bounded by the 300s cache
  cap, inside the §5 5-minute propagation bound); conflicting cookie/bearer identities
  rejected; refresh/delegated/runner surfaces explicitly rejected
  (`UnsupportedSurfaceError` / `False`), never accepted by broadening the
  upstream delegated allowlist.
- Standalone upstream consumers remain supported: the submodule files are
  unmodified; the adapter adds no MoonMind-only prerequisites to the portable
  package and keeps upstream's independent default behavior intact.

Explicitly excluded (never imported, never mounted for MoonMind browser
authority): `omnigent.server.app`, `omnigent.stores.permission_store`,
`omnigent.server.routes.*`, `omnigent.server.device_grant_store`,
`omnigent.server.accounts_store.SqlAlchemyAccountStore`, runtime server and
retired embedded host transport.

## 3. Qualification results

Conformance suite: `tests/unit/security/test_omnigent_qualification_4118.py`
(21 tests). Verified by direct execution of the adapter paths in this
environment (no pytest runner available offline): real upstream mint/validate
slice, MoonMind minimal flow with explicit config + injected store, password
verification + controlled enrollment, hook ordering
(identity → store → password → status), cross-issuer/case-sensitive identity
separation, reserved-identity rejection, inactive-account refusal, durable
revocation surviving cache hits, event-loop offload, cache/grant/malformed
rejection, unsupported-surface rejection, cookie/bearer conflict rejection,
fail-closed config, hostile `OMNIGENT_AUTH_*` ambient isolation, and
control-plane/runtime purpose isolation all pass. Re-verified 2026-09-08
after the cache-hit status-rule clarification: 21/21 stdlib-asyncio mirrors
of each test's assertions (real pinned upstream modules, no pytest runner
offline) pass, including pin-match at `f04b0354` with the submodule
initialized. Required CI runs the suite
via `./tools/test_unit.sh tests/unit/security/test_omnigent_qualification_4118.py`.

Existing standalone upstream consumers are unaffected: no submodule file was
modified (`git -C omnigent status` clean at the pinned commit).

## 4. Fixtures for later issues

`build_conformance_fixtures()` in `moonmind/omnigent_qualification.py`
returns `{config, store, auth, pin}`: real session issuance, account-store
call recording (`store.calls`), identity-hook ordering (`auth.hook_order`),
and optional-surface rejection — consumed by the K3/K4 children. Canonical
contract: `docs/Security/AuthenticationContracts.md` §11.

No new auth microservice, copied OIDC/password implementation, whole-server
embedding, or promotion of upstream admin flags into MoonMind superuser
authority was introduced.
