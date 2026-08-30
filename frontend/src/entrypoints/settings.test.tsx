import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';

import type { BootPayload } from '../boot/parseBootPayload';
import { renderWithClient } from '../utils/test-utils';
import { SettingsPage } from './settings';
import { readDashboardPreferences, updateDashboardPreferences } from '../utils/dashboardPreferences';

describe('Settings Entrypoint', () => {
  const mockPayload: BootPayload = {
    page: 'settings',
    apiBase: '/api',
    initialData: {
      settingsPermissions: [
        'provider_profiles.read',
        'secrets.metadata.read',
        'settings.catalog.read',
        'operations.read',
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
    window.history.replaceState({}, 'Settings', '/settings/user-workspace');
    updateDashboardPreferences({
      workflowListDisplayMode: 'hidden',
      lastSelectedWorkflowId: 'workflow-one',
      recurringListDisplayMode: 'hidden',
      lastSelectedDefinitionId: 'schedule-one',
    });
    renderWithClient(<SettingsPage payload={mockPayload} />);

    fireEvent.click(screen.getByRole('button', { name: 'Reset dashboard preferences' }));

    expect(readDashboardPreferences().workflowListDisplayMode).toBe('sidebar');
    expect(readDashboardPreferences().lastSelectedWorkflowId).toBe('');
    expect(readDashboardPreferences().recurringListDisplayMode).toBe('table');
    expect(readDashboardPreferences().lastSelectedDefinitionId).toBe('');
    expect(screen.getByText('Dashboard preferences reset.')).toBeTruthy();
  });

  it('renders page-matched scoped placeholders for provider profiles and managed secrets', () => {
    renderWithClient(<SettingsPage payload={mockPayload} />);

    expect(screen.getByRole('heading', { level: 2, name: 'Providers & Secrets' })).toBeTruthy();
    expect(screen.getByText('Settings provider profiles loading placeholder').closest('[role="status"]')).toBeTruthy();
    expect(screen.getByText('Settings managed secrets loading placeholder').closest('[role="status"]')).toBeTruthy();
    expect(screen.getAllByTestId('loading-placeholder-table').length).toBeGreaterThanOrEqual(2);
  });

  it.each([
    ['/settings/providers-secrets', 'Providers & Secrets'],
    ['/settings/user-workspace', 'User / Workspace'],
    ['/settings/operations', 'Operations'],
  ])('MoonLadderStudios/MoonMind#3817 gives %s route-owned identity without local navigation', (path, label) => {
    window.history.replaceState({}, 'Settings', path);
    renderWithClient(<SettingsPage payload={mockPayload} />);

    expect(screen.getByRole('heading', { level: 2, name: label })).toBeTruthy();
    expect(document.title).toBe(`${label} | MoonMind`);
    expect(screen.queryByRole('radio')).toBeNull();
    expect(screen.queryByRole('tab')).toBeNull();
    expect(document.querySelector('.settings-page .segmented-control')).toBeNull();
  });

  it('resolves bare Settings to the first readable destination with replacement history', async () => {
    window.history.replaceState({}, 'Settings', '/settings');
    renderWithClient(
      <SettingsPage
        payload={{
          page: 'settings',
          apiBase: '/api',
          initialData: { settingsPermissions: ['operations.read'] },
        }}
      />,
    );

    expect(screen.getByRole('heading', { level: 2, name: 'Operations' })).toBeTruthy();
    await waitFor(() => expect(window.location.pathname).toBe('/settings/operations'));
  });

  it('normalizes an unknown Settings alias through the authorized default destination', async () => {
    window.history.replaceState({}, 'Settings', '/settings/provider-profiles');
    renderWithClient(
      <SettingsPage
        payload={{
          page: 'settings',
          apiBase: '/api',
          initialData: { settingsPermissions: ['settings.catalog.read'] },
        }}
      />,
    );

    expect(screen.getByRole('heading', { level: 2, name: 'User / Workspace' })).toBeTruthy();
    await waitFor(() => expect(window.location.pathname).toBe('/settings/user-workspace'));
  });

  it('shows direct unauthorized destinations without mounting protected page content', () => {
    window.history.replaceState({}, 'Settings', '/settings/providers-secrets');
    renderWithClient(
      <SettingsPage
        payload={{
          page: 'settings',
          apiBase: '/api',
          initialData: { settingsPermissions: ['operations.read'] },
        }}
      />,
    );

    expect(screen.getByRole('heading', { level: 2, name: 'Providers & Secrets' })).toBeTruthy();
    expect(screen.getByRole('status', { name: 'Configuration unavailable' })).toBeTruthy();
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(window.location.pathname).toBe('/settings/providers-secrets');
  });

  it('renders the unavailable default state when no Configuration page is readable', () => {
    window.history.replaceState({}, 'Settings', '/settings');
    renderWithClient(
      <SettingsPage
        payload={{
          page: 'settings',
          apiBase: '/api',
          initialData: { settingsPermissions: ['operations.invoke'] },
        }}
      />,
    );

    expect(screen.getByRole('heading', { level: 2, name: 'Configuration unavailable' })).toBeTruthy();
    expect(screen.getByRole('status', { name: 'Configuration unavailable' })).toBeTruthy();
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(window.location.pathname).toBe('/settings');
  });

  it('does not request provider or secret datasets from another Settings page', () => {
    window.history.replaceState({}, 'Settings', '/settings/operations');
    renderWithClient(
      <SettingsPage
        payload={{
          page: 'settings',
          apiBase: '/api',
          initialData: { settingsPermissions: ['operations.read'] },
        }}
      />,
    );

    const requestedUrls = fetchSpy.mock.calls.map(([input]) => String(input));
    expect(requestedUrls).not.toContain('/api/v1/provider-profiles');
    expect(requestedUrls).not.toContain('/api/v1/secrets');
    expect(screen.queryByLabelText('Configuration health summary')).toBeNull();
  });

  it('does not request Operations data from the Providers & Secrets summary', async () => {
    window.history.replaceState({}, 'Settings', '/settings/providers-secrets');
    renderWithClient(
      <SettingsPage
        payload={{
          page: 'settings',
          apiBase: '/api',
          initialData: {
            settingsPermissions: ['provider_profiles.read', 'secrets.metadata.read'],
            workerPause: {
              get: '/api/system/worker-pause',
              post: '/api/system/worker-pause',
              shardHealth: '/api/v1/operations/codex/shards',
            },
          },
        }}
      />,
    );

    await waitFor(() => {
      const requestedUrls = fetchSpy.mock.calls.map(([input]) => String(input));
      expect(requestedUrls).toContain('/api/v1/provider-profiles');
      expect(requestedUrls).toContain('/api/v1/secrets');
    });
    expect(fetchSpy.mock.calls.map(([input]) => String(input))).not.toContain('/api/system/worker-pause');
  });
});

describe('MoonLadderStudios/MoonMind#3788 Settings Provider Profile runtime filter', () => {
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

  const payloadWithRuntimes: BootPayload = {
    page: 'settings',
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
          supportedRuntimes: ['omnigent', 'codex_cli', 'claude_code', 'codex_cloud'],
        },
      },
    },
  } as unknown as BootPayload;

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
      'Provider Profile runtime filter',
    ) as HTMLSelectElement;
  }

  function selectRuntimeFilter(runtimeId: string): void {
    fireEvent.change(runtimeFilterControl(), { target: { value: runtimeId } });
  }

  // The default profile ID also appears in the global health summary, so row
  // assertions are scoped to the Provider Profiles table.
  function providerProfilesTable(): HTMLElement {
    const section = screen
      .getByRole('heading', { name: 'Provider Profiles' })
      .closest('section');
    expect(section).not.toBeNull();
    return within(section as HTMLElement).getByRole('table');
  }

  it('fetches the complete Provider Profile collection and defaults to All runtimes', async () => {
    renderWithClient(<SettingsPage payload={payloadWithRuntimes} />);

    await screen.findByRole('heading', { name: 'Provider Profiles' });

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
    renderWithClient(<SettingsPage payload={payloadWithRuntimes} />);

    await screen.findByRole('heading', { name: 'Provider Profiles' });

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
      { value: 'codex_cloud', label: 'Codex Cloud' },
    ]);
    expect(options.map((option) => option.value)).not.toContain('omnigent');
  });

  it('shows only matching rows while the global health summary keeps every loaded profile', async () => {
    renderWithClient(<SettingsPage payload={payloadWithRuntimes} />);

    await screen.findByRole('heading', { name: 'Provider Profiles' });

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
    renderWithClient(<SettingsPage payload={payloadWithRuntimes} />);

    await screen.findByRole('heading', { name: 'Provider Profiles' });

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
    renderWithClient(<SettingsPage payload={payloadWithRuntimes} />);

    await screen.findByRole('heading', { name: 'Provider Profiles' });
    expect(screen.queryByText('No provider profiles configured yet.')).toBeNull();

    selectRuntimeFilter('codex_cloud');

    expect(
      screen.getByText('No provider profiles are configured for Codex Cloud.'),
    ).toBeTruthy();
    expect(screen.queryByText('No provider profiles configured yet.')).toBeNull();
  });

  it('keeps runtime_id immutable while editing an existing profile', async () => {
    renderWithClient(<SettingsPage payload={payloadWithRuntimes} />);

    await screen.findByRole('heading', { name: 'Provider Profiles' });

    selectRuntimeFilter('claude_code');
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));

    const runtimeIdInput = screen.getByLabelText(/Runtime ID/) as HTMLInputElement;
    expect(runtimeIdInput.value).toBe('claude_code');
    expect(runtimeIdInput.disabled).toBe(true);
  });
});
