# Research: Remediation Evidence Bundles

## Classification

Decision: MM-452 is a single-story runtime feature request.
Evidence: The Jira brief has one actor, one goal, one source document, and one bounded acceptance set around remediation evidence bundles and tools.
Rationale: It does not require story splitting; the source design path is an implementation design document but the selected mode is runtime, so it is treated as source requirements.
Alternatives considered: Treating `docs/Tasks/TaskRemediation.md` as broad design was rejected because the Jira brief selected only sections 5.3, 6, and 9 with a single remediation runtime story.
Test implications: Unit tests cover the service boundary; integration verification remains the compose-backed suite when available.

## Existing Context Builder

Decision: Context artifact generation is implemented and verified.
Evidence: `moonmind/workflows/temporal/remediation_context.py`; `tests/unit/workflows/temporal/test_remediation_context.py::test_remediation_context_builder_creates_bounded_linked_artifact`.
Rationale: The builder creates `reports/remediation_context.json`, links it to the remediation execution, records the link ref, clamps evidence hints, filters unsafe refs, and stores compact metadata.
Alternatives considered: Rebuilding the context artifact path was rejected because existing MM-432 work is complete and matches MM-452's artifact-first entrypoint.
Test implications: Keep existing unit coverage in the focused command.

## Existing Typed Evidence Tools

Decision: Context reads, target artifact reads, bounded log reads, and live follow are implemented and verified.
Evidence: `moonmind/workflows/temporal/remediation_tools.py`; tests for declared artifact/log access and live-follow gating/cursor handoff.
Rationale: The service requires a linked context artifact, validates context target identity, allows only context-declared artifacts/taskRunIds, clamps tail lines, and records live-follow cursors.
Alternatives considered: Adding a new transport was rejected; docs allow internal APIs, activities, or MCP tools, and the service boundary is sufficient for this runtime story.
Test implications: Existing unit tests remain the primary proof for FR-005 through FR-009.

## Pre-Action Freshness Guard

Decision: Add a side-effect-free `prepare_action_request` method.
Evidence: Before this story, no remediation evidence service method re-read target health and pinned-vs-current run identity before action submission.
Rationale: MM-452 explicitly requires re-reading current target health and target-change guard inputs before side-effecting actions. A preparation method satisfies the guard without introducing the out-of-scope action execution registry.
Alternatives considered: Implementing action execution was rejected because action execution is outside MM-452 and would expand scope beyond evidence bundles/tools.
Test implications: Add a focused unit test proving target state/run changes after context creation are reflected in the preparation result.

## Validation Strategy

Decision: Run the focused unit runner for remediation context/evidence behavior and record compose-backed integration as not run unless Docker is available.
Evidence: The affected code is a service boundary already covered by async DB/artifact tests.
Rationale: The focused unit file exercises persistence, artifact service behavior, policy checks, and guard reads without requiring external credentials.
Alternatives considered: Full integration suite was deferred unless Docker is available because the change does not alter HTTP routing or Temporal workflow signatures.
Test implications: Final verification must include the focused unit command and note integration status explicitly.
