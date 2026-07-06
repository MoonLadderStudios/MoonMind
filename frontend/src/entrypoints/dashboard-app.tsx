import {
  Suspense,
  lazy,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ComponentType,
  type Ref,
  type ReactNode,
} from 'react';
import {
  BrowserRouter,
  Link,
  Navigate,
  NavLink,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from 'react-router-dom';
import { QueryErrorResetBoundary, useQuery, useQueryClient } from '@tanstack/react-query';
import { ScrollText } from 'lucide-react';
import {
  DASHBOARD_PREFERENCES_CHANGED_EVENT,
  readDashboardPreferences,
  updateDashboardPreferences,
  type WorkflowListDisplayMode,
} from '../utils/dashboardPreferences';
import {
  WORKFLOW_START_TABLE_MODE_NAVIGATION_EVENT,
  WORKFLOW_LIST_DISPLAY_MODES,
  contractForWorkflowPath,
  effectiveWorkflowListDisplayMode,
  isWorkflowListDisplayModeSupported,
} from '../lib/workflowListDisplay';
import {
  workflowDetailHref,
  workflowListContextParams,
} from '../lib/workflowListContext';
import { WorkflowListSidebarSurface } from './workflow-list-sidebar';

import {
  MoonIcon,
  type MoonIconHandle,
  RocketIcon,
  type RocketIconHandle,
  SettingsIcon,
  type SettingsIconHandle,
  SparklesIcon,
  type SparklesIconHandle,
} from 'lucide-animated';

import type { BootPayload } from '../boot/parseBootPayload';
import { validatePageBoot } from '../boot/pageBootSchemas';
import { DashboardErrorState } from '../components/DashboardErrorState';
import { DashboardRouteErrorBoundary } from '../components/DashboardRouteErrorBoundary';
import {
  isDashboardInternalUrl,
  payloadForDashboardRoute,
  resolveDashboardRoute,
  type DashboardPage,
  type DashboardUiInfo,
} from '../lib/dashboardRoutes';
import { DashboardAlerts } from './dashboard-alerts';

type PageComponent = ComponentType<{ payload: BootPayload }>;
type PageImport = () => Promise<{ default: PageComponent }>;

const PAGE_IMPORTS = {
  'index-health': () => import('./index-health'),
  manifests: () => import('./manifests'),
  'oauth-terminal': () => import('./oauth-terminal'),
  schedules: () => import('./schedules'),
  settings: () => import('./settings'),
  skills: () => import('./skills'),
  'workflow-start': () => import('./workflow-start'),
  'workflows-workspace': () => import('./workflows-workspace'),
  'workflow-detail': () => import('./workflow-detail'),
  'workflows-home': () => import('./workflows-home'),
  'workflow-list': () => import('./workflow-list'),
} satisfies Record<DashboardPage, PageImport>;

const NAV_ICON_SIZE = 16;

type SharedLayoutConfig = {
  dataWidePanel?: boolean;
};

type NavIconProps = {
  className?: string | undefined;
};

type AnimatedNavIconHandle =
  | MoonIconHandle
  | RocketIconHandle
  | SettingsIconHandle
  | SparklesIconHandle;

type AnimatedNavIconProps = NavIconProps & {
  iconRef?: Ref<AnimatedNavIconHandle> | undefined;
};

function WorkflowsNavIcon({ className }: NavIconProps) {
  return <ScrollText size={NAV_ICON_SIZE} className={className} aria-hidden="true" />;
}

function StartWorkflowNavIcon({ className, iconRef }: AnimatedNavIconProps) {
  return (
    <RocketIcon
      ref={iconRef as Ref<RocketIconHandle>}
      size={NAV_ICON_SIZE}
      className={className}
      animateOnHover={false}
      aria-hidden="true"
    />
  );
}

function SchedulesNavIcon({ className, iconRef }: AnimatedNavIconProps) {
  return (
    <MoonIcon
      ref={iconRef as Ref<MoonIconHandle>}
      size={NAV_ICON_SIZE}
      className={className}
      animateOnHover={false}
      aria-hidden="true"
    />
  );
}

function SkillsNavIcon({ className, iconRef }: AnimatedNavIconProps) {
  return (
    <SparklesIcon
      ref={iconRef as Ref<SparklesIconHandle>}
      size={NAV_ICON_SIZE}
      className={className}
      animateOnHover={false}
      aria-hidden="true"
    />
  );
}

function SettingsNavIcon({ className, iconRef }: AnimatedNavIconProps) {
  return (
    <SettingsIcon
      ref={iconRef as Ref<SettingsIconHandle>}
      size={NAV_ICON_SIZE}
      className={className}
      animateOnHover={false}
      aria-hidden="true"
    />
  );
}

function AnimatedRouteNavLink({
  to,
  children,
  icon,
  className,
}: {
  to: string;
  children: ReactNode;
  icon: ComponentType<AnimatedNavIconProps>;
  className: ({ isActive }: { isActive: boolean }) => string | undefined;
}) {
  const Icon = icon;
  const iconRef = useRef<AnimatedNavIconHandle>(null);
  const startAnimation = () => {
    iconRef.current?.startAnimation();
  };
  const stopAnimation = () => {
    iconRef.current?.stopAnimation();
  };

  return (
    <NavLink
      to={to}
      className={className}
      onMouseEnter={startAnimation}
      onMouseLeave={stopAnimation}
      onFocus={startAnimation}
      onBlur={stopAnimation}
    >
      <Icon className="route-nav-icon" iconRef={iconRef} />
      {children}
    </NavLink>
  );
}

function isSupportedPage(page: string): page is DashboardPage {
  return Object.hasOwn(PAGE_IMPORTS, page);
}

function readSharedLayout(payload: BootPayload): SharedLayoutConfig {
  const raw = payload.initialData as { layout?: SharedLayoutConfig } | undefined;
  return raw?.layout ?? {};
}

function LoadingPage() {
  return (
    <div className="mx-auto max-w-5xl px-4 py-6 sm:px-6 lg:px-8">
      <div className="rounded-3xl border border-mm-border/80 bg-transparent p-6 text-sm text-slate-500 shadow-sm dark:text-slate-400">
        Loading MoonMind...
      </div>
    </div>
  );
}

function UnknownPage({ page }: { page: string }) {
  return (
    <div className="mx-auto max-w-5xl px-4 py-6 sm:px-6 lg:px-8">
      <div className="rounded-3xl border border-rose-200 bg-rose-50 p-6 text-sm text-rose-700 shadow-sm dark:border-rose-900/50 dark:bg-rose-900/20 dark:text-rose-400">
        Unknown dashboard page: <code>{page}</code>
      </div>
    </div>
  );
}

function ConfigurationErrorPage({ page, message }: { page: string; message: string }) {
  return (
    <DashboardErrorState
      title="Dashboard configuration error"
      description={
        <>
          The <code>{page}</code> page received invalid boot data and cannot be displayed. Please
          reload, and contact support if the problem persists.
        </>
      }
      detail={message}
    />
  );
}

function LazyPageView({ page, payload }: { page: DashboardPage; payload: BootPayload }) {
  const [reloadKey, setReloadKey] = useState(0);
  const LazyPage = useMemo(() => lazy(PAGE_IMPORTS[page]), [page, reloadKey]);

  return (
    <QueryErrorResetBoundary>
      {({ reset }) => (
        <DashboardRouteErrorBoundary
          key={reloadKey}
          onReset={() => {
            reset();
            setReloadKey((value) => value + 1);
          }}
        >
          <Suspense fallback={<LoadingPage />}>
            <LazyPage payload={payload} />
          </Suspense>
        </DashboardRouteErrorBoundary>
      )}
    </QueryErrorResetBoundary>
  );
}

function PageContent({ payload }: { payload: BootPayload }) {
  if (!isSupportedPage(payload.page)) {
    return <UnknownPage page={payload.page} />;
  }

  const validation = validatePageBoot(payload.page, payload);
  if (!validation.ok) {
    return <ConfigurationErrorPage page={payload.page} message={validation.message} />;
  }

  return <LazyPageView page={payload.page} payload={payload} />;
}

function useDashboardUiInfo() {
  return useQuery({
    queryKey: ['dashboard-ui-info'],
    queryFn: async (): Promise<DashboardUiInfo> => {
      const response = await fetch('/api/ui/info', { credentials: 'same-origin' });
      if (!response.ok) {
        throw new Error(`UI info request failed: ${response.status}`);
      }
      return (await response.json()) as DashboardUiInfo;
    },
    staleTime: 30_000,
    retry: 1,
  });
}

function DashboardLiveUpdateProvider({
  uiInfo,
  children,
}: {
  uiInfo: DashboardUiInfo | null;
  children: ReactNode;
}) {
  const queryClient = useQueryClient();
  const streamEndpoint =
    typeof uiInfo?.endpoints?.workflowUpdatesStream === 'string'
      ? uiInfo.endpoints.workflowUpdatesStream
      : null;
  const pollMs =
    typeof (uiInfo?.dashboardConfig as { pollIntervalsMs?: { list?: unknown } } | undefined)
      ?.pollIntervalsMs?.list === 'number'
      ? ((uiInfo?.dashboardConfig as { pollIntervalsMs: { list: number } }).pollIntervalsMs.list)
      : 5000;

  useEffect(() => {
    let eventSource: EventSource | null = null;
    let intervalId: number | null = null;

    const invalidateWorkflowSnapshots = () => {
      void queryClient.invalidateQueries({ queryKey: ['tasks'] });
      void queryClient.invalidateQueries({ queryKey: ['workflow-list'] });
      void queryClient.invalidateQueries({ queryKey: ['workflow-detail'] });
    };

    const start = () => {
      if (document.visibilityState === 'hidden') {
        return;
      }
      if (streamEndpoint && typeof EventSource !== 'undefined') {
        eventSource = new EventSource(streamEndpoint, { withCredentials: true });
        eventSource.onmessage = invalidateWorkflowSnapshots;
        eventSource.onerror = () => {
          eventSource?.close();
          eventSource = null;
          if (intervalId === null) {
            intervalId = window.setInterval(invalidateWorkflowSnapshots, pollMs);
          }
        };
        return;
      }
      intervalId = window.setInterval(invalidateWorkflowSnapshots, pollMs);
    };

    const stop = () => {
      eventSource?.close();
      eventSource = null;
      if (intervalId !== null) {
        window.clearInterval(intervalId);
        intervalId = null;
      }
    };

    const handleVisibility = () => {
      stop();
      start();
    };

    start();
    document.addEventListener('visibilitychange', handleVisibility);
    return () => {
      document.removeEventListener('visibilitychange', handleVisibility);
      stop();
    };
  }, [pollMs, queryClient, streamEndpoint]);

  return <>{children}</>;
}

function workflowRowId(row: unknown): string {
  if (!row || typeof row !== 'object') return '';
  const value = row as { workflowId?: unknown; taskId?: unknown };
  return String(value.workflowId || value.taskId || '').trim();
}

function useDashboardPreferenceSnapshot() {
  const [preferences, setPreferences] = useState(() => readDashboardPreferences());

  useEffect(() => {
    const sync = () => setPreferences(readDashboardPreferences());
    window.addEventListener(DASHBOARD_PREFERENCES_CHANGED_EVENT, sync);
    window.addEventListener('storage', sync);
    return () => {
      window.removeEventListener(DASHBOARD_PREFERENCES_CHANGED_EVENT, sync);
      window.removeEventListener('storage', sync);
    };
  }, []);

  return preferences;
}

function WorkflowListDisplayControl({
  uiInfo,
}: {
  uiInfo: DashboardUiInfo | null;
}) {
  const location = useLocation();
  const navigate = useNavigate();
  const preferences = useDashboardPreferenceSnapshot();
  const [statusMessage, setStatusMessage] = useState('');
  const contract = contractForWorkflowPath(location.pathname);
  const effectiveMode = effectiveWorkflowListDisplayMode(
    location.pathname,
    preferences.workflowListDisplayMode,
  );
  const apiBase = typeof uiInfo?.apiBase === 'string' ? uiInfo.apiBase : '/api';

  const resolveFirstWorkflowHref = useCallback(async (): Promise<string | null> => {
    const params = workflowListContextParams(new URLSearchParams(location.search));
    const pageSize = params.get('limit') || params.get('pageSize') || '25';
    params.delete('limit');
    params.set('pageSize', pageSize);
    if (!params.has('source')) {
      params.set('source', 'temporal');
    }
    const response = await fetch(`${apiBase}/executions?${params}`);
    if (!response.ok) {
      throw new Error(`Failed to fetch workflows: ${response.statusText}`);
    }
    const parsed = (await response.json()) as { items?: unknown[] };
    const workflowId = (parsed.items || []).map(workflowRowId).find(Boolean);
    return workflowId ? workflowDetailHref(workflowId, new URLSearchParams(location.search)) : null;
  }, [apiBase, location.search]);

  const resolveRememberedWorkflowHref = useCallback(
    async (workflowId: string): Promise<string | null> => {
      const source = new URLSearchParams(location.search).get('source') || 'temporal';
      try {
        const response = await fetch(
          `${apiBase}/executions/${encodeURIComponent(workflowId)}?source=${encodeURIComponent(source)}`,
        );
        if (!response.ok) {
          return null;
        }
        return workflowDetailHref(workflowId, new URLSearchParams(location.search));
      } catch {
        return null;
      }
    },
    [apiBase, location.search],
  );

  const selectMode = useCallback(
    async (mode: WorkflowListDisplayMode) => {
      if (!contract || !isWorkflowListDisplayModeSupported(contract, mode)) return;
      setStatusMessage('');

      if (mode === 'table' && contract.surface === 'workflow-start') {
        const guardEvent = new CustomEvent(WORKFLOW_START_TABLE_MODE_NAVIGATION_EVENT, {
          bubbles: true,
          cancelable: true,
        });
        window.dispatchEvent(guardEvent);
        if (guardEvent.defaultPrevented) {
          setStatusMessage('Review the unsaved Create draft before opening the Workflows table.');
          return;
        }
      }

      updateDashboardPreferences({
        workflowListDisplayMode: mode,
        workflowWorkspaceSidebarCollapsed: mode === 'hidden',
      });

      if (mode === 'table') {
        navigate('/workflows');
        return;
      }

      if (contract.surface === 'workflows-table') {
        const rememberedWorkflowId = preferences.lastSelectedWorkflowId.trim();
        if (rememberedWorkflowId) {
          const rememberedHref = await resolveRememberedWorkflowHref(rememberedWorkflowId);
          if (rememberedHref) {
            navigate(rememberedHref);
            return;
          }
        }
        setStatusMessage('Opening first workflow...');
        try {
          const href = await resolveFirstWorkflowHref();
          if (!href) {
            setStatusMessage('No workflow is available to open.');
            return;
          }
          navigate(href);
        } catch {
          setStatusMessage('Workflow navigation is unavailable.');
        }
      }
    },
    [
      contract,
      location.search,
      navigate,
      preferences.lastSelectedWorkflowId,
      resolveFirstWorkflowHref,
      resolveRememberedWorkflowHref,
    ],
  );

  if (!contract || !effectiveMode) {
    return null;
  }

  return (
    <div className="workflow-list-display-control-shell">
      <div
        className="workflow-list-display-control"
        role="radiogroup"
        aria-label="Workflow list display"
      >
        {WORKFLOW_LIST_DISPLAY_MODES.map((mode) => {
          const Icon = mode.icon;
          const supported = isWorkflowListDisplayModeSupported(contract, mode.value);
          return (
            <label
              key={mode.value}
              className={`workflow-list-display-option${effectiveMode === mode.value ? ' is-selected' : ''}`}
              title={mode.label}
              aria-label={mode.label}
            >
              <input
                type="radio"
                name="workflow-list-display"
                value={mode.value}
                checked={effectiveMode === mode.value}
                disabled={!supported}
                onChange={() => void selectMode(mode.value)}
              />
              <Icon aria-hidden="true" focusable="false" size={16} />
              <span className="sr-only">{mode.label}</span>
            </label>
          );
        })}
      </div>
      {statusMessage ? (
        <span className="sr-only" role="status">
          {statusMessage}
        </span>
      ) : null}
    </div>
  );
}

function DashboardNavigation({ uiInfo }: { uiInfo: DashboardUiInfo | null }) {
  const [open, setOpen] = useState(false);
  const location = useLocation();
  const isWorkflowStart = location.pathname.replace(/\/$/, '') === '/workflows/new';
  const isWorkflowDetail = location.pathname.startsWith('/workflows/') && !isWorkflowStart;
  const buildId = typeof uiInfo?.buildId === 'string' && uiInfo.buildId.trim() ? uiInfo.buildId : null;

  useEffect(() => {
    setOpen(false);
  }, [location.pathname]);

  return (
    <header className="masthead">
      <Link className="masthead-brand" to="/workflows" aria-label="MoonMind workflows">
        <img
          className="masthead-logo"
          src="/static/workflow_console/moonmindlogo.webp"
          alt="MoonMind owl and moon logo"
          width="256"
          height="199"
        />
        <h1>
          <span className="masthead-brand-moon">Moon</span>
          <span className="masthead-brand-mind">Mind</span>
        </h1>
      </Link>

      <WorkflowListDisplayControl uiInfo={uiInfo} />

      <button
        className="nav-hamburger"
        type="button"
        aria-expanded={open}
        aria-controls="dashboard-nav"
        aria-label="Toggle navigation menu"
        onClick={() => setOpen((value) => !value)}
      >
        <span className="nav-hamburger-icon" aria-hidden="true" />
      </button>

      <div className="masthead-nav">
        <nav
          className={`route-nav${open ? ' route-nav--open' : ''}`}
          id="dashboard-nav"
          aria-label="MoonMind navigation"
        >
          <NavLink
            to="/workflows"
            end
            className={({ isActive }) => (isActive || isWorkflowDetail ? 'active' : undefined)}
          >
            <WorkflowsNavIcon className="route-nav-icon" />
            Workflows
          </NavLink>
          <AnimatedRouteNavLink
            to="/workflows/new"
            icon={StartWorkflowNavIcon}
            className={({ isActive }) => (isActive ? 'active' : undefined)}
          >
            Create
          </AnimatedRouteNavLink>
          <AnimatedRouteNavLink
            to="/schedules"
            icon={SchedulesNavIcon}
            className={({ isActive }) => (isActive ? 'active' : undefined)}
          >
            Schedules
          </AnimatedRouteNavLink>
          <AnimatedRouteNavLink
            to="/skills"
            icon={SkillsNavIcon}
            className={({ isActive }) => (isActive ? 'active' : undefined)}
          >
            Skills
          </AnimatedRouteNavLink>
          <AnimatedRouteNavLink
            to="/settings"
            icon={SettingsNavIcon}
            className={({ isActive }) => (isActive ? 'active' : undefined)}
          >
            Settings
          </AnimatedRouteNavLink>
        </nav>
      </div>

      {buildId ? (
        <div className="masthead-title-meta">
          <div className="version-badge" title="MoonMind image version">
            <span className="version-badge-value">v{buildId}</span>
          </div>
        </div>
      ) : null}
    </header>
  );
}

function AppShell({
  dataWidePanel,
  uiInfo,
  workflowStartSidebar,
  children,
}: {
  dataWidePanel: boolean;
  uiInfo: DashboardUiInfo | null;
  workflowStartSidebar?: ReactNode;
  children: ReactNode;
}) {
  return (
    <DashboardLiveUpdateProvider uiInfo={uiInfo}>
      <main className="dashboard-root">
        <section className="worker-pause-banner" data-worker-pause hidden aria-live="polite">
          <p>
            <span className="worker-pause-label" data-worker-pause-status>
              Workers: Running
            </span>
            <span className="worker-pause-reason" data-worker-pause-reason />
            <Link className="worker-pause-manage" to="/settings?section=operations" data-worker-pause-manage>
              Manage operations
            </Link>
          </p>
        </section>

        <div className="dashboard-shell-full">
          <DashboardNavigation uiInfo={uiInfo} />
        </div>

        <div
          className={`dashboard-shell-constrained${dataWidePanel ? ' dashboard-shell-constrained--data-wide' : ''}`}
        >
          <DashboardAlerts />
        </div>
        <section className={`panel${dataWidePanel ? ' panel--data-wide' : ''}`} aria-live="polite">
          {workflowStartSidebar ? (
            <div className="workflow-workspace-shell workflow-workspace-shell--create">
              {workflowStartSidebar}
              <main className="workflow-workspace-detail workflow-workspace-detail--create" aria-label="Create workflow">
                {children}
              </main>
            </div>
          ) : children}
        </section>
      </main>
    </DashboardLiveUpdateProvider>
  );
}

function RoutedDashboardPage({
  payload,
  uiInfo,
  isUiInfoPending,
}: {
  payload: BootPayload;
  uiInfo: DashboardUiInfo | null;
  isUiInfoPending: boolean;
}) {
  const location = useLocation();
  const route = resolveDashboardRoute(location.pathname);
  const preferences = useDashboardPreferenceSnapshot();
  const workflowDisplayMode = effectiveWorkflowListDisplayMode(
    location.pathname,
    preferences.workflowListDisplayMode,
  );

  if (!route) {
    return (
      <AppShell dataWidePanel={false} uiInfo={uiInfo}>
        <UnknownPage page={location.pathname} />
      </AppShell>
    );
  }

  if (route.page === 'workflow-start' && isUiInfoPending) {
    return (
      <AppShell dataWidePanel={route.dataWidePanel} uiInfo={uiInfo}>
        <LoadingPage />
      </AppShell>
    );
  }

  const routedPayload = payloadForDashboardRoute(payload, route, uiInfo);
  const layout = readSharedLayout(routedPayload);
  const workflowStartSidebar = route.page === 'workflow-start' && workflowDisplayMode === 'sidebar'
    ? <WorkflowListSidebarSurface payload={routedPayload} search={new URLSearchParams(location.search)} />
    : null;
  const routeKey = route.page === 'workflows-workspace'
    ? 'workflows-workspace'
    : `${route.page}:${route.currentPath}${location.search}${location.hash}`;

  return (
    <AppShell
      dataWidePanel={layout.dataWidePanel === true || workflowStartSidebar !== null}
      uiInfo={uiInfo}
      workflowStartSidebar={workflowStartSidebar}
    >
      <PageContent key={routeKey} payload={routedPayload} />
    </AppShell>
  );
}

function DashboardRouter({ payload }: { payload: BootPayload }) {
  const uiInfoQuery = useDashboardUiInfo();
  const uiInfo = uiInfoQuery.data ?? null;
  const routedDashboardPage = (
    <RoutedDashboardPage payload={payload} uiInfo={uiInfo} isUiInfoPending={uiInfoQuery.isPending} />
  );
  const navigate = useNavigate();

  useEffect(() => {
    const handleClick = (event: MouseEvent) => {
      if (
        event.defaultPrevented ||
        event.button !== 0 ||
        event.metaKey ||
        event.ctrlKey ||
        event.shiftKey ||
        event.altKey
      ) {
        return;
      }
      const target = event.target;
      if (!(target instanceof Element)) {
        return;
      }
      const link = target.closest<HTMLAnchorElement>('a[href]');
      if (!link || link.target || link.hasAttribute('download')) {
        return;
      }
      const url = new URL(link.href, window.location.origin);
      if (!isDashboardInternalUrl(url)) {
        return;
      }
      event.preventDefault();
      navigate(`${url.pathname}${url.search}${url.hash}`);
    };

    document.addEventListener('click', handleClick);
    return () => document.removeEventListener('click', handleClick);
  }, [navigate]);

  return (
    <Routes>
      <Route path="/" element={<Navigate to="/workflows" replace />} />
      <Route path="/workflows" element={routedDashboardPage} />
      <Route path="/workflows/new" element={routedDashboardPage} />
      <Route path="/workflows/:workflowId" element={routedDashboardPage} />
      <Route path="/workflows/:workflowId/steps" element={routedDashboardPage} />
      <Route path="/workflows/:workflowId/artifacts" element={routedDashboardPage} />
      <Route path="/workflows/:workflowId/runs" element={routedDashboardPage} />
      <Route path="/workflows/:workflowId/debug" element={routedDashboardPage} />
      <Route path="/schedules" element={routedDashboardPage} />
      <Route path="/schedules/:definitionId" element={routedDashboardPage} />
      <Route path="/skills/*" element={routedDashboardPage} />
      <Route path="/settings/*" element={routedDashboardPage} />
      <Route path="/manifests" element={routedDashboardPage} />
      <Route path="/manifests/:manifestName" element={routedDashboardPage} />
      <Route path="/oauth-terminal" element={routedDashboardPage} />
      <Route path="/index-health" element={routedDashboardPage} />
      <Route
        path="*"
        element={
          <AppShell dataWidePanel={false} uiInfo={uiInfo}>
            <UnknownPage page={window.location.pathname} />
          </AppShell>
        }
      />
    </Routes>
  );
}

export function DashboardApp({ payload }: { payload: BootPayload }) {
  return (
    <BrowserRouter>
      <DashboardRouter payload={payload} />
    </BrowserRouter>
  );
}

export default DashboardApp;
