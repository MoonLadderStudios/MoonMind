# Verification: Refresh Managed Runtimes After Derived Skill Activation

## Implementation-Step Evidence

- Red-first focused unit command failed before production changes for missing MM-615 behavior: digest verification, partial projection preservation, compact activation metadata, runtime refresh diagnostics, and external-agent v1 exclusion.
- Red-first focused integration command failed before production changes for missing activation timing metadata and checksum failure classification.
- Focused unit validation passed:
  `pytest tests/unit/services/test_skill_materialization.py tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py tests/unit/workflows/adapters/test_base_external_agent_adapter.py -q`
- Focused integration validation passed:
  `pytest tests/integration/temporal/test_skills_on_demand_request_activation.py -m integration_ci -q`
- Full unit validation passed:
  `./tools/test_unit.sh`

## Quickstart Deviations

- Full hermetic integration validation command did not complete:
  `./tools/test_integration.sh`
- Blocker: Docker compose test startup failed in this managed environment with Docker build access denied by administrative rules after reporting that the buildx plugin is required.
- Smallest next step: rerun `./tools/test_integration.sh` in an environment where Docker compose build access and buildx are available.

## MM-615 Traceability

MM-615 remains preserved in the feature artifacts. The implementation covers activation only after verified materialization, snapshot preservation on materialization failure, next-turn guidance when canonical projection cannot be atomically switched, compact activation results, distinct `materialization_failed` and `runtime_refresh_failed` diagnostics, and v1 exclusion for external-agent activation support.
