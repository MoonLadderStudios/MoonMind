---
name: tactics-test
description: Run Unreal Engine build and automation test workflows for Tactics through MoonMind's shared Docker backend, with a portable direct-Docker fallback outside MoonMind.
---

# Dood Unreal Tactics Build Test

## Overview

Run Linux Unreal build and targeted automation tests for `Tactics.uproject`. Inside a MoonMind managed session, submit typed container jobs so every workflow uses the deployment daemon's shared image cache. Outside MoonMind, use the portable direct-Docker fallback. Capture build and test logs in timestamped artifact folders inside the Tactics repository, and emit a machine-readable gate file for publish blocking.

## Inputs

- Repository path (defaults to the active managed workspace inside MoonMind and
  `/mnt/d/Unreal/Tactics` in the direct-Docker fallback)
- Inside MoonMind: `containerJobs` capability and an operator-provisioned `tactics-unreal` image source
- Outside MoonMind: Docker CLI access and a reachable daemon
- Container image with the Unreal toolchain in the selected execution substrate

## Workflow

1. Validate repository path and project file.
2. Run `scripts/run_dood_unreal_tactics.sh` from this skill directory.
   The script selects `moonmind container run --spec` when managed-session
   identity is present; it does not inspect or invoke Docker in that mode.
3. Review timestamped artifacts under `.artifacts/dood-unreal-tactics/<timestamp>/` in the Tactics repo.
4. Review gate result at `.artifacts/dood-unreal-tactics/latest/gate.json` (or explicit `--gate-file` path).
5. Report:
   - Build status (if build phase ran)
   - Test status (if test phase ran)
   - Build log and test log paths

## Commands

### Build and test through the MoonMind shared Docker backend

```bash
scripts/run_dood_unreal_tactics.sh --repo "$(pwd)"
```

### Build and test with defaults (local bind mounts)

```bash
scripts/run_dood_unreal_tactics.sh
```

### Build and test with explicit gate file

```bash
scripts/run_dood_unreal_tactics.sh \
  --repo $(pwd) \
  --gate-file .artifacts/dood-unreal-tactics/latest/gate.json
```

### Build only

```bash
scripts/run_dood_unreal_tactics.sh --phase build
```

### Test only with custom filter

```bash
scripts/run_dood_unreal_tactics.sh --phase test --test-filter "Tactics.Unit.HostRuntime.MatrixSystems"
```

### Explicit repository and image

```bash
scripts/run_dood_unreal_tactics.sh \
  --repo /mnt/d/Unreal/Tactics \
  --image ghcr.io/epicgames/unreal-engine:dev-5.5@sha256:3f7b292cda7f6066aeaea46fa95a520a0d26811810e0d082cfbf5dc85018bd82
```

### Dry-run command preview

```bash
scripts/run_dood_unreal_tactics.sh --dry-run
```

## Notes

- The script runs build and test in separate ephemeral containers to keep worker and toolchain isolation clear.
- MoonMind mode uses `imageSourceRef=tactics-unreal`, `cacheRef=unreal-ccache`,
  and `cacheRef=unreal-ubt`. The trusted backend resolves the host image and
  volume names; the skill never receives daemon or raw-volume authority.
- `--workspace-volume`, `--ccache-volume`, and `--ubt-volume` apply only to the
  portable direct-Docker fallback outside MoonMind.
- Use `--pull always` when you need to force-refresh the container image.
- Gate contract: publish gating consumes the JSON gate output and requires `status="PASS"`.
