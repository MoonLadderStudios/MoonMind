# Research: Remediation Context Artifacts

## FR-001 / Builder Boundary

Decision: Add a dedicated `RemediationContextBuilder` service that requires an existing `TemporalExecutionRemediationLink`.
Evidence: `TemporalExecutionService.create_execution` now persists remediation links and lookup methods; `docs/Tasks/TaskRemediation.md` section 9.2 calls for a builder activity or service.
Rationale: Context generation belongs at an activity/service boundary where database and artifact access are allowed, not inside workflow history.
Alternatives considered: Generating context inline during create was rejected because artifact service writes and optional evidence loading should remain retryable and separately observable.
Test implications: Unit test generation from a valid remediation link and failure for a non-remediation workflow ID.

## FR-002 / Artifact Storage and Linkage

Decision: Use `TemporalArtifactService.create` and `write_complete` with execution link type `remediation.context`, then record the artifact ID on `execution_remediation_links.context_artifact_ref`.
Evidence: Temporal artifacts already support execution links and complete JSON artifact writes.
Rationale: The artifact link makes the bundle discoverable through existing artifact APIs, while the link column gives remediation relationship lookups a direct current context pointer.
Alternatives considered: Storing JSON directly on the remediation link was rejected because the design requires an artifact-first evidence model.
Test implications: Assert artifact status, link row, remediation link context ref, and execution artifact refs.

## FR-003 / Target Identity Payload

Decision: Build target identity from `TemporalExecutionCanonicalRecord` fields and pinned run identity from the remediation link.
Evidence: The create-link story pins `target_run_id`; canonical records carry title, summary, state, close status, and artifact refs.
Rationale: The context artifact should represent the pinned diagnosis anchor even if later target runs change.
Alternatives considered: Calling Temporal describe was rejected for this slice because local canonical records provide deterministic hermetic test evidence.
Test implications: Assert target workflow ID, run ID, state, title, and close status in the generated JSON.

## FR-004 / Evidence and Policy Snapshots

Decision: Normalize compact copies of `target.stepSelectors`, `target.taskRunIds`, `evidencePolicy`, `approvalPolicy`, `lockPolicy`, `actionPolicyRef`, `authorityMode`, and mode from persisted remediation parameters.
Evidence: `docs/Tasks/TaskRemediation.md` section 7.3 defines these fields; section 9.3 shows them in the artifact shape.
Rationale: The remediator needs stable policy snapshots and selectors without receiving raw logs or storage access.
Alternatives considered: Copying the entire `task.remediation` object was rejected because it may grow later with fields that do not belong in durable context artifacts.
Test implications: Assert normalized selectors and policy fields.

## FR-005 and FR-006 / Bounded, Ref-Only Payloads

Decision: Clamp `tailLines` to 2,000, limit task run IDs to 20, copy artifact identifiers only, and reject no fields based on optional evidence absence.
Evidence: `docs/Tasks/TaskRemediation.md` sections 9.4, 10.4, and 10.5 forbid unbounded bodies and raw storage access.
Rationale: The builder should be safe even when callers supply excessive hints or target runs have limited evidence.
Alternatives considered: Including log excerpts was rejected for this story because the source summary specifically scopes bounded context artifacts, not evidence readers.
Test implications: Assert clamped counts and absence of raw URL/path/storage-key/secret-like payload fields.
