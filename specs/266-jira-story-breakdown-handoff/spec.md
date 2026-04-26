# Feature Specification: Jira Story Breakdown Handoff

## User Story

As an operator running Jira Breakdown orchestration, I want generated story breakdown output to be consumed reliably by the Jira creation step even when the source branch is protected, so the workflow can create Jira stories without depending on an unpublished local commit.

## Requirements

- The planner MUST treat `branch` as the source/base branch for Jira story-output workflows, not as the writable handoff branch.
- When Jira story output needs repository publication, the planner MUST create a distinct `targetBranch` if one was not explicitly supplied.
- `story.create_jira_issues` MUST prefer inline story payloads, prior step story-output payloads, and `storyBreakdownArtifactRef` before fetching `storyBreakdownPath` from GitHub.
- If the previous step reports `push_status = protected_branch` and Jira creation cannot read the generated story breakdown, the failure MUST name the protected-branch handoff problem instead of surfacing only a raw GitHub 404.
- Regression tests MUST cover the protected-branch planner case, artifact-backed Jira creation, previous-output Jira creation, and the improved protected-branch error.

## Acceptance Criteria

- Given a Jira Breakdown workflow with `git.branch = main`, when the plan is generated, then the breakdown and Jira steps share a generated `targetBranch` that is not `main`, and `startingBranch` remains `main`.
- Given `storyBreakdownArtifactRef`, when `story.create_jira_issues` runs, then it reads the artifact and does not fetch the repository path.
- Given prior step outputs containing stories, when `story.create_jira_issues` runs, then it creates Jira issues from those stories.
- Given a protected-branch publish failure and no readable handoff payload, when `story.create_jira_issues` runs, then it fails with an actionable protected-branch handoff message.
