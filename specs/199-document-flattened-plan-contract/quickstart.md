# Quickstart: Document Flattened Plan Execution Contract

## Focused Documentation Contract Checks

Run after updating `docs/Tasks/SkillAndPlanContracts.md`:

```bash
rg -n "authoring concern|flattened execution contract|unresolved preset include|binding_id|include_path|blueprint_step_slug|detached|provenance" docs/Tasks/SkillAndPlanContracts.md
```

Expected result:
- Preset composition is described as an authoring concern.
- Stored plans are described as flattened execution contracts after expansion.
- Unresolved preset include entries are explicitly invalid in stored plan artifacts.
- Plan node examples include all four provenance fields.
- Provenance is described as optional traceability metadata, not executable logic.

## Validation Rule Check

```bash
rg -n "absent provenance|invalid claimed preset provenance|unresolved preset include|nested preset semantics|never executable logic" docs/Tasks/SkillAndPlanContracts.md
```

Expected result:
- Validation allows absent provenance.
- Validation rejects unresolved include entries.
- Validation rejects structurally invalid claimed preset provenance.
- Execution invariants state nested preset semantics do not exist at runtime.
- Execution invariants state provenance is never executable logic.

## Source Traceability Check

```bash
rg -n "MM-386|DESIGN-REQ-001|DESIGN-REQ-019|DESIGN-REQ-020|DESIGN-REQ-021|DESIGN-REQ-025|DESIGN-REQ-026" specs/199-document-flattened-plan-contract docs/tmp/jira-orchestration-inputs/MM-386-moonspec-orchestration-input.md
```

Expected result: MM-386 and all in-scope source design requirements are present in MoonSpec artifacts.

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
- The preserved MM-386 Jira preset brief is the canonical input.
- `docs/Tasks/SkillAndPlanContracts.md` satisfies FR-001 through FR-011.
- MoonSpec artifacts preserve MM-386 for FR-012.
- Verification records any environment blockers for full unit or integration commands.
