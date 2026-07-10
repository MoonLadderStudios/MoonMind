import {
  Suspense,
  lazy,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ComponentType,
  type KeyboardEvent,
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
import { Archive, PanelLeft, Rows3, ScrollText, Square } from 'lucide-react';
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
  WORKFLOW_LIST_DISPLAY_MODES,
  resolveRecurringListDisplay,
  resolveWorkflowListDisplay,
  type WorkflowListDisplayMode,
} from '../lib/workflowListDisplayMode';
import { requestRecurringScheduleFocus } from '../lib/recurringScheduleFocus';
import {
  DASHBOARD_PREFERENCES_CHANGED_EVENT,
  readDashboardPreferences,
  updateDashboardPreferences,
} from '../utils/dashboardPreferences';
import { DashboardAlerts } from './dashboard-alerts';
import {
  workflowDetailHref,
  workflowListApiQueryFromContext,
} from '../lib/workflowListContext';
import { requestWorkflowStartRouteChange } from '../lib/workflowStartRouteGuard';
import { decodeWorkflowIdFromPath } from '../lib/workflowDetailRoutes';

type PageComponent = ComponentType<{ payload: BootPayload }>;
type PageImport = () => Promise<{ default: PageComponent }>;

const PAGE_IMPORTS = {
  artifacts: () => import('./artifacts'),
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
const LIST_MODE_ICON_SIZE = 15;

type SharedLayoutConfig = {
  dataWidePanel?: boolean;
};

type NavIconProps = {
  className?: string | undefined;
};

function requestRecurringFocusForMode(
  mode: WorkflowListDisplayMode,
  definitionId: string | null | undefined,
): void {
  if (mode === 'table') {
    requestRecurringScheduleFocus(
      definitionId
        ? { target: 'table-row', definitionId }
        : { target: 'table-title' },
    );
    return;
  }
  if (mode === 'sidebar' && definitionId) {
    requestRecurringScheduleFocus({ target: 'sidebar-row', definitionId });
    return;
  }
  requestRecurringScheduleFocus(
    definitionId
      ? { target: 'detail-heading', definitionId }
      : { target: 'table-title' },
  );
}

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

function iconForWorkflowListMode(icon: string) {
  if (icon === 'Square') {
    return Square;
  }
  if (icon === 'PanelLeft') {
    return PanelLeft;
  }
  return Rows3;
}

function workflowRowId(row: unknown): string {
  if (!row || typeof row !== 'object') {
    return '';
  }
  const record = row as Record<string, unknown>;
  return String(record.workflowId || record.taskId || '').trim();
}

function firstVisibleWorkflowIdFromDocument(): string | null {
  const links = Array.from(document.querySelectorAll<HTMLAnchorElement>('a[href^="/workflows/"]'));
  for (const link of links) {
    const url = new URL(link.href, window.location.origin);
    const id = decodeWorkflowIdFromPath(url.pathname);
    if (id) {
      return id;
    }
  }
  return null;
}

async function authorizedWorkflowId(apiBase: string, workflowId: string, search: URLSearchParams): Promise<string | null> {
  const encoded = encodeURIComponent(workflowId);
  const source = search.get('source') || 'temporal';
  const params = new URLSearchParams({ source });
  const response = await fetch(`${apiBase}/executions/${encoded}?${params}`);
  if (!response.ok) {
    return null;
  }
  return workflowId;
}

async function firstVisibleWorkflowId(apiBase: string, search: URLSearchParams): Promise<string | null> {
  const response = await fetch(`${apiBase}/executions?${workflowListApiQueryFromContext(search)}`);
  if (!response.ok) {
    if (response.status === 404) {
      return firstVisibleWorkflowIdFromDocument();
    }
    throw new Error(`Failed to resolve first workflow: ${response.statusText}`);
  }
  const body = (await response.json()) as { items?: unknown[] } | null;
  const items = body && typeof body === 'object' && Array.isArray(body.items) ? body.items : null;
  const firstRow = items ? items.find((row) => workflowRowId(row)) : null;
  return firstRow ? workflowRowId(firstRow) : null;
}

async function firstVisibleRecurringDefinitionId(apiBase: string): Promise<string | null> {
  const response = await fetch(`${apiBase}/recurring-workflows?scope=personal`, { credentials: 'include' });
  if (!response.ok) {
    throw new Error(`Recurring schedule list request failed: ${response.status}`);
  }
  const payload = (await response.json()) as { items?: Array<{ id?: unknown; definitionId?: unknown }> } | null;
  const first = payload?.items?.find((item) => (
    (typeof item.id === 'string' && item.id.trim()) ||
    (typeof item.definitionId === 'string' && item.definitionId.trim())
  ));
  if (!first) {
    return null;
  }
  return typeof first.id === 'string' && first.id.trim()
    ? first.id.trim()
    : String(first.definitionId).trim();
}

async function authorizedRecurringDefinitionId(apiBase: string, definitionId: string): Promise<string | null> {
  const encoded = encodeURIComponent(definitionId);
  const response = await fetch(`${apiBase}/recurring-workflows/${encoded}`, { credentials: 'include' });
  if (!response.ok) {
    return null;
  }
  return definitionId;
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

function LazyPageView({
  page,
  payload,
  buildId,
}: {
  page: DashboardPage;
  payload: BootPayload;
  buildId?: string | null;
}) {
  const [reloadKey, setReloadKey] = useState(0);
  const LazyPage = useMemo(() => lazy(PAGE_IMPORTS[page]), [page, reloadKey]);

  return (
    <QueryErrorResetBoundary>
      {({ reset }) => (
        <DashboardRouteErrorBoundary
          key={reloadKey}
          buildId={buildId ?? null}
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

function PageContent({
  payload,
  buildId,
}: {
  payload: BootPayload;
  buildId?: string | null;
}) {
  if (!isSupportedPage(payload.page)) {
    return <UnknownPage page={payload.page} />;
  }

  const validation = validatePageBoot(payload.page, payload);
  if (!validation.ok) {
    return <ConfigurationErrorPage page={payload.page} message={validation.message} />;
  }

  return (
    <LazyPageView page={payload.page} payload={payload} buildId={buildId ?? null} />
  );
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

    const workflowIdFromUpdate = (event: MessageEvent | null): string | null => {
      if (!event?.data || typeof event.data !== 'string') {
        return null;
      }
      try {
        const payload = JSON.parse(event.data);
        if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
          return null;
        }
        const payloadRecord = payload as Record<string, unknown>;
        const row = payloadRecord.row && typeof payloadRecord.row === 'object' && !Array.isArray(payloadRecord.row)
          ? (payloadRecord.row as Record<string, unknown>)
          : null;
        return String(
          payloadRecord.workflowId ||
            payloadRecord.workflow_id ||
            payloadRecord.taskId ||
            payloadRecord.task_id ||
            row?.workflowId ||
            row?.workflow_id ||
            row?.taskId ||
            row?.task_id ||
            '',
        ).trim() || null;
      } catch {
        return null;
      }
    };

    const rowFromUpdate = (event: MessageEvent | null): Record<string, unknown> | null => {
      if (!event?.data || typeof event.data !== 'string') {
        return null;
      }
      try {
        const payload = JSON.parse(event.data);
        if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
          return null;
        }
        const payloadRecord = payload as Record<string, unknown>;
        const row = payloadRecord.row && typeof payloadRecord.row === 'object' && !Array.isArray(payloadRecord.row)
          ? (payloadRecord.row as Record<string, unknown>)
          : payloadRecord;
        const workflowId = String(row.workflowId || row.workflow_id || row.taskId || row.task_id || '').trim();
        return workflowId ? row : null;
      } catch {
        return null;
      }
    };

    const patchWorkflowListRows = (row: Record<string, unknown>) => {
      const workflowId = String(row.workflowId || row.workflow_id || row.taskId || row.task_id || '').trim();
      if (!workflowId) {
        return;
      }
      queryClient.setQueriesData({ queryKey: ['workflow-list'] }, (old: unknown) => {
        if (!old || typeof old !== 'object' || !Array.isArray((old as { items?: unknown }).items)) {
          return old;
        }
        let changed = false;
        const current = old as { items: unknown[] };
        const items = current.items.map((item) => {
          if (!item || typeof item !== 'object') {
            return item;
          }
          const itemRecord = item as Record<string, unknown>;
          const itemWorkflowId = String(
            itemRecord.workflowId ||
              itemRecord.workflow_id ||
              itemRecord.taskId ||
              itemRecord.task_id ||
              '',
          ).trim();
          if (itemWorkflowId !== workflowId) {
            return item;
          }
          changed = true;
          return { ...itemRecord, ...row };
        });
        return changed ? { ...current, items } : old;
      });
    };

    const invalidateWorkflowSnapshots = (event: MessageEvent | null = null) => {
      const workflowId = workflowIdFromUpdate(event);
      const row = rowFromUpdate(event);
      if (row) {
        patchWorkflowListRows(row);
      }
      void queryClient.invalidateQueries({ queryKey: ['tasks'] });
      void queryClient.invalidateQueries({ queryKey: ['workflow-list'] });
      if (workflowId) {
        const encodedWorkflowId = encodeURIComponent(workflowId);
        void queryClient.invalidateQueries({
          predicate: (query) => (
            query.queryKey[0] === 'workflow-detail' &&
            query.queryKey.includes(encodedWorkflowId)
          ),
        });
      } else {
        void queryClient.invalidateQueries({ queryKey: ['workflow-detail'] });
      }
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

function WorkflowListDisplayModeControl({
  accessibleName = 'Workflow list display',
  effectiveMode,
  status,
  onSelect,
}: {
  accessibleName?: string;
  effectiveMode: WorkflowListDisplayMode;
  status?: string | null | undefined;
  onSelect: (mode: WorkflowListDisplayMode) => void;
}) {
  const selectByKey = (
    event: KeyboardEvent<HTMLButtonElement>,
    currentIndex: number,
  ) => {
    const lastIndex = WORKFLOW_LIST_DISPLAY_MODES.length - 1;
    let nextIndex: number | null = null;

    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
      nextIndex = currentIndex === lastIndex ? 0 : currentIndex + 1;
    } else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
      nextIndex = currentIndex === 0 ? lastIndex : currentIndex - 1;
    } else if (event.key === 'Home') {
      nextIndex = 0;
    } else if (event.key === 'End') {
      nextIndex = lastIndex;
    }

    if (nextIndex === null) {
      return;
    }

    event.preventDefault();
    const nextMode = WORKFLOW_LIST_DISPLAY_MODES[nextIndex];
    if (!nextMode) {
      return;
    }
    onSelect(nextMode.value);
    const nextRadio = event.currentTarget.parentElement?.querySelector<HTMLButtonElement>(
      `[data-list-display-mode="${nextMode.value}"]`,
    );
    nextRadio?.focus();
  };

  return (
    <div
      className="workflow-list-display-control"
      role="radiogroup"
      aria-label={accessibleName}
    >
      {WORKFLOW_LIST_DISPLAY_MODES.map((mode, index) => {
        const Icon = iconForWorkflowListMode(mode.icon);
        const checked = effectiveMode === mode.value;
        return (
          <button
            key={mode.value}
            type="button"
            role="radio"
            aria-checked={checked}
            aria-label={mode.label}
            tabIndex={checked ? 0 : -1}
            title={mode.label}
            data-list-display-mode={mode.value}
            className={`workflow-list-display-option${checked ? ' workflow-list-display-option--selected' : ''}`}
            onClick={() => onSelect(mode.value)}
            onKeyDown={(event) => selectByKey(event, index)}
          >
            <Icon size={LIST_MODE_ICON_SIZE} aria-hidden="true" />
          </button>
        );
      })}
      {status ? (
        <span className="sr-only" role="status">
          {status}
        </span>
      ) : null}
    </div>
  );
}

function DashboardNavigation({
  uiInfo,
  listDisplayAccessibleName,
  workflowListMode,
  workflowListDisplayStatus,
  onWorkflowListModeSelect,
}: {
  uiInfo: DashboardUiInfo | null;
  listDisplayAccessibleName?: string | undefined;
  workflowListMode: WorkflowListDisplayMode | null;
  workflowListDisplayStatus?: string | null | undefined;
  onWorkflowListModeSelect: (mode: WorkflowListDisplayMode) => void;
}) {
  const [open, setOpen] = useState(false);
  const location = useLocation();
  const isWorkflowStart = location.pathname.replace(/\/$/, '') === '/workflows/new';
  const isWorkflowDetail = location.pathname.startsWith('/workflows/') && !isWorkflowStart;
  const buildId = typeof uiInfo?.buildId === 'string' && uiInfo.buildId.trim() ? uiInfo.buildId : null;

  useEffect(() => {
    setOpen(false);
  }, [location.pathname, location.search]);

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

      {workflowListMode ? (
        <WorkflowListDisplayModeControl
          {...(listDisplayAccessibleName ? { accessibleName: listDisplayAccessibleName } : {})}
          effectiveMode={workflowListMode}
          status={workflowListDisplayStatus}
          onSelect={onWorkflowListModeSelect}
        />
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
            Recurring
          </AnimatedRouteNavLink>
          <AnimatedRouteNavLink
            to="/skills"
            icon={SkillsNavIcon}
            className={({ isActive }) => (isActive ? 'active' : undefined)}
          >
            Skills
          </AnimatedRouteNavLink>
          {uiInfo?.features?.artifacts !== false ? (
            <NavLink
              to="/artifacts"
              className={({ isActive }) => (
                isActive || location.pathname === '/observability' ? 'active' : undefined
              )}
            >
              <Archive size={NAV_ICON_SIZE} className="route-nav-icon" aria-hidden="true" />
              Artifacts
            </NavLink>
          ) : null}
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
  listDisplayAccessibleName,
  workflowListMode,
  workflowListDisplayStatus,
  onWorkflowListModeSelect,
  children,
}: {
  dataWidePanel: boolean;
  uiInfo: DashboardUiInfo | null;
  listDisplayAccessibleName?: string | undefined;
  workflowListMode: WorkflowListDisplayMode | null;
  workflowListDisplayStatus?: string | null | undefined;
  onWorkflowListModeSelect: (mode: WorkflowListDisplayMode) => void;
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
          <DashboardNavigation
            uiInfo={uiInfo}
            listDisplayAccessibleName={listDisplayAccessibleName}
            workflowListMode={workflowListMode}
            workflowListDisplayStatus={workflowListDisplayStatus}
            onWorkflowListModeSelect={onWorkflowListModeSelect}
          />
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
  const navigate = useNavigate();
  const pendingRequestRef = useRef<symbol | null>(null);
  const [requestedMode, setRequestedMode] = useState<WorkflowListDisplayMode>(() => (
    readDashboardPreferences().workflowWorkspaceSidebarCollapsed ? 'hidden' : 'sidebar'
  ));
  const [requestedRecurringMode, setRequestedRecurringMode] = useState<WorkflowListDisplayMode>(
    () => readDashboardPreferences().recurringListDisplayMode,
  );
  const [lastSelectedWorkflowId, setLastSelectedWorkflowId] = useState<string | null>(
    () => readDashboardPreferences().lastSelectedWorkflowId.trim() || null,
  );
  const [lastSelectedDefinitionId, setLastSelectedDefinitionId] = useState<string | null>(
    () => readDashboardPreferences().lastSelectedDefinitionId.trim() || null,
  );
  const [resolutionStatus, setResolutionStatus] = useState<string | null>(null);
  const route = resolveDashboardRoute(location.pathname);
  const apiBase = typeof uiInfo?.apiBase === 'string' ? uiInfo.apiBase : '/api';
  const resolvedDisplay = resolveWorkflowListDisplay({
    pathname: location.pathname,
    search: location.search,
    requestedMode,
    selectedWorkflowId: lastSelectedWorkflowId,
    firstVisibleWorkflowId: null,
  });
  const resolvedRecurringDisplay = resolveRecurringListDisplay({
    pathname: location.pathname,
    search: location.search,
    requestedMode: requestedRecurringMode,
    selectedDefinitionId: lastSelectedDefinitionId,
    firstVisibleDefinitionId: null,
  });
  const activeListDisplay = resolvedDisplay ?? resolvedRecurringDisplay;
  const activeListDisplayAccessibleName = resolvedRecurringDisplay
    ? 'Recurring list display'
    : resolvedDisplay
      ? 'Workflow list display'
      : undefined;

  useEffect(() => {
    const routeWorkflowId = decodeWorkflowIdFromPath(location.pathname);
    if (routeWorkflowId) {
      setLastSelectedWorkflowId(routeWorkflowId);
    }
    const match = location.pathname.match(/^\/schedules\/([^/]+)$/);
    if (match) {
      try {
        const definitionId = decodeURIComponent(match[1] || '').trim();
        if (definitionId) {
          setLastSelectedDefinitionId(definitionId);
          // This effect also runs on `location.search` changes, so only persist
          // when the remembered definition actually changed to avoid a redundant
          // localStorage write, JSON round-trip, and change-event dispatch.
          if (readDashboardPreferences().lastSelectedDefinitionId !== definitionId) {
            updateDashboardPreferences({ lastSelectedDefinitionId: definitionId });
          }
        }
      } catch {
        // Ignore malformed paths; route validation handles unsupported pages.
      }
    }
  }, [location.pathname, location.search]);

  useEffect(() => {
    const syncPreferences = () => {
      const prefs = readDashboardPreferences();
      const normalizedPath = window.location.pathname.replace(/\/$/, '');
      // The `/workflows` route is always the full-screen table surface, and
      // `table` is not representable in the persisted collapse boolean. Deriving
      // the mode from that boolean here would clobber the table selection back to
      // `sidebar` whenever a preference change fires (for example when the
      // already-selected "Full screen table" button re-persists preferences),
      // so leave the route-owned `table` mode untouched on this surface.
      if (normalizedPath !== '/workflows') {
        setRequestedMode(prefs.workflowWorkspaceSidebarCollapsed ? 'hidden' : 'sidebar');
      }
      setLastSelectedWorkflowId(prefs.lastSelectedWorkflowId.trim() || null);
      // Recurring preferences are seeded into local state on mount, so a
      // preference change while the shell stays mounted (for example a reset from
      // another route in the same SPA session or a `storage` event from another
      // tab) must refresh them too; otherwise a stale recurring mode or remembered
      // definition survives until a full reload. Mirror the route-ownership
      // coercion applied on navigation: `/schedules` is route-owned `table`, and a
      // detail route coerces `table` -> `sidebar` while honoring a persisted
      // `hidden`.
      if (normalizedPath === '/schedules') {
        setRequestedRecurringMode('table');
      } else if (normalizedPath.startsWith('/schedules/')) {
        setRequestedRecurringMode(
          prefs.recurringListDisplayMode === 'table' ? 'sidebar' : prefs.recurringListDisplayMode,
        );
      } else {
        setRequestedRecurringMode(prefs.recurringListDisplayMode);
      }
      setLastSelectedDefinitionId(prefs.lastSelectedDefinitionId.trim() || null);
    };
    window.addEventListener(DASHBOARD_PREFERENCES_CHANGED_EVENT, syncPreferences);
    window.addEventListener('storage', syncPreferences);
    return () => {
      window.removeEventListener(DASHBOARD_PREFERENCES_CHANGED_EVENT, syncPreferences);
      window.removeEventListener('storage', syncPreferences);
    };
  }, []);

  useEffect(() => {
    const normalizedPath = location.pathname.replace(/\/$/, '');
    if (normalizedPath === '/workflows') {
      setRequestedMode('table');
    } else if (normalizedPath.startsWith('/workflows/') && normalizedPath !== '/workflows/new') {
      setRequestedMode((mode) => (mode === 'table' ? 'sidebar' : mode));
    } else if (normalizedPath === '/schedules') {
      setRequestedRecurringMode('table');
    } else if (normalizedPath.startsWith('/schedules/')) {
      setRequestedRecurringMode((mode) => (mode === 'table' ? 'sidebar' : mode));
    }
    pendingRequestRef.current = null;
    setResolutionStatus(null);
  }, [location.pathname]);

  const handleWorkflowListModeSelect = async (mode: WorkflowListDisplayMode) => {
    const selectedMode = mode;
    const search = new URLSearchParams(location.search);
    pendingRequestRef.current = null;
    setResolutionStatus(null);

    if (resolvedRecurringDisplay) {
      if (location.pathname.replace(/\/$/, '') === '/schedules' && selectedMode !== 'table') {
        const requestId = Symbol();
        pendingRequestRef.current = requestId;
        setRequestedRecurringMode(selectedMode);
        updateDashboardPreferences({ recurringListDisplayMode: selectedMode });
        setResolutionStatus('Opening first recurring schedule...');
        try {
          const rememberedId = lastSelectedDefinitionId?.trim() || '';
          const authorizedRememberedId = rememberedId
            ? await authorizedRecurringDefinitionId(apiBase, rememberedId)
            : null;
          if (pendingRequestRef.current !== requestId) {
            return;
          }
          const targetDefinitionId = authorizedRememberedId || await firstVisibleRecurringDefinitionId(apiBase);
          if (pendingRequestRef.current !== requestId) {
            return;
          }
          if (!targetDefinitionId) {
            setResolutionStatus('No recurring schedule to open.');
            return;
          }
          setLastSelectedDefinitionId(targetDefinitionId);
          updateDashboardPreferences({ lastSelectedDefinitionId: targetDefinitionId });
          setResolutionStatus(null);
          requestRecurringFocusForMode(selectedMode, targetDefinitionId);
          navigate(`/schedules/${encodeURIComponent(targetDefinitionId)}`);
        } catch {
          if (pendingRequestRef.current === requestId) {
            setResolutionStatus('Recurring schedule list is unavailable.');
          }
        }
        return;
      }

      const resolved = resolveRecurringListDisplay({
        pathname: location.pathname,
        search: location.search,
        requestedMode: selectedMode,
        selectedDefinitionId: lastSelectedDefinitionId,
        firstVisibleDefinitionId: null,
      });
      if (!resolved) {
        return;
      }
      setRequestedRecurringMode(selectedMode);
      // Batch both preference fields into a single persisted update to avoid a
      // redundant localStorage write, JSON round-trip, and change-event dispatch.
      const patch: Parameters<typeof updateDashboardPreferences>[0] = {
        recurringListDisplayMode: selectedMode,
      };
      if (resolved.selection.definitionId) {
        setLastSelectedDefinitionId(resolved.selection.definitionId);
        patch.lastSelectedDefinitionId = resolved.selection.definitionId;
      }
      updateDashboardPreferences(patch);
      requestRecurringFocusForMode(selectedMode, resolved.selection.definitionId);
      const current = `${location.pathname}${location.search}`;
      if (resolved.targetPath !== current) {
        navigate(resolved.targetPath);
      }
      return;
    }

    if (location.pathname.replace(/\/$/, '') === '/workflows' && selectedMode !== 'table') {
      const requestId = Symbol();
      pendingRequestRef.current = requestId;
      updateDashboardPreferences({ workflowWorkspaceSidebarCollapsed: selectedMode === 'hidden' });
      setRequestedMode(selectedMode);
      setResolutionStatus('Opening first workflow...');
      try {
        const rememberedId = lastSelectedWorkflowId?.trim() || '';
        const authorizedRememberedId = rememberedId
          ? await authorizedWorkflowId(apiBase, rememberedId, search)
          : null;
        if (pendingRequestRef.current !== requestId) {
          return;
        }
        const targetWorkflowId = authorizedRememberedId || await firstVisibleWorkflowId(apiBase, search);
        if (pendingRequestRef.current !== requestId) {
          return;
        }
        if (!targetWorkflowId) {
          setResolutionStatus('No workflow to open.');
          return;
        }
        setLastSelectedWorkflowId(targetWorkflowId);
        setResolutionStatus(null);
        navigate(workflowDetailHref(targetWorkflowId, search));
      } catch {
        if (pendingRequestRef.current === requestId) {
          setResolutionStatus('Workflow list is unavailable.');
        }
      }
      return;
    }

    const resolved = resolveWorkflowListDisplay({
      pathname: location.pathname,
      search: location.search,
      requestedMode: selectedMode,
      selectedWorkflowId: lastSelectedWorkflowId,
      firstVisibleWorkflowId: null,
    });
    if (!resolved) {
      return;
    }
    setRequestedMode(selectedMode);
    if (resolved.selection.workflowId) {
      setLastSelectedWorkflowId(resolved.selection.workflowId);
    }
    const current = `${location.pathname}${location.search}`;
    if (resolved.targetPath !== current) {
      if (
        location.pathname.replace(/\/$/, '') === '/workflows/new' &&
        !requestWorkflowStartRouteChange(resolved.targetPath)
      ) {
        return;
      }
      updateDashboardPreferences({ workflowWorkspaceSidebarCollapsed: selectedMode === 'hidden' });
      navigate(resolved.targetPath);
    } else {
      updateDashboardPreferences({ workflowWorkspaceSidebarCollapsed: selectedMode === 'hidden' });
    }
  };

  if (!route) {
    return (
      <AppShell
        dataWidePanel={false}
        uiInfo={uiInfo}
        listDisplayAccessibleName={activeListDisplayAccessibleName}
        workflowListMode={null}
        workflowListDisplayStatus={resolutionStatus}
        onWorkflowListModeSelect={handleWorkflowListModeSelect}
      >
        <UnknownPage page={location.pathname} />
      </AppShell>
    );
  }

  if (
    isUiInfoPending &&
    (route.page === 'workflow-start' || route.currentPath === '/workflows/new')
  ) {
    return (
      <AppShell
        dataWidePanel={route.dataWidePanel}
        uiInfo={uiInfo}
        listDisplayAccessibleName={activeListDisplayAccessibleName}
        workflowListMode={activeListDisplay?.effectiveMode ?? null}
        workflowListDisplayStatus={resolutionStatus ?? activeListDisplay?.status}
        onWorkflowListModeSelect={handleWorkflowListModeSelect}
      >
        <LoadingPage />
      </AppShell>
    );
  }

  const routedPayload = payloadForDashboardRoute(payload, route, uiInfo);
  if (resolvedDisplay) {
    routedPayload.initialData = {
      ...(routedPayload.initialData && typeof routedPayload.initialData === 'object'
        ? routedPayload.initialData
        : {}),
      workflowListDisplayMode: resolvedDisplay.effectiveMode,
      workflowListDisplayStatus: resolutionStatus ?? resolvedDisplay.status,
    };
  }
  if (resolvedRecurringDisplay) {
    routedPayload.initialData = {
      ...(routedPayload.initialData && typeof routedPayload.initialData === 'object'
        ? routedPayload.initialData
        : {}),
      recurringListDisplayMode: resolvedRecurringDisplay.effectiveMode,
      recurringListDisplayStatus: resolutionStatus ?? resolvedRecurringDisplay.status,
    };
  }
  const layout = readSharedLayout(routedPayload);
  const routeKey = route.page === 'workflows-workspace'
    ? 'workflows-workspace'
    : `${route.page}:${route.currentPath}${location.search}${location.hash}`;

  return (
    <AppShell
      dataWidePanel={layout.dataWidePanel === true}
      uiInfo={uiInfo}
      listDisplayAccessibleName={activeListDisplayAccessibleName}
      workflowListMode={activeListDisplay?.effectiveMode ?? null}
      workflowListDisplayStatus={resolutionStatus ?? activeListDisplay?.status}
      onWorkflowListModeSelect={handleWorkflowListModeSelect}
    >
      <PageContent
        key={routeKey}
        payload={routedPayload}
        buildId={uiInfo?.buildId ?? null}
      />
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
      <Route path="/workflows/:workflowId/chat" element={routedDashboardPage} />
      <Route path="/workflows/:workflowId/overview" element={routedDashboardPage} />
      <Route path="/workflows/:workflowId/execution" element={routedDashboardPage} />
      <Route path="/workflows/:workflowId/evidence" element={routedDashboardPage} />
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
      <Route path="/artifacts" element={routedDashboardPage} />
      <Route path="/observability" element={routedDashboardPage} />
      <Route
        path="*"
        element={
          <AppShell
            dataWidePanel={false}
            uiInfo={uiInfo}
            workflowListMode={null}
            onWorkflowListModeSelect={() => undefined}
          >
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
