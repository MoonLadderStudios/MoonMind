# Quickstart: Document Task Snapshot And Compilation Boundary

## Focused Documentation Contract Checks

Run after updating `docs/Tasks/TaskArchitecture.md`:

```bash
rg -n "Preset compilation|authoredPresets|source\\?|include-tree|detachment state|live preset catalog" docs/Tasks/TaskArchitecture.md
```

Expected result:
- `Preset compilation` appears as a control-plane subsection.
- `authoredPresets` appears in the representative task payload.
- `source?` appears in the representative step payload.
- Snapshot durability mentions include-tree summary and detachment state.
- Execution-plane language states workers do not depend on live preset catalog correctness.

## Source Traceability Check

```bash
rg -n "MM-385|DESIGN-REQ-015|DESIGN-REQ-017|DESIGN-REQ-018|DESIGN-REQ-019|DESIGN-REQ-025|DESIGN-REQ-026" specs/198-document-task-snapshot-boundary docs/tmp/jira-orchestration-inputs/MM-385-moonspec-orchestration-input.md
```

Expected result: MM-385 and all in-scope source design requirements are present in MoonSpec artifacts.

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
- The preserved MM-385 Jira preset brief is the canonical input.
- `docs/Tasks/TaskArchitecture.md` satisfies FR-001 through FR-008.
- MoonSpec artifacts preserve MM-385 for FR-009.
- Verification records any environment blockers for full unit or integration commands.
