# Keycloak Removal Qualification — #4118 (K2)

Status: Qualification slice for MoonLadderStudios/MoonMind#4118
(parent epic #4116, depends on #4117). Temporary execution scaffolding
under `docs/tmp/` per AGENTS.md. Durable adapter semantics live in
`docs/Security/OmnigentAuthAdapterContract.md`. This document changes no
runtime behavior, removes no services, and authorizes no production
account changes.

## 1. Provenance

- Upstream repository: `https://github.com/omnigent-ai/omnigent`
  (git submodule `omnigent`).
- Pinned commit: `f04b0354fb5344c1ea8b92795ceb6760a9ad7595` (verified via
  `git -C omnigent rev-parse HEAD` in the working tree).
- Packaged artifact: `omnigent==0.12.0` (`omnigent/pyproject.toml`
  `[project].version`, mirrored in `omnigent/omnigent/version.py`).
- No mutable-main pin and no private monkey-patch: qualification imports
  only the reusable modules
  (`omnigent.server.{auth,oidc,passwords,accounts_config}`) and never
  the whole application, permission store, runtime server, device-grant
  store, or CLI/magic-link routes. No `sys.modules` entry for
  `omnigent.server.app`, `omnigent.server.permissions`, or
  `omnigent.stores.permission_store` is created (asserted in
  `test_production_packaging_excludes_irrelevant_runtime_modules`).
- Image provenance: no new always-on auth container is introduced by
  this slice; deployment image changes (if any) are owned by the K4/K6
  cutover, not by qualification.

## 2. What was built

- Adapter: `moonmind/security/omnigent_auth_qualification.py` — explicit
  `MoonmindAuthConfig` (no ambient `OMNIGENT_*` reads), `ValidatedIdentity`
  hook resolved before minting, async `AsyncAccountStore` /
  `SessionRevocationStore` protocols with hermetic in-memory fixtures,
  MoonMind purpose-bound sessions (`moonmind-control-plane` /
  `moonmind-browser-session`, distinct cookies/keys), a TTL cache that
  re-validates every hit, and fail-closed rejection of refresh,
  delegated, runner, CLI-ticket, magic-link, and upstream-admin-roster
  surfaces.
- Conformance fixtures:
  `tests/unit/security/test_omnigent_auth_qualification_4118.py` (27
  tests covering all six acceptance criteria; hermetic — no DB, no
  network, no credentials).
- Canonical contract: `docs/Security/OmnigentAuthAdapterContract.md`,
  referenced from `docs/Security/AuthenticationContracts.md` §11.
  The K1 inventory (`docs/tmp/KeycloakRemovalInventory-4117.md`) and
  its fixtures are untouched.

## 3. Reuse evidence (real upstream entrypoints, pinned revision)

`collect_upstream_probe_evidence()` (exercised by
`test_upstream_probes_pass_on_pinned_revision`) confirms against the
pinned commit:

- `mint_session_token` + `hmac_digest` + `UnifiedAuthProvider`
  cookie round-trip, including `None` for missing/malformed tokens.
- Argon2 password hash/verify round-trip through
  `omnigent.server.passwords`.
- `AccountsConfig.from_env` and `OIDCConfig.from_env` fail loud on
  missing configuration.
- Upstream defaults intact (`local` reserved identity, default header
  name, header-mode fail-closed without the single-user marker), so
  existing standalone upstream consumers remain supported.

## 4. Test results

pytest is unavailable in the execution sandbox (no `pytest` module, no
network for installs, and the MoonMind managed container backend
requires a workflow runtime ID), so the conformance suite could not be
executed via the canonical `./tools/test_unit.sh` runner here.
Instead, every behavior the suite asserts was executed directly with
the system interpreter against the real pinned upstream sources:

- All five `UpstreamProbeEvidence` probes pass on the pinned revision
  (see §3).
- All adapter flows pass: minimal accounts/OIDC issuance, email-only
  rejection, cross-issuer distinction, case-sensitive subjects,
  reserved-identity rejection, argon2 round-trip, enrollment-required
  path, admin-advisory non-promotion, inactive blocking, cross-replica
  revocation, generation invalidation, cache-bypass resistance,
  grant/delegated/malformed/wrong-key rejection,
  missing/invalid/conflict semantics, unsupported-surface rejection,
  selector fail-closed behavior, hostile ambient `OMNIGENT_*`
  isolation, control-plane/runtime cookie/key/purpose separation, and
  upstream-token rejection at the MoonMind boundary.
- K1 regression risk: `docs/Security/AuthenticationContracts.md` keeps
  all frozen structured mode rows and retired-selector coverage and
  gains only an additive §11 pointer; `api_service/auth.py`,
  `api_service/auth_providers.py`, and `pyproject.toml` are unmodified
  (cutover and packaging advances belong to K4/K5). Downstream
  verification must rerun `./tools/test_unit.sh --python-only
  tests/unit/security/` including `test_auth_inventory_4117.py`.

## 5. Known limits / handoff to K3/K4

- Production persistence wiring (existing MoonMind transactions and
  profile authority behind the new protocols) is defined but not
  connected; K3/K4 own the concrete adapters and migration.
- API/route cutover is explicitly out of scope: no FastAPI Users path
  was changed or removed here.
- No upstream code changes were required for this slice, so no upstream
  PR is referenced. If K3/K4 need a natively supported
  validated-claims hook inside the upstream OIDC callback (rather than
  the MoonMind-side `ValidatedIdentity` contract defined here), that
  upstream extension must land through the reviewed upstream process
  before the cutover relies on it; this issue must not be read as
  permission to substitute an insecure local copy or to call Omnigent
  Server remotely.
