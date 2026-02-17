# Data Model: Dashboard Queue Task Default Pre-Population

## RunDefaultsSettings

Settings-backed defaults consumed by queue API and dashboard runtime config.

| Field | Type | Description |
|------|------|-------------|
| `defaultTaskRuntime` | enum (`codex` \| `gemini` \| `claude`) | Runtime used when submission omits runtime mode. |
| `defaultTaskModel` | string | Runtime model default used when model override is omitted. |
| `defaultTaskEffort` | string | Runtime effort default used when effort override is omitted. |
| `defaultRepository` | string (`owner/repo`) | Repository used when submission omits repository. |

## QueueSubmitFormState

Queue submit UI state with pre-populated editable fields.

| Field | Type | Description |
|------|------|-------------|
| `runtime` | string | Selected runtime mode; initialized from `defaultTaskRuntime`. |
| `model` | string | Editable model field; initialized from `defaultTaskModel`. |
| `effort` | string | Editable effort field; initialized from `defaultTaskEffort`. |
| `repository` | string | Editable repo field; initialized from `defaultRepository`. |

## CanonicalTaskPayloadResolution

Default resolution behavior for `type=task` submissions before validation.

| Rule ID | Input Condition | Resolution |
|--------|------------------|------------|
| `DR-001` | `payload.repository` blank/missing | Set to settings `defaultRepository`. |
| `DR-002` | runtime mode missing | Set runtime mode to settings/default runtime (`codex`). |
| `DR-003` | runtime mode is `codex` and model missing | Set to settings `defaultTaskModel`. |
| `DR-004` | runtime mode is `codex` and effort missing | Set to settings `defaultTaskEffort`. |
| `DR-005` | User provides explicit field value | Preserve explicit value, no override. |

## Validation Invariants

1. Resolved repository must be non-empty and format-compatible with existing validation expectations.
2. Resolved runtime mode must remain in supported task runtime set.
3. Model/effort defaults apply only when omitted, never when explicitly supplied.
4. Dashboard pre-populated values must match runtime config values from backend settings.
