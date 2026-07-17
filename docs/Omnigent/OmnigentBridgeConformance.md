# Omnigent bridge conformance

MoonMind uses one versioned Codex-first conformance profile to gate the bridge
compatibility boundary. The profile covers configuration, stock proxy routes,
first-message persistence, event replay and SSE, terminal fallback, Workflow
Detail chat, resources, lifecycle failures, direct Codex parity, cleanup, and
credential redaction. This is the validation contract for
`MoonLadderStudios/MoonMind#3368`.

The canonical fixtures are
`tests/fixtures/omnigent/conformance-profile-v1.json` and
`tests/fixtures/omnigent/stock-images-v1.json`. The latter pins published stock
server and host images by tag and immutable digest. A result is a compact JSON
report containing the profile version, exact images, case results, and artifact
references. Unknown versions fail critical cases and degrade optional cases.

## Execution policy

Pull requests run the deterministic unit, fake-server, and API matrix:

```bash
python tools/run_omnigent_conformance.py \
  --mode deterministic \
  --output artifacts/omnigent-conformance/deterministic.json
```

The scheduled or manually dispatched workflow runs against an explicitly
provisioned stock service. `stock-proxy`, `static-compose`, and `on-demand`
modes require `OMNIGENT_SERVER_URL`, `OMNIGENT_API_TOKEN`, and the agent name;
OAuth modes additionally require `OMNIGENT_CODEX_OAUTH_PROFILE_REF`. The value
of a credential or profile is never written to a report or command argument.
Interactive OAuth enrollment is a prerequisite, not part of automation.

Static deployments use the canonical `docker-compose.yaml` with
`COMPOSE_PROFILES=omnigent-host-codex`. Operators must start the pinned image
refs from the fixture, wait for exact-host `codex-native` readiness, and then
run `--mode static-compose`. On-demand environments provision the same pinned
host for the leased profile before running `--mode on-demand`.

Live environments own startup and cleanup. Cleanup removes only resources
labeled for the current run, preserves credential volumes, stops the host
before releasing its Provider Profile lease, and runs on success, failure, and
cancellation. Never upload OAuth homes, environment dumps, raw container logs,
headers, screenshots containing credentials, or Temporal payload dumps. Upload
only the generated report and explicitly redacted bounded diagnostics.

The deterministic job is required. Published-image and OAuth jobs are advisory
provider verification gates until their environment is explicitly provisioned;
a failure blocks claims that the corresponding live layer is conformant.
