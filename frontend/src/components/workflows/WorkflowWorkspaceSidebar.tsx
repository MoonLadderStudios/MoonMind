import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent,
  type Ref,
  type ReactNode,
} from 'react';
import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import { Link, useInRouterContext } from 'react-router-dom';
import {
  BotIcon,
  type BotIconHandle,
  LoaderCircleIcon,
  type LoaderCircleIconHandle,
  RouteIcon,
  type RouteIconHandle,
} from 'lucide-animated';
import { z } from 'zod';
import type { BootPayload } from '../../boot/parseBootPayload';
import {
  WorkflowColumnFilterButton,
  WorkflowColumnHeader,
} from '../WorkflowColumnHeader';
import { workflowDetailHref } from '../../lib/workflowListContext';
import { requestWorkflowStartRouteChange } from '../../lib/workflowStartRouteGuard';
import { updateDashboardPreferences } from '../../utils/dashboardPreferences';
import {
  WorkflowWorkspaceListResponseSchema,
  type WorkflowWorkspaceRow,
  workflowSidebarMatchesFilter,
  workflowWorkspaceRowId,
} from '../../lib/workflowWorkspaceList';
import {
  buildWorkflowListQueryKey,
  buildWorkflowListQueryParams,
  workflowListQueryString,
} from '../../lib/workflowListQuery';
import { formatStatusLabel } from '../../utils/formatters';
import { StatusIcon } from '../../utils/statusIcons';
import { executionStatusPillProps } from '../../utils/executionStatusPillClasses';

type DashboardConfig = {
  pollIntervalsMs?: { list?: number };
  features?: {
    temporalDashboard?: {
      listEnabled?: boolean;
    };
  };
};

const REDUCED_MOTION_QUERY = '(prefers-reduced-motion: reduce)';
const WORKFLOW_SIDEBAR_ANIMATED_STATUS = {
  initializing: LoaderCircleIcon,
  executing: BotIcon,
  planning: RouteIcon,
  finalizing: RouteIcon,
} as const;
type SidebarAnimatedWorkflowStatus = keyof typeof WORKFLOW_SIDEBAR_ANIMATED_STATUS;
export const WORKFLOW_SIDEBAR_ROUTE_ICON_ANIMATION_MS = 1200;
export const WORKFLOW_SIDEBAR_ANIMATED_RESTART_MS = {
  initializing: undefined,
  executing: 650,
  planning: 1400,
  finalizing: 1400,
} as const satisfies Readonly<Record<SidebarAnimatedWorkflowStatus, number | undefined>>;
type WorkflowSidebarStatusIconHandle = LoaderCircleIconHandle | BotIconHandle | RouteIconHandle;

function readDashboardConfig(payload: BootPayload): DashboardConfig | undefined {
  const raw = payload.initialData as { dashboardConfig?: DashboardConfig } | undefined;
  return raw?.dashboardConfig;
}

function AnimatedWorkflowSidebarStatusIcon({
  status,
  className,
  title,
}: {
  status: SidebarAnimatedWorkflowStatus;
  className?: string;
  title: string;
}) {
  const iconRef = useRef<WorkflowSidebarStatusIconHandle | null>(null);
  const Icon = WORKFLOW_SIDEBAR_ANIMATED_STATUS[status];
  const pillProps = executionStatusPillProps(status, { enableMotion: false });

  useEffect(() => {
    let active = true;
    let timerId: number | null = null;
    const replayMs = WORKFLOW_SIDEBAR_ANIMATED_RESTART_MS[status];
    const reducedMotion = typeof window !== 'undefined'
      && typeof window.matchMedia === 'function'
      && window.matchMedia(REDUCED_MOTION_QUERY).matches;

    const loop = () => {
      if (reducedMotion) {
        return;
      }
      if (!iconRef.current || !active) {
        return;
      }
      iconRef.current.startAnimation();
      if (!active || replayMs == null) {
        return;
      }
      timerId = window.setTimeout(() => {
        if (!active) {
          return;
        }
        void loop();
      }, replayMs);
    };

    void loop();

    return () => {
      active = false;
      if (timerId !== null) {
        window.clearTimeout(timerId);
      }
      iconRef.current?.stopAnimation();
    };
  }, [status]);

  const ref = iconRef as Ref<WorkflowSidebarStatusIconHandle> | null;

  return (
    <span
      {...pillProps}
      className={`${pillProps.className}${className ? ` ${className}` : ''}`}
      aria-label={`Status: ${title}`}
      title={title}
      data-testid="workflow-workspace-sidebar-status-icon"
    >
      <Icon
        ref={ref}
        size={16}
        animateOnHover={false}
        aria-hidden="true"
      />
    </span>
  );
}

function isSidebarAnimatedWorkflowStatus(status: string): status is SidebarAnimatedWorkflowStatus {
  return Object.prototype.hasOwnProperty.call(WORKFLOW_SIDEBAR_ANIMATED_STATUS, status);
}

function sidebarStatusLabel(status: string | null | undefined): string {
  const label = formatStatusLabel(status);
  return label.length > 0 ? `${label.charAt(0).toUpperCase()}${label.slice(1)}` : label;
}

function WorkflowSidebarStatusIcon({ status }: { status: string | null | undefined }) {
  let normalizedStatus = String(status || '')
    .toLowerCase()
    .trim()
    .replace(/\s+/g, '_');
  if (normalizedStatus === 'running') {
    normalizedStatus = 'executing';
  }
  if (isSidebarAnimatedWorkflowStatus(normalizedStatus)) {
    const label = sidebarStatusLabel(status);
    return (
      <AnimatedWorkflowSidebarStatusIcon
        status={normalizedStatus}
        title={label}
        className="workflow-workspace-sidebar-status-icon"
      />
    );
  }

  return (
    <StatusIcon
      status={status}
      domain="workflow"
      className="workflow-workspace-sidebar-status-icon"
      data-testid="workflow-workspace-sidebar-status-icon"
    />
  );
}

function WorkflowSidebarRow({
  row,
  activeWorkflowId,
  search,
  pinned = false,
}: {
  row: WorkflowWorkspaceRow;
  activeWorkflowId: string | null | undefined;
  search: URLSearchParams;
  pinned?: boolean;
}) {
  const workflowId = workflowWorkspaceRowId(row);
  const active = Boolean(activeWorkflowId) && workflowId === activeWorkflowId;
  const status = row.rawState || row.state || row.status || 'unknown';
  const title = row.title?.trim() || workflowId || 'Untitled workflow';
  const href = workflowDetailHref(workflowId, search);
  const inRouterContext = useInRouterContext();
  const className = `workflow-workspace-sidebar-row${pinned ? ' workflow-workspace-sidebar-row-pinned' : ''}`;
  const handleClick = (event: MouseEvent<HTMLAnchorElement>) => {
    if (
      !event.defaultPrevented
      && event.button === 0
      && !event.metaKey
      && !event.ctrlKey
      && !event.shiftKey
      && !event.altKey
      && !requestWorkflowStartRouteChange(href)
    ) {
      event.preventDefault();
      return;
    }
    updateDashboardPreferences({ lastSelectedWorkflowId: workflowId });
  };
  const content = (
    <>
      <span className="workflow-workspace-sidebar-row-main">
        {pinned ? <span className="workflow-workspace-sidebar-kicker">Current workflow</span> : null}
        <span className="workflow-workspace-sidebar-title">{title}</span>
      </span>
      <WorkflowSidebarStatusIcon status={status} />
    </>
  );
  return (
    <div
      role="row"
      className={`workflow-workspace-sidebar-row-frame${pinned ? ' workflow-workspace-sidebar-row-frame-pinned' : ''}`}
    >
      <div role="cell" className="workflow-workspace-sidebar-cell">
        {inRouterContext ? (
          <Link
            className={className}
            to={href}
            aria-current={active ? 'page' : undefined}
            data-active={active ? 'true' : 'false'}
            data-pinned={pinned ? 'true' : 'false'}
            onClick={handleClick}
          >
            {content}
          </Link>
        ) : (
          <a
            className={className}
            href={href}
            aria-current={active ? 'page' : undefined}
            data-active={active ? 'true' : 'false'}
            data-pinned={pinned ? 'true' : 'false'}
            onClick={handleClick}
          >
            {content}
          </a>
        )}
      </div>
    </div>
  );
}

function WorkflowSidebarList({
  rows,
  activeWorkflowId,
  search,
  ariaLabel = 'Workflow navigation list',
  pinned = false,
}: {
  rows: WorkflowWorkspaceRow[];
  activeWorkflowId: string | null | undefined;
  search: URLSearchParams;
  ariaLabel?: string;
  pinned?: boolean;
}) {
  if (rows.length === 0) {
    return null;
  }

  return (
    <div
      role="rowgroup"
      className={`workflow-workspace-sidebar-list${pinned ? ' workflow-workspace-sidebar-pinned-list' : ''}`}
      aria-label={ariaLabel}
    >
      {rows.map((row) => (
        <WorkflowSidebarRow
          key={workflowWorkspaceRowId(row)}
          row={row}
          activeWorkflowId={activeWorkflowId}
          search={search}
          pinned={pinned}
        />
      ))}
    </div>
  );
}

function WorkflowSidebarTableFrame({ children }: { children: ReactNode }) {
  return (
    <div
      role="table"
      aria-label="Workflow list table slice"
      className="workflow-workspace-sidebar-table"
    >
      {children}
    </div>
  );
}

function WorkflowSidebarState({
  children,
  role,
}: {
  children: ReactNode;
  role?: 'status';
}) {
  return (
    <div role="rowgroup" className="workflow-workspace-sidebar-state-group">
      <div role="row" className="workflow-workspace-sidebar-row-frame">
        <div role="cell" className="workflow-workspace-sidebar-cell">
          <div className="workflow-workspace-sidebar-state" role={role}>
            {children}
          </div>
        </div>
      </div>
    </div>
  );
}

function WorkflowSidebarHeader({
  filterText,
  setFilterText,
}: {
  filterText: string;
  setFilterText: (value: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const filterRef = useRef<HTMLDivElement | null>(null);
  const active = filterText.trim().length > 0;

  useEffect(() => {
    if (!open) {
      return undefined;
    }
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target;
      if (target instanceof Node && filterRef.current?.contains(target)) {
        return;
      }
      setOpen(false);
    };
    document.addEventListener('pointerdown', onPointerDown);
    return () => document.removeEventListener('pointerdown', onPointerDown);
  }, [open]);

  return (
    <div role="rowgroup" className="workflow-workspace-sidebar-header">
      <div role="row" className="workflow-workspace-sidebar-header-row">
        <div role="columnheader" className="workflow-workspace-sidebar-header-cell">
          <WorkflowColumnHeader
            label={<span className="workflow-workspace-sidebar-header-title">Workflow</span>}
            filterButton={
              <WorkflowColumnFilterButton
                active={active}
                expanded={open}
                ariaLabel={active ? `Workflow sidebar filter: ${filterText}` : 'Workflow sidebar filter. No filter applied.'}
                onClick={() => setOpen((value) => !value)}
              />
            }
            filterRef={filterRef}
          >
            {open ? (
              <div
                className="workflow-workspace-sidebar-filter-popover workflow-list-column-filter-popover"
                role="dialog"
                aria-label="Workflow sidebar filter"
                onKeyDown={(event) => {
                  if (event.key === 'Escape') {
                    event.stopPropagation();
                    setOpen(false);
                  }
                }}
              >
                <div className="workflow-list-column-filter-title">Workflow filter</div>
                <label className="workflow-list-filter-control">
                  <span>Workflow</span>
                  <input
                    type="search"
                    value={filterText}
                    onChange={(event) => setFilterText(event.target.value)}
                    placeholder="Filter workflows"
                    aria-label="Workflow sidebar filter value"
                    autoFocus
                  />
                </label>
                <div className="workflow-list-filter-actions">
                  <button
                    type="button"
                    className="secondary"
                    onClick={() => setFilterText('')}
                    disabled={!active}
                    aria-label="Reset workflow sidebar filter"
                  >
                    Reset
                  </button>
                  <button
                    type="button"
                    onClick={() => setOpen(false)}
                    aria-label="Apply workflow sidebar filter"
                  >
                    Apply
                  </button>
                </div>
              </div>
            ) : null}
          </WorkflowColumnHeader>
        </div>
      </div>
    </div>
  );
}

function WorkflowSidebar({
  workflowId,
  workflowsQuery,
  filteredRows,
  pinnedCurrentRow,
  search,
  filterText,
  setFilterText,
  listEnabled = true,
}: {
  workflowId: string | null | undefined;
  workflowsQuery: UseQueryResult<z.infer<typeof WorkflowWorkspaceListResponseSchema>, Error>;
  filteredRows: WorkflowWorkspaceRow[];
  pinnedCurrentRow: WorkflowWorkspaceRow | null;
  search: URLSearchParams;
  filterText: string;
  setFilterText: (value: string) => void;
  listEnabled?: boolean;
}) {
  return (
    <aside className="workflow-workspace-sidebar" aria-label="Workflow navigation">
      <WorkflowSidebarTableFrame>
        <WorkflowSidebarHeader filterText={filterText} setFilterText={setFilterText} />
        {!listEnabled ? (
          <WorkflowSidebarState>Workflow navigation is disabled.</WorkflowSidebarState>
        ) : null}
        {workflowsQuery.isLoading ? (
          <WorkflowSidebarState>Loading workflows...</WorkflowSidebarState>
        ) : null}
        {workflowsQuery.isError ? (
          <WorkflowSidebarState role="status">
            <p>Workflow navigation is unavailable.</p>
            <button type="button" className="secondary" onClick={() => void workflowsQuery.refetch()}>
              Retry
            </button>
          </WorkflowSidebarState>
        ) : null}
        {!workflowsQuery.isLoading && !workflowsQuery.isError && pinnedCurrentRow ? (
          <WorkflowSidebarList
            rows={[pinnedCurrentRow]}
            activeWorkflowId={workflowId}
            search={search}
            ariaLabel="Current workflow"
            pinned
          />
        ) : null}
        {!workflowsQuery.isLoading && !workflowsQuery.isError && filteredRows.length === 0 ? (
          <WorkflowSidebarState>No workflows match the current list filters.</WorkflowSidebarState>
        ) : null}
        <WorkflowSidebarList rows={filteredRows} activeWorkflowId={workflowId} search={search} />
      </WorkflowSidebarTableFrame>
    </aside>
  );
}

export function WorkflowWorkspaceSidebarPanel({
  payload,
  search,
  activeWorkflowId = null,
  pinnedCurrentRow = null,
  defaultSource,
}: {
  payload: BootPayload;
  search: URLSearchParams;
  activeWorkflowId?: string | null;
  pinnedCurrentRow?: WorkflowWorkspaceRow | null;
  defaultSource?: string;
}) {
  const cfg = readDashboardConfig(payload);
  const listPoll = cfg?.pollIntervalsMs?.list ?? 5000;
  const listEnabled = cfg?.features?.temporalDashboard?.listEnabled !== false;
  const searchKey = search.toString();
  const listQueryParams = useMemo(() => {
    const params = new URLSearchParams(searchKey);
    if (defaultSource && !params.has('source')) {
      params.set('source', defaultSource);
    }
    return buildWorkflowListQueryParams(params);
  }, [defaultSource, searchKey]);
  const listQueryKey = useMemo(() => buildWorkflowListQueryKey(listQueryParams), [listQueryParams]);
  const listQuery = useMemo(() => workflowListQueryString(listQueryParams), [listQueryParams]);
  const [sidebarFilterText, setSidebarFilterText] = useState('');
  const workflowsQuery = useQuery({
    queryKey: listQueryKey,
    queryFn: async ({ signal }) => {
      const response = await fetch(`${payload.apiBase}/executions?${listQuery}`, { signal });
      if (!response.ok) {
        throw new Error(`Failed to fetch workflows: ${response.statusText}`);
      }
      return WorkflowWorkspaceListResponseSchema.parse(await response.json());
    },
    enabled: listEnabled,
    refetchInterval: listEnabled ? listPoll : false,
    staleTime: listPoll,
  });
  if (!listEnabled) {
    return null;
  }
  const rows = workflowsQuery.data?.items || [];
  const activeInList = rows.some((row) => workflowWorkspaceRowId(row) === activeWorkflowId);
  const pinnedRow = pinnedCurrentRow && !activeInList ? pinnedCurrentRow : null;
  const filteredRows = useMemo(() => (
    rows.filter((row) => (
      workflowWorkspaceRowId(row)
      && workflowSidebarMatchesFilter(row, sidebarFilterText)
    ))
  ), [rows, sidebarFilterText]);

  return (
    <WorkflowSidebar
      workflowId={activeWorkflowId}
      workflowsQuery={workflowsQuery}
      filteredRows={filteredRows}
      pinnedCurrentRow={pinnedRow}
      search={search}
      filterText={sidebarFilterText}
      setFilterText={setSidebarFilterText}
      listEnabled={listEnabled}
    />
  );
}
