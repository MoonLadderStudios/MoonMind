# Contract: Claude Decision Pipeline

## Public Schema Module

`moonmind.schemas.managed_session_models`

## Required Models

- `ClaudeDecisionPoint`
- `ClaudeHookAudit`

## Required Constants

- `CLAUDE_DECISION_STAGE_ORDER`
- `CLAUDE_DECISION_EVENT_NAMES`
- `CLAUDE_HOOK_WORK_EVENT_NAMES`

## Required Type Aliases

- `ClaudeDecisionStage`
- `ClaudeDecisionOutcome`
- `ClaudeDecisionProposalKind`
- `ClaudeDecisionProvenanceSource`
- `ClaudeDecisionEventName`
- `ClaudeHookSourceScope`
- `ClaudeHookOutcome`
- `ClaudeHookWorkEventName`

## Wire Shape

Models use camelCase aliases for serialized fields and reject unknown fields.

Required DecisionPoint fields:
- `decisionId`
- `sessionId`
- `turnId`
- `proposalKind`
- `originStage`
- `outcome`
- `provenanceSource`
- `eventName`
- `createdAt`

Required HookAudit fields:
- `auditId`
- `sessionId`
- `turnId`
- `hookName`
- `sourceScope`
- `eventType`
- `matcher`
- `outcome`
- `createdAt`

## Boundary Helpers

### `ClaudeDecisionPoint.protected_path(...) -> ClaudeDecisionPoint`

Creates a protected-path DecisionPoint.

Requirements:
- Uses `originStage = protected_path_guard`.
- Uses `provenanceSource = protected_path`.
- Uses deny or ask outcome only.
- Rejects automatic allow semantics.

### `ClaudeDecisionPoint.classifier(...) -> ClaudeDecisionPoint`

Creates an auto-mode classifier DecisionPoint.

Requirements:
- Uses `originStage = auto_mode_classifier`.
- Uses `provenanceSource = classifier`.
- Distinguishes classifier outcomes from user approvals and policy outcomes.

### `ClaudeDecisionPoint.headless_resolution(...) -> ClaudeDecisionPoint`

Creates a headless unresolved DecisionPoint.

Requirements:
- Uses `originStage = interactive_prompt_or_headless_resolution`.
- Uses `provenanceSource = headless_policy`.
- Accepts deny or defer only.

### `ClaudeDecisionPoint.hook_tightened(...) -> ClaudeDecisionPoint`

Creates a hook-origin DecisionPoint that tightened restrictions.

Requirements:
- Uses `originStage = pretool_hooks` or `posttool_hooks`.
- Uses `provenanceSource = hook`.
- Records compact metadata explaining that the hook tightened restrictions.

## Validation Surface

Tests must confirm:
- All documented stages and decision event names validate.
- Unknown stages, event names, hook scopes, and hook outcomes fail validation.
- Protected-path and classifier decisions cannot be confused with user approvals or explicit allow-rule outcomes.
- Headless unresolved decisions accept only deny or defer outcomes.
- HookAudit records include source scope, event type, matcher, outcome, and bounded audit data.
- Representative end-to-end decision sequences preserve stage order.
