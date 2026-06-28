import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  ConfigurationHealthSummary,
  summarizeConfigurationHealth,
  type ConfigurationHealthSummaryProps,
} from './ConfigurationHealthSummary';
import type { ProviderProfile } from './ProviderProfilesManager';
import type { WorkerPauseConfig } from './OperationsSettingsSection';

function makeProfile(overrides: Partial<ProviderProfile> = {}): ProviderProfile {
  return {
    profile_id: 'profile-1',
    runtime_id: 'claude',
    provider_id: 'anthropic',
    provider_label: 'Anthropic',
    credential_source: 'managed_secret',
    runtime_materialization_mode: 'ephemeral',
    secret_refs: {},
    max_parallel_runs: 1,
    cooldown_after_429_seconds: 0,
    rate_limit_policy: 'default',
    enabled: true,
    is_default: false,
    ...overrides,
  };
}

function renderSummary(props: Partial<ConfigurationHealthSummaryProps> = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <ConfigurationHealthSummary
        providerProfiles={props.providerProfiles ?? []}
        secrets={props.secrets ?? []}
        isLoading={props.isLoading ?? false}
        isError={props.isError ?? false}
        workerPauseConfig={props.workerPauseConfig ?? null}
        canWriteProviderProfiles={props.canWriteProviderProfiles ?? true}
        canRunGithubTokenProbe={props.canRunGithubTokenProbe ?? true}
      />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('summarizeConfigurationHealth', () => {
  it('reports ready when an enabled default profile and healthy secrets are present', () => {
    const summary = summarizeConfigurationHealth({
      providerProfiles: [makeProfile({ is_default: true })],
      secrets: [{ slug: 'OPENAI_API_KEY', status: 'active' }],
      workerPauseConfigured: true,
      workersPaused: false,
    });

    expect(summary.level).toBe('ready');
    expect(summary.providerProfileCount).toBe(1);
    expect(summary.enabledProviderProfileCount).toBe(1);
    expect(summary.hasDefaultProfile).toBe(true);
    expect(summary.managedSecretCount).toBe(1);
    expect(summary.brokenSecretCount).toBe(0);
    expect(summary.warnings).toHaveLength(0);
  });

  it('blocks when there are no provider profiles', () => {
    const summary = summarizeConfigurationHealth({
      providerProfiles: [],
      secrets: [],
      workerPauseConfigured: true,
    });

    expect(summary.level).toBe('blocked');
    expect(summary.warnings.map((w) => w.id)).toContain('no-provider-profiles');
  });

  it('flags a missing default profile as a warning', () => {
    const summary = summarizeConfigurationHealth({
      providerProfiles: [makeProfile({ is_default: false })],
      secrets: [],
      workerPauseConfigured: true,
    });

    expect(summary.level).toBe('warning');
    expect(summary.hasDefaultProfile).toBe(false);
    expect(summary.warnings.map((w) => w.id)).toContain('no-default-profile');
  });

  it('blocks on broken secret references', () => {
    const summary = summarizeConfigurationHealth({
      providerProfiles: [makeProfile({ is_default: true })],
      secrets: [
        { slug: 'OPENAI_API_KEY', status: 'active' },
        { slug: 'GH_TOKEN', status: 'invalid' },
      ],
      workerPauseConfigured: true,
    });

    expect(summary.level).toBe('blocked');
    expect(summary.brokenSecretCount).toBe(1);
    expect(summary.warnings.map((w) => w.id)).toContain('broken-secret-refs');
  });
});

describe('ConfigurationHealthSummary', () => {
  it('renders the health summary with sample data: counts, default, and readiness badge', async () => {
    renderSummary({
      providerProfiles: [
        makeProfile({ profile_id: 'p1', is_default: true }),
        makeProfile({ profile_id: 'p2', enabled: false }),
      ],
      secrets: [
        { slug: 'OPENAI_API_KEY', status: 'active' },
        { slug: 'ANTHROPIC_API_KEY', status: 'active' },
      ],
      workerPauseConfig: null,
    });

    expect(
      screen.getByRole('region', { name: /Configuration health summary/i }),
    ).toBeTruthy();
    expect(screen.getByText('Provider profiles')).toBeTruthy();
    // 2 profiles, 1 enabled
    expect(screen.getByText('1 enabled')).toBeTruthy();
    expect(screen.getByText('Managed secrets')).toBeTruthy();
    expect(screen.getByText('All references healthy')).toBeTruthy();
    expect(screen.getByText('Configured')).toBeTruthy();
  });

  it('highlights missing/invalid defaults in the warning list', () => {
    renderSummary({
      providerProfiles: [makeProfile({ is_default: false })],
      secrets: [{ slug: 'GH_TOKEN', status: 'missing' }],
      workerPauseConfig: null,
    });

    const warnings = screen.getByRole('list', { name: /Configuration warnings/i });
    expect(warnings.textContent).toMatch(/No default provider profile is set/i);
    expect(warnings.textContent).toMatch(/broken state/i);
    expect(screen.getByText('Missing')).toBeTruthy();
    expect(screen.getByText('1 broken')).toBeTruthy();
  });

  it('shows the read-only state when provider profile writes are disabled', () => {
    renderSummary({
      providerProfiles: [makeProfile({ is_default: true })],
      canWriteProviderProfiles: false,
      canRunGithubTokenProbe: true,
      workerPauseConfig: null,
    });

    expect(screen.getByText(/Provider profile writes disabled/i)).toBeTruthy();
    expect(screen.getByText(/provider_profiles\.write/i)).toBeTruthy();
    expect(screen.getAllByText(/Read-only/i).length).toBeGreaterThanOrEqual(1);
  });

  it('shows why the permission-disabled GitHub token probe is unavailable', () => {
    renderSummary({
      providerProfiles: [makeProfile({ is_default: true })],
      canWriteProviderProfiles: true,
      canRunGithubTokenProbe: false,
      workerPauseConfig: null,
    });

    expect(screen.getByText(/GitHub token probe unavailable/i)).toBeTruthy();
    expect(screen.getByText(/settings\.effective\.read/i)).toBeTruthy();
  });

  it('reports the live worker pause state when worker controls are configured', async () => {
    const workerPauseConfig: WorkerPauseConfig = {
      get: '/api/v1/operations/workers',
      post: '/api/v1/operations/workers',
    };
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ system: { workersPaused: true, mode: 'drain' } }),
    });
    vi.stubGlobal('fetch', fetchMock);

    renderSummary({
      providerProfiles: [makeProfile({ is_default: true })],
      workerPauseConfig,
    });

    await waitFor(() => {
      expect(screen.getByText('Paused')).toBeTruthy();
    });
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/operations/workers',
      expect.objectContaining({ headers: expect.objectContaining({ Accept: 'application/json' }) }),
    );
  });

  it('renders a loading state without querying', () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    renderSummary({ isLoading: true });

    expect(screen.getByText(/Loading configuration health/i)).toBeTruthy();
  });
});
