# Tasks for Codex Worker Unification

- [ ] Create `moonmind/rag/context_injection.py`
  - [ ] Port `ContextInjectionService` with `inject_context` that accepts `workspace_path` and `request`.
  - [ ] Port context retrieval logic to load `RagRuntimeSettings`, retrieve the `ContextPack`, and persist it to `workspace_path/artifacts/context/`.
  - [ ] Port logic that modifies the instruction with the "SYSTEM SAFETY NOTICE".
- [ ] Update `CodexCliStrategy`
  - [ ] Override `prepare_workspace(self, workspace_path, request)` to instantiate `ContextInjectionService` and update `request.instruction_ref`.
- [x] Write Unit Tests
  - [x] Add tests for `ContextInjectionService` simulating context retrieval and instruction modification.
  - [x] Add tests for `CodexCliStrategy.prepare_workspace` ensuring the request is mutated when context is retrieved.
- [x] Run full test suite (`./tools/test_unit.sh`)
