# Omnigent Conformance and Live Smoke

**Document Class:** Canonical declarative
**Status:** Current
**Updated:** 2026-07-22
**Authority:** MoonLadderStudios/MoonMind#3456 product acceptance and conformance evidence contract

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
the report.

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
  --host-image ghcr.io/omnigent-ai/omnigent-host@sha256:<digest>
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
startup or journey. `--mode static` covers restart and replay; `stock`,
`product`, `ondemand`, and `failures` can be gated independently in provider
environments.

## Credentialed CI publication

`.github/workflows/omnigent-live-conformance.yml` is the scheduled and manually
dispatchable publication gate for MoonLadderStudios/MoonMind#3456. It runs on a
dedicated `omnigent-provider-verification` self-hosted runner so the enrolled
OAuth profile and live action adapter remain outside GitHub-hosted workers. The
protected environment supplies the adapter command; repository variables supply
the digest-pinned server and host images plus the four bounded evidence-channel
paths. Manual dispatch may override the two image references, but the workflow
rejects mutable references before provider execution.

Normal-create product journey, stock proxy, static restart/replay, on-demand
lifecycle, and failure/redaction run as independent matrix jobs. Each job
uploads evidence even on failure. The publication job runs only after all five
jobs pass, combines their reports, and uploads a
`moonmind.omnigent.product-acceptance/v1` manifest with the five report trees as
the durable GitHub Actions artifact. It links that passing report from #3456
and parent #3448. A configured workflow, fixture-generated success, or an
individual passing case is not published acceptance evidence.

Omnigent selection remains evidence-gated and must not become a general default
until this report passes for the deployed commit and immutable images. Canary
enablement is an explicit execution-profile/policy choice. Rollback disables
new Omnigent selection while preserving Workflow Detail reads for historical
bridge records; choosing direct Codex is a separate product/deployment decision,
never a per-request fallback. Cleanup failures retain `janitorRequired` evidence,
and the Provider Profile is released only after credential-consuming host cleanup.
