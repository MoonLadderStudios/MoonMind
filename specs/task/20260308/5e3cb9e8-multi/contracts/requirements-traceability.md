# Requirements Traceability

| Document Requirement | Mapped FR IDs | Planned Implementation Surface | Validation Strategy |
|----------------------|---------------|--------------------------------|---------------------|
| **DOC-REQ-001**: Provide documented steps so a developer can run `docker compose up` and have Temporal and workers auto-start. | FR-001, FR-002, FR-006 | `docker-compose.yaml` (Temporal server, DB, workers config), `docs/Temporal/DeveloperGuide.md` | Manual test/script validating that `docker compose up` starts all expected worker containers and Temporal services, and logs indicate they are polling. |
| **DOC-REQ-002**: Write an end-to-end test that creates a task, waits for worker execution, checks artifacts and UI status. | FR-003, FR-004, FR-005 | `scripts/test_temporal_e2e.py` | CI/CD pipeline or developer manual execution of `test_temporal_e2e.py` passing against a clean local stack. |
| **DOC-REQ-003**: Verify rollback and clean state between runs. | FR-006, FR-007 | `docs/Temporal/DeveloperGuide.md` (Cleanup section, Rollback section), Potential teardown steps in `test_temporal_e2e.py`. | Manual test following teardown guide verifies DB is empty, volumes reset. Rollback procedure correctly stops Temporal mode and uses standard DB execution. |
