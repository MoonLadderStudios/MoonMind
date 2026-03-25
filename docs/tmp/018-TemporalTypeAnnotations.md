## Description
Currently, many Temporal workflow activities in MoonMind (such as `artifact.write_complete`) are called by passing loosely-typed dictionaries as payloads. This circumvents Python static type checkers and relies heavily on runtime type inference.

Recently, this caused a critical workflow failure where a `bytes` object within a dictionary payload was serialized by the Temporal Python SDK as a massive JSON list of integers (e.g., `[123, 34, 115...]`). When deserialized by the activity, the resulting `list[int]` was passed to `hashlib.sha256()`, causing a `TypeError: object supporting the buffer API required` and crashing the Temporal worker.

To prevent such serialization anomalies and type mismatch bugs in the future, we need to enforce type safety across Temporal activity boundaries by adopting structured Python data models (like Pydantic models or standard `@dataclass`) for activity inputs.

## Plan
### Phase 1: Establish Model Standards
1. Create a centralized location (e.g., `moonmind/schemas/temporal_activity_models.py` or alongside existing artifact/temporal schemas) to house activity payload definitions.
2. Define standard Pydantic models or standard dataclasses for core artifact activities.
   - Example: `ArtifactWriteCompleteInput(principal: str, artifact_id: str, payload: str | bytes, content_type: Optional[str] = None)`

### Phase 2: Refactor Activity Definitions
1. Update `moonmind/workflows/temporal/artifacts.py` and `moonmind/workflows/temporal/activity_runtime.py` to accept these typed models instead of untyped dictionaries.
2. Ensure backward compatibility or graceful cutover handling for any in-flight workflows that might still be executing with legacy dictionary payloads (e.g., validating and parsing the dictionary dynamically if necessary).

### Phase 3: Refactor Workflow Call Sites
1. Update callers, particularly in `moonmind/workflows/temporal/workflows/run.py` and other orchestrator flows, to instantiate these typed models when invoking activities via `workflow.execute_activity()`.
2. Ensure the instantiation forces static type checkers to evaluate the inputs before execution.

### Phase 4: Expansion & Verification
1. Expand this pattern to other critical temporal activities (`artifact.read`, `plan.generate`, etc.).
2. Run standard unit tests and integration tests to verify successful Temporal serialization and deserialization using the new payload types.
3. Validate that `mypy` or `pyright` successfully catches mismatched types at workflow-to-activity call sites going forward.