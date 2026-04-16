# Research: Claude Decision Pipeline

## Decision: Use the existing managed-session schema module

Claude session-plane records already live in `moonmind/schemas/managed_session_models.py`. DecisionPoint and HookAudit records attach to `session_id`, `turn_id`, and optional work-item identifiers, so keeping them in the same schema module preserves a single runtime boundary for Claude managed-session contracts.

Alternatives considered:
- Create a new Claude-specific schema module. Rejected because the current public import surface already exposes Claude managed-session models from `managed_session_models.py` and `moonmind.schemas`.
- Add decision fields directly to `ClaudeManagedWorkItem`. Rejected because DecisionPoint and HookAudit are independently inspectable records with different validation rules.

## Decision: Validate stage and event vocabularies with strict literals

The design requires documented decision stages and event names to be normalized. Strict literal values provide fail-fast validation for unsupported stages, events, hook scopes, and outcomes, matching the pre-release compatibility policy.

Alternatives considered:
- Accept arbitrary strings and normalize later. Rejected because hidden compatibility transforms could obscure safety and billing-relevant behavior.

## Decision: Keep payloads bounded through existing compact metadata validation

Decision metadata and hook audit data must not carry large tool payloads or transport envelopes. Reusing `validate_compact_temporal_mapping` keeps behavior aligned with existing bounded schema metadata.

Alternatives considered:
- Store raw tool proposals in DecisionPoint. Rejected because large content belongs behind refs or artifacts, not compact workflow-facing metadata.

## Decision: Implement helper constructors for sensitive outcomes

Protected path, classifier, headless, and hook-tightening outcomes have invariants beyond literal validation. Helper constructors make representative boundary use deterministic and keep validation close to the schema model.

Alternatives considered:
- Require callers to hand-build all fields. Rejected because it leaves critical provenance distinctions easy to omit.
