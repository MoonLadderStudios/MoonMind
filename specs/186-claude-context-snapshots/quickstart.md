# Quickstart: Claude Context Snapshots

## Focused Red-First Validation

1. Create or update unit tests for context source kind validation, reinjection policies, guidance classification, compact metadata, and compaction immutability:

```bash
pytest tests/unit/schemas/test_claude_context_snapshots.py -q
```

Expected red result before implementation: imports for `ClaudeContextSnapshot`, `ClaudeContextSegment`, `ClaudeContextEvent`, `claude_default_reinjection_policy`, and `compact_claude_context_snapshot` fail or behavior assertions fail.

2. Create or update integration-style boundary tests for representative startup, on-demand, and compaction flow:

```bash
pytest tests/integration/schemas/test_claude_context_snapshots_boundary.py -q
```

Expected red result before implementation: context snapshot and compaction boundary contracts are unavailable or do not emit the expected work item/events.

## Focused Green Validation

Run focused tests after implementation:

```bash
pytest tests/unit/schemas/test_claude_context_snapshots.py tests/integration/schemas/test_claude_context_snapshots_boundary.py -q
```

## Required Final Verification

Run the full required unit suite:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

Run hermetic integration tests when Docker is available:

```bash
./tools/test_integration.sh
```

If Docker is unavailable in the managed-agent container, record the exact blocker and retain the focused integration-style pytest result.

## Story Validation

The story is complete when:
- Startup context source kinds and on-demand context source kinds are all covered by tests.
- Every segment carries an explicit reinjection policy.
- Unknown values fail validation.
- Guidance and memory segments cannot become enforcement sources.
- Compaction creates a new epoch and leaves the prior snapshot immutable.
- Compaction emits a compaction work item plus normalized events.
- Large payloads are rejected from central metadata.
