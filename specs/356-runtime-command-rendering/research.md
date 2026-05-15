# Research: Runtime Command Rendering After Context Preparation

## FR-001 / DESIGN-REQ-011: Final Render Ordering

Decision: Missing; add a final render step after retrieval context injection, skill activation summary projection, and managed runtime notes, immediately before command construction/process launch.
Evidence: `moonmind/workflows/temporal/runtime/strategies/codex_cli.py` mutates `request.instruction_ref` in `prepare_workspace()` with retrieval context and managed notes. `moonmind/workflows/temporal/runtime/launcher.py` then prepends skill activation summary in `_project_run_skill_snapshot()` before calling `build_command()`. No renderer reorders slash commands after these mutations.
Rationale: Parser-only behavior is insufficient because context and skill summaries can move the command away from the first runtime-visible character.
Alternatives considered: Rendering during task normalization was rejected because later runtime preparation mutates the prompt. Rendering in the Create page was rejected because provider-specific behavior belongs to runtime adapters.
Test implications: Unit tests for renderer order plus integration tests through launcher with retrieval and skill projection.

## FR-002 / DESIGN-REQ-012: Runtime-Owned Render Contract

Decision: Missing; extend the managed runtime strategy boundary with a render result model that reports render mode, final instruction text, unsupported state, fallback event, or failure reason.
Evidence: `ManagedRuntimeStrategy` currently exposes `prepare_workspace()` and `build_command()` but no command render contract. Existing command builders append `request.instruction_ref` directly.
Rationale: The contract keeps provider-specific command recognition out of Create page and central launcher branching.
Alternatives considered: Adding launcher-only `if runtime_id` branches was rejected because it violates adapter modularity and would not support future runtimes cleanly.
Test implications: Unit tests on base/default behavior and Codex/Claude strategy rendering.

## FR-003 / FR-007 / DESIGN-REQ-013: Codex And Claude Prompt Prefix

Decision: Missing; implement prompt-prefix rendering for Codex CLI and Claude Code so `/review` is first, followed by instruction body and prepared context.
Evidence: `CodexCliStrategy.build_command()` and `ClaudeCodeStrategy.build_command()` append `request.instruction_ref` as the final prompt argument. Existing tests verify prompt passing but not slash-command first-position behavior.
Rationale: Codex CLI and Claude Code are named in the Jira brief as required runtime targets.
Alternatives considered: Materializing files for all commands was rejected because unknown commands must pass through as text and materialization is only safe for allowlisted known commands.
Test implications: Unit strategy tests and launcher integration tests for both runtimes.

## FR-004 / FR-010 / DESIGN-REQ-017: Literal And Escaped Commands

Decision: Partial; escaped and malformed slash text is detected in `task_contract.py`, but runtime rendering must enforce non-executable literal output.
Evidence: `tests/unit/workflows/tasks/test_task_contract.py` covers escaped slash metadata. No runtime launch test asserts literal slash wrapping/prefixing.
Rationale: Preserving user intent requires that escaped commands never become executable after final rendering.
Alternatives considered: Dropping command metadata for escaped input was rejected because audit surfaces need to know why slash text was literal.
Test implications: Unit renderer tests and one launcher integration check for escaped literal behavior.

## FR-006 / FR-011 / DESIGN-REQ-019: Failure And Fallback

Decision: Missing; renderer failures must fail before launch or emit a policy-approved fallback event.
Evidence: Launcher has general subprocess and strategy failure handling, but no `runtime_command_render_failed` pre-launch classification or render fallback annotation.
Rationale: A renderer failure after command detection is user-actionable and must not silently launch with an unsafe prompt shape.
Alternatives considered: Always falling back to literal prompt was rejected because the spec requires policy-approved fallback only.
Test implications: Unit tests for render failure outcomes and launcher integration proving subprocess launch is skipped on hard failure.

## FR-008 / FR-009 / DESIGN-REQ-016: Unknown Commands And Materialization Guard

Decision: Partial to implemented_unverified; unknown valid commands are normalized as opaque pass-through, but final runtime rendering must consume that metadata and explicitly prevent unknown materialization.
Evidence: `task_contract.py` returns `hintStatus = opaque` and `recognitionMode = runtime_passthrough` for unknown valid commands; existing unit tests cover normalization. No runtime renderer consumes these fields.
Rationale: Unknown commands are valid for slash-capable runtimes and must remain first-class pass-through invocations.
Alternatives considered: Requiring a known hint before execution was rejected because the source design forbids using hints as a blocking allowlist.
Test implications: Unit tests for opaque prompt-prefix/native render and materialization guard.

## FR-012 / FR-013 / FR-014 / DESIGN-REQ-018: Security And Trust Boundary

Decision: Partial; existing normalization treats command text as data and launcher redaction exists, but renderer-specific no-secret and untrusted-text tests are needed.
Evidence: `task_contract.py` validates supplied metadata against backend normalization. `ManagedRuntimeLauncher` imports `SecretRedactor` and shapes environment. Render-specific diagnostics do not exist yet.
Rationale: The renderer will handle user-authored command text at a launch boundary, so tests must prove no shell construction or secret leakage is introduced.
Alternatives considered: Trusting known-command hints as executable command definitions was rejected; hints enrich UX and must not authorize arbitrary shell behavior.
Test implications: Unit security tests for command args/body and redacted failure/fallback diagnostics.

## FR-015 / SC-002: Prepared Context Regression Coverage

Decision: Missing; add coverage that retrieval context, skill activation summaries, and managed runtime notes are all positioned after commands requiring first-position recognition.
Evidence: `tests/integration/workflows/temporal/test_managed_session_retrieval_context.py` proves context injection reaches Claude prompt. `tests/unit/services/temporal/runtime/test_launcher.py` proves skill summary is prepended. Neither proves final slash-command ordering.
Rationale: This is the highest-risk behavioral gap named by MM-686.
Alternatives considered: Testing only strategy methods was rejected because ordering depends on launcher sequencing.
Test implications: Integration tests through `ManagedRuntimeLauncher.launch()` with patched subprocess capture.

## FR-016 / SC-007: Jira Traceability

Decision: Implemented_unverified; `spec.md` preserves MM-686 and the original preset brief, and planning artifacts must preserve it through final verification.
Evidence: `specs/356-runtime-command-rendering/spec.md` contains `MM-686` in the input, FR-016, and SC-007.
Rationale: Final verification and PR metadata must be able to compare implementation evidence back to Jira.
Alternatives considered: Keeping only a local handoff artifact was rejected because committed spec artifacts need durable traceability.
Test implications: Final MoonSpec verification checks traceability; no runtime test required.

## Test Tooling

Decision: Use pytest through `./tools/test_unit.sh` for unit coverage and `./tools/test_integration.sh` for hermetic integration coverage. Targeted iteration can use focused pytest paths before the final required runners.
Evidence: Repository instructions define `./tools/test_unit.sh` and `./tools/test_integration.sh` as required runners. Existing runtime tests are pytest-based.
Rationale: The change touches runtime strategy and launcher boundaries, so both unit and integration tests are required.
Alternatives considered: Browser or provider verification tests were rejected because this story concerns backend managed runtime launch behavior and must remain hermetic.
Test implications: Add red-first unit tests before code and at least one `integration_ci`-eligible launcher/runtime boundary test if possible.
