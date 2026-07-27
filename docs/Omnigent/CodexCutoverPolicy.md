# Codex-through-Omnigent cutover and compatibility policy

**Document Class:** Canonical declarative

**Status:** Current

**Contract generation:** `moonmind.omnigent.codex-cutover/v1`

**Authority:** MoonLadderStudios/MoonMind#3518

Codex through a profile-bound Omnigent host is MoonMind's target managed Codex
path. It becomes the default only through the generation-bound release gate
below. Direct Codex remains truthfully identified as `codex_direct_compat`; it
is never described as Omnigent and is never an implicit fallback from an
explicit Omnigent request. Claude-through-Omnigent feature parity is deferred.

## Compatibility inventory

| Direct surface | Classification | Owner and supported behavior | Evidence / flag | Removal and rollback contract |
| --- | --- | --- | --- | --- |
| Workflow Detail event, chat, resource and artifact projection | required historical-read compatibility | API/UI read model; reads `codex_direct_compat` without a live direct worker | historical-read conformance case; always enabled | retain through evidence retention; rollback never rewrites provenance |
| `MoonMind.AgentRun` payloads, activity names, managed-session schemas and decoders recorded in Temporal histories | required in-flight Temporal-history compatibility | Temporal worker; replay and finish runs started by a supported worker generation | replay evidence in the live report; no feature flag | remove only after no retained history needs the patch era and Replayer passes retained fixtures |
| direct managed-session bridge event producer | temporary bridge-event compatibility producer | managed runtime; emits normalized events with source `codex_direct_compat` | `codex.direct-event-parity` | retain while direct launches or replay fixtures execute; disabling launch does not delete its decoder |
| direct selection in Create/edit/rerun/schedule/preset | operator-selectable fallback during a bounded migration window | execution admission; explicit direct launches only | `FEATURE_FLAGS__OMNIGENT_CODEX_ROLLOUT_PHASE` | disabled at `direct_disabled`; rollback may restore a prior phase only while supported |
| direct event/runtime fixtures | test fixture/comparison substrate | test owners; verifies truthful projection and no semantic drift | unit/replay suites | may become a bounded harness after histories expire; never production acceptance evidence |
| direct launch-only UI, readiness/config flags, duplicated credential or capacity ownership | obsolete launch/configuration path eligible for removal | UI/runtime owners | retirement gate below | remove only at `retired`, after scheduling is impossible and rollback no longer needs launch |

Persisted inventory includes managed-session bindings and summaries, normalized
events and their source, artifact/checkpoint references, execution snapshots,
Workflow Detail routes, and Temporal histories. Lossless normalized records are
retained under normal artifact/history retention. Provider-local state that
cannot be normalized is not relabelled or fabricated: retain its opaque
MoonMind artifact reference and truthful provenance until retention expiry.

## Versioned rollout and immutable decision

`FEATURE_FLAGS__OMNIGENT_CODEX_ROLLOUT_GENERATION` binds rollout configuration
to `FEATURE_FLAGS__OMNIGENT_CODEX_CONFORMANCE_EVIDENCE_JSON`;
`FEATURE_FLAGS__OMNIGENT_CODEX_CONFORMANCE_MAX_AGE_HOURS` bounds freshness.
The projection must carry the protected report SHA-256, qualifying case IDs,
host modes, architectures and immutable image digests, and authenticate with
`FEATURE_FLAGS__OMNIGENT_CODEX_CONFORMANCE_SIGNING_KEY`. Unsigned, tampered,
incomplete, mutable-tag, or non-artifact/non-HTTPS evidence is rejected. The
signing key is secret-backed configuration and is never stored in a run
snapshot. Admission produces an immutable snapshot with generation, phase,
cohort, selected path, reason, matrix version and evidence reference. Persist
it with the run input; Temporal workflows never recompute it.

Phases are ordered: `internal`, `create_default`, `scheduled_default`,
`broad_default`, `direct_disabled`, `retired`. Create defaults move before
schedules and presets. The `internal` phase admits only requests authored with
the `internal` cohort; general requests retain direct compatibility. Promotion
fails closed on missing, stale, mismatched or failed evidence. Automatic
selection records `migration_window_default` or `rollout_default`; it does not
try another runtime after launch failure.
Explicit Omnigent failure records `conformance_gate_failed`, never direct
fallback. Rollback changes new admissions only and preserves run snapshots.

## Release and retirement gates

The operator-visible release status is the rollout decision plus its report.
Promotion requires a digest-pinned report no older than seven days, at least
99% readiness, terminal-harvest and control-delivery success, at most 1%
cleanup failures, zero replay gaps and zero secret violations, plus passing
live conformance, historical-read and Temporal replay assertions. Artifact,
checkpoint, remediation and RAG completeness must each be at least 99%, and
janitor failures at most 1%. Exceeding any limit requires rollback to the prior
phase.

Telemetry is segmented by selected path, host mode, image architecture and
failure class. It records profile-lease, host-ready, first-message, first-event,
terminal-harvest and cleanup latency; reconnect/replay gaps; control outcomes;
artifact/capture completeness; checkpoint/resume/branch, remediation and RAG
outcomes; stale/orphan/janitor rates; fallback/use; redaction violations; and
policy/readiness denials.

Before `retired`, prove no new surface schedules direct Codex, no active lease
or in-flight history needs it, rollback no longer uses it, retained histories
replay on the candidate worker, historical reads pass without a direct worker,
and credential capacity has exactly one owner. The decoder/read model is
removed only after its retention/replay evidence expires, not because of age.

See the [support matrix](CodexSupportMatrix.md), [conformance contract](ConformanceAndLiveSmoke.md), and [startup/rollback runbook](CombinedStackValidationAndRollback.md).
