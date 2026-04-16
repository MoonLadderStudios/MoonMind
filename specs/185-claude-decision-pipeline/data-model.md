# Data Model: Claude Decision Pipeline

## ClaudeDecisionPoint

Represents one material stage in the Claude decision pipeline.

Fields:
- `decision_id`: stable decision identifier.
- `session_id`: canonical Claude managed-session identifier.
- `turn_id`: parent turn identifier.
- `work_item_id`: optional related work-item identifier.
- `proposal_kind`: tool, file, network, mcp, hook, classifier, prompt, runtime, or checkpoint.
- `origin_stage`: one documented decision pipeline stage.
- `outcome`: proposed, mutated, allowed, asked, denied, deferred, canceled, resolved, failed, or executed.
- `provenance_source`: session_state, policy, hook, protected_path, permission_mode, sandbox, classifier, user, headless_policy, runtime, or checkpoint.
- `event_name`: one documented `decision.*` event name.
- `metadata`: bounded compact metadata.
- `created_at`: record timestamp.

Validation rules:
- Identifiers must be nonblank after trimming.
- Unknown fields are rejected.
- `origin_stage` accepts only the documented stage vocabulary.
- `event_name` accepts only documented decision event names.
- Protected-path records must use `origin_stage = protected_path_guard`, `provenance_source = protected_path`, and cannot use an automatic allow outcome.
- Classifier records must use `provenance_source = classifier` and cannot use user or policy provenance.
- Headless unresolved records must use deny or defer outcomes.
- Metadata remains compact and bounded.

## ClaudeHookAudit

Represents one Claude hook execution audit record.

Fields:
- `audit_id`: stable hook audit identifier.
- `session_id`: canonical Claude managed-session identifier.
- `turn_id`: parent turn identifier.
- `decision_id`: optional related DecisionPoint identifier.
- `hook_name`: hook name.
- `source_scope`: managed, user, project, plugin, or sdk.
- `event_type`: event type emitted by the hook system.
- `matcher`: hook matcher expression or name.
- `outcome`: allow, deny, ask, mutate, error, noop, or defer.
- `audit_data`: bounded compact metadata.
- `created_at`: record timestamp.

Validation rules:
- Identifiers and hook fields must be nonblank after trimming.
- Unknown fields are rejected.
- Source scope and outcome accept only documented values.
- Audit data remains compact and bounded.

## Event Vocabularies

Decision events:
- `decision.proposed`
- `decision.mutated`
- `decision.allowed`
- `decision.asked`
- `decision.denied`
- `decision.deferred`
- `decision.canceled`
- `decision.resolved`

Hook work events:
- `work.hook.started`
- `work.hook.completed`
- `work.hook.blocked`

## Stage Order

The canonical stage order is:

1. `session_state_guard`
2. `pretool_hooks`
3. `permission_rules`
4. `protected_path_guard`
5. `permission_mode_baseline`
6. `sandbox_substitution`
7. `auto_mode_classifier`
8. `interactive_prompt_or_headless_resolution`
9. `runtime_execution`
10. `posttool_hooks`
11. `checkpoint_capture`
