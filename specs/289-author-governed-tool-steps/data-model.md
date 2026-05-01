# Data Model: Author Governed Tool Steps

## Trusted Tool Definition

- `name`: canonical tool id returned by trusted discovery, for example `jira.transition_issue`.
- `description`: human-readable purpose.
- `inputSchema`: JSON schema metadata for contract visibility.

Validation:
- Tool definitions come from `/mcp/tools` only.
- Missing discovery data does not invalidate manual Tool authoring.

## Tool Choice Group

- `group`: namespace or domain derived from the tool name before the first dot.
- `tools`: matching trusted tool definitions.

Validation:
- Search filters by group, tool name, and description.
- Empty search results do not clear existing authored Tool values.

## Dynamic Jira Target Status Option

- `name`: target status name returned by trusted `jira.get_transitions` response.
- `transitionId`: returned transition id, visible only as trusted metadata if needed; submission for this story writes target status, not guessed ids.

Validation:
- Options are loaded only after `jira.transition_issue` is selected and `issueKey` is present in Tool inputs JSON.
- Selecting a status updates the Tool inputs JSON object while preserving other keys.
