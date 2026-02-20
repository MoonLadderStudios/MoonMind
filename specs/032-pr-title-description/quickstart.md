# Quickstart: Queue Publish PR Title and Description System

## Preconditions

- Working branch: `032-pr-title-description`
- Python test environment available (`./tools/test_unit.sh`)

## Validate Publish Text Logic

1. Run focused worker tests:

```bash
./tools/test_unit.sh tests/unit/agents/codex_worker/test_worker.py -k "publish or title or body"
```

2. Run full unit suite required by repo policy:

```bash
./tools/test_unit.sh
```

## Manual Contract Spot-Check (Optional)

1. Build a canonical task payload with `publish.mode="pr"` and no title/body overrides.
2. Ensure derived title comes from first non-empty step title; otherwise first instruction line/sentence.
3. Ensure generated PR body contains:
- `<!-- moonmind:begin -->`
- `MoonMind Job: <full-uuid>`
- `Runtime: <...>`
- `Base: <...>`
- `Head: <...>`
- `<!-- moonmind:end -->`
4. Ensure explicit overrides still pass through unchanged.

## Expected Outcome

- Publish stage text generation aligns with `docs/TaskQueueSystem.md` section 6.4.
- Tests demonstrate override precedence, fallback ordering, metadata correlation, and branch semantics.
