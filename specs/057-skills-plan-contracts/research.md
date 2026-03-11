# Research: Skills and Plans Runtime Contracts

## Decision 1: Keep contracts as explicit runtime data models

- **Decision**: Implement canonical skill, plan, artifact, and result contracts as dataclasses in `moonmind/workflows/skills/tool_plan_contracts.py`.
- **Rationale**: Supports deterministic orchestration and prevents hidden behavior from ad hoc payload parsing.
- **Alternatives considered**:
  - Dynamic dictionary-only handling: rejected because validation and compatibility become ambiguous.
  - Executable plan scripts: rejected because plans must remain data artifacts, not code.

## Decision 2: Pin plan execution to immutable registry snapshots

- **Decision**: Resolve skill definitions from snapshot digests/artifact refs created by `create_registry_snapshot`.
- **Rationale**: Guarantees reproducibility and enforces DOC-REQ pinned-resolution behavior.
- **Alternatives considered**:
  - Resolve latest registry state at runtime: rejected due to nondeterministic replays.
  - Embed full registry definitions inline in every node: rejected due to payload bloat and duplication.

## Decision 3: Split validation into structural + deep phases

- **Decision**: Perform deterministic structural checks in plan validators and deep authoritative validation through `plan_validate_activity`.
- **Rationale**: Keeps workflow-safe checks small while retaining complete schema/reference validation before execution.
- **Alternatives considered**:
  - Deep validation only inside interpreter loop: rejected because execution could begin on invalid plans.
  - Workflow-only deep validation: rejected due to determinism and maintainability constraints.

## Decision 4: Enforce DAG semantics with explicit failure policy

- **Decision**: Use topological validation and dependency-ready scheduling in `plan_interpreter.py` with `FAIL_FAST` and `CONTINUE`.
- **Rationale**: Matches v1 semantics and keeps execution order deterministic.
- **Alternatives considered**:
  - Conditional edges in v1: rejected by source contract.
  - Implicit continue behavior: rejected because policy must be explicit and testable.

## Decision 5: Keep inter-node data references deterministic and dependency-safe

- **Decision**: Accept only `{ "ref": { "node": "...", "json_pointer": "..." } }` references that point to valid dependency paths and schema-backed output pointers.
- **Rationale**: Prevents nondeterministic reads and catches invalid reference wiring before runtime side effects.
- **Alternatives considered**:
  - String interpolation references: rejected because pointer validation and type safety degrade.
  - Allow references to non-dependency nodes: rejected due to race/order ambiguity.

## Decision 6: Use declared activity bindings for dispatch, never inference

- **Decision**: Route invocations by `ToolDefinition.executor.activity_type` in `tool_dispatcher.py`.
- **Rationale**: Preserves explicit least-guess routing and supports curated specialized activity handlers.
- **Alternatives considered**:
  - Infer handler from skill name prefix: rejected as brittle and non-contractual.
  - Single hardcoded dispatcher for all skills: rejected because curated activity isolation is required.

## Decision 7: Keep payload discipline with artifact references

- **Decision**: Keep inline outputs small and emit large payloads as artifact refs through artifact store helpers.
- **Rationale**: Protects workflow payload/history size while maintaining immutable auditability.
- **Alternatives considered**:
  - Inline large logs/transcripts in result payloads: rejected due to history growth.
  - External mutable blob links: rejected because immutability guarantees weaken.

## Decision 8: Normalize failures with the standard skill error model

- **Decision**: Convert validation/dispatch/runtime failures into `ToolFailure` envelopes with explicit codes.
- **Rationale**: Ensures consistent retry and operator-facing diagnostics.
- **Alternatives considered**:
  - Raw exceptions bubbling to callers: rejected because policies cannot be applied uniformly.
  - Tool-specific opaque error objects: rejected as non-portable.

## Decision 9: Expose structured progress and durable summaries

- **Decision**: Maintain queryable progress in interpreter state and optionally persist progress/summary artifacts.
- **Rationale**: Gives operational visibility without parsing logs and supports completed-run auditing.
- **Alternatives considered**:
  - Log-only progress reporting: rejected due to poor machine-readability.
  - Summary-only final state without intermediate progress: rejected because in-flight observability is required.

## Decision 10: Keep runtime mode as a hard planning gate

- **Decision**: Treat this feature as runtime implementation mode, requiring production code changes plus test validation.
- **Rationale**: Spec explicitly disallows docs-only completion and sets runtime/test outcomes as success criteria.
- **Alternatives considered**:
  - Documentation-only closure: rejected as non-compliant with FR-014 and FR-015.
  - Partial testing without standard unit command: rejected because repository policy requires `./tools/test_unit.sh`.
