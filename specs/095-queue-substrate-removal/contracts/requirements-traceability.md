# Requirements Traceability: Queue Substrate Removal (Phase 1)

| DOC-REQ | Functional Req | Planned Implementation | Validation Strategy |
|---------|---------------|----------------------|---------------------|
| DOC-REQ-001 | FR-001, FR-004, FR-008, FR-009, FR-011, FR-012 | Produce `queue-feature-audit.md` documenting every queue endpoint's Temporal equivalent or deferral. | Audit report exists with status for every `/api/queue/*` endpoint. |
| DOC-REQ-002 | FR-007, FR-008, FR-009, FR-010, FR-011, FR-012 | Map each queue feature: worker lifecycle → deprecated (Temporal workers), SSE → deprecated (polling), live sessions → already have Temporal path, operator messages → deferred. | Audit report has non-empty mapping for every endpoint. |
| DOC-REQ-003 | FR-001, FR-003 | Verify `_create_execution_from_task_request` preserves runtime/model/effort/repo/publish. | Unit test covers all submit form fields round-trip to Temporal execution payload. |
| DOC-REQ-004 | FR-002 | Verify routing returns `"temporal"` for manifest jobs and `_create_execution_from_manifest_request` handles them. | Unit test: manifest-type job creation produces `MoonMind.ManifestIngest` workflow. |
| DOC-REQ-005 | FR-005 | Verify recurring tasks router creates Temporal Schedules. | Code audit of `recurring_tasks.py` confirms Temporal Schedule creation path. |
| DOC-REQ-006 | FR-006 | Verify step template expansion is source-agnostic — output usable with Temporal path. | Code audit of `task_step_templates.py` confirms no queue dependency. |
| DOC-REQ-007 | FR-001, FR-013 | Harden `routing.py` to always return `"temporal"`. Add fail-fast when submit_enabled=false. | Unit test: `get_routing_target_for_task()` never returns `"queue"`. |
