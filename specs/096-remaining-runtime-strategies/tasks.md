# Tasks: Remaining Runtime Strategies — Phase 2

## Phase 1: Strategy Implementations

- [ ] T001 Implement `CursorCliStrategy` in `strategies/cursor_cli.py` — build_command (model, -p prompt, --output-format stream-json, --force, --sandbox), default_auth_mode="oauth", shape_environment (no-op, uses OAuth-shaped env)
- [ ] T002 [P] Implement `ClaudeCodeStrategy` in `strategies/claude_code.py` — build_command (model, effort, --prompt), default_command_template=["claude"]
- [ ] T003 [P] Implement `CodexCliStrategy` in `strategies/codex_cli.py` — build_command (-m model, positional prompt), default_command_template=["codex","exec","--full-auto"], shape_environment (CODEX_HOME, CODEX_CONFIG_HOME, CODEX_CONFIG_PATH)

## Phase 2: Registration + Branching Removal

- [ ] T004 Register all three strategies in `strategies/__init__.py`
- [ ] T005 Remove if/elif block from `launcher.py:build_command()` — strategy handles everything
- [ ] T006 Remove command_template defaults from `adapter.py` (elif block L296-303)
- [ ] T007 Remove auth_mode hardcoded fallback from `adapter.py` (L242-245)
- [ ] T008 Remove `_runtime_env_keys` hardcoded list from `launcher.py` (L434-441) — each strategy handles its own env

## Phase 3: Tests

- [ ] T009 [P] Unit tests for CursorCliStrategy (build_command, properties)
- [ ] T010 [P] Unit tests for ClaudeCodeStrategy (build_command, properties)
- [ ] T011 [P] Unit tests for CodexCliStrategy (build_command, properties, shape_environment)
- [ ] T012 Run ./tools/test_unit.sh — verify all pass

## Phase 4: Polish

- [ ] T013 Verify no changes to supervisor.py
- [ ] T014 Verify launcher.build_command has no runtime-specific branching
