# Research: Harden Session Workflow

## Decision 1: Serialize async mutators with a workflow-local lock

**Decision**: Use one workflow-local async lock around async handlers that mutate shared managed-session state.

**Rationale**: The session workflow accepts multiple update handlers that can touch the same bounded state: runtime locator, active turn, status, control metadata, continuity refs, and termination flags. A single lock gives deterministic state ordering without changing the public control vocabulary.

**Alternatives considered**:

- Per-field locks: rejected because mutators update related fields as one logical session snapshot.
- Reject concurrent updates: rejected because operators and parent workflows can legitimately send controls near each other.
- Rely on handler scheduling order alone: rejected because async handlers can interleave around awaited activity calls.

## Decision 2: Gate accepted runtime-bound mutators on readiness

**Decision**: Accept valid runtime-bound updates before handles are attached, then wait until container and thread handles exist before constructing a locator.

**Rationale**: Early controls can arrive while launch and handle attachment are racing. Waiting at the workflow boundary preserves the accepted request and avoids nondeterministic startup failures while still letting validators reject truly invalid state such as termination or stale epoch.

**Alternatives considered**:

- Keep rejecting missing handles in validators: rejected because this makes startup timing visible as an operator/client failure.
- Add retry logic in callers: rejected because readiness is workflow-owned state and should be handled where the state is authoritative.
- Wait in activities: rejected because activities should receive complete locators and not own workflow readiness.

## Decision 3: Drain accepted handlers before completion or handoff

**Decision**: Before returning a terminal workflow result or continuing as new, wait for all accepted asynchronous handlers to finish.

**Rationale**: Accepted updates must remain retrievable by clients. Completion or handoff while handlers are still running can strand update results and create confusing operator outcomes.

**Alternatives considered**:

- Complete immediately after termination is requested: rejected because in-flight updates may still be resolving.
- Let clients retry failed update result retrievals: rejected because it shifts workflow lifecycle correctness to clients.

## Decision 4: Continue-As-New only from the main workflow loop

**Decision**: Evaluate history-length and server-suggested handoff from `run()`, then continue as new from the main workflow path.

**Rationale**: Main-loop handoff avoids initiating durable workflow shape changes from message handlers and makes handler drain explicit before the new run starts.

**Alternatives considered**:

- Continue-As-New directly from updates: rejected because it can interrupt the handler that accepted the update.
- External sweeper-triggered handoff: rejected for this phase because the workflow already owns the bounded state needed for handoff.

## Decision 5: Carry bounded session state only

**Decision**: Carry forward session binding, epoch, runtime locator, active turn, last control metadata, latest continuity refs, shortened-history threshold, and compact identified-control request-tracking state when available.

**Rationale**: This is enough for future controls and operator/recovery views while respecting the durable-state rule that prompts, transcripts, and large runtime-local data do not belong in workflow history.

**Alternatives considered**:

- Carry full session summaries or transcripts: rejected because large content belongs in artifacts and runtime storage, not workflow payloads.
- Carry only session ID and epoch: rejected because controls after handoff require the current locator and continuity refs.
- Invent content-based dedupe: rejected because request tracking should use stable caller/workflow identity when available, not prompt or transcript content.

## Decision 6: Use a shortened-history test hook

**Decision**: Add an explicit workflow input field that allows tests to force Continue-As-New at a low history threshold.

**Rationale**: Production-scale history thresholds are too expensive for unit tests. A test hook validates handoff behavior deterministically without changing production defaults.

**Alternatives considered**:

- Wait for real production thresholds in tests: rejected as slow and brittle.
- Mock only the handoff payload builder: rejected because it does not prove the main workflow loop drains handlers and requests handoff.
