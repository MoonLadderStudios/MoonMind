# Contract: Report Rollout Semantics

## Purpose

Define the runtime-visible contract that MM-497 verifies for incremental report rollout behavior.

## 1. Generic Output Compatibility

- Existing non-report workflows continue to use generic artifact link types such as:
  - `output.primary`
  - `output.summary`
  - `output.agent_result`
- Generic outputs are valid deliverables and must not be reclassified as reports solely through local heuristics or summary-like metadata.

## 2. Explicit Report Workflow Semantics

- New report-producing workflows opt into canonical report behavior through explicit report link types:
  - `report.primary`
  - `report.summary`
  - `report.structured`
  - `report.evidence`
- `report.primary` is the canonical human-facing report indicator for report-producing workflows.
- `output.primary` is a generic fallback, not a substitute for `report.primary` in report-producing workflows.

## 3. Incremental Rollout Contract

- MoonMind supports staged rollout rather than a flag-day migration.
- Existing generic outputs remain valid during the rollout.
- New report workflows should prefer explicit `report.*` semantics.
- UI and API consumers must degrade gracefully when only generic outputs exist.

## 4. Representative Workflow Families

The shared rollout contract must support representative mappings for at least:

| Workflow Family | Expected Report Type | Expected Canonical Behavior |
| --- | --- | --- |
| Unit test | `unit_test_report` | Explicit report bundle with canonical `report.primary` |
| Coverage | `coverage_report` | Explicit report bundle with report-friendly summary and evidence |
| Pentest/security | `security_pentest_report` | Explicit report bundle with canonical final report and supporting evidence |
| Benchmark | `benchmark_report` | Explicit report bundle with benchmark-specific report typing |

## 5. Consumer Contract

- Server-defined behavior resolves canonical report semantics and latest report views.
- Mission Control may surface a canonical report directly when one exists.
- Consumers must not guess canonical report identity from artifact ordering or filename conventions alone.

## 6. Out-Of-Scope Capabilities For MM-497

MM-497 does not require:

- PDF rendering engines
- provider-specific prompt contracts
- full-text indexing of report bodies
- legal review workflows
- a separate report storage subsystem
- mutable in-place report updates
- provider-native payload parsing as the canonical report model

## 7. Deferred Follow-Up Decisions

MM-497 preserves but does not decide:

- bounded `report_type` enum versus conventions-first typing
- auto-pinning policy for final reports
- whether a dedicated report projection endpoint is needed immediately
- export-format distinctions such as PDF versus HTML
- stronger evidence-grouping semantics
- multi-step task-level versus step-level report projection policy
