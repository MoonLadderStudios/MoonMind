/**
 * MoonLadderStudios/MoonMind#3822 — Settings redesign conformance suite.
 *
 * Machine-verifiable acceptance coverage for the complete Settings redesign
 * (parent #3815, design docs/UI/SettingsPage.md sections 12/14.4/15 and
 * docs/UI/ProviderProfileCreation.md sections 9-13).
 *
 * The suite fails when implementation drifts back to one tabbed Settings page,
 * browser-owned Provider Profile defaults, raw credential plumbing, or hidden
 * unsafe launch policy. It proves the pieces agree at their boundaries; deep
 * per-issue behavior remains in the per-issue suites (#3816-#3821, #1205,
 * #3348) referenced inline.
 */
import { readFileSync } from 'node:fs';
import { QueryClient } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { BrowserRouter } from 'react-router-dom';
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
  type MockInstance,
} from 'vitest';

import type { BootPayload } from '../boot/parseBootPayload';
import { DashboardSystemMenu } from '../components/DashboardSystemMenu';
import {
  ProviderProfilesManager,
  type ProviderProfile,
} from '../components/settings/ProviderProfilesManager';
import {
  DASHBOARD_DESTINATION_GROUPS,
  DASHBOARD_DESTINATIONS,
  destinationForPath,
  destinationState,
  exposedSystemDestinations,
  filterSettingsQueryForTarget,
  legacySettingsRedirect,
  resolveAuthorizedLegacySettingsTarget,
  resolveDashboardRoute,
} from '../lib/dashboardRoutes';
import { fireEvent, renderWithClient, screen, waitFor } from '../utils/test-utils';
import {
  buildProviderProfileTierPayload,
  normalizeProviderProfileTiers,
  runtimeDefaultTierDraft,
} from '../utils/providerProfileTiers';
import {
  OperationsSettingsPage,
  ProvidersSecretsSettingsPage,
  SettingsEntryPage,
  UserWorkspaceSettingsPage,
} from './settings';

function readRepoFile(...segments: string[]): string {
  return readFileSync(`${process.cwd()}/${segments.join('/')}`, 'utf8');
}

function ok(body: unknown): Response {
  return { ok: true, status: 200, statusText: 'OK', json: async () => body } as Response;
}

function fail(status = 503): Response {
  return { ok: false, status, statusText: 'Unavailable', json: async () => ({}) } as Response;
}

const SETTINGS_PAGES = [
  {
    key: 'settings-providers-secrets',
    path: '/settings/providers-secrets',
    title: 'Providers & Secrets',
    page: 'settings-providers-secrets',
    Page: ProvidersSecretsSettingsPage,
  },
  {
    key: 'settings-user-workspace',
    path: '/settings/user-workspace',
    title: 'User / Workspace',
    page: 'settings-user-workspace',
    Page: UserWorkspaceSettingsPage,
  },
  {
    key: 'settings-operations',
    path: '/settings/operations',
    title: 'Operations',
    page: 'settings-operations',
    Page: OperationsSettingsPage,
  },
] as const;

const userWorkspaceCatalog = (scope: string) => ({
  section: 'user-workspace',
  scope,
  categories: {
    Workflow: [
      {
        key: 'workflow.default_publish_mode',
        title: 'Default Publish Mode',
        description: 'Fallback publish mode.',
        category: 'Workflow',
        section: 'user-workspace',
        type: 'enum',
        ui: 'select',
        scopes: ['workspace', 'user'],
        default_value: 'pr',
        effective_value: 'pr',
        override_value: null,
        source: 'default',
        source_explanation: 'Resolved from default.',
        apply_mode: 'next_task',
        activation_state: 'active',
        active: true,
        pending_value: null,
        affected_process_or_worker: 'publishing',
        completion_guidance: null,
        options: [
          { value: 'pr', label: 'Pull request' },
          { value: 'branch', label: 'Branch' },
        ],
        constraints: null,
        sensitive: false,
        secret_role: null,
        read_only: false,
        read_only_reason: null,
        requires_reload: false,
        requires_worker_restart: false,
        requires_process_restart: false,
        applies_to: ['publishing'],
        depends_on: [],
        order: 10,
        audit: { store_old_value: true, store_new_value: true, redact: false },
        value_version: 1,
        diagnostics: [],
      },
    ],
  },
});

function mockSettingsFetch(requestedUrls: string[]) {
  return vi.spyOn(window, 'fetch').mockImplementation((input) => {
    const url = String(input);
    requestedUrls.push(url);
    if (url === '/api/v1/provider-profiles') return Promise.resolve(ok([]));
    if (url === '/api/v1/secrets') return Promise.resolve(ok({ items: [] }));
    if (url === '/me') return Promise.resolve(ok({ id: 'user-1', email: 'user@example.com' }));
    if (url.startsWith('/api/v1/settings/catalog')) {
      const scope = url.includes('scope=user') ? 'user' : 'workspace';
      return Promise.resolve(ok(userWorkspaceCatalog(scope)));
    }
    if (url === '/api/system/worker-pause') {
      return Promise.resolve(ok({ system: { workersPaused: false }, metrics: {}, commands: [] }));
    }
    if (url === '/api/v1/operations/codex/shards') return Promise.resolve(ok({ shards: [] }));
    if (url === '/api/v1/operations/deployment/stacks/moonmind') {
      return Promise.resolve(ok({ stack: 'moonmind', projectName: 'moonmind', currentImage: {}, recentActions: [], policy: {} }));
    }
    if (url.startsWith('/api/v1/operations/deployment/image-targets')) {
      return Promise.resolve(ok({ stack: 'moonmind', repositories: [] }));
    }
    return Promise.resolve(fail(404));
  });
}

function fullPermissionsPayload(page: string): BootPayload {
  return {
    page,
    apiBase: '/api',
    initialData: {
      settingsPermissions: [
        'provider_profiles.read',
        'provider_profiles.write',
        'secrets.metadata.read',
        'settings.effective.read',
        'settings.catalog.read',
        'settings.audit.read',
        'settings.workspace.write',
        'settings.user.write',
        'operations.read',
        'operations.invoke',
      ],
      runtimeConfig: { system: { supportedRuntimes: ['codex_cli', 'claude_code'] } },
      workerPause: {
        get: '/api/system/worker-pause',
        post: '/api/system/worker-pause',
        shardHealth: '/api/v1/operations/codex/shards',
      },
    },
  } as unknown as BootPayload;
}

const presetField = (value: unknown, editable = true, source = 'test_policy') => ({
  value,
  source,
  editable,
  required: false,
  lock_reason: editable ? null : 'Backend controlled.',
});

function guidedPreset(overrides: Record<string, unknown> = {}) {
  return {
    version: 'provider-profile-create-v1-conformance',
    supported: true,
    runtime_id: 'codex_cli',
    provider_id: 'openai',
    authentication_method: 'api_key',
    fields: {
      credential_source: presetField('none', false),
      runtime_materialization_mode: presetField('api_key_env', false),
      secret_refs: presetField({}),
      volume_ref: presetField(null, false),
      volume_mount_path: presetField(null, false),
      max_parallel_runs: presetField(1),
      cooldown_after_429_seconds: presetField(300),
      rate_limit_policy: presetField('backoff'),
      enabled: presetField(false, false),
      is_default: presetField(false),
      command_behavior: presetField({ auth_strategy: 'api_key_env' }, false),
      user_tags: presetField([]),
      priority: presetField(100),
      clear_env_keys: presetField(['OPENAI_API_KEY'], false),
      ...overrides,
    },
    diagnostics: [],
    manual_creation_allowed: false,
    required_manual_fields: [],
  };
}

function capabilitiesFor(preset: ReturnType<typeof guidedPreset>) {
  return {
    version: preset.version,
    runtime_id: preset.runtime_id,
    provider_id: preset.provider_id,
    supported: true,
    authentication_methods: [
      {
        id: 'api_key',
        label: 'API key',
        setup_action: 'api_key',
        launch_ready_after_setup: true,
        fields: preset.fields,
        secret_roles: [],
        imported_volume: { supported: false, mount_path: null, source: 'test', lock_reason: 'no' },
      },
    ],
    diagnostics: [],
  };
}

describe('MoonLadderStudios/MoonMind#3822 canonical navigation journey', () => {
  let fetchSpy: MockInstance;
  const requestedUrls: string[] = [];

  beforeEach(() => {
    requestedUrls.length = 0;
    fetchSpy = mockSettingsFetch(requestedUrls);
  });

  afterEach(() => {
    fetchSpy.mockRestore();
  });

  it('resolves each canonical route to its own route-owned page', () => {
    expect(resolveDashboardRoute('/settings/providers-secrets')?.page).toBe('settings-providers-secrets');
    expect(resolveDashboardRoute('/settings/user-workspace')?.page).toBe('settings-user-workspace');
    expect(resolveDashboardRoute('/settings/operations')?.page).toBe('settings-operations');
    expect(resolveDashboardRoute('/settings')?.page).toBe('settings-entry');
    // Legacy section identity is not a route: the registry owns page identity.
    expect(resolveDashboardRoute('/settings/provider-profiles')).toBeNull();
  });

  it('exposes exactly three ordered Configuration destinations with canonical metadata', () => {
    const group = DASHBOARD_DESTINATION_GROUPS.find(({ key }) => key === 'configuration');
    expect(group?.triggerLabel).toBe('Settings');
    expect(group?.destinationKeys).toEqual([
      'settings-providers-secrets',
      'settings-user-workspace',
      'settings-operations',
    ]);
    const settingsDestinations = DASHBOARD_DESTINATIONS.filter(({ menuGroupKey }) => menuGroupKey === 'configuration');
    expect(settingsDestinations.map(({ canonicalPath }) => canonicalPath)).toEqual([
      '/settings/providers-secrets',
      '/settings/user-workspace',
      '/settings/operations',
    ]);
    for (const destination of settingsDestinations) {
      expect(destination.navigationGroup).toBe('system');
      expect(destinationForPath(destination.canonicalPath)?.key).toBe(destination.key);
    }
  });

  it.each(SETTINGS_PAGES.map((entry) => [entry.title, entry] as const))(
    'direct load of %s sets the document title and keeps the Settings trigger stable',
    async (_title, entry) => {
      window.history.replaceState({}, '', entry.path);
      renderWithClient(
        <BrowserRouter>
          <entry.Page payload={fullPermissionsPayload(entry.page)} />
        </BrowserRouter>,
      );
      expect(await screen.findByRole('heading', { name: entry.title })).toBeTruthy();
      await waitFor(() => expect(document.title).toBe(`${entry.title} | MoonMind`));
      // Stable trigger contract: the Configuration group trigger stays `Settings`
      // on every child route (SettingsPage.md acceptance criterion 7).
      const group = DASHBOARD_DESTINATION_GROUPS.find(({ key }) => key === 'configuration');
      expect(group?.triggerLabel).toBe('Settings');
      expect(destinationForPath(entry.path)?.key).toBe(entry.key);
    },
  );

  it('loads only the owning page primary queries for each route', async () => {
    window.history.replaceState({}, '', '/settings/providers-secrets');
    const { unmount } = renderWithClient(
      <BrowserRouter>
        <ProvidersSecretsSettingsPage payload={fullPermissionsPayload('settings-providers-secrets')} />
      </BrowserRouter>,
    );
    expect(await screen.findByRole('heading', { name: 'Providers & Secrets' })).toBeTruthy();
    await waitFor(() => expect(requestedUrls).toContain('/api/v1/provider-profiles'));
    expect(requestedUrls).toContain('/api/v1/secrets');
    expect(requestedUrls.some((url) => url.startsWith('/api/v1/settings/catalog'))).toBe(false);
    expect(requestedUrls).not.toContain('/me');
    expect(requestedUrls).not.toContain('/api/system/worker-pause');
    unmount();
  });

  it('isolates a localized query failure to its own region', async () => {
    fetchSpy.mockImplementation((input) => {
      const url = String(input);
      requestedUrls.push(url);
      if (url === '/api/v1/provider-profiles') return Promise.resolve(fail());
      if (url === '/api/v1/secrets') return Promise.resolve(ok({ items: [] }));
      return Promise.resolve(fail(404));
    });
    window.history.replaceState({}, '', '/settings/providers-secrets');
    renderWithClient(
      <BrowserRouter>
        <ProvidersSecretsSettingsPage payload={fullPermissionsPayload('settings-providers-secrets')} />
      </BrowserRouter>,
    );
    expect(await screen.findByText('Failed to load provider profiles.')).toBeTruthy();
    // Sibling region on the same page stays usable.
    expect(screen.getByRole('heading', { name: 'Managed Secrets' })).toBeTruthy();
  });
});

describe('MoonLadderStudios/MoonMind#3822 legacy migration journey', () => {
  it('redirects legacy paths with replacement semantics, safe-filter survival, and section removal', () => {
    expect(legacySettingsRedirect('/secrets', '')).toBe('/settings/providers-secrets');
    expect(legacySettingsRedirect('/workers', '')).toBe('/settings/operations');
    expect(legacySettingsRedirect('/secrets', '?runtime=codex_cli')).toBe(
      '/settings/providers-secrets?runtime=codex_cli',
    );
    expect(legacySettingsRedirect('/workers', '?status=paused')).toBe('/settings/operations?status=paused');
    // `section` is retired page identity (SettingsPage.md 5.3/15.5): always removed.
    expect(legacySettingsRedirect('/secrets', '?section=providers&runtime=codex_cli')).toBe(
      '/settings/providers-secrets?runtime=codex_cli',
    );
    expect(filterSettingsQueryForTarget('?section=operations&status=paused', '/settings/operations')).toBe(
      '/settings/operations?status=paused',
    );
    expect(filterSettingsQueryForTarget('?section=user-workspace&scope=user', '/settings/user-workspace')).toBe(
      '/settings/user-workspace?scope=user',
    );
    // Cross-page filters do not leak into the target.
    expect(filterSettingsQueryForTarget('?runtime=codex_cli', '/settings/operations')).toBe('/settings/operations');
    // No redirect loop: every legacy target is a canonical route, never itself legacy.
    for (const target of [legacySettingsRedirect('/secrets', ''), legacySettingsRedirect('/workers', '')]) {
      const targetPath = String(target).split('?')[0] ?? '';
      expect(target).not.toBe('/secrets');
      expect(target).not.toBe('/workers');
      expect(resolveDashboardRoute(targetPath)).not.toBeNull();
      expect(legacySettingsRedirect(targetPath, '')).toBeNull();
    }
  });

  it('falls back permission-aware without retaining a redundant legacy history entry', () => {
    const allAuthorized = {
      features: {
        settingsProvidersSecrets: true,
        settingsUserWorkspace: true,
        settingsOperations: true,
      },
    };
    expect(resolveAuthorizedLegacySettingsTarget('/secrets', '?runtime=x', allAuthorized, false)).toEqual({
      status: 'redirect',
      target: '/settings/providers-secrets?runtime=x',
    });
    // Unauthorized preferred destination re-filters the original query for the fallback.
    expect(
      resolveAuthorizedLegacySettingsTarget('/workers', '?runtime=codex_cli', {
        features: { settingsProvidersSecrets: true, settingsUserWorkspace: false, settingsOperations: false },
      }, false),
    ).toEqual({ status: 'redirect', target: '/settings/providers-secrets?runtime=codex_cli' });
    expect(
      resolveAuthorizedLegacySettingsTarget('/secrets', '', {
        features: { settingsProvidersSecrets: false, settingsUserWorkspace: false, settingsOperations: false },
      }, false),
    ).toEqual({ status: 'redirect', target: '/settings' });
    expect(resolveAuthorizedLegacySettingsTarget('/secrets', '', allAuthorized, true)).toEqual({ status: 'pending' });
    // Unknown historical aliases resolve to the permission-aware entry point
    // (dashboard-app unknownSettingsAliasTarget uses replacement navigation).
    const dashboardApp = readRepoFile('frontend', 'src', 'entrypoints', 'dashboard-app.tsx');
    expect(dashboardApp).toContain('unknownSettingsAliasTarget');
    expect(dashboardApp).toMatch(/replace/);
  });

  it('resolves the bare entry to the first authorized destination', async () => {
    window.history.replaceState({}, '', '/settings');
    renderWithClient(
      <BrowserRouter>
        <SettingsEntryPage
          payload={{ page: 'settings-entry', apiBase: '/api', initialData: { settingsPermissions: ['operations.read'] } } as BootPayload}
        />
      </BrowserRouter>,
    );
    await waitFor(() => expect(window.location.pathname).toBe('/settings/operations'));
  });
});

describe('MoonLadderStudios/MoonMind#3822 menu visibility and accessibility matrix', () => {
  const featuresAll = {
    features: { settingsProvidersSecrets: true, settingsUserWorkspace: true, settingsOperations: true },
  };

  it('models shown, hidden, unavailable, and empty visibility states with one Configuration label', () => {
    const providers = DASHBOARD_DESTINATIONS.find(({ key }) => key === 'settings-providers-secrets')!;
    expect(destinationState(providers, featuresAll)).toBe('shown');
    expect(destinationState(providers, { features: {} })).toBe('hidden');
    expect(destinationState(providers, { features: { settingsProvidersSecrets: false } })).toBe('unavailable');
    expect(exposedSystemDestinations(featuresAll).filter(({ menuGroupKey }) => menuGroupKey === 'configuration')).toHaveLength(3);
    // Intentionally unavailable children stay exposed so the authorization
    // boundary is explicit instead of silently hidden.
    const oneUnavailable = exposedSystemDestinations({
      features: { settingsProvidersSecrets: false, settingsUserWorkspace: true, settingsOperations: true },
    }).filter(({ menuGroupKey }) => menuGroupKey === 'configuration');
    expect(oneUnavailable).toHaveLength(3);
    expect(exposedSystemDestinations({ features: {} }).filter(({ menuGroupKey }) => menuGroupKey === 'configuration')).toHaveLength(0);
    const configurationGroups = DASHBOARD_DESTINATION_GROUPS.filter(({ key }) => key === 'configuration');
    expect(configurationGroups).toHaveLength(1);
  });

  it.each(SETTINGS_PAGES.map((entry) => [entry.path] as const))(
    'keeps the stable Settings trigger on %s',
    (path) => {
      const uiInfo = { features: { settingsProvidersSecrets: true, settingsUserWorkspace: true, settingsOperations: true } };
      const active = destinationForPath(path);
      expect(active).not.toBeNull();
      renderWithClient(
        <MemoryRouter initialEntries={[path]}>
          <DashboardSystemMenu uiInfo={uiInfo} mobileDrawerOpen={false} />
        </MemoryRouter>,
      );
      expect(screen.getByRole('button', { name: /Settings/ })).toBeTruthy();
    },
  );

  it('supports desktop popover keyboard operation with focus return and link selection', async () => {
    const uiInfo = { features: { settingsProvidersSecrets: true, settingsUserWorkspace: true, settingsOperations: true } };
    renderWithClient(
      <MemoryRouter initialEntries={['/settings/providers-secrets']}>
        <DashboardSystemMenu uiInfo={uiInfo} mobileDrawerOpen={false} />
      </MemoryRouter>,
    );
    const trigger = screen.getByRole('button', { name: /Settings/ });
    fireEvent.click(trigger);
    const menu = await screen.findByRole('menu', { name: 'System' });
    const items = Array.from(menu.querySelectorAll<HTMLElement>('[role="menuitem"]'));
    expect(items.length).toBeGreaterThanOrEqual(3);
    // Configuration children render as links in a stable order, never tabs.
    expect(menu.querySelector('[role="tab"]')).toBeNull();
    const labels = items.map((item) => item.textContent ?? '');
    expect(labels.join('|')).toContain('Providers & Secrets');
    expect(labels.join('|')).toContain('User / Workspace');
    expect(labels.indexOf(labels.find((label) => label.includes('Providers & Secrets')) ?? '')).toBeLessThan(
      labels.indexOf(labels.find((label) => label.includes('Operations')) ?? ''),
    );

    const first = items[0]!;
    const last = items[items.length - 1]!;
    first.focus();
    fireEvent.keyDown(menu, { key: 'End' });
    expect(document.activeElement).toBe(last);
    fireEvent.keyDown(menu, { key: 'Home' });
    expect(document.activeElement).toBe(first);
    fireEvent.keyDown(menu, { key: 'ArrowDown' });
    expect(document.activeElement).toBe(items[1]);
    fireEvent.keyDown(menu, { key: 'ArrowUp' });
    expect(document.activeElement).toBe(first);
    // Escape closes and returns focus to the trigger.
    fireEvent.keyDown(menu, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('menu', { name: 'System' })).toBeNull());
    expect(document.activeElement).toBe(trigger);
    // Click selection navigates and closes the popover.
    fireEvent.click(trigger);
    const reopened = await screen.findByRole('menu', { name: 'System' });
    const operations = Array.from(reopened.querySelectorAll<HTMLElement>('[role="menuitem"]')).find((item) =>
      (item.textContent ?? '').includes('Operations'),
    )!;
    fireEvent.click(operations);
    await waitFor(() => expect(screen.queryByRole('menu', { name: 'System' })).toBeNull());
  });

  it('exposes the same group and order in the mobile drawer', () => {
    const uiInfo = { features: { settingsProvidersSecrets: true, settingsUserWorkspace: true, settingsOperations: true } };
    renderWithClient(
      <MemoryRouter initialEntries={['/settings/operations']}>
        <DashboardSystemMenu uiInfo={uiInfo} mobileDrawerOpen />
      </MemoryRouter>,
    );
    const inline = screen.getByLabelText('System destinations');
    expect(inline.textContent).toContain('Providers & Secrets');
    expect(inline.textContent).toContain('User / Workspace');
    expect(inline.textContent).toContain('Operations');
    expect(inline.querySelector('[role="tab"]')).toBeNull();
  });
});

describe('MoonLadderStudios/MoonMind#3822 page boundaries and dirty drafts', () => {
  let fetchSpy: MockInstance;

  beforeEach(() => {
    fetchSpy = mockSettingsFetch([]);
  });

  afterEach(() => {
    fetchSpy.mockRestore();
  });

  it('never mounts sibling managers on a route-owned page', async () => {
    window.history.replaceState({}, '', '/settings/user-workspace?scope=workspace');
    renderWithClient(
      <BrowserRouter>
        <UserWorkspaceSettingsPage payload={fullPermissionsPayload('settings-user-workspace')} />
      </BrowserRouter>,
    );
    expect(await screen.findByRole('heading', { name: 'User / Workspace' })).toBeTruthy();
    expect(screen.queryByRole('heading', { name: 'Provider Profiles' })).toBeNull();
    expect(screen.queryByLabelText('Worker Operations')).toBeNull();
  });

  it('guards a generated-settings draft with Stay, Discard and leave, and unload protection', async () => {
    window.history.replaceState({}, '', '/settings/user-workspace?scope=workspace');
    renderWithClient(
      <BrowserRouter>
        <UserWorkspaceSettingsPage payload={fullPermissionsPayload('settings-user-workspace')} />
      </BrowserRouter>,
    );
    const control = (await screen.findByLabelText('Default Publish Mode')) as HTMLSelectElement;
    fireEvent.change(control, { target: { value: 'branch' } });
    expect(control.value).toBe('branch');
    // Browser unload is guarded within platform limits while dirty.
    expect(window.dispatchEvent(new Event('beforeunload', { cancelable: true }))).toBe(false);

    const link = document.createElement('a');
    link.href = '/settings/operations';
    link.textContent = 'Destination';
    document.body.appendChild(link);
    try {
      fireEvent.click(link);
      expect(screen.getByRole('dialog', { name: 'Unsaved changes' })).toBeTruthy();
      fireEvent.click(screen.getByRole('button', { name: 'Stay' }));
      expect(window.location.pathname).toBe('/settings/user-workspace');
      expect((screen.getByLabelText('Default Publish Mode') as HTMLSelectElement).value).toBe('branch');

      fireEvent.click(link);
      fireEvent.click(screen.getByRole('button', { name: 'Discard and leave' }));
      await waitFor(() => expect(window.location.pathname).toBe('/settings/operations'));
    } finally {
      link.remove();
    }
    expect(fetchSpy).not.toHaveBeenCalledWith('/api/v1/provider-profiles', expect.anything());
  });
});

describe('MoonLadderStudios/MoonMind#3822 standard creation, disclosure, and secret safety', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  function renderManager(profiles: ProviderProfile[] = []) {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const onNotice = vi.fn();
    renderWithClient(
      <ProviderProfilesManager
        profiles={profiles}
        secretSlugs={['OPENAI_API_KEY']}
        onNotice={onNotice}
        queryClient={queryClient}
        defaultTaskModelByRuntime={{}}
      />,
    );
    return { onNotice };
  }

  it('creates from a supported guided preset without low-level plumbing and omits untouched advanced values', async () => {
    const preset = guidedPreset();
    const fetchSpy = vi.spyOn(window, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.startsWith('/api/v1/provider-profiles/creation-capabilities?')) {
        return ok(capabilitiesFor(preset));
      }
      if (url.startsWith('/api/v1/provider-profiles/creation-preset?')) return ok(preset);
      if (url === '/api/v1/provider-profiles' && (init as RequestInit | undefined)?.method === 'POST') {
        return ok({ profile_id: 'preset-profile', runtime_id: 'codex_cli', provider_id: 'openai' });
      }
      throw new Error(`Unexpected fetch ${url}`);
    });
    renderManager();
    fireEvent.change(screen.getByLabelText(/Profile ID/), { target: { value: 'preset-profile' } });
    fireEvent.change(screen.getByLabelText(/Runtime ID/), { target: { value: 'codex_cli' } });
    fireEvent.change(screen.getByLabelText(/Provider ID/), { target: { value: 'openai' } });
    fireEvent.click(await screen.findByLabelText('API key'));
    await screen.findByText(/Backend preset provider-profile-create-v1-conformance loaded/);

    // Supported standard path: no low-level plumbing interaction required.
    expect(screen.queryByLabelText(/Credential source/)).toBeNull();
    expect(screen.queryByLabelText('Secret refs (JSON object of string refs)')).toBeNull();
    expect(screen.queryByLabelText('Clear env keys')).toBeNull();
    expect(screen.getByLabelText(/Max parallel runs/)).toBeTruthy();
    expect(screen.getByLabelText('Runtime default')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Create provider profile' }));
    await waitFor(() => {
      expect(fetchSpy.mock.calls.some(([input, init]) => input === '/api/v1/provider-profiles' && (init as RequestInit)?.method === 'POST')).toBe(true);
    });
    const post = fetchSpy.mock.calls.find(([input, init]) => input === '/api/v1/provider-profiles' && (init as RequestInit)?.method === 'POST');
    const payload = JSON.parse(String((post?.[1] as RequestInit).body));
    expect(payload).toMatchObject({
      profile_id: 'preset-profile',
      runtime_id: 'codex_cli',
      provider_id: 'openai',
      authentication_method: 'api_key',
      preset_version: preset.version,
    });
    for (const omitted of [
      'credential_source', 'runtime_materialization_mode', 'secret_refs', 'volume_ref', 'volume_mount_path',
      'max_parallel_runs', 'cooldown_after_429_seconds', 'rate_limit_policy', 'enabled', 'is_default',
      'command_behavior', 'tags', 'priority', 'clear_env_keys',
    ]) {
      expect(payload).not.toHaveProperty(omitted);
    }
  });

  it('blocks an unsupported combination without a safe preset instead of guessing materialization', async () => {
    const unsupported = {
      ...guidedPreset(),
      supported: false,
      diagnostics: [{ code: 'no_safe_standard_creation_preset', severity: 'error', message: 'No validated creation preset exists for this runtime and provider.', field: null, action: 'open_manual_profile' }],
    };
    vi.spyOn(window, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.startsWith('/api/v1/provider-profiles/creation-capabilities?')) {
        return ok({ ...capabilitiesFor(guidedPreset()), supported: false, diagnostics: ['No validated creation preset exists for this runtime and provider.'] });
      }
      if (url.startsWith('/api/v1/provider-profiles/creation-preset?')) return ok(unsupported);
      throw new Error(`Unexpected fetch ${url}`);
    });
    renderManager();
    fireEvent.change(screen.getByLabelText(/Runtime ID/), { target: { value: 'codex_cli' } });
    fireEvent.change(screen.getByLabelText(/Provider ID/), { target: { value: 'openai' } });
    await screen.findByText('No validated creation preset exists for this runtime and provider.');
  });

  it('keeps advanced disclosure collapsed by default, connected, and draft-preserving', async () => {
    const preset = guidedPreset();
    vi.spyOn(window, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.startsWith('/api/v1/provider-profiles/creation-capabilities?')) return ok(capabilitiesFor(preset));
      if (url.startsWith('/api/v1/provider-profiles/creation-preset?')) return ok(preset);
      throw new Error(`Unexpected fetch ${url}`);
    });
    renderManager();
    fireEvent.change(screen.getByLabelText(/Runtime ID/), { target: { value: 'codex_cli' } });
    fireEvent.change(screen.getByLabelText(/Provider ID/), { target: { value: 'openai' } });
    fireEvent.click(await screen.findByLabelText('API key'));
    await screen.findByText(/Backend preset provider-profile-create-v1-conformance loaded/);

    const checkbox = screen.getByLabelText('Show advanced options') as HTMLInputElement;
    expect(checkbox.checked).toBe(false);
    expect(checkbox.getAttribute('aria-controls')).toBe('provider-profile-advanced-region');
    fireEvent.click(checkbox);
    const cooldown = screen.getByLabelText(/Cooldown after 429/) as HTMLInputElement;
    fireEvent.change(cooldown, { target: { value: '600' } });
    fireEvent.click(checkbox);
    expect(screen.queryByLabelText(/Cooldown after 429/)).toBeNull();
    fireEvent.click(screen.getByLabelText('Show advanced options'));
    expect((screen.getByLabelText(/Cooldown after 429/) as HTMLInputElement).value).toBe('600');
    expect(checkbox.checked).toBe(true);
  });

  it('clears API-key plaintext after submit and never leaks it outside the enrollment call', async () => {
    const preset = guidedPreset();
    const plaintext = 'sk-conformance-never-leak';
    const fetchSpy = vi.spyOn(window, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.startsWith('/api/v1/provider-profiles/creation-capabilities?')) return ok(capabilitiesFor(preset));
      if (url.startsWith('/api/v1/provider-profiles/creation-preset?')) return ok(preset);
      if (url === '/api/v1/provider-profiles' && (init as RequestInit | undefined)?.method === 'POST') {
        return ok({ profile_id: 'secret-safe', runtime_id: 'codex_cli', provider_id: 'openai', credential_source: 'none', runtime_materialization_mode: 'api_key_env', secret_refs: {}, enabled: false });
      }
      if (url === '/api/v1/provider-profiles/secret-safe/credentials/api-key') {
        return ok({ status: 'ready', readiness: { connected: true, launch_ready: true } });
      }
      throw new Error(`Unexpected fetch ${url}`);
    });
    renderManager();
    fireEvent.change(screen.getByLabelText(/Profile ID/), { target: { value: 'secret-safe' } });
    fireEvent.change(screen.getByLabelText(/Runtime ID/), { target: { value: 'codex_cli' } });
    fireEvent.change(screen.getByLabelText(/Provider ID/), { target: { value: 'openai' } });
    fireEvent.click(await screen.findByLabelText('API key'));
    await screen.findByText(/Backend preset provider-profile-create-v1-conformance loaded/);
    fireEvent.click(screen.getByRole('button', { name: 'Create provider profile' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Continue to API key paste' }));
    fireEvent.change(screen.getByLabelText('OpenAI API key'), { target: { value: plaintext } });
    fireEvent.click(screen.getByRole('button', { name: 'Validate and save OpenAI API key' }));
    await waitFor(() => {
      expect(fetchSpy.mock.calls.some(([input]) => input === '/api/v1/provider-profiles/secret-safe/credentials/api-key')).toBe(true);
    });
    // Plaintext is cleared from the rendered form after submit.
    await waitFor(() => {
      const remaining = screen.queryByLabelText('OpenAI API key');
      if (remaining) expect((remaining as HTMLInputElement).value).toBe('');
    });
    // Plaintext never enters the profile create payload, URLs, or web storage.
    const createCall = fetchSpy.mock.calls.find(([input, init]) => input === '/api/v1/provider-profiles' && (init as RequestInit)?.method === 'POST');
    expect(String((createCall?.[1] as RequestInit)?.body)).not.toContain(plaintext);
    expect(window.location.href).not.toContain(plaintext);
    expect(window.localStorage.getItem('secret-safe')).toBeNull();
    expect(window.sessionStorage.getItem('secret-safe')).toBeNull();
    for (const [input, init] of fetchSpy.mock.calls) {
      if (input === '/api/v1/provider-profiles/secret-safe/credentials/api-key') continue;
      expect(String(input)).not.toContain(plaintext);
      expect(String((init as RequestInit | undefined)?.body ?? '')).not.toContain(plaintext);
    }
  });
});

describe('MoonLadderStudios/MoonMind#3822 launch safety, tiers, and existing-profile compatibility', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('treats clear_env_keys as backend-owned read-only launch policy on guided paths', async () => {
    const preset = guidedPreset();
    vi.spyOn(window, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.startsWith('/api/v1/provider-profiles/creation-capabilities?')) return ok(capabilitiesFor(preset));
      if (url.startsWith('/api/v1/provider-profiles/creation-preset?')) return ok(preset);
      throw new Error(`Unexpected fetch ${url}`);
    });
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    renderWithClient(
      <ProviderProfilesManager profiles={[]} secretSlugs={[]} onNotice={vi.fn()} queryClient={queryClient} defaultTaskModelByRuntime={{}} />,
    );
    fireEvent.change(screen.getByLabelText(/Runtime ID/), { target: { value: 'codex_cli' } });
    fireEvent.change(screen.getByLabelText(/Provider ID/), { target: { value: 'openai' } });
    fireEvent.click(await screen.findByLabelText('API key'));
    await screen.findByText(/Backend preset provider-profile-create-v1-conformance loaded/);
    fireEvent.click(screen.getByLabelText('Show advanced options'));
    expect(screen.getByText(/Launch-security metadata — clear environment keys/)).toBeTruthy();
    expect(screen.getByText(/Value: OPENAI_API_KEY/)).toBeTruthy();
    // Guided path: no freeform textarea for launch-security metadata.
    expect(screen.queryByLabelText('Clear env keys')).toBeNull();
  });

  it('integrates model tiers canonically without legacy mirrors and preserves drafts across disclosure', () => {
    // Unit boundary: the standard form starts with one runtime-default tier
    // draft (null model/effort = backend-resolved runtime default), saved
    // canonically as model_tiers/default_model_tier.
    const first = runtimeDefaultTierDraft();
    expect(first.clientId).toBeTruthy();
    expect(first.model).toBeNull();
    expect(first.effort).toBeNull();
    const payload = buildProviderProfileTierPayload([first], first.clientId);
    expect(Array.isArray(payload.model_tiers)).toBe(true);
    expect(payload.model_tiers).toHaveLength(1);
    expect(payload).toHaveProperty('default_model_tier', 1);
    expect(payload).not.toHaveProperty('default_model');
    expect(payload).not.toHaveProperty('default_effort');
    // Saved tier entries normalize without being silently dropped.
    const normalized = normalizeProviderProfileTiers([{ model: 'custom-model', effort: 'high' }], 1);
    expect(normalized.tiers.length).toBeGreaterThanOrEqual(1);
    expect(normalized.defaultTierClientId).toBeTruthy();
  });

  it('does not silently normalize, erase, enable, or default an existing custom profile on edit', async () => {
    const fetchSpy = vi.spyOn(window, 'fetch').mockImplementation(async () => ok({}));
    const custom: ProviderProfile = {
      profile_id: 'existing-custom',
      runtime_id: 'codex_cli',
      provider_id: 'openai',
      credential_source: 'secret_ref',
      runtime_materialization_mode: 'api_key_env',
      secret_refs: { openai_api_key: 'db://team-key', unknown_role: 'db://legacy-binding' },
      volume_ref: 'codex_auth_volume',
      volume_mount_path: '/home/app/.codex',
      max_parallel_runs: 4,
      cooldown_after_429_seconds: 600,
      rate_limit_policy: 'backoff',
      command_behavior: { extra_args: ['--sandbox'] },
      tags: ['team-a'],
      priority: 10,
      clear_env_keys: ['OPENAI_API_KEY'],
      enabled: false,
      is_default: false,
    } as unknown as ProviderProfile;
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    renderWithClient(
      <ProviderProfilesManager profiles={[custom]} secretSlugs={['team-key']} onNotice={vi.fn()} queryClient={queryClient} defaultTaskModelByRuntime={{}} />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    // Unknown legacy bindings remain visible and round-trippable.
    expect((screen.getByLabelText(/Profile ID/) as HTMLInputElement).value).toBe('existing-custom');
    // Opening edit must not auto-mutate, enable, or default the profile.
    expect(fetchSpy.mock.calls.some(([, init]) => (init as RequestInit | undefined)?.method === 'PATCH')).toBe(false);
    expect(fetchSpy.mock.calls.some(([, init]) => (init as RequestInit | undefined)?.method === 'POST')).toBe(false);
  });
});

describe('MoonLadderStudios/MoonMind#3822 generated-contract and architecture guards', () => {
  it('keeps regenerated OpenAPI frontend types carrying the Provider Profile creation contract', () => {
    const generated = readRepoFile('frontend', 'src', 'generated', 'openapi.ts');
    expect(generated).toContain('/api/v1/provider-profiles/creation-preset');
    expect(generated).toContain('ProviderProfileCreationPresetResponse');
    expect(generated).toContain('ProviderProfileCreationCapabilitiesResponse');
    expect(generated).toContain('clear_env_keys');
  });

  it('prevents a second hand-maintained creation-preset schema and tabbed/section regressions', () => {
    const manager = readRepoFile('frontend', 'src', 'components', 'settings', 'ProviderProfilesManager.tsx');
    // Single canonical alias into the regenerated contract.
    expect(manager).toContain("components['schemas']['ProviderProfileCreationPresetResponse']");
    expect(manager).not.toMatch(/interface ProviderProfileCreationPresetResponse/);
    expect(manager).not.toMatch(/type ProviderProfileCreationPresetResponse\s*=\s*\{/);

    const settings = readRepoFile('frontend', 'src', 'entrypoints', 'settings.tsx');
    // No tabbed Settings regression: destinations navigate as documents/links.
    expect(settings).not.toContain('role="tab"');
    expect(settings).not.toContain("role='tab'");
    // `?section=` is retired page identity: no settings page reads it.
    expect(settings).not.toMatch(/get\(['"]section['"]\)/);
    const menu = readRepoFile('frontend', 'src', 'components', 'DashboardSystemMenu.tsx');
    expect(menu).not.toContain('role="tab"');
    // Launch-safety stays backend-owned: no freeform textarea regression in the standard form.
    expect(manager).not.toMatch(/<textarea[^>]*clear_env_keys/i);
  });
});
