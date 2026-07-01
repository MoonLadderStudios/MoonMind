import {
  Suspense,
  lazy,
  useEffect,
  useMemo,
  useState,
  type ComponentType,
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

type SharedLayoutConfig = {
  dataWidePanel?: boolean;
};

type NavIconProps = {
  className?: string | undefined;
};

function NavIcon({ className, children }: NavIconProps & { children: ReactNode }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      aria-hidden="true"
      focusable="false"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {children}
    </svg>
  );
}

function WorkflowsNavIcon({ className }: NavIconProps) {
  return (
    <NavIcon className={className}>
      <path d="M4 7h16" />
      <path d="M4 12h16" />
      <path d="M4 17h16" />
      <circle cx="7" cy="7" r="1" fill="currentColor" stroke="none" />
      <circle cx="7" cy="12" r="1" fill="currentColor" stroke="none" />
      <circle cx="7" cy="17" r="1" fill="currentColor" stroke="none" />
    </NavIcon>
  );
}

function StartWorkflowNavIcon({ className }: NavIconProps) {
  return (
    <NavIcon className={className}>
      <path d="M12 5v14" />
      <path d="M5 12h14" />
      <circle cx="12" cy="12" r="8" />
    </NavIcon>
  );
}

function SchedulesNavIcon({ className }: NavIconProps) {
  return (
    <NavIcon className={className}>
      <rect x="4" y="5" width="16" height="15" rx="2" />
      <path d="M8 3v4" />
      <path d="M16 3v4" />
      <path d="M4 10h16" />
      <path d="M9 15h3" />
    </NavIcon>
  );
}

function SkillsNavIcon({ className }: NavIconProps) {
  return (
    <NavIcon className={className}>
      <path d="M12 3l1.55 4.25L18 8.8l-4.45 1.55L12 15l-1.55-4.65L6 8.8l4.45-1.55L12 3z" />
      <path d="M19 14l.75 2 2.25.75-2.25.75L19 20l-.75-2.5L16 16.75l2.25-.75L19 14z" />
      <path d="M5 14l.75 2L8 16.75l-2.25.75L5 20l-.75-2.5L2 16.75 4.25 16 5 14z" />
    </NavIcon>
  );
}

function SettingsNavIcon({ className }: NavIconProps) {
  return (
    <NavIcon className={className}>
      <circle cx="12" cy="12" r="3" />
      <path d="M12 3v3" />
      <path d="M12 18v3" />
      <path d="M3 12h3" />
      <path d="M18 12h3" />
      <path d="M5.6 5.6l2.1 2.1" />
      <path d="M16.3 16.3l2.1 2.1" />
      <path d="M5.6 18.4l2.1-2.1" />
      <path d="M16.3 7.7l2.1-2.1" />
    </NavIcon>
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

function DashboardNavigation({ uiInfo }: { uiInfo: DashboardUiInfo | null }) {
  const [open, setOpen] = useState(false);
  const location = useLocation();
  const isWorkflowDetail =
    location.pathname.startsWith('/workflows/') && location.pathname !== '/workflows/new';
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
          <NavLink to="/workflows/new" className={({ isActive }) => (isActive ? 'active' : undefined)}>
            <StartWorkflowNavIcon className="route-nav-icon" />
            Start Workflow
          </NavLink>
          <NavLink to="/schedules" className={({ isActive }) => (isActive ? 'active' : undefined)}>
            <SchedulesNavIcon className="route-nav-icon" />
            Schedules
          </NavLink>
          <NavLink to="/skills" className={({ isActive }) => (isActive ? 'active' : undefined)}>
            <SkillsNavIcon className="route-nav-icon" />
            Skills
          </NavLink>
          <NavLink to="/settings" className={({ isActive }) => (isActive ? 'active' : undefined)}>
            <SettingsNavIcon className="route-nav-icon" />
            Settings
          </NavLink>
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
