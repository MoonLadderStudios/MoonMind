# Codex Worker Unification (Phase 3)

## Overview

This feature completes Phase 3 of the `SharedManagedAgentAbstractions.md` migration by unifying the standalone `codex_worker` execution pipeline into the managed runtime adapter using the "Absorb" strategy.

Currently, the `codex_worker` standalone daemon handles:
1. RAG Context Injection
2. Publishing workflows (commit, push, PR creation)
3. Output sanitization

To eliminate the parallel execution paths and adopt the Strategy pattern introduced in Phase 1 & 2, we will migrate these capabilities directly into the `CodexCliStrategy` (and shared services) so that Codex runs can be executed purely via the Managed Agent Launcher.

## Architecture & Approach

**Absorb Incrementally:**
Instead of wrapping the entire `codex_worker` pipeline inside a shim, we will absorb its key capabilities into the managed layer:

1. **RAG Context Injection Service:**
   Migrate `_resolve_prompt_context` and related RAG building blocks from `codex_worker/handlers.py` into a shared `moonmind.rag.context_injection.ContextInjectionService`.
   `CodexCliStrategy.prepare_workspace()` will invoke this service. Since `AgentExecutionRequest` is mutable, `prepare_workspace` will inject the retrieved context directly into `request.instruction_ref` before the synchronous `build_command()` is called.

2. **CodexCliStrategy Implementation:**
   - Override `prepare_workspace` to execute the RAG context injection.
   - Override `build_command` to use the modified `instruction_ref`.

## Technical Details

**`moonmind/rag/context_injection.py`**
- Create `ContextInjectionService` exposing an `inject_context` method.
- Port `_retrieve_context_pack`, `_persist_context_pack`, `_compose_instruction_with_context`, and `_repository_filter_value` logic from `CodexExecHandler`.

**`moonmind/workflows/temporal/runtime/strategies/codex_cli.py`**
- Add `prepare_workspace(self, workspace_path: Path, request: AgentExecutionRequest)` to run context injection and update `request.instruction_ref`.

*Note on Publishing:* Publishing workflows will be handled separately by Temporal in future phases or as a post-run activity outside the strict subprocess wrapper, as the launcher's responsibility is purely standard execution. For this phase, we ensure the core CLI execution and RAG context work natively.
