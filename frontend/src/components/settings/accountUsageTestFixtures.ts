import type { components } from '../../generated/openapi';

type Projection = components['schemas']['ProviderAccountUsageProjection'];
type UsageWindow = components['schemas']['ProviderAccountUsageWindow'];

/** One provider-reported window, as the generated `account_usage` contract projects it. */
export function usageWindow(overrides: Partial<UsageWindow> = {}): UsageWindow {
  return {
    key: 'weekly',
    period: 'weekly',
    scope: 'general',
    model_label: null,
    duration_seconds: 604800,
    used_percent: 64,
    remaining_percent: 36,
    exhausted: false,
    reset_at: '2026-11-20T21:00:00Z',
    source: 'codex.app_server',
    source_version: null,
    observed_at: '2026-11-18T19:58:00Z',
    fresh_until: '2026-11-18T20:03:00Z',
    freshness: 'fresh',
    last_known: false,
    ...overrides,
  };
}

export type ProjectionOverrides = Partial<Omit<Projection, 'usage' | 'account' | 'refresh'>> & {
  windows?: UsageWindow[];
  primaryWindowKey?: string | null;
  account?: Partial<Projection['account']>;
  refresh?: Partial<Projection['refresh']>;
};

/** A cached `account_usage` projection for one profile credential generation. */
export function accountUsageProjection(overrides: ProjectionOverrides = {}): Projection {
  const { windows, primaryWindowKey, account, refresh, ...rest } = overrides;
  const usageWindows = windows ?? [usageWindow()];
  return {
    schema_version: 1,
    profile_id: 'codex-oauth',
    credential_generation: 3,
    snapshot_revision: 7,
    provider_id: 'openai',
    runtime_id: 'codex_cli',
    binding_verified: true,
    status: 'fresh',
    account: {
      status: 'verified',
      email: 'personal@example.com',
      display_name: 'Personal workspace',
      plan: 'plus',
      source: 'codex.app_server',
      observed_at: '2026-11-18T19:58:00Z',
      ...account,
    },
    usage: {
      completeness: 'full',
      primary_window_key:
        primaryWindowKey === undefined
          ? usageWindows.find((w) => w.period === 'weekly' && w.scope === 'general')?.key ?? null
          : primaryWindowKey,
      full_observed_at: '2026-11-18T19:58:00Z',
      windows: usageWindows,
    },
    refresh: {
      state: 'idle',
      request_id: null,
      last_attempt_at: '2026-11-18T19:58:00Z',
      last_success_at: '2026-11-18T19:58:00Z',
      last_failure_at: null,
      last_failure: null,
      next_allowed_at: null,
      ...refresh,
    },
    ...rest,
  };
}
