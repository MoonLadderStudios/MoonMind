# Omnigent Conformance and Live Smoke

**Document Class:** Canonical declarative
**Status:** Current
**Updated:** 2026-07-17
**Authority:** MoonLadderStudios/MoonMind#3368 conformance evidence contract

MoonMind uses the versioned profile at
`tests/fixtures/omnigent/conformance-v1.json` as the single inventory for the
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
python tools/run_omnigent_live_conformance.py --mode all \
  --server-image ghcr.io/omnigent-ai/omnigent-server@sha256:<digest> \
  --host-image ghcr.io/omnigent-ai/omnigent-host@sha256:<digest>
```

By default the runner uses the repository-owned
`tools/omnigent_live_action.py` client. The provisioned test environment sets
`MOONMIND_OMNIGENT_HARNESS_URL` and `MOONMIND_OMNIGENT_HARNESS_TOKEN`; the
harness action responses must include durable `evidenceRefs`. A custom portable
client may be selected with `MOONMIND_OMNIGENT_ACTION_COMMAND`. Bare success
booleans are rejected as evidence.

Runs use the isolated `moonmind-test-omnigent-live` Compose project. Cleanup
removes that project's containers and networks only; it intentionally never
passes `--volumes`, so enrolled OAuth and unrelated volumes survive. The live
runner always attempts cleanup and evidence scanning, including after a failed
startup or journey. `--mode static` covers restart and replay; `stock`,
`ondemand`, and `failures` can be gated independently in provider environments.
