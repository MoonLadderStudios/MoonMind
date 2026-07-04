# MoonSpec Verify Resolution Failure

Document Class: Imperative Working Document
Working Type: Implementation Plan
Status: Active
Date: 2026-07-04
Source Workflow: `mm:c6720d9c-10c7-4fe7-87de-4b3ed06c74fb`

## Failure

The workflow failed in the parent `MoonMind.UserWorkflow` during `agent_skill.resolve` for the final `moonspec-verify` step. Temporal reported:

```text
ValueError: Could not resolve selected skill 'moonspec-verify'
```

The running workflow worker had `/app/.agents/skills/moonspec-verify/SKILL.md` as a symlink to `../../../moonspec/bundle/skills/moonspec-verify/SKILL.md`, but `/app/moonspec` was empty. The local checkout also had an uninitialized `moonspec` submodule before remediation, so the compose bind mount `./moonspec:/app/moonspec:ro` exposed an empty directory to the worker.

The published `ghcr.io/moonladderstudios/moonmind:latest` image also had an empty `/app/moonspec`. The release workflow checked out the repository without submodules before building `api_service/Dockerfile`, so `COPY moonspec /app/moonspec/` shipped an empty submodule directory while `.agents/skills/moonspec-*` symlinks still pointed into it.

## Possible Solutions

1. Operator remediation only: run `git submodule update --init --recursive moonspec` and restart affected containers. This fixes the current local bind mount, but it does not prevent another broken GHCR image.
2. Remove the `./moonspec:/app/moonspec:ro` compose bind mount and rely only on image-bundled MoonSpec content. This avoids local empty-submodule shadowing, but it makes local MoonSpec bundle iteration less direct and still requires correct image packaging.
3. Fix image publishing: check out submodules in `docker-publish.yml` and add a Dockerfile guard that fails the build if `moonspec.bundle.yaml` or `moonspec-verify/SKILL.md` is absent. This prevents broken runtime images while preserving the existing source-mounted local development path.
4. Improve resolver diagnostics: make broken skill symlinks visible during discovery and fail selected skills with the existing submodule remediation hint instead of reporting a generic unresolved skill.

## Selected Fix

Implement solutions 3 and 4.

This is the most direct durable fix because the release workflow created the broken image, and the resolver behavior hid the real missing-submodule cause. The compose/local workaround remains available for already-running deployments: initialize the `moonspec` submodule and restart workers, or pull a rebuilt image after this fix lands.

## Verification Plan

- Unit-test the Docker publish workflow checkout for `submodules: recursive`.
- Unit-test the Dockerfile guard for required MoonSpec bundle files.
- Unit-test skill resolution with a broken `moonspec-verify` symlink to confirm the actionable remediation message.
- Run the targeted unit tests for the changed workflow, Dockerfile, and skill resolver.

## Verification Results

- `git submodule update --init --recursive moonspec` populated the local bundle at pinned commit `2a2d9e703ed7b8545c7d0556844a783408826df0`.
- `python3 tools/link_moonspec_submodule.py --check --prune` passed.
- `./tools/test_unit.sh tests/unit/test_docker_publish_workflow.py tests/unit/services/test_skill_resolution.py` passed, including the unit runner's frontend checks.
- `./tools/test_integration.sh` passed: 407 passed, 1 skipped, 87 deselected.
- After restoring the local compose stack, `temporal-worker-agent-runtime` successfully resolved `moonspec-verify` in a container smoke check.
