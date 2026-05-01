# Research: Author Agentic Skill Steps

## FR-001 / SC-001 / DESIGN-REQ-010

Decision: Add MM-577-specific verification for authored Skill selector, instructions, args, and required capabilities.
Evidence: `frontend/src/entrypoints/task-create.tsx` already exposes Skill selector and advanced Skill Args/Required Capabilities; `frontend/src/entrypoints/task-create.test.tsx` had MM-564 coverage and is extended to include MM-577.
Rationale: The user story is traceability-sensitive; existing behavior is present but needed MM-577 evidence.
Alternatives considered: Adding new production controls was rejected because the existing surface already supports the requested fields currently exposed by the direct task authoring flow.
Test implications: frontend integration.

## FR-002 / FR-007 / DESIGN-REQ-009

Decision: Verify Skill payloads remain Skill work and UI labels keep Skill distinct from deterministic Tool work.
Evidence: Create-page submission uses `type: "skill"`, `task.skill`, and `tool.type: "skill"` metadata for Skill steps; existing Tool and Preset tests share the same target.
Rationale: The core distinction is already implemented; preserving it through regression coverage is sufficient for this story.
Alternatives considered: Introducing a separate Skill authoring page was out of scope for the single story.
Test implications: frontend integration plus final traceability review.

## FR-003 / FR-005 / FR-006 / DESIGN-REQ-019

Decision: Reuse existing validation surfaces and verify them through focused backend and Create-page tests.
Evidence: `tests/unit/workflows/tasks/test_task_contract.py` rejects non-skill Tool payloads on Skill steps; `tests/unit/api/test_task_step_templates_service.py` rejects mixed Skill/Tool payloads; Create-page tests reject malformed Skill Args JSON.
Rationale: Validation belongs at existing UI and task contract boundaries. Unsupported values fail through established validation instead of compatibility aliases.
Alternatives considered: Adding hidden fallback semantics for unresolved skills was rejected by the compatibility policy and source validation requirements.
Test implications: unit and frontend integration.

## FR-004

Decision: Preserve currently supported Skill selector, args/context, and required capabilities in direct submissions, and rely on existing template service tests for broader metadata preservation.
Evidence: The Create-page payload carries Skill id, args, and required capabilities; task-template catalog tests cover context, permissions, autonomy metadata preservation for template-derived steps.
Rationale: The Jira brief says controls are included when supported; direct task authoring should not invent unsupported hidden fields.
Alternatives considered: Adding new direct-form fields for every future metadata category was rejected as broader than MM-577.
Test implications: frontend integration and service unit tests.

## FR-008 / SC-004

Decision: Preserve MM-577 and the canonical Jira preset brief in this feature's artifacts and verification report.
Evidence: `artifacts/moonspec-inputs/MM-577-canonical-moonspec-input.md`; `specs/290-author-agentic-skill-steps/spec.md`.
Rationale: Traceability is an explicit Jira requirement and is necessary for downstream PR metadata.
Alternatives considered: Reusing `specs/283-agentic-skill-steps` was rejected because it preserves MM-564, not MM-577.
Test implications: final verification review.
