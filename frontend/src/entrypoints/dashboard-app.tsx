import {
  Suspense,
  lazy,
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
import { QueryErrorResetBoundary, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { PanelLeft, Rows3, ScrollText, Square } from 'lucide-react';
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
import {
  dispatchWorkflowListDisplayModeChange,
  type WorkflowListDisplayMode,
} from '../lib/workflowListDisplayMode';
import { workflowDetailHref } from '../lib/workflowListContext';
import { readDashboardPreferences, updateDashboardPreferences } from '../utils/dashboardPreferences';
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
const WORKFLOW_LIST_DISPLAY_ICON_SIZE = 15;

const WORKFLOW_LIST_DISPLAY_MODES = [
  { value: 'hidden', label: 'No list', icon: Square },
  { value: 'sidebar', label: 'Sidebar list', icon: PanelLeft },
  { value: 'table', label: 'Full screen table', icon: Rows3 },
] as const;

type SharedLayoutConfig = {
  dataWidePanel?: boolean;
};

type WorkflowListQueryData = {
  items?: Array<{
    taskId?: string;
    workflowId?: string;
  }>;
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

function isWorkflowStartPath(pathname: string): boolean {
  return pathname.replace(/\/$/, '') === '/workflows/new';
}

function isWorkflowDetailRoute(pathname: string): boolean {
  return pathname.startsWith('/workflows/') && !isWorkflowStartPath(pathname);
}

function isWorkflowTableRoute(pathname: string): boolean {
  return pathname.replace(/\/$/, '') === '/workflows';
}

function isWorkflowListDisplayRoute(pathname: string): boolean {
  return isWorkflowTableRoute(pathname) || isWorkflowStartPath(pathname) || isWorkflowDetailRoute(pathname);
}

function resolvedWorkflowListDisplayMode(pathname: string): WorkflowListDisplayMode | null {
  if (!isWorkflowListDisplayRoute(pathname)) {
    return null;
  }
  if (isWorkflowTableRoute(pathname)) {
    return 'table';
  }
  return readDashboardPreferences().workflowWorkspaceSidebarCollapsed ? 'hidden' : 'sidebar';
}

function firstWorkflowDetailHrefFromCache(queryClient: QueryClient, source: URLSearchParams): string | null {
  const workflowListQueries = queryClient.getQueriesData<WorkflowListQueryData>({
    queryKey: ['workflow-list'],
  });
  for (const [, data] of workflowListQueries) {
    const firstWorkflow = data?.items?.find((row) => row.workflowId || row.taskId);
    const workflowId = firstWorkflow?.workflowId || firstWorkflow?.taskId;
    if (workflowId) {
      return workflowDetailHref(workflowId, source);
    }
  }
  return null;
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

function WorkflowListDisplayControl({
  mode,
  onModeChange,
}: {
  mode: WorkflowListDisplayMode;
  onModeChange: (mode: WorkflowListDisplayMode) => void;
}) {
  return (
    <div className="workflow-list-display-control" role="radiogroup" aria-label="Workflow list display">
      {WORKFLOW_LIST_DISPLAY_MODES.map((definition) => {
        const Icon = definition.icon;
        return (
          <label
            key={definition.value}
            className="workflow-list-display-option"
            title={definition.label}
          >
            <input
              type="radio"
              name="workflow-list-display"
              value={definition.value}
              checked={mode === definition.value}
              onChange={() => onModeChange(definition.value)}
            />
            <Icon
              className="workflow-list-display-option-icon"
              size={WORKFLOW_LIST_DISPLAY_ICON_SIZE}
              aria-hidden="true"
            />
            <span className="workflow-list-display-option-label">{definition.label}</span>
          </label>
        );
      })}
    </div>
  );
}

function DashboardNavigation({ uiInfo }: { uiInfo: DashboardUiInfo | null }) {
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [selectedMode, setSelectedMode] = useState<WorkflowListDisplayMode | null>(() => {
    return resolvedWorkflowListDisplayMode(location.pathname);
  });
  const [pendingFocusMode, setPendingFocusMode] = useState<WorkflowListDisplayMode | null>(null);
  const isWorkflowDetail = isWorkflowDetailRoute(location.pathname);
  const buildId = typeof uiInfo?.buildId === 'string' && uiInfo.buildId.trim() ? uiInfo.buildId : null;

  useEffect(() => {
    setOpen(false);
  }, [location.pathname]);

  useEffect(() => {
    setSelectedMode(resolvedWorkflowListDisplayMode(location.pathname));
  }, [location.pathname]);

  useEffect(() => {
    if (!pendingFocusMode) {
      return undefined;
    }
    const timer = window.setTimeout(() => {
      const input = document.querySelector<HTMLInputElement>(
        `input[name="workflow-list-display"][value="${pendingFocusMode}"]`,
      );
      input?.focus();
      setPendingFocusMode(null);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [location.pathname, location.search, pendingFocusMode]);

  const handleWorkflowListDisplayChange = (mode: WorkflowListDisplayMode) => {
    const pathname = location.pathname;
    if (!isWorkflowListDisplayRoute(pathname)) {
      return;
    }

    setPendingFocusMode(mode);
    if (mode === 'table') {
      setSelectedMode('table');
      navigate('/workflows');
      return;
    }

    if (isWorkflowTableRoute(pathname)) {
      const firstWorkflowHref = firstWorkflowDetailHrefFromCache(queryClient, new URLSearchParams(location.search));
      if (firstWorkflowHref) {
        updateDashboardPreferences({ workflowWorkspaceSidebarCollapsed: mode === 'hidden' });
        dispatchWorkflowListDisplayModeChange(mode);
        setSelectedMode(mode);
        navigate(firstWorkflowHref);
        return;
      }
      setPendingFocusMode('table');
      setSelectedMode('table');
      return;
    }

    updateDashboardPreferences({ workflowWorkspaceSidebarCollapsed: mode === 'hidden' });
    dispatchWorkflowListDisplayModeChange(mode);
    setSelectedMode(mode);
  };

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

      {selectedMode ? (
        <WorkflowListDisplayControl mode={selectedMode} onModeChange={handleWorkflowListDisplayChange} />
      ) : null}

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
  children,
}: {
  dataWidePanel: boolean;
  uiInfo: DashboardUiInfo | null;
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
          {children}
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
  const routeKey = route.page === 'workflows-workspace'
    ? 'workflows-workspace'
    : `${route.page}:${route.currentPath}${location.search}${location.hash}`;

  return (
    <AppShell dataWidePanel={layout.dataWidePanel === true} uiInfo={uiInfo}>
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
