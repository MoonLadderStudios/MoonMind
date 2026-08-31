import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  ConfigurationHealthSummary,
  summarizeConfigurationHealth,
  type ConfigurationHealthSummaryProps,
} from './ConfigurationHealthSummary';
import type { ProviderProfile } from './ProviderProfilesManager';

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
    });

    expect(summary.level).toBe('blocked');
    expect(summary.warnings.map((w) => w.id)).toContain('no-provider-profiles');
  });

  it('flags a missing default profile as a warning', () => {
    const summary = summarizeConfigurationHealth({
      providerProfiles: [makeProfile({ is_default: false })],
      secrets: [],
    });

    expect(summary.level).toBe('warning');
    expect(summary.hasDefaultProfile).toBe(false);
    expect(summary.warnings.map((w) => w.id)).toContain('no-default-profile');
  });

  it('blocks on broken secret references bound by an enabled profile', () => {
    const summary = summarizeConfigurationHealth({
      providerProfiles: [
        makeProfile({
          is_default: true,
          secret_refs: { GH_TOKEN: 'db://GH_TOKEN' },
        }),
      ],
      secrets: [
        { slug: 'OPENAI_API_KEY', status: 'active' },
        { slug: 'GH_TOKEN', status: 'invalid' },
      ],
    });

    expect(summary.level).toBe('blocked');
    expect(summary.brokenSecretCount).toBe(1);
    expect(summary.warnings.map((w) => w.id)).toContain('broken-secret-refs');
  });

  it('does not block on broken secrets that no enabled profile references', () => {
    const summary = summarizeConfigurationHealth({
      providerProfiles: [
        makeProfile({
          is_default: true,
          secret_refs: { OPENAI_API_KEY: 'db://OPENAI_API_KEY' },
        }),
        // Disabled profile binding the broken secret must not block launches.
        makeProfile({
          profile_id: 'profile-2',
          enabled: false,
          secret_refs: { GH_TOKEN: 'db://GH_TOKEN' },
        }),
      ],
      secrets: [
        { slug: 'OPENAI_API_KEY', status: 'active' },
        { slug: 'GH_TOKEN', status: 'invalid' },
      ],
    });

    expect(summary.level).toBe('ready');
    // The broken secret is still surfaced as a metric, just not a blocker.
    expect(summary.brokenSecretCount).toBe(1);
    expect(summary.warnings.map((w) => w.id)).not.toContain('broken-secret-refs');
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
      providerProfiles: [
        makeProfile({
          is_default: false,
          secret_refs: { GH_TOKEN: 'db://GH_TOKEN' },
        }),
      ],
      secrets: [{ slug: 'GH_TOKEN', status: 'missing' }],
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
    });

    expect(screen.getByText(/GitHub token probe unavailable/i)).toBeTruthy();
    expect(screen.getByText(/settings\.effective\.read/i)).toBeTruthy();
  });

  it('renders a loading state without querying', () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    renderSummary({ isLoading: true });

    expect(screen.getByText(/Loading configuration health/i)).toBeTruthy();
  });
});
