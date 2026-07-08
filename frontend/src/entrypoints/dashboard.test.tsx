import {
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
  type MockInstance,
} from 'vitest';
import postcss from 'postcss';
import type { Root, Rule } from 'postcss';

import type { BootPayload } from '../boot/parseBootPayload';
import { fireEvent, renderWithClient, screen, waitFor } from '../utils/test-utils';
import { DashboardApp } from './dashboard-app';
import { navigateTo } from '../lib/navigation';
import { readDashboardPreferences, updateDashboardPreferences } from '../utils/dashboardPreferences';

const animatedNavIconMocks = vi.hoisted(() => ({
  moonStart: vi.fn(),
  moonStop: vi.fn(),
  rocketStart: vi.fn(),
  rocketStop: vi.fn(),
  settingsStart: vi.fn(),
  settingsStop: vi.fn(),
  sparklesStart: vi.fn(),
  sparklesStop: vi.fn(),
}));

function normalizeCssSelector(selector: string): string {
  return selector
    .replace(/\s*\{\s*$/, '')
    .trim()
    .replace(/\s+/g, ' ');
}

const parsedCssCache = new Map<string, Root>();

function parsedCssRoot(css: string): Root {
  const cachedRoot = parsedCssCache.get(css);
  if (cachedRoot) {
    return cachedRoot;
  }

  const root = postcss.parse(css);
  parsedCssCache.set(css, root);
  return root;
}

function cssRuleBlocks(css: string, selector: string): string[] {
  const expectedSelector = normalizeCssSelector(selector);
  const expectedSelectors = selector.split(',').map(normalizeCssSelector);
  const blocks: string[] = [];
  parsedCssRoot(css).walkRules((rule) => {
    const ruleSelector = normalizeCssSelector(rule.selector);
    const ruleSelectors = rule.selector.split(',').map(normalizeCssSelector);
    if (
      ruleSelector === expectedSelector ||
        ruleSelectors.includes(expectedSelector) ||
        expectedSelectors.every((expected) => ruleSelectors.includes(expected))
    ) {
      blocks.push(rule.nodes.map((node) => `${node.toString()};`).join('\n'));
    }
  });
  return blocks;
}

function cssRuleBlock(css: string, selector: string): string {
  return cssRuleBlocks(css, selector)[0] ?? '';
}

function cssRuleBlockMatching(css: string, matches: (rule: Rule) => boolean): string {
  let block = '';
  parsedCssRoot(css).walkRules((rule) => {
    if (!block && matches(rule)) {
      block = rule.nodes.map((node) => `${node.toString()};`).join('\n');
    }
  });
  return block;
}

vi.mock('@xterm/xterm', () => {
  class MockTerminal {
    cols = 80;
    rows = 24;
    private element: HTMLElement | null = null;
    constructor(_options?: unknown) {}
    loadAddon(_addon: unknown) {}
    open(element: HTMLElement) {
      this.element = element;
      element.setAttribute('data-testid', 'oauth-xterm');
    }
    write(data: string) {
      if (this.element) {
        this.element.textContent = `${this.element.textContent ?? ''}${data}`;
      }
    }
    writeln(data: string) {
      this.write(`${data}\n`);
    }
    onData(_callback: (data: string) => void) {
      return { dispose: vi.fn() };
    }
    getSelection() {
      return this.element?.textContent ?? '';
    }
    dispose() {}
  }

  return { Terminal: MockTerminal };
});

vi.mock('@xterm/addon-fit', () => ({
  FitAddon: class MockFitAddon {
    fit() {}
  },
}));

vi.mock('lucide-animated', async () => {
  const React = await vi.importActual<typeof import('react')>('react');
  type AnimatedIconProps = {
    'aria-hidden'?: boolean | 'true' | 'false';
    animateOnHover?: boolean;
    className?: string;
    size?: number;
  };

  function createAnimatedIcon(
    testId: string,
    startAnimation: () => void,
    stopAnimation: () => void,
  ) {
    return React.forwardRef<unknown, AnimatedIconProps>((props, ref) => {
      React.useImperativeHandle(ref, () => ({
        startAnimation,
        stopAnimation,
      }));
      return React.createElement('svg', {
        'aria-hidden': props['aria-hidden'],
        className: props.className,
        'data-animate-on-hover': String(props.animateOnHover),
        'data-size': props.size,
        'data-testid': testId,
      });
    });
  }

  return {
    MoonIcon: createAnimatedIcon(
      'animated-nav-icon-schedules',
      animatedNavIconMocks.moonStart,
      animatedNavIconMocks.moonStop,
    ),
    RocketIcon: createAnimatedIcon(
      'animated-nav-icon-create',
      animatedNavIconMocks.rocketStart,
      animatedNavIconMocks.rocketStop,
    ),
    SettingsIcon: createAnimatedIcon(
      'animated-nav-icon-settings',
      animatedNavIconMocks.settingsStart,
      animatedNavIconMocks.settingsStop,
    ),
    SparklesIcon: createAnimatedIcon(
      'animated-nav-icon-skills',
      animatedNavIconMocks.sparklesStart,
      animatedNavIconMocks.sparklesStop,
    ),
  };
});

// MM-960: simulate a transient dynamic-import (chunk-load) failure for one page
// so we can assert the route boundary's Retry recreates the lazy import instead
// of replaying React.lazy's cached rejection. The factory rejects the first time
// it is evaluated and resolves to a real component afterward.
const skillsImport = vi.hoisted(() => ({ attempts: 0 }));
vi.mock('./skills', () => {
  skillsImport.attempts += 1;
  if (skillsImport.attempts === 1) {
    throw new Error('Failed to fetch dynamically imported module: skills');
  }
  return { default: () => <div>Skills page recovered</div> };
});

vi.mock('./workflow-list', () => ({
  default: () => <div>Workflow list route loaded</div>,
}));

vi.mock('./workflows-workspace', () => ({
  default: ({ payload }: { payload: BootPayload }) => {
    const isDetailRoute =
      window.location.pathname.startsWith('/workflows/') && window.location.pathname !== '/workflows/new';
    const isCreateRoute = window.location.pathname === '/workflows/new';
    const initialData = payload.initialData as { dashboardConfig?: Record<string, unknown> } | undefined;
    const repository = initialData?.dashboardConfig?.defaultRepository;
    return (
      <div data-testid="workflows-workspace-route">
        <a href="/workflows/mm%3A97d44980-355c-4300-96a7-0ad166440d95?source=temporal">
          Mock workflow title
        </a>
        {isDetailRoute ? (
          <div>Workflow detail route loaded: {window.location.pathname}</div>
        ) : isCreateRoute ? (
          <>
            <div>Workflow start route loaded</div>
            <div>Workflow start default repository: {typeof repository === 'string' ? repository : 'none'}</div>
          </>
        ) : (
          <div>Workflow list route loaded</div>
        )}
      </div>
    );
  },
}));

const workflowDetailMock = vi.hoisted(() => ({
  initialPathByNode: new WeakMap<HTMLElement, string>(),
}));

vi.mock('./workflow-detail', () => {
  return {
    default: () => (
      <div
        ref={(node) => {
          if (!node) {
            return;
          }
          if (!workflowDetailMock.initialPathByNode.has(node)) {
            workflowDetailMock.initialPathByNode.set(node, window.location.pathname);
          }
          node.textContent = `Workflow detail initial path: ${workflowDetailMock.initialPathByNode.get(node)}`;
        }}
      />
    ),
  };
});

vi.mock('./workflow-start', () => ({
  default: ({ payload }: { payload: BootPayload }) => {
    const initialData = payload.initialData as {
      dashboardConfig?: Record<string, unknown>;
      workflowListDisplayMode?: unknown;
    } | undefined;
    const repository = initialData?.dashboardConfig?.defaultRepository;
    return (
      <>
        <div>Workflow start route loaded</div>
        <div>Workflow start default repository: {typeof repository === 'string' ? repository : 'none'}</div>
        <div>Workflow start list display: {String(initialData?.workflowListDisplayMode ?? 'unset')}</div>
      </>
    );
  },
}));

vi.mock('./settings', () => ({
  default: ({ payload }: { payload: BootPayload }) => {
    const initialData = payload.initialData as { settingsPermissions?: string[] } | undefined;
    return <div>Settings permissions: {(initialData?.settingsPermissions ?? []).join(',')}</div>;
  },
}));

function uiInfo(overrides: Record<string, unknown> = {}) {
  return {
    app: 'moonmind',
    buildId: 'test-build',
    apiBase: '/api',
    features: { workflowLiveUpdates: true },
    limits: {},
    endpoints: {
      workflows: '/api/executions',
      workflowUpdatesPoll: '/api/executions',
      workflowUpdatesStream: '/api/workflows/updates/stream',
    },
    dashboardConfig: {
      initialPath: '/workflows/new',
      pollIntervalsMs: { list: 60_000, detail: 60_000, events: 60_000 },
    },
    settingsPermissions: [],
    workerPause: {
      get: '/api/system/worker-pause',
      post: '/api/system/worker-pause',
      shardHealth: '/api/workflows/codex/shards',
    },
    ...overrides,
  };
}

describe('Dashboard shared entry', () => {
  let fetchSpy: MockInstance;
  let dashboardCss: string;
  const originalWebSocket = window.WebSocket;

  beforeAll(async () => {
    const { readFileSync } = await import('node:fs');
    dashboardCss = readFileSync(
      `${process.cwd()}/frontend/src/styles/dashboard.css`,
      'utf8',
    );
  });

  beforeEach(() => {
    Object.values(animatedNavIconMocks).forEach((mock) => mock.mockClear());
    fetchSpy = vi.spyOn(window, 'fetch').mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/ui/info') {
        return Promise.resolve({
          ok: true,
          json: async () => uiInfo(),
        } as Response);
      }
      if (url === '/api/v1/secrets') {
        return Promise.resolve({
          ok: true,
          json: async () => ({ items: [] }),
        } as Response);
      }
      if (url === '/api/v1/provider-profiles') {
        return Promise.resolve({
          ok: true,
          json: async () => [],
        } as Response);
      }
      return Promise.resolve({
        ok: false,
        status: 404,
        statusText: 'Not Found',
        text: async () => 'Unhandled fetch',
      } as Response);
    });
  });

  afterEach(() => {
    fetchSpy.mockRestore();
    window.WebSocket = originalWebSocket;
    document.querySelectorAll('[data-nav]').forEach((node) => node.remove());
    window.localStorage.clear();
    window.history.replaceState({}, '', '/');
  });

  it('MM-1107 animates lucide nav icons from the whole route link hover', async () => {
    window.history.replaceState({}, '', '/workflows');
    renderWithClient(<DashboardApp payload={{ page: 'dashboard', apiBase: '/api' }} />);

    await screen.findByText('Workflow list route loaded', {}, { timeout: 10000 });

    const workflowsLink = screen.getByRole('link', { name: 'Workflows' });
    expect(workflowsLink.querySelector('[data-testid^="animated-nav-icon-"]')).toBeNull();

    const createLink = screen.getByRole('link', { name: 'Create' });
    const createIcon = screen.getByTestId('animated-nav-icon-create');
    expect(createIcon.getAttribute('data-animate-on-hover')).toBe('false');

    fireEvent.mouseEnter(createLink);
    expect(animatedNavIconMocks.rocketStart).toHaveBeenCalledTimes(1);

    fireEvent.mouseLeave(createLink);
    expect(animatedNavIconMocks.rocketStop).toHaveBeenCalledTimes(1);

    const schedulesLink = screen.getByRole('link', { name: 'Recurring' });
    fireEvent.mouseEnter(schedulesLink);
    expect(animatedNavIconMocks.moonStart).toHaveBeenCalledTimes(1);
    fireEvent.mouseLeave(schedulesLink);
    expect(animatedNavIconMocks.moonStop).toHaveBeenCalledTimes(1);

    const skillsLink = screen.getByRole('link', { name: 'Skills' });
    fireEvent.mouseEnter(skillsLink);
    expect(animatedNavIconMocks.sparklesStart).toHaveBeenCalledTimes(1);
    fireEvent.mouseLeave(skillsLink);
    expect(animatedNavIconMocks.sparklesStop).toHaveBeenCalledTimes(1);

    const settingsLink = screen.getByRole('link', { name: 'Settings' });
    fireEvent.mouseEnter(settingsLink);
    expect(animatedNavIconMocks.settingsStart).toHaveBeenCalledTimes(1);
    fireEvent.mouseLeave(settingsLink);
    expect(animatedNavIconMocks.settingsStop).toHaveBeenCalledTimes(1);
  });

  it('renders dashboard alerts and lazy-loads the requested page component', async () => {
    window.history.replaceState({}, '', '/workflows');
    const payload: BootPayload = {
      page: 'dashboard',
      apiBase: '/api',
      initialData: {
        layout: {
          dataWidePanel: true,
        },
      },
    };

    renderWithClient(<DashboardApp payload={payload} />);

    expect(await screen.findByText('Workflow list route loaded', {}, { timeout: 10000 })).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Workflows' }).getAttribute('href')).toBe('/workflows');
    expect(document.querySelectorAll('.route-nav-icon')).toHaveLength(5);
    expect(screen.getByText('vtest-build')).toBeTruthy();
    expect(screen.queryByLabelText('Operational metrics')).toBeNull();
    expect(fetchSpy.mock.calls.some(([url]) => String(url).startsWith('/api/executions/metrics'))).toBe(false);
    await waitFor(() => {
      expect(document.querySelector('.panel--data-wide')).toBeTruthy();
      expect(document.querySelector('.dashboard-shell-constrained--data-wide')).toBeTruthy();
    });
  });

  it('renders one workflow list display radio group on covered workflow surfaces', async () => {
    window.history.replaceState({}, '', '/workflows');
    renderWithClient(<DashboardApp payload={{ page: 'dashboard', apiBase: '/api' }} />);

    await screen.findByText('Workflow list route loaded', {}, { timeout: 10000 });

    const group = screen.getByRole('radiogroup', { name: 'Workflow list display' });
    expect(group).toBeTruthy();
    expect(screen.getByRole('button', { name: 'No list' }).getAttribute('aria-pressed')).toBe('false');
    expect(screen.getByRole('button', { name: 'Sidebar list' }).getAttribute('aria-pressed')).toBe('false');
    expect(screen.getByRole('button', { name: 'Full screen table' }).getAttribute('aria-pressed')).toBe('true');
    expect(screen.queryByRole('button', { name: 'Close sidebar' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Open workflow sidebar' })).toBeNull();
    expect(screen.queryByRole('link', { name: 'Expand to full list' })).toBeNull();
  });

  it('opens a visible workflow when sidebar mode is selected from the workflows table', async () => {
    window.history.replaceState({}, '', '/workflows?source=temporal');
    renderWithClient(<DashboardApp payload={{ page: 'dashboard', apiBase: '/api' }} />);

    await screen.findByText('Workflow list route loaded', {}, { timeout: 10000 });
    fireEvent.click(screen.getByRole('button', { name: 'Sidebar list' }));

    await waitFor(() => {
      expect(window.location.pathname).toBe('/workflows/mm%3A97d44980-355c-4300-96a7-0ad166440d95');
    });
    expect(await screen.findByText(/Workflow detail route loaded:/)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Sidebar list' }).getAttribute('aria-pressed')).toBe('true');
  });

  it('renders the Recurring list display selector on schedule routes and opens the first schedule in sidebar mode', async () => {
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/ui/info') {
        return Promise.resolve({ ok: true, json: async () => uiInfo() } as Response);
      }
      if (url === '/api/recurring-tasks?scope=personal') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            items: [{
              id: 'schedule-one',
              name: 'Daily recurring scan',
              enabled: true,
              cron: '0 9 * * *',
              timezone: 'UTC',
              nextRunAt: '2026-07-09T09:00:00Z',
              lastDispatchStatus: 'enqueued',
              target: {},
              policy: {},
            }],
          }),
        } as Response);
      }
      if (url === '/api/recurring-workflows/schedule-one') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            id: 'schedule-one',
            name: 'Daily recurring scan',
            description: 'Runs every morning.',
            enabled: true,
            cron: '0 9 * * *',
            timezone: 'UTC',
            nextRunAt: '2026-07-09T09:00:00Z',
            lastScheduledFor: '2026-07-08T09:00:00Z',
            lastDispatchStatus: 'enqueued',
            target: {},
            policy: {},
          }),
        } as Response);
      }
      if (url === '/api/recurring-workflows/schedule-one/runs?limit=200') {
        return Promise.resolve({ ok: true, json: async () => ({ items: [] }) } as Response);
      }
      return Promise.resolve({ ok: false, status: 404, statusText: 'Not Found' } as Response);
    });

    window.history.replaceState({}, '', '/schedules');
    renderWithClient(<DashboardApp payload={{ page: 'dashboard', apiBase: '/api' }} />);

    expect(await screen.findByRole('heading', { name: 'Recurring Schedules' })).toBeTruthy();
    expect(screen.getByRole('radiogroup', { name: 'Recurring list display' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Full screen table' }).getAttribute('aria-pressed')).toBe('true');

    fireEvent.click(screen.getByRole('button', { name: 'Sidebar list' }));

    await waitFor(() => {
      expect(window.location.pathname).toBe('/schedules/schedule-one');
    });
    expect(await screen.findByRole('heading', { name: 'Daily recurring scan' })).toBeTruthy();
    expect(screen.getByRole('complementary', { name: 'Recurring schedule navigation' })).toBeTruthy();
    expect(screen.getByRole('link', { name: /Daily recurring scan/ }).getAttribute('aria-current')).toBe('page');
    expect(screen.getByRole('button', { name: 'Sidebar list' }).getAttribute('aria-pressed')).toBe('true');
  });

  it('switches Recurring detail between full table and hidden-list modes', async () => {
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/ui/info') {
        return Promise.resolve({ ok: true, json: async () => uiInfo() } as Response);
      }
      if (url === '/api/recurring-tasks?scope=personal') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            items: [{
              id: 'schedule-one',
              name: 'Daily recurring scan',
              enabled: true,
              cron: '0 9 * * *',
              timezone: 'UTC',
              nextRunAt: '2026-07-09T09:00:00Z',
              lastDispatchStatus: 'enqueued',
              target: {},
              policy: {},
            }],
          }),
        } as Response);
      }
      if (url === '/api/recurring-workflows/schedule-one') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            id: 'schedule-one',
            name: 'Daily recurring scan',
            description: 'Runs every morning.',
            enabled: true,
            cron: '0 9 * * *',
            timezone: 'UTC',
            nextRunAt: '2026-07-09T09:00:00Z',
            lastScheduledFor: '2026-07-08T09:00:00Z',
            lastDispatchStatus: 'enqueued',
            target: {},
            policy: {},
          }),
        } as Response);
      }
      if (url === '/api/recurring-workflows/schedule-one/runs?limit=200') {
        return Promise.resolve({ ok: true, json: async () => ({ items: [] }) } as Response);
      }
      return Promise.resolve({ ok: false, status: 404, statusText: 'Not Found' } as Response);
    });

    window.history.replaceState({}, '', '/schedules/schedule-one');
    renderWithClient(<DashboardApp payload={{ page: 'dashboard', apiBase: '/api' }} />);

    expect(await screen.findByRole('heading', { name: 'Daily recurring scan' })).toBeTruthy();
    expect(screen.getByRole('complementary', { name: 'Recurring schedule navigation' })).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'No list' }));
    expect(window.location.pathname).toBe('/schedules/schedule-one');
    await waitFor(() => {
      expect(screen.queryByRole('complementary', { name: 'Recurring schedule navigation' })).toBeNull();
    });
    expect(screen.getByRole('button', { name: 'No list' }).getAttribute('aria-pressed')).toBe('true');

    fireEvent.click(screen.getByRole('button', { name: 'Full screen table' }));
    await waitFor(() => {
      expect(window.location.pathname).toBe('/schedules');
    });
    expect(await screen.findByRole('heading', { name: 'Recurring Schedules' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Full screen table' }).getAttribute('aria-pressed')).toBe('true');
  });

  it('hides the workflow list display control on unsupported routes', async () => {
    window.history.replaceState({}, '', '/settings');
    renderWithClient(<DashboardApp payload={{ page: 'dashboard', apiBase: '/api' }} />);

    await screen.findByText('Settings permissions:');

    expect(screen.queryByRole('radiogroup', { name: 'Workflow list display' })).toBeNull();
  });

  it('does not register a dashboard proposal review page for MM-859', async () => {
    window.history.replaceState({}, '', '/proposals');
    renderWithClient(<DashboardApp payload={{ page: 'dashboard', apiBase: '/api' }} />);

    expect(await screen.findByText('Unknown dashboard page:')).toBeTruthy();
    expect(screen.getByText('/proposals')).toBeTruthy();
    expect(fetchSpy.mock.calls.some(([url]) => String(url).startsWith('/api/proposals'))).toBe(false);
  });

  it('normalizes stale boot layout data from the client route table (MM-960)', async () => {
    window.history.replaceState({}, '', '/workflows');
    renderWithClient(
      <DashboardApp
        payload={{
          page: 'dashboard',
          apiBase: '/api',
          initialData: { layout: { dataWidePanel: 'wide' } },
        } as unknown as BootPayload}
      />,
    );

    expect(await screen.findByText('Workflow list route loaded')).toBeTruthy();
    expect(document.querySelector('.panel--data-wide')).toBeTruthy();
    expect(screen.queryByText('Dashboard configuration error')).toBeNull();
  });

  it('routes percent-encoded workflow detail IDs through the shared shell', async () => {
    const encodedPath = '/workflows/mm%3A97d44980-355c-4300-96a7-0ad166440d95';
    window.history.replaceState({}, '', encodedPath);

    renderWithClient(<DashboardApp payload={{ page: 'dashboard', apiBase: '/api' }} />);

    expect(
      await screen.findByText(`Workflow detail route loaded: ${encodedPath}`),
    ).toBeTruthy();
    expect(screen.queryByText(/Unknown dashboard page:/i)).toBeNull();
    expect(document.querySelector('.panel--data-wide')).toBeTruthy();
  });

  it('recovers from a failed lazy page import when the user retries (MM-960)', async () => {
    const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    try {
      window.history.replaceState({}, '', '/skills');
      renderWithClient(<DashboardApp payload={{ page: 'dashboard', apiBase: '/api' }} />);

      // First dynamic import rejects -> styled route error with a Retry action.
      expect(await screen.findByText('This page failed to load')).toBeTruthy();
      expect(screen.queryByText('Skills page recovered')).toBeNull();

      // Retry must recreate the lazy import (a cached rejection would re-throw).
      fireEvent.click(screen.getByRole('button', { name: 'Retry' }));

      expect(await screen.findByText('Skills page recovered')).toBeTruthy();
      expect(screen.queryByText('This page failed to load')).toBeNull();
      expect(skillsImport.attempts).toBeGreaterThanOrEqual(2);
    } finally {
      consoleErrorSpy.mockRestore();
    }
  });

  it('MM-1029 intercepts dashboard links and changes routes without a document navigation', async () => {
    window.history.replaceState({}, '', '/workflows/new');

    renderWithClient(<DashboardApp payload={{ page: 'dashboard', apiBase: '/api' }} />);

    expect(await screen.findByText('Workflow start route loaded')).toBeTruthy();
    const shellPanel = document.querySelector('.panel');
    const startLink = screen.getByRole('link', { name: 'Create' });
    const workflowsLink = screen.getByRole('link', { name: 'Workflows' });
    expect(startLink.getAttribute('aria-current')).toBe('page');
    expect(workflowsLink.getAttribute('aria-current')).toBeNull();
    expect(startLink.classList.contains('active')).toBe(true);
    expect(workflowsLink.classList.contains('active')).toBe(false);

    fireEvent.click(workflowsLink);

    expect(await screen.findByText('Workflow list route loaded')).toBeTruthy();
    expect(document.querySelector('.panel')).toBe(shellPanel);
    expect(window.location.pathname).toBe('/workflows');
    expect(screen.getByRole('link', { name: 'Workflows' }).getAttribute('aria-current')).toBe('page');
    expect(screen.getByRole('link', { name: 'Workflows' }).classList.contains('active')).toBe(true);
  });

  it('waits for UI info before mounting the workflow start route', async () => {
    window.history.replaceState({}, '', '/workflows/new');
    let resolveUiInfo: (response: Response) => void = () => {};
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/ui/info') {
        return new Promise<Response>((resolve) => {
          resolveUiInfo = resolve;
        });
      }
      return Promise.resolve({
        ok: false,
        status: 404,
        statusText: 'Not Found',
        text: async () => 'Unhandled fetch',
      } as Response);
    });

    renderWithClient(<DashboardApp payload={{ page: 'dashboard', apiBase: '/api' }} />);

    expect(await screen.findByText('Loading MoonMind...')).toBeTruthy();
    expect(screen.queryByText('Workflow start route loaded')).toBeNull();

    resolveUiInfo({
      ok: true,
      json: async () => uiInfo({ dashboardConfig: { defaultRepository: 'MoonLadderStudios/MoonMind' } }),
    } as Response);

    expect(await screen.findByText('Workflow start route loaded')).toBeTruthy();
    expect(screen.getByText('Workflow start default repository: MoonLadderStudios/MoonMind')).toBeTruthy();
  });

  it('MM-1121 keeps sidebar mode on the Create route', async () => {
    window.history.replaceState({}, '', '/workflows/new?source=temporal');
    renderWithClient(<DashboardApp payload={{ page: 'dashboard', apiBase: '/api' }} />);

    expect(await screen.findByText('Workflow start route loaded')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Sidebar list' }).getAttribute('aria-pressed')).toBe('true');
    await waitFor(() => {
      expect(document.querySelector('.panel--data-wide')).toBeTruthy();
    });

    fireEvent.click(screen.getByRole('button', { name: 'No list' }));
    expect(screen.getByRole('button', { name: 'No list' }).getAttribute('aria-pressed')).toBe('true');
    expect(window.location.pathname).toBe('/workflows/new');

    fireEvent.click(screen.getByRole('button', { name: 'Sidebar list' }));
    expect(screen.getByRole('button', { name: 'Sidebar list' }).getAttribute('aria-pressed')).toBe('true');
    expect(window.location.pathname).toBe('/workflows/new');
    expect(window.location.search).toBe('?source=temporal');
  });

  it('MM-1029 navigateTo uses the SPA route event for dashboard-internal URLs', async () => {
    window.history.replaceState({}, '', '/workflows/new');
    renderWithClient(<DashboardApp payload={{ page: 'dashboard', apiBase: '/api' }} />);

    expect(await screen.findByText('Workflow start route loaded')).toBeTruthy();

    navigateTo('/workflows?source=temporal');

    expect(await screen.findByText('Workflow list route loaded')).toBeTruthy();
    expect(window.location.pathname).toBe('/workflows');
    expect(window.location.search).toBe('?source=temporal');
  });

  it('MM-1061 keeps the workflows workspace parent mounted across list-to-detail SPA navigation', async () => {
    window.history.replaceState({}, '', '/workflows?source=temporal');
    renderWithClient(<DashboardApp payload={{ page: 'dashboard', apiBase: '/api' }} />);

    expect(await screen.findByText('Workflow list route loaded')).toBeTruthy();
    const workspace = screen.getByTestId('workflows-workspace-route');

    fireEvent.click(screen.getByRole('link', { name: 'Mock workflow title' }));

    expect(
      await screen.findByText(
        'Workflow detail route loaded: /workflows/mm%3A97d44980-355c-4300-96a7-0ad166440d95',
      ),
    ).toBeTruthy();
    expect(screen.getByTestId('workflows-workspace-route')).toBe(workspace);
    expect(window.location.pathname).toBe('/workflows/mm%3A97d44980-355c-4300-96a7-0ad166440d95');
    expect(window.location.search).toBe('?source=temporal');
  });

  it('MM-1029 loads Settings permissions during SPA navigation', async () => {
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/ui/info') {
        return Promise.resolve({
          ok: true,
          json: async () => uiInfo({
            settingsPermissions: ['provider_profiles.write', 'settings.effective.read'],
          }),
        } as Response);
      }
      return Promise.resolve({
        ok: false,
        status: 404,
        statusText: 'Not Found',
        text: async () => 'Unhandled fetch',
      } as Response);
    });
    window.history.replaceState({}, '', '/workflows');

    renderWithClient(<DashboardApp payload={{ page: 'dashboard', apiBase: '/api' }} />);

    expect(await screen.findByText('Workflow list route loaded')).toBeTruthy();
    fireEvent.click(screen.getByRole('link', { name: 'Settings' }));

    expect(
      await screen.findByText('Settings permissions: provider_profiles.write,settings.effective.read'),
    ).toBeTruthy();
    expect(fetchSpy).toHaveBeenCalledWith(
      '/api/ui/info',
      expect.objectContaining({ credentials: 'same-origin' }),
    );
  });

  it('MM-1061 updates workflow detail routes inside the shared workspace component', async () => {
    window.history.replaceState({}, '', '/workflows/first/debug');

    renderWithClient(<DashboardApp payload={{ page: 'dashboard', apiBase: '/api' }} />);

    expect(await screen.findByText('Workflow detail route loaded: /workflows/first/debug')).toBeTruthy();

    navigateTo('/workflows/second');

    expect(await screen.findByText('Workflow detail route loaded: /workflows/second')).toBeTruthy();
    expect(screen.queryByText('Workflow detail route loaded: /workflows/first/debug')).toBeNull();
  });

  it('MM-1113 opens an authorized remembered workflow when leaving the full table', async () => {
    window.history.replaceState({}, '', '/workflows?stateIn=completed&limit=50');
    updateDashboardPreferences({ lastSelectedWorkflowId: 'remembered-123' });
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/ui/info') {
        return Promise.resolve({ ok: true, json: async () => uiInfo() } as Response);
      }
      if (url === '/api/executions/remembered-123?source=temporal') {
        return Promise.resolve({ ok: true, json: async () => ({ workflowId: 'remembered-123' }) } as Response);
      }
      return Promise.resolve({ ok: false, status: 404, statusText: 'Not Found' } as Response);
    });
    renderWithClient(<DashboardApp payload={{ page: 'dashboard', apiBase: '/api' }} />);

    expect(await screen.findByText('Workflow list route loaded')).toBeTruthy();
    fetchSpy.mockClear();

    fireEvent.click(screen.getByRole('button', { name: 'No list' }));

    await waitFor(() => {
      expect(window.location.pathname).toBe('/workflows/remembered-123');
    });
    expect(window.location.search).toContain('stateIn=completed');
    expect(window.location.search).toContain('limit=50');
    expect(window.location.search).toContain('source=temporal');
    expect(fetchSpy).toHaveBeenCalledWith('/api/executions/remembered-123?source=temporal');
    expect(fetchSpy.mock.calls.some(([url]) => String(url).startsWith('/api/executions?'))).toBe(false);
    expect(readDashboardPreferences().workflowWorkspaceSidebarCollapsed).toBe(true);
  });

  it('MM-1113 keeps a route-selected workflow when switching detail-compatible modes', async () => {
    window.history.replaceState({}, '', '/workflows/route-123?stateIn=failed&limit=10');
    updateDashboardPreferences({ lastSelectedWorkflowId: 'remembered-123' });
    renderWithClient(<DashboardApp payload={{ page: 'dashboard', apiBase: '/api' }} />);

    expect(await screen.findByText('Workflow detail route loaded: /workflows/route-123')).toBeTruthy();
    fetchSpy.mockClear();

    fireEvent.click(screen.getByRole('button', { name: 'No list' }));

    await waitFor(() => {
      expect(readDashboardPreferences().workflowWorkspaceSidebarCollapsed).toBe(true);
    });
    expect(window.location.pathname).toBe('/workflows/route-123');
    expect(window.location.search).toBe('?stateIn=failed&limit=10');
    expect(fetchSpy.mock.calls.some(([url]) => String(url).startsWith('/api/executions/remembered-123'))).toBe(false);
    expect(fetchSpy.mock.calls.some(([url]) => String(url).startsWith('/api/executions?'))).toBe(false);
  });

  it('MM-1113 resolves the first row from the exact current list query context', async () => {
    window.history.replaceState(
      {},
      '',
      '/workflows?stateIn=completed&source=jules&limit=50&nextPageToken=cursor-1&repoIn=MoonLadderStudios%2FMoonMind&targetRuntime=codex_cli&sort=updatedAt',
    );
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/ui/info') {
        return Promise.resolve({ ok: true, json: async () => uiInfo() } as Response);
      }
      if (
        url ===
        '/api/executions?source=jules&pageSize=50&stateIn=completed&nextPageToken=cursor-1&repoIn=MoonLadderStudios%2FMoonMind&targetRuntime=codex_cli'
      ) {
        return Promise.resolve({
          ok: true,
          json: async () => ({ items: [{ workflowId: 'first-query-row', title: 'First query row' }] }),
        } as Response);
      }
      return Promise.resolve({ ok: false, status: 404, statusText: 'Not Found' } as Response);
    });
    renderWithClient(<DashboardApp payload={{ page: 'dashboard', apiBase: '/api' }} />);

    expect(await screen.findByText('Workflow list route loaded')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Sidebar list' }));

    await waitFor(() => {
      expect(window.location.pathname).toBe('/workflows/first-query-row');
    });
    expect(fetchSpy).toHaveBeenCalledWith(
      '/api/executions?source=jules&pageSize=50&stateIn=completed&nextPageToken=cursor-1&repoIn=MoonLadderStudios%2FMoonMind&targetRuntime=codex_cli',
    );
    expect(window.location.search).toContain('stateIn=completed');
    expect(window.location.search).toContain('source=jules');
    expect(window.location.search).toContain('limit=50');
    expect(window.location.search).toContain('nextPageToken=cursor-1');
    expect(window.location.search).not.toContain('sort=updatedAt');
  });

  it('MM-1113 ignores unauthorized remembered selections and opens the first visible row', async () => {
    window.history.replaceState({}, '', '/workflows?stateIn=completed&limit=50');
    updateDashboardPreferences({ lastSelectedWorkflowId: 'unauthorized-123' });
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/ui/info') {
        return Promise.resolve({ ok: true, json: async () => uiInfo() } as Response);
      }
      if (url === '/api/executions/unauthorized-123?source=temporal') {
        return Promise.resolve({ ok: false, status: 403, statusText: 'Forbidden' } as Response);
      }
      if (url === '/api/executions?source=temporal&pageSize=50&stateIn=completed') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            items: [{ workflowId: 'visible-456', taskId: 'visible-456', title: 'Visible authorized row' }],
          }),
        } as Response);
      }
      return Promise.resolve({ ok: false, status: 404, statusText: 'Not Found' } as Response);
    });
    renderWithClient(<DashboardApp payload={{ page: 'dashboard', apiBase: '/api' }} />);

    expect(await screen.findByText('Workflow list route loaded')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Sidebar list' }));

    await waitFor(() => {
      expect(window.location.pathname).toBe('/workflows/visible-456');
    });
    expect(window.location.pathname).not.toBe('/workflows/unauthorized-123');
    expect(readDashboardPreferences().workflowWorkspaceSidebarCollapsed).toBe(false);
    expect(readDashboardPreferences().lastSelectedWorkflowId).toBe('unauthorized-123');
  });

  it('MM-1113 never navigates to or renders an unauthorized remembered workflow during fallback', async () => {
    window.history.replaceState({}, '', '/workflows?stateIn=completed');
    updateDashboardPreferences({ lastSelectedWorkflowId: 'secret-remembered' });
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/ui/info') {
        return Promise.resolve({ ok: true, json: async () => uiInfo() } as Response);
      }
      if (url === '/api/executions/secret-remembered?source=temporal') {
        return Promise.resolve({ ok: false, status: 403, statusText: 'Forbidden' } as Response);
      }
      if (url === '/api/executions?source=temporal&pageSize=25&stateIn=completed') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            items: [{ workflowId: 'authorized-row', taskId: 'authorized-row', title: 'Authorized row' }],
          }),
        } as Response);
      }
      return Promise.resolve({ ok: false, status: 404, statusText: 'Not Found' } as Response);
    });
    renderWithClient(<DashboardApp payload={{ page: 'dashboard', apiBase: '/api' }} />);

    expect(await screen.findByText('Workflow list route loaded')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'No list' }));

    await waitFor(() => {
      expect(window.location.pathname).toBe('/workflows/authorized-row');
    });
    expect(window.location.pathname).not.toBe('/workflows/secret-remembered');
    expect(screen.queryByText(/secret-remembered/i)).toBeNull();
    expect(readDashboardPreferences().lastSelectedWorkflowId).toBe('secret-remembered');
  });

  it('MM-1113 selects only returned authorized first-row data', async () => {
    window.history.replaceState({}, '', '/workflows?stateIn=running');
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/ui/info') {
        return Promise.resolve({ ok: true, json: async () => uiInfo() } as Response);
      }
      if (url === '/api/executions?source=temporal&pageSize=25&stateIn=running') {
        return Promise.resolve({
          ok: true,
          json: async () => ({ items: [{ taskId: 'authorized-task-row', title: 'Authorized task row' }] }),
        } as Response);
      }
      return Promise.resolve({ ok: false, status: 404, statusText: 'Not Found' } as Response);
    });
    renderWithClient(<DashboardApp payload={{ page: 'dashboard', apiBase: '/api' }} />);

    expect(await screen.findByText('Workflow list route loaded')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'No list' }));

    await waitFor(() => {
      expect(window.location.pathname).toBe('/workflows/authorized-task-row');
    });
    expect(window.location.pathname).not.toContain('unauthorized');
    expect(screen.queryByText(/unauthorized/i)).toBeNull();
  });

  it('MM-1113 exposes a resolving state while the matching workflow list is loading', async () => {
    window.history.replaceState({}, '', '/workflows?limit=25');
    let resolveList: ((response: Response) => void) | undefined;
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/ui/info') {
        return Promise.resolve({ ok: true, json: async () => uiInfo() } as Response);
      }
      if (url === '/api/executions?source=temporal&pageSize=25') {
        return new Promise<Response>((resolve) => {
          resolveList = resolve;
        });
      }
      return Promise.resolve({ ok: false, status: 404, statusText: 'Not Found' } as Response);
    });
    renderWithClient(<DashboardApp payload={{ page: 'dashboard', apiBase: '/api' }} />);

    expect(await screen.findByText('Workflow list route loaded')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'No list' }));

    expect((await screen.findByRole('status')).textContent).toContain('Opening first workflow...');
    const completeList = resolveList;
    if (!completeList) {
      throw new Error('Expected workflow list request to be pending.');
    }
    completeList({
      ok: true,
      json: async () => ({ items: [{ workflowId: 'first-loading-row' }] }),
    } as Response);
    await waitFor(() => {
      expect(window.location.pathname).toBe('/workflows/first-loading-row');
    });
  });

  it('MM-1113 stays on the table when fallback list resolution fails or has no rows', async () => {
    window.history.replaceState({}, '', '/workflows?stateIn=failed');
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/ui/info') {
        return Promise.resolve({ ok: true, json: async () => uiInfo() } as Response);
      }
      if (url === '/api/executions?source=temporal&pageSize=25&stateIn=failed') {
        return Promise.resolve({ ok: true, json: async () => ({ items: [] }) } as Response);
      }
      return Promise.resolve({ ok: false, status: 404, statusText: 'Not Found' } as Response);
    });
    renderWithClient(<DashboardApp payload={{ page: 'dashboard', apiBase: '/api' }} />);

    expect(await screen.findByText('Workflow list route loaded')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'No list' }));

    await waitFor(() => {
      expect(screen.getByRole('status').textContent).toContain('No workflow to open.');
    });
    expect(window.location.pathname).toBe('/workflows');
  });

  it('MM-1113 treats non-object fallback list responses as empty', async () => {
    window.history.replaceState({}, '', '/workflows?stateIn=completed');
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/ui/info') {
        return Promise.resolve({ ok: true, json: async () => uiInfo() } as Response);
      }
      if (url === '/api/executions?source=temporal&pageSize=25&stateIn=completed') {
        return Promise.resolve({ ok: true, json: async () => null } as Response);
      }
      return Promise.resolve({ ok: false, status: 404, statusText: 'Not Found' } as Response);
    });
    renderWithClient(<DashboardApp payload={{ page: 'dashboard', apiBase: '/api' }} />);

    expect(await screen.findByText('Workflow list route loaded')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'No list' }));

    await waitFor(() => {
      expect(screen.getByRole('status').textContent).toContain('No workflow to open.');
    });
    expect(window.location.pathname).toBe('/workflows');
  });

  it('MM-1113 stays on the table and exposes a recoverable state when fallback list loading fails', async () => {
    window.history.replaceState({}, '', '/workflows?stateIn=failed');
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/ui/info') {
        return Promise.resolve({ ok: true, json: async () => uiInfo() } as Response);
      }
      if (url === '/api/executions?source=temporal&pageSize=25&stateIn=failed') {
        return Promise.resolve({ ok: false, status: 503, statusText: 'Service Unavailable' } as Response);
      }
      return Promise.resolve({ ok: false, status: 404, statusText: 'Not Found' } as Response);
    });
    renderWithClient(<DashboardApp payload={{ page: 'dashboard', apiBase: '/api' }} />);

    expect(await screen.findByText('Workflow list route loaded')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'No list' }));

    await waitFor(() => {
      expect(screen.getByRole('status').textContent).toContain('Workflow list is unavailable.');
    });
    expect(window.location.pathname).toBe('/workflows');
    expect(window.location.search).toBe('?stateIn=failed');
  });

  it('MM-1113 ignores stale fallback navigation after leaving the workflow surface', async () => {
    window.history.replaceState({}, '', '/workflows?stateIn=running');
    let resolveList: ((response: Response) => void) | undefined;
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/ui/info') {
        return Promise.resolve({ ok: true, json: async () => uiInfo() } as Response);
      }
      if (url === '/api/executions?source=temporal&pageSize=25&stateIn=running') {
        return new Promise<Response>((resolve) => {
          resolveList = resolve;
        });
      }
      return Promise.resolve({ ok: false, status: 404, statusText: 'Not Found' } as Response);
    });
    renderWithClient(<DashboardApp payload={{ page: 'dashboard', apiBase: '/api' }} />);

    expect(await screen.findByText('Workflow list route loaded')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'No list' }));
    expect((await screen.findByRole('status')).textContent).toContain('Opening first workflow...');
    fireEvent.click(screen.getByRole('link', { name: 'Settings' }));
    expect(await screen.findByText('Settings permissions:')).toBeTruthy();

    const completeList = resolveList;
    if (!completeList) {
      throw new Error('Expected workflow list request to be pending.');
    }
    completeList({
      ok: true,
      json: async () => ({ items: [{ workflowId: 'stale-row' }] }),
    } as Response);

    await waitFor(() => {
      expect(window.location.pathname).toBe('/settings');
    });
    expect(window.location.search).toBe('');
    expect(screen.queryByText(/stale-row/i)).toBeNull();
  });

  it('does not render operational metrics on the workflows home dashboard', async () => {
    window.history.replaceState({}, '', '/workflows');
    renderWithClient(<DashboardApp payload={{ page: 'dashboard', apiBase: '/api' }} />);

    expect(await screen.findByText('Workflow list route loaded')).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Workflows' }).getAttribute('href')).toBe('/workflows');
    expect(screen.queryByLabelText('Operational metrics')).toBeNull();
    expect(screen.queryByText('Operational metrics are unavailable.')).toBeNull();
    expect(fetchSpy.mock.calls.some(([url]) => String(url).startsWith('/api/executions/metrics'))).toBe(false);
  });

  it('uses the constrained shell by default for non-table pages', async () => {
    window.history.replaceState({}, '', '/skills');
    skillsImport.attempts = 1;
    renderWithClient(<DashboardApp payload={{ page: 'dashboard', apiBase: '/api' }} />);

    expect(await screen.findByText('Skills page recovered')).toBeTruthy();
    expect(document.querySelector('.panel--data-wide')).toBeNull();
    expect(document.querySelector('.dashboard-shell-constrained--data-wide')).toBeNull();
    expect(document.querySelector('.dashboard-shell-constrained')).toBeTruthy();
  });

  it('keeps the default panel constrained and centered while data routes opt wider', async () => {
    expect(dashboardCss).toMatch(
      /\.panel\s*\{[^}]*margin-left:\s*auto;[^}]*margin-right:\s*auto;[^}]*max-width:\s*min\(72rem,\s*calc\(100vw - 2rem\)\)/s,
    );
    expect(dashboardCss).toMatch(
      /\.panel\.panel--data-wide\s*\{[^}]*max-width:\s*min\(112rem,\s*calc\(100vw - 2rem\)\)/s,
    );
  });

  it('lets edge-to-edge data panels beat the 112rem cap via higher specificity', async () => {
    // The edge-to-edge `max-width: none` rule and the `.panel.panel--data-wide`
    // 112rem cap would otherwise have equal specificity, letting the later cap
    // win on source order and re-center wide layouts. Prefixing the `:has()`
    // rule with `.panel` raises its specificity so the edge-to-edge intent wins.
    expect(dashboardCss).toMatch(
      /\.panel\.panel--data-wide:has\(\.workflow-list-data-slab\),\s*\.panel\.panel--data-wide:has\(\.workflow-workspace-shell\)\s*\{[^}]*max-width:\s*none/s,
    );
  });

  it('bleeds workflow list chrome while keeping cell content inset', async () => {
    const slabBlock = cssRuleBlock(dashboardCss, '.workflow-list-data-slab');
    expect(slabBlock).toContain('--workflow-list-slab-bleed-inline: 1rem');

    const workflowWrapperBlock = cssRuleBlock(dashboardCss, '.workflow-list-data-slab .queue-table-wrapper');
    expect(workflowWrapperBlock).toContain('width: calc(100% + (var(--workflow-list-slab-bleed-inline) * 2))');
    expect(workflowWrapperBlock).toContain('margin-inline: calc(var(--workflow-list-slab-bleed-inline) * -1)');
    expect(workflowWrapperBlock).toContain('max-width: none');

    const firstCellBlock = cssRuleBlock(
      dashboardCss,
      '.workflow-list-data-slab .queue-table-wrapper th:first-child,\n.workflow-list-data-slab .queue-table-wrapper td:first-child',
    );
    expect(firstCellBlock).toContain('padding-left: 1rem');

    const lastCellBlock = cssRuleBlock(
      dashboardCss,
      '.workflow-list-data-slab .queue-table-wrapper th:last-child,\n.workflow-list-data-slab .queue-table-wrapper td:last-child',
    );
    expect(lastCellBlock).toContain('padding-right: 1rem');
  });

  it('MM-1138 Q1 bleeds the workspace rail to the screen edge and aligns its titles like the table column', async () => {
    // The shell mirrors the data slab's bleed token so the rail (which lives
    // outside `.workflow-list-data-slab`) can consume it.
    const shellBlock = cssRuleBlock(dashboardCss, '.workflow-workspace-shell');
    expect(shellBlock).toContain('--workflow-list-slab-bleed-inline: 1rem');

    // Only the left edge widens (right edge stays on the grid boundary); the
    // container padding stays 0 so the rail's dividers reach the screen edge.
    const sidebarBlock = cssRuleBlock(dashboardCss, '.workflow-workspace-sidebar');
    expect(sidebarBlock).toContain('width: calc(100% + var(--workflow-list-slab-bleed-inline))');
    expect(sidebarBlock).toContain('margin-left: calc(var(--workflow-list-slab-bleed-inline) * -1)');
    expect(sidebarBlock).toContain('padding: 0');

    // Header and rows re-inset the left padding by the bleed so titles land at
    // the same 1rem inset as the list table's first column.
    const sidebarHeaderBlock = cssRuleBlock(dashboardCss, '.workflow-workspace-sidebar-header');
    expect(sidebarHeaderBlock).toContain('padding: 0.5rem 0.75rem 0.5rem var(--workflow-list-slab-bleed-inline)');

    const sidebarRowBlock = cssRuleBlock(dashboardCss, '.workflow-workspace-sidebar-row');
    expect(sidebarRowBlock).toContain('0.58rem 0.55rem 0.58rem var(--workflow-list-slab-bleed-inline)');
  });

  it('MM-1138 Q1 scopes the workflow-list row divider to the shared list token', async () => {
    const rowDividerBlock = cssRuleBlock(
      dashboardCss,
      '.workflow-list-data-slab .queue-table-wrapper td',
    );
    expect(rowDividerBlock).toContain('border-bottom-color: var(--workflow-list-divider-color)');

    // The global table divider stays at 0.65 for other tables (bounded scope).
    const genericCellBlock = cssRuleBlock(dashboardCss, 'th,\ntd');
    expect(genericCellBlock).toContain('border-bottom: 1px solid rgb(var(--mm-border) / 0.65)');
  });

  it('MM-1138 Q2 keeps the create page content anchored when the rail is toggled', async () => {
    // Primary content stays in column 2 rather than spanning the full width.
    const primaryCollapsedBlock = cssRuleBlock(
      dashboardCss,
      '.workflow-start-workspace[data-sidebar-collapsed="true"] .workflow-start-primary',
    );
    expect(primaryCollapsedBlock).toContain('grid-column: 2');
    expect(primaryCollapsedBlock).not.toContain('1 / -1');

    // The two-column track stays reserved on the create page when collapsed.
    const createCollapsedGridBlock = cssRuleBlockMatching(dashboardCss, (rule) => (
      normalizeCssSelector(rule.selector)
        === '.workflow-start-workspace.workflow-workspace-shell[data-sidebar-collapsed="true"]' &&
      rule.parent?.type === 'atrule' &&
      rule.parent.name === 'media' &&
      rule.parent.params.includes('min-width: 768px')
    ));
    expect(createCollapsedGridBlock).toContain(
      'grid-template-columns: var(--workflow-list-column-workflow-width) minmax(0, 1fr)',
    );

    // The displacement offset is preserved: there is no collapsed reset to 0.
    const startWorkspaceBlock = cssRuleBlock(dashboardCss, '.workflow-start-workspace');
    expect(startWorkspaceBlock).toContain('--workflow-start-primary-offset');
    const collapsedOffsetResetBlock = cssRuleBlock(
      dashboardCss,
      '.workflow-start-workspace[data-sidebar-collapsed="true"]',
    );
    expect(collapsedOffsetResetBlock).not.toContain('--workflow-start-primary-offset: 0rem');
  });

  it('MM-1116 defines one workflow-list row metric token family for table and sidebar modes', async () => {
    const rootBlock = cssRuleBlock(dashboardCss, ':root');
    expect(rootBlock).toContain('--workflow-list-header-row-height: 2.75rem');
    expect(rootBlock).toContain('--workflow-list-body-row-height: 4rem');
    expect(rootBlock).toContain('--workflow-list-column-workflow-width: 20rem');
    expect(rootBlock).toContain('--workflow-list-divider-width: 1px');
    expect(rootBlock).toContain('--workflow-list-divider-color: rgb(var(--mm-border) / 0.72)');

    const tableWorkflowColumnBlock = cssRuleBlock(dashboardCss, '.queue-table-column-workflow');
    expect(tableWorkflowColumnBlock).toContain('width: var(--workflow-list-column-workflow-width)');

    const shellBlock = cssRuleBlock(dashboardCss, '.workflow-workspace-shell');
    expect(shellBlock).toContain('grid-template-columns: var(--workflow-list-column-workflow-width) minmax(0, 1fr)');

    const sidebarBlock = cssRuleBlock(dashboardCss, '.workflow-workspace-sidebar');
    expect(sidebarBlock).not.toContain('border-right');
  });

  it('keeps sidebar Workflow header chrome owned by the sidebar header container', async () => {
    const tableHeaderBlock = cssRuleBlock(dashboardCss, '.queue-table-wrapper th');
    expect(tableHeaderBlock).toContain('height: var(--workflow-list-header-row-height)');
    expect(tableHeaderBlock).toContain('background: rgb(var(--mm-panel) / 0.98)');
    expect(tableHeaderBlock).toContain('box-shadow: 0 1px 0 var(--workflow-list-divider-color)');
    expect(tableHeaderBlock).toContain('font-weight: 600');
    expect(tableHeaderBlock).toContain('color: rgb(var(--mm-ink) / 0.85)');

    const sidebarHeaderContainerBlock = cssRuleBlock(dashboardCss, '.workflow-workspace-sidebar-header');
    expect(sidebarHeaderContainerBlock).toContain('display: grid');
    expect(sidebarHeaderContainerBlock).toContain('min-height: var(--workflow-list-header-row-height)');
    expect(sidebarHeaderContainerBlock).toContain('background: rgb(var(--mm-panel) / 0.98)');
    expect(sidebarHeaderContainerBlock).toContain('box-shadow: 0 1px 0 var(--workflow-list-divider-color)');
    expect(sidebarHeaderContainerBlock).toContain('font-weight: 600');
    expect(sidebarHeaderContainerBlock).toContain('color: rgb(var(--mm-ink) / 0.85)');

    const sidebarHeaderBlock = cssRuleBlock(dashboardCss, '.workflow-workspace-sidebar-header-cell');
    expect(sidebarHeaderBlock).toContain('padding: 0');
    expect(sidebarHeaderBlock).toContain('background: transparent');
    expect(sidebarHeaderBlock).toContain('box-shadow: none');
    expect(sidebarHeaderBlock).toContain('text-align: left');
  });

  it('keeps workflow titles clamped to two consistent lines in the list and sidebar', async () => {
    const tableRowBlock = cssRuleBlock(dashboardCss, '.workflow-list-data-slab tbody tr');
    expect(tableRowBlock).toContain('height: var(--workflow-list-body-row-height)');

    const tableWorkflowCellBlock = cssRuleBlock(dashboardCss, '.queue-table-cell-workflow');
    expect(tableWorkflowCellBlock).toContain('height: var(--workflow-list-body-row-height)');

    const tableTitleBlock = cssRuleBlock(dashboardCss, '.workflow-list-row-title');
    expect(tableTitleBlock).toContain('display: -webkit-box');
    expect(tableTitleBlock).toContain('height: 2.5em');
    expect(tableTitleBlock).toContain('-webkit-line-clamp: 2');

    const sidebarRowBlock = cssRuleBlock(dashboardCss, '.workflow-workspace-sidebar-row');
    expect(sidebarRowBlock).toContain('height: var(--workflow-list-body-row-height)');
    expect(sidebarRowBlock).toContain('max-height: var(--workflow-list-body-row-height)');

    const sidebarTitleBlock = cssRuleBlock(dashboardCss, '.workflow-workspace-sidebar-title');
    expect(sidebarTitleBlock).toContain('display: -webkit-box');
    expect(sidebarTitleBlock).toContain('height: 2.5em');
    expect(sidebarTitleBlock).toContain('-webkit-line-clamp: 2');
    expect(sidebarTitleBlock).not.toContain('white-space: nowrap');
  });

  it('MM-1116 presents the workflow sidebar list region as a table slice rather than cards or menus', async () => {
    const sidebarTableBlock = cssRuleBlock(dashboardCss, '.workflow-workspace-sidebar-table');
    expect(sidebarTableBlock).toContain('display: grid');
    expect(sidebarTableBlock).toContain('grid-template-columns: minmax(0, 1fr)');
    expect(sidebarTableBlock).toContain('border-radius: 0');
    expect(sidebarTableBlock).toContain('box-shadow: none');
    expect(sidebarTableBlock).not.toContain('menu');

    const sidebarRowBlock = cssRuleBlock(dashboardCss, '.workflow-workspace-sidebar-row');
    expect(sidebarRowBlock).not.toContain('border-radius');

    const sidebarLastRowFrameBlock = cssRuleBlock(
      dashboardCss,
      '.workflow-workspace-sidebar-table > :last-child .workflow-workspace-sidebar-row-frame:last-child',
    );
    expect(sidebarLastRowFrameBlock).toContain('border-bottom: 0');

    const compactTableBlock = cssRuleBlock(dashboardCss, ".queue-table-wrapper[data-density='compact']");
    expect(compactTableBlock).toContain('--workflow-list-body-row-height: var(--workflow-list-compact-body-row-height)');

    const pinnedListBlock = cssRuleBlock(dashboardCss, '.workflow-workspace-sidebar-pinned-list');
    expect(pinnedListBlock).toContain('position: sticky');
    expect(pinnedListBlock).toContain('top: var(--workflow-list-header-row-height)');

    const pinnedRowTitleBlock = cssRuleBlock(
      dashboardCss,
      '.workflow-workspace-sidebar-row-pinned .workflow-workspace-sidebar-title',
    );
    expect(pinnedRowTitleBlock).toContain('-webkit-line-clamp: 1');
  });

  it('MM-1116 keeps sidebar/table mode changes aligned and reduced-motion safe', async () => {
    const tableWorkflowCellBlock = cssRuleBlock(dashboardCss, '.queue-table-cell-workflow');
    expect(tableWorkflowCellBlock).not.toContain('border-right');

    const workflowWrapperBlock = cssRuleBlock(dashboardCss, '.workflow-list-data-slab .queue-table-wrapper');
    expect(workflowWrapperBlock).toContain('transition: opacity 120ms ease');
    expect(workflowWrapperBlock).not.toContain('translateX');
    expect(workflowWrapperBlock).not.toContain('translate3d');

    const reducedMotionBlock = cssRuleBlockMatching(dashboardCss, (rule) => (
      rule.selector.includes('.workflow-workspace-sidebar-table') &&
      rule.selector.includes('.workflow-list-data-slab .queue-table-wrapper') &&
      rule.parent?.type === 'atrule' &&
      'params' in rule.parent &&
      String(rule.parent.params).includes('prefers-reduced-motion: reduce')
    ));
    expect(reducedMotionBlock).toContain('transition-duration: 1ms !important');
    expect(reducedMotionBlock).toContain('transform: none !important');
  });

  it('keeps the workflow sidebar scrollbar close to its divider', async () => {
    const sidebarBlock = cssRuleBlock(dashboardCss, '.workflow-workspace-sidebar');
    expect(sidebarBlock).toContain('padding: 0');

    const sidebarTableBlock = cssRuleBlock(dashboardCss, '.workflow-workspace-sidebar-table');
    expect(sidebarTableBlock).toContain('scrollbar-width: thin');
  });

  it('MM-1064 keeps workflow sidebar status icons compact inside status-colored containers', async () => {
    const iconBlock = cssRuleBlock(dashboardCss, '.workflow-workspace-sidebar-status-icon');
    expect(iconBlock).toContain('display: inline-flex');
    expect(iconBlock).toContain('align-items: center');
    expect(iconBlock).toContain('width: 1.375rem');
    expect(iconBlock).toContain('height: 1.375rem');
    expect(iconBlock).toContain('min-width: 1.375rem');
    expect(iconBlock).toContain('border-radius: 0.375rem');
    expect(iconBlock).toContain('justify-content: center');

    const svgBlock = cssRuleBlock(dashboardCss, '.workflow-workspace-sidebar-status-icon svg');
    expect(svgBlock).toContain('width: 0.8125rem');
    expect(svgBlock).toContain('height: 0.8125rem');
  });

  it('keeps workflow detail step timeline icons large inside their status circles', async () => {
    const iconBlock = cssRuleBlock(dashboardCss, '.step-tl-icon');
    expect(iconBlock).toContain('width: 1.35rem');
    expect(iconBlock).toContain('height: 1.35rem');
    expect(iconBlock).toContain('border-radius: 50%');

    const svgBlock = cssRuleBlock(dashboardCss, '.step-tl-icon svg');
    expect(svgBlock).toContain('width: 1.05rem');
    expect(svgBlock).toContain('height: 1.05rem');
    expect(svgBlock).toContain('stroke-width: 2.4');
  });

  it('keeps checkbox label hit areas bounded to visible control text', async () => {
    const checkboxLabelBlock = cssRuleBlock(dashboardCss, 'label.checkbox');

    expect(checkboxLabelBlock).toContain('display: inline-flex');
    expect(checkboxLabelBlock).toContain('width: fit-content');
    expect(checkboxLabelBlock).toContain('max-width: 100%');
  });

  it('defines shared visual atmosphere and glass tokens for light and dark themes', async () => {
    const requiredTokens = [
      '--mm-atmosphere-violet',
      '--mm-atmosphere-cyan',
      '--mm-atmosphere-warm',
      '--mm-atmosphere-base',
      '--mm-glass-fill',
      '--mm-glass-border',
      '--mm-glass-edge',
      '--mm-input-well',
      '--mm-elevation-panel',
      '--mm-elevation-floating',
    ];

    for (const token of requiredTokens) {
      expect(dashboardCss).toMatch(new RegExp(`:root\\s*\\{[^}]*${token}:`, 's'));
      expect(dashboardCss).toMatch(new RegExp(`\\.dark\\s*\\{[^}]*${token}:`, 's'));
    }
  });

  it('renders dashboard atmosphere and shared chrome from visual tokens', async () => {
    expect(dashboardCss).toMatch(
      /^body\s*\{[^}]*background:\s*var\(--mm-atmosphere-violet\),\s*var\(--mm-atmosphere-cyan\),\s*var\(--mm-atmosphere-warm\),\s*var\(--mm-atmosphere-base\);/ms,
    );
    expect(dashboardCss).toMatch(
      /\.dark body\s*\{[^}]*background:\s*var\(--mm-atmosphere-violet\),\s*var\(--mm-atmosphere-cyan\),\s*var\(--mm-atmosphere-warm\),\s*var\(--mm-atmosphere-base\);/s,
    );
    expect(dashboardCss).toMatch(
      /\.masthead::before\s*\{[^}]*background:\s*var\(--mm-glass-fill\);[^}]*box-shadow:\s*var\(--mm-elevation-panel\);/s,
    );
    expect(dashboardCss).toMatch(
      /\.panel\s*\{[^}]*background:\s*var\(--mm-glass-fill\);[^}]*border:\s*1px solid var\(--mm-glass-border\);[^}]*box-shadow:\s*var\(--mm-elevation-panel\);/s,
    );
    expect(dashboardCss).toMatch(
      /\.queue-floating-bar\s*\{[^}]*background:\s*var\(--mm-glass-fill\);[^}]*border:\s*1px solid var\(--mm-glass-border\);[^}]*box-shadow:\s*var\(--mm-elevation-floating\);/s,
    );
    expect(dashboardCss).toMatch(
      /\.queue-floating-bar \.queue-inline-selector select,\s*\.queue-floating-bar \.queue-inline-selector input\s*\{[^}]*background:\s*var\(--mm-input-well\);[^}]*border-color:\s*var\(--mm-glass-edge\);/s,
    );
  });

  it('defines the MM-425 shared surface hierarchy roles', async () => {
    const matteBlock = cssRuleBlock(dashboardCss, '.surface--matte-data');
    const satinBlock = cssRuleBlock(dashboardCss, '.panel--satin');
    const glassBlock = cssRuleBlock(
      dashboardCss,
      '.surface--glass-control, .panel--controls, .panel--floating, .panel--utility',
    );
    const liquidBlock = cssRuleBlock(dashboardCss, '.surface--liquidgl-hero');
    const accentBlock = cssRuleBlock(dashboardCss, '.surface--accent-live');
    const nestedDenseBlock = cssRuleBlock(dashboardCss, '.surface--nested-dense');

    expect(matteBlock).toContain('background: rgb(var(--mm-panel) / 0.92)');
    expect(dashboardCss).toMatch(
      /\.panel--data\s*\{[^}]*background:\s*rgb\(var\(--mm-panel\) \/ 0\.92\);/s,
    );
    expect(satinBlock).toContain('background: var(--mm-input-well)');
    expect(glassBlock).toContain('background: var(--mm-glass-fill)');
    expect(glassBlock).toContain('border: 1px solid var(--mm-glass-border)');
    expect(glassBlock).toContain('box-shadow: var(--mm-elevation-panel)');
    expect(liquidBlock).toContain('background: var(--mm-glass-fill)');
    expect(liquidBlock).toContain('box-shadow: var(--mm-elevation-floating)');
    expect(accentBlock).toContain('background: rgb(var(--mm-accent) / 0.14)');
    expect(nestedDenseBlock).toContain('background: rgb(var(--mm-panel) / 0.86)');
  });

  it('keeps glass token based with near-opaque fallbacks when backdrop filtering is unavailable', async () => {
    const glassBlock = cssRuleBlock(
      dashboardCss,
      '.surface--glass-control, .panel--controls, .panel--floating, .panel--utility',
    );

    expect(glassBlock).toContain('backdrop-filter: blur(18px) saturate(1.35)');
    expect(glassBlock).toContain('-webkit-backdrop-filter: blur(18px) saturate(1.35)');
    expect(dashboardCss).toMatch(
      /@supports not \(\(backdrop-filter:\s*blur\(2px\)\) or \(-webkit-backdrop-filter:\s*blur\(2px\)\)\)\s*\{[^}]*\.surface--glass-control,\s*\.panel--controls,\s*\.panel--floating,\s*\.panel--utility,\s*\.surface--liquidgl-hero,\s*\.queue-floating-bar\s*\{[^}]*background:\s*rgb\(var\(--mm-panel\) \/ 0\.94\);/s,
    );
  });

  it('keeps the Step Type segmented control focus ring visible', async () => {
    const stepTypeBlock = cssRuleBlock(dashboardCss, '.segmented-control[data-intensity="loud"]');
    const stepTypeFocusBlock = cssRuleBlock(
      dashboardCss,
      '.segmented-control[data-intensity="loud"] .segmented-control-item:has(input:focus-visible)',
    );

    expect(stepTypeBlock).toContain('backdrop-filter: blur(14px) saturate(140%)');
    expect(stepTypeBlock).not.toContain('-webkit-backdrop-filter');
    expect(stepTypeBlock).not.toContain('overflow: hidden');
    expect(stepTypeFocusBlock).toContain('box-shadow: var(--mm-control-focus-ring)');
  });

  it('MM-1020 keeps Workflow Detail segmented tabs count-aware and step toggles contained', async () => {
    const segmentedBaseBlock = cssRuleBlockMatching(dashboardCss, (rule) => (
      normalizeCssSelector(rule.selector) === '.segmented-control' && !rule.parent?.parent
    ));
    const baseThumbBlock = cssRuleBlock(dashboardCss, '.segmented-control::before');
    const quietThumbBlock = cssRuleBlock(dashboardCss, '.segmented-control[data-intensity="quiet"]::before');
    const stepToggleBlock = cssRuleBlock(dashboardCss, '.step-tl-toggle');

    expect(segmentedBaseBlock).toContain('--segmented-control-count: 1');
    expect(quietThumbBlock).toContain('width: calc((100% - 0.5rem) / var(--segmented-control-count))');
    expect(baseThumbBlock).toContain('translateX(calc(var(--segmented-control-active-index) * 100%))');
    expect(stepToggleBlock).toContain('box-sizing: border-box');
  });

  it('MM-1138 Q3 unifies the segmented controls into one system with two intensity tiers', async () => {
    // Shared base owns the thumb-position variables, chrome, and stacking.
    const baseBlock = cssRuleBlockMatching(dashboardCss, (rule) => (
      normalizeCssSelector(rule.selector) === '.segmented-control' && !rule.parent?.parent
    ));
    expect(baseBlock).toContain('--segmented-control-count: 1');
    expect(baseBlock).toContain('--segmented-control-active-index: 0');
    expect(baseBlock).toContain('isolation: isolate');

    // Quiet tier: subtle detail-tab look (padded container, full width).
    const quietBlock = cssRuleBlock(dashboardCss, '.segmented-control[data-intensity="quiet"]');
    expect(quietBlock).toContain('padding: 0.25rem');
    expect(quietBlock).toContain('width: 100%');

    // Loud tier: high-energy create/settings look (glass blur + count-driven
    // neon thumb positioned from the checked radio via :has()). MM-1138 Q3/B2
    // calmed the tier: the thumb keeps its springy interaction slide but no
    // longer runs the perpetual shimmer animation.
    const loudBlock = cssRuleBlock(dashboardCss, '.segmented-control[data-intensity="loud"]');
    expect(loudBlock).toContain('backdrop-filter: blur(14px) saturate(140%)');

    const loudThumbBlock = cssRuleBlock(dashboardCss, '.segmented-control[data-intensity="loud"]::before');
    expect(loudThumbBlock).toContain('width: calc(100% / var(--segmented-control-count))');
    expect(loudThumbBlock).toContain('transition: transform 360ms cubic-bezier(0.34, 1.3, 0.5, 1)');
    expect(loudThumbBlock).not.toContain('animation:');

    const loudActiveIndexBlock = cssRuleBlockMatching(dashboardCss, (rule) => (
      rule.selector.includes('data-intensity="loud"') &&
      rule.selector.includes(':has(') &&
      rule.selector.includes(':nth-child(2)') &&
      !rule.parent?.parent
    ));
    expect(loudActiveIndexBlock).toContain('--segmented-control-active-index: 1');

    // MM-1138 Q3/B3: the quiet thumb echoes the loud accent gradient plus one
    // soft glow (was a flat fill) so both tiers read as the same family, while
    // keeping the calmer 180ms slide.
    const quietThumbBlock = cssRuleBlock(dashboardCss, '.segmented-control[data-intensity="quiet"]::before');
    expect(quietThumbBlock).toContain('background: linear-gradient(');
    expect(quietThumbBlock).toContain('0 0 10px rgb(var(--mm-accent) / 0.22)');
    expect(quietThumbBlock).toContain('transition: transform 180ms ease');

    // MM-1138 Q3/B2: no perpetual motion survives — the scan border and both
    // shimmer/scan keyframes are removed; motion is interaction-only.
    expect(dashboardCss).not.toContain('@keyframes segmented-control-thumb-shimmer');
    expect(dashboardCss).not.toContain('@keyframes segmented-control-scan');
    expect(dashboardCss).not.toContain('data-intensity="loud"]::after');

    // MM-1138 Q3/B4: the detail "More" overflow trigger is aligned to the quiet
    // tab height (2.15rem) so it sits flush with the tabs it overflows from.
    const moreTriggerBlock = cssRuleBlock(dashboardCss, '.td-subroute-more-trigger');
    expect(moreTriggerBlock).toContain('min-height: 2.15rem');
    const quietItemBlock = cssRuleBlock(
      dashboardCss,
      '.segmented-control[data-intensity="quiet"] .segmented-control-item',
    );
    expect(quietItemBlock).toContain('min-height: 2.15rem');

    // The badge (quiet detail-tab affordance) rides the shared system.
    const badgeBlock = cssRuleBlock(dashboardCss, '.segmented-control-badge');
    expect(badgeBlock).toContain('border-radius: 999px');

    // The superseded per-consumer class families are fully removed.
    expect(dashboardCss).not.toContain('segmented-nav');
    expect(dashboardCss).not.toContain('queue-step-type');
    expect(dashboardCss).not.toContain('settings-nav');
  });

  it('lets Settings use the page canvas without a surrounding shared card', async () => {
    const settingsPanelBlock = cssRuleBlock(dashboardCss, '.panel:has(.settings-page)');

    expect(settingsPanelBlock).toContain('border: 0');
    expect(settingsPanelBlock).toContain('background: transparent');
    expect(settingsPanelBlock).toContain('box-shadow: none');
    expect(settingsPanelBlock).toContain('padding: 0');
  });

  it('drops the workflow list table shell to a card-first layout on mobile viewports', async () => {
    const isMobileWorkflowRule = (selector: string) => (rule: Rule) =>
      normalizeCssSelector(rule.selector) === selector &&
      rule.parent?.type === 'atrule' &&
      rule.parent.name === 'media' &&
      rule.parent.params.includes('max-width: 767px');

    const mobileSlabBlock = cssRuleBlockMatching(
      dashboardCss,
      isMobileWorkflowRule('.workflow-list-data-slab'),
    );
    const mobileHeaderBlock = cssRuleBlockMatching(
      dashboardCss,
      isMobileWorkflowRule('.workflow-list-results-header'),
    );
    const mobileFooterBlock = cssRuleBlockMatching(
      dashboardCss,
      isMobileWorkflowRule('.workflow-list-data-slab .workflow-list-results-footer'),
    );
    const mobileViewOptionsPopoverBlock = cssRuleBlockMatching(
      dashboardCss,
      isMobileWorkflowRule('.workflow-list-view-options-popover'),
    );

    expect(mobileSlabBlock).toContain('border: 0');
    expect(mobileSlabBlock).toContain('border-radius: 0');
    expect(mobileSlabBlock).toContain('background: transparent');
    expect(mobileSlabBlock).toContain('box-shadow: none');
    expect(mobileSlabBlock).toContain('padding: 0');

    expect(mobileHeaderBlock).toContain('border-bottom: 0');
    expect(mobileHeaderBlock).toContain('background: transparent');

    expect(mobileFooterBlock).toContain('border-top: 0');
    expect(mobileFooterBlock).toContain('padding-left: 0');
    expect(mobileFooterBlock).toContain('padding-right: 0');

    expect(mobileViewOptionsPopoverBlock).toContain('right: auto');
    expect(mobileViewOptionsPopoverBlock).toContain('left: 0');
    expect(mobileViewOptionsPopoverBlock).toContain('max-width: calc(100vw - 2rem)');

    // Individual cards must keep their standalone card styling.
    const cardBlock = cssRuleBlock(dashboardCss, '.queue-card');
    expect(cardBlock).toContain('border: 1px solid rgb(var(--mm-border) / 0.8)');
    expect(cardBlock).toContain('border-radius: 1rem');
    expect(cardBlock).toContain('background: rgb(var(--mm-panel) / 0.78)');
  });

  it('stacks Settings section radio controls on mobile viewports', async () => {
    const settingsLoudSelector = '.settings-page .segmented-control[data-intensity="loud"]';
    const settingsLoudItemSelector = `${settingsLoudSelector} .segmented-control-item`;
    const mobileSettingsNavBlock = cssRuleBlockMatching(dashboardCss, (rule) => {
      return (
        normalizeCssSelector(rule.selector) === settingsLoudSelector &&
        rule.parent?.type === 'atrule' &&
        rule.parent.name === 'media' &&
        rule.parent.params.includes('max-width: 640px')
      );
    });
    const mobileSettingsOptionBlock = cssRuleBlockMatching(dashboardCss, (rule) => {
      return (
        normalizeCssSelector(rule.selector) === settingsLoudItemSelector &&
        rule.parent?.type === 'atrule' &&
        rule.parent.name === 'media' &&
        rule.parent.params.includes('max-width: 640px')
      );
    });
    const mobileFirstSettingsOptionBlock = cssRuleBlockMatching(dashboardCss, (rule) => {
      return (
        normalizeCssSelector(rule.selector) === `${settingsLoudItemSelector}:first-of-type` &&
        rule.parent?.type === 'atrule' &&
        rule.parent.name === 'media' &&
        rule.parent.params.includes('max-width: 640px')
      );
    });
    const mobileLastSettingsOptionBlock = cssRuleBlockMatching(dashboardCss, (rule) => {
      return (
        normalizeCssSelector(rule.selector) === `${settingsLoudItemSelector}:last-of-type` &&
        rule.parent?.type === 'atrule' &&
        rule.parent.name === 'media' &&
        rule.parent.params.includes('max-width: 640px')
      );
    });
    const mobileSettingsLabelBlock = cssRuleBlockMatching(dashboardCss, (rule) => {
      return (
        normalizeCssSelector(rule.selector) === `${settingsLoudSelector} .segmented-control-item-label` &&
        rule.parent?.type === 'atrule' &&
        rule.parent.name === 'media' &&
        rule.parent.params.includes('max-width: 640px')
      );
    });
    const mobileSettingsActiveBlock = cssRuleBlockMatching(dashboardCss, (rule) => {
      return (
        normalizeCssSelector(rule.selector) === `${settingsLoudItemSelector}:has(input:checked)` &&
        rule.parent?.type === 'atrule' &&
        rule.parent.name === 'media' &&
        rule.parent.params.includes('max-width: 640px')
      );
    });

    expect(mobileSettingsNavBlock).toContain('display: grid');
    expect(mobileSettingsNavBlock).toContain('grid-template-columns: minmax(0, 1fr)');
    expect(mobileSettingsNavBlock).toContain('width: 100%');
    expect(mobileSettingsNavBlock).toContain('padding: 0');
    expect(mobileSettingsOptionBlock).toContain('justify-content: flex-start');
    expect(mobileSettingsOptionBlock).toContain('width: 100%');
    expect(mobileFirstSettingsOptionBlock).toContain('border-top-left-radius: 9px');
    expect(mobileFirstSettingsOptionBlock).toContain('border-top-right-radius: 9px');
    expect(mobileLastSettingsOptionBlock).toContain('border-bottom-left-radius: 9px');
    expect(mobileLastSettingsOptionBlock).toContain('border-bottom-right-radius: 9px');
    expect(mobileSettingsActiveBlock).toContain('0 0 18px rgb(var(--mm-accent) / 0.55)');
    expect(mobileSettingsActiveBlock).toContain('0 0 32px rgb(var(--mm-accent-2) / 0.22)');
    // MM-1138 Q3/B2: the active pill keeps its neon glow but no longer runs the
    // perpetual shimmer animation, so no reduced-motion override is needed.
    expect(mobileSettingsActiveBlock).not.toContain('animation');
    expect(mobileSettingsLabelBlock).toContain('white-space: normal');
    expect(mobileSettingsLabelBlock).toContain('overflow-wrap: anywhere');
  });

  it('keeps liquidGL opt-in and away from default dense surfaces', async () => {
    expect(cssRuleBlock(dashboardCss, '.panel')).not.toContain('liquid');
    expect(cssRuleBlock(dashboardCss, '.card')).not.toContain('liquid');
    expect(cssRuleBlock(dashboardCss, 'table')).not.toContain('liquid');
    expect(cssRuleBlock(dashboardCss, '.data-table-slab')).not.toContain('liquid');

    const liquidBlock = cssRuleBlock(dashboardCss, '.surface--liquidgl-hero');
    expect(liquidBlock).toContain('isolation: isolate');
    expect(liquidBlock).toContain('overflow: hidden');
    expect(liquidBlock).toContain('backdrop-filter: blur(26px) saturate(1.65)');
  });

  it('enforces MM-429 readable contrast tokens across common dashboard surfaces', async () => {
    expect(cssRuleBlock(dashboardCss, 'label')).toContain('color: rgb(var(--mm-ink))');
    expect(cssRuleBlock(dashboardCss, '.data-table th,\n.data-table td')).toContain(
      'color: rgb(var(--mm-ink))',
    );
    expect(cssRuleBlock(dashboardCss, 'input::placeholder,\ntextarea::placeholder')).toContain(
      'color: rgb(var(--mm-muted))',
    );
    expect(cssRuleBlock(dashboardCss, '.workflow-list-filter-chip')).toContain(
      'color: rgb(var(--mm-ink))',
    );
    const primaryButtonBlock = cssRuleBlockMatching(dashboardCss, (rule) => {
      const selectors = rule.selector.split(',').map(normalizeCssSelector);
      return (
        selectors.some(
          (selector) =>
            selector.startsWith('button:not(') || selector.startsWith('button:where(:not('),
        ) &&
        rule.nodes.some((node) => node.type === 'decl' && node.prop === 'color' && node.value === '#fff')
      );
    });
    expect(primaryButtonBlock).toContain('color: #fff');
    expect(cssRuleBlock(dashboardCss, '.surface--glass-control, .panel--controls, .panel--floating, .panel--utility')).toContain(
      'background: var(--mm-glass-fill)',
    );
  });

  it('enforces MM-429 focus visibility across representative interactive surfaces', async () => {
    const focusSelectors = [
      'button:focus-visible',
      '.button:focus-visible',
      'input:focus-visible,\nselect:focus-visible,\ntextarea:focus-visible',
      '.route-nav a:focus-visible',
      '.table-sort-button:focus-visible',
      '.queue-action:focus-visible,\n.queue-submit-primary:focus-visible',
      '.queue-step-attachment-add-button:focus-visible',
      '.live-logs-artifact-link:focus-visible',
      '.td-instructions-toggle:focus-visible',
    ];

    for (const selector of focusSelectors) {
      const block = cssRuleBlock(dashboardCss, selector);
      expect(block).toContain('box-shadow: var(--mm-control-focus-ring)');
    }
  });

  it('enforces MM-429 reduced-motion suppression for live and premium effects', async () => {
    const runningIconBlock = cssRuleBlockMatching(
      dashboardCss,
      (rule) => {
        const selectors = rule.selector.split(',').map(normalizeCssSelector);
        return selectors.includes('.step-tl-icon.status-running') &&
          rule.nodes.some((node) => node.type === 'decl' && node.toString() === 'animation: none !important');
      },
    );
    expect(runningIconBlock).toContain('animation: none !important');
    expect(runningIconBlock).toContain('opacity: 1');

    const premiumEffectBlock = cssRuleBlockMatching(
      dashboardCss,
      (rule) =>
        rule.selector
          .split(',')
          .map(normalizeCssSelector)
          .includes('.surface--liquidgl-hero') &&
        rule.nodes.some(
          (node) =>
            node.type === 'decl' && node.toString() === 'transition-duration: 0s !important',
        ),
    );
    expect(premiumEffectBlock).toContain('transition-duration: 0s !important');
    expect(premiumEffectBlock).toContain('animation-duration: 0s !important');
  });

  it('enforces MM-961 reduced-motion suppression for the panel entry animation', async () => {
    const panelReducedMotionBlock = cssRuleBlockMatching(
      dashboardCss,
      (rule) =>
        normalizeCssSelector(rule.selector) === '.panel' &&
        rule.parent?.type === 'atrule' &&
        rule.parent.name === 'media' &&
        rule.parent.params.includes('prefers-reduced-motion: reduce'),
    );
    expect(panelReducedMotionBlock).toContain('animation: none !important');
  });

  it('keeps the dashboard panel flush with the masthead', async () => {
    expect(cssRuleBlock(dashboardCss, '.panel')).toContain('margin-top: 0;');
  });

  it('disables MM-961 fixed background attachment on mobile and touch/low-power devices', async () => {
    const mobileBackgroundBlock = cssRuleBlockMatching(
      dashboardCss,
      (rule) =>
        rule.selector
          .split(',')
          .map(normalizeCssSelector)
          .includes('body') &&
        rule.parent?.type === 'atrule' &&
        rule.parent.name === 'media' &&
        rule.parent.params.includes('max-width: 767px') &&
        rule.parent.params.includes('(hover: none) and (pointer: coarse)'),
    );
    expect(mobileBackgroundBlock).toContain('background-attachment: scroll');

    // The default desktop atmosphere should still pin the background.
    expect(cssRuleBlock(dashboardCss, 'body')).toContain('background-attachment: fixed');
    expect(cssRuleBlock(dashboardCss, '.dark body')).toContain('background-attachment: fixed');
  });

  it('enforces MM-429 fallback shells and premium-effect limits', async () => {
    expect(dashboardCss).toMatch(
      /@supports not \(\(backdrop-filter:\s*blur\(2px\)\) or \(-webkit-backdrop-filter:\s*blur\(2px\)\)\)\s*\{[^}]*\.surface--glass-control,[^}]*\.panel--controls,[^}]*\.panel--floating,[^}]*\.panel--utility,[^}]*\.surface--liquidgl-hero,[^}]*\.queue-floating-bar\s*\{[^}]*background:\s*rgb\(var\(--mm-panel\) \/ 0\.94\);[^}]*border-color:\s*rgb\(var\(--mm-border\) \/ 0\.84\);/s,
    );

    for (const selector of [
      '.surface--matte-data',
      '.surface--nested-dense',
      '.data-table-slab',
      '.td-evidence-region',
      '.td-evidence-slab',
      'textarea',
    ]) {
      const block = cssRuleBlock(dashboardCss, selector);
      expect(block).not.toContain('liquid');
      expect(block).not.toContain('backdrop-filter');
      expect(block).not.toContain('blur(26px)');
    }
  });

  it('enforces MM-430 semantic shell class stability for dashboard sources', async () => {
    const { readFileSync } = await import('node:fs');
    const dashboardShellSource = readFileSync(
      `${process.cwd()}/frontend/src/entrypoints/dashboard-app.tsx`,
      'utf8',
    );

    expect(dashboardShellSource).toContain('className="dashboard-root"');
    expect(dashboardShellSource).toContain('className="masthead"');
    expect(dashboardShellSource).toContain('className={`route-nav');
    expect(dashboardShellSource).not.toContain('/proposals');
    expect(dashboardShellSource).not.toContain('Proposals');

    for (const selector of [
      '.dashboard-root',
      '.masthead',
      '.route-nav',
      '.panel',
      '.card',
      '.toolbar',
      '.status-queued',
      '.status-scheduled',
      '.status-awaiting-slot',
      '.status-awaiting-dependencies',
      '.status-awaiting-external',
      '.status-initializing',
      '.status-planning',
      '.status-finalizing',
      '.status-canceled',
      '.status-running',
      '.queue-submit-form',
    ]) {
      expect(cssRuleBlock(dashboardCss, selector)).not.toBe('');
    }
  });

  it('colors only Moon white in the masthead brand', async () => {
    const { readFileSync } = await import('node:fs');
    const dashboardShellSource = readFileSync(
      `${process.cwd()}/frontend/src/entrypoints/dashboard-app.tsx`,
      'utf8',
    );

    expect(dashboardShellSource).toContain('className="masthead-brand-moon"');
    expect(dashboardShellSource).toContain('className="masthead-brand-mind"');
    expect(cssRuleBlock(dashboardCss, '.masthead-brand-moon')).toContain('color: rgb(255 255 255)');
    expect(cssRuleBlock(dashboardCss, '.masthead-brand-mind')).toContain('color: inherit');
  });

  it('defines MM-1035 exact workflow status color roles', async () => {
    const queueBlock = cssRuleBlocks(
      dashboardCss,
      '.status-queued, .status-scheduled, .status-awaiting-slot',
    ).join('\n');
    expect(queueBlock).toContain('color: rgb(var(--mm-status-queued, 99 102 241))');
    expect(queueBlock).toContain('rgb(var(--mm-status-queued, 99 102 241) / 0.14)');

    const waitBlock = cssRuleBlocks(
      dashboardCss,
      '.status-awaiting-action, .status-waiting, .status-awaiting-dependencies, .status-awaiting-external',
    ).join('\n');
    expect(dashboardCss).toContain('--mm-status-waiting: 146 64 14');
    expect(dashboardCss).toContain('--mm-status-waiting: 250 204 21');
    expect(waitBlock).toContain('color: rgb(var(--mm-status-waiting))');
    expect(waitBlock).toContain('rgb(var(--mm-status-waiting) / 0.14)');

    const setupBlock = cssRuleBlocks(dashboardCss, '.status-initializing, .status-planning, .status-finalizing').join('\n');
    expect(setupBlock).toContain('color: rgb(var(--mm-status-setup, 37 99 235))');
    expect(setupBlock).toContain('rgb(var(--mm-status-setup, 37 99 235) / 0.14)');
    expect(cssRuleBlock(dashboardCss, '.status-canceled')).toContain(
      'color: rgb(var(--mm-status-canceled, 249 115 22))',
    );
    expect(cssRuleBlock(dashboardCss, '.status-no-commit')).toContain('color: rgb(var(--mm-muted))');
    expect(cssRuleBlock(dashboardCss, '.status-running, .status-running.is-executing')).toContain(
      'color: rgb(var(--mm-accent-2))',
    );
  });


  it('defines the shared MM-488 executing shimmer modifier contract', async () => {
    expect(dashboardCss).toMatch(/--mm-executing-sweep-cycle-duration:\s*2600ms/);
    // MM-1048: angle and vertical travel are slightly more horizontal than the
    // previous -24deg / +/-128% treatment.
    expect(dashboardCss).toMatch(/--mm-executing-sweep-angle:\s*-20deg/);
    expect(dashboardCss).toMatch(/--mm-executing-sweep-band-width:\s*24%/);
    expect(dashboardCss).toMatch(/--mm-executing-sweep-band-height:\s*180%/);
    expect(dashboardCss).toMatch(/--mm-executing-sweep-halo-width-multiplier:\s*10/);
    expect(dashboardCss).toMatch(/--mm-executing-sweep-core-width-multiplier:\s*9\.1667/);
    expect(dashboardCss).not.toContain('--mm-executing-sweep-halo-peak-width-multiplier');
    expect(dashboardCss).not.toContain('--mm-executing-sweep-core-peak-width-multiplier');
    // MM-1048: vertical travel is reduced to +/-120% so the horizontal delta
    // exceeds the vertical delta and the sweep travels more horizontally.
    expect(dashboardCss).toMatch(/--mm-executing-sweep-start-x:\s*135%/);
    expect(dashboardCss).toMatch(/--mm-executing-sweep-start-y:\s*120%/);
    expect(dashboardCss).toMatch(/--mm-executing-sweep-end-x:\s*-135%/);
    expect(dashboardCss).toMatch(/--mm-executing-sweep-end-y:\s*-120%/);
    expect(dashboardCss).toMatch(/--mm-executing-sweep-layer-offset-x:\s*-12%/);
    expect(dashboardCss).toMatch(/--mm-executing-sweep-layer-offset-y:\s*-10%/);
    expect(dashboardCss).toContain('--mm-executing-letter-cycle-duration: var(--mm-executing-sweep-cycle-duration)');
    expect(dashboardCss).toContain('--mm-executing-letter-sweep-start-ratio: 0.2');
    expect(dashboardCss).toContain('--mm-executing-letter-sweep-travel-ratio: 0.18');
    expect(dashboardCss).toContain('--mm-executing-letter-sweep-direction: 1');
    expect(dashboardCss).toContain('--mm-executing-letter-halo: var(--mm-status-shimmer-letter-halo)');
    expect(dashboardCss).toContain('--mm-executing-letter-bright: var(--mm-status-shimmer-letter-bright)');
    expect(dashboardCss).toContain('--mm-executing-border-glint-outset: 1px');
    expect(dashboardCss).toContain('--mm-executing-border-glint-width: 3px');
    expect(dashboardCss).toContain('--mm-executing-border-glint-opacity: 0.95');
    expect(dashboardCss).toContain('--mm-executing-moving-light-gradient:');
    expect(dashboardCss).toContain('--mm-status-shimmer-halo: color-mix(in srgb, currentColor 30%, transparent)');
    expect(dashboardCss).toContain('--mm-status-shimmer-core: color-mix(in srgb, currentColor 70%, white 30%)');
    expect(dashboardCss).toContain('--mm-status-shimmer-letter-halo: color-mix(in srgb, currentColor 32%, transparent)');
    expect(dashboardCss).toContain('--mm-status-shimmer-letter-bright: color-mix(in srgb, currentColor 68%, white 32%)');
    expect(dashboardCss).toContain('var(--mm-status-shimmer-halo) 50%');
    expect(dashboardCss).toContain('var(--mm-status-shimmer-core) 50%');
    expect(dashboardCss).not.toContain('rgb(var(--mm-accent) / var(--mm-executing-sweep-halo-opacity)) 50%');
    expect(dashboardCss).not.toContain('rgb(var(--mm-accent-2) / var(--mm-executing-sweep-core-opacity)) 50%');

    const shimmerBlock = cssRuleBlocks(
      dashboardCss,
      '.status[data-effect="shimmer-sweep"], .status-running.is-executing',
    ).join('\n');
    expect(shimmerBlock).toContain('overflow: hidden');
    expect(shimmerBlock).toContain('isolation: isolate');
    expect(shimmerBlock).not.toContain('animation-delay:');
    const runningBackgroundBlock = cssRuleBlocks(
      dashboardCss,
      '.status-running[data-effect="shimmer-sweep"], .status-running.is-executing',
    ).join('\n');
    expect(runningBackgroundBlock).toContain('background-color: rgb(var(--mm-accent-2) / 0.14)');

    const sharedLightMaskBlock = cssRuleBlockMatching(
      dashboardCss,
      (rule) =>
        rule.selector.includes('status') &&
        rule.selector.includes('shimmer-sweep') &&
        rule.selector.includes('::before') &&
        rule.selector.includes('::after'),
    );
    expect(sharedLightMaskBlock).toContain('background-image: var(--mm-executing-moving-light-gradient)');
    expect(sharedLightMaskBlock).toContain('animation: mm-status-pill-shimmer var(--mm-executing-sweep-cycle-duration) linear infinite');
    expect(sharedLightMaskBlock).toContain('mix-blend-mode: plus-lighter');
    expect(sharedLightMaskBlock).toMatch(
      /background-size:\s*calc\(var\(--mm-executing-sweep-band-width\)\s*\*\s*var\(--mm-executing-sweep-halo-width-multiplier\)\)\s*var\(--mm-executing-sweep-band-height\),\s*calc\(var\(--mm-executing-sweep-band-width\)\s*\*\s*var\(--mm-executing-sweep-core-width-multiplier\)\)\s*var\(--mm-executing-sweep-band-height\)/,
    );
    expect(sharedLightMaskBlock).toMatch(
      /background-position:\s*var\(--mm-executing-sweep-start-x\)\s*var\(--mm-executing-sweep-start-y\),\s*calc\(var\(--mm-executing-sweep-start-x\)\s*\+\s*var\(--mm-executing-sweep-layer-offset-x\)\)\s*calc\(var\(--mm-executing-sweep-start-y\)\s*\+\s*var\(--mm-executing-sweep-layer-offset-y\)\)/,
    );

    expect(dashboardCss).not.toMatch(/@keyframes mm-status-pill-shimmer\s*\{[\s\S]*?52%\s*\{/);
    expect(dashboardCss).toMatch(/@keyframes mm-status-pill-shimmer\s*\{[\s\S]*?0%\s*\{[\s\S]*?background-position:\s*var\(--mm-executing-sweep-start-x\)\s*var\(--mm-executing-sweep-start-y\),[\s\S]*?100%\s*\{[\s\S]*?background-position:\s*var\(--mm-executing-sweep-end-x\)\s*var\(--mm-executing-sweep-end-y\),[\s\S]*?background-size:\s*calc\(var\(--mm-executing-sweep-band-width\)\s*\*\s*var\(--mm-executing-sweep-halo-width-multiplier\)\)\s*var\(--mm-executing-sweep-band-height\),[\s\S]*?calc\(var\(--mm-executing-sweep-band-width\)\s*\*\s*var\(--mm-executing-sweep-core-width-multiplier\)\)\s*var\(--mm-executing-sweep-band-height\);/);
    expect(dashboardCss).toMatch(
      /@media \(prefers-reduced-motion: reduce\)\s*\{[\s\S]*?\.status\[data-effect="shimmer-sweep"\],\s*\.status-running\.is-executing[\s\S]*?animation: none;/,
    );
    expect(dashboardCss).toMatch(
      /@media \(prefers-reduced-motion: reduce\)\s*\{[\s\S]*?background-position:\s*50% 50%,[\s\S]*?calc\(50% \+ \(var\(--mm-executing-sweep-layer-offset-x\) \/ 2\)\)\s*calc\(50% \+ \(var\(--mm-executing-sweep-layer-offset-y\) \/ 2\)\);[\s\S]*?background-size:\s*160% var\(--mm-executing-sweep-band-height\),\s*140% var\(--mm-executing-sweep-band-height\);/,
    );
    const shimmerBeforeSelector = cssRuleBlocks(
      dashboardCss,
      '.status[data-effect="shimmer-sweep"]::before, .status-running.is-executing::before',
    ).join('\n');
    expect(shimmerBeforeSelector).toContain('opacity: 0.62');
    expect(shimmerBeforeSelector).toContain('z-index: 1');

    const shimmerAfterBlock = cssRuleBlocks(
      dashboardCss,
      '.status[data-effect="shimmer-sweep"]::after, .status-running.is-executing::after',
    ).join('\n');
    expect(shimmerAfterBlock).toContain('mask-composite: exclude');
    expect(shimmerAfterBlock).toContain('-webkit-mask-composite: xor');
    expect(shimmerAfterBlock).toContain('z-index: 2');
    expect(shimmerAfterBlock).toContain('inset: calc(-1 * var(--mm-executing-border-glint-outset))');
    expect(shimmerAfterBlock).toContain('padding: var(--mm-executing-border-glint-width)');
    expect(shimmerAfterBlock).toContain('opacity: var(--mm-executing-border-glint-opacity)');
    expect(cssRuleBlock(dashboardCss, '.status-letter-wave')).toContain('z-index: 3');
    const glyphBlock = cssRuleBlock(dashboardCss, '.status-letter-wave__glyph');
    expect(glyphBlock).toContain('--mm-letter-phase');
    expect(glyphBlock).toContain('var(--mm-letter-index)');
    expect(glyphBlock).toContain('var(--mm-letter-count)');
    expect(glyphBlock).toContain('var(--mm-executing-letter-sweep-direction)');
    expect(glyphBlock).not.toContain('will-change');
    expect(dashboardCss).toContain('@keyframes mm-executing-letter-brighten');
    expect(dashboardCss).toContain('mm-executing-letter-brighten');

    const baseLetterWaveBlock = cssRuleBlock(dashboardCss, '.status-letter-wave');
    expect(baseLetterWaveBlock).toContain('color: inherit');
    expect(baseLetterWaveBlock).not.toContain('animation:');
    expect(baseLetterWaveBlock).not.toContain('background-image:');
    expect(baseLetterWaveBlock).not.toContain('background-clip: text');
    expect(baseLetterWaveBlock).not.toContain('-webkit-background-clip: text');
    expect(baseLetterWaveBlock).not.toContain('-webkit-text-fill-color: transparent');
    const textMaskBlock = cssRuleBlockMatching(
      dashboardCss,
      (rule) =>
        rule.selector.includes('status') &&
        rule.selector.includes('shimmer-sweep') &&
        rule.selector.includes('.status-letter-wave::after'),
    );
    expect(textMaskBlock).toContain('content: attr(data-label)');
    expect(textMaskBlock).toContain('background-image: var(--mm-executing-moving-light-gradient)');
    expect(textMaskBlock).toContain('animation: mm-status-pill-shimmer var(--mm-executing-sweep-cycle-duration) linear infinite');
    expect(textMaskBlock).toContain('background-clip: text');
    expect(textMaskBlock).toContain('-webkit-background-clip: text');
    expect(textMaskBlock).toContain('-webkit-text-fill-color: transparent');
    expect(textMaskBlock).toContain('mix-blend-mode: plus-lighter');

    const activeLetterWaveBlock = cssRuleBlocks(
      dashboardCss,
      '.status[data-effect="shimmer-sweep"] .status-letter-wave, .status-running.is-executing .status-letter-wave',
    ).join('\n');
    expect(activeLetterWaveBlock).toContain('color: inherit');
    expect(activeLetterWaveBlock).not.toContain('color: var(--mm-executing-letter-bright)');
    expect(activeLetterWaveBlock).not.toContain('text-shadow: 0 0 10px var(--mm-executing-letter-halo)');
    expect(activeLetterWaveBlock).not.toContain('animation: mm-status-pill-shimmer');
    expect(activeLetterWaveBlock).not.toContain('white 50%');
    const activeGlyphBlock = cssRuleBlocks(
      dashboardCss,
      '.status[data-effect="shimmer-sweep"] .status-letter-wave__glyph, .status-running.is-executing .status-letter-wave__glyph',
    ).join('\n');
    expect(activeGlyphBlock).toContain('animation: none');
    expect(activeGlyphBlock).toContain('animation-name: mm-executing-letter-brighten');
    expect(activeGlyphBlock).toContain(
      'animation-duration: var(--mm-executing-letter-cycle-duration, var(--mm-executing-sweep-cycle-duration, 2200ms))',
    );
    expect(activeGlyphBlock).toContain('animation-timing-function: linear');
    expect(activeGlyphBlock).toContain('animation-iteration-count: infinite');
    expect(activeGlyphBlock).toContain('animation-delay: calc(');
    expect(activeGlyphBlock).toContain('(var(--mm-letter-phase) - 0.22)');
    expect(dashboardCss).toMatch(
      /@keyframes mm-executing-letter-brighten\s*\{[\s\S]*?color:\s*var\(--mm-executing-letter-bright\);[\s\S]*?text-shadow:\s*0 0 10px var\(--mm-executing-letter-halo\);/,
    );
    expect(dashboardCss).toMatch(
      /@media \(prefers-reduced-motion: reduce\)\s*\{[\s\S]*?\.status\[data-effect="shimmer-sweep"\] \.status-letter-wave,\s*\.status-running\.is-executing \.status-letter-wave[\s\S]*?text-shadow: none;/,
    );
    expect(dashboardCss).toMatch(
      /@media \(prefers-reduced-motion: reduce\)\s*\{[\s\S]*?\.status\[data-effect="shimmer-sweep"\]::before,[\s\S]*?\.status-running\.is-executing \.status-letter-wave::after[\s\S]*?animation: none;/,
    );
    expect(dashboardCss).toMatch(
      /@media \(prefers-reduced-motion: reduce\)\s*\{[\s\S]*?\.status-letter-wave__glyph\s*\{[\s\S]*?animation:\s*none !important;[\s\S]*?text-shadow:\s*none !important;[\s\S]*?filter:\s*none !important;/,
    );

    const forcedColorsLetterWaveBlock = cssRuleBlockMatching(
      dashboardCss,
      (rule) =>
        rule.selector.includes('.status-letter-wave') &&
        Boolean(rule.parent?.toString().startsWith('@media (forced-colors: active)')),
    );
    expect(forcedColorsLetterWaveBlock).toContain('color: ButtonText');
    expect(forcedColorsLetterWaveBlock).not.toContain('-webkit-text-fill-color');
    expect(forcedColorsLetterWaveBlock).not.toContain('background-clip');
    const forcedColorsGlyphBlock = cssRuleBlockMatching(
      dashboardCss,
      (rule) =>
        rule.selector.includes('.status-letter-wave__glyph') &&
        Boolean(rule.parent?.toString().startsWith('@media (forced-colors: active)')),
    );
    expect(forcedColorsGlyphBlock).toContain('animation: none');
  });

  it('enforces MM-430 additive shared styling modifiers', async () => {
    for (const selector of [
      '.panel--controls',
      '.panel--data',
      '.panel--floating',
      '.panel--utility',
      '.panel.panel--data-wide',
      '.dashboard-shell-constrained--data-wide',
    ]) {
      expect(cssRuleBlock(dashboardCss, selector)).not.toBe('');
    }

    expect(cssRuleBlock(dashboardCss, '.panel.panel--data-wide')).toContain(
      'max-width: min(112rem, calc(100vw - 2rem))',
    );
  });

  it('enforces MM-430 token-first styling for semantic role surfaces', async () => {
    const semanticRoleSelectors = [
      '.panel',
      '.card',
      '.route-nav a',
      '.queue-floating-bar',
      '.queue-inline-filter',
      '.surface--glass-control, .panel--controls, .panel--floating, .panel--utility',
    ];

    for (const selector of semanticRoleSelectors) {
      const blocks = cssRuleBlocks(dashboardCss, selector);
      expect(blocks.join('\n')).toContain('var(--mm-');
      for (const block of blocks) {
        expect(block).not.toMatch(
          /(?:^|\n)(?:color|background|border|outline|box-shadow):.*?(?:#[0-9a-fA-F]{3,8}\b|rgba\(|rgb\((?!var\())/,
        );
      }
    }
  });

  it('enforces MM-430 light and dark themes through token swaps', async () => {
    for (const token of [
      '--mm-bg',
      '--mm-panel',
      '--mm-ink',
      '--mm-muted',
      '--mm-border',
      '--mm-accent',
      '--mm-glass-fill',
      '--mm-control-shell',
    ]) {
      expect(dashboardCss).toMatch(new RegExp(`:root\\s*\\{[^}]*${token}:`, 's'));
      expect(dashboardCss).toMatch(new RegExp(`\\.dark\\s*\\{[^}]*${token}:`, 's'));
    }

    expect(dashboardCss).toMatch(
      /^body\s*\{[^}]*background:\s*var\(--mm-atmosphere-violet\),\s*var\(--mm-atmosphere-cyan\),\s*var\(--mm-atmosphere-warm\),\s*var\(--mm-atmosphere-base\);/ms,
    );
    expect(dashboardCss).toMatch(
      /\.dark body\s*\{[^}]*background:\s*var\(--mm-atmosphere-violet\),\s*var\(--mm-atmosphere-cyan\),\s*var\(--mm-atmosphere-warm\),\s*var\(--mm-atmosphere-base\);/s,
    );
    expect(dashboardCss).not.toMatch(/\.dark\s+\.panel\s*\{[^}]*background:/s);
    expect(dashboardCss).not.toMatch(/\.dark\s+\.card\s*\{[^}]*background:/s);
  });

  it('defines shared interaction tokens for routine controls', async () => {
    const requiredTokens = [
      '--mm-control-hover-scale',
      '--mm-control-press-scale',
      '--mm-control-transition',
      '--mm-control-focus-ring',
      '--mm-control-disabled-opacity',
      '--mm-control-shell',
      '--mm-control-shell-hover',
      '--mm-control-border',
    ];

    for (const token of requiredTokens) {
      expect(dashboardCss).toMatch(new RegExp(`:root\\s*\\{[^}]*${token}:`, 's'));
    }
  });

  it('uses scale-only glow and grow states for routine buttons', async () => {
    const routineBlocks = [
      cssRuleBlock(
        dashboardCss,
        'button:where(:not(.secondary, .queue-action, .queue-submit-primary, .queue-step-icon-button, .queue-step-attachment-add-button, .queue-step-extension-button, .table-sort-button, .td-instructions-toggle)):hover',
      ),
      cssRuleBlock(dashboardCss, 'button.secondary:hover'),
      cssRuleBlock(
        dashboardCss,
        '.button:where(:not(.secondary, .queue-action, .queue-submit-primary, .queue-step-icon-button, .queue-step-attachment-add-button, .queue-step-extension-button, .table-sort-button, .td-instructions-toggle)):hover',
      ),
      cssRuleBlock(dashboardCss, '.button.secondary:hover'),
      cssRuleBlock(dashboardCss, '.queue-action:hover,\n.queue-submit-primary:hover'),
      cssRuleBlock(dashboardCss, '.queue-step-extension-button:hover'),
      cssRuleBlock(dashboardCss, '.queue-step-icon-button:hover'),
      cssRuleBlock(dashboardCss, '.queue-step-icon-button.destructive:hover'),
    ];

    for (const block of routineBlocks) {
      expect(block).toContain('scale(var(--mm-control-hover-scale))');
      expect(block).not.toContain('translateY');
    }

    const pressedBlocks = [
      cssRuleBlock(
        dashboardCss,
        'button:where(:not(.secondary, .queue-action, .queue-submit-primary, .queue-step-icon-button, .queue-step-attachment-add-button, .queue-step-extension-button, .table-sort-button, .td-instructions-toggle)):active',
      ),
      cssRuleBlock(
        dashboardCss,
        '.button:where(:not(.secondary, .queue-action, .queue-submit-primary, .queue-step-icon-button, .queue-step-attachment-add-button, .queue-step-extension-button, .table-sort-button, .td-instructions-toggle)):active',
      ),
      cssRuleBlock(dashboardCss, '.queue-action:active,\n.queue-submit-primary:active'),
      cssRuleBlock(dashboardCss, '.queue-step-icon-button:active'),
      cssRuleBlock(dashboardCss, '.queue-step-icon-button.destructive:active'),
    ];

    for (const block of pressedBlocks) {
      expect(block).toContain('scale(var(--mm-control-press-scale))');
      expect(block).not.toContain('translateY');
    }
  });

  it('aligns compact controls, focus rings, disabled states, and reduced motion', async () => {
    const inlineToggleBlock = cssRuleBlock(dashboardCss, '.queue-inline-toggle {');
    expect(inlineToggleBlock).toContain('padding: 0');
    expect(inlineToggleBlock).not.toContain('background: var(--mm-control-shell)');
    expect(inlineToggleBlock).not.toContain('border: 1px solid var(--mm-control-border)');

    const inlineFilterBlock = cssRuleBlock(dashboardCss, '.queue-inline-filter {');
    expect(inlineFilterBlock).toContain('background: var(--mm-control-shell)');
    expect(inlineFilterBlock).toContain('border: 1px solid var(--mm-control-border)');

    const pageSizeSelectorBlock = cssRuleBlock(dashboardCss, '.queue-page-size-selector {');
    expect(pageSizeSelectorBlock).toContain('background: transparent');
    expect(pageSizeSelectorBlock).toContain('border: 0');
    expect(pageSizeSelectorBlock).toContain('box-shadow: none');
    expect(pageSizeSelectorBlock).toContain('transition: var(--mm-control-transition)');

    const filterChipBlock = cssRuleBlock(dashboardCss, '.workflow-list-filter-chip {');
    expect(filterChipBlock).toContain('background: var(--mm-control-shell)');
    expect(filterChipBlock).toContain('border: 1px solid var(--mm-control-border)');
    const drawerFilterBlock = cssRuleBlock(
      dashboardCss,
      '.workflow-list-filter-section .workflow-list-filter-control',
    );
    expect(drawerFilterBlock).toContain('display: grid');
    expect(drawerFilterBlock).toContain('gap: 0.75rem');
    expect(drawerFilterBlock).toContain('border: 0');
    expect(drawerFilterBlock).toContain('background: transparent');
    expect(drawerFilterBlock).toContain('box-shadow: none');
    expect(
      cssRuleBlock(
        dashboardCss,
        '.workflow-list-filter-section .workflow-list-filter-control label',
      ),
    ).toContain('gap: 0.55rem');
    expect(cssRuleBlock(dashboardCss, '.workflow-list-filter-actions')).toContain(
      'border-top: 1px solid rgb(var(--mm-border) / 0.7)',
    );
    expect(cssRuleBlock(dashboardCss, '.queue-inline-filter:focus-within')).toContain(
      'box-shadow: var(--mm-control-focus-ring)',
    );
    expect(cssRuleBlock(dashboardCss, 'button:focus-visible')).toContain(
      'box-shadow: var(--mm-control-focus-ring)',
    );
    expect(cssRuleBlock(dashboardCss, 'input:focus-visible,\nselect:focus-visible,\ntextarea:focus-visible')).toContain(
      'box-shadow: var(--mm-control-focus-ring)',
    );
    expect(cssRuleBlock(dashboardCss, 'button:disabled,\nbutton:disabled:hover,\nbutton.secondary:disabled,\nbutton.secondary:disabled:hover,\nbutton:where(:not(.secondary, .queue-action, .queue-submit-primary, .queue-step-icon-button, .queue-step-attachment-add-button, .queue-step-extension-button, .table-sort-button, .td-instructions-toggle)):disabled,\nbutton:where(:not(.secondary, .queue-action, .queue-submit-primary, .queue-step-icon-button, .queue-step-attachment-add-button, .queue-step-extension-button, .table-sort-button, .td-instructions-toggle)):disabled:hover,\n.button[aria-disabled="true"],\n.button[aria-disabled="true"]:hover,\n.button.secondary[aria-disabled="true"],\n.button.secondary[aria-disabled="true"]:hover,\n.button:where(:not(.secondary, .queue-action, .queue-submit-primary, .queue-step-icon-button, .queue-step-attachment-add-button, .queue-step-extension-button, .table-sort-button, .td-instructions-toggle))[aria-disabled="true"],\n.button:where(:not(.secondary, .queue-action, .queue-submit-primary, .queue-step-icon-button, .queue-step-attachment-add-button, .queue-step-extension-button, .table-sort-button, .td-instructions-toggle))[aria-disabled="true"]:hover')).toMatch(
      /opacity:\s*var\(--mm-control-disabled-opacity\);[^}]*transform:\s*none;[^}]*box-shadow:\s*none;/s,
    );
    expect(dashboardCss).toMatch(
      /@media \(prefers-reduced-motion: reduce\)\s*\{[^}]*button,[^}]*\.button,[^}]*\.queue-action,[^}]*\.queue-submit-primary,[^}]*\.queue-step-icon-button,[^}]*\.queue-step-extension-button,[^}]*\.queue-inline-toggle,[^}]*\.queue-inline-filter,[^}]*\.queue-page-size-selector\s*\{[^}]*transition-duration:\s*0s !important;[^}]*animation-duration:\s*0s !important;[^}]*transform:\s*none !important;/s,
    );
    expect(dashboardCss).toMatch(
      /@media \(forced-colors: active\)\s*\{[^}]*button:focus-visible,[^}]*\.button:focus-visible,[^}]*\.route-nav a:focus-visible,[^}]*\.live-logs-artifact-link:focus-visible,[^}]*\.queue-action:focus-visible,[^}]*\.queue-submit-primary:focus-visible\s*\{[^}]*outline:\s*2px solid ButtonText;[^}]*outline-offset:\s*2px;/s,
    );
  });

  it('lets masthead content and chrome span the page while panels stay constrained', async () => {
    const dashboardRootBlock = cssRuleBlock(dashboardCss, '.dashboard-root');
    expect(dashboardRootBlock).toContain('--dashboard-pt: 0;');
    expect(dashboardCss).toMatch(
      /\.dashboard-shell-full\s*\{[^}]*width:\s*100%/s,
    );
    expect(dashboardCss).toMatch(
      /\.masthead::before\s*\{[^}]*left:\s*calc\(50% - 50cqw - 1rem\);[^}]*right:\s*calc\(50% - 50cqw - 1rem\);/s,
    );
    expect(dashboardCss).toMatch(
      /\.masthead::after\s*\{[^}]*left:\s*calc\(50% - 50cqw - 1rem\);[^}]*right:\s*calc\(50% - 50cqw - 1rem\);/s,
    );
  });

  it('keeps the masthead brand and list display grouped left, with version aligned right on desktop', async () => {
    const { readFileSync } = await import('node:fs');
    const dashboardCss = readFileSync(
      `${process.cwd()}/frontend/src/styles/dashboard.css`,
      'utf8',
    );

    expect(dashboardCss).toMatch(
      /\.masthead\s*\{[^}]*display:\s*grid;[^}]*grid-template-columns:\s*auto\s+auto\s+minmax\(0,\s*1fr\)\s+auto;/s,
    );
    expect(dashboardCss).toMatch(
      /\.masthead-brand\s*\{[^}]*justify-self:\s*start;/s,
    );
    expect(dashboardCss).toMatch(
      /\.workflow-list-display-control\s*\{[^}]*justify-self:\s*start;/s,
    );
    expect(dashboardCss).toMatch(
      /\.masthead-nav\s*\{[^}]*align-self:\s*stretch;[^}]*justify-content:\s*center;[^}]*justify-self:\s*center;/s,
    );
    expect(dashboardCss).toMatch(
      /\.masthead-title-meta\s*\{[^}]*justify-self:\s*end;[^}]*justify-content:\s*flex-end;/s,
    );
    const desktopMastheadRule = (selector: string) =>
      cssRuleBlockMatching(
        dashboardCss,
        (rule) =>
          normalizeCssSelector(rule.selector) === selector &&
          rule.parent?.type === 'atrule' &&
          rule.parent.name === 'media' &&
          rule.parent.params.includes('min-width: 1181px'),
      );
    expect(desktopMastheadRule('.masthead-brand')).toContain('grid-column: 1;');
    expect(desktopMastheadRule('.workflow-list-display-control')).toContain('grid-column: 2;');
    expect(desktopMastheadRule('.masthead-nav')).toContain('grid-column: 1 / -1;');
    expect(desktopMastheadRule('.masthead-title-meta')).toContain('grid-column: 4;');
    expect(cssRuleBlock(dashboardCss, '.masthead-title-meta .version-badge')).toContain('white-space: nowrap;');
  });

  it('keeps navigation positions stable across route selection changes', async () => {
    const htmlBlock = cssRuleBlock(dashboardCss, 'html');
    expect(htmlBlock).toContain('scrollbar-gutter: stable;');

    const linkBlocks = cssRuleBlocks(dashboardCss, '.route-nav a');
    expect(linkBlocks.join('\n')).not.toMatch(/transition:[^;]*\btransform\b/);
    const desktopLinkBlock = linkBlocks.find((block) =>
      block.includes('padding: 0.48rem 0.82rem;'),
    );
    expect(desktopLinkBlock).toBeDefined();
    expect(desktopLinkBlock).toContain('display: inline-flex;');
    expect(desktopLinkBlock).toContain('align-items: center;');
    expect(desktopLinkBlock).toContain('gap: 0.42rem;');
    expect(desktopLinkBlock).not.toMatch(/margin-bottom:\s*-/);

    const iconBlock = cssRuleBlock(dashboardCss, '.route-nav-icon');
    expect(iconBlock).toContain('width: 1rem;');
    expect(iconBlock).toContain('height: 1rem;');
    expect(iconBlock).toContain('flex: 0 0 auto;');

    const underlineBlocks = cssRuleBlocks(dashboardCss, '.route-nav a::after');
    expect(
      underlineBlocks.some((block) =>
        block.includes('bottom: calc(-1 * var(--masthead-padding-block-end));'),
      ),
    ).toBe(true);
    expect(underlineBlocks.some((block) => block.includes('height: 3px;'))).toBe(true);

    const activeBlocks = cssRuleBlocks(dashboardCss, '.route-nav a.active');
    expect(activeBlocks.join('\n')).not.toMatch(/transform:[^;]*\bscale\b/);
  });

  it('keeps the mobile navigation layer above route content panels', async () => {
    const mastheadBlock = cssRuleBlock(dashboardCss, '.masthead');
    expect(mastheadBlock).toContain('position: relative;');
    expect(mastheadBlock).toContain('z-index: 50;');
    expect(mastheadBlock).toContain('isolation: isolate;');

    const navBlocks = cssRuleBlocks(dashboardCss, '.route-nav');
    expect(
      navBlocks.some(
        (block) =>
          block.includes('position: fixed;') &&
          block.includes('top: 7rem;') &&
          block.includes('left: 0.875rem;') &&
          block.includes('right: 0.875rem;') &&
          block.includes('z-index: 50;'),
      ),
    ).toBe(true);
  });

  it('uses a mobile top-sheet navigation with row-style links', async () => {
    const navBlocks = cssRuleBlocks(dashboardCss, '.route-nav');
    expect(
      navBlocks.some(
        (block) =>
          block.includes('border-radius: 1.5rem;') &&
          block.includes('background: var(--mm-mobile-nav-fill);') &&
          block.includes('max-height: min(28rem, calc(100dvh - 8rem));') &&
          block.includes('overflow-y: auto;') &&
          block.includes('backdrop-filter: blur(18px);'),
      ),
    ).toBe(true);

    const linkBlocks = cssRuleBlocks(dashboardCss, '.route-nav a');
    expect(
      linkBlocks.some(
        (block) =>
          block.includes('min-height: 3.25rem;') &&
          block.includes('margin-bottom: 0;') &&
          block.includes('border: 0;') &&
          block.includes('border-radius: 1rem;') &&
          block.includes('font-weight: 600;') &&
          block.includes('background: transparent;'),
      ),
    ).toBe(true);

    const activeBlocks = cssRuleBlocks(dashboardCss, '.route-nav a.active');
    expect(
      activeBlocks.some(
        (block) =>
          block.includes('background: linear-gradient(') &&
          block.includes('var(--mm-mobile-nav-active-start)') &&
          block.includes('inset 3px 0 0 var(--mm-mobile-nav-active-edge)'),
      ),
    ).toBe(true);
  });

  it('derives mobile navigation colors from theme tokens', async () => {
    const rootBlock = cssRuleBlock(dashboardCss, ':root');
    const darkBlock = cssRuleBlock(dashboardCss, '.dark');

    expect(rootBlock).toContain('--mm-mobile-nav-fill: rgb(var(--mm-panel) / 0.92);');
    expect(rootBlock).toContain('--mm-mobile-nav-border: rgb(var(--mm-accent) / 0.35);');
    expect(rootBlock).toContain('--mm-mobile-nav-hover: rgb(var(--mm-accent) / 0.12);');
    expect(rootBlock).toContain('--mm-mobile-nav-active-start: rgb(var(--mm-accent) / 0.30);');
    expect(darkBlock).toContain('--mm-mobile-nav-active-edge: rgb(var(--mm-accent) / 0.95);');
  });

  it('keeps masthead responsiveness separate from the mobile-only display control cutoff', async () => {
    const { readFileSync } = await import('node:fs');
    const dashboardCss = readFileSync(
      `${process.cwd()}/frontend/src/styles/dashboard.css`,
      'utf8',
    );

    expect(dashboardCss).toMatch(
      /@media \(max-width: 1180px\)\s*\{[\s\S]*\.masthead\s*\{/,
    );
    expect(dashboardCss).toMatch(
      /@media \(max-width: 767px\)\s*\{[\s\S]*\.workflow-list-display-control\s*\{[^}]*display:\s*none/s,
    );
    expect(dashboardCss).toMatch(
      /@media \(max-width: 900px\)\s*\{[\s\S]*\.grid-2\s*\{/,
    );
  });

  it('renders an explicit error state for unknown pages', async () => {
    window.history.replaceState({}, '', '/not-a-page');
    renderWithClient(
      <DashboardApp payload={{ page: 'dashboard', apiBase: '/api' }} />,
    );

    expect(await screen.findByText(/Unknown dashboard page:/i)).toBeTruthy();
    expect(screen.getByText('/not-a-page')).toBeTruthy();
  });

  it('treats inherited object keys as unsupported pages', async () => {
    window.history.replaceState({}, '', '/toString');
    renderWithClient(
      <DashboardApp payload={{ page: 'dashboard', apiBase: '/api' }} />,
    );

    expect(await screen.findByText(/Unknown dashboard page:/i)).toBeTruthy();
    expect(screen.getByText('/toString')).toBeTruthy();
  });

  it('renders the OAuth terminal page and attaches through the session bridge', async () => {
    window.history.replaceState({}, '', '/oauth-terminal');
    const sentFrames: string[] = [];
    const originalClipboardDescriptor = Object.getOwnPropertyDescriptor(navigator, 'clipboard');
    const clipboardMock = { writeText: vi.fn() };
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: clipboardMock,
    });
    try {
      class MockWebSocket extends EventTarget {
        static readonly OPEN = 1;
        readonly OPEN = 1;
        readyState = 1;
        onopen: ((event: Event) => void) | null = null;
        onmessage: ((event: MessageEvent) => void) | null = null;
        onclose: ((event: CloseEvent) => void) | null = null;
        onerror: ((event: Event) => void) | null = null;
        constructor(readonly url: string) {
          super();
          setTimeout(() => {
            this.onopen?.(new Event('open'));
            this.onmessage?.(new MessageEvent('message', { data: 'Ready for login' }));
          }, 0);
        }
        send(frame: string) {
          sentFrames.push(frame);
        }
        close() {
          this.onclose?.(new CloseEvent('close'));
        }
      }
      window.WebSocket = MockWebSocket as unknown as typeof WebSocket;
      fetchSpy.mockImplementation((input: RequestInfo | URL) => {
        const url = String(input);
        if (url === '/api/v1/oauth-sessions/oas_terminal_ui') {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              session_id: 'oas_terminal_ui',
              status: 'awaiting_user',
              terminal_session_id: 'term_oas_terminal_ui',
              terminal_bridge_id: 'br_oas_terminal_ui',
            }),
          } as Response);
        }
        if (url === '/api/v1/oauth-sessions/oas_terminal_ui/terminal/attach') {
          return Promise.resolve({
            ok: true,
            json: async () => ({
              session_id: 'oas_terminal_ui',
              terminal_session_id: 'term_oas_terminal_ui',
              terminal_bridge_id: 'br_oas_terminal_ui',
              websocket_url:
                '/api/v1/oauth-sessions/oas_terminal_ui/terminal/ws?token=once',
              attach_token: 'once',
            }),
          } as Response);
        }
        if (url === '/api/v1/secrets') {
          return Promise.resolve({ ok: true, json: async () => ({ items: [] }) } as Response);
        }
        if (url === '/api/v1/provider-profiles') {
          return Promise.resolve({ ok: true, json: async () => [] } as Response);
        }
        return Promise.resolve({
          ok: false,
          status: 404,
          statusText: 'Not Found',
          text: async () => 'Unhandled fetch',
        } as Response);
      });

      renderWithClient(
        <DashboardApp
          payload={{
            page: 'dashboard',
            apiBase: '/api',
            initialData: { sessionId: 'oas_terminal_ui' },
          }}
        />,
      );

      expect(await screen.findByText('Provider Login Terminal', {}, { timeout: 3000 })).toBeTruthy();
      expect(await screen.findByText('Ready for login')).toBeTruthy();
      const terminalElement = screen.getByTestId('oauth-xterm');
      vi.spyOn(terminalElement, 'getBoundingClientRect').mockReturnValue({
        x: 10,
        y: 20,
        left: 10,
        top: 20,
        right: 90,
        bottom: 60,
        width: 80,
        height: 40,
        toJSON: () => ({}),
      } as DOMRect);

      fireEvent.contextMenu(terminalElement, {
        clientX: 24,
        clientY: 32,
      });
      const copyMenuItem = screen.getByRole('menuitem', { name: 'Copy selection' });
      await waitFor(() => {
        expect(document.activeElement).toBe(copyMenuItem);
      });
      fireEvent.contextMenu(document.body, {
        clientX: 200,
        clientY: 220,
      });
      expect(screen.queryByRole('menuitem', { name: 'Copy selection' })).toBeNull();

      fireEvent.contextMenu(terminalElement, {
        clientX: 0,
        clientY: 0,
      });
      const fallbackMenuItem = screen.getByRole('menuitem', { name: 'Copy selection' });
      const fallbackMenu = fallbackMenuItem.closest('.oauth-terminal-context-menu');
      expect(fallbackMenu).toBeInstanceOf(HTMLElement);
      expect((fallbackMenu as HTMLElement).style.left).toBe('34px');
      expect((fallbackMenu as HTMLElement).style.top).toBe('40px');

      fireEvent.click(fallbackMenuItem);
      expect(clipboardMock.writeText).toHaveBeenCalledWith('Ready for login');
      fireEvent.click(screen.getByRole('button', { name: 'Copy selection' }));
      expect(clipboardMock.writeText).toHaveBeenCalledWith('Ready for login');
      await waitFor(() => {
        expect(sentFrames.some((frame) => frame.includes('"heartbeat"'))).toBe(true);
      });
      expect(document.body.textContent).not.toContain('Docker exec');
    } finally {
      if (originalClipboardDescriptor) {
        Object.defineProperty(navigator, 'clipboard', originalClipboardDescriptor);
      } else {
        Reflect.deleteProperty(navigator, 'clipboard');
      }
    }
  });

  it('waits for OAuth terminal readiness before requesting an attach token', async () => {
    window.history.replaceState({}, '', '/oauth-terminal');
    const sentFrames: string[] = [];
    const attachCalls: string[] = [];
    const sessionStatuses = [
      { status: 'pending' },
      { status: 'starting' },
      {
        status: 'awaiting_user',
        terminal_session_id: 'term_oas_terminal_wait',
        terminal_bridge_id: 'br_oas_terminal_wait',
      },
    ];

    class MockWebSocket extends EventTarget {
      static readonly OPEN = 1;
      readonly OPEN = 1;
      readyState = 1;
      onopen: ((event: Event) => void) | null = null;
      onmessage: ((event: MessageEvent) => void) | null = null;
      onclose: ((event: CloseEvent) => void) | null = null;
      onerror: ((event: Event) => void) | null = null;
      constructor(readonly url: string) {
        super();
        setTimeout(() => {
          this.onopen?.(new Event('open'));
          this.onmessage?.(new MessageEvent('message', { data: 'Ready after wait' }));
        }, 0);
      }
      send(frame: string) {
        sentFrames.push(frame);
      }
      close() {
        this.onclose?.(new CloseEvent('close'));
      }
    }
    window.WebSocket = MockWebSocket as unknown as typeof WebSocket;

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/v1/oauth-sessions/oas_terminal_wait') {
        const nextStatus = sessionStatuses.shift() ?? {
          status: 'awaiting_user',
          terminal_session_id: 'term_oas_terminal_wait',
          terminal_bridge_id: 'br_oas_terminal_wait',
        };
        return Promise.resolve({
          ok: true,
          json: async () => ({
            session_id: 'oas_terminal_wait',
            ...nextStatus,
          }),
        } as Response);
      }
      if (url === '/api/v1/oauth-sessions/oas_terminal_wait/terminal/attach') {
        attachCalls.push(url);
        return Promise.resolve({
          ok: true,
          json: async () => ({
            session_id: 'oas_terminal_wait',
            terminal_session_id: 'term_oas_terminal_wait',
            terminal_bridge_id: 'br_oas_terminal_wait',
            websocket_url: '/api/v1/oauth-sessions/oas_terminal_wait/terminal/ws?token=once',
            attach_token: 'once',
          }),
        } as Response);
      }
      return Promise.resolve({
        ok: false,
        status: 404,
        statusText: 'Not Found',
        json: async () => ({ detail: 'Unhandled fetch' }),
      } as Response);
    });

    renderWithClient(
      <DashboardApp
        payload={{
          page: 'dashboard',
          apiBase: '/api',
          initialData: { sessionId: 'oas_terminal_wait' },
        }}
      />,
    );

    expect(await screen.findByText('Provider Login Terminal', {}, { timeout: 3000 })).toBeTruthy();
    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/v1/oauth-sessions/oas_terminal_wait',
        expect.objectContaining({ headers: { Accept: 'application/json' } }),
      );
    });
    expect(attachCalls).toEqual([]);

    await waitFor(
      () => {
        expect(attachCalls).toEqual([
          '/api/v1/oauth-sessions/oas_terminal_wait/terminal/attach',
        ]);
      },
      { timeout: 3500 },
    );
    expect(await screen.findByText('Ready after wait')).toBeTruthy();
    expect(sentFrames.some((frame) => frame.includes('"heartbeat"'))).toBe(true);
  });

  it('attaches the OAuth terminal when a Claude session reaches awaiting user', async () => {
    window.history.replaceState({}, '', '/oauth-terminal');
    const sentFrames: string[] = [];
    const attachCalls: string[] = [];
    const websocketUrls: string[] = [];
    const sessionStatuses = [
      { status: 'starting' },
      {
        status: 'awaiting_user',
        runtime_id: 'claude_code',
        profile_id: 'claude_anthropic',
        terminal_session_id: 'term_oas_claude_wait',
        terminal_bridge_id: 'br_oas_claude_wait',
      },
    ];

    class MockWebSocket extends EventTarget {
      static readonly OPEN = 1;
      readonly OPEN = 1;
      readyState = 1;
      onopen: ((event: Event) => void) | null = null;
      onmessage: ((event: MessageEvent) => void) | null = null;
      onclose: ((event: CloseEvent) => void) | null = null;
      onerror: ((event: Event) => void) | null = null;
      constructor(readonly url: string) {
        super();
        websocketUrls.push(url);
        setTimeout(() => {
          this.onopen?.(new Event('open'));
          this.onmessage?.(
            new MessageEvent('message', {
              data: 'Open https://claude.ai/login and paste the returned code',
            }),
          );
        }, 0);
      }
      send(frame: string) {
        sentFrames.push(frame);
      }
      close() {
        this.onclose?.(new CloseEvent('close'));
      }
    }
    window.WebSocket = MockWebSocket as unknown as typeof WebSocket;

    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/v1/oauth-sessions/oas_claude_wait') {
        const nextStatus = sessionStatuses.shift() ?? {
          status: 'awaiting_user',
          runtime_id: 'claude_code',
          profile_id: 'claude_anthropic',
          terminal_session_id: 'term_oas_claude_wait',
          terminal_bridge_id: 'br_oas_claude_wait',
        };
        return Promise.resolve({
          ok: true,
          json: async () => ({
            session_id: 'oas_claude_wait',
            ...nextStatus,
          }),
        } as Response);
      }
      if (url === '/api/v1/oauth-sessions/oas_claude_wait/terminal/attach') {
        attachCalls.push(url);
        return Promise.resolve({
          ok: true,
          json: async () => ({
            session_id: 'oas_claude_wait',
            terminal_session_id: 'term_oas_claude_wait',
            terminal_bridge_id: 'br_oas_claude_wait',
            websocket_url: '/api/v1/oauth-sessions/oas_claude_wait/terminal/ws?token=once',
            attach_token: 'once',
          }),
        } as Response);
      }
      return Promise.resolve({
        ok: false,
        status: 404,
        statusText: 'Not Found',
        json: async () => ({ detail: 'Unhandled fetch' }),
      } as Response);
    });

    renderWithClient(
      <DashboardApp
        payload={{
          page: 'dashboard',
          apiBase: '/api',
          initialData: { sessionId: 'oas_claude_wait' },
        }}
      />,
    );

    expect(await screen.findByText('Provider Login Terminal', {}, { timeout: 3000 })).toBeTruthy();
    expect(attachCalls).toEqual([]);

    await waitFor(
      () => {
        expect(attachCalls).toEqual([
          '/api/v1/oauth-sessions/oas_claude_wait/terminal/attach',
        ]);
      },
      { timeout: 3500 },
    );
    expect(websocketUrls).toEqual([
      'ws://localhost:3000/api/v1/oauth-sessions/oas_claude_wait/terminal/ws?token=once',
    ]);
    expect(await screen.findByText(/Open https:\/\/claude\.ai\/login/)).toBeTruthy();
    expect(sentFrames.some((frame) => frame.includes('"heartbeat"'))).toBe(true);
  });
});
