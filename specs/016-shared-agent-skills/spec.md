# Feature Specification: Unified Agent Skills Directory

**Feature Branch**: `016-shared-agent-skills`  
**Created**: 2026-02-15  
**Status**: Draft  
**Input**: User description: "Create a technical design so MoonMind uses one shared skills directory for both Codex CLI and Gemini CLI with per-run selection and secure materialization."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Shared Skills Discovery for Both CLIs (Priority: P1)

As a worker runtime, I want one active skills directory to be discovered by both Codex and Gemini so both agents operate on the same skill set without duplication.

**Why this priority**: The main objective is one source-of-truth for runtime skills.

**Independent Test**: Materialize a run workspace, verify both `.agents/skills` and `.gemini/skills` exist as symlinks to the same `skills_active` directory, and confirm both CLIs list identical skills.

**Acceptance Scenarios**:

1. **Given** an active run workspace, **When** MoonMind inspects skill adapters, **Then** `.agents/skills` resolves to `skills_active`.
2. **Given** an active run workspace, **When** MoonMind inspects skill adapters, **Then** `.gemini/skills` resolves to the same `skills_active`.
3. **Given** one skill folder in `skills_active`, **When** either CLI performs discovery, **Then** both CLIs expose the same skill metadata.

---

### User Story 2 - Per-Run Skill Selection Without Global Mutation (Priority: P1)

As a platform operator, I want queue/job policy to select skills per run so workers can vary skill availability safely without mutating user-global CLI configs.

**Why this priority**: Shared infrastructure workers need deterministic, isolated run-level behavior.

**Independent Test**: Execute two runs with different skill policies and verify each run sees only its selected skills while global `~/.codex` and `~/.gemini` state remains unchanged.

**Acceptance Scenarios**:

1. **Given** queue defaults and job overrides, **When** a run starts, **Then** MoonMind resolves the effective skill list using precedence rules.
2. **Given** run A and run B with different selected skills, **When** each workspace is materialized, **Then** each run receives only its own `skills_active` links.
3. **Given** run materialization, **When** runtime setup completes, **Then** no worker code mutates global skill enablement state.

---

### User Story 3 - Trusted Skill Supply and Runtime Guardrails (Priority: P2)

As a security owner, I want skill artifacts verified and workers constrained so automation can stay non-interactive while minimizing supply-chain and prompt-injection risk.

**Why this priority**: Skills may include scripts and must be treated as trusted code.

**Independent Test**: Attempt to materialize a tampered skill artifact and a duplicate-skill-name set, verify both are rejected with clear diagnostics, and confirm fallback safety defaults remain intact.

**Acceptance Scenarios**:

1. **Given** a skill artifact hash mismatch, **When** materialization runs, **Then** the run fails before activation.
2. **Given** duplicate skill names in the selected set, **When** materialization validates metadata, **Then** the run fails with a collision error.
3. **Given** Gemini headless execution, **When** strict mode is configured, **Then** worker runtime uses explicit approval/tool policies instead of unrestricted defaults.

### Edge Cases

- Skill source fetch succeeds but `SKILL.md` is missing or invalid Agent Skills metadata.
- Skill folder name does not match declared skill `name`.
- Selected skill is allowlisted by workflow stage policy but absent from registry/version mapping.
- Immutable cache path exists but is partially written from a prior interrupted fetch.
- Workspace symlink creation collides with pre-existing non-symlink directory content.
- Codex and Gemini runtime sessions execute concurrently against the same cached artifact set.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: MoonMind MUST maintain one canonical skill artifact format based on Agent Skills (`SKILL.md` + skill directory payload).
- **FR-002**: MoonMind MUST resolve an effective per-run skill set from queue defaults plus job-level overrides.
- **FR-003**: MoonMind MUST fetch selected skills into an immutable content-addressed cache keyed by verified digest.
- **FR-004**: MoonMind MUST validate skill artifact integrity before activation using expected hash and optional signature metadata.
- **FR-005**: MoonMind MUST build run-local `skills_active` as symlinks to immutable cache entries.
- **FR-006**: MoonMind MUST create `.agents/skills` and `.gemini/skills` symlinks that both point to the same run-local `skills_active`.
- **FR-007**: MoonMind MUST reject activation when duplicate skill names exist in a run’s selected set.
- **FR-008**: MoonMind MUST avoid mutating global CLI skill configuration files to enforce per-run isolation.
- **FR-009**: MoonMind MUST emit structured materialization metadata (selected skills, versions, digests, source URIs, and activation status) per run.
- **FR-010**: MoonMind MUST support skill sources from private git and object storage bundle URIs defined in MoonMind registry metadata.
- **FR-011**: MoonMind MUST fail fast with actionable errors when materialization cannot produce a valid shared directory.
- **FR-012**: Technical design and follow-on implementation validation MUST use `./tools/test_unit.sh` for unit tests.

### Key Entities *(include if feature involves data)*

- **SkillRegistryEntry**: Canonical mapping of `skill_name` + `version` to immutable source URI, digest, optional signature, and compatibility notes.
- **RunSkillSelection**: Resolved per-run list of selected skills with provenance (`queue_default`, `job_override`).
- **SkillCacheRecord**: Immutable on-disk cache entry for one verified artifact digest.
- **RunSkillWorkspace**: Run-scoped directory containing `skills_active` and adapter symlinks (`.agents/skills`, `.gemini/skills`).
- **MaterializationAuditEvent**: Structured run event capturing resolver inputs, validation outcomes, and activation paths.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of successful run materializations produce `.agents/skills` and `.gemini/skills` symlinks to a single `skills_active` directory.
- **SC-002**: For controlled test runs with different skill policies, each run’s active skill set matches resolved policy with no cross-run leakage.
- **SC-003**: Integrity or metadata validation failures are detected before CLI execution and reported with deterministic error codes/messages.
- **SC-004**: Skill materialization telemetry includes selected skill names, versions, digests, and activation result for every run attempt.
- **SC-005**: Design-driven implementation validation passes through `./tools/test_unit.sh`.

## Assumptions

- Existing workflow stage routing and skill allowlist controls remain in `moonmind/workflows/skills/` and are extended, not replaced.
- MoonMind workers already run in containerized runtime contexts where run workspaces can be isolated and cleaned up.
- Existing `.codex/skills` mirroring is a legacy path and can be deprecated in favor of `.agents/skills` plus `.gemini/skills`.
