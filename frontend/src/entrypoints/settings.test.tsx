import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';

import type { BootPayload } from '../boot/parseBootPayload';
import { renderWithClient } from '../utils/test-utils';
import { ProvidersSecretsSettingsPage, InstanceSettingsPage } from './settings';
import { readDashboardPreferences, updateDashboardPreferences } from '../utils/dashboardPreferences';
import { profileAction } from '../components/settings/profileActionsTestHelpers';

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
      initialData: { },
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

describe('Providers & Secrets defaults for new work', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('shows the compact Harness and Provider profile controls with precedence hint', async () => {
    window.history.pushState({}, 'Settings', '/settings/providers-secrets');
    const descriptor = (key: string, title: string, extra: Record<string, unknown>) => ({
      key, title, category: 'Workflow', section: 'user-workspace', description: null,
      effective_value: null, source: 'config_or_default', source_explanation: 'Resolved from application settings.',
      apply_mode: 'next_workflow', activation_state: 'active', active: true, sensitive: false, read_only: false,
      requires_reload: false, requires_worker_restart: false, requires_process_restart: false, applies_to: [],
      order: 10, audit: { store_old_value: true, store_new_value: true, redact: false }, value_version: 1,
      diagnostics: [], ...extra,
    });
    vi.spyOn(window, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      const respond = (body: unknown) => ({ ok: true, status: 200, json: async () => body }) as Response;
      if (url.startsWith('/api/v1/settings/catalog')) {
        return respond({
          section: 'user-workspace',
          categories: {
            Workflow: [
              descriptor('workflow.default_runtime', 'Default Harness', {
                type: 'enum', ui: 'select', effective_value: 'codex_cli',
                options: [
                  { value: 'omnigent', label: 'OpenCode' },
                  { value: 'codex_cli', label: 'Codex' },
                  { value: 'claude_code', label: 'Claude Code' },
                ],
              }),
              descriptor('workflow.default_publish_mode', 'Default Publish Mode', {
                type: 'enum', ui: 'select', effective_value: 'pr', options: [{ value: 'pr', label: 'Pull Request' }],
              }),
              descriptor('workflow.default_provider_profile_ref', 'Default Provider Profile', {
                type: 'string', ui: 'provider_profile_picker', order: 70,
              }),
            ],
          },
        });
      }
      if (url.startsWith('/api/v1/provider-profiles')) return respond([]);
      return respond({ items: [] });
    });
    renderProvidersPage({ page: 'settings-providers-secrets', apiBase: '/api', initialData: {} });

    const card = await screen.findByRole('region', { name: 'Defaults for new work' });
    const harness = (await within(card).findByLabelText('Harness')) as HTMLSelectElement;
    expect(harness.value).toBe('codex_cli');
    expect(harness.selectedOptions[0]?.textContent).toBe('Codex');
    expect(within(card).getByLabelText('Provider profile')).toBeTruthy();
    expect(within(card).queryByLabelText('Default Publish Mode')).toBeNull();
    expect(card.textContent).toContain("Used when a launch doesn't choose a harness or profile.");
    expect(card.textContent).toContain('Takes priority over the harness when set.');
    expect(card.textContent).not.toMatch(/Instance override|Pending|Affects|Details|workflow\.default/);
  });
});

describe('Providers & Secrets action and health integration', () => {
  afterEach(() => vi.restoreAllMocks());
  it('refreshes a disabled bound secret across alert, Status and Usage, focuses its row and toasts re-enabling', async () => {
    window.history.pushState({}, 'Settings', '/settings/providers-secrets');
    let status = 'active';
    vi.spyOn(window, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const respond = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });
      if (url === '/api/v1/provider-profiles') return respond([{
        profile_id: 'bound-profile', runtime_id: 'claude_code', provider_id: 'anthropic', enabled: true,
        secret_refs: { key: 'db://api-key' }, max_parallel_runs: 1,
      }]);
      if (url === '/api/v1/secrets/api-key/status' && init?.method === 'PUT') {
        status = JSON.parse(String(init.body)).status; return respond({});
      }
      if (url === '/api/v1/secrets') return respond({ items: [{ slug: 'api-key', status, details: {}, createdAt: '2026-11-06' }] });
      if (url.endsWith('/usage')) return respond({ secretRef: 'db://api-key', usages: [
        { consumerType: 'profile', objectName: 'bound-profile usage', reference: 'db://api-key' },
      ] });
      return respond({ items: [] });
    });
    const { queryClient } = renderProvidersPage({ page: 'settings-providers-secrets', apiBase: '/api', initialData: {} });
    await screen.findByText('Active');
    status = 'disabled';
    await act(async () => { await queryClient.invalidateQueries({ queryKey: ['secrets'] }); });
    await screen.findByText('Secret api-key is disabled. Launches will fail.');
    await screen.findByText('bound-profile usage');
    const alert = screen.getByRole('alert');
    expect(alert.textContent).toContain("bound-profile can't launch because its secret api-key is disabled.");
    fireEvent.click(within(alert).getByRole('link', { name: 'Show secret' }));
    expect(document.activeElement?.id).toBe('managed-secret-api-key');
    fireEvent.click(screen.getByRole('button', { name: 'Re-enable api-key' }));
    const result = await screen.findByText('Secret re-enabled.');
    expect(result.closest('[aria-label="Dashboard notifications"]')).toBeTruthy();
  });
});

describe('MoonLadderStudios/MoonMind#3788 Settings Provider Profile Harness filter', () => {
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
      runtimeConfig: {
        system: {
          // `omnigent` is a facade, never a Provider Profile owner, so it must
          // not become a runtime filter option.
          supportedRuntimes: ['omnigent', 'codex_cli', 'claude_code', 'openclaw'],
        },
      },
    },
  } satisfies BootPayload;

  let fetchSpy: MockInstance;

  beforeEach(() => {
    window.history.pushState({}, 'Settings', '/settings/providers-secrets');
    fetchSpy = vi.spyOn(window, 'fetch').mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/v1/provider-profiles/creation-choices') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            version: 'provider-profile-creation-v1',
            profile_id_max_length: 128,
            harnesses: [
              { runtime_id: 'codex_cli', label: 'Codex CLI', providers: [{ provider_id: 'openai', label: 'OpenAI' }], custom_provider_allowed: false },
              { runtime_id: 'claude_code', label: 'Claude Code', providers: [{ provider_id: 'anthropic', label: 'Anthropic' }], custom_provider_allowed: false },
              { runtime_id: 'opencode', label: 'OpenCode', providers: [{ provider_id: 'opencode', label: 'OpenCode' }], custom_provider_allowed: true },
            ],
          }),
        } as Response);
      }
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
      'Harness filter',
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

  it('fetches the complete Provider Profile collection and defaults to All Harnesses', async () => {
    renderProvidersPage(payloadWithRuntimes);

    await screen.findByRole('heading', { name: 'Profiles' });

    // Settings is the administrative view, so it never scopes the request by
    // runtime the way an execution surface does.
    const profileRequests = fetchSpy.mock.calls
      .map(([requestUrl]) => String(requestUrl))
      .filter(
        (requestUrl) =>
          requestUrl.startsWith('/api/v1/provider-profiles') &&
          requestUrl !== '/api/v1/provider-profiles/creation-choices',
      );
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
      { value: 'all', label: 'All Harnesses' },
      { value: 'codex_cli', label: 'Codex CLI' },
      { value: 'claude_code', label: 'Claude Code' },
      { value: 'openclaw', label: 'OpenClaw' },
    ]);
    expect(options.map((option) => option.value)).not.toContain('omnigent');
  });

  it('keeps hidden profile problems in the alert and clears the filter before focusing the row', async () => {
    const previous = fetchSpy.getMockImplementation()!;
    fetchSpy.mockImplementation((input, init) => String(input) === '/api/v1/provider-profiles'
      ? Promise.resolve({ ok: true, json: async () => [codexProfile, {
        ...claudeProfile, readiness: { status: 'blocked', summary: 'Reconnect credentials' },
      }] } as Response) : previous(input, init));
    renderProvidersPage(payloadWithRuntimes);
    await screen.findByRole('heading', { name: 'Profiles' });
    selectRuntimeFilter('codex_cli');
    expect(within(providerProfilesTable()).queryByText('claude_minimax_team')).toBeNull();
    const alert = screen.getByRole('alert');
    expect(alert.textContent).toContain("claude_minimax_team can't launch");
    fireEvent.click(within(alert).getByRole('link', { name: 'Show profile' }));
    await waitFor(() => expect(runtimeFilterControl().value).toBe('all'));
    await waitFor(() => expect(document.activeElement?.id).toBe('provider-profile-claude_minimax_team'));
  });

  it('opens on profiles when healthy and orders defaults, secrets and Source Control after it', async () => {
    renderProvidersPage(payloadWithRuntimes);
    const profiles = (await screen.findByRole('heading', { name: 'Profiles' })).closest('section')!;
    const defaults = screen.getByRole('region', { name: 'Defaults for new work' });
    const secrets = screen.getByRole('heading', { name: 'Stored secrets' }).closest('section')!;
    const source = screen.getByRole('region', { name: 'Source Control' });
    expect(screen.queryByLabelText('Configuration health summary')).toBeNull();
    expect(screen.queryByRole('alert')).toBeNull();
    for (const [before, after] of [[profiles, defaults], [defaults, secrets], [secrets, source]]) {
      expect(before!.compareDocumentPosition(after!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }
  });

  it('prefills the create form Harness from the active filter without touching an existing runtime', async () => {
    renderProvidersPage(payloadWithRuntimes);

    await screen.findByRole('heading', { name: 'Profiles' });
    fireEvent.click(screen.getByRole('button', { name: 'Add profile' }));

    const harnessSelect = () => screen.getByLabelText('Harness') as HTMLSelectElement;
    await waitFor(() => expect(harnessSelect().disabled).toBe(false));
    expect(harnessSelect().value).toBe('');

    selectRuntimeFilter('claude_code');
    expect(harnessSelect().value).toBe('claude_code');

    selectRuntimeFilter('codex_cli');
    expect(harnessSelect().value).toBe('codex_cli');

    // A filter Harness that is not a permitted creation choice seeds nothing.
    selectRuntimeFilter('openclaw');
    expect(harnessSelect().value).toBe('');

    // An explicitly chosen Harness survives a later filter change.
    fireEvent.change(harnessSelect(), { target: { value: 'opencode' } });
    selectRuntimeFilter('claude_code');
    expect(harnessSelect().value).toBe('opencode');
  });

  it('names the active runtime in the empty state instead of the global message', async () => {
    renderProvidersPage(payloadWithRuntimes);

    await screen.findByRole('heading', { name: 'Profiles' });
    expect(screen.queryByText('No provider profiles configured yet.')).toBeNull();

    selectRuntimeFilter('openclaw');

    expect(
      screen.getByText('No Provider Profiles are configured for OpenClaw.'),
    ).toBeTruthy();
    expect(screen.queryByText('No provider profiles configured yet.')).toBeNull();
  });

  it('keeps runtime_id immutable while editing an existing profile', async () => {
    renderProvidersPage(payloadWithRuntimes);

    await screen.findByRole('heading', { name: 'Profiles' });

    selectRuntimeFilter('claude_code');
    fireEvent.click(profileAction('Edit'));

    const harnessSelect = screen.getByLabelText('Harness') as HTMLSelectElement;
    expect(harnessSelect.value).toBe('claude_code');
    expect(harnessSelect.disabled).toBe(true);
  });
  it('does not offer a removed runtime from a stale boot catalog', async () => {
    const stale = structuredClone(payloadWithRuntimes);
    stale.initialData.runtimeConfig.system.supportedRuntimes.push('codex_cloud', 'jules', 'jules_api');
    renderProvidersPage(stale);
    await screen.findByRole('heading', { name: 'Profiles' });
    const offered = Array.from(runtimeFilterControl().options).map((option) => option.value);
    for (const removed of ['codex_cloud', 'jules', 'jules_api']) expect(offered).not.toContain(removed);
  });

});

describe('Providers & Secrets readiness after secret changes', () => {
  afterEach(() => vi.restoreAllMocks());

  it.each([
    { operation: 're-enable', pendingInventory: false },
    { operation: 'create', pendingInventory: false },
    { operation: 're-enable', pendingInventory: true },
    { operation: 'create', pendingInventory: true },
    { operation: 're-enable', pendingInventory: false, disabledAgain: true },
  ])('refreshes $operation readiness with pending inventory=$pendingInventory and later disable=$disabledAgain', async ({ operation, pendingInventory, disabledAgain }) => {
    window.history.pushState({}, 'Settings', '/settings/providers-secrets');
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    let status: string | null = operation === 'create' ? null : 'disabled';
    let finishInitialProfileRead: (() => void) | undefined;
    let failMutation = true;
    let profileReads = 0;
    const readiness = (blocked: boolean) => ({
      status: blocked ? 'blocked' : 'ready', launch_ready: !blocked,
      summary: blocked ? 'Provider profile has launch blockers.' : 'Provider profile is ready for launch.',
      checks: [{ id: 'secret_refs', label: 'SecretRef bindings', status: blocked ? 'error' : 'pass', message: blocked ? 'Secret unavailable.' : 'Valid.' }],
    });
    vi.spyOn(window, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const respond = (body: unknown, code = 200) => new Response(JSON.stringify(body), { status: code });
      if (url === '/api/v1/provider-profiles') {
        profileReads += 1;
        const profiles = [{
          profile_id: 'bound-profile', runtime_id: 'claude_code', provider_id: 'anthropic', enabled: true,
          secret_refs: { ANTHROPIC_API_KEY: 'db://api-key' }, max_parallel_runs: 1,
          credential_source: 'secret_ref', runtime_materialization_mode: 'api_key_env', readiness: readiness(status !== 'active'),
        }, {
          profile_id: 'unrelated-profile', runtime_id: 'codex_cli', provider_id: 'openai', enabled: true,
          secret_refs: {}, max_parallel_runs: 1, readiness: { ...readiness(true), summary: 'Authentication expired.' },
        }];
        if (pendingInventory && profileReads === 1) return new Promise<Response>((resolve) => {
          finishInitialProfileRead = () => resolve(respond(profiles));
        });
        return respond(profiles);
      }
      if (url.startsWith('/api/v1/settings/catalog')) return respond({ categories: { Workflow: [
        { key: 'workflow.default_runtime', title: 'Harness', type: 'enum', ui: 'select', effective_value: 'claude_code', options: [{ value: 'claude_code', label: 'Claude Code' }], value_version: 1 },
        { key: 'workflow.default_provider_profile_ref', title: 'Provider profile', type: 'string', ui: 'provider_profile_picker', effective_value: 'bound-profile', value_version: 1 },
      ] } });
      if (url.startsWith('/api/v1/secrets') && init?.method) {
        if (failMutation) return respond({ detail: 'Secret change failed.' }, 500);
        // A later disable can win after the repair commits but before inventory reloads.
        if (init.method === 'POST' || url.endsWith('/status')) status = disabledAgain ? 'disabled' : 'active';
        return respond({});
      }
      if (url === '/api/v1/secrets') return respond({ items: status ? [{ slug: 'api-key', status, details: {}, createdAt: '2026-11-06' }] : [] });
      if (url.endsWith('/usage')) return respond({ secretRef: 'db://api-key', usages: [] });
      return respond({ items: [] });
    });
    const { queryClient } = renderProvidersPage({ page: 'settings-providers-secrets', apiBase: '/api', initialData: {} });
    await screen.findByRole('button', { name: 'Add secret' });
    if (!pendingInventory) {
      await screen.findByText("unrelated-profile can't launch: Authentication expired.");
      await waitFor(() => expect(queryClient.isFetching({ queryKey: ['provider-profiles'] })).toBe(0));
    }
    const initialProfileReads = profileReads;
    const defaultProblem = () => screen.queryByText(/New work defaults to bound-profile, which is not launch-ready/);
    const profileProblem = () => screen.queryByText(/bound-profile can't launch: Provider profile has launch blockers/);
    const assertBlocked = (blocked: boolean) => {
      expect(Boolean(defaultProblem())).toBe(blocked);
      expect(Boolean(profileProblem())).toBe(blocked);
      expect(within(document.getElementById('provider-profile-bound-profile')!).getByText(`Readiness: ${blocked ? 'Blocked' : 'Ready'}`)).toBeTruthy();
      expect(screen.getByText("unrelated-profile can't launch: Authentication expired.")).toBeTruthy();
    };
    if (!pendingInventory) assertBlocked(true);
    if (operation === 'create') {
      fireEvent.click(screen.getByRole('button', { name: 'Add secret' }));
      fireEvent.change(screen.getByLabelText('Secret slug'), { target: { value: 'api-key' } });
      fireEvent.change(screen.getByLabelText('Secure value'), { target: { value: 'replacement-value' } });
    }
    const submit = () => fireEvent.click(screen.getByRole('button', {
      name: operation === 'create' ? 'Encrypt and store secret' : 'Re-enable api-key',
    }));
    submit();
    await screen.findByText('Secret change failed.');
    if (!pendingInventory) assertBlocked(true);
    expect(profileReads).toBe(initialProfileReads);
    failMutation = false;
    submit();
    await screen.findByText(operation === 'create' ? 'Secret saved successfully.' : 'Secret re-enabled.');
    await waitFor(() => assertBlocked(Boolean(disabledAgain)));
    expect(profileReads).toBeGreaterThan(initialProfileReads);
    expect(document.getElementById('managed-secret-api-key')?.textContent).toContain(disabledAgain ? 'Disabled' : 'Active');
    if (finishInitialProfileRead) {
      await act(async () => { finishInitialProfileRead!(); });
      assertBlocked(Boolean(disabledAgain));
    }
    if (operation === 're-enable' && !pendingInventory && !disabledAgain) {
      // An external disable survives editing the value: refresh must reveal the
      // new blocker rather than optimistically treating every success as ready.
      status = 'disabled';
      fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
      fireEvent.change(screen.getByLabelText('Secure value'), { target: { value: 'updated-value' } });
      fireEvent.click(screen.getByRole('button', { name: 'Save new value' }));
      await screen.findByText('Secret updated successfully.');
      await waitFor(() => assertBlocked(true));
      submit();
      await waitFor(() => assertBlocked(false));
    }
  });
});
