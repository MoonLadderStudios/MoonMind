# Omnigent Semantic Telemetry, Operator Session Timeline, and Stuck-State Reconciliation

Status: Proposed design
Document Class: System / Feature Design View
Owners: MoonMind Platform
Last updated: 2026-08-18

**Issue:** [MoonLadderStudios/MoonMind#3708](https://github.com/MoonLadderStudios/MoonMind/issues/3708) ([Omnigent control plane 7/11]).

**Implementation tracking:** rollout notes and temporary handoffs belong under `docs/tmp/` or gitignored local-only artifacts, not in this canonical design document.

## Related docs

- [`docs/Omnigent/ControlPlaneAggregates.md`](./ControlPlaneAggregates.md) — the durable aggregates this telemetry and timeline project.
- [`docs/Omnigent/ControlPlaneConcurrencyAndFencing.md`](./ControlPlaneConcurrencyAndFencing.md) — the revision/fencing authority the automated response is bound to.
- [`docs/Omnigent/OmnigentLifecycleReconciler.md`](./OmnigentLifecycleReconciler.md) — the pure reducer whose decisions the timeline and stuck-state response derive from.

## Why

OpenTelemetry and Temporal Visibility remain the operational telemetry and
durable orchestration planes. Temporal, canonical session state, decision
journals, and artifacts remain the durable sources of lifecycle truth. This
feature does not replace them: it makes Omnigent lifecycle behavior directly
observable through a stable domain telemetry convention, one durable
operator-facing session timeline, and a bounded stuck-state detector that
requests a **fenced** reconciliation before a six-hour timeout or a user report.

## Semantic trace convention

Span names and bounded attributes are the closed vocabulary in
`moonmind/omnigent/control_plane/spans.py`. Instrumentation wraps the
infrastructure and activity boundaries **around** the domain decisions; the pure
reducer never performs exporter I/O.

Span names (closed set `OMNIGENT_SPANS`):

```
omnigent.intent.compile
omnigent.session.reconcile
omnigent.observation.load
omnigent.provider.observe_snapshot
omnigent.provider.read_event_batch
omnigent.turn.submit
omnigent.command.execute
omnigent.profile_lease.ensure
omnigent.host.ensure
omnigent.session.ensure_provider_attachment
omnigent.evidence.harvest
omnigent.workspace.publish
omnigent.cleanup.execute
omnigent.compatibility.verify
omnigent.stuck_state.inspect
```

Bounded span attributes (closed set `SAFE_SPAN_ATTRIBUTES`) carry only runtime,
harness, host mode, command/decision class, desired/durable/observed/resulting
state, reason code, expected/resulting revision, fencing generation class or
ordinal, attempt ordinal, provider status vocabulary value, observation source
and schema version, terminal evidence kind, compatibility/image-manifest
digests, and retry/delivery-unknown/cleanup outcomes.

`omnigent_span` fails safe: an unknown span name degrades to a no-op, unknown or
oversized/secret-like attribute values are dropped, and a missing tracer or a
failing exporter is swallowed. Prompts, transcripts, diffs, terminal input,
credentials, presigned URLs, host paths, and unbounded provider payloads can
never be emitted. **Exporter or backend failure cannot change execution
correctness.**

## Metric families

Low-cardinality metric families live in
`moonmind/omnigent/control_plane/metrics.py`; the concurrency *conflict* counters
(revision/fencing conflicts, duplicate-command suppression, delivery-unknown
created/reconciled, stale observation retained, cleanup-claim conflicts) remain
in `telemetry.py` (#3704) and are not duplicated. The added families cover:

- **Reconciliation:** decisions by decision/reason class, convergence latency,
  repeated no-progress decisions, quarantined ambiguity, snapshot-recovered
  terminal transitions.
- **Provider and transport:** event-batch disconnects/reconnects, liveness-only
  duration, snapshot latency/errors, provider-terminal-to-MoonMind-terminal
  latency, unknown provider status/schema values, HTTP/SSE/WebSocket readiness
  and failure classes.
- **Resources and cleanup:** lease acquisition latency, lease renewal/fencing
  conflicts, cleanup lag, orphaned lease count, janitor claim/success/conflict/
  failure, evidence harvest/publication latency.
- **Compatibility and verification:** deployed-build compatibility, runtime
  capability readiness, exact-image conformance, protected-live evidence age,
  provider verification runner health.

Every label key is declared per-metric with a closed bounded value vocabulary,
and `FORBIDDEN_LABEL_KEYS` rejects any Workflow, run, user, session, binding,
provider-session, host, runner, profile, credential, repository, or workspace
identity at registration and at record time. **Metric labels are low cardinality
and never carry identity.**

## Durable operator session timeline

`moonmind/omnigent/control_plane/timeline.py` projects one bounded, safe
`SessionTimeline` for a single canonical session from its durable records. It is
a projection, not a second lifecycle authority: it reads only durable records
and performs no live-resource I/O, so it **survives provider/host/workspace
cleanup**. The timeline surfaces the difference among desired, durable, observed,
and reconciled state and explains why a session is launching, running,
delivery-unknown, awaiting observation, retrying, terminal, cleanup-incomplete,
or quarantined. Refs/digests pass through a secret-free guard and links are
server-authored relative URLs built from opaque validated ids.

The authorized machine-readable endpoint is
`GET /api/omnigent/sessions/{session_id}/timeline` (operator permission
`operations.read`). A Workflow Detail or operator UI projection may consume it
without becoming a second lifecycle authority.

## Stuck-state detector and automated response

`moonmind/omnigent/control_plane/stuck_state.py` is a pure, bounded detector over
durable records plus tri-state observation signals (`None` = not observed, so a
missing observation is never treated as an observed negative). It detects at
least: MoonMind active with no recent event/snapshot; provider-terminal vs
MoonMind-nonterminal (and the reverse); active turn with only liveness
observations; repeated no-progress reconciliation; orphaned host lease; profile
lease without a consumer; cleanup incomplete past deadline; compatibility unknown
after admission; command stuck claimed/delivery-unknown; stale live-conformance
evidence.

Automated response policy (`plan_response`):

1. Record a stuck-state finding and stable reason code.
2. The **first automated response is a fenced reconciliation** bound to the
   durable session's current revision and fencing generation — never a blind
   duplicate provider mutation and never a heuristic lease release. There is no
   resubmit/release response action in the detector at all.
3. Only decisions the pure reconciler authorizes under current fencing apply.
4. Persistent ambiguity (beyond policy) escalates to **quarantine plus a
   redacted diagnostics payload** rather than a fabricated success.
5. Product reads and evidence stay available even when interactive mutation is
   disabled.

## New-admission readiness

`moonmind/omnigent/control_plane/readiness.py` computes a bounded
`AdmissionReadiness` over actual runtime capability and evidence freshness:
reconciler/session-workflow generation, schema/repository compatibility, provider
endpoint and snapshot capability, event transport, server/UI/host build
manifests, WebSocket availability, worker/container backend, observation
freshness, janitor health, and the last exact-image and protected-live evidence.
Admission **fails closed**: a capability is ready only when explicitly observed
ready; unknown or negative signals block new admission. Historical reads and
cleanup for existing sessions stay available regardless.

## Non-goals

- Replacing Temporal Visibility, Workflow Detail, or the artifact system with
  OpenTelemetry.
- Exporting raw provider transcripts or terminal input.
- Automatically repairing arbitrary deployment configuration without reconciler
  authority.
