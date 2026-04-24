# Research: Unrestricted Container and Docker CLI Contracts

## Story Classification

Decision: Treat MM-501 as a single-story runtime verification-first feature request.
Evidence: `docs/tmp/jira-orchestration-inputs/MM-501-moonspec-orchestration-input.md`; `specs/250-unrestricted-container-and-docker-cli-contracts/spec.md`.
Rationale: The Jira preset brief defines one independently testable runtime outcome: MoonMind exposes and enforces distinct unrestricted container and Docker CLI contracts without weakening the normal profile-backed path.
Alternatives considered: Broad design breakdown was rejected because the Jira brief already selects one bounded story.
Test implications: Unit tests plus at least one hermetic integration boundary are required because the story touches request validation, tool registration, and dispatcher/runtime execution behavior.

## FR-001 / DESIGN-REQ-017 First-Class `container.run_container`

Decision: implemented_verified.
Evidence: `moonmind/workloads/tool_bridge.py` registers `container.run_container` as a Docker-backed workload tool in unrestricted mode; `moonmind/schemas/workload_models.py` defines `UnrestrictedContainerRequest`; `tests/unit/workloads/test_workload_tool_bridge.py` verifies unrestricted tool registration; `tests/unit/workloads/test_workload_contract.py` verifies unrestricted request parsing; `tests/integration/temporal/test_integration_ci_tool_contract.py` executes `container.run_container` through the tool activity dispatcher in unrestricted mode.
Rationale: The unrestricted arbitrary-container contract already exists at the schema, tool-registration, and dispatcher/runtime layers.
Alternatives considered: Classify as implemented_unverified. Rejected because there is both unit and integration evidence for the unrestricted container path.
Test implications: Preserve unit plus integration evidence.

## FR-002 Structured Unrestricted Boundary Rejection

Decision: implemented_unverified.
Evidence: `moonmind/schemas/workload_models.py` restricts unrestricted requests to explicit fields, Docker named-cache mounts, and allowed network policies; `moonmind/workloads/docker_launcher.py` hard-codes `--privileged=false`, `--cap-drop ALL`, and `no-new-privileges`; `tests/unit/workloads/test_workload_contract.py` verifies unrestricted cache-mount and workspace constraints; `tests/unit/workloads/test_docker_workload_launcher.py` verifies restricted launcher args.
Rationale: The current code strongly suggests the structured unrestricted boundary is enforced, but the exact user-facing rejection set named by the MM-501 brief should remain under verification scrutiny until final traceability review confirms the source-design examples and current test coverage line up completely.
Alternatives considered: Mark as implemented_verified immediately. Rejected to keep the plan conservative where the source design names several forbidden capability classes that are enforced partly by schema shape and partly by launcher defaults.
Test implications: Re-run focused unit coverage and use final verification to decide whether any additional negative tests are needed.

## FR-003 / DESIGN-REQ-017 Docker-CLI-Specific `container.run_docker`

Decision: implemented_verified.
Evidence: `moonmind/schemas/workload_models.py` defines `UnrestrictedDockerRequest` and rejects commands whose first token is not `docker`; `moonmind/workloads/docker_launcher.py` replaces only the leading Docker binary while preserving Docker CLI arguments; `tests/unit/workloads/test_workload_contract.py` verifies the `docker` prefix requirement; `tests/unit/workloads/test_docker_workload_launcher.py` verifies Docker CLI arg construction; `tests/unit/workloads/test_workload_tool_bridge.py` verifies unrestricted Docker handler execution in unrestricted mode.
Rationale: The unrestricted Docker CLI contract is already explicitly distinguished from arbitrary-container execution.
Alternatives considered: Treat `container.run_docker` as a generic shell wrapper. Rejected because the code and tests already enforce the Docker-specific contract.
Test implications: Preserve unit plus integration evidence.

## FR-004 / DESIGN-REQ-010 Mode-Aware Denial

Decision: implemented_verified.
Evidence: `moonmind/workloads/tool_bridge.py` omits unrestricted tools in `profiles` mode and denies direct unrestricted invocation when not allowed; `moonmind/workflows/temporal/activity_runtime.py` raises non-retryable `docker_workflow_mode_forbidden` errors; `tests/unit/workloads/test_workload_tool_bridge.py`, `tests/unit/workflows/temporal/test_workload_run_activity.py`, and `tests/integration/temporal/test_integration_ci_tool_contract.py` verify `profiles` versus `unrestricted` behavior.
Rationale: Mode-aware unrestricted-tool denial is already enforced both at registration and at runtime invocation.
Alternatives considered: Add a second policy layer outside the existing tool bridge and activity runtime. Rejected because the current shared decision path already exists.
Test implications: Preserve unit plus integration evidence.

## FR-005 / DESIGN-REQ-025 Preserve `container.run_workload` As Profile-Backed

Decision: implemented_verified.
Evidence: `moonmind/workloads/tool_bridge.py` keeps `container.run_workload`, `container.start_helper`, and `container.stop_helper` in the curated/profile-backed set while adding unrestricted tools separately; `moonmind/workflow_docker_mode.py` and `tool_allowed_for_workflow_docker_mode` keep the mode matrix explicit; `tests/unit/workloads/test_workload_tool_bridge.py` verifies profiles-mode exposure excludes unrestricted tools; `tests/integration/temporal/test_integration_ci_tool_contract.py` shows unrestricted registration is additive rather than a replacement for curated workload paths.
Rationale: The current unrestricted implementation preserves the normal profile-backed path instead of widening it.
Alternatives considered: Treat `container.run_workload` as a backward-compatible alias for unrestricted requests. Rejected because both the source design and repo code keep the paths distinct.
Test implications: Preserve unit plus integration evidence.

## FR-006 / DESIGN-REQ-022 Example-Flow Alignment

Decision: implemented_unverified.
Evidence: `docs/ManagedAgents/DockerOutOfDocker.md` defines unrestricted example flows in sections 18.2-18.4; `moonmind/schemas/workload_models.py` and `moonmind/workloads/docker_launcher.py` define the current request shape and runtime behavior; `tests/unit/workloads/test_workload_contract.py` covers example-like unrestricted request payloads.
Rationale: The repo appears aligned with the documented unrestricted examples, but this is principally a traceability and contract-verification concern that final verification should compare directly rather than assume from adjacent coverage.
Alternatives considered: Mark as implemented_verified solely from source/code similarity. Rejected because the story explicitly names example-flow alignment and that deserves an explicit final review.
Test implications: Focused unit verification plus final contract review; add tests only if final verification finds drift.

## FR-007 Traceability

Decision: implemented_verified.
Evidence: `docs/tmp/jira-orchestration-inputs/MM-501-moonspec-orchestration-input.md`; `specs/250-unrestricted-container-and-docker-cli-contracts/spec.md`; `plan.md`; `research.md`; `contracts/unrestricted-docker-workload-contract.md`; `quickstart.md`.
Rationale: The feature-local MoonSpec artifacts preserve MM-501 and the original Jira preset brief for downstream tasks and verification.
Alternatives considered: Preserve the Jira key only in the source brief. Rejected because the story explicitly requires downstream traceability.
Test implications: Final traceability review only.

## Design Artifact Decision

Decision: create a feature-local contract artifact and skip `data-model.md`.
Evidence: MM-501 is about runtime tool-contract behavior, policy boundaries, and verification evidence; it does not add new persisted entities or a changed storage shape.
Rationale: A contract artifact is necessary because the story is about the unrestricted tool surface and its allowed and forbidden inputs. A separate data model would duplicate existing request-model code without clarifying the story.
Alternatives considered: Create `data-model.md` for unrestricted request entities. Rejected because those shapes already exist in repo code and the story does not introduce new persistent data.
Test implications: Contract review plus unit and integration verification are sufficient.

## Planning Workflow Gap

Decision: continue manual planning artifact generation and record the missing helper scripts as an environment gap.
Evidence: `scripts/bash/setup-plan.sh` and `scripts/bash/update-agent-context.sh` are not present in this repository checkout.
Rationale: The MoonSpec plan gate still requires `plan.md`, `research.md`, `quickstart.md`, and required contract artifacts, so planning should continue instead of stopping on missing helper automation.
Alternatives considered: Stop planning entirely because the helper scripts are absent. Rejected because the required planning artifacts can still be produced deterministically from the active feature directory.
Test implications: None beyond documenting the missing helper scripts in the final report.
