# Feature Specification: Jira Breakdown and Orchestrate Skill

**Feature Branch**: `207-jira-breakdown-orchestrate-skill`  
**Created**: 2026-04-18  
**Status**: Draft  
**Input**:

```text
# MM-404 MoonSpec Orchestration Input

## Source

- Jira issue: MM-404
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Create the Jira Breakdown and Orchestrate skill
- Labels: None
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-404 from MM project
Summary: Create the Jira Breakdown and Orchestrate skill
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-404 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-404: Create the Jira Breakdown and Orchestrate skill

Summary

Create the Jira Breakdown and Orchestrate skill.

Description

Create a Jira Breakdown and Orchestrate skill that performs the normal Jira Breakdown workflow. At the end of the breakdown, it should create tasks for each generated story to run Jira Orchestrate on that story.

The skill should also set up task dependencies so later generated tasks run only after earlier generated tasks are complete.

Acceptance Criteria

- The skill performs the normal Jira Breakdown workflow.
- After breakdown completes, the skill creates a task for each generated story.
- Each generated task runs Jira Orchestrate on its corresponding story.
- The skill creates task dependencies between generated tasks.
- Later generated tasks do not run until earlier generated tasks complete.
- MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata preserve Jira issue key MM-404.

Relevant Implementation Notes

- Treat this as a MoonMind agent skill/orchestration feature, not a raw Jira API script.
- Reuse the existing Jira Breakdown and Jira Orchestrate workflows instead of duplicating their behavior.
- Preserve generated story order when creating downstream Jira Orchestrate tasks.
- Dependency creation must be explicit enough that the task scheduler can enforce the earlier-task-before-later-task ordering.
- Keep Jira operations behind MoonMind's trusted Jira tool surface.

Out of Scope

- Replacing the existing Jira Breakdown workflow.
- Replacing the existing Jira Orchestrate workflow.
- Running downstream story implementation inline inside the breakdown step instead of creating dependent tasks.

Verification

- Verify the skill can run the normal Jira Breakdown flow for a broad Jira issue.
- Verify generated stories produce downstream Jira Orchestrate tasks.
- Verify dependency ordering prevents later tasks from starting before earlier tasks complete.
- Verify MM-404 and the original synthesized Jira preset brief are preserved in MoonSpec artifacts and delivery metadata.
```

**Canonical Jira Brief**: `spec.md` (Input)

## Original Jira Preset Brief

Jira issue: MM-404 from MM project
Summary: Create the Jira Breakdown and Orchestrate skill
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-404 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-404: Create the Jira Breakdown and Orchestrate skill

Summary

Create the Jira Breakdown and Orchestrate skill.

Description

Create a Jira Breakdown and Orchestrate skill that performs the normal Jira Breakdown workflow. At the end of the breakdown, it should create tasks for each generated story to run Jira Orchestrate on that story.

The skill should also set up task dependencies so later generated tasks run only after earlier generated tasks are complete.

Acceptance Criteria

- The skill performs the normal Jira Breakdown workflow.
- After breakdown completes, the skill creates a task for each generated story.
- Each generated task runs Jira Orchestrate on its corresponding story.
- The skill creates task dependencies between generated tasks.
- Later generated tasks do not run until earlier generated tasks complete.
- MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata preserve Jira issue key MM-404.

Relevant Implementation Notes

- Treat this as a MoonMind agent skill/orchestration feature, not a raw Jira API script.
- Reuse the existing Jira Breakdown and Jira Orchestrate workflows instead of duplicating their behavior.
- Preserve generated story order when creating downstream Jira Orchestrate tasks.
- Dependency creation must be explicit enough that the task scheduler can enforce the earlier-task-before-later-task ordering.
- Keep Jira operations behind MoonMind's trusted Jira tool surface.

Out of Scope

- Replacing the existing Jira Breakdown workflow.
- Replacing the existing Jira Orchestrate workflow.
- Running downstream story implementation inline inside the breakdown step instead of creating dependent tasks.

Verification

- Verify the skill can run the normal Jira Breakdown flow for a broad Jira issue.
- Verify generated stories produce downstream Jira Orchestrate tasks.
- Verify dependency ordering prevents later tasks from starting before earlier tasks complete.
- Verify MM-404 and the original synthesized Jira preset brief are preserved in MoonSpec artifacts and delivery metadata.

## User Story - Jira Breakdown to Ordered Orchestration

**Summary**: As a MoonMind operator, I want one Jira Breakdown and Orchestrate skill to break down a broad Jira issue, create one Jira Orchestrate task for each generated story, and order those tasks by dependency.

**Goal**: Operators can start a single workflow from a broad Jira issue and receive an ordered set of dependent story-implementation tasks without manually copying each generated story into separate Jira Orchestrate runs.

**Independent Test**: Run the skill with a Jira issue that breaks down into at least three stories, then verify that it completes the normal breakdown, creates exactly one Jira Orchestrate task per generated story, and records dependencies so task 2 waits for task 1 and task 3 waits for task 2.

**Acceptance Scenarios**:

1. **Given** a Jira issue can be broken into multiple ordered stories, **When** the Jira Breakdown and Orchestrate skill runs, **Then** it performs the normal Jira Breakdown workflow before creating downstream implementation tasks.
2. **Given** the breakdown returns ordered stories, **When** downstream tasks are created, **Then** each generated story has exactly one corresponding Jira Orchestrate task.
3. **Given** downstream Jira Orchestrate tasks are created, **When** task dependencies are inspected, **Then** each later task depends on completion of the immediately earlier task.
4. **Given** the first downstream task is not complete, **When** later downstream tasks are evaluated for execution, **Then** they remain blocked until the required earlier task completes.
5. **Given** the breakdown creates no independently implementable stories, **When** the skill finishes, **Then** it reports that no downstream Jira Orchestrate tasks were created rather than claiming orchestration success.
6. **Given** downstream MoonSpec artifacts, implementation notes, verification, commit text, or pull request metadata are generated, **When** traceability is reviewed, **Then** Jira issue key MM-404 and the original Jira preset brief remain present.

### Edge Cases

- The breakdown yields a single story; the skill creates one Jira Orchestrate task and records that no inter-task dependency is needed.
- A generated story cannot be converted into a downstream task; the skill reports the failed story and does not report full orchestration success.
- Dependency creation fails after some downstream tasks are created; the skill reports partial task creation and the missing dependency evidence.
- A generated story lacks enough Jira issue context for Jira Orchestrate; the skill reports the missing context for that story instead of inventing an issue key.
- The target Jira issue is inaccessible through trusted Jira tooling; the skill stops with an operator-readable blocked result.

## Assumptions

- Runtime mode is selected, so this specification describes observable skill behavior and validation outcomes rather than documentation-only changes.
- Generated story order from the normal Jira Breakdown workflow is the dependency order unless the breakdown output carries a more specific dependency order.
- Existing Jira Breakdown and Jira Orchestrate behavior remains authoritative; this feature composes them instead of redefining their individual internals.
- One downstream Jira Orchestrate task per generated story is the correct default because the Jira brief asks to create tasks for each story.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST provide a Jira Breakdown and Orchestrate skill discoverable as a reusable MoonMind skill.
- **FR-002**: The skill MUST accept a Jira issue as the source input for the breakdown.
- **FR-003**: The skill MUST perform the normal Jira Breakdown workflow before attempting to create downstream Jira Orchestrate tasks.
- **FR-004**: The skill MUST preserve generated story order from the breakdown result.
- **FR-005**: For each generated story, the skill MUST create exactly one downstream Jira Orchestrate task unless that story is explicitly reported as failed or skipped.
- **FR-006**: Each downstream task MUST preserve the generated story's Jira issue context and enough source traceability for Jira Orchestrate to run on that story.
- **FR-007**: The skill MUST create dependencies so each later downstream task waits for the immediately earlier downstream task to complete.
- **FR-008**: When the breakdown yields exactly one story, the skill MUST create one downstream Jira Orchestrate task and report that no inter-task dependency was required.
- **FR-009**: When no downstream tasks can be created, the skill MUST report a no-downstream-task outcome rather than reporting orchestration success.
- **FR-010**: When downstream task creation or dependency creation fails partially, the skill MUST report which stories, tasks, and dependencies succeeded or failed.
- **FR-011**: The skill MUST keep Jira operations behind MoonMind's trusted Jira tool surface and MUST NOT require raw Jira credentials in an agent runtime.
- **FR-012**: The skill MUST NOT replace or redefine the existing Jira Breakdown workflow.
- **FR-013**: The skill MUST NOT replace or redefine the existing Jira Orchestrate workflow.
- **FR-014**: The skill MUST NOT run downstream story implementation inline inside the breakdown step.
- **FR-015**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata for this work MUST preserve Jira issue key `MM-404` and the original Jira preset brief.

### Key Entities

- **Source Jira Issue**: The broad Jira issue supplied to the skill and used as the input for normal Jira Breakdown.
- **Generated Story**: One independently implementable story emitted by the breakdown workflow, including order and source traceability.
- **Downstream Jira Orchestrate Task**: A task created for one generated story so Jira Orchestrate can implement that story through its normal lifecycle.
- **Task Dependency**: An ordering relationship that prevents a later downstream task from running until the immediately earlier downstream task is complete.
- **Orchestration Result**: The skill outcome summarizing generated stories, created downstream tasks, dependency links, skipped stories, failures, and traceability.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For a breakdown result containing three valid stories, the skill creates three downstream Jira Orchestrate tasks.
- **SC-002**: For a breakdown result containing three valid stories, the skill records two ordering dependencies: task 2 waits for task 1, and task 3 waits for task 2.
- **SC-003**: For a single-story breakdown result, the skill creates one downstream Jira Orchestrate task and records zero inter-task dependencies.
- **SC-004**: For a zero-story breakdown result, the skill creates zero downstream Jira Orchestrate tasks and reports a no-downstream-task outcome.
- **SC-005**: A partial downstream creation failure identifies every succeeded task, failed story, created dependency, and missing dependency in the orchestration result.
- **SC-006**: Review of the skill behavior confirms downstream story implementation is delegated to created Jira Orchestrate tasks rather than executed inline during breakdown.
- **SC-007**: Final verification confirms `MM-404` and the original Jira preset brief are preserved in MoonSpec artifacts and delivery metadata.
