# Quickstart: Agent Skill Catalog and Source Policy

## Focused Test-First Loop

1. Add failing resolver tests for repo source policy:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/services/test_skill_resolution.py
```

Required failing coverage before implementation:
- Repo skill candidates are excluded when repo sources are policy-denied.
- Repo skill candidates participate in precedence when repo sources are policy-allowed.
- Policy summary reports repo and local source decisions.

2. Add or update focused service test coverage for immutable versions:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api/test_agent_skills_service.py
```

Required coverage:
- Creating a second version preserves the first version row and artifact metadata.

3. Implement the minimum source-policy changes:
- Add explicit repo-source policy to the skill resolution context.
- Make repo source loading return no active candidates when repo sources are denied.
- Preserve existing local-source denial behavior.
- Keep source precedence unchanged when repo and local sources are allowed.

4. Re-run focused unit tests:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/services/test_skill_resolution.py tests/unit/api/test_agent_skills_service.py tests/unit/services/test_skill_materialization.py
```

## Final Verification Commands

Run the required unit suite:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

Run required hermetic integration checks when Docker is available:

```bash
./tools/test_integration.sh
```

Run traceability check:

```bash
rg -n "MM-405|DESIGN-REQ-001|DESIGN-REQ-002|DESIGN-REQ-003|DESIGN-REQ-004" specs/206-agent-skill-catalog-source-policy
```

## End-to-End Story Validation

The story is complete when:
- Contract and service tests prove agent-skill instruction bundle contracts remain distinct from executable tool and runtime command contracts.
- Versioning tests prove a deployment-stored skill edit creates a new immutable version without mutating previous versions.
- Resolver tests prove source precedence for allowed sources and source exclusion for denied repo/local sources.
- Materialization tests prove runtime-visible skill data comes from the resolved snapshot and not checked-in folder mutation.
- Final verification preserves MM-405 and the original Jira preset brief.
