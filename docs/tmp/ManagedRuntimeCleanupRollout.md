# Managed Runtime Cleanup Rollout

This temporary working note tracks rollout sequencing for the managed runtime cleanup design in `docs/ManagedAgents/ManagedRuntimeCleanup.md`.

1. Document and wire dry-run only. Create the workflow/activity, store iterators, candidate scanner, and result model. Keep deletion disabled.
2. Run dry-run in production. Confirm counts against observed `/work/agent_jobs` volume evidence.
3. Enable bounded workspace deletion. Keep artifacts and record deletion disabled. Use small path/byte caps.
4. Enable artifact deletion later. Use longer retention and confirm UI/log surfaces do not depend on deleted local files.
5. Consider record deletion last. Leave disabled unless there is a clear operator/audit decision.
