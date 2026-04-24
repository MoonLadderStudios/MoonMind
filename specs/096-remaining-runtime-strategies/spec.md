# Spec: Remaining Runtime Strategies — Phase 2

**Feature ID**: 096-remaining-runtime-strategies
**Source**: `spec.md (Input)` Phase 2
**Branch**: `096-remaining-runtime-strategies`

## Overview

Implement CursorCliStrategy, ClaudeCodeStrategy, and CodexCliStrategy, register them in RUNTIME_STRATEGIES, then remove all if/elif branching from launcher.build_command() and adapter command_template/auth_mode defaults.

## Functional Requirements

- FR-001: CursorCliStrategy with build_command, default_auth_mode="oauth", default_command_template=["cursor"]
- FR-002: ClaudeCodeStrategy with build_command, default_command_template=["claude"]
- FR-003: CodexCliStrategy with build_command, default_command_template=["codex","exec","--full-auto"]
- FR-004: Register all three strategies in RUNTIME_STRATEGIES
- FR-005: Remove if/elif branching from launcher.build_command()
- FR-006: Remove command_template defaults from adapter (L296-303)
- FR-007: Remove auth_mode defaults from adapter (L242-245 fallback)
- FR-008: Remove _runtime_env_keys hardcoded list from launcher (L434-441)
- FR-009: No regression — all existing tests pass
- FR-010: No supervisor changes

## Success Criteria

1. All 4 runtimes (gemini_cli, cursor_cli, claude_code, codex_cli) are strategy-driven
2. No if/elif runtime branching remains in launcher.build_command()
3. Adapter uses only strategy registry for defaults
4. All unit tests pass
