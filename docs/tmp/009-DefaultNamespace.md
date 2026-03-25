# Default Temporal namespace: `default` for local, custom for shared deploys

MoonMind currently defaults `TEMPORAL_NAMESPACE` to `moonmind` in env templates, Docker Compose, `TemporalSettings`, workflow artifact settings, the namespace bootstrap script, and several code-level fallbacks. The bootstrap script creates or updates the configured namespace and registers search attributes there.

## Goal

- Local Docker Compose uses Temporal’s built-in `default` namespace unless overridden.
- Custom namespaces remain fully supported via `TEMPORAL_NAMESPACE`.
- Shared or enterprise setups can keep `moonmind`, `moonmind-dev`, `moonmind-prod`, etc.
- Less friction for operators and agents used to the standard Temporal local namespace.

## Target behavior

**Fresh local deployment**

- `.env-template`: `TEMPORAL_NAMESPACE=default` (not `moonmind`).
- `docker-compose.yaml`: every `${TEMPORAL_NAMESPACE:-…}` fallback uses `default` for API, all worker services, and `temporal-namespace-init`.
- `services/temporal/scripts/bootstrap-namespace.sh`: internal default `TEMPORAL_NAMESPACE="${TEMPORAL_NAMESPACE:-default}"` so the script matches Compose when the variable is unset.
- `moonmind/config/settings.py`: `TemporalSettings.namespace` and `WorkflowSettings.temporal_artifact_default_namespace` both default to `default` (the latter still uses `TEMPORAL_NAMESPACE` as its validation alias so env overrides stay aligned).

**Custom / shared deployments**

- Operators set `TEMPORAL_NAMESPACE` to the desired name; behavior unchanged from today aside from the new defaults when unset.
- Bootstrap continues to create or update non-`default` namespaces and apply retention + search attributes as it does now.

## Implementation plan

### 1. Default namespace surface

Update every default that still implies `moonmind` for the active Temporal namespace or artifact key prefix.

**Files**

- `.env-template`
- `docker-compose.yaml`
- `services/temporal/scripts/bootstrap-namespace.sh` (Compose passes env in; the script still has its own `:-moonmind` default on line 15—change that to `:-default`.)
- `moonmind/config/settings.py` — `TemporalSettings.namespace` and `WorkflowSettings.temporal_artifact_default_namespace`

**Code fallbacks** (empty or whitespace namespace segments must not silently keep `moonmind` when settings default to `default`)

- `moonmind/workflows/temporal/artifacts.py` — `build_storage_key` on local and S3 backends uses `or "moonmind"` after normalizing `namespace`; change to `or "default"` (or derive from a single shared constant used with settings tests).
- `moonmind/workflows/temporal/service.py` — `TemporalService.__init__` parameter default `namespace: str = "moonmind"`; align with `default` or require callers to pass settings (prefer matching app default).

**Tests and tooling that encode the old default** (update when changing defaults)

- `tests/integration/temporal/test_compose_foundation.py` — asserts `${TEMPORAL_NAMESPACE:-moonmind}`; update expected strings.
- Any unit test that assumes `TemporalSettings.namespace == "moonmind"` without setting env.
- `scripts/test_temporal_e2e.py` — `os.getenv("TEMPORAL_NAMESPACE", "moonmind")` should use `default`.
- Integration/e2e fixtures that hardcode `"namespace": "moonmind"` only need changes if they assert *default* behavior without env; if they explicitly model a custom namespace, leave them.

**Why artifact settings and literals must move together**

`WorkflowSettings.temporal_artifact_default_namespace` shares `TEMPORAL_NAMESPACE` as alias. If runtime defaults move to `default` but `artifacts.py` still falls back to `moonmind` for empty segments, storage keys and debugging drift from the real namespace.

### 2. Bootstrap behavior for `default` vs custom

File: `services/temporal/scripts/bootstrap-namespace.sh`

**`TEMPORAL_NAMESPACE=default`**

- Wait for Temporal health; verify the namespace is reachable.
- Do not create or update the built-in `default` namespace; do not change its retention.
- Still run search-attribute registration MoonMind needs, using the same `--namespace default` (or equivalent) paths the script already uses for custom namespaces—only the namespace *lifecycle* steps are skipped.
- Log explicitly that create/update was skipped because `default` is the built-in namespace.

**Any other namespace**

- Keep current flow: health wait, create/update namespace, retention when configured, search-attribute registration.

Do not tie “skip namespace create” to “skip search attributes”; those are separate steps in the script.

### 3. Search attributes

MoonMind registers custom search attributes (`mm_entry`, `mm_owner_id`, `mm_state`, `mm_updated_at`, `mm_repo`, etc.) during bootstrap. Confirm against the Temporal version in Compose whether registration is namespace-scoped (current script passes `--namespace` on the modern CLI path). Preserve working registration for both `default` and custom namespaces; only omit namespace create/update for `default`.

### 4. Documentation

- `README.md` — local quick start: default namespace is `default`; override with `TEMPORAL_NAMESPACE`.
- `docs/Temporal/TemporalArchitecture.md` — adjust `temporal-namespace-init` / container copy so local default is `default`, custom mode still documented.
- `docs/Temporal/TemporalPlatformFoundation.md` — distinguish local default (`default`) from shared/enterprise recommendation (dedicated namespace such as `moonmind`).
- `AGENTS.md` — optional short note if it helps agent runs; not a substitute for README + Temporal docs.

### 5. Regression coverage

**Settings**

- Default `TemporalSettings.namespace` is `default`.
- `TEMPORAL_NAMESPACE=moonmind` (and other values) override correctly.
- `WorkflowSettings.temporal_artifact_default_namespace` tracks `TEMPORAL_NAMESPACE` default and overrides.

**Bootstrap**

- Shell tests or existing harness: `default` → no namespace create/update; search-attribute path still invoked as today.
- Custom namespace → unchanged create/update + attributes.
- Unset/blank resolves to `default` consistently in script + Compose.
- Logs identify which branch ran.

**Compose / contract**

- `test_compose_foundation.py` (or equivalent) expects `${TEMPORAL_NAMESPACE:-default}` everywhere applicable.
- Repo check or test that fails if any `${TEMPORAL_NAMESPACE:-moonmind}` remains in `docker-compose.yaml` (and optionally in `docker-compose.test.yaml` if that file mirrors production fallbacks for namespace).

## Rollout

Pre-release: single clean default change, no compatibility shims.

1. Config, template, Compose, script default, code literals.
2. Bootstrap branching for `default`.
3. Tests (settings, compose contract, bootstrap).
4. Docs in the same PR as behavior.

Existing `.env` files with `TEMPORAL_NAMESPACE=moonmind` are unchanged because explicit env wins.

## Risks and mitigations

| Risk | Mitigation |
|------|------------|
| Search attributes missing on local | Keep registration on the `default` branch; only skip namespace CRUD. |
| Artifact keys diverge from Temporal namespace | Change settings and `artifacts.py` fallbacks together. |
| Operators assume `moonmind` | Update README + both Temporal docs in the same PR. |
| Stale Compose fallbacks | Contract test + grep/CI guard for `:-moonmind` on `TEMPORAL_NAMESPACE`. |

## Acceptance criteria

- Fresh `cp .env-template .env && docker compose up -d` uses `default` without extra steps.
- API and workers connect with namespace default `default`.
- Setting `TEMPORAL_NAMESPACE` to a custom name still works end-to-end.
- Bootstrap skips namespace create/update for `default` but performs required safe steps (including search attributes).
- Docs state: local default = `default`; shared/enterprise = dedicated namespace (e.g. `moonmind`).

## Suggested commit split (one PR)

1. Defaults: env template, Compose, settings, script default line, code fallbacks.
2. Bootstrap: branch for `default` vs custom.
3. Tests.
4. Docs.

Keeps behavior, safety, and documentation in sync.
