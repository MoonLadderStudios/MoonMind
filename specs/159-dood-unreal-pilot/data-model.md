# Data Model: DooD Unreal Pilot

## RunnerProfile

- `id`: `unreal-5_3-linux`
- `image`: pinned Unreal runner image reference
- `requiredMounts`: `agent_workspaces` mounted at `/work/agent_jobs`
- `optionalMounts`: `unreal_ccache_volume` and `unreal_ubt_volume`
- `envAllowlist`: Unreal runner env keys plus cache path keys
- `networkPolicy`: `none`
- `devicePolicy`: `none`
- `resources`: bounded Unreal pilot CPU, memory, and shared-memory settings
- `timeoutSeconds`: 7200
- `maxConcurrency`: 1

## Unreal Run Tests Input

- `projectPath`: required repository-relative Unreal project path
- `target`: optional target name
- `testSelector`: optional automation test selector
- `reportPaths`: optional relative artifact output paths for primary, summary, and junit reports
- `envOverrides`: optional allowlisted env overrides; raw Docker env is still denied by profile validation

## Workload Outputs

- `runtime.stdout`
- `runtime.stderr`
- `runtime.diagnostics`
- `output.primary`
- `output.summary`
- `output.logs.junit`

Cache volume contents are operational acceleration state and are not output artifacts.
