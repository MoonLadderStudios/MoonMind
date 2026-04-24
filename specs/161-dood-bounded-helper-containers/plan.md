# Implementation Plan: DooD Phase 7 Bounded Helper Containers

## Technical Context

Implement Phase 7 in the existing `moonmind.schemas.workload_models`, `moonmind.workloads.registry`, and `moonmind.workloads.docker_launcher` modules. Keep the executable-tool path and existing one-shot workload behavior intact.

## Constitution Check

- I Orchestrate, Don't Recreate: PASS, Docker remains behind MoonMind launcher boundaries.
- II One-Click Agent Deployment: PASS, no new required external service.
- III Avoid Vendor Lock-In: PASS, helper contract is generic Docker workload metadata.
- IV Own Your Data: PASS, diagnostics remain local artifacts/metadata.
- V Skills Are First-Class: PASS, helpers support tool execution without becoming agents.
- VI Scientific Method: PASS, tests define the runtime contract first.
- VII Runtime Configurability: PASS, profiles carry policy and limits.
- VIII Modular Architecture: PASS, bounded helper lifecycle stays in workload modules.
- IX Resilient by Default: PASS, TTL, readiness, and teardown are explicit.
- X Continuous Improvement: PASS, diagnostics capture bounded outcome metadata.
- XI Spec-Driven: PASS, this spec/plan/tasks tracks the change.
- XII Canonical Docs vs Tmp: PASS, implementation tracking updates remain under local-only handoffs.
- XIII Pre-release Compatibility: PASS, no compatibility aliases are introduced.

## Implementation Strategy

1. Extend workload schemas with `bounded_service`, TTL, and readiness probe contracts.
2. Validate helper profile/request policy in the registry.
3. Add launcher methods for detached helper start, bounded readiness probing, explicit teardown, and expired-helper sweeping.
4. Add focused unit coverage and update the DooD remaining-work tracker.
