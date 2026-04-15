# Quickstart: Jira Chain Blockers

## Focused Unit Validation

Run story-output tool tests:

```bash
./tools/test_unit.sh tests/unit/workflows/temporal/test_story_output_tools.py
```

Expected coverage:

- `linear_blocker_chain` creates adjacent blocker link results after issue creation.
- `none` creates issues without link requests.
- Partial link failure reports partial success and preserves created issue keys.
- Retry/reuse reports existing issue and link state.
- Unsupported dependency modes fail fast or fallback before Jira mutation according to fallback policy.

Run trusted Jira service tests:

```bash
./tools/test_unit.sh tests/unit/integrations/test_jira_tool_service.py
```

Expected coverage:

- Jira issue-link request model validates issue keys and self-links.
- `JiraToolService` posts through the trusted Jira client boundary.
- Allowed action and project policy apply to link creation.
- Results are sanitized and compact.

Run preset expansion tests:

```bash
./tools/test_unit.sh tests/unit/api/test_task_step_templates_service.py
```

Expected coverage:

- The seeded Jira Breakdown preset exposes dependency mode input.
- Expansion passes `none` and `linear_blocker_chain` instructions to the Jira creation step.
- Existing Jira Orchestrate and Jira Breakdown seed behavior remains intact.

## Final Verification

Before `/speckit.verify`, run:

```bash
./tools/test_unit.sh
```

If any integration-ci behavior is touched beyond the planned unit boundaries, also run:

```bash
./tools/test_integration.sh
```
