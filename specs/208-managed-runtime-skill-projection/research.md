# Research: Managed Runtime Skill Projection

## FR-001 / FR-002 / DESIGN-REQ-005 / DESIGN-REQ-012

Decision: Align the service materializer with the shared run workspace projection model so `.agents/skills` is the canonical visible path and the backing store remains MoonMind-owned.
Evidence: `moonmind/workflows/skills/workspace_links.py` already creates and validates `.agents/skills -> skills_active`; `moonmind/services/skill_materialization.py` currently writes `.agents/skills_active` and explicitly avoids `.agents/skills`.
Rationale: There should be one canonical managed-runtime visible path. Reusing the shared link helper avoids introducing a second projection rule.
Alternatives considered: Keep service output at `.agents/skills_active`; rejected because MM-407 and canonical docs require `.agents/skills`.
Test implications: Unit tests for visible path projection and activity-boundary coverage.

## FR-003 / FR-008 / DESIGN-REQ-013

Decision: Treat existing non-symlink `.agents/skills` paths as unprojectable and fail before runtime launch with path, object kind, attempted action, and remediation guidance.
Evidence: `workspace_links.py` already rejects existing non-symlink paths, but the service materializer does not use it.
Rationale: A checked-in `.agents/skills` directory is a source input, not mutable runtime state. The service must not merge or overwrite it.
Alternatives considered: Rename or move the checked-in directory automatically; rejected because it mutates user-authored repo content.
Test implications: Unit test for incompatible path failure and source directory preservation.

## FR-004 / FR-006 / FR-011 / DESIGN-REQ-016 / DESIGN-REQ-021

Decision: Materialize from the supplied `ResolvedSkillSet` only, using selected entries to populate the backing store and manifest.
Evidence: `AgentSkillMaterializer.materialize` already accepts `ResolvedSkillSet` directly and does not call the resolver.
Rationale: Runtime projection is downstream of snapshot resolution and must preserve retry/rerun semantics.
Alternatives considered: Let materializer reload or re-resolve sources; rejected because it breaks immutable snapshot semantics.
Test implications: Multi-skill selected-only projection test and activity-boundary test using a supplied snapshot.

## FR-005 / DESIGN-REQ-012

Decision: Write `_manifest.json` into the runtime-visible active tree with snapshot identity, runtime id, materialization mode, visible path, backing path, and per-skill name/version/source fields.
Evidence: Service currently writes `active_manifest.json` with only snapshot, resolved time, and skills.
Rationale: The active manifest must be owned by MoonMind and useful to agents, operators, and tests at the visible path.
Alternatives considered: Keep `active_manifest.json`; rejected because Jira acceptance criteria name `_manifest.json`.
Test implications: Unit assertion on exact manifest location and required fields.

## FR-007 / DESIGN-REQ-014

Decision: Preserve existing compatibility-link behavior where `.gemini/skills` may mirror the same active backing store, while `.agents/skills` remains canonical.
Evidence: `workspace_links.py` and `tests/unit/workflows/test_workspace_links.py` already verify both links resolve to the same backing store.
Rationale: No additional compatibility surface is required for this story.
Alternatives considered: Remove `.gemini/skills`; rejected as unrelated and already covered.
Test implications: Existing regression coverage is sufficient.

## FR-009 / FR-010 / DESIGN-REQ-011 / DESIGN-REQ-015

Decision: Keep instruction payload compact and file-oriented: mention active skill names and paths, but do not inline full skill bodies.
Evidence: `moonmind/agents/codex_worker/worker.py` already emits compact workspace guidance for selected skills and prompt-index activity emits metadata rather than bodies.
Rationale: Agents need the stable path and first-read hints; large skill bodies belong on disk/artifacts.
Alternatives considered: Prompt-bundle full `SKILL.md` bodies for managed runtimes; rejected because hybrid workspace materialization is the desired default.
Test implications: Existing instruction tests plus focused prompt-index/no-body test if needed.

## FR-012

Decision: Preserve MM-407 in all MoonSpec artifacts and final verification output.
Evidence: `spec.md` includes the full MM-407 canonical input and traceability.
Rationale: Downstream PR and verification automation needs Jira traceability.
Alternatives considered: Reference only the summary; rejected because the original request must remain available to `/speckit.verify`.
Test implications: Final verification traceability check.
