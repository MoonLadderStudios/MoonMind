Result: already implemented.

I reviewed this issue against the current codebase. Preset-derived workflows now surface the preset slug in the operator-visible Skill field, while the executable runtime skill remains the real agent skill so preset names are not submitted as agent skill bundles.

| Evidence | Details |
| --- | --- |
| Create submission preserves preset provenance | `frontend/src/entrypoints/workflow-start.tsx` keeps `appliedStepTemplates` on the task payload and submits the executable first-step skill through `task.tool`, `task.skill`, and `task.skills`. |
| Execution projection surfaces preset as Skill | `api_service/api/routers/executions.py` derives `targetSkill` and `taskSkills` from `task.taskTemplate` or the latest `appliedStepTemplates` entry when no workflow-level skill is otherwise available. |
| Task list renders that field | `frontend/src/entrypoints/workflow-list.tsx` renders the Skill column from `formatTaskSkills(row.taskSkills, row.targetSkill)`. |
| Regression coverage | `tests/unit/api/routers/test_executions.py` covers task-template and applied-template slugs appearing as primary skill values. |
| Related merged PRs | #1194 originally added preset slug surfacing for the Skill column; #1619 moved the projection into API serialization; #1977 preserved executable skill submission while retaining preset provenance. |

Verification run:

- `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh --python-only tests/unit/api/routers/test_executions.py -k 'template_slug_as_primary_skill or applied_template_slug_as_primary_skill or latest_applied_template_as_primary_skill'` -> 3 passed, 224 deselected.

I also attempted the focused frontend regression command, but local JS dependencies are not installed in this workspace, so `npm run ui:test -- frontend/src/entrypoints/workflow-start.test.tsx -t 'submits Jira Orchestrate preset runs with the executable first-step skill'` could not start because `vitest` was not found.

Closing this as completed.
