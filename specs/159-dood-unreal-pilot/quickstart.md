# Quickstart: DooD Unreal Pilot

1. Ensure the Docker-capable `agent_runtime` worker can reach the Docker proxy.
2. Ensure the Docker host has the `agent_workspaces`, `unreal_ccache_volume`, and `unreal_ubt_volume` named volumes.
3. Use the built-in `config/workloads/default-runner-profiles.yaml`, or set `MOONMIND_WORKLOAD_PROFILE_REGISTRY` to an operator-owned registry containing `unreal-5_3-linux`.
4. Submit an executable tool step using `tool.type = "skill"` and `name = "unreal.run_tests"`.
5. Inspect workload artifacts under the step `artifactsDir` for stdout, stderr, diagnostics, and declared test reports.
