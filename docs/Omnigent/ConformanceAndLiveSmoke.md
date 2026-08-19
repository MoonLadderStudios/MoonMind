# Omnigent Conformance and Live Smoke

**Document Class:** Canonical declarative
**Status:** Current
**Updated:** 2026-08-19
**Authority:** MoonLadderStudios/MoonMind#3508 browser-to-host product acceptance, MoonLadderStudios/MoonMind#3480 cumulative-remediation evidence contract, MoonLadderStudios/MoonMind#3642 native Workflow Chat release matrix, and MoonLadderStudios/MoonMind#3710 exact-artifact CI and verification tiers

MoonMind uses the versioned profile at
`tests/fixtures/omnigent/conformance-v4.json` as the single inventory for the
Omnigent bridge conformance program. Deterministic unit, fake-server, API, and
frontend tests run in normal PR CI. Credentialed stock-image, static Compose
Codex OAuth, and on-demand Codex OAuth cases remain provider verification and
must emit results for the same case identifiers.

## Terminal evidence

Every complete run produces one JSON report with the profile and report schema
versions, immutable server and host image references, host architecture, auth
mode, advertised capabilities, every declared case result, and durable evidence
references. Generate it from runner evidence with:

```bash
python tools/build_omnigent_conformance_report.py \
  artifacts/omnigent-conformance/runner-evidence.json \
  artifacts/omnigent-conformance/report.json
```

The report gate fails when either stock image is not identified by an immutable
`sha256` digest, a profile case is absent, an undeclared case is present, a case
status is invalid, or secret-like content occurs anywhere in report evidence.
Unknown fixture versions must explicitly declare whether consumers fail or
degrade; the profile itself fails closed on unknown versions.

The deterministic runner executes the unit/fake/API and Workflow Detail suites
and emits both `runner-evidence.json` and `report.json`:

```bash
python tools/run_omnigent_conformance.py \
  --server-image fake-server@sha256:<64-hex-digest> \
  --host-image fake-host@sha256:<64-hex-digest> \
  --host-architecture linux/amd64
```

The aggregate report command defaults to the complete live gate: a failed case
or a skipped critical case returns nonzero. `--allow-partial` is reserved for
the deterministic runner, where all skipped provider cases remain explicit in
the report. The canonical profile includes
`workflow-chat.native-release-matrix`, so the documented/default `--mode all`
report always carries the protected Workflow Chat result instead of leaving it
only in the dedicated mode report.

## Live-run boundaries

The controlling product case begins with the normal `/api/executions` create
payload produced by `/workflows/new`. Its evidence must bind authored intent and
the task-input snapshot to compilation as `external/omnigent`, the selected
Provider Profile, execution profile, policy/host mode, and workspace authority.
It then follows the production Temporal/activity route and the Workflow Detail
and SSE read surfaces. A raw `AgentExecutionRequest`, manually selected host, or
action-adapter-only execution cannot satisfy this case.

The static runner uses canonical `docker-compose.yaml` and the
`omnigent-host-codex` profile. The on-demand runner uses the production Provider
Profile lease and host lifecycle. Both must validate the already-enrolled OAuth
profile without reading or archiving its contents. A successful run proves one
first-message post, active events, terminal snapshot and resource harvest,
Workflow Detail replay after host removal or restart, and cleanup of only the
lease-owned host/state. Credential volumes and unrelated containers or volumes
must survive cleanup. Provider Profile lease release is the last lifecycle
action.

Failure cases archive bounded redacted diagnostics and lifecycle events. Before
publication, the report gate scans the aggregate evidence; runners must also
scan their raw logs, Temporal history export, screenshots, and archive manifest,
and reference those scan results from `failures.lifecycle-and-redaction`.

The credentialed entrypoint is `tools/run_omnigent_live_conformance.py`. It
requires immutable image references and an already-enrolled OAuth profile:

```bash
MOONMIND_OMNIGENT_ACTION_COMMAND=/path/to/live-action-adapter \
python tools/run_omnigent_live_conformance.py --mode all \
  --server-image ghcr.io/omnigent-ai/omnigent-server@sha256:<digest> \
  --host-image ghcr.io/omnigent-ai/omnigent-host@sha256:<digest> \
  --source-commit "$(git rev-parse HEAD)"
```

The runner requires `MOONMIND_OMNIGENT_ACTION_COMMAND` to name an
operator-provisioned adapter that performs the real live actions. No
repository semantic backend is accepted as live evidence. Action responses must include
durable `evidenceRefs` using `https` or
run-output-scoped `file` URLs. Each referenced JSON document uses
`moonmind.omnigent.action-evidence/v1`, names the scenario and action, records
`observed: true`, and repeats any returned durable identifiers. The runner
resolves and secret-scans every document and rejects missing, malformed,
mismatched, or opaque references. Product and failure documents must also carry
`sourceRecords`; every record names its production record type, durable ref, and
SHA-256 digest. The runner requires action-specific records (for example the
create request and authored workflow for `workflow_created`, Temporal history,
host binding, and profile lease for `temporal_routed`, and injection control,
terminal projection, and side-effect audit for every failure). Bare success
booleans and repository-synthesized identifiers are rejected as evidence.

Runs use the isolated `moonmind-test-omnigent-live` Compose project. Cleanup
removes that project's containers and networks only; it intentionally never
passes `--volumes`, so enrolled OAuth and unrelated volumes survive. The live
runner always attempts cleanup and evidence scanning, including after a failed
startup or journey. `--mode browser` is the controlling #3508 journey. It
drives `/workflows/new` through an operator-provisioned headless browser and
independently proves the static, restart/replay, on-demand, repository read,
controlled mutation, admission failure, host-readiness failure, cancellation,
and cleanup-reconciliation rows. Every row resolves the full profile, policy,
runtime, host, session, workspace, artifact, cleanup, janitor, and lease
authority chain and fails on any direct-Codex or alternate-authority fallback.

`--mode workflow_chat` is the #3642 protected controller. It runs the four
native-conversation, scoped-transport/resource, authority/security-denial, and
terminal/evidence/continuation actions in order against the digest-pinned stock
host. It resolves the typed source records returned by the live action adapter,
derives assertions from those records, scans the logs, Temporal history,
screenshots, and archives, builds and validates the commit-bound
`moonmind.omnigent.workflow-chat-acceptance/v1` artifact, and then invokes the
dedicated provider gate. A missing row, source record, raw evidence channel,
digest correlation, or provider-test result fails the mode before its evidence
can enter the publication job. The final publication-tree scan runs after
per-mode cleanup and final report generation, and therefore covers the cleanup
logs and the exact report tree uploaded by the workflow.

`--mode static` covers restart and replay; `stock`, `product`, `cumulative`,
`ondemand`, `failures`, and `workflow_chat` can be gated independently in
provider environments.

The cumulative mode is the controlling gate for #3480. It begins at the same
normal create boundary as the product mode and records authored state,
`external/omnigent` compilation, and the exact selected profile. It then proves
C0 → C1 → C2 across distinct workspaces, leases, hosts, sessions, and first
messages. The attempt-one source workspace, process, session, host, and
host-local state are removed after C1 is durable and before attempt two
restores it. Verification is read-only, Workflow Detail remains available
after cleanup, and Provider Profile release is the final owned side effect.

The same evidence records idempotent terminal control-stop continuation,
preservation of prior side effects, the integrated failure matrix, and canary,
disable-new-selection, rollback, historical-read, and worker-version replay
outcomes. A missing or false assertion fails publication; there is no
fresh-root, alternate-profile, direct-Codex, or lower-level fallback.

New control-stop destinations are disabled unless
`FEATURE_FLAGS__CONTROL_STOP_CONTINUATION_ENABLED=true`, shadow mode is false,
and `FEATURE_FLAGS__CONTROL_STOP_CONTINUATION_GENERATION` matches the frozen
contract generation. The comma-separated `CANARY_OWNER_IDS`,
`ALLOWED_PROVIDER_PROFILE_IDS`, `ALLOWED_EXECUTION_PROFILE_REFS`, and
`ALLOWED_LAUNCH_POLICY_REFS` values under the same
`FEATURE_FLAGS__CONTROL_STOP_CONTINUATION_` prefix are exact allowlists.
Disabling the feature or changing its generation blocks new admissions without
changing replay or historical reads for already-started destinations.

## Verification tiers

Omnigent verification is separated into four tiers with distinct reliability and
credential needs (MoonLadderStudios/MoonMind#3710). A higher-tier outage never
flips a lower required tier closed, but it does keep rollout readiness closed
where the protected tier is required.

- **Tier 1 — required noncredentialed exact-artifact conformance.** The
  `omnigent-exact-artifact` job in `.github/workflows/pytest-unit-tests.yml`
  runs for every affected PR on standard Docker-enabled merge infrastructure
  with no protected credentials. It builds the exact deployable image, tests it
  by immutable digest through its real entrypoints — the in-image capability
  probe (`tools/omnigent_exact_artifact_probe.py`, which proves Uvicorn resolves
  an installed WebSocket implementation so a #3697-style drop fails closed),
  HTTP/SSE/WebSocket route handshakes, clean and prior-schema PostgreSQL
  migrations, worker task-queue/readiness advertisement, and the compiled
  native UI consuming the hosted bootstrap with no root `/v1/*` traffic — and
  drives a bounded fake-provider execution plus restart/terminal replay after
  the fake host is removed. `tools/run_omnigent_exact_artifact_conformance.py`
  is the authoritative fail-closed decision
  (`moonmind.omnigent.exact_artifact_conformance`). Dependency and lockfile
  changes always select this gate, and the `ci-required` aggregator fails when
  it is selected but skipped, cancelled, or neutral.
- **Tier 2 — credentialed protected provider canary.** The credentialed
  publication gate below, run on the dedicated
  `omnigent-provider-verification` runner.
- **Tier 3 — post-deployment synthetic.** A bounded disposable execution
  against the deployed commit and actual image digests through the normal
  product API, requiring no operator to copy session identifiers.
- **Tier 4 — scheduled soak and failure matrix.** Broader browser, product,
  cumulative-remediation, restart, cleanup, and fault scenarios.

`moonmind.omnigent.live_verification_health` projects `tier1Ready` and
`protectedTierReady` from non-secret runner/queue/freshness/matrix signals; the
publication/readiness support gate reuses `rolloutReady` so a Tier-4 outage
fails rollout closed while leaving Tier 1 protecting PRs.

## Credentialed CI publication

`.github/workflows/omnigent-live-conformance.yml` is the scheduled and manually
dispatchable publication gate for MoonLadderStudios/MoonMind#3508. It runs on a
dedicated `omnigent-provider-verification` self-hosted runner so the enrolled
OAuth profile and live action adapter remain outside GitHub-hosted workers. The
protected environment supplies the adapter command; repository variables supply
the digest-pinned server and host images plus the four bounded evidence-channel
paths. Manual dispatch may override the two image references, but the workflow
rejects mutable references before provider execution.

Browser-controlled release rows, normal-create product journey, cumulative remediation, stock proxy, static
restart/replay, on-demand lifecycle, and failure/redaction run as independent
matrix jobs. Each job uploads evidence even on failure. The publication job
runs only after all six
jobs pass, combines their reports, and uploads a
`moonmind.omnigent.product-acceptance/v1` manifest with the seven report trees as
the durable GitHub Actions artifact. It links that passing report from #3508
and parent #3448. The manifest expires after 30 days; missing, expired,
malformed, mutable-image, incomplete-row, or incomplete-authority evidence
keeps the release gate closed. A configured workflow, fixture-generated success, or an
individual passing case is not published acceptance evidence.

Omnigent selection remains evidence-gated and must not become a general default
until this report passes for the deployed commit and immutable images. Canary
enablement is an explicit execution-profile/policy choice. Rollback disables
new Omnigent selection while preserving Workflow Detail reads for historical
bridge records; choosing direct Codex is a separate product/deployment decision,
never a per-request fallback. Cleanup failures retain `janitorRequired` evidence,
and the Provider Profile is released only after credential-consuming host cleanup.
