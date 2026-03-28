# Managed Agents Authentication

> [!IMPORTANT]
> This document has been superseded by [ProviderProfiles.md](../Security/ProviderProfiles.md).
>
> The Provider Profiles document covers the full provider-aware profile system including
> credential sources, runtime materialization modes, provider-aware selection, and
> concurrency/cooldown policy.

**Implementation tracking:** [`docs/tmp/remaining-work/ManagedAgents-ManagedAgentsAuthentication.md`](../tmp/remaining-work/ManagedAgents-ManagedAgentsAuthentication.md)

---

## Quick Reference

| Topic | See |
|-------|-----|
| Provider profile model and contract | [ProviderProfiles.md §5](../Security/ProviderProfiles.md) |
| Credential sources and materialization modes | [ProviderProfiles.md §4, §6](../Security/ProviderProfiles.md) |
| Profile examples (Anthropic, MiniMax, Z.AI, OpenAI) | [ProviderProfiles.md §7](../Security/ProviderProfiles.md) |
| Selection and assignment | [ProviderProfiles.md §8–9](../Security/ProviderProfiles.md) |
| Materialization pipeline | [ProviderProfiles.md §10](../Security/ProviderProfiles.md) |
| Security requirements | [ProviderProfiles.md §12](../Security/ProviderProfiles.md) |
| OAuth session UX | [TmateArchitecture.md](TmateArchitecture.md), [OAuthTerminal.md](OAuthTerminal.md) |
| OAuth volume provisioning scripts | `tools/auth-gemini-volume.sh`, `tools/auth-codex-volume.sh`, `tools/auth-claude-volume.sh` |
