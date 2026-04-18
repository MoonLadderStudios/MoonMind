# Contract: Proposal Promotion Preset Provenance

## Scope

This contract defines the observable Task Proposal System behavior required by MM-388.

## Required Contract Outcomes

1. Proposal invariants state preset-derived metadata is advisory UX/reconstruction metadata and not a runtime dependency.
2. Stored proposal payload examples may include optional `task.authoredPresets` and `steps[].source` metadata alongside execution-ready flat steps.
3. Default promotion validates and submits the reviewed flat task payload without live preset catalog lookup.
4. Default promotion preserves authored preset metadata and per-step provenance when present unless the operator intentionally overrides those fields through a validated override.
5. Default promotion does not live re-expand presets.
6. Any refresh-latest preset workflow is explicit operator-selected behavior and not the default promotion path.
7. Proposal generators may preserve reliable parent-run preset provenance and must not fabricate bindings when reliable binding metadata is unavailable.
8. Proposal detail and observability can distinguish manual work, preset-derived work with preserved binding metadata, and preset-derived flattened-only work.

## Verification Commands

```bash
rg -n "preset-derived metadata|authoredPresets|live preset catalog|live re-expansion|refresh-latest|flattened-only|fabricate.*binding|preset provenance" docs/Tasks/TaskProposalSystem.md
rg -n "MM-388|DESIGN-REQ-015|DESIGN-REQ-019|DESIGN-REQ-023|DESIGN-REQ-025|DESIGN-REQ-026" specs/202-document-proposal-promotion docs/tmp/jira-orchestration-inputs/MM-388-moonspec-orchestration-input.md
```
