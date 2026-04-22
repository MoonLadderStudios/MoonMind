# Research: Launch Claude Secret Ref

## FR-001 / DESIGN-REQ-013 - Existing Provider-Profile Launch Path

Decision: Treat the existing managed runtime launcher and `ProviderProfileMaterializer` as the implementation path, then add exact Claude secret-ref launch coverage.
Evidence: `moonmind/workflows/temporal/runtime/launcher.py` resolves `profile.secret_refs`, constructs `ProviderProfileMaterializer`, and materializes environment/command before process start; `tests/unit/services/temporal/runtime/test_launcher.py` already covers generic secret-ref launch behavior for MiniMax.
Rationale: The MM-448 story explicitly forbids a new runtime-selection model, so the correct implementation route is the existing profile-driven launcher path.
Alternatives considered: Add a Claude-only launch branch. Rejected because it would violate the source design and create a second materialization path.
Test implications: Unit launcher boundary test for `claude_anthropic`.

## FR-002 through FR-005 / DESIGN-REQ-006 - Claude Profile Shape

Decision: Verify the exact `claude_anthropic` profile shape with `secret_refs.anthropic_api_key`, `env_template.ANTHROPIC_API_KEY.from_secret_ref`, and conflict clearing.
Evidence: `api_service/api/routers/provider_profiles.py` writes the post-auth shape for MM-447; `ProviderProfileMaterializer` supports alias-based `from_secret_ref` rendering and clear-env removal; existing materializer tests cover only direct `ANTHROPIC_API_KEY` secret refs and generic clear-env behavior.
Rationale: The implementation appears present but not sufficiently proven for the alias-based Claude Anthropic profile shape that MM-448 depends on.
Alternatives considered: Rely on MM-447 route tests. Rejected because those tests verify profile persistence, not runtime launch materialization.
Test implications: Unit materializer test and launcher test.

## FR-007 / FR-008 - Secret-Free Payloads and Diagnostics

Decision: Preserve compact secret refs in profile/workflow data and assert failure output does not include raw resolved secret material.
Evidence: Launcher resolves secrets inside the launch boundary and stores only profile refs before materialization; no code path found that intentionally logs resolved values. Missing-ref behavior is not directly covered for the Claude alias path.
Rationale: Secret-free durability is a security-critical requirement and should be proven at the error boundary as well as the success boundary.
Alternatives considered: Add broad logging redaction changes. Rejected unless tests expose leakage because existing boundaries already avoid durable raw secrets.
Test implications: Unit failure-path test that verifies no process starts and error text is actionable without containing supplied secret values.

## FR-009 - Missing or Unreadable Binding Failure

Decision: Use existing resolver/materializer exceptions as the fail-before-start mechanism; improve wording only if focused tests show the current output is not actionable enough.
Evidence: `resolve_managed_api_key_reference` raises `ValueError` for unresolved refs and `ProviderProfileMaterializer` raises for unknown `from_secret_ref` aliases.
Rationale: Failing before process launch satisfies the runtime requirement; tests should lock the behavior and keep raw secrets out of messages.
Alternatives considered: Add a new custom exception hierarchy. Rejected as unnecessary for this narrow story unless test evidence proves callers need it.
Test implications: Unit launcher failure test with `asyncio.create_subprocess_exec` guarded against invocation.

## FR-010 - Non-Claude Profile Preservation

Decision: Do not change the generic materializer contract unless required by failing tests.
Evidence: Existing OpenRouter, MiniMax, and generic materializer tests cover other profile shapes.
Rationale: MM-448 is a narrow Claude Anthropic launch story; broad materializer refactors would increase risk.
Alternatives considered: Refactor materialization into provider-specific classes. Rejected as unnecessary and contrary to the profile-driven design.
Test implications: Existing tests plus final focused test run.

## Test Strategy

Decision: Use unit-level boundary tests for materializer and launcher; no compose-backed integration is required.
Evidence: The behavior can be verified without external Claude Code or real secrets by monkeypatching secret resolution and subprocess launch, matching existing launcher tests.
Rationale: The highest-risk boundary is the environment passed to subprocess launch, not external provider behavior.
Alternatives considered: Provider verification with a real Anthropic token. Rejected because the story is about launch materialization, not provider API behavior, and provider credentials are not required for CI.
Test implications: Focused tests first, then final unit suite if feasible.
