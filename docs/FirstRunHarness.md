# First-Run Harness (MoonMind#3938)

**Document Class:** Canonical declarative
**Status:** Current
**Audience:** Operators and CI proving the fresh-clone journey
**Authority:** Derived from `docker-compose.yaml` (Compose wins on conflict),
the README [Quick Start](../README.md#quick-start) (the documented production
journey), and [FirstRunServiceInventory](FirstRunServiceInventory.md) (the
operational service map).
**Related:** #3926 (parent), #3937 (topology), #3939 (CLI), #3940 (Quick Start),
#3950 (CI selection). CLI-example parity with this journey is owned by #3939;
until it lands, this harness binds the README Quick Start commands/URLs only
and claims no CLI coverage.

The gap this harness closes: default admission authority (OpenCode Agent
Profile / credentialless `opencode-zen-free` Provider Profile at the real
admission boundary) and Quick Start prose contracts exist, but no executable,
reproducible first-run contract drives the documented journey to terminal
evidence. A credentialless external provider is still an external dependency:
a stubbed run is never proof of a live zero-configuration journey, and live
provider availability is never a required hermetic PR test.

## Two evidence tiers

| Tier | Where it runs | What it proves |
| --- | --- | --- |
| **Hermetic (required)** | `integration` + `integration_ci` via `./tools/test_integration.sh` | Production startup/admission/host/session/finalization boundaries with controlled local dependencies only. Deterministic, credential-free, no live provider inference calls. Controlled substitutions are labeled as substitutions. |
| **Live qualification (protected)** | Scheduled/manual via `tools/first_run_live_qualification.py --live` | The exact default external provider and images from a documented clean state. Publishes a live report; never gates a PR. |

## Clean-install starting state

- Supported prerequisites: Docker Desktop (Compose V2), git, `amd64` or
  `arm64` host, registry access to GHCR for image pulls.
- Disposable Compose project only: `moonmind-test` or `moonmind-test-<suffix>`.
  The `moonmind` deployment project is never a valid first-run target.
- Compose services on this path include `api`, `postgres`, `temporal`, and
  `omnigent` (derived from `docker-compose.yaml`; Compose wins on conflict).
- Clean project-scoped volumes, no `.env`, no inherited provider credentials
  (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_API_KEY`,
  `OPENROUTER_API_KEY`, `OPENCODE_API_KEY`, `GITHUB_TOKEN`, `GITHUB_PAT`),
  and no prior catalog or profile state.
- Exact MoonMind revision and image digests recorded per run (live tier pins
  complete immutable `*_IMAGE_REF` digests; hermetic tier records the
  substitution label instead of a live digest).
- Cold- and warm-cache results reported separately (cold = images and caches
  absent; warm = images and caches present).

## Production journey (both tiers)

1. `git clone` + `git submodule update --init --recursive`.
2. `docker compose up -d` (or the disposable-project equivalent with
   `--project-name moonmind-test-<suffix>`); images are pulled, nothing is
   built locally on this path.
3. Migrations / bootstrap / readiness: dashboard at `http://localhost:7000`
   **and** `curl -fsS http://localhost:7000/healthz` succeeds. Health means
   the control plane is up; it does not mean a run is ready.
4. Select the omitted/default options (no explicit profile/model), or
   equivalently the documented `auto`/default values — both must resolve to
   the same intended authority.
5. Launch one bounded task from the non-sensitive fixture (summarize the
   README; `publication.mode: none`, no PAT, no operator-repo mutation).
6. Observe authoritative terminal evidence: the Workflow Detail page
   (`/workflows/{workflowId}`) with outputs and artifacts plus logs and
   diagnostics. An accepted submission, green dashboard, seeded profile, or
   passing readiness alone is insufficient.
7. Verify host/resource cleanup and tear down only disposable project
   resources, even after failure.

## Phase timings (measured budget)

Recorded per phase — `image_acquisition`, `bootstrap`, `readiness`,
`admission`, `provider_execution`, `evidence_finalization`, `cleanup` — with
cold and warm cache stated separately. The ten-minute goal is a measured
budget under documented conditions, not a guarantee about arbitrary network
speed or public-provider service. Helpers: `build_timing_record` in
`moonmind/first_run/harness.py`.

## Negative journeys

Provider failures (`provider_unavailable`, `provider_rate_limited`) are
labeled with the provider tier, recorded as failures (never success), and
return actionable errors. Platform failures (`image_resolution_failed`,
`bootstrap_authority_missing`, `startup_interrupted`, `worker_restart`,
`startup_failure`, `admission_failure`) are labeled with the platform tier.
No failure silently switches to a paid provider, another credential, or a
less-constrained runtime; only explicit operator action may do that.
Helper: `classify_first_run_failure`.

## Restart / retry idempotency

Interrupting startup and restarting, retrying a failed admission, or
restarting a worker mid-journey resumes the same session. Post-recovery audit
must show exactly one session, one publication decision, no duplicated
credentials, and no duplicated resources.

## Diagnostics and teardown

- Always capture bounded redacted diagnostics (`redact_diagnostics`, 8000-char
  bound; secret-like values replaced with `[REDACTED]`).
- Tear down only disposable project resources (`plan_scoped_teardown`).
  Refused, never-executed actions include global prune
  (`docker volume prune`, `docker system prune`), `down -v` on the deployment
  project, and unrelated operator volumes.

## Live report schema

Produced only by the protected live runner. Fields: exact revision, image
digests, provider/model, cache condition (`cold`/`warm`), per-phase timings,
result, cleanup status, recorded timestamp. Environmental failures are labeled
truthfully and never counted as success. Helper: `build_live_report`.

## How to re-derive

```bash
# Required hermetic tier (no credentials, no live provider calls):
./tools/test_integration.sh   # runs tests/integration -m 'integration_ci'

# Protected live tier (explicit opt-in, never in required CI):
python tools/first_run_live_qualification.py --check-only
python tools/first_run_live_qualification.py --live \
  --provider <provider> --model <model> --cache-condition cold \
  --project-name moonmind-test-<suffix>
```
