# Verification: Promote Proposals Without Live Preset Drift

**Date**: 2026-04-29
**Verdict**: FULLY_IMPLEMENTED

## Evidence

| Item | Status | Evidence |
| --- | --- | --- |
| FR-001 | VERIFIED | `TaskProposalService.promote_proposal` validates the stored proposal payload through `CanonicalTaskPayload`; `tests/unit/workflows/task_proposals/test_service.py::test_promote_proposal_rejects_unresolved_preset_steps` proves unresolved `type: "preset"` steps fail before commit. |
| FR-002 | VERIFIED | `test_promote_proposal_preserves_preset_provenance` and `test_promote_proposal_applies_runtime_override` verify `authoredPresets` and step `source` metadata survive promotion. |
| FR-003 | VERIFIED | Full task payload replacement was removed from the schema, router, and service. Promotion derives the final task from `proposal.task_create_request`. |
| FR-004 | VERIFIED | `runtimeMode` is passed as `runtime_mode_override` and applied service-side without replacing reviewed steps. |
| FR-005 | VERIFIED | `TaskProposalPromoteRequest` forbids extra fields; API test `test_promote_proposal_rejects_task_create_request_override` verifies `taskCreateRequestOverride` is rejected before execution creation. |
| FR-006 | VERIFIED | Existing preview model continues to expose `presetProvenance`, `authoredPresetCount`, and `stepSourceKinds`; no ambiguous umbrella terminology was introduced. |
| DESIGN-REQ-014 | VERIFIED | New promotion validation preserves Step Type convergence and rejects unresolved Preset submission. |
| DESIGN-REQ-018 | VERIFIED | Stored proposals execute as reviewed flat payloads; preset provenance remains metadata. |
| DESIGN-REQ-019 | VERIFIED | Hidden promotion-time refresh or live preset replacement is blocked by removing full payload overrides. |

## Commands

| Command | Result |
| --- | --- |
| `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/task_proposals/test_service.py tests/unit/api/routers/test_task_proposals.py` | PASS: Python `26 passed`; frontend unit suite invoked by runner `17 passed`, `471 passed`. |
| `rg -n "MM-560\|DESIGN-REQ-014\|DESIGN-REQ-018\|DESIGN-REQ-019" specs/280-promote-proposals-no-drift` | PASS: Traceability IDs preserved in spec, plan, quickstart, and tasks. |
| `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` | PASS: Python `4213 passed, 1 xpassed, 100 warnings, 16 subtests passed`; frontend `17 passed`, `471 passed`. |
| `./tools/test_integration.sh` | NOT RUN: Docker socket unavailable in the managed workspace (`/var/run/docker.sock` missing). |

## Residual Risk

No known implementation gaps remain for MM-560. The full unit run emitted pre-existing warnings unrelated to this change. Compose-backed integration could not run because Docker is unavailable in this managed workspace.
