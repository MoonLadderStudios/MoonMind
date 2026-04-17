# Quickstart: Mission Control Preset Provenance Surfaces

## Focused Documentation Contract Check

```bash
rg -n "Preset provenance|Manual|Preset path|unresolved preset includes|Expansion summaries|subtask|sub-plan|separate workflow" docs/UI/MissionControlArchitecture.md
```

Expected result: matches show Mission Control preview, task detail, submit, evidence hierarchy, and vocabulary rules for preset provenance.

## Source Traceability Check

```bash
rg -n "MM-387|DESIGN-REQ-014|DESIGN-REQ-015|DESIGN-REQ-022|DESIGN-REQ-025|DESIGN-REQ-026" specs/200-mission-control-preset-provenance docs/tmp/jira-orchestration-inputs/MM-387-moonspec-orchestration-input.md
```

Expected result: all source IDs and the Jira issue key remain visible in MoonSpec artifacts and the canonical orchestration input.

## Full Unit Verification

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

Expected result: pass, unless blocked by unrelated environment constraints.

## End-To-End Review

Review `docs/UI/MissionControlArchitecture.md` and confirm:

- preview, edit/rerun, task list, and task detail surfaces can explain preset-derived work;
- task detail allows Manual, Preset, and Preset path chips or summaries;
- flat steps remain primary execution order;
- `/tasks/new` forbids unresolved preset includes as runtime work;
- expansion summaries remain secondary evidence;
- vocabulary avoids subtask, sub-plan, and separate workflow-run labels for preset includes.
