# Implementation Plan: Refresh Managed Runtimes After Derived Skill Activation

**Branch**: `run-jira-orchestrate-for-mm-615-refresh-c2179a0f` | **Date**: 2026-05-08 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `/work/agent_jobs/mm:a3430c58-ca43-4dec-9edc-8f9c304abd0a/repo/specs/318-refresh-managed-runtimes-after-derived-skill-activation/spec.md`

**Note**: `.specify/scripts/bash/setup-plan.sh --json` was attempted, but the managed branch name does not match the numeric MoonSpec branch pattern. Planning continued against the active feature directory from `.specify/feature.json`.

## Summary

MM-615 requires managed runtimes to receive an on-demand Skill activation update only after a derived Skill snapshot is fully materialized, verified, and safe to expose. The current repository already contains `agent_skill.request_on_demand`, `AgentSkillMaterializer`, compact activation result models, projection diagnostics, and unit/integration tests for successful activation and materialization failure. Planning identifies remaining implementation work around manifest/checksum verification, explicit atomic-vs-next-turn activation semantics, distinct runtime refresh failure reporting, and boundary tests that prove runtime adapters cannot observe partial projections or broaden active Skill sets.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | implemented_unverified | `moonmind/workflows/agent_skills/agent_skills_activities.py` calls `AgentSkillMaterializer.materialize()` before returning `activated`; tests mock this path | Add boundary verification that the real materializer completes before activation output is accepted | integration |
| FR-002 | partial | `moonmind/services/skill_materialization.py` writes `_manifest.json`; no evidence of content digest validation against `content_digest` before activation | Add manifest/checksum verification for materialized bundles | unit + integration |
| FR-003 | partial | `ensure_shared_skill_links()` switches aliases after backing dir creation; materializer writes directly into snapshot dir and clears existing dirs | Add staging or equivalent verification proving no partial projection is visible, including same-snapshot/retry cases | unit + integration |
| FR-004 | implemented_unverified | `SkillsOnDemandService.activated_request_result()` returns compact activation data after materialization argument is provided | Add activity-boundary assertion that activation summary is emitted only after materialization metadata exists | integration |
| FR-005 | missing | No explicit activation timing field or controlled steer-point contract found | Add compact activation timing/guidance metadata for atomic switch vs next-turn activation | unit + integration |
| FR-006 | implemented_verified | Unit and integration tests cover materialization failure preserving active snapshot and avoiding manifest persistence | Keep coverage and extend if verification changes the failure path | none beyond final verify |
| FR-007 | partial | Runtime errors can map to `runtime_refresh_failed`, but no concrete refresh delivery boundary or tests were found | Add runtime-refresh failure path and diagnostics that preserve current snapshot | unit + integration |
| FR-008 | implemented_unverified | Compact model exists; `test_materializer_hybrid_returns_compact_metadata_without_skill_body` and activation tests check content refs/bodies are absent | Add activation-result tests covering materialization metadata and no unrestricted refs | unit + integration |
| FR-009 | implemented_unverified | External agent adapters are separate from `agent_skill.request_on_demand`; no explicit negative test for v1 exposure found | Add explicit external-agent boundary test proving on-demand activation is unavailable without governed controls | unit |
| FR-010 | partial | Materializer preserves repo-authored `.agents/skills`; workspace link helpers restrict projection ownership | Add adapter-boundary tests for no independent broadening and local/repo source separation during refresh | unit + integration |
| FR-011 | implemented_unverified | Materializer writes under runtime backing paths and skips repo-authored `.agents/skills`; tests cover source preservation | Add git/workspace assertion that projection changes are not treated as repo-authored changes | unit |
| FR-012 | partial | `materialization_failed` is tested; `runtime_refresh_failed` mapping exists but lacks a concrete failure test | Add distinct diagnostic tests for both failure classes | unit + integration |
| FR-013 | implemented_unverified | `spec.md` preserves MM-615 and canonical preset brief | Preserve MM-615 in plan, tasks, verification, commit, and PR metadata | final verify |
| SC-001 | implemented_unverified | Activation currently follows materialization in the activity flow | Add end-to-end activity evidence for all successful activations | integration |
| SC-002 | partial | Existing tests preserve previous snapshot on materialization failure; partial-write visibility is not fully tested | Add partial-write and verification-failure tests | unit + integration |
| SC-003 | missing | No explicit next-turn/controlled steer-point metadata found | Add activation timing contract and tests | unit + integration |
| SC-004 | implemented_unverified | Compact materialization metadata tests exist | Extend to activation output and failure output | unit |
| SC-005 | partial | `materialization_failed` covered; `runtime_refresh_failed` not covered end to end | Add runtime refresh failure simulation and diagnostics coverage | unit + integration |
| SC-006 | implemented_unverified | External agent paths are separate; no explicit v1 activation exposure evidence | Add negative external-agent test | unit |
| SC-007 | implemented_unverified | MM-615 preserved in `spec.md` | Preserve traceability across all generated artifacts and final verification | final verify |
| DESIGN-REQ-001 | partial | Immutable refs and projection helpers exist; partial activation safety needs stronger proof | Verify atomic/staged projection and compact history behavior | unit + integration |
| DESIGN-REQ-002 | implemented_unverified | Runtime instruction text mentions active skill path; activation result has summary field | Add compact activation guidance fields and tests | unit |
| DESIGN-REQ-003 | partial | Materialization occurs before activation result but checksum verification and projection safety are incomplete | Add verification-before-announcement contract | unit + integration |
| DESIGN-REQ-004 | missing | No clear next-turn/controlled steer-point result contract found | Add v1 deferred activation semantics | unit + integration |
| DESIGN-REQ-005 | implemented_unverified | Retrieval enum exists and compact materialization summary avoids bodies | Ensure activation refresh does not expose unrestricted refs | unit |
| DESIGN-REQ-006 | implemented_unverified | External agents are on separate adapter paths | Add explicit v1 exclusion test | unit |
| DESIGN-REQ-007 | partial | Materialization failure preserves snapshot; runtime refresh failure needs concrete path | Add runtime refresh failure behavior | unit + integration |
| DESIGN-REQ-008 | partial | Repo/local source preservation tests exist; arbitrary ref and projection publication boundaries need explicit coverage | Add security and projection non-publication assertions | unit |

## Technical Context

**Language/Version**: Python 3.12  
**Primary Dependencies**: Pydantic v2, Temporal Python SDK, pytest, existing MoonMind skill resolver/materializer services  
**Storage**: Existing artifact-backed Skill content, resolved skillset manifests, runtime workspace/cache filesystem paths; no new persistent tables planned  
**Unit Testing**: `./tools/test_unit.sh` with targeted pytest during iteration  
**Integration Testing**: `./tools/test_integration.sh` for `integration_ci` Temporal activity/materialization boundary tests  
**Target Platform**: MoonMind managed runtime worker and Temporal activity fleet on Linux containers  
**Project Type**: Backend workflow/service feature with managed-runtime adapter boundaries  
**Performance Goals**: Activation output remains compact regardless of Skill body size; materialization verifies only selected snapshot contents and should add no unbounded workflow-history payloads  
**Constraints**: Do not expose Skill bodies, secrets, or arbitrary body-readable refs in workflow/activity payloads; preserve existing active snapshots on failure; `.agents/skills` remains runtime projection state, not repo-authored mutable source  
**Scale/Scope**: One single-story runtime feature for MM-615; request resolution is covered by MM-614 and audit persistence is covered by MM-616

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Orchestrate, Don't Recreate: PASS. Work remains behind managed-runtime and activity/materializer boundaries.
- II. One-Click Agent Deployment: PASS. No new mandatory external services or secrets.
- III. Avoid Vendor Lock-In: PASS. Contract is runtime/materializer-oriented and not vendor-specific.
- IV. Own Your Data: PASS. Skill materialization uses operator-controlled artifacts and workspace paths.
- V. Skills Are First-Class and Easy to Add: PASS. Feature strengthens Skill activation and materialization contracts.
- VI. The Bittersweet Lesson: PASS. Plan favors contracts and tests around replaceable activation mechanics.
- VII. Powerful Runtime Configurability: PASS. Existing feature flag and runtime/materialization mode patterns are preserved.
- VIII. Modular and Extensible Architecture: PASS. Work stays in schemas, service/activity, materializer, and adapter-boundary tests.
- IX. Resilient by Default: PASS. Failure preservation and activity-boundary compatibility tests are explicit.
- X. Facilitate Continuous Improvement: PASS. Diagnostics and verification artifacts remain structured.
- XI. Spec-Driven Development Is the Source of Truth: PASS. This plan preserves MM-615 traceability from `spec.md`.
- XII. Documentation Separation: PASS. Planning details remain under `specs/318-refresh-managed-runtimes-after-derived-skill-activation/`.
- XIII. Pre-Release Compatibility Policy: PASS. No compatibility aliases or fallback transforms are planned for internal contracts; any payload changes must be explicit and boundary-tested.

## Project Structure

### Documentation (this feature)

```text
specs/318-refresh-managed-runtimes-after-derived-skill-activation/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── skill-activation-refresh-contract.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/
├── schemas/
│   └── agent_skill_models.py
├── services/
│   ├── skill_materialization.py
│   └── skills_on_demand.py
└── workflows/
    ├── agent_skills/
    │   └── agent_skills_activities.py
    ├── skills/
    │   └── workspace_links.py
    └── temporal/
        ├── activity_catalog.py
        └── activity_runtime.py

tests/
├── unit/
│   ├── services/test_skill_materialization.py
│   └── workflows/agent_skills/test_skills_on_demand_controls.py
└── integration/
    └── temporal/test_skills_on_demand_request_activation.py
```

**Structure Decision**: Use the existing backend workflow/service layout. The feature should update schema contracts, Skills On Demand service/activity behavior, materializer projection behavior, and unit/integration tests at the activity/adapter boundary. No frontend files are expected.

## Complexity Tracking

No constitution violations are planned.
