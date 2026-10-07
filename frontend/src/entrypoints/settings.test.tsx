import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { fireEvent, screen, within } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';

import type { BootPayload } from '../boot/parseBootPayload';
import { renderWithClient } from '../utils/test-utils';
import { ProvidersSecretsSettingsPage, InstanceSettingsPage } from './settings';
import { readDashboardPreferences, updateDashboardPreferences } from '../utils/dashboardPreferences';

const renderProvidersPage = (payload: BootPayload) => renderWithClient(
  <BrowserRouter><ProvidersSecretsSettingsPage payload={payload} /></BrowserRouter>,
);

const renderInstancePage = (payload: BootPayload) => renderWithClient(
  <BrowserRouter><InstanceSettingsPage payload={payload} /></BrowserRouter>,
);

describe('Settings Entrypoint', () => {
  const mockPayload: BootPayload = {
    page: 'settings-providers-secrets',
    apiBase: '/api',
    initialData: {
      settingsPermissions: [
        'provider_profiles.read',
        'secrets.metadata.read',
        'settings.catalog.read',
      ],
    },
  };

  let fetchSpy: MockInstance;

  beforeEach(() => {
    window.history.pushState({}, 'Settings', '/settings/providers-secrets');
    fetchSpy = vi.spyOn(window, 'fetch').mockReturnValue(new Promise(() => {}) as Promise<Response>);
  });

  afterEach(() => {
    fetchSpy.mockRestore();
    window.localStorage.clear();
  });

  it('MM-1185 resets collection layouts and remembered identities from Settings', () => {
    window.history.replaceState({}, 'Settings', '/settings/instance');
    updateDashboardPreferences({
      workflowListDisplayMode: 'hidden',
      lastSelectedWorkflowId: 'workflow-one',
      recurringListDisplayMode: 'hidden',
      lastSelectedDefinitionId: 'schedule-one',
    });
    renderInstancePage(mockPayload);

    fireEvent.click(screen.getByRole('button', { name: 'Reset dashboard preferences' }));

    expect(readDashboardPreferences().workflowListDisplayMode).toBe('sidebar');
    expect(readDashboardPreferences().lastSelectedWorkflowId).toBe('');
    expect(readDashboardPreferences().recurringListDisplayMode).toBe('table');
    expect(readDashboardPreferences().lastSelectedDefinitionId).toBe('');
    expect(screen.getByText('Dashboard preferences reset.')).toBeTruthy();
  });

  it('renders page-matched scoped placeholders for provider profiles and managed secrets', () => {
    renderProvidersPage(mockPayload);

    expect(screen.getByRole('heading', { name: 'Providers & Secrets' })).toBeTruthy();
    expect(screen.getByText('Settings provider profiles loading placeholder').closest('[role="status"]')).toBeTruthy();
    expect(screen.getByText('Settings managed secrets loading placeholder').closest('[role="status"]')).toBeTruthy();
    expect(screen.getAllByTestId('loading-placeholder-table').length).toBeGreaterThanOrEqual(2);
  });
});

describe('MoonLadderStudios/MoonMind#4019 Source Control on Providers & Secrets', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('renders Source Control connections with Test connection bound to the selected connection', async () => {
    window.history.pushState({}, 'Settings', '/settings/providers-secrets');
    const fetchSpy = vi.spyOn(window, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const respond = (body: unknown) =>
        ({ ok: true, status: 200, json: async () => body }) as Response;
      if (url === '/api/v1/repository-connections') {
        return respond({
          items: [
            {
              id: 'personal-github',
              displayName: 'Personal GitHub',
              endpoint: 'https://github.com',
              credentialKind: 'personal_access_token',
              lifecycle: 'active',
              policyRevision: 1,
              credentialRevision: 1,
              allowedOperations: ['read'],
              assignments: [
                { repository: 'acme/widgets', providerRepoId: '7', operations: ['read'], revision: 1, verified: true },
              ],
            },
          ],
        });
      }
      if (url === '/api/v1/settings/github/token-probe') {
        return respond({ observations: { read: 'verified', write: 'untested' }, repositoryAccessible: true });
      }
      void init;
      return new Promise(() => {}) as Promise<Response>;
    });
    renderProvidersPage({
      page: 'settings-providers-secrets',
      apiBase: '/api',
      initialData: { settingsPermissions: ['settings.effective.read'] },
    });

    const section = await screen.findByRole('region', { name: 'Source Control' });
    expect(within(section).getByRole('button', { name: 'Add token connection' })).toBeTruthy();
    expect(within(section).getByRole('button', { name: 'Connect GitHub App' })).toBeTruthy();
    await within(section).findByText('acme/widgets');
    expect(within(section).queryByText(/SecretRef|GITHUB_TOKEN/)).toBeNull();

    fireEvent.click(within(section).getByRole('button', { name: 'Test connection' }));
    await within(section).findByText('Read access verified');
    const probeCalls = fetchSpy.mock.calls.filter(([url]) => String(url) === '/api/v1/settings/github/token-probe');
    expect(probeCalls).toHaveLength(1);
    for (const [, init] of probeCalls) {
      expect(JSON.parse(String((init as RequestInit).body)).connectionId).toBe('personal-github');
    }
  });
});

describe('MoonLadderStudios/MoonMind#3788 Settings Profile runtime filter', () => {
  const codexProfile = {
    profile_id: 'codex_minimax_team',
    runtime_id: 'codex_cli',
    provider_id: 'minimax',
    credential_source: 'secret_ref',
    runtime_materialization_mode: 'api_key_env',
    secret_refs: { MINIMAX_API_KEY: 'db://MINIMAX_API_KEY' },
    max_parallel_runs: 1,
    cooldown_after_429_seconds: 300,
    rate_limit_policy: 'backoff',
    enabled: true,
    is_default: true,
  };
  const claudeProfile = {
    ...codexProfile,
    profile_id: 'claude_minimax_team',
    runtime_id: 'claude_code',
    is_default: false,
  };

  const payloadWithRuntimes = {
    page: 'settings-providers-secrets',
    apiBase: '/api',
    initialData: {
      settingsPermissions: [
        'provider_profiles.read',
        'provider_profiles.write',
        'secrets.metadata.read',
      ],
      runtimeConfig: {
        system: {
          // `omnigent` is a facade, never a Provider Profile owner, so it must
          // not become a runtime filter option.
          supportedRuntimes: ['omnigent', 'codex_cli', 'claude_code', 'jules'],
        },
      },
    },
  } satisfies BootPayload;

  let fetchSpy: MockInstance;

  beforeEach(() => {
    window.history.pushState({}, 'Settings', '/settings/providers-secrets');
    fetchSpy = vi.spyOn(window, 'fetch').mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith('/api/v1/provider-profiles')) {
        return Promise.resolve({
          ok: true,
          json: async () => [codexProfile, claudeProfile],
        } as Response);
      }
      return Promise.resolve({
        ok: true,
        json: async () => ({ items: [] }),
      } as Response);
    });
  });

  afterEach(() => {
    fetchSpy.mockRestore();
  });

  function runtimeFilterControl(): HTMLSelectElement {
    return screen.getByLabelText(
      'Profile runtime filter',
    ) as HTMLSelectElement;
  }

  function selectRuntimeFilter(runtimeId: string): void {
    fireEvent.change(runtimeFilterControl(), { target: { value: runtimeId } });
  }

  // The default profile ID also appears in the global health summary, so row
  // assertions are scoped to the Provider Profiles table.
  function providerProfilesTable(): HTMLElement {
    const section = screen
      .getByRole('heading', { name: 'Profiles' })
      .closest('section');
    expect(section).not.toBeNull();
    return within(section as HTMLElement).getByRole('table');
  }

  it('fetches the complete Provider Profile collection and defaults to All runtimes', async () => {
    renderProvidersPage(payloadWithRuntimes);

    await screen.findByRole('heading', { name: 'Profiles' });

    // Settings is the administrative view, so it never scopes the request by
    // runtime the way an execution surface does.
    const profileRequests = fetchSpy.mock.calls
      .map(([requestUrl]) => String(requestUrl))
      .filter((requestUrl) => requestUrl.startsWith('/api/v1/provider-profiles'));
    expect(profileRequests).toEqual(['/api/v1/provider-profiles']);

    expect(runtimeFilterControl().value).toBe('all');
    const table = providerProfilesTable();
    expect(within(table).getByText('codex_minimax_team')).toBeTruthy();
    expect(within(table).getByText('claude_minimax_team')).toBeTruthy();
  });

  it('offers one option per available runtime using canonical IDs and formatted labels', async () => {
    renderProvidersPage(payloadWithRuntimes);

    await screen.findByRole('heading', { name: 'Profiles' });

    const control = runtimeFilterControl();
    const options = within(control)
      .getAllByRole('option')
      .map((option) => ({
        value: (option as HTMLOptionElement).value,
        label: option.textContent,
      }));

    expect(options).toEqual([
      { value: 'all', label: 'All runtimes' },
      { value: 'codex_cli', label: 'Codex CLI' },
      { value: 'claude_code', label: 'Claude Code' },
      { value: 'jules', label: 'Jules' },
    ]);
    expect(options.map((option) => option.value)).not.toContain('omnigent');
  });

  it('shows only matching rows while the global health summary keeps every loaded profile', async () => {
    renderProvidersPage(payloadWithRuntimes);

    await screen.findByRole('heading', { name: 'Profiles' });

    const healthSummary = screen.getByLabelText('Configuration health summary');
    expect(within(healthSummary).getByText('2')).toBeTruthy();

    selectRuntimeFilter('codex_cli');

    expect(within(providerProfilesTable()).getByText('codex_minimax_team')).toBeTruthy();
    expect(
      within(providerProfilesTable()).queryByText('claude_minimax_team'),
    ).toBeNull();
    // Filtering the table must not narrow global configuration health.
    expect(within(healthSummary).getByText('2')).toBeTruthy();
    expect(within(healthSummary).getByText('2 enabled')).toBeTruthy();

    selectRuntimeFilter('claude_code');

    expect(within(providerProfilesTable()).getByText('claude_minimax_team')).toBeTruthy();
    expect(
      within(providerProfilesTable()).queryByText('codex_minimax_team'),
    ).toBeNull();
    expect(within(healthSummary).getByText('2')).toBeTruthy();
  });

  it('prefills the create form runtime from the active filter without touching an existing runtime', async () => {
    renderProvidersPage(payloadWithRuntimes);

    await screen.findByRole('heading', { name: 'Profiles' });

    const runtimeIdInput = () => screen.getByLabelText(/Runtime ID/) as HTMLInputElement;
    expect(runtimeIdInput().value).toBe('');

    selectRuntimeFilter('claude_code');
    expect(runtimeIdInput().value).toBe('claude_code');

    selectRuntimeFilter('codex_cli');
    expect(runtimeIdInput().value).toBe('codex_cli');

    // An explicitly authored runtime survives a later filter change.
    fireEvent.change(runtimeIdInput(), { target: { value: 'opencode' } });
    selectRuntimeFilter('claude_code');
    expect(runtimeIdInput().value).toBe('opencode');
  });

  it('names the active runtime in the empty state instead of the global message', async () => {
    renderProvidersPage(payloadWithRuntimes);

    await screen.findByRole('heading', { name: 'Profiles' });
    expect(screen.queryByText('No provider profiles configured yet.')).toBeNull();

    selectRuntimeFilter('jules');

    expect(
      screen.getByText('No provider profiles are configured for Jules.'),
    ).toBeTruthy();
    expect(screen.queryByText('No provider profiles configured yet.')).toBeNull();
  });

  it('keeps runtime_id immutable while editing an existing profile', async () => {
    renderProvidersPage(payloadWithRuntimes);

    await screen.findByRole('heading', { name: 'Profiles' });

    selectRuntimeFilter('claude_code');
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));

    const runtimeIdInput = screen.getByLabelText(/Runtime ID/) as HTMLInputElement;
    expect(runtimeIdInput.value).toBe('claude_code');
    expect(runtimeIdInput.disabled).toBe(true);
  });
  it('does not offer a removed runtime from a stale boot catalog (#4644)', async () => {
    const stale = structuredClone(payloadWithRuntimes);
    stale.initialData.runtimeConfig.system.supportedRuntimes.push('codex_cloud');
    renderProvidersPage(stale);
    await screen.findByRole('heading', { name: 'Profiles' });
    expect(Array.from(runtimeFilterControl().options).map((option) => option.value)).not.toContain('codex_cloud');
  });

});
