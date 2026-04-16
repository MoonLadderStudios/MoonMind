# Quickstart: Claude Governance Telemetry

## Focused Unit Tests

Run the story-specific unit tests:

```bash
pytest tests/unit/schemas/test_claude_governance_telemetry.py -q
```

Expected result: governance telemetry contracts validate supported values, reject unsupported values, preserve payload-light storage evidence, require policy-controlled retention classes, normalize telemetry names, and reject usage rollup double-counting.

## Focused Integration-Style Boundary Test

Run the synthetic schema boundary test:

```bash
pytest tests/integration/schemas/test_claude_governance_telemetry_boundary.py -q
```

Expected result: one deterministic synthetic Claude session flow produces event subscriptions, event envelopes across supported families, storage refs, retention metadata, telemetry evidence, usage rollups, governance evidence, compliance export, and dashboard summary without embedding source code, transcripts, file reads, checkpoint payloads, or local caches in central-plane records.

## Required Unit Suite

Run full unit verification before final MoonSpec verification:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

## Hermetic Integration Suite

Run the required hermetic integration suite when Docker is available:

```bash
./tools/test_integration.sh
```

If Docker is unavailable in the managed workspace, record the exact blocker and preserve focused integration-style schema boundary results.

## Final MoonSpec Verification

Before closing MM-349:

1. Confirm `spec.md` preserves the MM-349 Jira preset brief.
2. Confirm `tasks.md` has all implementation and validation tasks marked complete.
3. Confirm focused unit and integration-style tests pass.
4. Run final MoonSpec verification against `specs/191-claude-governance-telemetry/spec.md`.
