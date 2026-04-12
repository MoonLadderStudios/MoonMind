# Research: DooD Unreal Pilot

## Decision: Ship a default deployment-owned registry file

Use `config/workloads/default-runner-profiles.yaml` as the built-in registry source and keep `MOONMIND_WORKLOAD_PROFILE_REGISTRY` as the operator override.

**Rationale**: Phase 6 needs a curated profile available without arbitrary image strings. A checked-in config file is visible to operators, testable, and still deployment-owned.

**Alternatives considered**: Require every deployment to set `MOONMIND_WORKLOAD_PROFILE_REGISTRY`; rejected because the pilot should have a safe default. Embed the profile in Python code; rejected because profile policy is more operator-visible as config.

## Decision: Pin an external image reference under the approved registry policy

Use `ghcr.io/moonladderstudios/moonmind-unreal-runner:5.3` for the pilot profile.

**Rationale**: The repo cannot build or validate a licensed Unreal Engine image in unit CI. A pinned non-`latest` GHCR image policy satisfies the control-plane contract while leaving image publication to deployment operations.

**Alternatives considered**: Build the Unreal image during CI; rejected because Unreal licensing/assets are not available in hermetic unit CI. Allow arbitrary operator image strings; rejected because Phase 5 requires profile allowlists.

## Decision: Model reports as declared workload outputs

Map Unreal report paths into `declaredOutputs` such as `output.primary`, `output.summary`, and `output.logs.junit`.

**Rationale**: The launcher already collects declared outputs under `artifactsDir`, which keeps durable outputs separate from cache volumes.

**Alternatives considered**: Parse runner-specific report locations after execution; rejected because declared outputs are already the workload contract. Publish reports as session continuity artifacts; rejected because workload containers are not managed sessions.

## Decision: Keep caches as optional profile mounts

Mount `unreal_ccache_volume` and `unreal_ubt_volume` from the profile, not the tool input.

**Rationale**: Repeat execution can reuse caches without allowing plan authors to choose arbitrary mounts or treating cache state as workflow truth.

**Alternatives considered**: Expose cache mounts as tool inputs; rejected because raw mount selection is outside the curated tool contract. Publish cache contents as artifacts; rejected because caches are acceleration state, not durable run evidence.
