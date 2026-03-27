# Research: Jules Provider Adapter Runtime Alignment

## Decision 1: Bundle Jules work in MoonMind orchestration, not in transport

- **Decision**: Perform Jules bundle grouping and brief compilation in `MoonMind.Run` after ordered plan nodes are known, while keeping `JulesClient` and `JulesAgentAdapter` transport-focused.
- **Rationale**: The current contradiction to the doc lives in orchestration (`worker_runtime.py`, `run.py`, `agent_run.py`), not in the provider HTTP layer.
- **Alternatives considered**:
  - Add bundling logic to `JulesAgentAdapter`: rejected because it would mix workflow planning semantics into the provider adapter.
  - Add bundling logic to `JulesClient`: rejected because transport must remain thin and replay-insensitive.

## Decision 2: Remove normal `jules_session_id` chaining for new executions

- **Decision**: Delete the normal execution-path use of `jules_session_id` / sequential `sendMessage` continuation for new Jules work.
- **Rationale**: The updated doc explicitly replaces multi-step progression with one-shot bundled execution and the repo constitution prefers deletion over compatibility aliases for pre-release internal behavior.
- **Alternatives considered**:
  - Keep `jules_session_id` as a hidden fallback: rejected because it would preserve the exact brittle behavior the doc now forbids.
  - Continue using `sendMessage` for some multi-step cases: rejected because the new standard is bundle-first, with `sendMessage` reserved for exception flows only.

## Decision 3: Preserve clarification handling as the only normal follow-up message path

- **Decision**: Keep `integration.jules.send_message` only for clarification, intervention, and explicit resume flows, with auto-answer remaining in `MoonMind.AgentRun`.
- **Rationale**: This already matches the updated doc and avoids moving workflow choreography into the transport layer.
- **Alternatives considered**:
  - Remove `sendMessage` entirely: rejected because clarification/resume still require it.
  - Move clarification logic into Jules activities only: rejected because the workflow must own stateful polling and escalation policy.

## Decision 4: Treat branch publication as a MoonMind success gate

- **Decision**: Make `publishMode == "branch"` succeed only after PR extraction, optional base retarget, merge completion, and any required MoonMind verification.
- **Rationale**: The updated doc defines these steps as part of completion semantics, not best-effort post-processing.
- **Alternatives considered**:
  - Keep merge as best-effort logging only: rejected because it would allow false success reporting.
  - Trust provider completion alone: rejected because provider success does not guarantee the requested branch outcome landed.

## Decision 5: Use artifact-backed bundle manifests instead of inflating workflow payloads

- **Decision**: Persist bundle manifest details in artifact-backed metadata and carry compact identifiers/refs in workflow results.
- **Rationale**: This preserves auditability and bundle explanation without bloating workflow history or violating compact-payload guidance.
- **Alternatives considered**:
  - Store the full compiled brief and checklist inline in workflow state: rejected because it increases history size and replay risk.
  - Omit bundle metadata entirely: rejected because operators need traceability from bundle results back to logical plan nodes.

## Decision 6: Anchor verification in workflow-boundary tests

- **Decision**: Update workflow-level tests first-class, including replay-sensitive control-flow coverage for changed branching behavior.
- **Rationale**: Bundling, branch publication, and clarification exception handling all cross module boundaries and can affect in-flight workflow histories.
- **Alternatives considered**:
  - Rely only on adapter/client unit tests: rejected because they would miss orchestration regressions.
