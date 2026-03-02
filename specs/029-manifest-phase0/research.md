# Phase 0 Alignment Research Findings

**Feature**: Manifest Queue Phase 0 Alignment  
**Branch**: `task/20260302/a4bb533a-multi`  
**Date**: March 2, 2026

## Current Runtime State (Verified)

The codebase already implements the core Phase 0 deliverables:

- Queue job type registration in `moonmind/workflows/agent_queue/job_types.py` includes `manifest`.
- Manifest normalization and capability derivation are handled by `moonmind/workflows/agent_queue/manifest_contract.py`.
- `AgentQueueService.create_job` routes manifest jobs through manifest contract normalization.
- Manifest registry APIs and service orchestration are implemented in `api_service/api/routers/manifests.py` and `api_service/services/manifests_service.py`.
- Queue payload responses are sanitized through `moonmind/schemas/agent_queue_models.py` and `sanitize_manifest_payload`.

## Gap Identified

Validation failures on manifest submission paths are not consistently actionable:

- `POST /api/queue/jobs` currently maps manifest validation failures to a generic queue error code.
- `PUT /api/manifests/{name}` currently returns a generic message instead of the specific contract reason.

## Decision

Propagate manifest contract error messages for manifest-specific submission endpoints while preserving existing non-manifest queue error mapping.

### Implementation Surface

- `api_service/api/routers/agent_queue.py`
- `api_service/api/routers/manifests.py`
- Unit tests in:
  - `tests/unit/api/routers/test_agent_queue.py`
  - `tests/unit/api/routers/test_manifests.py`

## Rationale

- Aligns with Phase 0 operator usability goals: fast correction of invalid manifests.
- Maintains existing response semantics for non-manifest queue clients.
- Uses already-sanitized `ManifestContractError` text, avoiding raw-secret leakage.

## Alternatives Considered

- **Keep generic errors**: rejected; slows debugging and conflicts with actionable validation expectations.
- **Change service-layer exception types**: rejected; unnecessary for this scope and higher regression risk.
- **Global queue error mapping by string heuristics**: rejected; less deterministic than endpoint-context handling.
