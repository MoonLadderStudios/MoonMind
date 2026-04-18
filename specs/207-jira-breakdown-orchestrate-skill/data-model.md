# Data Model: Jira Breakdown and Orchestrate Skill

## Source Orchestration Request

**Purpose**: Operator input for the composite workflow.

Fields:

- `featureRequest`: source design text, source path, or Jira-derived brief to feed into normal Jira Breakdown.
- `jiraProjectKey`: Jira project for generated story issues.
- `jiraIssueType`: Jira issue type for generated story issues.
- `jiraDependencyMode`: Jira issue-link dependency mode, initially `linear_blocker_chain` or `none`.
- `orchestrationMode`: downstream Jira Orchestrate mode, default `runtime`.
- `repository`: target repository for downstream Jira Orchestrate tasks.
- `runtime`: target runtime selection for downstream tasks.
- `publish`: downstream publish behavior.

Validation rules:

- `featureRequest` must be non-empty.
- Jira target fields must be validated through existing trusted Jira metadata paths where required.
- Runtime and publish selections must reuse the canonical task submission validation path.

## Generated Story

**Purpose**: One independently implementable story emitted by normal Jira Breakdown.

Fields:

- `storyId`
- `storyIndex`
- `summary`
- `description`
- `sourceReference`
- `dependencies`

Relationships:

- One generated story maps to zero or one created Jira story issue.
- One generated story maps to zero or one downstream Jira Orchestrate task.

Validation rules:

- `storyIndex` defines stable order when more specific dependencies are absent.
- `sourceReference.path` is required before Jira issue creation when the story comes from a breakdown file.

## Jira Story Issue Mapping

**Purpose**: Structured output from Jira issue creation, used as input to downstream task creation.

Fields:

- `storyId`
- `storyIndex`
- `summary`
- `issueKey`
- `issueId`
- `self`
- `created` or `existing`

Relationships:

- Ordered mappings drive downstream task creation order.
- `issueKey` becomes the Jira issue key for one Jira Orchestrate task.

Validation rules:

- `issueKey` must be present before a downstream Jira Orchestrate task can be created.
- Mappings are sorted by `storyIndex` before dependency wiring.

## Downstream Jira Orchestrate Task Request

**Purpose**: Task-shaped request for one generated Jira story issue.

Fields:

- `jiraIssueKey`
- `title`
- `instructions`
- `runtime`
- `publish`
- `repository`
- `dependsOn`
- `idempotencyKey`
- `sourceStoryId`
- `sourceStoryIndex`

Relationships:

- The first downstream task has no `dependsOn` value.
- Each later downstream task depends on the immediately previous downstream task's `workflowId`.

Validation rules:

- `dependsOn` may reference only an already-created `MoonMind.Run` workflow ID.
- Each direct `dependsOn` list must stay within the existing task dependency limit.
- The request must preserve the source Jira issue key and MM-404 traceability.

## Downstream Jira Orchestrate Task Result

**Purpose**: Durable creation result for a downstream task.

Fields:

- `storyId`
- `storyIndex`
- `jiraIssueKey`
- `workflowId`
- `created`
- `existing`
- `dependsOn`
- `errorCode`
- `message`

Relationships:

- Successful task results produce dependency inputs for the next task in order.
- Failed task results stop or mark incomplete downstream dependency wiring for later stories.

Validation rules:

- A successful result must include `workflowId`.
- A failed result must include an operator-readable message.

## Task Dependency Edge

**Purpose**: Ordering relationship between created downstream tasks.

Fields:

- `fromWorkflowId`
- `toWorkflowId`
- `fromStoryId`
- `toStoryId`
- `status`
- `message`

Validation rules:

- Edges are linear in v1: task N depends on task N-1.
- The edge is represented in the dependent task's create request as `dependsOn: [fromWorkflowId]`.

## Orchestration Result

**Purpose**: Structured outcome for the composite workflow.

Fields:

- `status`: `completed`, `partial`, `no_downstream_tasks`, or `failed`
- `storyCount`
- `createdTaskCount`
- `dependencyCount`
- `tasks`
- `dependencies`
- `skippedStories`
- `failures`
- `traceability`

Validation rules:

- `completed` is valid only when every generated story with a Jira issue key has a downstream task and every required dependency edge is represented.
- `partial` is required when any task or dependency fails after at least one downstream task succeeds.
- `no_downstream_tasks` is required when zero downstream tasks are created from a zero-story or all-skipped result.
- `traceability` must include `MM-404`.
