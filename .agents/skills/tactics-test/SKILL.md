---
name: tactics-test
description: Run Unreal Engine build and automation tests for Tactics through managed container jobs, or an explicitly configured standalone Docker host.
metadata:
  required-capabilities:
    - docker
---

# Tactics Build and Test

Run Linux Unreal builds and targeted automation tests for `Tactics.uproject`.
The Skill submits work to the existing container-job owner inside MoonMind;
the standalone path uses the host's Docker daemon directly.

## Inputs and boundaries

- The repository defaults to the admitted workspace in managed sessions and
  the current working directory on standalone hosts. Use `--repo` for an
  explicit project directory and `--uproject` for its project-relative file.
- Managed sessions use the admitted workspace and the provisioned
  `imageSourceRef=tactics-unreal`, with `unreal-ccache` and `unreal-ubt` caches.
  A project subdirectory maps under that workspace; an outside repository is
  rejected rather than silently testing a different candidate.
- Standalone execution requires Docker CLI/daemon access and a compatible
  image configured in `MOONMIND_UNREAL_ENGINE_IMAGE`, or selected with `--image`.
  An unconfigured toolchain is reported; the script does not guess a version.
- `--image` selects an explicit toolchain. In managed execution it remains
  subject to the container backend's image and registry authorization.
  `--pull` selects its pull policy; without an explicit managed image, the
  provisioned image source owns that policy and `always`/`never` are rejected.
- `--workspace-volume`, `--workspace-target`, cache mount options and `--platform`
  configure only standalone Docker execution. Managed resources
  and cache authority remain owned by the backend.

Inside a managed session there is no Docker CLI, by design. Run the entrypoint
and read its diagnostic; do not probe `docker info` or `docker --version` to
infer Unreal availability. Partial managed identity is an environment defect,
not permission to fall back to host Docker. Missing provisioned image or
registry access is a precise toolchain limitation, not a completed test.

## Commands

Run from the Tactics project directory, using the selected Skill's script path:

```bash
scripts/run_dood_unreal_tactics.sh
scripts/run_dood_unreal_tactics.sh --phase build
scripts/run_dood_unreal_tactics.sh --phase test --test-filter "Tactics.Unit.HostRuntime.MatrixSystems"
scripts/run_dood_unreal_tactics.sh --repo /path/to/Tactics --image "$MOONMIND_UNREAL_ENGINE_IMAGE"
scripts/run_dood_unreal_tactics.sh --dry-run
```

The default phase is `all`: build first, then test only after the build passes.
Explicit target, build platform, configuration and test filter retain their
values. The chosen image must provide the compatible Unreal toolchain at the
paths used by the script; a successful image pull does not establish that.

## Results and recovery

Report the current gate's repository, phase, selected image/source, per-phase
statuses, result directory and evidence paths. Managed results live under
`.artifacts/moonmind-unreal-tactics/`; standalone results live under
`.artifacts/dood-unreal-tactics/`. Each new run gets a distinct directory.
`latest/gate.json` (or `--gate-file`) is only the current-run summary; retained
run artifacts remain available separately.

- A build-only PASS is build success, never test success. Skipped phases have
  no required log. Tests require positive completed-test evidence; empty or
  unavailable logs are incomplete, no matching tests are `no_tests`, and a
  failed automation result fails even when the process exits zero.
- Managed execution consumes the current container job's structured result,
  including its job identity and artifact references. Test verdicts use the
  full Unreal log written into the shared workspace and declared as a job
  output, rather than a truncated CLI tail. A submission, process exit or
  successful container start alone does not prove completion.
- Resume the same managed run with `--resume <results-directory>` and its
  original options. Matching successful phases are retained; missing evidence
  reattaches with the same job request identity. Changed inputs or source
  invalidate reuse. Resume requires a Git source snapshot; non-Git projects
  can run but cannot safely reuse prior phases. A new request without
  `--resume` creates fresh work.
- Preserve original logs/results when investigating failures. Never credit an
  earlier `latest/gate.json` as the result of a new run. Read the gate written
  by this invocation and distinguish incomplete work from a failed phase.
- `--dry-run` is a non-mutating preview. It submits no jobs, pulls no images,
  writes no gate and preserves prior evidence. Its stdout `SKIPPED` result
  never satisfies publish gating or proves toolchain availability.

Publish gating requires `status="PASS"` for the requested phases. Include the
exact remaining evidence or environment limitation when the gate is not PASS.
