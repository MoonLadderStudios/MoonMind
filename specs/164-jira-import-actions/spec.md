# Feature Specification: Jira Import Actions

**Feature Branch**: `164-jira-import-actions`  
**Created**: 2026-04-13  
**Status**: Draft  
**Input**: User description: "Implement Phase 5 using test-driven development of the Jira UI plan. Wire Jira issue import into existing Create page preset Feature Request / Initial Instructions and any step Instructions field. The Jira browser must keep issue selection read-only until an explicit import action. Add Replace target text and Append to target text actions. Support import modes: Preset brief, Execution brief, Description only, and Acceptance criteria only. Preset target behavior: write imported content into the existing preset objective text, preserve objective precedence that favors feature-request text first, and if a preset has already been applied mark the UI as preset instructions changed and requiring explicit reapply without silently mutating existing expanded steps. Step target behavior: write imported content only into the selected step instructions and reuse the existing step update path so importing into a template-bound step counts as a manual edit and detaches template identity when instructions diverge. Jira must remain additive and must not change task submission contracts or expose Jira credentials to the browser. Required deliverables include production runtime code changes (not docs/spec-only) plus validation tests."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Import Jira Into Preset Objective (Priority: P1)

An operator composing a preset-driven task can browse to a Jira story, preview the normalized story content, choose the desired import mode, and explicitly copy that content into the preset Feature Request / Initial Instructions field.

**Why this priority**: Preset objective text is the highest-priority objective source for preset-driven Create page workflows, so Jira import must support this target before the feature delivers its core value.

**Independent Test**: Can be tested by opening the Jira browser from the preset objective field, selecting an issue, choosing each import mode, and confirming that Replace or Append changes only the preset objective text.

**Acceptance Scenarios**:

1. **Given** Jira integration is enabled and the Jira browser is opened from the preset objective field, **When** the operator selects an issue and chooses Replace target text, **Then** the preset Feature Request / Initial Instructions field contains the selected import text.
2. **Given** the preset objective field already contains text, **When** the operator selects an issue and chooses Append to target text, **Then** the existing text remains and the imported Jira text is added after a clear separator.
3. **Given** the operator changes the import mode before importing, **When** the operator imports the issue, **Then** the copied text matches the selected mode rather than the default mode.

---

### User Story 2 - Import Jira Into a Selected Step (Priority: P1)

An operator composing a multi-step task can open the shared Jira browser from a specific step and explicitly copy Jira story content into only that step's Instructions field.

**Why this priority**: Step instructions are the canonical authored execution units on the Create page, and Jira import must not accidentally rewrite unrelated steps or task fields.

**Independent Test**: Can be tested by creating multiple steps, opening the Jira browser from one step, importing an issue, and verifying that only that step's instructions changed.

**Acceptance Scenarios**:

1. **Given** the Jira browser is opened from a step's Instructions field, **When** the operator imports an issue with Replace target text, **Then** only that step's Instructions field is replaced.
2. **Given** another step and the preset objective field already contain text, **When** the operator imports into the selected step, **Then** those other fields remain unchanged.
3. **Given** the operator imports into the primary step while the preset objective field is empty, **When** the task objective is resolved, **Then** the primary step instructions can satisfy the objective as before.

---

### User Story 3 - Preserve Preset Reapply Semantics (Priority: P2)

An operator who already applied a preset can import Jira text into the preset objective without the page silently rewriting the expanded preset-derived steps.

**Why this priority**: Preset application is explicit today; Jira import must not create hidden changes to the step list or make it unclear when the preset needs to be regenerated.

**Independent Test**: Can be tested by applying a preset, importing Jira text into the preset objective, and confirming that existing expanded steps remain unchanged while the page signals that reapply is needed.

**Acceptance Scenarios**:

1. **Given** a preset has already been applied, **When** Jira import changes the preset objective field, **Then** the page shows a non-blocking message that preset instructions changed and the preset should be reapplied to regenerate preset-derived steps.
2. **Given** expanded preset steps are present, **When** Jira import changes only the preset objective field, **Then** those existing steps are not automatically rewritten or removed.

---

### User Story 4 - Treat Jira Import as a Manual Template-Step Edit (Priority: P2)

An operator can import Jira text into a preset-expanded step, and the step behaves like a manually customized step when its instructions no longer match the template-provided instructions.

**Why this priority**: Template-bound identity must remain trustworthy so submitted tasks do not claim that a customized step still matches its preset blueprint.

**Independent Test**: Can be tested by applying a preset, importing Jira text into a template-bound step, submitting the task, and verifying the customized step no longer carries the original template-step identity.

**Acceptance Scenarios**:

1. **Given** a step was expanded from a preset and still matches its template instructions, **When** Jira import replaces that step's instructions with different text, **Then** the step is treated as manually customized.
2. **Given** a task is submitted after Jira import customized a template-bound step, **When** submitted step metadata is inspected, **Then** the customized step does not preserve the original template-step identity.

### Edge Cases

- If no Jira issue is selected, import actions must not change any draft field.
- If the selected Jira issue has empty text for the chosen import mode, import actions must not erase existing target text.
- If the target step no longer exists while the browser is open, the import action must not change another step by mistake.
- If Jira browser loading fails, manual editing and task creation must remain available.
- If Jira integration is disabled, no Jira import entry points or actions should be visible.
- If a user appends into an empty target, the result should be the imported text without a leading separator.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The Create page MUST provide explicit Jira import actions for replacing target text and appending to target text after an issue has been selected.
- **FR-002**: The system MUST keep issue selection and preview read-only until an operator chooses an explicit import action.
- **FR-003**: Users MUST be able to choose among Preset brief, Execution brief, Description only, and Acceptance criteria only before importing.
- **FR-004**: The default import mode MUST be Preset brief when the Jira browser opens from the preset objective target.
- **FR-005**: The default import mode MUST be Execution brief when the Jira browser opens from any step instructions target.
- **FR-006**: Importing to the preset target MUST update only the Feature Request / Initial Instructions field.
- **FR-007**: Importing to the preset target MUST preserve the existing objective precedence in which non-empty preset objective text is preferred over primary step instructions.
- **FR-008**: If a preset has already been applied, changing preset objective text through Jira import MUST show a non-blocking reapply-needed message and MUST NOT automatically rewrite existing expanded steps.
- **FR-009**: Importing to a step target MUST update only the selected step's Instructions field.
- **FR-010**: Importing to a step target MUST use the same manual-edit behavior as direct instruction editing, including detaching template-step identity when imported instructions diverge from template instructions.
- **FR-011**: Append behavior MUST preserve existing target text and add the imported text after a clear separator.
- **FR-012**: Replace behavior MUST replace the current target text with the selected import text.
- **FR-013**: Jira import MUST remain additive and MUST NOT block manual task creation when Jira browsing or issue loading fails.
- **FR-014**: Browser clients MUST continue using MoonMind-owned Jira browser data and MUST NOT receive or require raw Jira credentials.
- **FR-015**: The feature MUST NOT change the task submission contract; Jira import changes authored field text only.
- **FR-016**: Required deliverables MUST include production runtime code changes and validation tests; docs-only or spec-only output is insufficient for this runtime-intent feature.

### Key Entities

- **Jira Issue Import**: A one-time copy operation from a selected Jira issue into a Create page target. Key attributes include target, import mode, write action, selected issue, and imported text.
- **Import Target**: The destination field chosen by the operator. It is either the preset Feature Request / Initial Instructions field or a specific step Instructions field.
- **Import Mode**: The operator-selected shape of issue text to copy: Preset brief, Execution brief, Description only, or Acceptance criteria only.
- **Template-Bound Step**: A preset-expanded step that still carries template identity while its instructions match the template-provided instructions.

### Assumptions

- Jira browsing and issue preview capability already exist behind the enabled Jira Create page experience.
- Imported Jira text is a one-time copy into the draft, not an ongoing sync with the Jira issue.
- Jira issue provenance does not need to be persisted in submitted task payloads for this phase.
- Manual task authoring remains the fallback path whenever Jira is unavailable.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: In validation tests, selecting a Jira issue without pressing Replace or Append results in zero changes to preset and step instruction fields.
- **SC-002**: In validation tests, 100% of supported import modes copy the expected text into the selected target.
- **SC-003**: In validation tests, importing into one step changes exactly one step and leaves all other steps and the preset objective unchanged.
- **SC-004**: In validation tests, importing into the preset objective after preset application preserves the existing expanded steps and displays a reapply-needed message.
- **SC-005**: In validation tests, a Jira import into a template-bound step that changes instructions causes the customized step to lose template-step identity before submission.
- **SC-006**: Manual task creation remains possible after simulated Jira browser failure in validation tests.
- **SC-007**: Existing Create page submission behavior remains compatible, with no new required task payload fields for Jira provenance or Jira-specific task types.
