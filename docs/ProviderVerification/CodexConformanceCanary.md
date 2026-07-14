---
doc_type: ModuleContractSpecification
status: active
owner: reliability
last_reviewed: 2026-07-10
---

# Codex Conformance Canary

## CONTRACT-001 Live Long-Command Canary

MoonMind maintains a credentialed provider-verification canary for
`MoonLadderStudios/MoonMind#3150`. The canary runs a managed Codex session with
a harmless foreground command that outlives the first tool yield, is polled at
least once after that yield, writes `var/conformance/long_command_result.json`
only after the helper process exits, and returns a terminal result that
references that marker.

The canary must not mutate GitHub, create a pull request, or write outside the
isolated canary workspace except for the marker.

## CONTRACT-002 Compact Evidence

Each run publishes one compact JSON result using
`moonmind.codex_conformance.canary.CodexCanaryEvidence`. The result includes the
candidate image digest, Codex CLI and app-server versions, MoonMind build SHA,
scenario version, run/session/turn correlation IDs, ordered timestamps, marker
artifact ref, compact protocol event summary, and stable pass/fail reason code.

The evidence must not include raw authentication material, full environment
dumps, unrestricted transcripts, raw terminal scrollback, or provider-native
credential payloads. Secret-like values fail validation before publication.

## CONTRACT-003 Failure Classification

Provider availability and protocol conformance are separate outcomes. Provider
outages use `CANARY_PROVIDER_UNAVAILABLE`; protocol and lifecycle failures use
stable canary reason codes such as `CANARY_TOOL_PROTOCOL_INCOMPATIBLE`,
`CANARY_TERMINAL_MARKER_MISSING`, `CANARY_SESSION_TERMINATED_EARLY`,
`CANARY_PROCESS_ABANDONED`, `CANARY_DUPLICATE_EXECUTION`, and `CANARY_TIMEOUT`.

Promotion policy may decide how to handle provider outages, but it must not
classify every outage as a product conformance regression.

## CONTRACT-004 Promotion Gate

The `Release / Promote Stable` workflow blocks app image promotion unless a
fresh Codex conformance result validates for the exact candidate digest being
promoted. The accepted freshness window is 72 hours. Digest mismatches and stale
results fail closed.

PentestGPT runner promotion is not gated by the Codex canary because that image
does not carry the managed Codex runtime.

## CONTRACT-005 Last-Known-Good Rollback

Operators should retain the previous `stable` digest as the last-known-good
rollback pointer before promoting a new app digest. If the nightly canary begins
failing for the current production digest, compare the failed evidence against
the most recent passing evidence for the previous digest, then either roll back
to the last-known-good digest or hold promotion while provider availability is
confirmed.

Rollback uses the same GHCR stable promotion workflow with the last-known-good
source tag or digest and a passing conformance result for that exact digest. A
live provider outage must not block rollback to an already validated
last-known-good digest.

## TEST-001 Manual Invocation

Run the live canary only in a credentialed environment with a running MoonMind
API and a managed Codex profile:

```bash
export MOONMIND_API_URL="https://moonmind.example"
export MOONMIND_API_TOKEN="..."
export MOONMIND_CODEX_CANARY_PROFILE_REF="codex-default"
export MOONMIND_CODEX_CANARY_CANDIDATE_DIGEST="sha256:..."
tools/test_codex_provider.sh
```

For a direct local run without Compose:

```bash
python tools/run_codex_conformance_canary.py \
  --candidate-digest "$MOONMIND_CODEX_CANARY_CANDIDATE_DIGEST" \
  --candidate-ref "ghcr.io/moonladderstudios/moonmind:candidate" \
  --output artifacts/codex-conformance/canary-result.json
python -m moonmind.codex_conformance.canary check \
  --result artifacts/codex-conformance/canary-result.json \
  --candidate-digest "$MOONMIND_CODEX_CANARY_CANDIDATE_DIGEST"
```

## TEST-002 Alerting

The scheduled `Provider / Codex Conformance Canary` workflow is the production
alert surface. A previously passing production digest that begins failing
nightly requires comparing the failing result with the previous passing result
for the same digest and checking whether the reason code is provider
availability, protocol conformance, duplicate execution, stale evidence, or
marker validation.
