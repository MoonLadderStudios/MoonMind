# Research: MM-1103 Branch Compare and Promotion

## Inputs Reviewed

- `spec.md`
- `docs/Workflows/CheckpointBranchSystem.md`
- `moonmind/schemas/checkpoint_branch_models.py`
- `api_service/api/routers/executions.py`
- `tests/unit/api/routers/test_checkpoint_branch_apis.py`

## Decisions

### Use Existing Checkpoint Branch API Surface

Decision: Extend or verify the existing checkpoint branch endpoints instead of creating a new service surface.

Rationale: The repository already exposes checkpoint branch create, fork, compare, promote, archive, and publish behavior through `api_service/api/routers/executions.py`. Reusing this surface keeps MM-1103 scoped to comparison and promotion without inventing a parallel branch authority.

### Store Compare and Promote Evidence in Operation Records

Decision: Use `WorkflowCheckpointBranchOperation.response_payload` as the durable operation ledger for compare and promote records, with separate branch artifact ref rows for discoverability.

Rationale: The design requires durable evidence but not necessarily a new table. Operation records already capture operation type, idempotency key, request digest, response payload, branch id, and branch turn id.

### Keep Large Evidence Behind Artifact Refs

Decision: API responses include bounded summaries and artifact refs for diff, metadata, diagnostics, promotion record, and downstream invalidation evidence.

Rationale: The source design explicitly requires branch comparison not to inline large or sensitive evidence. Artifact refs are identifiers and must not be treated as direct storage access grants.

### Treat Promotion as Separate from Publication

Decision: Promotion records git and PR evidence but does not push branches, create PRs, or infer promotion from `publishStatus`.

Rationale: `docs/Workflows/CheckpointBranchSystem.md` distinguishes publication from promotion. A branch can be published without being canonical, and promotion can occur without publication.

### Fail Closed on Unsafe Promotion Inputs

Decision: Promotion rejects missing or stale head evidence, failing gates, unsafe side effects, required-but-missing approval, and mismatched accepted output refs with explicit error codes.

Rationale: The acceptance criteria require fail-closed behavior. This also follows the checkpoint branch safety rules for fresh branch-head validation and side-effect control.

## Open Questions for Implementation Review

- Should synthesized comparison artifact refs be backed by physical artifact bodies in this story, or is operation-ledger evidence sufficient for the current API behavior?
- Should `budget_exhausted` be represented as a first-class promotion request field, a gate verdict, or policy evidence status?
- Should checkpoint validity be rechecked through a dedicated checkpoint service immediately before promotion, beyond matching the persisted current branch head?

