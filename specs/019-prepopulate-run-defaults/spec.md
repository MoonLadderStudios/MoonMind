# Feature Specification: Dashboard Task Run Defaults

**Feature Branch**: `019-prepopulate-run-defaults`  
**Created**: 2026-02-17  
**Status**: Draft  
**Input**: User description: "I want to set defaults if nothing is specified for MoonMind to run tasks using codex, model gpt-5.3-codex, high effort, MoonLadderStudios/MoonMind repo. Default values should be retrieved from settings and pre-populated in the UI boxes for easy adjustment as desired in the dashboard."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Apply Defaults on Submit (Priority: P1)

As a dashboard operator, I can submit a task run without manually filling provider/model/effort/repository every time, and MoonMind applies configured defaults automatically.

**Why this priority**: This is the core workflow improvement that removes repetitive input and prevents failed runs due to missing values.

**Independent Test**: Leave provider/model/effort/repository empty in a run submission and confirm the resulting run uses configured defaults.

**Acceptance Scenarios**:

1. **Given** default run values exist in settings, **When** a user submits a run with those fields omitted, **Then** the run executes with settings-derived defaults.
2. **Given** default run values are unavailable in settings, **When** a user submits with omissions, **Then** the system applies baseline fallback values and still executes.

---

### User Story 2 - Pre-Populate Dashboard Inputs (Priority: P2)

As a dashboard operator, I see provider/model/effort/repository fields pre-populated when opening the run form, so I can quickly submit or make small edits.

**Why this priority**: Pre-population improves speed and visibility of what will be used before submission.

**Independent Test**: Open the dashboard run form and verify fields are pre-filled with current settings values, then adjust a value and submit.

**Acceptance Scenarios**:

1. **Given** default run values exist in settings, **When** the run form loads, **Then** all related input boxes are pre-populated with those values.
2. **Given** a user edits one or more pre-populated fields before submit, **When** they run the task, **Then** the submitted run uses edited values for that run.

---

### User Story 3 - Keep Defaults in Sync with Settings (Priority: P3)

As an administrator, I can change defaults in settings and have future run forms and fallback behavior use the updated defaults.

**Why this priority**: Ensures configuration remains centralized and operators do not depend on stale hardcoded UI values.

**Independent Test**: Update default settings, reload the run form, and submit a run with omitted fields to verify updated defaults are used.

**Acceptance Scenarios**:

1. **Given** defaults are updated in settings, **When** users next load the run form, **Then** the form reflects updated values.
2. **Given** defaults are updated in settings, **When** users submit with omitted fields, **Then** runtime fallback resolution uses updated values.

### Edge Cases

- Settings store is reachable but one default field is missing; the system should apply a safe fallback for only the missing field.
- Settings values are present but invalid (for example malformed repository slug/URL); submission should fail fast with a clear validation error.
- UI loads before settings response completes; fields should avoid displaying misleading values and then update once defaults are available.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST store and expose dashboard run defaults in settings for provider, model, effort, and repository.
- **FR-002**: System MUST resolve omitted run input fields from settings defaults during run submission.
- **FR-003**: System MUST use baseline fallback defaults (`codex`, `gpt-5.3-codex`, `high`, `MoonLadderStudios/MoonMind`) when settings values are absent.
- **FR-004**: System MUST pre-populate dashboard run form input boxes from current settings defaults on load.
- **FR-005**: System MUST allow users to override pre-populated values per run without mutating persisted defaults.
- **FR-006**: System MUST apply updated settings defaults to future form loads and fallback resolution without requiring code changes.
- **FR-007**: System MUST validate repository defaults as token-free references (`owner/repo`, `https://...`, or `git@...`) before run submission.
- **FR-008**: System MUST include automated validation tests that cover default resolution and UI pre-population behavior.

### Key Entities *(include if feature involves data)*

- **RunDefaultsSettings**: Persisted default values for `provider`, `model`, `effort`, and `repository`, with update timestamp.
- **TaskRunInput**: Runtime payload for task execution that may include explicit user values or unresolved blanks.
- **ResolvedTaskRunConfig**: Final task-run configuration after applying explicit inputs plus settings-derived defaults.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of task runs submitted with omitted provider/model/effort/repository fields resolve to non-empty final values.
- **SC-002**: 100% of dashboard run-form loads pre-populate provider/model/effort/repository fields from settings or baseline fallbacks.
- **SC-003**: Operators can submit a default-config run in 30 seconds or less without manually entering those four fields.
- **SC-004**: Automated tests covering default resolution and dashboard pre-population pass in CI for this feature.
