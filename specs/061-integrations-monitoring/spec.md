# Feature Specification: Integrations Monitoring Design

**Feature Branch**: `047-integrations-monitoring`  
**Created**: 2026-03-06  
**Status**: Draft  
**Input**: User description: "Implement the Integrations Monitoring Design as described in docs/Temporal/IntegrationsMonitoringDesign.md. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests. Preserve all user-provided constraints."

## Source Document Requirements

| Requirement ID | Source Citation | Requirement Summary |
| --- | --- | --- |
| DOC-REQ-001 | `docs/Temporal/IntegrationsMonitoringDesign.md` §1, §3.2 (lines 12-19, 49-55) | Temporal-managed integrations must be modeled as workflow executions that use Activities, Signals, durable polling timers, artifacts, and visibility metadata, with monitoring anchored inside `MoonMind.Run` by default. |
| DOC-REQ-002 | `docs/Temporal/IntegrationsMonitoringDesign.md` §1, §3.3, §7.5 (lines 21-23, 59-64, 306-315) | The design must remain provider-neutral, must not introduce a product-level integration queue or one root workflow type per provider, and may use child workflows only for explicitly justified isolation cases. |
| DOC-REQ-003 | `docs/Temporal/IntegrationsMonitoringDesign.md` §4 (lines 68-90) | Workflow code must remain deterministic, external events must use `ExternalEvent`, large or volatile payloads must stay in artifacts, idempotency must rely on stable `correlation_id` instead of `run_id`, and worker topology should stay minimal until justified. |
| DOC-REQ-004 | `docs/Temporal/IntegrationsMonitoringDesign.md` §5 (lines 94-128) | Each monitored operation must maintain the required compact workflow state fields, optional bounded monitoring fields, and artifact-backed storage discipline for large payloads. |
| DOC-REQ-005 | `docs/Temporal/IntegrationsMonitoringDesign.md` §6, §6.1 (lines 130-170) | The provider start contract must follow `integration.<provider>.start`, run on `mm.activity.integrations`, return monitoring hints, and be safe under retry using stable idempotency keys. |
| DOC-REQ-006 | `docs/Temporal/IntegrationsMonitoringDesign.md` §6.2-§6.4 (lines 172-237) | Provider status, result fetch, and cancel contracts must normalize provider status, use idempotent artifact-backed result handling, and fail fast when cancellation is unsupported. |
| DOC-REQ-007 | `docs/Temporal/IntegrationsMonitoringDesign.md` §7.1-§7.4 (lines 241-304) | Default workflow behavior must set `mm_state=awaiting_external`, wait on `ExternalEvent`, polling, and cancellation, fetch results on terminal success, and use hybrid callback-plus-polling with a terminal latch by default. |
| DOC-REQ-008 | `docs/Temporal/IntegrationsMonitoringDesign.md` §8.1-§8.2 (lines 319-346) | Callback ingestion must verify inbound requests in the API layer, optionally store raw payload artifacts, resolve workflows through a durable correlation record, and avoid relying on visibility scans by external operation ID. |
| DOC-REQ-009 | `docs/Temporal/IntegrationsMonitoringDesign.md` §8.3-§8.4 (lines 348-366) | `ExternalEvent` payloads must stay small and callback handling must support bounded dedupe, replay safety, duplicate delivery, and out-of-order events. |
| DOC-REQ-010 | `docs/Temporal/IntegrationsMonitoringDesign.md` §9 (lines 368-404) | Dashboard visibility must reuse canonical `mm_*` lifecycle fields, keep memo small and human-readable, and avoid indexing high-cardinality provider identifiers by default. |
| DOC-REQ-011 | `docs/Temporal/IntegrationsMonitoringDesign.md` §10 (lines 406-450) | Polling must honor provider guidance when reasonable, use bounded backoff with jitter, Continue-As-New must preserve monitoring identity/state, and worker routing/rate limits must stay minimal and configurable. |
| DOC-REQ-012 | `docs/Temporal/IntegrationsMonitoringDesign.md` §11 (lines 452-477) | Retry posture, terminal failure handling, and user cancellation must distinguish provider capabilities, preserve operator-facing summaries, and keep cancellation semantics explicit even when provider cancellation is best-effort. |
| DOC-REQ-013 | `docs/Temporal/IntegrationsMonitoringDesign.md` §12 (lines 479-500) | Secrets, webhook verification, request limits, and artifact previews/retention must enforce security and redaction boundaries. |
| DOC-REQ-014 | `docs/Temporal/IntegrationsMonitoringDesign.md` §13 (lines 502-526) | Validation must include unit, Temporal integration, and failure-injection coverage for normalization, idempotency, callback races, Continue-As-New, cancellation, and provider failures. |
| DOC-REQ-015 | `docs/Temporal/IntegrationsMonitoringDesign.md` §14 (lines 528-574) | Jules must be supported as the initial provider profile using the provider-neutral activity contract, normalized status mapping, hybrid monitoring preference, and compact result artifacts. |
| DOC-REQ-016 | `docs/Temporal/IntegrationsMonitoringDesign.md` §15 (lines 576-583) | Implementation sequencing must first establish the provider contract and normalized status model, then correlation storage, callback handling, polling fallback, visibility updates, and provider-specific tests. |

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Monitor External Work Inside a Run (Priority: P1)

As a platform user, I can submit work to an external integration and have the same run wait durably for progress, results, or failure without leaving the normal execution lifecycle.

**Why this priority**: This is the core product behavior the design introduces, and all provider-specific monitoring depends on it.

**Independent Test**: Start a run that launches an external operation, verify it enters `awaiting_external`, then complete it through callback-first or polling fallback and confirm the run resumes with result artifacts.

**Acceptance Scenarios**:

1. **Given** a run starts a provider operation, **When** the provider accepts the request, **Then** the run stores a compact monitoring state, records a stable correlation ID, and transitions to `awaiting_external`.
2. **Given** callbacks are enabled, **When** a valid terminal callback arrives before the next poll cycle, **Then** the run consumes the external event once, fetches the result, and resumes without duplicate completion.
3. **Given** callbacks are absent or unreliable, **When** polling observes a terminal provider result, **Then** the run fetches outputs or failure diagnostics and closes with the correct MoonMind outcome.

---

### User Story 2 - Correlate and Secure External Callbacks (Priority: P1)

As an operator, I can trust inbound integration callbacks to resolve the correct run, reject malformed or unverifiable requests, and remain safe under duplicate or out-of-order delivery.

**Why this priority**: Callback handling is the main source of correctness, security, and idempotency risk for long-running integrations.

**Independent Test**: Deliver valid, duplicate, reordered, and invalid callbacks against an active monitored run and verify API validation, durable correlation lookup, workflow dedupe, and safe rejection behavior.

**Acceptance Scenarios**:

1. **Given** a provider callback includes valid verification material and correlation keys, **When** the API receives it, **Then** it resolves the target workflow through correlation storage and signals `ExternalEvent` with a compact payload.
2. **Given** duplicate or out-of-order callbacks arrive, **When** the workflow processes them, **Then** already applied or stale events are ignored without corrupting terminal state or artifacts.
3. **Given** a callback is malformed, oversized, or unverifiable, **When** the API validates the request, **Then** the callback is rejected before workflow state changes are attempted.
4. **Given** callback bursts or sensitive provider payloads are present, **When** the API stores callback or result artifacts, **Then** callback rate limits are enforced, previews are redacted, download access stays short-lived, and retention policy is applied to provider debug payloads.

---

### User Story 3 - Operate Provider Monitoring Safely at Runtime (Priority: P2)

As a platform operator, I can monitor integrations with bounded history growth, explicit retry and cancellation rules, secure artifact handling, and a portable provider contract that works first for Jules and later for other providers.

**Why this priority**: Reliability, migration safety, and provider portability determine whether the monitoring design can operate in production.

**Independent Test**: Exercise long waits, Continue-As-New, terminal provider failures, unsupported provider cancel behavior, and Jules normalization paths while verifying visibility fields, summaries, and test coverage.

**Acceptance Scenarios**:

1. **Given** a monitored run exceeds configured wait-cycle thresholds, **When** Continue-As-New is triggered, **Then** the workflow preserves correlation identity, bounded provider state, and required visibility metadata.
2. **Given** the provider reaches terminal failure or rejects cancellation, **When** the run finalizes, **Then** the system records an operator-facing summary artifact and exposes the correct failure or cancellation outcome.
3. **Given** Jules is configured as the first provider profile, **When** Jules statuses and result metadata are observed, **Then** they are normalized into the portable monitoring contract without introducing Jules-specific workflow types or queue semantics.

### Edge Cases

- Provider `start` times out ambiguously after the request may already have been created upstream.
- Callback and polling detect terminal completion at nearly the same time.
- A non-terminal callback arrives after polling already marked the operation terminal.
- Provider cancellation is unsupported or acknowledged too late to affect MoonMind closure semantics.
- Correlation records must survive Continue-As-New even if the latest Temporal `run_id` changes.
- Raw callback or result payloads contain sensitive values that must not leak into memo, workflow history, or previews.
- Callback bursts or replay storms must trigger rate limits without blocking recovery of legitimate later events.
- Polling must recover when callbacks never arrive, while still respecting provider rate limits.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST model external integrations as a Temporal-native concern inside `MoonMind.Run` by default, using workflow executions, provider Activities, `ExternalEvent` signals, durable timers, artifacts, and visibility metadata together. (DOC-REQ-001)
- **FR-002**: The system MUST remain provider-neutral and MUST NOT introduce a product-level integration queue, user-facing task-queue semantics, or one root workflow type per provider. (DOC-REQ-002)
- **FR-003**: The system MUST permit child workflows for integration monitoring only when long-lived isolation, history pressure, or materially distinct retry/cancellation policy makes the split necessary. (DOC-REQ-002)
- **FR-004**: The system MUST keep provider I/O, webhook verification, filesystem access, and artifact writes out of deterministic workflow code, using Activities or the API layer instead. (DOC-REQ-003)
- **FR-005**: The system MUST use `ExternalEvent` as the workflow signal vocabulary for inbound provider events, store large or volatile provider payloads as artifacts, and base long-lived idempotency/correlation on stable `correlation_id` values rather than Temporal `run_id`. (DOC-REQ-003)
- **FR-006**: The system MUST maintain compact workflow-side monitoring state containing `integration_name`, `correlation_id`, `external_operation_id`, `normalized_status`, `provider_status`, `started_at`, `last_observed_at`, `monitor_attempt_count`, `callback_supported`, and `result_ref` or `result_refs`, with optional bounded fields for dedupe and polling metadata. (DOC-REQ-004)
- **FR-007**: The system MUST keep only bounded monitoring state in workflow memory/history and MUST store non-trivial provider payloads, status snapshots, and fetched outputs as artifacts. (DOC-REQ-004)
- **FR-008**: The system MUST expose provider activity contracts named `integration.<provider>.start`, `integration.<provider>.status`, `integration.<provider>.fetch_result`, and `integration.<provider>.cancel`, routed to `mm.activity.integrations` by default. (DOC-REQ-005, DOC-REQ-006)
- **FR-009**: The `integration.<provider>.start` contract MUST be safe under retry, MUST return the provider handle and monitoring hints, and MUST derive idempotency keys from stable execution identity instead of `run_id`. (DOC-REQ-005)
- **FR-010**: The `integration.<provider>.status` contract MUST normalize provider-specific states into MoonMind-facing statuses while preserving raw provider status for diagnostics. (DOC-REQ-006)
- **FR-011**: The `integration.<provider>.fetch_result` contract MUST persist large outputs as artifacts, perform idempotent artifact creation, and prefer returning stable artifact references on retry. (DOC-REQ-006)
- **FR-012**: The `integration.<provider>.cancel` contract MUST surface explicit unsupported or ambiguous cancellation outcomes rather than reporting false success. (DOC-REQ-006, DOC-REQ-012)
- **FR-013**: After a provider operation starts, the workflow MUST set `mm_state=awaiting_external`, may set bounded integration visibility metadata, and MUST wait on `ExternalEvent`, a durable polling timer, and user cancellation until the monitored operation becomes terminal. (DOC-REQ-007, DOC-REQ-010)
- **FR-014**: The default provider-monitoring mode MUST be hybrid callback-plus-polling, including a terminal-state latch so callback/poll races cannot double-complete the run and late duplicates become harmless. (DOC-REQ-007)
- **FR-015**: The API callback path MUST verify signatures or auth tokens, request size, and basic schema before state changes, optionally persist raw payload artifacts, and then signal the workflow using a compact `ExternalEvent` payload. (DOC-REQ-008, DOC-REQ-009, DOC-REQ-013)
- **FR-016**: The system MUST maintain a durable callback correlation record outside workflow history keyed by provider correlation material and workflow identity, and MUST NOT depend on visibility scans by provider operation ID as the default lookup path. (DOC-REQ-008)
- **FR-017**: `ExternalEvent` payloads MUST remain small and support fields for source, event type, external operation ID, provider event ID when available, normalized/provider status when known, observed time, and an optional payload artifact reference. (DOC-REQ-009)
- **FR-018**: Callback processing MUST dedupe using provider event IDs when available or a conservative fallback key, ignore non-terminal late events after terminal completion, and remain safe under replay, duplicate delivery, and out-of-order arrival. (DOC-REQ-009)
- **FR-019**: Dashboard visibility MUST continue to use canonical `mm_owner_id`, `mm_state`, `mm_updated_at`, and `mm_entry` fields, may add only bounded integration-specific visibility fields such as `mm_integration` or `mm_stage`, and MUST keep high-cardinality provider identifiers out of default search attributes. (DOC-REQ-010)
- **FR-020**: Memo content for monitored runs MUST remain small and human-readable, limited to fields such as title, summary, and safe display details like external URLs, with raw provider payloads and detailed status dumps stored as artifacts instead. (DOC-REQ-010)
- **FR-021**: Polling policy MUST honor provider-recommended intervals when reasonable, otherwise start near 5 seconds, back off with jitter, reset when provider status materially changes, and cap steady-state polling to a conservative provider-specific maximum. (DOC-REQ-011)
- **FR-022**: Long-lived monitoring waits MUST trigger Continue-As-New on an explicit bounded-wait policy, preserving workflow identity, `correlation_id`, required memo/search attributes, and provider state needed to resume monitoring. (DOC-REQ-011)
- **FR-023**: Worker routing and rate limiting MUST start from minimal shared queues and configurable concurrency/rate controls, and MUST only split into provider-specific queues when secrets, quotas, or traffic isolation require it. (DOC-REQ-003, DOC-REQ-011)
- **FR-024**: Retry behavior for `start`, `status`, `fetch_result`, and `cancel` MUST reflect provider idempotency and ambiguity rules, and terminal provider failures MUST produce compact summary artifacts, preserve raw diagnostics when available, and surface `integration_error` to the UI. (DOC-REQ-012)
- **FR-025**: User-initiated cancellation during integration monitoring MUST attempt provider cancellation when supported, record whether the provider accepted or ignored cancellation, and still close the run with MoonMind cancellation semantics. (DOC-REQ-012)
- **FR-026**: Provider credentials, workflow history, memo fields, artifacts, webhook validation, preview generation, and retention policies MUST enforce the security boundaries, redaction, and request controls described by the source design. (DOC-REQ-013)
- **FR-027**: The first provider profile delivered under this design MUST support Jules through the provider-neutral activity contract, Jules-to-portable status normalization, hybrid monitoring preference when callbacks exist, and compact result artifact generation. (DOC-REQ-015)
- **FR-028**: Delivery planning for this feature MUST preserve the documented implementation order by establishing provider contracts and normalization first, then correlation storage, callback handling, polling fallback, visibility updates, and provider-specific tests. (DOC-REQ-016)
- **FR-029**: Deliverables for this feature MUST include production runtime code changes that implement integrations monitoring behavior and automated validation tests that verify the documented runtime contracts and failure paths. (Runtime intent guard)
- **FR-030**: Automated validation coverage MUST include unit tests, Temporal integration tests, and failure-injection scenarios for normalization, idempotency, callback races, missed callbacks, Continue-As-New, cancellation, and provider failure modes. (DOC-REQ-014)

### Key Entities *(include if feature involves data)*

- **ExternalOperationState**: Compact workflow-resident state for one monitored provider operation, including stable correlation identity, normalized/raw status, timing metadata, callback support, and result references.
- **ProviderActivityContract**: Provider-neutral start/status/fetch-result/cancel contract used by integrations workers and workflow orchestration.
- **CorrelationRecord**: Durable lookup record that maps provider callback material to workflow identity and lifecycle metadata across retries and Continue-As-New.
- **ExternalEventPayload**: Small workflow signal payload carrying validated provider event details and an optional artifact reference for the raw body.
- **IntegrationVisibilitySnapshot**: Search-attribute and memo fields exposed to dashboard/API consumers while a run is waiting on external progress.
- **PollingPolicy**: Runtime policy describing initial delay, backoff, jitter, cap, and wait-cycle thresholds for long-lived monitoring.
- **ProviderFailureSummary**: Compact operator-facing summary and diagnostics references written when a monitored external operation fails or produces ambiguous outcomes.
- **ProviderProfile**: Provider-specific normalization and capability mapping layered behind the shared monitoring contract, with Jules as the first implemented example.

### Assumptions & Dependencies

- Existing Temporal lifecycle fields and `ExternalEvent` vocabulary remain the canonical control surface for execution monitoring.
- Existing artifact APIs and storage are available to hold raw callback payloads, fetched outputs, and failure diagnostics.
- Existing authorization and request-validation mechanisms can be extended to secure provider callback ingress.
- Initial rollout can target Jules first while keeping all contracts portable to other providers.
- Product-facing dashboard views may continue compatibility behavior during migration, but Temporal execution state remains the source of truth.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Automated runtime validation shows 100% of monitored runs enter `awaiting_external` with a stable correlation identifier and leave that state through a single terminal completion path.
- **SC-002**: Callback ingestion tests show 100% rejection of malformed or unverifiable callbacks and 100% successful routing of valid callbacks through durable correlation lookup without relying on provider-ID visibility scans.
- **SC-003**: Duplicate, reordered, late, and callback-versus-poll race tests show zero double-completion outcomes across the covered scenarios.
- **SC-004**: Long-wait validation demonstrates 100% preservation of workflow identity, monitoring state, and required visibility metadata across configured Continue-As-New transitions.
- **SC-005**: Provider failure and cancellation tests show 100% of covered terminal error paths produce a compact operator-facing summary plus the correct MoonMind failure or cancellation outcome.
- **SC-006**: Jules acceptance coverage demonstrates normalized status mapping, supported monitoring mode selection, and compact result artifact generation for all covered Jules terminal states.
- **SC-007**: Release readiness for this feature requires production runtime implementation changes and automated validation tests; docs-only output does not satisfy completion.
- **SC-008**: Security validation shows callback rate limiting, redacted previews, short-lived artifact download grants, and retention-class assignment behave correctly for all covered callback/result artifact paths.
