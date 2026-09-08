# Keycloak-removal qualification evidence — remediation pass (MoonLadderStudios/MoonMind#4128)

Run-local handoff for the `remediate-issue` remediation step. This is execution
scaffolding, not canonical desired-state documentation.

## Tested revision

- Candidate head preserved: `a2d619ab84d902fc7b6781c7f95d5c09d36cd7cf`
  (assessment base `94fe55d0b`, plus `5a43af23` conformance baseline and the
  `a2d619ab` remediation delta).
- Working-tree delta of this pass (uncommitted, 5 files, additive except the
  dead-fixture deletion):
  - `tests/conftest.py` — deleted the unused pre-cutover `keycloak_mode`
    fixture (zero consumers repository-wide).
  - `tests/integration/temporal/test_temporal_artifact_authorization.py` —
    added `_AUTHENTICATED_PROVIDER_MODE`; 4 raw literals replaced.
  - `tests/integration/temporal/test_task_shaped_submission_normalization.py` —
    added `_AUTHENTICATED_PROVIDER_MODE`; 1 raw literal replaced.
  - `tests/unit/workflows/temporal/test_artifacts.py` —
    added `_AUTHENTICATED_PROVIDER_MODE`; 1 raw literal replaced.
  - `tests/unit/auth/test_keycloak_removal_conformance.py` — 4 new guards
    (fixture retirement, selector centralization, generated-frontend input,
    default-profile topology). 13 prior guards untouched.

## Commands and results (2026-09-08, hermetic sandbox)

| Command | Result |
|---|---|
| `python3 -m py_compile` on all 5 edited files plus `tests/unit/test_integration_test_taxonomy.py` and `tools/select_test_suites.py` | PASS |
| Direct execution of all 17 functions in `tests/unit/auth/test_keycloak_removal_conformance.py` (plain asserts over file reads + YAML; no pytest needed) | 17/17 PASS (13 prior + 4 new) |
| Direct `select_suites([...], event_name="pull_request")` for 12 representative paths (auth × 6, transport × 2, schema × 1, pins × 2, conformance module × 1) | All expected outputs hold; impl-8 VERIFIED gate intact |
| AST check: `_AUTHENTICATED_PROVIDER_MODE` defined once per named module; zero raw `setattr(..., "AUTH_PROVIDER", "keycloak")` literals remain in the 3 modules; zero `keycloak_mode` references in `tests/conftest.py` | PASS |
| `./tools/test_unit.sh --python-only ...` | NOT RUN — `/opt/venv` has no `pytest` module in this sandbox |
| `moonmind container python-tests ...` | NOT RUN — requires `MOONMIND_RUNTIME_ID` (managed workflow only); explicit container-job evidence per AGENTS.md, not an assertion failure |
| Compose-backed `integration_ci` suites (artifact authorization, submission normalization, browser, two-replica) | UNEXECUTED — require Docker/Compose backend unavailable in this sandbox |
| Live IdP / MFA / operator cutover | EXTERNAL — separate evidence category per the issue brief; never part of merge CI |

## Failed / skipped

- Failed: none in the executed checks above.
- Skipped by design (out of scope for this bounded pass, owned by the
  #4118–4127 feature work and its integrated qualification): rw-1 hermetic
  fixtures, rw-2 production session-issuance replacement, rw-3 modes and
  negative authority matrix, rw-4 migration durability, rw-5 two-user browser
  journey, rw-6 built-artifact/DNS-block/rendered-topology qualification.
- Remaining raw `keycloak` test literals outside this pass (not touched to keep
  scope bounded): `tests/unit/api/routers/test_executions.py` (2),
  `tests/integration/reliability/test_api_startup_provider_maintenance.py` (1).
  Both still select "any authenticated mode" and remain valid pre-cutover
  negative coverage; the cutover should centralize or repoint them the same way.

## Hermetic vs live distinction

Everything executed above is hermetic product qualification (source-level
guards, selector outputs, static checks — no external credentials). No live
provider or deployment result is claimed. The full browser / multi-instance /
OIDC migration matrix in the removal plan still requires the integrated
implementation, deletion, and documentation candidate before final
qualification of #4128.
