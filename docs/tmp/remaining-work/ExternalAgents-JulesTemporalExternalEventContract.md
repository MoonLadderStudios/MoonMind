# Remaining work: `docs/ExternalAgents/JulesTemporalExternalEventContract.md`

**Source:** [`docs/ExternalAgents/JulesTemporalExternalEventContract.md`](../../ExternalAgents/JulesTemporalExternalEventContract.md)  
**Last synced:** 2026-03-24

## Open items

- **§4 Implementation stance (reconcile with code):** Source states `JulesAgentAdapter` instantiation in `MoonMind.AgentRun` “not yet wired (Phase C).” Verify against current `agent_run.py`; if outdated, fix the doc and remove this tracker item.
- **`integration.jules.cancel`:** Not registered in activity catalog (per source) — register and wire or document intentional omission.
- **`invoke_adapter_cancel` → Jules adapter:** Phase C wiring pending in source.
- **Callback-first posture:** Until Jules callbacks are reliable, polling remains; implement callback correlation + `ExternalEvent` path per contract sections.
- **Status taxonomy:** Full locked Jules status mapping still called out as incomplete in places — align normalizer with provider.
