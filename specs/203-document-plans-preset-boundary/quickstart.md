# Quickstart: Document Plans Overview Preset Boundary

## Focused Documentation Contract Check

Run after updating `docs/MoonMindRoadmap.md`:

```bash
rg -n "control plane|PlanDefinition|flattened execution graphs|TaskPresetsSystem|SkillAndPlanContracts" docs/MoonMindRoadmap.md
```

Expected result:
- The plans overview includes a concise boundary clarification near the tasks, skills, presets, and plans section.
- Preset composition is described as a control-plane concern resolved before `PlanDefinition` creation.
- Runtime plans are described as flattened execution graphs of concrete nodes and edges.
- The paragraph links to both `TaskPresetsSystem` and `SkillAndPlanContracts`.

## No Canonical Migration Checklist Check

```bash
! rg -n "MM-389|Document plans overview preset boundary|preset boundary" docs --glob '!artifacts/**'
```

Expected result: no canonical docs outside `local-only handoffs` contain a new MM-389 migration checklist or story-specific backlog entry.

## Source Traceability Check

```bash
rg -n "MM-389|DESIGN-REQ-001|DESIGN-REQ-020|DESIGN-REQ-024|DESIGN-REQ-025|DESIGN-REQ-026" specs/203-document-plans-preset-boundary
```

Expected result: MM-389 and all in-scope source design requirements are present in MoonSpec artifacts.

## Full Unit Suite

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

Expected result: full unit suite passes. This documentation-contract story does not require new executable unit tests unless implementation changes runtime code.

## Hermetic Integration Suite

```bash
./tools/test_integration.sh
```

Run when Docker is available. This story does not require credentials or external providers.

## Final MoonSpec Verification

Run final verification against the active feature:

```text
/moonspec-verify
```

Expected result:
- The preserved MM-389 Jira preset brief is the canonical input.
- `docs/MoonMindRoadmap.md` satisfies FR-001 through FR-008.
- MoonSpec artifacts preserve MM-389 for FR-009.
- Verification records any environment blockers for full unit or integration commands.
