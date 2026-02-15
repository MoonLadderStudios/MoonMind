# Runtime Contract: Skills Stage Execution

## Stage Input Contract

Every stage invocation must provide:

- `run_id` (UUID)
- `feature_id` (string)
- `stage` (`specify|plan|tasks|analyze|implement`)
- `requested_skill_id` (optional string)
- `payload` (stage-specific object)
- `metadata` (optional object; includes queue/run context)

## Skill Resolution Contract

1. If `requested_skill_id` is present and allowlisted for the stage, use it.
2. Otherwise use configured default stage skill.
3. If no valid skill can be resolved, use direct path only when explicitly configured.

## Output Contract

Stage execution returns:

- `run_id`
- `stage`
- `selected_skill_id`
- `execution_path` (`skill|direct_fallback|direct_only`)
- `status` (`succeeded|failed`)
- `duration_ms`
- `artifacts` (list of artifact descriptors)
- `error` (optional)

## Fallback Contract

- Fallback is attempted only when enabled by policy.
- Fallback attempts must record:
  - triggering skill id,
  - reason for fallback,
  - fallback path status.
- If fallback succeeds, final status is success with `execution_path=direct_fallback`.

## Observability Contract

Each stage attempt emits structured fields:

- `run_id`
- `feature_id`
- `stage`
- `queue`
- `selected_skill_id`
- `execution_path`
- `status`
- `duration_ms`
