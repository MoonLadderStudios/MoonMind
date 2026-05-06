# Feature Specification: Codex Session Skill Projection Stability

**Feature Branch**: `307-codex-session-skill-projection`
**Created**: 2026-05-06
**Status**: Draft
**Input**: Managed Codex `pr-resolver` runs can receive instructions pointing to `.agents/skills/<skill>/SKILL.md` after the cold session launch has replaced the workspace and removed the active skill projection.

## User Scenarios & Testing

### User Story 1 - Selected Skills Survive Cold Session Launch (Priority: P1)

As an operator running a managed Codex task with a selected MoonMind skill, I need Codex to see the resolved active skill snapshot at `.agents/skills` when the turn starts, even if the session launch has to clone or repair the workspace.

**Independent Test**: Start a cold Codex managed session with a selected skill and verify turn preparation runs after session launch, so the prepared instructions are based on the final workspace state.

**Acceptance Scenarios**:

1. **Given** a selected skill and a cold managed Codex session, **When** MoonMind launches the session and sends the first turn, **Then** `.agents/skills/<skill>/SKILL.md` is present in the runtime-visible workspace before Codex receives instructions to read it.
2. **Given** the managed session launch creates or replaces the repo checkout, **When** turn instructions are prepared, **Then** preparation observes the post-launch workspace rather than a pre-launch placeholder.
3. **Given** preparation produces durable retrieval metadata, **When** a cold session is launched, **Then** launch metadata includes that compact durable metadata without relying on a pre-launch skill projection.
4. **Given** MoonMind-generated compatibility skill links exist in a workspace, **When** post-agent publishing stages changed files, **Then** projection-only skill symlinks are not committed to the target repository.

## Requirements

### Functional Requirements

- **FR-001**: Managed Codex session startup MUST establish the final workspace before selected-skill turn instruction preparation materializes `.agents/skills`.
- **FR-002**: Selected-skill instruction preparation MUST continue to validate the active `.agents/skills` projection before a turn is sent.
- **FR-003**: Launch-safe preparation MUST be able to collect durable retrieval metadata before launch without installing selected-skill projections into a pre-launch workspace.
- **FR-004**: Runtime-generated skill projection links under `.agents/skills`, `.gemini/skills`, and root `skills_active` MUST be excluded from publish staging when they are symlinks.
- **FR-005**: Checked-in, non-symlink skill directories MUST remain publishable when they are user-authored content, not runtime projections.
- **FR-006**: Existing pr-resolver result contract behavior MUST remain unchanged; missing `var/pr_resolver/result.json` still fails the run.

## Success Criteria

- **SC-001**: A regression test proves cold Codex sessions prepare turn instructions only after launch has completed.
- **SC-002**: A regression test proves launch metadata still includes durable retrieval metadata produced by preparation.
- **SC-003**: A regression test proves generated `.gemini/skills` and `skills_active` symlink projections are excluded from publish staging.
- **SC-004**: Existing managed-session adapter and agent-runtime activity tests continue to pass.
