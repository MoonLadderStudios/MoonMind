# Codex via Omnigent Support and Cutover

**Contract version:** `moonmind.codex-omnigent-cutover/v1`
**Support-matrix version:** `codex-omnigent-support-matrix/v1`

This owns Codex support evidence and retained cutover diagnostic contracts;
selection and retirement controls stay with their providing code owners.
“Supported” means repository evidence or a protected live
artifact independently proves the row. Code presence alone is “implemented” or
“unverified,” never “supported.” The Codex harness is a first-class choice in
the normal Create catalog; readiness may steer an operator to connect OAuth or
repair a live dependency. Missing optional release evidence does not hide or
disable ordinary admission. Current defaults are resolved by the shared runtime-target selector and
its surviving rollout controls, not the retained v1 phase calculation. Ordinary
admission can truthfully record an uncertified tier; explicit strict certification
still requires its actual evidence. The end-to-end product-path sequence, field
authority, and complete failure matrix that this support state qualifies are
reconciled in
[Normal Codex product-path reconciliation](./NormalCodexProductPathReconciliation.md).

Ownership note: this document owns the Codex support evidence and retained cutover
diagnostic/evidence phases. The versioned rollout mechanism that
promotes every combination (states, canary, rollback, migration status) is
owned by [Runtime-Provider Rollout Policy](./RuntimeProviderRollout.md).

> Ordinary new work (Workflow Create,
> schedule create) resolves through the one shared runtime-target selection
> boundary (`moonmind.workflows.executions.new_work_runtime`), not through
> `MOONMIND_CODEX_OMNIGENT_CUTOVER_PHASE` /
> `MOONMIND_CODEX_OMNIGENT_DEPLOYED_PHASE` or `effective_phase()` /
> `select_runtime()`. The surviving rollback controls for future admission are
> the bounded direct-retirement cutoff `MOONMIND_CODEX_DIRECT_RETIRED_AT`
> (`moonmind.omnigent.codex_cutover_drain`), the code-owned retirement class
> (`moonmind.omnigent.cutover.assert_runtime_new_admission`), and the
> deployment-owned rollback list
> `MOONMIND_OMNIGENT_RUNTIME_PROVIDER_ROLLBACK`. Lowering a retired phase value
> no longer rolls back new-work defaults; use the surviving controls. Phase
> inputs below still serve the live catalog `cutover` diagnostic/evidence
> projection and historical interpretation. They do not select new-work defaults.

## Compatibility inventory

| Direct Codex surface | Classification | Owner and supported behavior | Evidence contract / control | Removal condition | Rollback implication |
| --- | --- | --- | --- | --- | --- |
| Persisted sessions, provenance, event journal, artifacts and checkpoints | required historical-read compatibility | API read model and Workflow Detail render truthful `codex_direct_compat` and legacy evidence without a live worker | `tests/unit/omnigent/test_direct_compat_historical_reads.py` (pure journal read via `OmnigentBridgeSessionStore.list_event_page`) plus configured retention inventory; always readable | retention has elapsed and lossless migration or approved archival is proven | never rewrite provider, host, or runner identity |
| Recorded and in-flight workflow/activity payloads | required in-flight Temporal-history compatibility | Temporal workers retain decoders, activity bindings, and patch behavior needed by supported histories | `tests/unit/workflows/temporal/test_run_replayer.py::test_github_3518_cutover_runtime_parameter_histories_replay` replays pre-/post-cutover start payloads on one current worker; `::test_github_3518_cutover_selection_never_runs_inside_workflow_code` keeps selection at the submission boundary | no open history can schedule/retry the binding and retention policy permits removal | roll back only to a worker version that replays the same histories |
| `moonmind.codex_direct_compat.v1` event producer | temporary bridge-event compatibility producer | direct managed-session adapter emits normalized events with direct provenance; it is not Omnigent | explicit `parameters.communication.mode=omnigent_bridge`; parity and deduplication tests | profile-bound Omnigent coverage, projection parity, historical retention, and no schedulable compatibility activity | retain while rollback can restore direct launch |
| Explicit direct Codex selection | explicit compatibility choice during a bounded migration window | shared selection and admission owners preserve explicit intent; no implicit fallback from explicit Omnigent | current rollout controls, direct-retirement cutoff, and code-owned admission class | affected new admission is closed and its actual retained consumers are handled | only surviving authorized controls can restore a supported choice; lowering a diagnostic phase does not change defaults |
| Direct/Omnigent comparison fixtures | test fixture/comparison substrate | conformance owners retain bounded fixtures and `dual_write` comparison diagnostics | hermetic conformance and replay suites | may be archived after direct retirement; keep minimal historical decoder fixtures | no production scheduling authority |
| Direct launch worker, launch-only UI, readiness/config flags and duplicate capacity paths | obsolete launch/configuration path eligible for removal | runtime owners stop affected new admission; Provider Profile ledger remains the mutable credential-capacity owner | current admission controls and owner-specific active-work, history, and cleanup checks | no supported new work can schedule the removed path; required histories, reads, and recovery remain safe | retain an executable path only for an actual consumer or authorized rollback; diagnostic phase 6 does not authorize deletion |

## Versioned rollout

The retained v1 cutover evidence and diagnostic projection use the numbered
phase vocabulary below. These original meanings decode existing observations;
they no longer select current Create or schedule defaults. New-work selection
uses the shared rollout boundary without these phase fields, and
ordinary admission is certificate independent; see
[Runtime Provider Rollout](RuntimeProviderRollout.md#9-configuration) and
[Conformance and Live Smoke](ConformanceAndLiveSmoke.md#deployed-release-support-evidence).

1. `opt_in`: Omnigent is normally available for explicit selection; direct Codex remains the preselected default.
2. `create_default`: new Create defaults to Omnigent; explicit direct remains.
3. `schedule_default`: schedules and presets use versioned Omnigent defaults.
4. `broad_default`: all eligible new Codex work defaults to Omnigent.
5. `direct_launch_disabled`: new direct work is rejected; reads remain.
6. `direct_launch_removed`: launch/UI/config code is removed; readers remain for their retention contract.

For this retained diagnostic/evidence contract,
[`moonmind/omnigent/cutover.py`](../../moonmind/omnigent/cutover.py) permits only
one-step phase promotion with a fresh, version-matched evidence artifact. It requires
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

`GET /api/omnigent/codex-catalog-readiness` still calls `effective_phase()` and
returns its result under `cutover`. That live diagnostic/evidence consumer reads
`MOONMIND_CODEX_OMNIGENT_CUTOVER_PHASE` (default `opt_in`) as the desired phase
and `MOONMIND_CODEX_OMNIGENT_DEPLOYED_PHASE` (default `opt_in`) as the recorded,
operator-controlled deployed phase. Its promotion calculation accepts only the
immediate successor with evidence naming both values. A denied promotion retains
the deployed diagnostic phase; a lower desired value reports the earlier phase.
Neither result changes current new-work defaults, and these fields are absent
from the shipped Compose/template configuration. For a diagnostic promotion,
the reader also loads the JSON document at the
local path or `file://` URI in
`MOONMIND_CODEX_OMNIGENT_CONFORMANCE_EVIDENCE_REF`. Remote and opaque artifact
references are evidence links, not launch authority. The mounted document must
authorize the exact desired phase and current deployed phase and pass the
complete gate; otherwise the effective phase remains the deployed phase.
The canonical Compose deployment mounts
`MOONMIND_CODEX_OMNIGENT_EVIDENCE_DIR` (default `./var/cutover`) read-only at
`/workspace/cutover`; the evidence builder stages digest-bound artifacts beside
the promotion document so its relative manifest refs remain readable in the
container.
Phase 6 additionally requires explicit code, UI, configuration, and duplicate
capacity-owner removal assertions plus independently resolvable retirement
evidence refs. It also remains compile-time blocked by
`DIRECT_LAUNCH_REMOVAL_VERSION` until the cohesive retirement change removes
those paths and enables its absence guards; protected-live evidence alone
cannot authorize removal. The single deployment-owned retirement cutoff
`MOONMIND_CODEX_DIRECT_RETIRED_AT`
(`moonmind.omnigent.codex_cutover_drain`) closes the direct lane to new work
once its instant passes, regardless of phase; unset preserves the usable path
until its qualified replacement is available. The bounded drain procedure in
the same module inventories schedules, queued starts, open parent/child
workflows, retries, pending interventions, serialized launch inputs, resource
leases, and publication/cleanup work with clean/active/unknown/blocked
reporting, and records the explicit retained-history disposition per branch
with its consumer and removal condition. Operators inject live Temporal
visibility, schedule, lease, publication, and cleanup queries through
`run_live_codex_drain`/`operator_drain_sources`, which normalize
heterogeneous rows to the same four states (raising sources become
`unknown`); the report itself never terminates workflows, erases evidence,
or deletes credential volumes. The API's `cutover` projection publishes desired/deployed/effective phase,
policy/profile versions, generation/expiry, image digests, architectures,
thresholds, evidence refs, blockers, and direct-launch status in
`/api/omnigent/codex-catalog-readiness`. Current Create/schedule defaults follow
the shared selection owner rather than `create_default` or `schedule_default`.
The API retains the serialized `runtimeCutover` key for passive new-work
target/policy provenance from `new_work_evidence`, without a phase gate; old
phase-bearing records keep their meaning. Invalid phase inputs can fail the
diagnostic reader, not select another execution path. Future admission rollback
uses the surviving controls; historical run evidence is never rewritten.

Catalog `gateReasons` are operational launch blockers. Protected acceptance
matrix gaps are published separately as `supportGateReasons`: they qualify
the declared certification scope, but do not independently select defaults or deny an otherwise safe ordinary
launch. A first ordinary launch does not require prior session observations or
the protected browser acceptance, exact-artifact, and live-health files; those
signals remain visible in `admissionReadiness.capabilities` and
`supportGateReasons`. Runtime registration, schema, WebSocket, worker/backend,
janitor, profile, and policy checks still gate admission. The workflow
supervisor reports readiness and registration for its child workers;
worker build identities are recorded independently and do not have to match for
new admission. The canonical Compose path enables the bridge and internal
endpoint by default, seeds an active portable `codex` agent profile, resolves
stock image tags to immutable policy digests, and uses the on-demand policy
unless an operator explicitly selects static hosting. The API synchronizes the stable
upstream agent identity before activating that profile, keeps its projection
fresh, and retries transient image, bridge, or inventory startup failures with
capped backoff; no service restart is required for recovery.

## Support and conformance matrix v1

This section describes the retained protected v1 certification scope, not
ordinary admission or a requirement to restore removed runtime capabilities.
The stable machine-readable row inventory is the versioned catalog
`moonmind.omnigent.cutover.REQUIRED_ROW_CATALOG` (support-matrix version
`codex-omnigent-support-matrix/v1`). Each `MatrixRow` binds one protected row
to exactly one owning evidence kind and the host modes and runtime provenances
under which it may be observed; `REQUIRED_MATRIX_ROWS` is its ordered row-ID
projection. Row ownership is fixed by this catalog, never by a caller-supplied
list.

Protected-live owners publish one passing
`moonmind.codex-omnigent-cutover-artifact/v2` document for each required
evidence kind. Every artifact declares a `producerVersion` and a `rows` array
of observed-evidence objects; each object carries the owned `row`, `hostMode`,
`architecture`, immutable `images`, `profileVersion`/`profileSha256`,
`launchPolicyVersion`, `agentProfileVersion`, `runtimeProvenance`, `observedResult`,
and `secretScan`. The `secretScan` is per-channel evidence, not a self-asserted
string: it maps every required conformance evidence channel (`logs`,
`temporalHistory`, `screenshots`, `archives`) to a `{status: "passed",
evidenceRef}` object bound to a resolvable ref, mirroring
`moonmind.omnigent.conformance.build_report`.
`moonmind.omnigent.cutover.validate_matrix_artifact` rejects
any artifact whose rows are unknown, foreign to the artifact's kind, observed
on the wrong host mode or runtime provenance, produced for different images or
profile/policy/agent-profile versions, carrying a self-asserted or incomplete
secret scan, or not observed as `passed`. Architecture membership is not enough:
when the release declares more than one architecture, each owned row must carry
its own passing observation for **every** released architecture, or the row is
left unproven and promotion fails closed. A bare `passed` boolean or a
self-declared row name is never sufficient.

Run `tools/build_codex_omnigent_cutover_evidence.py --release RELEASE.json
--artifact ARTIFACT.json ... --output promotion.json` to assemble the mounted
promotion document. `RELEASE.json` supplies the immutable images, architecture,
`launchPolicyVersion`, `agentProfileVersion`, telemetry, and thresholds; the builder
derives the pass booleans, owned rows, `matrixRows`, evidence URIs, and SHA-256
digests from the validated observed evidence. It fails on a missing row or
kind, an artifact that fails per-row validation, duplicate ownership, incomplete
telemetry, or a failed threshold. Generating the document does not turn a
pending matrix row into supported evidence; its owning artifact must contain the
protected observed result.

At diagnostic phase promotion the retained evidence reader
(`moonmind.omnigent.cutover.effective_phase`) independently re-resolves every
manifest ref, binds its bytes to the recorded digest, **and re-parses each
artifact to re-validate its observed per-row evidence** against the promotion
document's declared images, architectures, and profile/policy/agent-profile
versions. It also binds each evidence kind to exactly one artifact: two
digest-valid artifacts that share a kind but own disjoint rows are rejected as
split coverage before their rows are unioned, so a hand-authored document cannot
splice partial results from separate runs into apparent completeness. Digest
integrity alone never authorizes a phase: a self-asserted, mismatched,
incomplete, split-kind, or coverage-short artifact leaves the affected rows
unsupported and the effective phase at the deployed phase. The release-status
projection additionally publishes the support-matrix version
(`matrixVersion`), covered `matrixRows`, `launchPolicyVersion`, and
`agentProfileVersion` alongside the evidence generation, expiry, image digests,
and architecture already recorded there.

| Capability | Mode(s) | Status | Independently resolvable evidence / gate |
| --- | --- | --- | --- |
| OAuth Provider Profile readiness and shared capacity | static, on-demand | implemented; live support pending | [`CodexCreateToHostContract.md`](./CodexCreateToHostContract.md), `tests/unit/omnigent/test_oauth_profile_lifecycle.py`; protected live matrix required |
| Stock proxy bridge | stock server/host | implemented; live support pending | [`ConformanceAndLiveSmoke.md`](./ConformanceAndLiveSmoke.md), `tests/integration/omnigent/test_bridge_proxy_fake_server.py`; immutable-image live artifact required |
| Embedded compatibility bridge | unchanged stock host | experimental, not default | [`EmbeddedHostAuthCompatibility.md`](./EmbeddedHostAuthCompatibility.md), `tests/integration/omnigent/test_embedded_projection_conformance.py`; separate production gate required |
| Static and on-demand Codex host | Compose, Docker backend | implemented; live support pending | [`CombinedStackValidationAndRollback.md`](./CombinedStackValidationAndRollback.md), `tests/provider/omnigent/test_omnigent_smoke.py` |
| Create/edit/rerun/schedule/preset | authored external/omnigent request | Create implemented; strict full matrix pending | [`CodexCreateToHostContract.md`](./CodexCreateToHostContract.md), `tests/unit/services/test_omnigent_execution_plan_service.py`; protected end-to-end artifact required for the strict claim |
| Repository read, mutation, publication | workspace locator and mounted tools | implemented; live support pending | [`OmnigentHostMountedTools.md`](./OmnigentHostMountedTools.md), conformance case evidence required |
| Workflow Detail live/replay/resources/controls | proxy and direct compatibility events | supported hermetically; live Omnigent pending | `tests/integration/omnigent/test_bridge_conformance.py`, `tests/integration/omnigent/test_execute_fake_server.py` |
| Cancellation, timeout, failure, cleanup, janitor | static/on-demand | implemented; live support pending | `tests/integration/omnigent/test_execute_fake_server.py`, `tests/unit/omnigent/test_oauth_host_janitor.py`; live lifecycle artifact required |
| Checkpoint capture, reattach, cold restore, branches | external-state plus workspace evidence | partially implemented; unsupported as a complete matrix | [`CheckpointBranchSystem.md`](../Workflows/CheckpointBranchSystem.md); capture/restore/branch live evidence required |
| Failed-run remediation | ordinary Omnigent workflow with embedded failure evidence | implemented; a remediation never acts on its target | [`WorkflowRemediation.md`](../Workflows/WorkflowRemediation.md); the `remediation.operator-autonomous-gate` row stays in the protected v1 catalog (`moonmind/omnigent/cutover.py`) until its release owner retires it |
| Initial/follow-up RAG | none; native retrieval is not provided | retired; explicit `rag`/`followUpRetrieval` requests are rejected before launch | `tests/unit/config/test_vector_free_regression_4114.py`; the row stays in the protected v1 catalog (`moonmind/omnigent/cutover.py`) until its release owner retires it |
| Persistent policy and agent-profile UI | immutable selected versions | partial; unsupported as a complete matrix | persisted policy/profile UI and snapshot evidence required |
| Enforced egress | host/runtime boundary | partial; unsupported | enforcement and denial evidence required; declarations alone do not qualify |
| Architecture and images | `linux/amd64`; digest-pinned server/host | amd64 evidence contract implemented; other architectures unsupported until proven | `moonmind/omnigent/conformance.py`; each supported architecture requires immutable-image live evidence |
| Direct runtime reads and compatibility selection | historical read; explicit choices under current admission controls | compatibility retained for actual consumers | [`OmnigentBridge.md`](./OmnigentBridge.md), `tests/unit/omnigent/test_direct_compat_historical_reads.py`, `tests/unit/workflows/temporal/test_run_replayer.py` cutover replay tests, compatibility inventory above |

## Release thresholds and telemetry

Within the retained v1 certification scope, each promotion artifact records launch/readiness totals by host mode and
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
fallback, or missing required artifact has zero tolerance and fails that
certification/promotion verdict. The diagnostic does not perform a deployment
rollback or rewrite selection; real safety failures remain enforced by their
action owners and surviving explicit stop controls.
The release-status projection shows phase, evidence generation/expiry, image
digests, architecture, profile version, each threshold result, artifact refs,
and blockers.

Upstream Omnigent server and host images used as release evidence are pinned by
SHA-256 digest and validated against the conformance profile. Apache-2.0 license
and notice ownership remains as recorded in `README.md`, `LICENSE`, `NOTICE`, and
the initialized `omnigent` submodule. Adding an architecture, image, protocol,
or upstream version requires a new conformance artifact before the matrix can
mark it supported. This matrix says nothing about Claude Code support.
