# Feature Specification: Mission Control Styling Source and Build Invariants

**Feature Branch**: `225-preserve-styling-invariants`
**Created**: 2026-04-22
**Status**: Implemented
**Input**: Trusted Jira preset brief for MM-430 from `spec.md` (Input). Summary: "Preserve Mission Control styling source and build invariants." Source design: `docs/UI/MissionControlDesignSystem.md`, sections 1 and 13.

## Original Jira Preset Brief

Jira issue: MM-430 from MM project
Summary: Preserve Mission Control styling source and build invariants
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-430 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-430: Preserve Mission Control styling source and build invariants

Source Reference
Source Document: docs/UI/MissionControlDesignSystem.md
Source Title: Mission Control Design System
Source Sections:
- 1. Purpose
- 13. Implementation invariants
Coverage IDs:
- DESIGN-REQ-001
- DESIGN-REQ-024
- DESIGN-REQ-025
- DESIGN-REQ-026

User Story
As a maintainer, I want Mission Control styling to remain token-first, semantically named, and built from the canonical source files so future UI work does not drift into hardcoded colors or hand-edited build artifacts.

Acceptance Criteria
- Existing semantic shell class names such as dashboard-root, masthead, route-nav, panel, card, toolbar, status-*, and queue-* remain stable where applicable.
- New shared styling uses additive modifiers such as panel--controls, panel--data, panel--floating, panel--utility, or table-wrap--wide where useful.
- Mission Control semantic classes consume --mm-* tokens instead of introducing hardcoded opaque colors for tokenized roles.
- Light and dark themes stay behaviorally identical through token swaps rather than scattered one-off overrides.
- Tailwind content scanning includes the documented template and frontend source paths.
- Design-system changes edit frontend/src/styles/mission-control.css or source components/templates, not generated dist assets.

Requirements
- Protect semantic class stability.
- Enforce token-first theming.
- Validate Tailwind source scanning.
- Preserve the canonical styling source and generated-artifact boundary.

Implementation Notes
- Preserve MM-430 in MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/UI/MissionControlDesignSystem.md` as the source design reference for Mission Control purpose, semantic styling conventions, and implementation invariants.
- Scope implementation to Mission Control styling source preservation, semantic class stability, token-first theming, Tailwind content scanning, and generated-asset boundary checks.
- Keep styling changes in `frontend/src/styles/mission-control.css` or source components/templates; do not hand-edit generated dist assets.
- Ensure light and dark themes continue to vary through `--mm-*` token swaps rather than scattered one-off overrides.

Dependencies
- Trusted Jira link metadata at fetch time shows MM-430 blocks MM-429, whose embedded status is Code Review. This is not a blocker for MM-430 and is ignored for dependency gating.

## Classification

Single-story runtime feature request. The brief contains one independently testable maintainer outcome: Mission Control styling must stay semantic, token-first, and generated from canonical source inputs rather than drifting into hardcoded colors, scattered theme overrides, missing source scans, or hand-edited build artifacts.

## User Story - Preserve Styling Invariants

**Summary**: As a Mission Control maintainer, I want styling source and build invariants to be enforced so future UI work keeps stable semantic classes, token-first theming, complete source scanning, and generated-asset boundaries.

**Goal**: Mission Control keeps a stable styling contract for maintainers: shared semantic shell classes remain compatible, new shared variants use additive modifier naming, tokenized roles use `--mm-*` variables, light and dark themes behave through token swaps, Tailwind scans all documented source paths, and generated assets are treated as build outputs rather than hand-edited sources.

**Independent Test**: Inspect Mission Control source styling, component/template class usage, Tailwind content configuration, and generated asset boundaries. The story passes when semantic class compatibility is preserved, tokenized styling avoids opaque hardcoded role colors, documented source paths are included in Tailwind scanning, and source changes are made outside generated dist assets while existing Mission Control behavior remains intact.

**Acceptance Scenarios**:

1. **Given** existing Mission Control shared surfaces, **when** source styling and templates/components are inspected, **then** semantic shell class names such as `dashboard-root`, `masthead`, `route-nav`, `panel`, `card`, `toolbar`, `status-*`, and compatible `queue-*` classes remain stable where applicable.
2. **Given** shared styling needs additional surface variants, **when** new class names are introduced, **then** they use additive semantic modifiers such as `panel--controls`, `panel--data`, `panel--floating`, `panel--utility`, or `table-wrap--wide` where those patterns fit the role.
3. **Given** Mission Control semantic classes define colors, borders, shadows, or surfaces for tokenized roles, **when** the stylesheet is inspected, **then** those classes consume `--mm-*` tokens instead of adding hardcoded opaque colors for roles that already have token meanings.
4. **Given** light and dark themes are compared, **when** matching Mission Control surfaces render in each theme, **then** they remain behaviorally identical through token value swaps rather than scattered one-off dark-mode overrides.
5. **Given** Tailwind builds Mission Control CSS, **when** its content scanning configuration is inspected, **then** it includes `api_service/templates/react_dashboard.html`, `api_service/templates/_navigation.html`, and `frontend/src/**/*.{js,jsx,ts,tsx}`.
6. **Given** Mission Control styling or template/component sources change, **when** repository diffs are inspected, **then** canonical source files such as `frontend/src/styles/mission-control.css` or source components/templates change, and generated dist assets under `api_service/static/task_dashboard/dist/` are not hand-edited.

### Edge Cases

- Existing compatibility names may remain only where current surfaces still depend on them; unused compatibility selectors should not be expanded into new conventions without a source-backed reason.
- Token-first enforcement must still allow transparent, inherited, currentColor, gradient, and computed color values when they do not represent opaque role colors.
- Visual state selectors must keep semantic meaning when token values change between light and dark themes.
- Tailwind scanning must include templates that can contain utilities before Vite output exists.
- Build artifacts may be regenerated by a build process, but direct hand edits to generated dist files must not become the source of truth.

## Assumptions

- Runtime mode is selected; `docs/UI/MissionControlDesignSystem.md` is treated as runtime source requirements, not a documentation-only target.
- The trusted Jira preset brief for MM-430 is the canonical orchestration input and must be preserved in downstream artifacts and PR metadata.
- Mission Control source styling currently lives in `frontend/src/styles/mission-control.css`, with templates/components under the documented source paths.
- This story validates styling/build invariants for Mission Control only; unrelated dashboard behavior changes are out of scope unless required to preserve the documented styling contract.
- The non-blocking Jira link where MM-430 blocks MM-429 does not gate this story.

## Source Design Requirements

- **DESIGN-REQ-001** (`docs/UI/MissionControlDesignSystem.md` section 1): Mission Control's design system must preserve its operational, futuristic, high-function interface identity while remaining functional and coherent for real task work. Scope: in scope. Mapped to FR-001, FR-002, FR-003, FR-004, FR-008.
- **DESIGN-REQ-024** (`docs/UI/MissionControlDesignSystem.md` section 13.1): Shared Mission Control surfaces must prefer stable semantic class names, preserving existing shell classes and using additive modifier classes for new shared variants. Scope: in scope. Mapped to FR-001, FR-002, FR-008.
- **DESIGN-REQ-025** (`docs/UI/MissionControlDesignSystem.md` sections 13.2 and 13.3): Mission Control theming must be token-first, avoid hardcoded opaque role colors and scattered dark overrides, and include all documented Mission Control source paths in Tailwind scanning. Scope: in scope. Mapped to FR-003, FR-004, FR-005, FR-008.
- **DESIGN-REQ-026** (`docs/UI/MissionControlDesignSystem.md` sections 13.4 and 13.5): Mission Control styling source of truth must remain `frontend/src/styles/mission-control.css`; generated dist assets must not be hand-edited, and enhanced surfaces must retain complete source-backed CSS behavior. Scope: in scope. Mapped to FR-006, FR-007, FR-008.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Existing shared Mission Control semantic shell class names MUST remain stable where their surfaces still exist, including `dashboard-root`, `masthead`, `route-nav`, `panel`, `card`, `toolbar`, `status-*`, and compatible `queue-*` classes.
- **FR-002**: New shared Mission Control styling variants MUST use additive semantic modifier naming where useful, including patterns equivalent to `panel--controls`, `panel--data`, `panel--floating`, `panel--utility`, and `table-wrap--wide`.
- **FR-003**: Mission Control semantic classes MUST consume `--mm-*` tokens for tokenized color, surface, border, shadow, and atmosphere roles instead of introducing hardcoded opaque colors for those roles.
- **FR-004**: Light and dark Mission Control themes MUST remain behaviorally identical through token value swaps rather than scattered one-off overrides for matching surfaces.
- **FR-005**: Tailwind content scanning MUST include `api_service/templates/react_dashboard.html`, `api_service/templates/_navigation.html`, and `frontend/src/**/*.{js,jsx,ts,tsx}`.
- **FR-006**: Mission Control styling changes MUST treat `frontend/src/styles/mission-control.css` and source components/templates as canonical source files.
- **FR-007**: Generated assets under `api_service/static/task_dashboard/dist/` MUST NOT be hand-edited as the source of truth for Mission Control styling changes.
- **FR-008**: Automated verification MUST cover semantic class stability, additive modifier usage, token-first style rules, light/dark token behavior, Tailwind source scanning, and generated-asset boundary protection.
- **FR-009**: Existing Mission Control task-list, task-creation, navigation, filtering, pagination, and detail/evidence behavior MUST remain unchanged.
- **FR-010**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve MM-430 and the trusted Jira preset brief.

### Key Entities

- **Semantic Shell Class**: A stable Mission Control class name that identifies a shared surface or state role and may be reused across templates, source components, and styles.
- **Additive Modifier Class**: A semantic class suffix that extends a shared surface role without replacing the base shell class.
- **Tokenized Role**: A style role represented by `--mm-*` variables so themes can swap values without changing component behavior.
- **Canonical Styling Source**: Source files that define Mission Control styling behavior, especially `frontend/src/styles/mission-control.css` and source templates/components.
- **Generated Asset**: Build output under `api_service/static/task_dashboard/dist/` that may be regenerated but is not the authoring source of truth.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Source verification confirms all applicable existing semantic shell classes remain present or intentionally unchanged for current Mission Control shared surfaces.
- **SC-002**: Source verification confirms new shared variants, when present, use additive semantic modifiers rather than replacing stable shell classes.
- **SC-003**: Style verification confirms tokenized semantic role colors and surfaces consume `--mm-*` tokens and do not introduce new hardcoded opaque colors for those roles.
- **SC-004**: Theme verification confirms representative light and dark surfaces vary through token values while preserving matching layout, state, and interaction behavior.
- **SC-005**: Build configuration verification confirms Tailwind scans the documented template and frontend source paths.
- **SC-006**: Diff/build-boundary verification confirms Mission Control styling changes are authored in source files and generated dist assets are not hand-edited.
- **SC-007**: Existing Mission Control regression tests for task-list, task-creation, navigation, filtering, pagination, and detail/evidence behavior continue to pass.
- **SC-008**: Traceability verification confirms MM-430, the trusted Jira preset brief, and DESIGN-REQ-001, DESIGN-REQ-024, DESIGN-REQ-025, and DESIGN-REQ-026 are preserved in MoonSpec artifacts and final evidence.
