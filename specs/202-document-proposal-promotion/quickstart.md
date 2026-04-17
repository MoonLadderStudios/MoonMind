# Quickstart: Proposal Promotion Preset Provenance

## Focused Documentation Contract Check

```bash
rg -n "preset-derived metadata|authoredPresets|live preset catalog|live re-expansion|refresh-latest|flattened-only|fabricate.*binding|preset provenance" docs/Tasks/TaskProposalSystem.md
```

Expected result: matches show proposal invariants, payload examples, default promotion behavior, refresh-latest explicitness, generator guidance, and proposal detail/observability states.

## Source Traceability Check

```bash
rg -n "MM-388|DESIGN-REQ-015|DESIGN-REQ-019|DESIGN-REQ-023|DESIGN-REQ-025|DESIGN-REQ-026" specs/202-document-proposal-promotion docs/tmp/jira-orchestration-inputs/MM-388-moonspec-orchestration-input.md
```

Expected result: all source IDs and the Jira issue key remain visible in MoonSpec artifacts and the canonical orchestration input.

## Full Unit Suite

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

Expected result: pass, or record an exact environment blocker in verification evidence.

## End-to-End Review

Review `docs/Tasks/TaskProposalSystem.md` and confirm:

- preset-derived metadata is advisory UX/reconstruction metadata,
- proposal promotion avoids live preset catalog lookup and live re-expansion by default,
- optional `task.authoredPresets` and per-step `source` provenance can coexist with flat executable steps,
- promotion preserves provenance by default,
- refresh-latest behavior is explicit,
- generators preserve reliable provenance but do not fabricate bindings,
- detail/observability distinguishes manual, preserved-binding preset-derived, and flattened-only proposal states.
