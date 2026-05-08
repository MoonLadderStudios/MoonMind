# Data Model: Resolve On-Demand Skill Requests

## Entity: SkillsOnDemandRequest

Represents a managed-runtime request to activate additional Skills.

Fields:
- `current_snapshot_ref`: required compact ref for the active snapshot in enabled request mode.
- `requested_skills`: non-empty list of requested Skill selectors.
- `requested_skills[].name`: non-blank Skill name.
- `requested_skills[].version`: optional non-blank version pin.
- `reason`: optional non-blank request reason.
- `runtime_id`: optional non-blank runtime identifier.
- `step_id`: optional non-blank step identifier.
- `active_snapshot`: compact active snapshot context supplied at the trusted boundary.

Validation:
- Enabled request mode requires `active_snapshot` and matching `current_snapshot_ref`.
- Empty requested lists, blank names, blank versions, and blank optional context values are denied before resolution.
- Duplicate requested names are de-duplicated deterministically.

## Entity: SkillsOnDemandRequestResult

Represents the compact request outcome returned to a managed runtime.

Fields:
- `status`: `activated`, `denied`, or `no_change`.
- `code`: optional structured code.
- `message`: compact human-readable outcome.
- `active_snapshot_id`: current active snapshot id before request handling.
- `parent_snapshot_ref`: parent snapshot ref for unchanged or derived results.
- `snapshot_id`: derived snapshot id for activated requests.
- `resolved_skillset_ref`: compact manifest/ref for the derived resolved Skill set.
- `activation_summary`: compact instructions for next-turn or controlled refresh activation.
- `materialization`: optional compact materialization metadata.
- `metadata`: safe lineage and diagnostic metadata.

Rules:
- Results must not include Skill body text, hidden content refs for arbitrary reads, secrets, or unchecked source paths.
- Denied and failed outcomes must preserve `active_snapshot_id` and must not report a derived `snapshot_id`.

## Entity: Derived Skill Snapshot

Represents an immutable active Skill set produced from an approved request.

Fields:
- `snapshot_id`
- `manifest_ref`
- `skills`
- `source_trace.skillsOnDemandLineage`
- `resolution_inputs`
- `policy_summary`

Rules:
- Contains all previously active Skills plus approved inactive additions.
- Records compact lineage: parent snapshot, requester, runtime, step, reason, requested Skills, and created-by marker.
- Does not mutate the parent snapshot.

## Entity: Materialization Summary

Represents runtime-facing activation metadata.

Fields:
- `mode`
- `visible_path`
- `manifest_ref`

Rules:
- Present for activated requests when materialization is available.
- Must describe a fully materialized snapshot, not a partially written projection.
