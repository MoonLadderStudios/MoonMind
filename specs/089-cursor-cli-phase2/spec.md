# Spec: Cursor CLI Phase 2 — Adapter Wiring

**Source Document**: [CursorCli.md](file:///Users/nsticco/MoonMind/docs/ManagedAgents/CursorCli.md)
**Phase**: 2 of 5

---

## Document Requirement Identifiers

These identifiers trace directly to specific sections of the source document:

| ID | Source Section | Requirement |
|----|---------------|-------------|
| DOC-REQ-P2-001 | §5 Adapter Code Changes | `cursor_cli` branch in `resolve_volume_mount_env()` sets `CURSOR_CONFIG_DIR` |
| DOC-REQ-P2-002 | §5 Environment Shaping | `CURSOR_API_KEY` added to OAuth scrubbable keys in `shape_agent_environment()` |
| DOC-REQ-P2-003 | §5 Command Construction | `_build_cursor_command()` builds CLI invocation with `-p`, `--output-format stream-json`, `--force` |
| DOC-REQ-P2-004 | §4 Output Parsing | NDJSON stream parser for `stream-json` output format with event types: system, user, assistant, tool_call, result |
| DOC-REQ-P2-005 | §5 Runtime Registration | `cursor_cli` registered as managed runtime capability in worker fleet |

---

## User Stories

### US1: Volume Mount Resolution for Cursor CLI
**As a** MoonMind sandbox worker  
**I want** the Cursor CLI auth volume mapped to `CURSOR_CONFIG_DIR`  
**So that** the `cursor-agent` binary can locate its credentials at runtime

### US2: API Key Scrubbing in OAuth Mode
**As a** MoonMind operator  
**I want** `CURSOR_API_KEY` scrubbed from the environment when auth mode is OAuth  
**So that** the CLI uses volume-based credentials and doesn't fall back to API key auth

### US3: Cursor CLI Command Construction
**As a** managed runtime launcher  
**I want** `AgentExecutionRequest` parameters converted to `cursor-agent` CLI flags  
**So that** headless Cursor CLI runs use the correct model, output format, and execution mode

### US4: NDJSON Output Parser for Cursor CLI
**As a** MoonMind run supervisor  
**I want** Cursor CLI's `stream-json` NDJSON output parsed into structured events  
**So that** assistant responses, tool calls, and completion status are observable in real-time

### US5: Cursor CLI Worker Fleet Registration
**As a** MoonMind platform  
**I want** `cursor_cli` recognized as a managed runtime capability  
**So that** agent runtime workers can accept and dispatch Cursor CLI execution requests

---

## Functional Requirements

### Adapter

| ID | Requirement | DOC-REQ | Testable |
|----|-------------|---------|----------|
| FR-001 | `resolve_volume_mount_env()` maps `cursor_cli` runtime_id to `CURSOR_CONFIG_DIR` env var | DOC-REQ-P2-001 | Unit test |
| FR-002 | `shape_agent_environment()` scrubs `CURSOR_API_KEY` when auth_mode is `oauth` | DOC-REQ-P2-002 | Unit test |
| FR-003 | `shape_agent_environment()` preserves `CURSOR_API_KEY` when auth_mode is `api_key` | DOC-REQ-P2-002 | Unit test |

### Launcher

| ID | Requirement | DOC-REQ | Testable |
|----|-------------|---------|----------|
| FR-004 | `build_command()` for `cursor_cli` starts with `cursor-agent`, `-p`, instruction | DOC-REQ-P2-003 | Unit test |
| FR-005 | `build_command()` for `cursor_cli` includes `--output-format stream-json` and `--force` | DOC-REQ-P2-003 | Unit test |
| FR-006 | `build_command()` for `cursor_cli` appends `--model` when specified | DOC-REQ-P2-003 | Unit test |
| FR-007 | `build_command()` for `cursor_cli` appends `--sandbox` when sandbox_mode specified | DOC-REQ-P2-003 | Unit test |

### Output Parsing

| ID | Requirement | DOC-REQ | Testable |
|----|-------------|---------|----------|
| FR-008 | NDJSON parser yields typed events from newline-delimited JSON output | DOC-REQ-P2-004 | Unit test |
| FR-009 | Parser handles all event types: system, user, assistant, tool_call, result | DOC-REQ-P2-004 | Unit test |
| FR-010 | Parser skips malformed JSON lines gracefully (logs warning, does not raise) | DOC-REQ-P2-004 | Unit test |

### Registration

| ID | Requirement | DOC-REQ | Testable |
|----|-------------|---------|----------|
| FR-011 | `cursor_cli` appears as a recognized capability in worker fleet capabilities | DOC-REQ-P2-005 | Unit test |

---

## Success Criteria

| # | Criterion | Verification |
|---|-----------|-------------|
| SC-1 | `resolve_volume_mount_env(env, "cursor_cli", "/home/app/.cursor")` returns env with `CURSOR_CONFIG_DIR=/home/app/.cursor` | Unit test in `test_adapter.py` |
| SC-2 | `shape_agent_environment(env, "oauth")` returns env with `CURSOR_API_KEY=""` | Unit test in `test_adapter.py` |
| SC-3 | `build_command()` for a cursor_cli profile produces `["cursor-agent", "-p", ..., "--output-format", "stream-json", "--force"]` | Unit test in `test_launcher.py` |
| SC-4 | NDJSON parser correctly yields all event types from a multiline input stream | Unit test in `test_ndjson_parser.py` |
| SC-5 | Worker fleet includes `cursor_cli` in agent_runtime capabilities | Unit test in `test_activity_catalog.py` |
| SC-6 | All existing unit tests continue to pass (`./tools/test_unit.sh`) | CLI verification |
