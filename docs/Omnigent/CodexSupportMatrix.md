# Codex-through-Omnigent support matrix

**Document Class:** Canonical declarative

**Status:** Current

**Matrix version:** `moonmind.omnigent.codex-support/v1`

**Authority:** MoonLadderStudios/MoonMind#3518

“Supported” requires both repository evidence and the protected live acceptance
report for the deployed commit, architecture and immutable image digests.
Implementation alone remains “implemented, live evidence required.” Publication
is defined in [Conformance and Live Smoke](ConformanceAndLiveSmoke.md#credentialed-ci-publication).

| Capability / combination | Status | Independently resolvable evidence |
| --- | --- | --- |
| OAuth Provider Profile readiness, lease and capacity | implemented; live evidence required | [`test_bridge_conformance.py`](../../tests/integration/omnigent/test_bridge_conformance.py), live `ondemand` report |
| stock proxy bridge | implemented; live evidence required | `stock-images.proxy`, live `stock` report |
| embedded compatibility mode | implemented; not production default | [`test_embedded_projection_conformance.py`](../../tests/integration/omnigent/test_embedded_projection_conformance.py); separate gate |
| static and on-demand OAuth hosts | implemented; live evidence required | `compose.static-codex-oauth`, `ondemand.codex-oauth`, static/ondemand reports |
| Create/edit/rerun | implemented; live evidence required | `product.normal-create-api`, live product report |
| schedules and presets | rollout-gated; not supported by v1 evidence | `scheduled_default` plus future live case IDs |
| repository read, mutation and publication | implemented; live evidence required | product source records and side-effect audit |
| Workflow Detail live/replay/resources/controls | implemented; live evidence required | `events.durable-replay-sse`, `projection.workflow-detail-chat`, `resources.authorization-and-evidence` |
| cancellation, timeout, failure, cleanup and janitor | implemented; live evidence required | `failures.*`, `cleanup.lease-owned-only`, failures/ondemand reports |
| checkpoint capture, live reattach, cold restore and Branches | implemented; live evidence required | cumulative report and replay assertion |
| operator remediation | implemented; live evidence required | `product.cumulative-remediation` |
| autonomous remediation | policy gated; not generally supported | cumulative plus autonomous-policy promotion evidence |
| initial/follow-up RAG and persistent policy/profile UI | implemented; live evidence required | product/cumulative authored-state and context records |
| enforced egress | policy gated; live evidence required | failure injection-control and side-effect audit |
| linux/amd64 digest-pinned images | candidate supported after report passes | report images and hostArchitecture |
| other architectures or mutable tags | unsupported | no qualifying v1 evidence |
| direct Codex migration fallback | compatibility only through `broad_default` | `codex.direct-event-parity` and [inventory](CodexCutoverPolicy.md#compatibility-inventory) |
| historical direct reads without worker | required compatibility; live evidence required | cumulative historical-read assertion and direct-event UI tests |
| Claude through Omnigent | deferred | outside MoonLadderStudios/MoonMind#3518 |

## Required protected-report dimensions

No row above is “supported” in v1 until a protected report resolves every
applicable combination below to a concrete case ID and immutable report/image
digest. Omitted combinations are unsupported, not inferred from another row.

| Dimension | Required values |
| --- | --- |
| host | static and on-demand, independently |
| architecture/image | `linux/amd64` plus exact server and host `@sha256` digests; every other architecture is unsupported |
| submission | Create, edit, rerun, schedule and preset |
| repository authority | read-only, mutation and publication |
| Workflow Detail | live, reconnect, cold replay, resources and each control |
| terminal/recovery | cancellation, timeout, provider failure, cleanup, janitor, checkpoint, reattach, cold restore and branch |
| authored context | operator remediation, autonomous gate, initial RAG, follow-up RAG, persistent policy and persistent profile |
| network/security | enforced egress, redaction, policy denial and readiness denial |
| migration | explicit Omnigent, automatic cohort/default and explicit direct fallback while allowed |

Upstream compatibility is the exact protocol and digest-pinned server/host
images in the report. An upgrade requires a new report for candidate digests
and architectures before promotion. Image license and notice material must be
reviewed before a row is marked supported.
