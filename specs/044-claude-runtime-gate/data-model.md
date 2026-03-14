# Data Model — Claude Runtime API-Key Gating

## RuntimeGateState
| Field | Type | Source | Notes |
| --- | --- | --- | --- |
| `enabled` | bool | Derived via `is_claude_runtime_enabled(api_key=settings.anthropic.anthropic_api_key)` | True only when either `ANTHROPIC_API_KEY` or `CLAUDE_API_KEY` is non-empty. |
| `source_env` | list[str] | Environment | Optional diagnostics showing which env var satisfied the gate (e.g., `["ANTHROPIC_API_KEY"]`). |
| `error_message` | str | Static string | When `enabled=False` and Claude runtime is requested, surface `"targetRuntime=claude requires ANTHROPIC_API_KEY to be configured"`. |

This state is transient (no persistence) and is computed anywhere runtime gating is needed.

## WorkerPreflightConfig
| Field | Type | Source |
| --- | --- | --- |
| `runtime` | Literal["codex","gemini","claude","universal"] | `MOONMIND_WORKER_RUNTIME` env, default `codex`. |
| `capabilities` | tuple[str,...] | Parsed from `MOONMIND_WORKER_CAPABILITIES` (comma-delimited, lowercase). |
| `claude_required` | bool | `runtime == "claude" or "claude" in capabilities`. |
| `anthropic_api_key` | str | Comes from `RuntimeGateState`.
| `cli_paths` | dict[str,str] | Outputs of `verify_cli_is_executable` for codex/gemini/claude/agentkit as needed. |

This model feeds `run_preflight` to determine which CLIs to validate.

## TaskRuntimeDescriptor
| Field | Type | Source |
| --- | --- | --- |
| `requested_runtime` | str | Client payload `targetRuntime` or `task.runtime.mode`. |
| `resolved_runtime` | str | `_enrich_task_payload_defaults` result (defaults to settings.workflow/default env). |
| `validation_errors` | list[str] | Populated when resolved runtime is `claude` while `RuntimeGateState.enabled` is False; contains the canonical error message. |
| `required_capabilities` | list[str] | Derived from runtime: `claude` implies `claude` capability appended to `requiredCapabilities` array. |

Used by queue normalization to reject invalid tasks before persistence.

## DashboardRuntimeConfig
| Field | Type | Notes |
| --- | --- | --- |
| `supportedTaskRuntimes` | list[str] | Always `['codex','gemini']` plus `claude` when gate enabled. |
| `defaultTaskRuntime` | str | First match among `MOONMIND_WORKER_RUNTIME`, `settings.workflow.default_task_runtime`, fallback `supportedTaskRuntimes[0]`. |
| `defaultTaskModelByRuntime` | dict[str,str] | Currently only codex-specific defaults; unchanged by this feature. |
| `defaultTaskEffortByRuntime` | dict[str,str] | Only codex defaults; unaffected. |
| `claudeEnabled` | bool (derived, not serialized) | Internal convenience flag for the view-model builder. |

This model informs the dashboard JS runtime dropdown and ensures UI + API share the same gate logic.
