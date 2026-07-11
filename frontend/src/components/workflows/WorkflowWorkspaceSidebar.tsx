import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent,
  type Ref,
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
  buildWorkflowListQueryParams,
  workflowListQueryString,
} from '../../lib/workflowListQuery';
import { resolveWorkflowDisplayStatus } from '../../status/workflowStatus';
import { formatStatusLabel } from '../../utils/formatters';
import { StatusIcon } from '../../utils/statusIcons';
import { executionStatusPillProps } from '../../utils/executionStatusPillClasses';
import {
  CollectionSidebar,
  CollectionSidebarFilterHeader,
  type CollectionSidebarFilterCopy,
  type CollectionSidebarRow,
} from '../CollectionSidebar';

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
  const normalizedStatus = resolveWorkflowDisplayStatus(status) ?? '';
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

const WORKFLOW_SIDEBAR_FILTER_COPY: CollectionSidebarFilterCopy = {
  columnHeader: 'Workflow',
  dialogLabel: 'Workflow sidebar filter',
  dialogTitle: 'Workflow filter',
  fieldLabel: 'Workflow',
  placeholder: 'Filter workflows',
  inputLabel: 'Workflow sidebar filter value',
  triggerIdleLabel: 'Workflow sidebar filter. No filter applied.',
  triggerActiveLabel: (value) => `Workflow sidebar filter: ${value}`,
  resetLabel: 'Reset workflow sidebar filter',
  applyLabel: 'Apply workflow sidebar filter',
};

function WorkflowSidebar({
  workflowId,
  workflowsQuery,
  filteredRows,
  pinnedCurrentRow,
  search,
  filterText,
  setFilterText,
}: {
  workflowId: string | null | undefined;
  workflowsQuery: UseQueryResult<z.infer<typeof WorkflowWorkspaceListResponseSchema>, Error>;
  filteredRows: WorkflowWorkspaceRow[];
  pinnedCurrentRow: WorkflowWorkspaceRow | null;
  search: URLSearchParams;
  filterText: string;
  setFilterText: (value: string) => void;
}) {
  const inRouterContext = useInRouterContext();
  const adaptRow = (row: WorkflowWorkspaceRow): CollectionSidebarRow => {
    const id = workflowWorkspaceRowId(row);
    return {
      id,
      href: workflowDetailHref(id, search),
      primaryText: row.title?.trim() || id || 'Untitled workflow',
      metadata: <WorkflowSidebarStatusIcon status={row.rawState || row.state || row.status || 'unknown'} />,
    };
  };
  const rows = filteredRows.map(adaptRow);
  const pinnedRow = pinnedCurrentRow ? adaptRow(pinnedCurrentRow) : null;
  return (
    <CollectionSidebar
      landmarkLabel="Workflow navigation"
      tableLabel="Workflow list table slice"
      header="Workflow"
      filterLabel="Workflow sidebar filter value"
      filterPlaceholder="Filter workflows"
      rows={rows}
      activeId={workflowId ?? null}
      pinnedRow={pinnedRow}
      isLoading={workflowsQuery.isLoading}
      error={workflowsQuery.isError ? workflowsQuery.error : null}
      onRetry={() => void workflowsQuery.refetch()}
      loadingCopy="Loading workflows..."
      emptyCopy="No workflows match the current list filters."
      filteredEmptyCopy="No workflows match the current list filters."
      errorCopy="Workflow navigation is unavailable."
      currentRowCopy="Current workflow"
      filterValue={filterText}
      onFilterChange={setFilterText}
      externalFiltering
      className="workflow-collection-sidebar"
      headerContent={(
        <CollectionSidebarFilterHeader
          copy={WORKFLOW_SIDEBAR_FILTER_COPY}
          filterText={filterText}
          setFilterText={setFilterText}
        />
      )}
      renderLink={(row, props) => {
        const handleClick = (event: MouseEvent<HTMLAnchorElement>) => {
          if (
            !event.defaultPrevented && event.button === 0 && !event.metaKey && !event.ctrlKey
            && !event.shiftKey && !event.altKey && !requestWorkflowStartRouteChange(row.href)
          ) {
            event.preventDefault();
            return;
          }
          updateDashboardPreferences({ lastSelectedWorkflowId: row.id });
        };
        return inRouterContext
          ? <Link to={row.href} {...props} onClick={handleClick} />
          : <a href={row.href} {...props} onClick={handleClick} />;
      }}
    />
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
  const listQueryKey = useMemo(() => ['workflow-workspace-sidebar', listQueryParams] as const, [listQueryParams]);
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
    />
  );
}
