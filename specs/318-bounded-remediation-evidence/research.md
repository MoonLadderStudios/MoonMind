# Research: Bounded Remediation Evidence Context

## Scope Classification

Decision: MM-618 remains one runtime story, not a broad design needing breakdown.
Evidence: `/work/agent_jobs/mm:a6d63116-cfbf-4474-90db-6af6f461079b/repo/specs/318-bounded-remediation-evidence/spec.md` contains exactly one `## User Story - Diagnose With Bounded Evidence` and preserves MM-618 as the canonical Jira preset brief.
Rationale: The Jira brief selects one independently testable remediation evidence behavior from `docs/Tasks/TaskRemediation.md`: bounded context plus typed evidence/live-follow access.
Alternatives considered: Running `moonspec-breakdown` was rejected because no multiple independently testable stories are required by MM-618.
Test implications: none beyond final traceability verification.

## Existing Remediation Context Builder

Decision: Mark context creation as implemented for basic linked artifact creation, but partial for rich evidence resolution.
Evidence: `moonmind/workflows/temporal/remediation_context.py` defines `RemediationContextBuilder`, `REMEDIATION_CONTEXT_ARTIFACT_NAME = "reports/remediation_context.json"`, restricted artifact creation, link update through `context_artifact_ref`, boundedness flags, target artifact refs, selected steps, task run IDs, and policy snapshots. `tests/unit/workflows/temporal/test_remediation_context.py::test_remediation_context_builder_creates_bounded_linked_artifact` verifies artifact creation, linkage, metadata, persisted refs, and bounded payload.
Rationale: The core artifact-builder shape exists, but the current payload does not yet prove full observability-summary, stdout/stderr/merged/diagnostic refs, provider snapshot refs, compact summaries, or unavailable-evidence records for all target evidence classes.
Alternatives considered: Treating the builder as complete was rejected because MM-618 requires typed evidence availability and degraded evidence, not only a linked context artifact.
Test implications: add unit tests for richer context payload construction and an integration test proving artifact generation before remediation diagnostic work.

## Boundedness And Redaction

Decision: Mark boundedness and secret-safety requirements as implemented_verified.
Evidence: `remediation_context.py` stores refs instead of artifact bodies, sets `rawLogBodiesIncluded` and `artifactContentsIncluded` false, restricts redaction level, and sanitizes policy/lifecycle payloads. Unit tests in `test_remediation_context.py` assert no presigned URLs, raw secrets, or raw local paths are serialized in context, lifecycle, audit, and guard payloads.
Rationale: Current implementation and tests directly cover the strongest safety constraints in MM-618.
Alternatives considered: Adding new persistent redaction storage was rejected; the artifact service metadata and sanitized JSON payloads are enough for this story.
Test implications: preserve unit coverage and include one integration payload assertion that the generated context contains no forbidden raw access values.

## Typed Evidence Access Surface

Decision: Mark typed evidence tools as implemented_verified for service-level membership checks, partial for real log/live-follow adapter binding.
Evidence: `moonmind/workflows/temporal/remediation_tools.py` provides `get_context`, `read_target_artifact`, `read_target_logs`, `follow_target_logs`, `prepare_action_request`, and `execute_action`. The service reads only the linked context artifact, rejects artifacts and task run IDs not listed in context, normalizes log streams, caps tail lines from context policy, and rereads target health before actions. Unit tests cover declared evidence reads, live-follow gating, action prep, and action lifecycle artifacts.
Rationale: The typed API boundary exists, but injected log/live-follow readers remain runtime adapters. The plan must prove or implement binding to existing `/api/task-runs` observability/log surfaces or equivalent internal services.
Alternatives considered: Giving remediation tasks raw URLs or filesystem paths was rejected by the spec and constitution.
Test implications: add adapter-boundary unit tests plus hermetic integration exercising target log/diagnostic reads through the real server-mediated path.

## Live Follow Semantics

Decision: Mark live-follow requirements partial.
Evidence: `RemediationEvidenceToolService.follow_target_logs()` requires context `liveFollow.supported is True`, allowed mode, declared taskRunId membership, and resumes from a sequence cursor. `api_service/api/routers/task_runs.py` exposes live SSE via `/task-runs/{id}/logs/stream` with `since` support and terminal-run fallback behavior. Mission Control tests cover live logs and artifact fallback.
Rationale: The follow primitive and task-run stream exist, but `RemediationContextBuilder` currently emits `supported: False` and does not compute `active`, `unavailable`, `unsupported`, or `policy_denied` state from target activity, taskRun support, and policy. Cursor persistence is service-injectable but not yet proven in a remediation lifecycle integration.
Alternatives considered: Treating live-follow as authoritative was rejected; durable artifacts/logs remain the fallback and source of truth.
Test implications: add unit cases for each live-follow state and integration coverage for active supported and unavailable fallback paths.

## Evidence Availability And Degraded Targets

Decision: Mark degraded evidence support partial.
Evidence: `build_remediation_summary_block()` supports `evidenceDegraded`, `unavailableEvidenceClasses`, and `fallbacksUsed`; Mission Control tests render degraded remediation states. The context builder does not yet compute missing stdout/stderr/diagnostics/provider snapshot/continuity classes into a first-class availability record.
Rationale: MM-618 requires every missing evidence class to be recorded without deadlock. Existing helpers are useful, but the context artifact and typed tool responses need consistent availability semantics.
Alternatives considered: Failing remediation when evidence is incomplete was rejected because historical and degraded targets must remain diagnosable.
Test implications: add unit tests for historical merged-log-only target, partial artifact refs, and unavailable live follow; add integration proof that diagnosis can proceed with degraded evidence.

## Mission Control Evidence Presentation

Decision: Mark Mission Control presentation partial.
Evidence: `frontend/src/entrypoints/task-detail.tsx` renders remediation relationship panels, evidence bundle status, context artifact download links, approval summaries, and degraded messages. `frontend/src/entrypoints/task-detail.test.tsx` covers evidence link rendering, degraded states, approval controls, and containment/accessibility styling.
Rationale: UI coverage demonstrates the context artifact and degraded states, but MM-618 calls out direct access to target logs, diagnostics, decision logs, action request/results, verification artifacts, and live observation state. The existing UI should be expanded or verified for all remediation evidence artifact classes.
Alternatives considered: Relying only on artifact list generic rendering was rejected because operators need evidence classes to be discoverable from the remediation panel.
Test implications: add focused UI tests for all evidence artifact classes and live observation/fallback state.

## Unit Test Strategy

Decision: Use focused `pytest` unit tests for backend service models/builders/tools and frontend Vitest tests for task detail evidence presentation.
Evidence: Existing unit coverage lives in `tests/unit/workflows/temporal/test_remediation_context.py`, `tests/unit/workflows/temporal/test_temporal_service.py`, `tests/unit/api/routers/test_executions.py`, and `frontend/src/entrypoints/task-detail.test.tsx`.
Rationale: The story spans deterministic builders, typed access services, API serialization, and React rendering, which are all suitable for focused unit tests.
Alternatives considered: Only broad integration tests were rejected because they would make failure diagnosis slower and would not prove small contract details.
Test implications: run `./tools/test_unit.sh` before final verification; for focused frontend iteration use `./tools/test_unit.sh --ui-args frontend/src/entrypoints/task-detail.test.tsx`.

## Integration Test Strategy

Decision: Add hermetic integration coverage for the real remediation context/evidence boundary only where it can stay within required CI timeouts.
Evidence: Repo instructions require `./tools/test_integration.sh` for tests marked `integration_ci`; existing high-risk seams include artifacts, worker topology, and live logs.
Rationale: MM-618 crosses artifact publication, execution records, task-run evidence, and UI/API contracts. Unit tests alone are insufficient for final proof that context artifacts are linked before diagnostic work and that evidence reads remain server-mediated.
Alternatives considered: Temporal time-skipping workflow tests were rejected for required CI because repo instructions say those are not marked `integration_ci` due timeout risk.
Test implications: create or update hermetic integration tests under `tests/integration/temporal/` and run `./tools/test_integration.sh` when integration coverage is added.

## No New Persistent Storage

Decision: Reuse existing remediation link, execution record, artifact metadata/content, and managed-run observability stores.
Evidence: `api_service/db/models.py` already has `TemporalExecutionRemediationLink.context_artifact_ref`; migration `221_remediation_context_artifacts.py` adds the context artifact ref; artifact service stores context/lifecycle payloads.
Rationale: MM-618 requires durable evidence artifacts, not a new database table. Availability and live-follow cursor state can live in bounded artifacts or existing compact remediation state.
Alternatives considered: A new remediation evidence table was rejected because it would duplicate artifact-backed context and increase migration scope.
Test implications: tests should verify existing DB rows and artifact refs rather than new persistence.
