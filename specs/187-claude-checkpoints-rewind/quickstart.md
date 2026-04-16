# Quickstart: Claude Checkpoints Rewind

## Focused Red-First Validation

1. Create or update unit tests for checkpoint trigger validation, capture-rule defaults, rewind modes, lineage fields, payload locality, and summary-from-here behavior:

```bash
pytest tests/unit/schemas/test_claude_checkpoints.py -q
```

Expected red result before implementation: imports for `ClaudeCheckpoint`, `ClaudeRewindRequest`, `ClaudeRewindResult`, and checkpoint helper functions fail or behavior assertions fail.

2. Create or update integration-style boundary tests for representative checkpoint capture and rewind flow:

```bash
pytest tests/integration/schemas/test_claude_checkpoints_boundary.py -q
```

Expected red result before implementation: checkpoint and rewind boundary contracts are unavailable or do not emit the expected work evidence.

## Focused Green Validation

Run focused tests after implementation:

```bash
pytest tests/unit/schemas/test_claude_checkpoints.py tests/integration/schemas/test_claude_checkpoints_boundary.py -q
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
- User prompt and tracked file edit triggers create checkpoint metadata.
- Bash side effects do not create code-state checkpoints by default.
- External manual edits are represented as best-effort.
- All four rewind modes validate and unknown modes fail.
- Rewind output preserves event-log references, updates the active cursor, and records `rewound_from_checkpoint_id`.
- Summary-from-here emits a summary artifact reference without claiming code restore.
- Checkpoint payloads remain runtime-local references with compact metadata only.
