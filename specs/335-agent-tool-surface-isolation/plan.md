# Implementation Plan: Agent Tool-Surface Isolation

**Branch**: `change-jira-issue-mm-680-to-status-in-pr-6478e030` | **Date**: 2026-05-10 | **Spec**: [spec.md](./spec.md)
**Input**: Single-story feature specification from `/work/agent_jobs/mm:84c05579-71b9-4970-a650-3eb2341060d1/repo/specs/335-agent-tool-surface-isolation/spec.md`

**Setup Note**: `.specify/scripts/bash/setup-plan.sh --json` was attempted but failed because the managed branch is `change-jira-issue-mm-680-to-status-in-pr-6478e030`, not a numeric MoonSpec branch. `.specify/feature.json` already points to this active feature directory, so planning proceeded manually against the active feature artifacts.

## Summary

MM-680 requires MoonMind-launched managed agent sessions to be constrained to MoonMind-owned tool, external-service, and publish paths, while MoonMind-owned publishing must adopt pre-existing pull requests and classify branch publish conflicts as retryable structured outcomes. Current repo evidence shows selected skill resolution/projection and coarse worker egress policy labels exist, plus native branch/PR publish activities exist, but there is no complete per-skill closed tool/MCP/egress contract enforcement and no idempotent `repo.create_pr` adoption path. The implementation should add contract-first runtime enforcement and publish reconciliation with boundary tests before production changes.

## Requirement Status

| ID | Status | Evidence | Planned Work | Required Tests |
| --- | --- | --- | --- | --- |
| FR-001 | partial | Provider/profile and managed-session identity code exists, but no launch-time guard was found that rejects operator-account connector grants for all managed agent sessions. | Add service-identity/session connector rejection at managed runtime launch boundaries. | unit + integration |
| FR-002 | partial | `moonmind/workflows/skills/tool_plan_contracts.py` validates executable tool definitions; selected skill snapshots are resolved, but skill manifests do not yet declare a closed runtime tool/MCP/egress set. | Extend resolved skill contract metadata to include closed runtime tool, MCP, connector, and egress surfaces. | unit |
| FR-003 | partial | `verify_skill_projection()` and `AgentRunWorkflow` validate selected skill projection and resolved skill names, but not full runtime tool/MCP/egress diffs. | Add fail-closed launcher validation comparing requested runtime surfaces with the resolved skill contract. | unit + integration |
| FR-004 | partial | `moonmind/workflows/temporal/workers.py` assigns coarse `restricted-sandbox-egress` to agent runtime fleet. | Add per-skill egress policy materialization/enforcement and denied-egress diagnostics. | unit + integration_ci |
| FR-005 | partial | `docs/ManagedAgents/ManagedAgentsGit.md` says `PublishActivity` owns branch/PR publishing; runtime code still needs enforceable proof that agent sessions lack usable publish authority. | Remove or neutralize in-session publish credentials/remotes for managed runtime workspaces and expose an explicit diagnostic when direct publish is attempted. | unit + integration_ci |
| FR-006 | missing | No direct denial path was found for agent shell attempts such as `git push`, `gh pr create`, or raw provider mutation calls. | Add runtime/network/credential denial behavior and tests proving no external mutation occurs. | unit + integration_ci |
| FR-007 | missing | `GitHubService.create_pull_request()` posts directly to `/pulls`; no head/base lookup/adoption path was found before creation. | Query existing open pull requests for head/base before create; return an adopted success result when one exists. | unit + integration |
| FR-008 | missing | `_push_workspace_changes_if_needed()` uses `git push -u origin <branch>` and returns generic `failed`; no `--force-with-lease` against an activity-recorded remote SHA or structured retryable conflict was found. | Record remote SHA, push with lease, fetch on lease miss, and return retryable structured conflict details. | unit + integration |
| FR-009 | partial | Projection and publish status diagnostics exist, but no unified telemetry for blocked external-service access, rejected runtime surfaces, direct publish attempts, PR adoption, and publish conflicts was found. | Add sanitized isolation/publish diagnostic event envelopes at activity/runtime boundaries. | unit + integration_ci |
| FR-010 | partial | Runtime adapter structure exists across Temporal and agent runtime code, but closed isolation enforcement is not yet shown as adapter-neutral. | Apply the same contract model through shared managed-runtime launcher boundaries and adapter-specific projections. | unit + integration_ci |
| FR-011 | missing | Existing tests cover selected skill projection and repo.create_pr validation/delegation, but not all MM-680 required isolation and reconciliation cases. | Add workflow/activity/adapter boundary coverage for every scenario listed in FR-011. | unit + integration_ci |
| FR-012 | implemented_unverified | `spec.md` preserves `MM-680` and the original brief. | Preserve `MM-680` in plan, tasks, verification, commits, and PR metadata; final verify checks traceability. | final verify |
| SCN-001 | partial | Jira trusted tool paths exist; account-level connector invisibility is not proven for all agent sessions. | Add launch/session isolation guard and replay the incident shape. | integration_ci |
| SCN-002 | partial | Selected skill projection validates active skill snapshot; tool/MCP/egress denial is incomplete. | Add contract diff validation and denied-surface diagnostics. | unit + integration_ci |
| SCN-003 | missing | Publish ownership is documented; direct in-agent publish denial is not proven. | Add credential/remote/network denial and test representative commands. | integration_ci |
| SCN-004 | missing | Existing PR adoption is not present in `repo.create_pr`. | Implement PR lookup/adoption before creation. | unit + integration |
| SCN-005 | missing | Lease-miss handling is not structured/retryable. | Implement lease-aware push conflict handling. | unit + integration |
| SCN-006 | partial | Adapter boundaries exist, but runtime-neutral isolation contract is incomplete. | Route enforcement through shared launch contract with runtime adapter projections. | unit + integration_ci |
| SC-001 | missing | No audit evidence for zero operator-account connectors. | Add session-bootstrap audit assertions. | integration_ci |
| SC-002 | missing | No non-contract/direct publish denial suite. | Add mutation-denial test cases. | integration_ci |
| SC-003 | missing | No existing PR adoption result. | Add adoption test for existing head/base PR. | unit + integration |
| SC-004 | missing | No structured lease-miss outcome. | Add lease-miss conflict test. | unit + integration |
| SC-005 | missing | Required coverage list is not complete. | Add one test per required isolation/reconciliation case. | unit + integration_ci |
| SC-006 | implemented_unverified | `spec.md` preserves traceability. | Carry traceability through all downstream artifacts and final evidence. | final verify |
| DESIGN-REQ-001 | partial | Coarse worker egress and skill projection exist, not closed per-skill external-service enforcement. | Covered by FR-001 through FR-004 work. | unit + integration_ci |
| DESIGN-REQ-002 | partial | Runtime adapter architecture exists but attribution/determinism under bypass attempts is incomplete. | Covered by FR-009 through FR-012 work. | unit + integration_ci |
| DESIGN-REQ-003 | partial | No complete operator-account connector rejection evidence found. | Covered by FR-001. | unit + integration_ci |
| DESIGN-REQ-004 | missing | Direct publish path denial and PR adoption are incomplete. | Covered by FR-005 through FR-007. | unit + integration_ci |
| DESIGN-REQ-005 | partial | Trusted Jira path exists; universal contracted external-service path is incomplete. | Covered by FR-002 through FR-004. | unit + integration_ci |
| DESIGN-REQ-006 | partial | Managed identity concepts exist; rejection of operator identity injection is incomplete. | Covered by FR-001. | unit + integration_ci |
| DESIGN-REQ-007 | partial | Selected skill projection is enforced; full runtime surface enforcement is incomplete. | Covered by FR-002 and FR-003. | unit + integration_ci |
| DESIGN-REQ-008 | partial | Coarse egress policy label exists; per-contract fail-closed enforcement is incomplete. | Covered by FR-004 and FR-006. | unit + integration_ci |
| DESIGN-REQ-009 | partial | MoonMind-owned publish exists; no proof of absent in-session publish authority. | Covered by FR-005 and FR-006. | unit + integration_ci |
| DESIGN-REQ-010 | missing | `repo.create_pr` does not adopt existing PRs before creation. | Covered by FR-007. | unit + integration |
| DESIGN-REQ-011 | missing | Branch push does not use structured lease-miss handling. | Covered by FR-008. | unit + integration |
| DESIGN-REQ-012 | partial | Skill manifests/resolution exist; runtime-neutral enforcement is incomplete. | Covered by FR-002, FR-003, FR-010. | unit + integration_ci |
| DESIGN-REQ-013 | missing | Required coverage is incomplete. | Covered by FR-011. | unit + integration_ci |
| DESIGN-REQ-014 | partial | Some diagnostics exist; isolation/publish diagnostic envelope is incomplete. | Covered by FR-009. | unit + integration_ci |
| DESIGN-REQ-015 | implemented_unverified | Spec preserves out-of-scope boundary. | Keep plan/tasks within selected story. | final verify |

## Technical Context

**Language/Version**: Python 3.12; TypeScript/React only if Mission Control display of new diagnostics is required later.  
**Primary Dependencies**: Pydantic v2, Temporal Python SDK, FastAPI/MCP tool registry, `httpx`, existing GitHub/Jira trusted integration services, existing managed runtime launcher and agent-skill resolver/materializer services.  
**Storage**: Existing workflow history, memo/search attributes, artifact store, managed-session metadata, and existing configuration/secret references only; no new persistent database tables planned.  
**Unit Testing**: `./tools/test_unit.sh`; targeted Python pytest during iteration.  
**Integration Testing**: `./tools/test_integration.sh` for `integration_ci` coverage with local dependencies; direct focused `pytest tests/integration -m integration_ci` only during iteration when needed.  
**Target Platform**: Linux containers running MoonMind Temporal worker fleets and managed agent runtime workspaces.  
**Project Type**: Temporal-backed orchestration platform with managed runtime adapters and FastAPI control-plane surfaces.  
**Performance Goals**: Launch-time surface validation adds bounded metadata checks only; PR adoption lookup performs at most one provider query before create; denied-surface diagnostics remain compact and safe for workflow metadata/artifacts.  
**Constraints**: No raw credentials in artifacts/logs; no compatibility aliases for superseded internal contracts; workflow/activity payload changes must preserve in-flight compatibility or document explicit cutover; externally visible side effects must be retry-safe/idempotent.  
**Scale/Scope**: One runtime boundary story covering managed session identity/tool/egress isolation, direct publish denial, PR adoption, branch publish lease conflicts, diagnostics, and boundary tests.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- I. Orchestrate, Don't Recreate: PASS. The plan constrains provider runtimes through MoonMind launcher/tool boundaries and shared adapter contracts rather than replacing agent behavior.
- II. One-Click Agent Deployment: PASS. No new mandatory external service or storage is introduced; enforcement uses existing worker/runtime infrastructure.
- III. Avoid Vendor Lock-In: PASS. GitHub-specific PR adoption is behind existing provider service boundaries, while the session isolation contract remains runtime/provider-neutral.
- IV. Own Your Data: PASS. Diagnostics and artifacts remain in MoonMind-owned workflow/artifact stores; external calls stay behind trusted provider services.
- V. Skills Are First-Class: PASS. The feature strengthens skill contract metadata and selected-skill enforcement.
- VI. The Bittersweet Lesson: PASS. Enforcement is contract-centered and test-backed, not bespoke prompt scaffolding.
- VII. Powerful Runtime Configurability: PASS. Per-skill/manifest policy should be data-driven and observable, with fail-closed defaults.
- VIII. Modular and Extensible Architecture: PASS. Work belongs at launcher, runtime adapter, skill contract, provider service, and activity boundaries.
- IX. Resilient by Default: PASS. PR adoption and lease-miss handling make publish side effects retry-safe and explicit.
- X. Facilitate Continuous Improvement: PASS. Diagnostic events provide operator-visible evidence for bypass attempts and reconciliation.
- XI. Spec-Driven Development Is the Source of Truth: PASS. `spec.md`, this `plan.md`, and downstream `tasks.md` preserve traceability.
- XII. Documentation Separation: PASS. Rollout and implementation sequencing stay in MoonSpec artifacts, not canonical docs.
- XIII. Pre-Release Velocity: PASS. Superseded internal patterns should be removed in the same implementation instead of bridged indefinitely.

Re-check after Phase 1: PASS. The generated `data-model.md`, contract artifacts, and `quickstart.md` keep the same contract-first approach and introduce no new constitution violations.

## Project Structure

### Documentation (this feature)

```text
specs/335-agent-tool-surface-isolation/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── managed-runtime-isolation-contract.md
│   └── publish-reconciliation-contract.md
└── tasks.md             # To be generated by /speckit.tasks, not this step
```

### Source Code (repository root)

```text
moonmind/
├── workflows/
│   ├── skills/
│   │   ├── tool_plan_contracts.py
│   │   └── run_projection.py
│   ├── temporal/
│   │   ├── activity_runtime.py
│   │   ├── activity_catalog.py
│   │   ├── isolation_diagnostics.py
│   │   ├── activities/jules_activities.py
│   │   ├── runtime/launcher.py
│   │   ├── workflows/run.py
│   │   └── workers.py
│   └── adapters/github_service.py
├── agents/
│   └── codex_worker/worker.py
└── auth/github_credentials.py

tests/
├── unit/
│   ├── workflows/skills/
│   ├── workflows/temporal/
│   ├── workflows/adapters/
│   ├── services/
│   └── specs/
└── integration/
    └── temporal/ or managed-runtime boundary suites marked integration_ci
```

**Structure Decision**: Keep implementation in existing MoonMind workflow, managed-runtime, skill, and provider-service modules. Add tests at the same boundaries already used by selected skill projection, Temporal run publish behavior, and GitHub adapter behavior.

## Complexity Tracking

No constitution violations identified. Cross-module coordination is required because MM-680 spans launch-time skill contracts, runtime networking/credentials, publish activities, and operator diagnostics; those are existing ownership boundaries for this behavior.
