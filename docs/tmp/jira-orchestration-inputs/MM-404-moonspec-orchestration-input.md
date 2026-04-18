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
