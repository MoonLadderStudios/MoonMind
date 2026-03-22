# Feature Specification: Phase 3 - Remove Queue Backend Code

**Feature Branch**: `100-queue-removal-phase-3`  
**Created**: 2026-03-21  
**Status**: Approved  
**Input**: User request: `Implement phase 3 of docs/tmp/SingleSubstrateMigration.md`

## 1. Overview
As part of the Single Substrate Migration, Phase 3 aims to completely remove the legacy queue execution backend code from MoonMind. Since Phase 2 collapsed the frontend APIs to Temporal exclusively, the legacy backend system (the Agent Queue module, orchestrator, database tables, models, routers, and associated unit tests) is now completely unused and can be safely deleted. 

## 2. Requirements
- Delete the `api_service/api/routers/agent_queue.py` and its inclusion in `router_setup.py`.
- Delete the entire `moonmind/workflows/agent_queue/` module.
- Delete the entire `moonmind/workflows/orchestrator/` module.
- Delete the entire `tests/unit/orchestrator_removal/` test directory.
- Remove all queue-related database models.
- Generate an Alembic migration script to drop the respective queue tables (`agent_jobs`, `agent_job_artifacts`, `agent_job_events`, `agent_job_skills`).
- Remove configuration variables mapping to the queue substrate natively (`MOONMIND_QUEUE`, `defaultQueue`, `queueEnv`).
- Remove queue-related integration tests corresponding to the removed models.
- Purge `moonmind/workflows/__init__.py` of any legacy queue/orchestrator exports.
