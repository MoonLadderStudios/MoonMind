/** Tier editor capability loading – backend-driven, no hard-coded catalog. */
import { useEffect, useState } from 'react';

export type TierCapabilityOption = {
  value: string;
  label: string;
  description?: string | null;
  status: 'available' | 'deprecated' | 'unavailable';
  recommended?: boolean;
  compatible_models?: string[] | null;
};

export type ProviderProfileTierCapabilities = {
  version: string;
  profile_id: string | null;
  runtime_id: string;
  provider_id: string;
  evidence: {
    source: 'profile_catalog_evidence' | 'runtime_draft';
    credential_generation: number | null;
    image_ref: string | null;
    observed_at: string | null;
    stale: boolean;
  };
  tier_constraints: { min_count: number; max_count: number | null };
  model: {
    runtime_default: string | null;
    allow_custom: boolean;
    options: TierCapabilityOption[];
  };
  effort: {
    supported: boolean;
    runtime_default: string | null;
    allow_custom: boolean;
    application: string;
    options: TierCapabilityOption[];
  };
  diagnostics: Array<{ code: string; level: string; message: string }>;
};

export type CapabilityState = {
  data: ProviderProfileTierCapabilities | null;
  loading: boolean;
  error: string | null;
};

async function fetchTierCapabilitiesForProfile(profileId: string, signal?: AbortSignal): Promise<ProviderProfileTierCapabilities> {
  const res = await fetch(`/api/v1/provider-profiles/${encodeURIComponent(profileId)}/capabilities`, {
    headers: { Accept: 'application/json' },
    signal,
  });
  if (!res.ok) {
    const payload = await res.json().catch(() => ({}));
    const msg = typeof (payload as Record<string, unknown>).detail === 'string' ? (payload as Record<string, unknown>).detail as string : `Failed to load tier capabilities for ${profileId}`;
    throw new Error(msg);
  }
  return (await res.json()) as ProviderProfileTierCapabilities;
}

async function fetchTierCapabilitiesForDraft(runtimeId: string, providerId: string, signal?: AbortSignal): Promise<ProviderProfileTierCapabilities> {
  const params = new URLSearchParams({ runtime_id: runtimeId, provider_id: providerId });
  const res = await fetch(`/api/v1/provider-profiles/capabilities?${params.toString()}`, {
    headers: { Accept: 'application/json' },
    signal,
  });
  if (!res.ok) {
    const payload = await res.json().catch(() => ({}));
    const msg = typeof (payload as Record<string, unknown>).detail === 'string' ? (payload as Record<string, unknown>).detail as string : 'Failed to load tier capabilities';
    throw new Error(msg);
  }
  return (await res.json()) as ProviderProfileTierCapabilities;
}

export function useProviderProfileTierCapabilities(opts: {
  profileId?: string | null;
  runtimeId?: string;
  providerId?: string;
  enabled?: boolean;
}): CapabilityState {
  const { profileId, runtimeId, providerId, enabled = true } = opts;
  const [state, setState] = useState<CapabilityState>({ data: null, loading: false, error: null });

  useEffect(() => {
    if (!enabled) {
      setState({ data: null, loading: false, error: null });
      return;
    }
    const controller = new AbortController();
    const load = async () => {
      // Prefer profile-scoped when we have profileId
      if (profileId) {
        setState((s) => ({ ...s, loading: true, error: null }));
        try {
          const data = await fetchTierCapabilitiesForProfile(profileId, controller.signal);
          if (controller.signal.aborted) return;
          setState({ data, loading: false, error: null });
        } catch (e) {
          if (controller.signal.aborted) return;
          setState({ data: null, loading: false, error: e instanceof Error ? e.message : String(e) });
        }
        return;
      }
      if (runtimeId && providerId) {
        setState((s) => ({ ...s, loading: true, error: null }));
        try {
          const data = await fetchTierCapabilitiesForDraft(runtimeId, providerId, controller.signal);
          if (controller.signal.aborted) return;
          setState({ data, loading: false, error: null });
        } catch (e) {
          if (controller.signal.aborted) return;
          setState({ data: null, loading: false, error: e instanceof Error ? e.message : String(e) });
        }
        return;
      }
      setState({ data: null, loading: false, error: null });
    };
    void load();
    return () => controller.abort();
  }, [profileId, runtimeId, providerId, enabled]);

  return state;
}

export function resolvesToPreview(tierModel: string | null, tierEffort: string | null, caps: ProviderProfileTierCapabilities | null): string {
  if (!caps) {
    if (tierModel == null && tierEffort == null) return 'Runtime default';
    return `${tierModel ?? 'Runtime default'} · ${tierEffort ?? 'Runtime default'}`;
  }
  const modelRes = tierModel ?? caps.model.runtime_default ?? 'Runtime default (unknown)';
  const effortRes = tierEffort ?? caps.effort.runtime_default ?? (caps.effort.supported ? 'Runtime default (unknown)' : 'Not supported');
  return `Resolves to ${modelRes} · ${effortRes}`;
}
