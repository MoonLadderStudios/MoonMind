# Contract: Skill Snapshot Resolution

## Scope

This contract defines observable runtime behavior for MM-406. It covers effective task/step skill selector construction, pre-launch snapshot resolution, artifact-backed payload discipline, and runtime launch ref propagation.

## Inputs

### Task Skill Selector

Optional task-level object:

```json
{
  "sets": ["default"],
  "include": [{"name": "pr-resolver", "version": "1.0.0"}],
  "exclude": ["legacy"],
  "materializationMode": "hybrid"
}
```

### Step Skill Selector

Optional step-level object with the same shape. Step-level values refine the inherited task-level selector for one step.

## Effective Selector Rules

1. The effective selector MUST start from task-level skill intent.
2. Step-level `sets` and `include` MUST be additive.
3. Step-level `exclude` MUST remove matching inherited and step-local skills.
4. Step-level `materializationMode` MUST override task-level `materializationMode` for that step only.
5. The original task selector MUST remain unchanged after effective selector construction.

## Resolution Rules

1. MoonMind MUST call the trusted agent-skill resolution activity or service before runtime launch when an effective selector is non-empty.
2. Source loading, source policy filtering, manifest generation, and materialization MUST happen outside deterministic workflow code.
3. Source policy MUST be applied before precedence.
4. Missing required skills, unsatisfied pins, nondeterministic same-source collisions, policy blocks, and runtime incompatibility MUST fail before runtime launch.
5. Successful resolution MUST return a compact `ResolvedSkillSet` ref and metadata for the downstream runtime request.

## Artifact Discipline

1. Large skill bodies, resolved manifests, materialization bundles, and source traces MUST be artifact-backed.
2. Workflow payloads MUST carry compact refs and metadata only.
3. The resolved snapshot manifest ref MUST be sufficient for audit and rerun reasoning without embedding full skill bodies in workflow history.

## Runtime Launch Rules

1. `MoonMind.AgentRun` requests MUST receive `resolvedSkillsetRef` when a pre-launch skill snapshot was resolved.
2. Runtime adapters MUST consume the immutable ref and MUST NOT independently re-resolve skill sources.
3. Retries, continue-as-new, and ordinary reruns MUST reuse the same resolved snapshot unless explicit re-resolution is requested.

## Failure Output

Resolution failure before launch MUST include:
- the failing selector context,
- the validation reason,
- whether the failure was caused by missing skill, pin mismatch, policy, collision, or runtime incompatibility,
- no raw skill body content or secret material.
