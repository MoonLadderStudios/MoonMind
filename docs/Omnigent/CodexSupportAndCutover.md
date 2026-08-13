# Codex via Omnigent Support and Cutover

**Contract version:** `moonmind.codex-omnigent-cutover/v1`
**Support-matrix version:** `codex-omnigent-support-matrix/v1`
**Source:** MoonLadderStudios/MoonMind#3518; protected-row binding MoonLadderStudios/MoonMind#3564 (observations from MoonLadderStudios/MoonMind#3563)

This is the canonical support, compatibility, and retirement policy for Codex
through Omnigent. “Supported” means repository evidence or a protected live
artifact independently proves the row. Code presence alone is “implemented” or
“unverified,” never “supported.” Codex via Omnigent is a first-class runtime in
the normal Create catalog; readiness may steer an operator to connect OAuth or
repair a live dependency, but release evidence does not hide or disable the
runtime. The current default-selection phase is **1 — explicit selection**:
the repository does not contain the complete protected-live acceptance artifact,
so promotion to the preselected Codex default remains fail-closed. The end-to-end product-path sequence, field
authority, and complete failure matrix that this support state qualifies are
reconciled in
[Normal Codex product-path reconciliation](./NormalCodexProductPathReconciliation.md).

## Compatibility inventory

| Direct Codex surface | Classification | Owner and supported behavior | Evidence contract / control | Removal condition | Rollback implication |
| --- | --- | --- | --- | --- | --- |
| Persisted sessions, provenance, event journal, artifacts and checkpoints | required historical-read compatibility | API read model and Workflow Detail render truthful `codex_direct_compat` and legacy evidence without a live worker | `tests/unit/omnigent/test_direct_compat_historical_reads.py` (pure journal read via `OmnigentBridgeSessionStore.list_event_page`) plus configured retention inventory; always readable | retention has elapsed and lossless migration or approved archival is proven | never rewrite provider, host, or runner identity |
| Recorded and in-flight workflow/activity payloads | required in-flight Temporal-history compatibility | Temporal workers retain decoders, activity bindings, and patch behavior needed by supported histories | `tests/unit/workflows/temporal/test_run_replayer.py::test_github_3518_cutover_runtime_parameter_histories_replay` replays pre-/post-cutover start payloads on one current worker; `::test_github_3518_cutover_selection_never_runs_inside_workflow_code` keeps selection at the submission boundary | no open history can schedule/retry the binding and retention policy permits removal | roll back only to a worker version that replays the same histories |
| `moonmind.codex_direct_compat.v1` event producer | temporary bridge-event compatibility producer | direct managed-session adapter emits normalized events with direct provenance; it is not Omnigent | explicit `parameters.communication.mode=omnigent_bridge`; parity and deduplication tests | profile-bound Omnigent coverage, projection parity, historical retention, and no schedulable compatibility activity | retain while rollback can restore direct launch |
| Explicit direct Codex selection | operator-selectable fallback during a bounded migration window | workflow compiler schedules direct only when the operator selected it or an explicit automatic strategy records `selectedPath` and `fallbackReason` | rollout phases 1–4; no implicit fallback from explicit Omnigent | phase 5 promotion evidence passes | phase rollback restores selection for new work without changing existing run snapshots |
| Direct/Omnigent comparison fixtures | test fixture/comparison substrate | conformance owners retain bounded fixtures and `dual_write` comparison diagnostics | hermetic conformance and replay suites | may be archived after direct retirement; keep minimal historical decoder fixtures | no production scheduling authority |
| Direct launch worker, launch-only UI, readiness/config flags and duplicate capacity paths | obsolete launch/configuration path eligible for removal | runtime owners disable scheduling first; Provider Profile ledger remains the sole mutable credential-capacity owner | phase 5 disables; phase 6 removes after history, rollback, and release gates | no supported new work can schedule direct, rollback no longer needs it, histories replay, and historical reads pass | removal is irreversible within the release; do not promote phase 6 while rollback depends on it |

## Versioned rollout

The persisted rollout value is the numbered phase, not an inferred default:

1. `opt_in`: Omnigent is normally available for explicit selection; direct Codex remains the preselected default.
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
cannot authorize removal. The API publishes desired/deployed/effective phase,
policy/profile versions, generation/expiry, image digests, architectures,
thresholds, evidence refs, blockers, and direct-launch status in
`/api/omnigent/codex-catalog-readiness`. Create/edit/rerun defaults
advance at `create_default`; schedule and preset defaults advance at
`schedule_default`. Every created execution stores `runtimeCutover` beside its
runtime/profile/model evidence. Invalid phases and explicit direct launches at
or after `direct_launch_disabled` fail before Temporal submission. Rollback
changes only future default selection and never rewrites existing run evidence.

Catalog `gateReasons` are operational launch blockers. Protected acceptance
matrix gaps are published separately as `supportGateReasons`: they qualify
release support and default promotion, but do not deny an otherwise safe local
launch. The canonical Compose path enables the bridge and internal endpoint by
default, seeds an active portable `codex` agent profile, resolves stock image
tags to immutable policy digests, and uses the on-demand policy unless an
operator explicitly selects static hosting. The API synchronizes the stable
upstream agent identity before activating that profile, keeps its projection
fresh, and retries transient image, bridge, or inventory startup failures with
capped backoff; no service restart is required for recovery.

## Support and conformance matrix v1

The stable machine-readable row inventory is the versioned catalog
`moonmind.omnigent.cutover.REQUIRED_ROW_CATALOG` (support-matrix version
`codex-omnigent-support-matrix/v1`). Each `MatrixRow` binds one protected row
to exactly one owning evidence kind and the host modes and runtime provenances
under which it may be observed; `REQUIRED_MATRIX_ROWS` is its ordered row-ID
projection. Row ownership is fixed by this catalog, never by a caller-supplied
list.

Protected-live owners (MoonLadderStudios/MoonMind#3563) publish one passing
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

At promotion time the launch-authority boundary
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
| Create/edit/rerun/schedule/preset | authored external/omnigent request | Create implemented; full matrix pending | [`CodexCreateToHostContract.md`](./CodexCreateToHostContract.md), `tests/unit/docs/test_omnigent_create_to_host_contract.py`; protected end-to-end artifact required |
| Repository read, mutation, publication | workspace locator and mounted tools | implemented; live support pending | [`OmnigentHostMountedTools.md`](./OmnigentHostMountedTools.md), conformance case evidence required |
| Workflow Detail live/replay/resources/controls | proxy and direct compatibility events | supported hermetically; live Omnigent pending | `tests/integration/omnigent/test_bridge_conformance.py`, `tests/integration/omnigent/test_execute_fake_server.py` |
| Cancellation, timeout, failure, cleanup, janitor | static/on-demand | implemented; live support pending | `tests/integration/omnigent/test_execute_fake_server.py`, `tests/unit/omnigent/test_oauth_host_janitor.py`; live lifecycle artifact required |
| Checkpoint capture, reattach, cold restore, branches | external-state plus workspace evidence | partially implemented; unsupported as a complete matrix | [`CheckpointBranchSystem.md`](../Workflows/CheckpointBranchSystem.md); capture/restore/branch live evidence required |
| Operator/autonomous remediation | policy-bound actions | partial; autonomous gate closed | Controlling contract `moonmind.omnigent.remediation_matrix` (`operator-remediation-support-matrix/v1`), [`WorkflowRemediation.md`](../Workflows/WorkflowRemediation.md), [`RemediationVerificationCadence.md`](../Workflows/RemediationVerificationCadence.md), `tests/unit/omnigent/test_remediation_matrix.py`; protected per-row browser-to-verification evidence required and the autonomous rollout gate stays a hard blocker |
| Initial/follow-up RAG | artifact-backed context | partial; unsupported as a complete matrix | canonical RAG docs and delivery/denial/budget evidence required |
| Persistent policy and agent-profile UI | immutable selected versions | partial; unsupported as a complete matrix | persisted policy/profile UI and snapshot evidence required |
| Enforced egress | host/runtime boundary | partial; unsupported | enforcement and denial evidence required; declarations alone do not qualify |
| Architecture and images | `linux/amd64`; digest-pinned server/host | amd64 evidence contract implemented; other architectures unsupported until proven | `moonmind/omnigent/conformance.py`; each supported architecture requires immutable-image live evidence |
| Direct runtime reads and fallback | historical read; phases 1–4 explicit fallback | compatibility supported; retirement gated | [`OmnigentBridge.md`](./OmnigentBridge.md), `tests/unit/omnigent/test_direct_compat_historical_reads.py`, `tests/unit/workflows/temporal/test_run_replayer.py` cutover replay tests, compatibility inventory above |

## Operator remediation support matrix v1

The Codex cutover matrix above qualifies *runtime and submission* support. The
distinct question "can an operator safely diagnose and repair a real workflow
through the normal MoonMind UI and a stock profile-bound Omnigent host" is
qualified by its own versioned controlling artifact
(`moonmind.omnigent.remediation_matrix`, support-matrix version
`operator-remediation-support-matrix/v1`; source
MoonLadderStudios/MoonMind#3626). It reuses the same evidence discipline: support
is observed, never asserted.

`REMEDIATION_ROW_CATALOG` is the machine-readable required-row inventory. Each
`RemediationMatrixRow` names scenario identity and owner, target and remediation
runtime provenance, host mode and released architecture, the required
action/verification capability, the required policy/profile authority mode and
egress authority, the required normal-Create UI journey, the owning evidence
kind and artifact schema, the exact pass/fail threshold keys, whether the row
gates manual diagnosis, manual mutation, or autonomous rollout, and whether the
row is satisfied by a passing capability or by an *intentional* safety denial.
Row ownership is fixed by this catalog; a caller-supplied row list never
qualifies support.

Protected owners publish one passing
`moonmind.operator-remediation-evidence/v1` artifact per owning evidence kind
(`diagnosisEvidence`, `recoveryBranchEvidence`, `actionApprovalEvidence`,
`verificationPreventionEvidence`, `reliabilitySecurityEvidence`). Each observed
row records its owned `row`, `gate`, `observedDisposition`, `hostMode`,
`targetProvenance`/`remediationProvenance`, `authorityMode`, `egress`,
`actionCapability`/`verificationCapability`, a normal-product-path `uiJourney`,
immutable `images`, `profileVersion`/`profileSha256`, `launchPolicyVersion`,
`agentProfileVersion`, `remediationPolicyVersion`, `thresholds`, and per-channel
`secretScan`. Every row also carries complete durable `lineage` and an exact
`evidenceManifest` containing each required semantic source-record type with its
schema version, content type, generated time, byte bound, and SHA-256 digest.
Mutating rows additionally record `actionDelivery.status` and,
**as a separate field**, `repairVerification.outcome`: action delivery and
target repair are never the same column (MoonLadderStudios/MoonMind#3622). The
`uiJourney` must assert the browser-originated normal Create path
(`browserOriginated`, `importedPinnedRemediationDraft`, `normalCreateRequest`,
`validatedPolicyProfileFields`, `workflowDetailFollowThrough`) and must carry no
prohibited authority marker (`hiddenSubmission`, `manualHostOrSessionId`,
`alternateWireContract`, `unvalidatedPolicyProfileFields`, `directCodexFallback`,
`logDerivedAuthority`); any prohibited marker fails the row closed.

`validate_remediation_evidence_artifact` rejects any artifact whose rows are
unknown, foreign to the artifact's kind, observed on the wrong host mode,
runtime provenance, authority mode, or egress, produced for different images or
profile/policy/agent-profile/remediation-policy versions, missing the
delivery/repair separation, carrying a prohibited UI-journey authority or an
over-limit threshold, carrying a self-asserted or incomplete secret scan, or not
observed with the required disposition. When the release declares more than one
architecture, each owned row must carry its own observation for every released
architecture. Every source-record type has one exact v1 schema and repeats the
target/remediation workflow, run, Step Execution, attempt, branch, Agent
Profile, Provider Profile, lease, host, bridge, and session identity. Lineage
`*Ref` values must equal a resolved record in the same manifest. The row result
is derived from the typed browser, request, input, workflow, context,
profile/policy, egress, approval, action, verification, publication, cleanup,
Temporal-history, side-effect-audit, and retained-scan records; the
`scenarioObservation` summary is cross-checked and has no independent authority.
`evaluate_remediation_release` (mounted via
`MOONMIND_OMNIGENT_REMEDIATION_RELEASE_EVIDENCE_REF`, published on
`/api/omnigent/codex-catalog-readiness` as `remediationRelease`) re-resolves every
manifest ref, binds its bytes to the recorded digest, re-validates each artifact,
binds each evidence kind to exactly one artifact, and requires the full row
cross-product before it reports `manualDiagnosisSupported` or
`manualMutationSupported`. A missing, stale, malformed, secret-bearing, or
over-threshold document fails closed. Compose defaults that reference to
`/workspace/cutover/remediation-release.json` on the existing read-only
`MOONMIND_CODEX_OMNIGENT_EVIDENCE_DIR` mount.

The repository-owned live controller is
`tools/run_omnigent_live_conformance.py --mode remediation`. For every catalog
row it asks the deployment adapter only to establish the target fixture and
perform the named scenario; MoonMind owns the target-Detail → Remediate → visible
Create → ordinary create request → remediation/target Detail replay browser
journey and derives qualification from resolved observations. The controller
requires immutable images and exact launch-policy, Agent Profile, and
remediation-policy versions, re-resolves and hashes all semantic source records,
performs retained-channel scans after cleanup, and emits one artifact per owning
evidence kind. `tools/build_operator_remediation_release_evidence.py` stages the
row artifacts and their nested source/scan records into a deployment-local
bundle, re-validates complete catalog coverage, and derives telemetry,
thresholds, manifest digests, and the combined release document; callers cannot
supply passed rows or ownership.

The versioned derived telemetry schema separately exposes creation and context
success rates; evidence degradation/unavailability; approval denial, expiration,
and stale-rejection rates; exact action outcome buckets by catalog action kind
and risk; lock, cooldown, duplicate, nested-denial, and escalation counts/rates;
branch-launch, host/session, first-message, terminal, publication, and cleanup
latencies; verification distributions and unverified mutations; repeated
failure and attempt exhaustion; egress decisions and attestation failures;
operator cancellation/takeover; and autonomous/manual origin. The release
evaluator re-derives that projection and its versioned objective threshold set
from the validated row records; a missing dimension, wrong kind/risk bucket,
missing phase latency, or telemetry/threshold divergence blocks promotion. The
Create UI exposes manual diagnosis/mutation qualification, manual promotion,
rollback, evidence expiry, telemetry version, and every operator alert in
addition to the autonomous authorization state.

**Autonomous rollout stays disabled.** A fully passing manual matrix never
authorizes autonomous mutating remediation: `autonomousRolloutAuthorized` is
always `false` and `autonomous_rollout_gate_closed` is a permanent blocker in
this matrix version (acceptance criterion 9). No workflow is granted
`admin_auto` by publishing evidence. The normal Create service rejects that
authority mode from the server-owned release status, and both Workflow Detail
and Create show it as release-gated. A production-shaped run must still publish
fresh passing live artifacts before this matrix qualifies operator remediation
as supported; until such evidence is mounted, the classification above stays
`partial; autonomous gate closed`.

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
