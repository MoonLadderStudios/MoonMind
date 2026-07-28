# Codex via Omnigent Support and Cutover

**Contract version:** `moonmind.codex-omnigent-cutover/v1`
**Source:** MoonLadderStudios/MoonMind#3518

This is the canonical support, compatibility, and retirement policy for Codex
through Omnigent. “Supported” means repository evidence or a protected live
artifact independently proves the row. Code presence alone is “implemented” or
“unverified,” never “supported.” The current release phase is **1 — opt-in**:
the repository does not contain the complete protected-live acceptance artifact,
so promotion is fail-closed.

## Compatibility inventory

| Direct Codex surface | Classification | Owner and supported behavior | Evidence contract / control | Removal condition | Rollback implication |
| --- | --- | --- | --- | --- | --- |
| Persisted sessions, provenance, event journal, artifacts and checkpoints | required historical-read compatibility | API read model and Workflow Detail render truthful `codex_direct_compat` and legacy evidence without a live worker | historical-read tests plus configured retention inventory; always readable | retention has elapsed and lossless migration or approved archival is proven | never rewrite provider, host, or runner identity |
| Recorded and in-flight workflow/activity payloads | required in-flight Temporal-history compatibility | Temporal workers retain decoders, activity bindings, and patch behavior needed by supported histories | replay fixtures and mixed-version deployment evidence | no open history can schedule/retry the binding and retention policy permits removal | roll back only to a worker version that replays the same histories |
| `moonmind.codex_direct_compat.v1` event producer | temporary bridge-event compatibility producer | direct managed-session adapter emits normalized events with direct provenance; it is not Omnigent | explicit `parameters.communication.mode=omnigent_bridge`; parity and deduplication tests | profile-bound Omnigent coverage, projection parity, historical retention, and no schedulable compatibility activity | retain while rollback can restore direct launch |
| Explicit direct Codex selection | operator-selectable fallback during a bounded migration window | workflow compiler schedules direct only when the operator selected it or an explicit automatic strategy records `selectedPath` and `fallbackReason` | rollout phases 1–4; no implicit fallback from explicit Omnigent | phase 5 promotion evidence passes | phase rollback restores selection for new work without changing existing run snapshots |
| Direct/Omnigent comparison fixtures | test fixture/comparison substrate | conformance owners retain bounded fixtures and `dual_write` comparison diagnostics | hermetic conformance and replay suites | may be archived after direct retirement; keep minimal historical decoder fixtures | no production scheduling authority |
| Direct launch worker, launch-only UI, readiness/config flags and duplicate capacity paths | obsolete launch/configuration path eligible for removal | runtime owners disable scheduling first; Provider Profile ledger remains the sole mutable credential-capacity owner | phase 5 disables; phase 6 removes after history, rollback, and release gates | no supported new work can schedule direct, rollback no longer needs it, histories replay, and historical reads pass | removal is irreversible within the release; do not promote phase 6 while rollback depends on it |

## Versioned rollout

The persisted rollout value is the numbered phase, not an inferred default:

1. `opt_in`: internal/explicit Omnigent selection only.
2. `create_default`: new Create defaults to Omnigent; explicit direct remains.
3. `schedule_default`: schedules and presets use versioned Omnigent defaults.
4. `broad_default`: all eligible new Codex work defaults to Omnigent.
5. `direct_launch_disabled`: new direct work is rejected; reads remain.
6. `direct_launch_removed`: launch/UI/config code is removed; readers remain for their retention contract.

[`moonmind/omnigent/cutover.py`](../../moonmind/omnigent/cutover.py) permits only
one-step promotion with a fresh, version-matched evidence artifact. It requires
profile/policy readiness, all required cases, raw-channel secret scans, replay,
historical reads, single capacity ownership, objective thresholds, and nonempty
artifact refs. Every ref must also appear exactly once in the provenance-bound
evidence manifest with a lowercase SHA-256 digest and one of the required
submission-matrix, historical-read, Temporal-replay, capacity-ownership,
secret-scan, or release-metadata evidence kinds. At promotion time every
manifest ref must resolve to a deployment-local file (absolute, `file://`, or
relative to the conformance document), and the SHA-256 digest of its bytes must
match the manifest. Missing, unreadable, digest-mismatched, malformed, older
than seven days, incomplete, or over-threshold
evidence blocks promotion. Rollback to an earlier phase is always allowed and
does not mutate immutable per-run runtime/profile/policy snapshots. A denied or
failed explicit Omnigent selection is an error; it never invokes direct Codex.

The API reads `MOONMIND_CODEX_OMNIGENT_CUTOVER_PHASE` (default `opt_in`) as a
desired phase and `MOONMIND_CODEX_OMNIGENT_DEPLOYED_PHASE` (default `opt_in`)
as the durable, operator-controlled current phase. Promotion is valid only to
the immediate successor of that deployed phase; the evidence document must
name both values. After deployment succeeds, operators advance the deployed
value. A denied promotion preserves the deployed phase, while an explicit
lower desired value performs a rollback for future defaults. For every
promotion it also reads the JSON document at the
local path or `file://` URI in
`MOONMIND_CODEX_OMNIGENT_CONFORMANCE_EVIDENCE_REF`. Remote and opaque artifact
references are evidence links, not launch authority. The mounted document must
authorize the exact desired phase and current deployed phase and pass the
complete gate; otherwise the effective phase remains the deployed phase.
Phase 6 additionally requires explicit code, UI, configuration, and duplicate
capacity-owner removal assertions plus independently resolvable retirement
evidence refs. It also remains compile-time blocked by
`DIRECT_LAUNCH_REMOVAL_VERSION` until the cohesive retirement change removes
those paths and enables its absence guards; protected-live evidence alone
cannot authorize removal. The API publishes desired/deployed/effective phase,
policy/profile versions, generation/expiry, image digests, architectures,
thresholds, evidence refs, blockers, and direct-launch status in
`/api/omnigent/codex-catalog-readiness`. Create/edit/rerun defaults
advance at `create_default`; schedule and preset defaults advance at
`schedule_default`. Every created execution stores `runtimeCutover` beside its
runtime/profile/model evidence. Invalid phases and explicit direct launches at
or after `direct_launch_disabled` fail before Temporal submission. Rollback
changes only future default selection and never rewrites existing run evidence.

## Support and conformance matrix v1

The stable machine-readable row inventory is
`moonmind.omnigent.cutover_conformance.REQUIRED_MATRIX_ROWS`. Protected-live
owners publish one passing
`moonmind.codex-omnigent-cutover-artifact/v1` document for each required
evidence kind, with disjoint `matrixRows`. Run
`tools/build_codex_omnigent_cutover_evidence.py --release RELEASE.json
--artifact ARTIFACT.json ... --output promotion.json` to assemble the mounted
promotion document. The builder fails on a missing row or kind, failed
artifact, duplicate ownership, incomplete telemetry, or failed threshold and
derives every artifact URI and SHA-256 digest from deployment-local bytes.
Generating the document does not turn a pending matrix row into supported
evidence; its owning artifact must contain the protected observed result.

| Capability | Mode(s) | Status | Independently resolvable evidence / gate |
| --- | --- | --- | --- |
| OAuth Provider Profile readiness and shared capacity | static, on-demand | implemented; live support pending | [`CodexCreateToHostContract.md`](./CodexCreateToHostContract.md), `tests/unit/omnigent/test_oauth_profile_lifecycle.py`; protected live matrix required |
| Stock proxy bridge | stock server/host | implemented; live support pending | [`ConformanceAndLiveSmoke.md`](./ConformanceAndLiveSmoke.md), `tests/integration/omnigent/test_bridge_proxy_fake_server.py`; immutable-image live artifact required |
| Embedded compatibility bridge | unchanged stock host | experimental, not default | [`EmbeddedHostAuthCompatibility.md`](./EmbeddedHostAuthCompatibility.md), `tests/integration/omnigent/test_embedded_projection_conformance.py`; separate production gate required |
| Static and on-demand Codex host | Compose, Docker backend | implemented; live support pending | [`CombinedStackValidationAndRollback.md`](./CombinedStackValidationAndRollback.md), `tests/provider/omnigent/test_omnigent_smoke.py` |
| Create/edit/rerun/schedule/preset | authored external/omnigent request | Create implemented; full matrix pending | [`CodexCreateToHostContract.md`](./CodexCreateToHostContract.md), `tests/unit/docs/test_omnigent_create_to_host_contract.py`; protected end-to-end artifact required |
| Repository read, mutation, publication | workspace locator and mounted tools | implemented; live support pending | [`OmnigentHostMountedTools.md`](./OmnigentHostMountedTools.md), conformance case evidence required |
| Workflow Detail live/replay/resources/controls | proxy and direct compatibility events | supported hermetically; live Omnigent pending | `tests/integration/omnigent/test_bridge_conformance.py`, `tests/integration/omnigent/test_execute_fake_server.py` |
| Cancellation, timeout, failure, cleanup, janitor | static/on-demand | implemented; live support pending | `tests/integration/omnigent/test_execute_fake_server.py`, `tests/unit/omnigent/test_oauth_host_janitor.py`; live lifecycle artifact required |
| Checkpoint capture, reattach, cold restore, branches | external-state plus workspace evidence | partially implemented; unsupported as a complete matrix | [`CheckpointBranchSystem.md`](../Workflows/CheckpointBranchSystem.md); capture/restore/branch live evidence required |
| Operator/autonomous remediation | policy-bound actions | partial; autonomous gate closed | canonical remediation docs and protected action/verification evidence required |
| Initial/follow-up RAG | artifact-backed context | partial; unsupported as a complete matrix | canonical RAG docs and delivery/denial/budget evidence required |
| Persistent policy and agent-profile UI | immutable selected versions | partial; unsupported as a complete matrix | persisted policy/profile UI and snapshot evidence required |
| Enforced egress | host/runtime boundary | partial; unsupported | enforcement and denial evidence required; declarations alone do not qualify |
| Architecture and images | `linux/amd64`; digest-pinned server/host | amd64 evidence contract implemented; other architectures unsupported until proven | `moonmind/omnigent/conformance.py`; each supported architecture requires immutable-image live evidence |
| Direct runtime reads and fallback | historical read; phases 1–4 explicit fallback | compatibility supported; retirement gated | [`OmnigentBridge.md`](./OmnigentBridge.md), direct projection/replay tests, compatibility inventory above |

## Release thresholds and telemetry

Every promotion artifact records launch/readiness totals by host mode and
architecture; profile-lease, host-ready, first-message, first-event, terminal
harvest, and cleanup latency; reconnect/replay gaps; control delivery;
provider/session/host failures; artifact and capture completeness;
checkpoint/resume/branch, remediation, and RAG outcomes; stale/orphan/janitor
rates; selected direct/Omnigent path and explicit fallback reason; secret scan
violations; and policy/readiness denials. Labels contain bounded categories, not
credentials or session identifiers.

`thresholds.withinLimits=true` is produced only when the versioned release
threshold set passes. Any secret violation, provenance rewrite, duplicate
credential-capacity owner, replay failure, historical-read failure, silent
fallback, or missing required artifact has a zero tolerance and forces rollback.
The release-status projection shows phase, evidence generation/expiry, image
digests, architecture, profile version, each threshold result, artifact refs,
and blockers.

Upstream Omnigent server and host images used as release evidence are pinned by
SHA-256 digest and validated against the conformance profile. Apache-2.0 license
and notice ownership remains as recorded in `README.md`, `LICENSE`, `NOTICE`, and
the initialized `omnigent` submodule. Adding an architecture, image, protocol,
or upstream version requires a new conformance artifact before the matrix can
mark it supported. Claude-through-Omnigent parity is deferred and is not implied
by this Codex matrix.
