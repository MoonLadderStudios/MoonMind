1. **Factor shared env-shaping helpers into `moonmind/auth/env_shaping.py`**:
   - Create `moonmind/auth/env_shaping.py`.
   - Move `_OAUTH_CLEARED_VARS`, `_BASE_ENV_FILTER_FRAGMENTS`, `_should_filter_base_env_var`, `_shape_environment_for_oauth`, and `_shape_environment_for_api_key` from `moonmind/workflows/adapters/managed_agent_adapter.py` to `moonmind/auth/env_shaping.py`.
   - Update imports in `moonmind/workflows/adapters/managed_agent_adapter.py` and `tests/unit/workflows/adapters/test_managed_agent_adapter.py` to import these from `moonmind.auth.env_shaping`.

2. **Clean up `agent_runtime_env_keys` in `settings.py`**:
   - Remove `agent_runtime_env_keys` field and related validation from `moonmind/config/settings.py`. It is a dead config.

3. **Implement `GeminiCliStrategy.prepare_workspace()`**:
   - In `moonmind/workflows/temporal/runtime/strategies/gemini_cli.py`, add `prepare_workspace` method.
   - It should write the instruction to a `.gemini/instruction.txt` or similar as specified in the issue. The exact file or env config is "write `.gemini/` instruction files or `GEMINI_INSTRUCTIONS`". I'll use `workspace_path / ".gemini" / "instruction.txt"`. Or use `.gemini/instruction.md`. I will create the directory if it doesn't exist and write `request.instruction_ref` to it if available, or just standard task context. Wait, `GEMINI_INSTRUCTIONS` might be an env var. Let's look at `GeminiCliStrategy`.
   - `build_command` already passes `--prompt request.instruction_ref`. Let's create `GEMINI_INSTRUCTIONS` or `.gemini/instructions.txt` if that's what's asked.
   - I will add a `prepare_workspace` method to `GeminiCliStrategy` that writes `.gemini/instruction.md` with the task context (e.g. `request.instruction_ref`).

4. **Implement `ClaudeCodeStrategy.prepare_workspace()`**:
   - In `moonmind/workflows/temporal/runtime/strategies/claude_code.py`, add `prepare_workspace` method.
   - Write `CLAUDE.md` to `workspace_path` with the task context (e.g. `request.instruction_ref`).

5. **Update Markdown documents**:
   - In `docs/tmp/SharedManagedAgentAbstractions.md`, check the checkboxes for Phase 5.
   - Move `docs/tmp/SharedManagedAgentAbstractions.md` to `docs/ManagedAgents/SharedManagedAgentAbstractions.md`.
   - Update `docs/Temporal/ManagedAndExternalAgentExecutionModel.md` to reference the managed runtime strategy pattern.

6. **Complete pre-commit steps to ensure proper testing, verification, review, and reflection are done**.

7. **Submit the PR**:
   - Use `submit` to commit and push the changes, then use `gh pr create` with `run_in_bash_session`. Oh wait, I should do `gh pr create` before `submit` because `submit` finalizes the task. But I don't have github auth probably. Let me check the instruction: "After completing the changes above, create a GitHub pull request with the changes using `gh pr create`." So I will run `gh pr create` before `submit`.
