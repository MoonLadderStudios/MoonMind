# Story Breakdown: Finish production hardening and rollout of MoonMind PentestGPT integration

- Source design: inline user request
- Source reference path: none
- Source document class: imperative-override
- Story extraction date: 2026-06-15T01:29:48+00:00
- Output mode: jira

## Design Summary

The source describes production hardening for the already-present, gated PentestGPT executable tool. The target state centers on a dedicated activity boundary, pre-launch policy gates, real Docker and provider-manager proof, truthful network posture, hardened runner image publishing, strict findings/report semantics, report-first Mission Control behavior, conservative VPN handling, and staged rollout controls. Security, redaction, compact workflow payloads, approved scope, cleanup, and conservative defaults are first-order requirements.

## Coverage Points

- DESIGN-REQ-001 (requirement, Summary): Production gated PentestGPT tool - security.pentest.run must be production-ready for approved lab and authorized assessment workflows while remaining operator-gated.
- DESIGN-REQ-002 (integration, Phase 1): Dedicated Pentest activity boundary - Pentest-specific execution logic must move to a dedicated activity module/class while preserving the public activity binding.
- DESIGN-REQ-003 (security, Target outcome): Artifact-backed approved scope - Untrusted execution must require scope_artifact_ref and reject inline self-authorization or invalid scope before side effects.
- DESIGN-REQ-004 (security, Target outcome): Deployment policy before side effects - Runner profiles, operation modes, evidence levels, external targets, approvals, and time budgets must be enforced before provider lease, secret resolution, or launch.
- DESIGN-REQ-005 (integration, Phase 3): Provider lease lifecycle - Provider leases and cooldowns must behave correctly across success, failure, capacity, cancellation, timeout, worker failure, and duplicate release.
- DESIGN-REQ-006 (security, Target outcome): Secret-safe materialization - Provider secrets resolve only after lease acquisition and never leak into logs, artifacts, history, result payloads, or UI previews.
- DESIGN-REQ-007 (integration, Phase 2): Real Docker workload execution - Valid lab requests must run through the real Docker workload launcher using pentestgpt-safe and a curated runner image or local test tag.
- DESIGN-REQ-008 (observability, Phase 2): Cleanup and partial artifacts - Containers and leases must clean up on success, failure, timeout, cancellation, and cooldown while preserving partial diagnostics when available.
- DESIGN-REQ-009 (security, Phase 4): Truthful network semantics - Docs, metadata, profile names, and UI copy must match actual network enforcement; external targets stay disabled until reviewed.
- DESIGN-REQ-010 (integration, Phase 5): Runner CI upstream compatibility - Runner CI must validate wrapper smoke tests, upstream PentestGPT CLI contract, failure classification, redaction, and deterministic artifact output.
- DESIGN-REQ-011 (artifact, Phase 5): Digest-pinnable runner image - The MoonMind-owned runner image must be separate, pinned, multi-arch, published by digest, and production-configurable by digest.
- DESIGN-REQ-012 (artifact, Phase 6): Strict findings schema - Normalized findings must validate as PentestFindingSet/PentestFinding and malformed records must be rejected or quarantined.
- DESIGN-REQ-013 (requirement, Phase 6): Accurate failure semantics - Results and reports must distinguish no findings from runner failure, provider failure, normalizer failure, and no machine-readable findings.
- DESIGN-REQ-014 (artifact, Target outcome): Report-first bundle - Successful runs publish report_bundle_v=1 with primary, summary, structured, evidence, and separate observability artifacts.
- DESIGN-REQ-015 (requirement, Phase 7): Mission Control report-first UX - Mission Control must prioritize report.primary, relate report artifacts, show narrowed discovery values, and keep observability separate.
- DESIGN-REQ-016 (security, Phase 7): Restricted evidence authorization - Restricted evidence must not expose raw bytes to unauthorized users.
- DESIGN-REQ-017 (constraint, Configuration requirements): Narrow task input schema - User inputs stay limited to the approved Pentest schema and deployment allowlists.
- DESIGN-REQ-018 (security, Configuration requirements): No dangerous task controls - Ordinary users cannot provide raw shell, Docker args, arbitrary images, host mounts, API keys, auth mode, telemetry switch, or terminal attach.
- DESIGN-REQ-019 (non-goal, Phase 8): VPN disabled unless complete - VPN/lab support remains hidden/disabled unless a reviewed curated VPN profile is fully implemented and tested.
- DESIGN-REQ-020 (security, Acceptance criteria): Conservative defaults - Default posture is disabled, approved-scope-required, telemetry-disabled, safe-profile-only, no external targets, no full_authorized unless approved, and VPN hidden.
- DESIGN-REQ-021 (migration, Phases 9-10): Staging checklist and rollout gates - Docs must define staging prerequisites, leak checks, cleanup/lease verification, and conservative rollout sequence.
- DESIGN-REQ-022 (observability, Target outcome): Compact workflow payloads - Workflow history/result payloads carry refs, counts, status, and metadata rather than raw logs, evidence, prompts, or blobs.
- DESIGN-REQ-023 (observability, Target outcome): Redacted heartbeats - Long-running execution emits compact redacted heartbeat phases.
- DESIGN-REQ-024 (constraint, Test plan): Boundary-focused test coverage - Hardening must include unit, integration, image, UI/API, and E2E smoke coverage at real boundaries.

## Ordered Story Candidates

### STORY-001: Extract dedicated Pentest activity boundary

- Short name: pentest-activity-boundary
- Source reference: inline user request
- Sections: Phase 1, Remaining gaps
- Dependencies: none
- Independent test: Run activity-boundary tests proving security.pentest.execute still binds to the public untrusted entrypoint, delegation preserves behavior, trusted inline scope remains internal-only, and redacted heartbeats/failure construction still work.
- Description: As a MoonMind operator, I need PentestGPT execution owned by a dedicated activity implementation so the generic runtime remains focused while the public Temporal contract stays stable.
- Acceptance criteria:
  - Pentest-specific logic moves to a dedicated Pentest activity module/class.
  - TemporalAgentRuntimeActivities.security_pentest_execute() remains a thin delegate.
  - _ACTIVITY_HANDLER_ATTRS["security.pentest.execute"] resolves to the public untrusted entrypoint.
  - Untrusted execution cannot self-authorize inline approved_scope.
- Requirements:
  - Preserve activity type and routing.
  - Keep validation/failure behavior unchanged during extraction.
  - Add workflow/activity boundary coverage.
- Source design coverage:
  - DESIGN-REQ-002: Pentest-specific execution logic must move to a dedicated activity module/class while preserving the public activity binding.
  - DESIGN-REQ-003: Untrusted execution must require scope_artifact_ref and reject inline self-authorization or invalid scope before side effects.
  - DESIGN-REQ-004: Runner profiles, operation modes, evidence levels, external targets, approvals, and time budgets must be enforced before provider lease, secret resolution, or launch.
  - DESIGN-REQ-023: Long-running execution emits compact redacted heartbeat phases.
  - DESIGN-REQ-024: Hardening must include unit, integration, image, UI/API, and E2E smoke coverage at real boundaries.

### STORY-002: Enforce pre-launch Pentest policy gates

- Short name: pentest-prelaunch-policy
- Source reference: inline user request
- Sections: Target outcome, Configuration requirements, Acceptance criteria
- Dependencies: STORY-001
- Independent test: Run denial tests for missing, expired, unauthorized, out-of-scope, and non-idempotent scope with spies proving provider lease, secret resolution, and Docker launch are never called.
- Description: As a security operator, I need invalid or unauthorized Pentest requests denied before leases, secrets, or Docker side effects.
- Acceptance criteria:
  - Tool discovery is enabled only by MOONMIND_PENTEST_ENABLED=true.
  - Public execution requires scope_artifact_ref.
  - Scope and deployment-policy denials happen before provider lease.
  - Secret resolution cannot happen before provider lease.
  - Only the narrow approved input schema is exposed.
  - Dangerous Docker, shell, secret, telemetry, auth, and terminal controls are not user-editable.
- Requirements:
  - Validate scope artifacts as PentestApprovedScope.
  - Enforce deployment policy from operator settings.
  - Return deterministic redacted denial results.
- Source design coverage:
  - DESIGN-REQ-001: security.pentest.run must be production-ready for approved lab and authorized assessment workflows while remaining operator-gated.
  - DESIGN-REQ-003: Untrusted execution must require scope_artifact_ref and reject inline self-authorization or invalid scope before side effects.
  - DESIGN-REQ-004: Runner profiles, operation modes, evidence levels, external targets, approvals, and time budgets must be enforced before provider lease, secret resolution, or launch.
  - DESIGN-REQ-006: Provider secrets resolve only after lease acquisition and never leak into logs, artifacts, history, result payloads, or UI previews.
  - DESIGN-REQ-017: User inputs stay limited to the approved Pentest schema and deployment allowlists.
  - DESIGN-REQ-018: Ordinary users cannot provide raw shell, Docker args, arbitrary images, host mounts, API keys, auth mode, telemetry switch, or terminal attach.
  - DESIGN-REQ-020: Default posture is disabled, approved-scope-required, telemetry-disabled, safe-profile-only, no external targets, no full_authorized unless approved, and VPN hidden.
  - DESIGN-REQ-024: Hardening must include unit, integration, image, UI/API, and E2E smoke coverage at real boundaries.

### STORY-003: Verify real Docker Pentest workload execution

- Short name: pentest-docker-e2e
- Source reference: inline user request
- Sections: Phase 2, Integration tests, End-to-end smoke test
- Dependencies: STORY-002
- Independent test: Run integration smoke tests with default-runner-profiles.yaml, pentestgpt-safe, a valid lab scope artifact, curated/local runner image, harmless local target, plus invalid scope, timeout, cancellation, and runner failure paths.
- Description: As an operator, I need valid lab-scope Pentest requests to run through the real Docker workload launcher and prove cleanup, compact outputs, and partial artifact behavior.
- Acceptance criteria:
  - Valid recon_only lab request launches through the real Docker workload launcher.
  - Declared runner outputs and report bundle artifacts exist.
  - Result/history contain refs, counts, status, and cleanup metadata rather than raw blobs.
  - Invalid scope never launches Docker.
  - Timeout/cancellation clean containers, release leases, and preserve partial artifacts.
- Requirements:
  - Use the real workload launcher rather than fake/file-writing launchers.
  - Assert cleanup metadata for every terminal path.
- Source design coverage:
  - DESIGN-REQ-007: Valid lab requests must run through the real Docker workload launcher using pentestgpt-safe and a curated runner image or local test tag.
  - DESIGN-REQ-008: Containers and leases must clean up on success, failure, timeout, cancellation, and cooldown while preserving partial diagnostics when available.
  - DESIGN-REQ-014: Successful runs publish report_bundle_v=1 with primary, summary, structured, evidence, and separate observability artifacts.
  - DESIGN-REQ-022: Workflow history/result payloads carry refs, counts, status, and metadata rather than raw logs, evidence, prompts, or blobs.
  - DESIGN-REQ-024: Hardening must include unit, integration, image, UI/API, and E2E smoke coverage at real boundaries.
- Assumptions:
  - Local E2E may use a locally built test tag when the published image is unavailable.

### STORY-004: Prove provider manager lifecycle

- Short name: pentest-provider-lifecycle
- Source reference: inline user request
- Sections: Phase 3, Integration tests
- Dependencies: STORY-002
- Independent test: Run staging/integration coverage against the actual provider profile manager for success, capacity failure, 429/quota cooldown, worker failure after lease, cancellation, timeout, and duplicate release.
- Description: As an operator, I need real provider-profile lease, release, cooldown, and redaction behavior across every terminal path.
- Acceptance criteria:
  - Leases are acquired only after scope/policy validation.
  - Secrets resolve only after lease acquisition.
  - Leases release on success, failure, cancellation, timeout, cooldown, and worker failure.
  - Duplicate release is idempotent.
  - Cooldown records and provider metadata are compact and redacted.
- Requirements:
  - Exercise the actual provider manager workflow.
  - Keep provider snapshots as observability artifacts.
- Source design coverage:
  - DESIGN-REQ-005: Provider leases and cooldowns must behave correctly across success, failure, capacity, cancellation, timeout, worker failure, and duplicate release.
  - DESIGN-REQ-006: Provider secrets resolve only after lease acquisition and never leak into logs, artifacts, history, result payloads, or UI previews.
  - DESIGN-REQ-008: Containers and leases must clean up on success, failure, timeout, cancellation, and cooldown while preserving partial diagnostics when available.
  - DESIGN-REQ-022: Workflow history/result payloads carry refs, counts, status, and metadata rather than raw logs, evidence, prompts, or blobs.
  - DESIGN-REQ-024: Hardening must include unit, integration, image, UI/API, and E2E smoke coverage at real boundaries.

### STORY-005: Resolve Pentest network posture semantics

- Short name: pentest-network-posture
- Source reference: inline user request
- Sections: Phase 4
- Dependencies: STORY-002, STORY-003
- Independent test: Choose and verify either accurate Docker-bridge documentation for approved lab scopes only or enforceable restricted-egress implementation with tests proving off-scope network attempts fail.
- Description: As a security reviewer, I need the safe profile network claims to match enforceable reality.
- Acceptance criteria:
  - Docs, launch metadata, runner profile names, and UI copy match actual enforcement.
  - External targets remain disabled until decision and review.
  - Restricted egress is claimed only if enforceable and tested.
  - If Docker bridge remains, operator surfaces avoid restricted-egress language.
- Requirements:
  - Implement exactly one network posture option.
  - Keep external targets disabled by default.
- Source design coverage:
  - DESIGN-REQ-009: Docs, metadata, profile names, and UI copy must match actual network enforcement; external targets stay disabled until reviewed.
  - DESIGN-REQ-020: Default posture is disabled, approved-scope-required, telemetry-disabled, safe-profile-only, no external targets, no full_authorized unless approved, and VPN hidden.
  - DESIGN-REQ-024: Hardening must include unit, integration, image, UI/API, and E2E smoke coverage at real boundaries.
- [NEEDS CLARIFICATION]:
  - Choose Option A documentation-only bridge posture or Option B enforceable restricted egress before specify.

### STORY-006: Harden Pentest runner image CI

- Short name: pentest-runner-ci
- Source reference: inline user request
- Sections: Phase 5, Image tests
- Dependencies: none
- Independent test: Run image CI checks for wrapper --version/self-test, upstream pentestgpt executable and flags, distinct failure classification, LANGFUSE default, sample redaction, deterministic artifact paths, manifest inspection, and default image publication.
- Description: As a release operator, I need runner image CI to prove upstream CLI compatibility, redaction, deterministic artifacts, and digest-pinnable publication.
- Acceptance criteria:
  - Runner image stays separate from the main MoonMind image.
  - CI validates wrapper and upstream CLI compatibility.
  - Upstream CLI failure is classified separately from wrapper/config failure.
  - LANGFUSE_ENABLED=false and redaction are verified.
  - Artifact paths are deterministic.
  - Digest is surfaced and production config can pin by digest.
  - CI fails if configured default image tag is unpublished.
- Requirements:
  - Support multi-arch build and manifest inspection.
  - Align published tags/digests with config defaults.
- Source design coverage:
  - DESIGN-REQ-010: Runner CI must validate wrapper smoke tests, upstream PentestGPT CLI contract, failure classification, redaction, and deterministic artifact output.
  - DESIGN-REQ-011: The MoonMind-owned runner image must be separate, pinned, multi-arch, published by digest, and production-configurable by digest.
  - DESIGN-REQ-020: Default posture is disabled, approved-scope-required, telemetry-disabled, safe-profile-only, no external targets, no full_authorized unless approved, and VPN hidden.
  - DESIGN-REQ-024: Hardening must include unit, integration, image, UI/API, and E2E smoke coverage at real boundaries.

### STORY-007: Validate normalized findings and report semantics

- Short name: pentest-findings-validation
- Source reference: inline user request
- Sections: Phase 6, Acceptance criteria
- Dependencies: STORY-001
- Independent test: Run normalizer and activity publication tests for valid findings, malformed records, empty output, runner failure, provider failure, and normalizer failure.
- Description: As a report consumer, I need schema-valid findings and accurate failure/no-findings semantics.
- Acceptance criteria:
  - Raw findings normalize into PentestFindingSet/PentestFinding.
  - Malformed records are rejected or quarantined.
  - Invalid records appear only in restricted diagnostics/evidence.
  - Results distinguish no findings, runner failed, provider failed, normalizer failed, and no machine-readable findings.
  - Human report does not imply no vulnerabilities after provider/tool/normalization failure.
  - Published report.structured is schema-valid.
- Requirements:
  - Use typed MoonMind Pentest models for validation.
  - Keep compact results limited to refs/status/counts.
- Source design coverage:
  - DESIGN-REQ-012: Normalized findings must validate as PentestFindingSet/PentestFinding and malformed records must be rejected or quarantined.
  - DESIGN-REQ-013: Results and reports must distinguish no findings from runner failure, provider failure, normalizer failure, and no machine-readable findings.
  - DESIGN-REQ-014: Successful runs publish report_bundle_v=1 with primary, summary, structured, evidence, and separate observability artifacts.
  - DESIGN-REQ-016: Restricted evidence must not expose raw bytes to unauthorized users.
  - DESIGN-REQ-022: Workflow history/result payloads carry refs, counts, status, and metadata rather than raw logs, evidence, prompts, or blobs.
  - DESIGN-REQ-024: Hardening must include unit, integration, image, UI/API, and E2E smoke coverage at real boundaries.

### STORY-008: Verify Mission Control report-first UX

- Short name: pentest-report-first-ui
- Source reference: inline user request
- Sections: Phase 7
- Dependencies: STORY-007
- Independent test: Run API/frontend tests for discovery gating, narrowed settings-derived values, full_authorized hiding, pentest-recon defaults, report.primary priority, related report artifacts, observability separation, and restricted evidence authorization.
- Description: As a Mission Control user, I need Pentest runs to surface report.primary first, relate report artifacts, and control raw evidence/observability access.
- Acceptance criteria:
  - Tool is hidden when disabled.
  - Discovery shows narrowed operation/evidence/time/profile values.
  - full_authorized is hidden unless policy enables it.
  - pentest-recon renders safe defaults.
  - Execution detail prioritizes report.primary and relates summary, structured, and evidence artifacts.
  - Runtime stdout/stderr/diagnostics stay in observability sections.
  - Restricted evidence raw bytes are authorization-gated.
  - No ordinary run exposes dangerous Docker/shell/terminal controls.
- Requirements:
  - Cover API discovery and Mission Control presentation.
  - Preserve shared report.* semantics.
- Source design coverage:
  - DESIGN-REQ-014: Successful runs publish report_bundle_v=1 with primary, summary, structured, evidence, and separate observability artifacts.
  - DESIGN-REQ-015: Mission Control must prioritize report.primary, relate report artifacts, show narrowed discovery values, and keep observability separate.
  - DESIGN-REQ-016: Restricted evidence must not expose raw bytes to unauthorized users.
  - DESIGN-REQ-017: User inputs stay limited to the approved Pentest schema and deployment allowlists.
  - DESIGN-REQ-018: Ordinary users cannot provide raw shell, Docker args, arbitrary images, host mounts, API keys, auth mode, telemetry switch, or terminal attach.
  - DESIGN-REQ-020: Default posture is disabled, approved-scope-required, telemetry-disabled, safe-profile-only, no external targets, no full_authorized unless approved, and VPN hidden.
  - DESIGN-REQ-024: Hardening must include unit, integration, image, UI/API, and E2E smoke coverage at real boundaries.

### STORY-009: Gate or defer VPN lab profile

- Short name: pentest-vpn-profile-gate
- Source reference: inline user request
- Sections: Phase 8
- Dependencies: STORY-002, STORY-005
- Independent test: Run settings/discovery/UI tests proving pentestgpt-vpn-lab is hidden/rejected when disabled; if implemented, validate NET_ADMIN, /dev/net/tun, named volume, network_attachment_ref, no arbitrary mounts, review gates, and hidden-unless-enabled behavior.
- Description: As a security reviewer, I need VPN/lab profile support hidden by default unless fully implemented, reviewed, and tested.
- Acceptance criteria:
  - VPN support remains hidden/rejected when MOONMIND_PENTEST_ALLOW_VPN_LAB_PROFILE=false.
  - Docs identify VPN as future work when deferred.
  - If implemented, VPN requires explicit reviewed controls and tests.
  - network_attachment_ref is user-editable only for approved VPN/lab profiles.
- Requirements:
  - Choose explicit deferral or full curated VPN implementation.
  - Do not expose partial VPN support.
- Source design coverage:
  - DESIGN-REQ-017: User inputs stay limited to the approved Pentest schema and deployment allowlists.
  - DESIGN-REQ-018: Ordinary users cannot provide raw shell, Docker args, arbitrary images, host mounts, API keys, auth mode, telemetry switch, or terminal attach.
  - DESIGN-REQ-019: VPN/lab support remains hidden/disabled unless a reviewed curated VPN profile is fully implemented and tested.
  - DESIGN-REQ-020: Default posture is disabled, approved-scope-required, telemetry-disabled, safe-profile-only, no external targets, no full_authorized unless approved, and VPN hidden.
  - DESIGN-REQ-024: Hardening must include unit, integration, image, UI/API, and E2E smoke coverage at real boundaries.
- [NEEDS CLARIFICATION]:
  - Choose explicit VPN deferral or full curated VPN implementation before specify.

### STORY-010: Document staging rollout gates

- Short name: pentest-rollout-gates
- Source reference: inline user request
- Sections: Phase 9, Phase 10
- Dependencies: STORY-003, STORY-004, STORY-005, STORY-006, STORY-008, STORY-009
- Independent test: Review PentestOperations checklist and run staging smoke verification for real runner/provider setup, recon_only, report bundles, no leaks, cleanup metadata, and lease release.
- Description: As an operator, I need staging checklist and conservative rollout gates before enabling PentestGPT broadly.
- Acceptance criteria:
  - PentestOperations documents runner image, digest pinning, Docker profiles, agent_runtime queue, Docker proxy, workspaces volume, artifact backend, provider profile/secrets, lab scope artifact, enablement, recon_only submission, report bundle verification, leak checks, cleanup, lease release, and disable-again guidance.
  - Hardening merges behind MOONMIND_PENTEST_ENABLED=false.
  - Local/dev lab tests precede staging provider-manager tests.
  - recon_only precedes validate_hypothesis.
  - full_authorized, VPN, and external targets remain disabled until separate review.
- Requirements:
  - Keep rollout guidance deployment-level.
  - Prefer digest-pinned runner image for staging/production.
- Source design coverage:
  - DESIGN-REQ-001: security.pentest.run must be production-ready for approved lab and authorized assessment workflows while remaining operator-gated.
  - DESIGN-REQ-011: The MoonMind-owned runner image must be separate, pinned, multi-arch, published by digest, and production-configurable by digest.
  - DESIGN-REQ-014: Successful runs publish report_bundle_v=1 with primary, summary, structured, evidence, and separate observability artifacts.
  - DESIGN-REQ-020: Default posture is disabled, approved-scope-required, telemetry-disabled, safe-profile-only, no external targets, no full_authorized unless approved, and VPN hidden.
  - DESIGN-REQ-021: Docs must define staging prerequisites, leak checks, cleanup/lease verification, and conservative rollout sequence.
  - DESIGN-REQ-024: Hardening must include unit, integration, image, UI/API, and E2E smoke coverage at real boundaries.

## Coverage Matrix

- DESIGN-REQ-001 Production gated PentestGPT tool: STORY-002, STORY-010
- DESIGN-REQ-002 Dedicated Pentest activity boundary: STORY-001
- DESIGN-REQ-003 Artifact-backed approved scope: STORY-001, STORY-002
- DESIGN-REQ-004 Deployment policy before side effects: STORY-001, STORY-002
- DESIGN-REQ-005 Provider lease lifecycle: STORY-004
- DESIGN-REQ-006 Secret-safe materialization: STORY-002, STORY-004
- DESIGN-REQ-007 Real Docker workload execution: STORY-003
- DESIGN-REQ-008 Cleanup and partial artifacts: STORY-003, STORY-004
- DESIGN-REQ-009 Truthful network semantics: STORY-005
- DESIGN-REQ-010 Runner CI upstream compatibility: STORY-006
- DESIGN-REQ-011 Digest-pinnable runner image: STORY-006, STORY-010
- DESIGN-REQ-012 Strict findings schema: STORY-007
- DESIGN-REQ-013 Accurate failure semantics: STORY-007
- DESIGN-REQ-014 Report-first bundle: STORY-003, STORY-007, STORY-008, STORY-010
- DESIGN-REQ-015 Mission Control report-first UX: STORY-008
- DESIGN-REQ-016 Restricted evidence authorization: STORY-007, STORY-008
- DESIGN-REQ-017 Narrow task input schema: STORY-002, STORY-008, STORY-009
- DESIGN-REQ-018 No dangerous task controls: STORY-002, STORY-008, STORY-009
- DESIGN-REQ-019 VPN disabled unless complete: STORY-009
- DESIGN-REQ-020 Conservative defaults: STORY-002, STORY-005, STORY-006, STORY-008, STORY-009, STORY-010
- DESIGN-REQ-021 Staging checklist and rollout gates: STORY-010
- DESIGN-REQ-022 Compact workflow payloads: STORY-003, STORY-004, STORY-007
- DESIGN-REQ-023 Redacted heartbeats: STORY-001
- DESIGN-REQ-024 Boundary-focused test coverage: STORY-001, STORY-002, STORY-003, STORY-004, STORY-005, STORY-006, STORY-007, STORY-008, STORY-009, STORY-010

## Dependencies

- STORY-001: none
- STORY-002: STORY-001
- STORY-003: STORY-002
- STORY-004: STORY-002
- STORY-005: STORY-002, STORY-003
- STORY-006: none
- STORY-007: STORY-001
- STORY-008: STORY-007
- STORY-009: STORY-002, STORY-005
- STORY-010: STORY-003, STORY-004, STORY-005, STORY-006, STORY-008, STORY-009

## Out Of Scope

- Creating spec.md files or specs/ directories: Breakdown only creates temporary story candidates.
- Implementing hardening changes: This run is limited to story extraction and coverage checking.
- Creating Jira issues: The output is Jira-ready but issue creation is a later workflow step.
- Enabling external targets, full_authorized, or VPN by default: The source keeps these disabled until separate review or full implementation.

## Coverage Gate

PASS - every major design point is owned by at least one story.

## Original Source Design

```text
Finish production hardening and rollout of MoonMind PentestGPT integration

Summary

Finish the MoonMind PentestGPT integration so security.pentest.run is production-ready for approved lab and authorized security assessment workflows.

This issue is no longer about building the integration from scratch. The current MoonMind repo already contains a substantial implementation behind an operator gate, including the Temporal activity route, untrusted scope-artifact loading, policy validation, provider profile resolution/materialization, Docker workload translation, report-first artifact publication, terminal cleanup metadata, a curated runner image, CI publishing, settings, docs, presets, and tests.

The remaining work is to harden the implementation, extract the Pentest-specific activity code out of the generic runtime class, prove the real Docker/provider paths end to end, close network-policy ambiguity, validate Mission Control report-first behavior, and define safe rollout gates.

Current repo state: substantial implementation exists for models, gated tool discovery, Temporal routing, untrusted scope-artifact loading, deployment policy, provider profile resolution/materialization, provider lease/cooldown flow, secret resolution before launch, Docker workload translation, pentestgpt-safe runner profile, curated runner image, CI publishing, report-first artifacts, cleanup metadata, docs, presets, and tests.

Remaining gaps: extract Pentest activity code from TemporalAgentRuntimeActivities; prove real Docker launcher behavior; prove real provider-manager behavior; reconcile restricted-egress language with Docker bridge; validate upstream PentestGPT CLI compatibility in runner CI; strictly validate structured findings; verify Mission Control report-first UX; keep VPN/lab profile disabled or fully implement it; add staging checklist and rollout gates.

Target outcome: a user submits an approved security.pentest.run task with target, scope_artifact_ref, objective, operation_mode, runner_profile_id, execution_profile_ref, time_budget_minutes, and evidence_level. MoonMind validates approved scope, denies invalid/expired/unauthorized/out-of-scope/non-idempotent requests before provider lease, secret resolution, or Docker launch, resolves and leases a PentestGPT provider profile, materializes secret-safe runtime env, launches a pinned MoonMind-owned runner through Docker workloads, emits compact redacted heartbeats, publishes runtime and report artifacts, releases leases and cleans containers on all terminal paths, and returns a compact PentestWorkloadResult.

Phases: 1 extract a dedicated Pentest activity module without behavior change and keep the public activity binding stable; 2 add real Docker end-to-end coverage and failure-path tests; 3 prove provider-manager lifecycle behavior; 4 resolve network-policy semantics by documenting bridge accurately or implementing enforceable restricted egress; 5 strengthen runner image CI with upstream CLI compatibility, redaction, deterministic artifacts, digest surfacing, and publication validation; 6 strictly validate normalized findings and distinguish no-findings from runner/provider/normalizer failures; 7 verify Mission Control report-first behavior and restricted evidence controls; 8 keep VPN/lab profile disabled or fully implement a reviewed curated VPN profile; 9 add staging checklist to docs/Security/PentestOperations.md; 10 define conservative rollout gates.

Configuration requirements: safe operator-facing settings remain deployment-level and not user-task-editable. Runtime prerequisites include Docker workload profiles mode, agent_runtime worker/task queue, Docker proxy, agent workspaces volume, Temporal artifact backend, and provider profile/secret refs. User task input schema remains narrow. Do not expose raw shell, Docker args, arbitrary images, host mounts, provider API keys, PENTESTGPT_AUTH_MODE, LANGFUSE_ENABLED, or terminal attach controls.

Test plan: unit tests for activity extraction, settings/discovery, scope loading, denial before side effects, provider lifecycle, secret-safe materialization, launch translation, runner validation, redaction, findings validation, failure classification, and cleanup metadata; integration tests for real Docker launcher, invalid scope, provider lease release, cooldowns, report bundles, compact workflow history, and Mission Control discovery; image tests for multi-arch, --version, --self-test, upstream CLI compatibility, default image publication, manifest inspection, and digest-pinned production; E2E smoke for recon_only against local lab/fake target with report/runtime artifacts, counts, cleanup, lease release, and no secret leaks.

Acceptance criteria: security.pentest.run is discoverable only when enabled; untrusted execution requires scope_artifact_ref; invalid scope fails before provider/secret/Docker side effects; valid lab scope runs through real Docker launcher; result is compact; successful runs publish report_bundle_v=1 with report.primary, report.summary, schema-valid report.structured, and report.evidence; observability artifacts remain separate; Mission Control shows report-first; secrets never leak; leases release and containers clean up on all terminal paths; runner image is owned, pinned, smoke-tested, multi-arch, and digest-pinnable; dangerous user inputs are not exposed; defaults remain conservative with Pentest disabled, approved scope required, telemetry disabled, pentestgpt-safe only, external targets disabled, full_authorized disabled unless approved, and VPN hidden unless reviewed.
```
