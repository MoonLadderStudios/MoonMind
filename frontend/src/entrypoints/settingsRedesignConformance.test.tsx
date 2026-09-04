/**
 * Settings redesign conformance suite — navigation, migration, accessibility,
 * and architecture guards.
 *
 * MoonLadderStudios/MoonMind#3822 (parent MoonLadderStudios/MoonMind#3815).
 *
 * This suite proves the independently implemented pieces of the redesign agree
 * at their boundaries. It must fail when the implementation drifts back to one
 * tabbed Settings page or to browser-owned Settings destination state.
 *
 * Run it on its own with `npm run ui:test:settings-redesign`.
 */
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

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
import { act, fireEvent, renderWithClient, screen, waitFor, within } from '../utils/test-utils';
import {
  legacySettingsRedirect,
  resolveDashboardRoute,
  DASHBOARD_DESTINATIONS,
  DASHBOARD_DESTINATION_GROUPS,
} from '../lib/dashboardRoutes';
import { DashboardApp } from './dashboard-app';
import {
  OperationsSettingsPage,
  ProvidersSecretsSettingsPage,
  UserWorkspaceSettingsPage,
} from './settings';

vi.mock('lucide-animated', async () => {
  const React = await vi.importActual<typeof import('react')>('react');
  const AnimatedIcon = React.forwardRef<unknown, { className?: string }>((props, ref) => {
    React.useImperativeHandle(ref, () => ({
      startAnimation: () => undefined,
      stopAnimation: () => undefined,
    }));
    return React.createElement('svg', { className: props.className, 'aria-hidden': true });
  });
  return {
    MoonIcon: AnimatedIcon,
    RocketIcon: AnimatedIcon,
    SettingsIcon: AnimatedIcon,
    SparklesIcon: AnimatedIcon,
  };
});

vi.mock('./workflows-workspace', () => ({
  default: () => <div>Workflow list route loaded</div>,
}));

const CANONICAL_SETTINGS_ROUTES = [
  {
    path: '/settings/providers-secrets',
    heading: 'Providers & Secrets',
    title: 'Providers & Secrets | MoonMind',
    menuItem: 'Providers & Secrets',
  },
  {
    path: '/settings/user-workspace',
    heading: 'User / Workspace',
    title: 'User / Workspace | MoonMind',
    menuItem: 'User / Workspace',
  },
  {
    path: '/settings/operations',
    heading: 'Operations',
    title: 'Operations | MoonMind',
    menuItem: 'Operations',
  },
] as const;

const ALL_SETTINGS_PERMISSIONS = [
  'provider_profiles.read',
  'provider_profiles.write',
  'secrets.metadata.read',
  'settings.effective.read',
  'settings.catalog.read',
  'settings.workspace.write',
  'settings.user.write',
  'operations.read',
  'operations.invoke',
];

const userWorkspaceDescriptor = {
  key: 'workflow.default_publish_mode',
  title: 'Default Publish Mode',
  description: 'Default publication behavior.',
  category: 'Workflow',
  section: 'user-workspace',
  type: 'enum',
  ui: 'select',
  scopes: ['workspace', 'user'],
  default_value: 'pr',
  effective_value: 'pr',
  override_value: null,
  source: 'default',
  source_explanation: 'Default value.',
  apply_mode: 'next_request',
  activation_state: 'active',
  active: true,
  options: [
    { value: 'pr', label: 'Pull request' },
    { value: 'branch', label: 'Branch' },
  ],
  constraints: null,
  sensitive: false,
  read_only: false,
  requires_reload: false,
  requires_worker_restart: false,
  requires_process_restart: false,
  applies_to: ['workflows'],
  order: 1,
  value_version: 1,
  diagnostics: [],
};

function ok(body: unknown): Response {
  return { ok: true, status: 200, statusText: 'OK', json: async () => body } as Response;
}

function notFound(): Response {
  return {
    ok: false,
    status: 404,
    statusText: 'Not Found',
    json: async () => ({}),
    text: async () => 'Not Found',
  } as Response;
}

function uiInfo(overrides: Record<string, unknown> = {}) {
  return {
    app: 'moonmind',
    buildId: 'test-build',
    apiBase: '/api',
    features: {
      workflowList: true,
      workflowActions: true,
      artifacts: true,
      schedules: true,
      skills: true,
      manifests: true,
      settingsProvidersSecrets: true,
      settingsUserWorkspace: true,
      settingsOperations: true,
    },
    limits: {},
    endpoints: { workflows: '/api/executions' },
    dashboardConfig: { initialPath: '/workflows', pollIntervalsMs: { list: 60_000, detail: 60_000, events: 60_000 } },
    settingsPermissions: ALL_SETTINGS_PERMISSIONS,
    workerPause: {
      get: '/api/system/worker-pause',
      post: '/api/system/worker-pause',
      shardHealth: '/api/v1/operations/codex/shards',
    },
    ...overrides,
  };
}

function settingsFetch(url: string): Response | null {
  if (url === '/api/v1/provider-profiles') return ok([]);
  if (url === '/api/v1/secrets') return ok({ items: [] });
  if (url === '/me') return ok({ id: 'user-1', email: 'user@example.com' });
  if (url.startsWith('/api/v1/settings/catalog')) {
    return ok({
      section: 'user-workspace',
      scope: url.includes('scope=user') ? 'user' : 'workspace',
      categories: { Workflow: [userWorkspaceDescriptor] },
    });
  }
  if (url === '/api/system/worker-pause') {
    return ok({ system: { workersPaused: false }, metrics: {}, commands: [] });
  }
  if (url === '/api/v1/operations/codex/shards') return ok({ shards: [] });
  if (url === '/api/v1/operations/deployment/stacks/moonmind') {
    return ok({
      stack: 'moonmind',
      projectName: 'moonmind',
      currentImage: { evidence: 'available' },
      recentActions: [],
      policy: {
        repository: 'moonmind',
        allowedReferences: [],
        recentTags: [],
        mutableReferences: [],
        allowedModes: [],
      },
    });
  }
  if (url === '/api/v1/operations/deployment/image-targets?stack=moonmind') {
    return ok({ stack: 'moonmind', repositories: [] });
  }
  return null;
}

function frontendFiles(root: string, keep: (name: string) => boolean): string[] {
  const files: string[] = [];
  for (const entry of readdirSync(root, { withFileTypes: true })) {
    const absolute = join(root, entry.name);
    if (entry.isDirectory()) {
      files.push(...frontendFiles(absolute, keep));
      continue;
    }
    if (keep(entry.name)) {
      files.push(absolute);
    }
  }
  return files;
}

function frontendSourceFiles(root: string): string[] {
  return frontendFiles(root, (name) => /\.(ts|tsx)$/.test(name) && !/\.test\.tsx?$/.test(name));
}

function frontendSourceTestFiles(root: string): string[] {
  return frontendFiles(root, (name) => /\.test\.tsx?$/.test(name));
}

describe('MoonLadderStudios/MoonMind#3822 Settings redesign conformance', () => {
  let fetchSpy: MockInstance;
  let requestedUrls: string[];

  beforeEach(() => {
    requestedUrls = [];
    fetchSpy = vi.spyOn(window, 'fetch').mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      requestedUrls.push(url);
      if (url === '/api/ui/info') return Promise.resolve(ok(uiInfo()));
      return Promise.resolve(settingsFetch(url) ?? notFound());
    });
  });

  afterEach(() => {
    fetchSpy.mockRestore();
    document.querySelectorAll('a[data-conformance-link]').forEach((node) => node.remove());
    window.history.replaceState({}, '', '/');
    document.title = '';
  });

  describe('canonical navigation journey', () => {
    it.each(CANONICAL_SETTINGS_ROUTES)(
      'resolves $path to its own page, title, stable Settings trigger, and active dropdown child',
      async ({ path, heading, title, menuItem }) => {
        window.history.replaceState({}, '', path);
        renderWithClient(<DashboardApp payload={{ page: 'dashboard', apiBase: '/api' }} />);

        expect(await screen.findByRole('heading', { name: heading }, { timeout: 10000 })).toBeTruthy();
        await waitFor(() => expect(document.title).toBe(title));

        // The Configuration group keeps one stable trigger on every child route.
        const trigger = screen.getByRole('button', { name: 'Settings' });
        expect(trigger.classList.contains('active')).toBe(true);
        expect(screen.queryByRole('button', { name: 'System' })).toBeNull();

        fireEvent.click(trigger);
        // Exactly one Configuration label in the navigation menu. The page
        // header eyebrow is page content, not a second navigation group.
        const configurationLabels = Array.from(
          document.querySelectorAll<HTMLElement>('.dashboard-system-menu-label'),
        ).filter((label) => label.textContent?.trim() === 'Configuration');
        expect(configurationLabels).toHaveLength(1);

        const configuration = configurationLabels[0]?.closest(
          '.dashboard-system-menu-section',
        ) as HTMLElement;
        const activeChildren = within(configuration)
          .getAllByRole('menuitem')
          .filter((item) => item.getAttribute('aria-current') === 'page')
          .map((item) => item.textContent?.trim());
        expect(activeChildren).toEqual([menuItem]);
      },
    );

    it('loads only the primary datasets owned by the requested canonical route', async () => {
      window.history.replaceState({}, '', '/settings/operations');
      renderWithClient(<DashboardApp payload={{ page: 'dashboard', apiBase: '/api' }} />);

      expect(await screen.findByRole('heading', { name: 'Operations' }, { timeout: 10000 })).toBeTruthy();
      await waitFor(() => expect(requestedUrls).toContain('/api/system/worker-pause'));
      expect(requestedUrls.some((url) => url.startsWith('/api/v1/settings/catalog'))).toBe(false);
      expect(requestedUrls).not.toContain('/me');
    });
  });

  describe('legacy migration journey', () => {
    it.each([
      // Default entry point.
      ['/settings', '', '/settings/providers-secrets', ''],
      // All three retired ?section= page identities.
      ['/settings', '?section=providers-secrets', '/settings/providers-secrets', ''],
      ['/settings', '?section=user-workspace', '/settings/providers-secrets', ''],
      ['/settings', '?section=operations&runtime=codex', '/settings/providers-secrets', '?runtime=codex'],
      // Legacy pathnames.
      ['/secrets', '?runtime=codex&status=paused&section=providers-secrets', '/settings/providers-secrets', '?runtime=codex'],
      ['/workers', '?status=paused&runtime=codex&section=operations', '/settings/operations', '?status=paused'],
      // Retained historical alias documented by #3816: an unmatched
      // /settings/* child resolves through the entry point.
      ['/settings/provider-profiles', '', '/settings/providers-secrets', ''],
    ])(
      'replaces %s%s with %s%s, keeping only that page\'s filters and no legacy history entry',
      async (legacyPath, legacySearch, expectedPath, expectedSearch) => {
        window.history.replaceState({}, '', '/workflows');
        window.history.pushState({}, '', `${legacyPath}${legacySearch}`);
        const historyLengthAtLegacyEntry = window.history.length;

        renderWithClient(<DashboardApp payload={{ page: 'dashboard', apiBase: '/api' }} />);

        await waitFor(() => expect(window.location.pathname).toBe(expectedPath), { timeout: 10000 });
        expect(window.location.search).toBe(expectedSearch);
        expect(window.location.search).not.toContain('section=');

        // No redirect loop: the resolved location is a fixpoint of the
        // production redirect chain and stays put once the app settles.
        expect(legacySettingsRedirect(window.location.pathname, window.location.search)).toBeNull();
        expect(resolveDashboardRoute(window.location.pathname)).not.toBeNull();
        const settled = `${window.location.pathname}${window.location.search}`;
        await act(async () => {
          await Promise.resolve();
          await Promise.resolve();
        });
        expect(`${window.location.pathname}${window.location.search}`).toBe(settled);

        // Replacement, not a push: the whole chain must not add a history
        // entry, and Back must not return to a retired legacy URL.
        expect(window.history.length).toBe(historyLengthAtLegacyEntry);
        act(() => window.history.back());
        await waitFor(() => expect(window.location.pathname).toBe('/workflows'));
      },
    );
  });

  describe('dirty-draft departure after save outcomes', () => {
    function renderUserWorkspace() {
      window.history.replaceState({}, '', '/settings/user-workspace?scope=workspace');
      renderWithClient(
        <BrowserRouter>
          <UserWorkspaceSettingsPage
            payload={{
              page: 'settings-user-workspace',
              apiBase: '/api',
              initialData: {
                settingsPermissions: ['settings.catalog.read', 'settings.workspace.write'],
              },
            } as BootPayload}
          />
        </BrowserRouter>,
      );
    }

    function departureLink(): HTMLAnchorElement {
      const link = document.createElement('a');
      link.href = '/settings/operations';
      link.textContent = 'Operations';
      link.setAttribute('data-conformance-link', 'true');
      document.body.appendChild(link);
      return link;
    }

    it('keeps guarding departure after a failed save and still allows an explicit discard', async () => {
      fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url === '/api/v1/settings/workspace' && init?.method === 'PATCH') {
          return Promise.resolve({
            ok: false,
            status: 500,
            statusText: 'Server Error',
            json: async () => ({ detail: 'Settings save failed.' }),
          } as Response);
        }
        return Promise.resolve(settingsFetch(url) ?? notFound());
      });

      renderUserWorkspace();
      const control = (await screen.findByLabelText('Default Publish Mode')) as HTMLSelectElement;
      fireEvent.change(control, { target: { value: 'branch' } });

      fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
      await screen.findByText(/Settings save failed with status 500\./);
      // Flush passive effects so the draft guard has observed the post-save
      // draft state, as it has by the time a real user can click a link.
      await act(async () => undefined);

      // A failed save keeps the draft, so departure must still be guarded.
      expect(control.value).toBe('branch');
      const link = departureLink();
      // fireEvent returns false when the guard cancels the navigation.
      expect(fireEvent.click(link)).toBe(false);
      expect(screen.getByRole('dialog', { name: 'Unsaved changes' })).toBeTruthy();
      fireEvent.click(screen.getByRole('button', { name: 'Stay' }));
      expect(window.location.pathname).toBe('/settings/user-workspace');
      expect(control.value).toBe('branch');

      fireEvent.click(link);
      fireEvent.click(screen.getByRole('button', { name: 'Discard and leave' }));
      await waitFor(() => expect(window.location.pathname).toBe('/settings/operations'));
    });

    it('stops guarding departure after a successful save', async () => {
      fetchSpy.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        if (url === '/api/v1/settings/workspace' && init?.method === 'PATCH') {
          return Promise.resolve(ok({ applied: ['workflow.default_publish_mode'] }));
        }
        return Promise.resolve(settingsFetch(url) ?? notFound());
      });

      renderUserWorkspace();
      const control = (await screen.findByLabelText('Default Publish Mode')) as HTMLSelectElement;
      fireEvent.change(control, { target: { value: 'branch' } });

      fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
      await screen.findByText('Settings saved.');
      // Flush passive effects so the draft guard has observed the cleared
      // draft, as it has by the time a real user can click a link.
      await act(async () => undefined);

      const link = departureLink();
      // The guard lets the navigation through untouched once the draft is
      // saved: no dialog and no cancelled click.
      expect(fireEvent.click(link)).toBe(true);
      expect(screen.queryByRole('dialog', { name: 'Unsaved changes' })).toBeNull();
    });
  });

  describe('architecture guards', () => {
    it.each([
      ['Providers & Secrets', ProvidersSecretsSettingsPage, 'settings-providers-secrets'],
      ['User / Workspace', UserWorkspaceSettingsPage, 'settings-user-workspace'],
      ['Operations', OperationsSettingsPage, 'settings-operations'],
    ])(
      'never re-exposes the three destinations as local tabs or radios on %s',
      async (heading, Page, page) => {
        window.history.replaceState({}, '', `/${page.replace('settings-', 'settings/')}`);
        renderWithClient(
          <BrowserRouter>
            <Page
              payload={{
                page,
                apiBase: '/api',
                initialData: {
                  settingsPermissions: ALL_SETTINGS_PERMISSIONS,
                  workerPause: {
                    get: '/api/system/worker-pause',
                    post: '/api/system/worker-pause',
                    shardHealth: '/api/v1/operations/codex/shards',
                  },
                },
              } as BootPayload}
            />
          </BrowserRouter>,
        );
        await screen.findByRole('heading', { name: heading });

        const destinationLabels = DASHBOARD_DESTINATION_GROUPS.flatMap((group) =>
          group.destinationKeys.map(
            (key) => DASHBOARD_DESTINATIONS.find((destination) => destination.key === key)!.label,
          ),
        );
        expect(destinationLabels).toEqual([
          'Providers & Secrets',
          'User / Workspace',
          'Operations',
        ]);

        // No local switcher — tab, radio, segmented control, pill, card, or
        // sidebar — whose options are the sibling Configuration destinations.
        const switcherSelector =
          '[role="tab"], [role="tablist"], [role="radiogroup"], .segmented-control, .segmented-control-field';
        for (const switcher of Array.from(document.querySelectorAll<HTMLElement>(switcherSelector))) {
          const containedDestinations = destinationLabels.filter((label) =>
            (switcher.textContent ?? '').includes(label),
          );
          expect(containedDestinations).toEqual([]);
        }
        for (const label of destinationLabels) {
          for (const candidate of Array.from(
            document.querySelectorAll<HTMLElement>('input, button, a, [role]'),
          )) {
            const accessibleText = (
              candidate.getAttribute('aria-label') ??
              candidate.textContent ??
              ''
            ).trim();
            if (accessibleText !== label) continue;
            expect(candidate.getAttribute('type')).not.toBe('radio');
            expect(candidate.getAttribute('role')).not.toBe('radio');
            expect(candidate.getAttribute('role')).not.toBe('tab');
            expect(candidate.closest(switcherSelector)).toBeNull();
          }
        }
      },
    );

    it('retains no Settings destination section-state helper in frontend source', () => {
      // Standalone identifiers only: `GeneratedSettingsSection` and
      // `OperationsSettingsSection` are page-owned components, not the removed
      // cross-destination section state.
      const forbiddenSymbols = [
        /\bactiveSection\b/,
        /\bsetActiveSection\b/,
        /\bsectionState\b/,
        /\bSETTINGS_SECTIONS?\b/,
        /\bSettingsSection(Id|Key)?\b/,
        /\bsettingsSectionFor[A-Za-z]*\b/,
        /\bresolveSettingsSection[A-Za-z]*\b/,
      ];
      const offenders: string[] = [];
      for (const file of frontendSourceFiles(join(process.cwd(), 'frontend', 'src'))) {
        const source = readFileSync(file, 'utf8');
        for (const symbol of forbiddenSymbols) {
          const match = source.match(symbol);
          if (match) {
            offenders.push(`${file}: ${match[0]}`);
          }
        }
      }
      expect(offenders).toEqual([]);
    });

    it('builds no ?section= Settings destination link in frontend source', () => {
      // The generated-settings catalog request legitimately carries a backend
      // `section` query parameter; Settings *destination* links must not.
      const settingsDestinationLink =
        /\/settings(\/(providers-secrets|user-workspace|operations))?\?[^\s'"`]*section=/g;
      const offenders: string[] = [];
      for (const file of frontendSourceFiles(join(process.cwd(), 'frontend', 'src'))) {
        const source = readFileSync(file, 'utf8');
        for (const match of source.matchAll(settingsDestinationLink)) {
          offenders.push(`${file}: ${match[0]}`);
        }
      }
      expect(offenders).toEqual([]);
    });

    it('keeps the shared segmented-control system owned by a non-Settings surface', () => {
      const owners = frontendSourceFiles(join(process.cwd(), 'frontend', 'src')).filter((file) =>
        readFileSync(file, 'utf8').includes('"segmented-control'),
      );
      expect(owners.length).toBeGreaterThan(0);
      expect(owners.every((file) => !file.includes(join('components', 'settings')))).toBe(true);
      expect(owners.every((file) => !file.endsWith(join('entrypoints', 'settings.tsx')))).toBe(true);
    });
  });

  describe('documented focused command', () => {
    it('exposes one focused npm command that selects both conformance files', () => {
      const packageJson = JSON.parse(
        readFileSync(join(process.cwd(), 'package.json'), 'utf8'),
      ) as { scripts?: Record<string, string> };
      const command = packageJson.scripts?.['ui:test:settings-redesign'];
      expect(command).toBeTruthy();
      // The vitest filter must select this file and the Provider Profile
      // conformance file, and nothing else.
      const filter = command?.split('--ui-args ')[1]?.trim() ?? '';
      expect(filter).toBeTruthy();
      const selected = frontendSourceTestFiles(join(process.cwd(), 'frontend', 'src')).filter(
        (file) => new RegExp(filter).test(file),
      );
      expect(selected.map((file) => file.split('/').pop()).sort()).toEqual([
        'providerProfileRedesignConformance.test.tsx',
        'settingsRedesignConformance.test.tsx',
      ]);
    });
  });
});
