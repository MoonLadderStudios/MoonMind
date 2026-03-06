# Feature Specification: Jules Temporal External Events

**Feature Branch**: `048-jules-external-events`  
**Created**: 2026-03-06  
**Status**: Draft  
**Input**: User description: "Implement docs/Integrations/JulesTemporalExternalEventContract.md. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests. Preserve all user-provided constraints."

## Source Document Requirements

| Requirement ID | Source Citation | Requirement Summary |
| --- | --- | --- |
| DOC-REQ-001 | `docs/Integrations/JulesTemporalExternalEventContract.md` sections 1-4 (lines 8-91) | Jules must be implemented as the provider-specific Temporal external-monitoring profile, stay aligned with shared Temporal docs, reflect the current hybrid repo baseline, and preserve the migration stance of polling today with callback-first only after verified support exists. |
| DOC-REQ-002 | `docs/Integrations/JulesTemporalExternalEventContract.md` section 5 (lines 95-121) | Jules runtime state must use `jules` as the canonical integration name, `taskId` as `external_operation_id`, raw provider status as `provider_status`, and optional `url` as an external deep link. |
| DOC-REQ-003 | `docs/Integrations/JulesTemporalExternalEventContract.md` section 6 (lines 125-153) | Jules execution must obey the existing runtime gate and required configuration, reject invalid Jules runtime requests early, hide Jules tooling when disabled, and avoid introducing a second enablement flag for Temporal paths. |
| DOC-REQ-004 | `docs/Integrations/JulesTemporalExternalEventContract.md` section 7 (lines 157-211) | Temporal-backed Jules execution must preserve or intentionally supersede the current create/get/resolve request shapes, compact response model, transport behavior, retry policy, and scrubbed error handling established by the non-Temporal adapter. |
| DOC-REQ-005 | `docs/Integrations/JulesTemporalExternalEventContract.md` section 8 (lines 215-244) | Jules monitoring must use stable MoonMind-owned correlation identifiers, track the required bounded identifiers and URLs, and only use provider metadata for non-secret correlation hints rather than as the durable source of truth. |
| DOC-REQ-006 | `docs/Integrations/JulesTemporalExternalEventContract.md` section 9 (lines 248-283) | Jules status handling must preserve raw provider status, normalize into the bounded internal status set, centralize alias mapping in one Jules-specific implementation path, and fall back unknown statuses to `unknown`. |
| DOC-REQ-007 | `docs/Integrations/JulesTemporalExternalEventContract.md` section 10.1 (lines 287-313) | Jules must use the documented integration activity names on `mm.activity.integrations`, avoid a Jules-specific queue by default, and treat `integration.jules.cancel` as reserved until runtime support exists. |
| DOC-REQ-008 | `docs/Integrations/JulesTemporalExternalEventContract.md` section 10.2 (lines 314-362) | `integration.jules.start` must accept the documented semantic inputs, reuse the current adapter behavior safely, emit the semantic output contract, and default `callback_supported` to false unless a verified callback path is implemented. |
| DOC-REQ-009 | `docs/Integrations/JulesTemporalExternalEventContract.md` section 10.3 (lines 364-390) | `integration.jules.status` must read the current provider state using `taskId`, preserve raw status, stay compact in workflow-visible results, and support retry-safe polling and reconciliation. |
| DOC-REQ-010 | `docs/Integrations/JulesTemporalExternalEventContract.md` section 10.4 (lines 392-416) | `integration.jules.fetch_result` must conservatively persist final task snapshots and available MoonMind-authored summaries as artifacts without assuming richer Jules output endpoints. |
| DOC-REQ-011 | `docs/Integrations/JulesTemporalExternalEventContract.md` section 10.5 (lines 418-437) | Jules provider cancellation must remain explicitly unsupported until a real cancel API and runtime binding exist, while workflow-level cancellation still remains truthful and complete on the MoonMind side. |
| DOC-REQ-012 | `docs/Integrations/JulesTemporalExternalEventContract.md` section 11 (lines 441-476) | The Temporal-backed Jules path must be polling-capable now, callback-ready in architecture, callback-first only when verified, and use bounded polling backoff with a terminal-state latch. |
| DOC-REQ-013 | `docs/Integrations/JulesTemporalExternalEventContract.md` section 12 (lines 480-512) | Any future Jules callback path must use the generic `ExternalEvent` contract, authenticate before signaling workflows, dedupe safely, and store raw payloads as restricted artifacts rather than inline workflow data. |
| DOC-REQ-014 | `docs/Integrations/JulesTemporalExternalEventContract.md` section 13 (lines 516-553) | Jules-backed monitoring must keep workflow state compact, persist the recommended artifact classes, require terminal snapshots and failure summaries at minimum, link artifacts to workflow execution, and use the Temporal artifact backend as the canonical storage posture for Temporal flows. |
| DOC-REQ-015 | `docs/Integrations/JulesTemporalExternalEventContract.md` section 14 (lines 557-580) | Jules credentials and bearer tokens must stay out of workflow state, artifacts, logs, and exceptions, while Temporal activity error handling preserves the adapter's retry and fail-fast semantics and reports cancellation truthfully. |
| DOC-REQ-016 | `docs/Integrations/JulesTemporalExternalEventContract.md` section 15 (lines 584-596) | API and UI compatibility layers must preserve the distinction between MoonMind workflow identity and Jules provider identity and must not treat Jules `taskId` as the durable MoonMind execution identifier. |
| DOC-REQ-017 | Task objective runtime scope guard | Delivery must include production runtime code changes that implement the Jules Temporal external event contract plus automated validation tests; docs-only completion is not acceptable. |

Each `DOC-REQ-*` listed above maps to at least one functional requirement below.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Start Jules-Backed Work with Stable Correlation (Priority: P1)

As a platform operator, I can start Jules-backed work through the Temporal integration path only when Jules is enabled and configured, and I get a stable MoonMind correlation record plus the provider handle needed for monitoring.

**Why this priority**: Without correct runtime gating and correlation, every later polling, callback, artifact, and UI flow becomes unreliable.

**Independent Test**: Submit one Jules-backed request with valid runtime configuration and one without it, then verify that the valid request returns the bounded provider identity fields while the invalid request is rejected before provider work starts.

**Acceptance Scenarios**:

1. **Given** Jules runtime configuration is complete and enabled, **When** a Jules-backed run is started, **Then** the system records `integration_name=jules`, stores a stable `correlation_id`, maps Jules `taskId` to `external_operation_id`, and returns the raw provider status plus any external URL.
2. **Given** Jules is disabled or missing required credentials, **When** a Jules-backed run is requested, **Then** the system rejects the request early and does not expose Jules tools or start provider work.
3. **Given** the provider start call succeeds, **When** the initial activity result is returned to workflow logic, **Then** `callback_supported` is false unless a verified Jules callback path is configured and the returned contract is compact and retry-safe.

---

### User Story 2 - Monitor Jules Progress and Materialize Results Safely (Priority: P1)

As a runtime developer, I can monitor Jules work through polling today and callback-ready contracts for later, while status changes, terminal snapshots, and final results remain consistent across the legacy and Temporal paths.

**Why this priority**: Monitoring and result materialization are the core runtime behaviors that make Temporal-backed Jules execution usable in production.

**Independent Test**: Start a Jules-backed run, drive it through non-terminal and terminal states, and verify that polling uses the centralized normalizer, future callback payloads remain bounded and authenticated, and terminal outputs are captured as artifacts instead of large inline payloads.

**Acceptance Scenarios**:

1. **Given** a Jules task is still in progress and no verified callback path exists, **When** the workflow reconciles provider state, **Then** it polls using the Jules status activity, applies the central status normalizer, and keeps provider-visible payloads compact.
2. **Given** Jules returns an empty, aliased, or previously unseen status, **When** status normalization runs, **Then** the raw provider string is preserved, known aliases map to the bounded MoonMind states, and unknown values fall back to `unknown`.
3. **Given** a Jules task reaches a terminal outcome, **When** result materialization runs, **Then** the system persists the terminal snapshot and any available summary or diagnostics as artifacts and returns artifact references instead of assuming richer provider output endpoints.

---

### User Story 3 - Preserve Truthful Cancellation, Security, and UI Identity (Priority: P2)

As an operator or API consumer, I can trust that Jules cancellation claims, secrets handling, and compatibility views reflect the real provider capabilities and keep MoonMind execution identity separate from Jules provider identity.

**Why this priority**: Production readiness depends on truthful failure reporting, secret hygiene, and an unambiguous execution model for operators and dashboard users.

**Independent Test**: Cancel a Jules-backed run, inspect workflow-visible summaries, logs, and compatibility rows, and verify that unsupported provider cancellation is reported honestly, secrets stay scrubbed, and the Temporal workflow identity remains the primary MoonMind handle.

**Acceptance Scenarios**:

1. **Given** a Jules-backed workflow is canceled while the provider task is still running, **When** the workflow closes, **Then** MoonMind may close as canceled but any provider-side cancellation status explicitly states that Jules cancellation was unsupported or not performed.
2. **Given** transport failures, rate limits, or non-retryable client errors occur, **When** activity error handling runs, **Then** retries and fail-fast behavior stay consistent with the adapter rules and no secrets appear in workflow state, artifacts, logs, or exception text.
3. **Given** a Temporal-backed Jules execution is shown in API or dashboard compatibility views, **When** identity fields are rendered, **Then** the MoonMind workflow execution remains the durable primary handle and Jules `taskId` is shown only as the provider handle.

### Edge Cases

- Jules runtime is selected while one or more required configuration values are absent.
- The start request must derive its provider description from an input artifact because inline description text is absent.
- Jules returns an empty status, an unknown status, or a legacy alias that only the existing worker path previously recognized.
- A callback payload arrives in the future with no verified authenticity, duplicate provider event identity, or an unknown `external_operation_id`.
- A workflow is canceled after provider work started but before Jules exposes a real cancel endpoint.
- Terminal provider snapshots or callback payloads are too large or sensitive to store in workflow history or memo fields.
- A compatibility row exposes both workflow and provider identifiers and must not let clients confuse Jules `taskId` for the durable MoonMind execution ID.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The delivery MUST include production runtime code changes that implement the Jules Temporal external event contract plus automated validation tests; docs-only completion is not acceptable. (DOC-REQ-017)
- **FR-002**: The system MUST implement Jules as the provider-specific Temporal external-monitoring profile while keeping the shared Temporal integration documents authoritative on generic behavior and preserving the current hybrid-repo migration stance of polling-required today and callback-first only after verified support exists. (DOC-REQ-001)
- **FR-003**: The system MUST use `jules` as the canonical provider identity, MUST treat Jules `taskId` as `external_operation_id`, MUST preserve raw Jules `status` as `provider_status`, and MUST surface Jules `url` as an optional external deep link when present. (DOC-REQ-002)
- **FR-004**: The system MUST enforce the existing Jules runtime gate for all Temporal, API, worker, and tooling paths by requiring `JULES_ENABLED`, `JULES_API_URL`, and `JULES_API_KEY`, rejecting Jules-backed execution early when the gate is unsatisfied, and avoiding any second Temporal-only enablement flag. (DOC-REQ-003)
- **FR-005**: The system MUST preserve or intentionally supersede the current Jules create/get/resolve request and response contract, including compact payload shapes, bearer-auth JSON transport, retries on `5xx`, `429`, transport failures, and timeouts, immediate failure on other `4xx`, and scrubbed exception text. (DOC-REQ-004)
- **FR-006**: The system MUST track `integration_name`, `correlation_id`, `external_operation_id`, `provider_status`, `normalized_status`, and optional `external_url` for Jules-backed monitoring, and MUST treat any provider metadata correlation hints as non-secret supplements rather than the durable system of record. (DOC-REQ-005)
- **FR-007**: The system MUST provide one centralized Jules status normalizer shared by the Temporal and legacy polling paths, MUST normalize into only `queued`, `running`, `succeeded`, `failed`, `canceled`, or `unknown`, and MUST fall back unrecognized provider statuses to `unknown` while preserving the raw provider string. (DOC-REQ-006)
- **FR-008**: The system MUST use `integration.jules.start`, `integration.jules.status`, and `integration.jules.fetch_result` on the default `mm.activity.integrations` queue, MUST keep `integration.jules.cancel` reserved until runtime support exists, and MUST NOT introduce a Jules-specific queue unless a later operational requirement explicitly demands one. (DOC-REQ-007)
- **FR-009**: `integration.jules.start` MUST accept the documented semantic inputs, reuse the existing create-task adapter behavior, derive idempotent provider start behavior from stable request identity rather than Temporal `run_id`, map the current runtime result into the semantic contract, and default `callback_supported=false` unless a verified callback ingestion path exists. (DOC-REQ-008)
- **FR-010**: `integration.jules.status` MUST fetch provider state with Jules `taskId`, remain read-only and aggressively retryable, preserve raw status and optional external URL, and return only compact workflow-visible monitoring data plus artifact references when available. (DOC-REQ-009)
- **FR-011**: `integration.jules.fetch_result` MUST conservatively materialize the terminal Jules snapshot and any MoonMind-authored summary or resolution notes as artifacts, return artifact references plus compact summary data, and MUST NOT assume Jules already exposes richer logs, diff, or output-download endpoints. (DOC-REQ-010)
- **FR-012**: Jules provider cancellation MUST remain explicitly unsupported until a real cancel endpoint and runtime binding exist; when MoonMind cancels a workflow, the system MUST still complete workflow-side cancellation truthfully and MUST state that provider-side cancellation was not performed instead of faking success. (DOC-REQ-011)
- **FR-013**: The Temporal-backed Jules monitoring path MUST be polling-capable today, callback-ready in architecture, callback-first only after verified provider support exists, use bounded polling backoff with jitter and reset-on-material-status-change behavior, and prevent callback and polling races from double-completing the workflow. (DOC-REQ-012)
- **FR-014**: Any future Jules callback ingress MUST use the generic `ExternalEvent` signal contract with Jules-specific bounded fields, MUST authenticate callback payloads before signaling workflows, MUST dedupe on bounded provider event identity when available, and MUST store raw callback bodies as restricted artifacts rather than inline workflow state. (DOC-REQ-013)
- **FR-015**: Jules-backed Temporal runs MUST keep workflow state compact by persisting the recommended monitoring artifact classes, MUST store at least the terminal task snapshot and failure summary when applicable, MUST link artifacts to the owning workflow execution, and MUST treat the Temporal artifact backend as the canonical storage posture for Temporal-backed Jules flows. (DOC-REQ-014)
- **FR-016**: Jules credentials, bearer tokens, and other secrets MUST remain confined to approved secret-handling paths and MUST NOT appear in workflow history, memo fields, artifacts, logs, or exception text; Temporal activity error handling MUST preserve the adapter's transient-retry and non-retryable-fail-fast semantics with structured failure metadata. (DOC-REQ-015)
- **FR-017**: API and dashboard compatibility views for Jules-backed executions MUST preserve the distinction between the durable MoonMind workflow execution identity and the Jules provider `taskId`, MAY continue to show task-style labels during migration, and MUST NOT treat Jules `taskId` as the primary MoonMind execution identifier. (DOC-REQ-016)

### Key Entities *(include if feature involves data)*

- **JulesExecutionHandle**: The bounded provider identity returned from Jules-backed start and status calls, including `integration_name`, `external_operation_id`, `provider_status`, `normalized_status`, and optional external URL.
- **JulesCorrelationRecord**: The durable MoonMind-owned correlation state that survives retries and long-running orchestration and links workflow execution identity to the provider operation.
- **JulesStatusSnapshot**: A compact record of one observed Jules state change, including the raw provider status, normalized status, terminal flag, and any artifact-backed tracking summary.
- **JulesExternalEvent**: The bounded callback payload MoonMind will accept in the future for Jules, including source, event type, provider operation identity, timestamps, and optional payload artifact reference.
- **JulesResultArtifactSet**: The group of persisted task snapshots, summaries, diagnostics, and future callback payload artifacts linked back to one Jules-backed workflow execution.
- **JulesCancellationOutcome**: The workflow-visible record that distinguishes MoonMind cancellation status from provider-side cancellation support or lack thereof.
- **JulesCompatibilityView**: The API or dashboard representation that shows workflow execution identity, provider handle, bounded status, and links without conflating provider and MoonMind identifiers.

### Assumptions & Dependencies

- Shared Temporal integration documents remain the source of truth for provider-neutral workflow behavior, while this feature only locks Jules-specific narrowing rules.
- Existing Jules settings, schemas, adapter, runtime-gate helpers, and early Temporal activity registrations are the migration baseline rather than greenfield replacements.
- A verified Jules callback ingress path may not exist at delivery time, so polling support is required for a complete implementation.
- The Temporal artifact storage contract is available for Temporal-backed Jules flows, while legacy filesystem artifact roots remain relevant only for non-Temporal paths.
- API and dashboard compatibility layers may preserve task-oriented labels during migration as long as they preserve the identity separation required above.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Automated validation shows 100% of Jules-backed start attempts are rejected before provider execution when the Jules runtime gate is unsatisfied, and 100% of valid start attempts return the bounded provider identity and correlation fields required by this specification.
- **SC-002**: Automated contract tests show 100% of Jules start, status, and result-materialization flows preserve raw provider status, produce only bounded normalized statuses, and keep Temporal workflow-visible payloads compact.
- **SC-003**: Automated monitoring tests show 100% of callback-absent Jules runs complete through bounded polling fallback without duplicate completion, and duplicated or unrecognized provider statuses resolve to the documented central normalization behavior.
- **SC-004**: Automated artifact and security tests show 100% of terminal Jules runs persist the required terminal snapshot artifacts and, when applicable, failure summaries, while exposing no bearer tokens or other secrets in workflow-visible data, artifacts, logs, or exception text.
- **SC-005**: Automated compatibility tests show 100% of Temporal-backed Jules list/detail views preserve MoonMind workflow execution identity as the primary durable handle and never substitute Jules `taskId` for that role.
- **SC-006**: Release acceptance for this feature requires production runtime implementation changes plus automated validation tests for the Jules Temporal external event contract, and no docs-only outcome is accepted as complete.
