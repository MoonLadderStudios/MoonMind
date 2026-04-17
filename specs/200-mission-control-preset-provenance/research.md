# Research: Mission Control Preset Provenance Surfaces

## Runtime Intent

Decision: Treat MM-387 as runtime UI behavior requirements expressed through the canonical Mission Control architecture contract.
Rationale: The user explicitly selected runtime mode, and the Jira brief asks Mission Control surfaces to explain preset-derived work without changing runtime execution semantics.
Alternatives considered: A docs-only story was rejected because docs mode was not explicitly requested.

## Canonical Target

Decision: Use `docs/UI/MissionControlArchitecture.md` as the active target.
Rationale: The Jira brief names MissionControlArchitecture as the source section, and this document already owns task list, detail, submit, artifact, and vocabulary behavior.
Alternatives considered: `docs/Tasks/PresetComposability.md` is referenced by the Jira issue but is absent in the current checkout; the reference is preserved for traceability.

## Validation Strategy

Decision: Use focused `rg` contract checks plus end-to-end review against the spec and source design mappings.
Rationale: The implementation target is a canonical architecture contract; checking for required terms and reading the updated sections gives direct evidence without starting services.
Alternatives considered: Full frontend unit tests are not required unless executable React behavior changes.

## Evidence Hierarchy

Decision: Keep expansion summaries secondary to flat steps, logs, diagnostics, and output artifacts.
Rationale: This preserves the runtime execution model and avoids implying nested workflow runs or sub-plans for preset includes.
Alternatives considered: Promoting expansion summaries to primary evidence was rejected because it conflicts with MM-387 and cross-document invariants.
