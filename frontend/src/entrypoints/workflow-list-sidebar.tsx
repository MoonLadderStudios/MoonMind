import { useMemo } from 'react';
import { useQuery, type UseQueryResult } from '@tanstack/react-query';
import { z } from 'zod';

import type { BootPayload } from '../boot/parseBootPayload';
import { StatusIcon } from '../utils/statusIcons';
import { formatStatusLabel } from '../utils/formatters';
import { updateDashboardPreferences } from '../utils/dashboardPreferences';
import {
  workflowDetailHref,
  workflowListContextParams,
} from '../lib/workflowListContext';

const WorkflowListSidebarRowSchema = z
  .object({
    taskId: z.string().optional(),
    workflowId: z.string().optional(),
    title: z.string().optional(),
    status: z.string().optional(),
    state: z.string().optional(),
    rawState: z.string().optional(),
  })
  .strip();

const WorkflowListSidebarResponseSchema = z.object({
  items: z.array(WorkflowListSidebarRowSchema),
});

export type WorkflowListSidebarRow = z.infer<typeof WorkflowListSidebarRowSchema>;

export function workflowListSidebarRowId(row: WorkflowListSidebarRow): string {
  return row.workflowId || row.taskId || '';
}

export function workflowListSidebarQuery(search: URLSearchParams): string {
  const pageSize = search.get('limit') || search.get('pageSize') || '25';
  const params = workflowListContextParams(search);
  params.delete('limit');
  params.set('pageSize', pageSize);
  if (!params.has('source')) {
    params.set('source', 'temporal');
  }
  return params.toString();
}

function WorkflowListSidebarStatusIcon({ status }: { status: string | null | undefined }) {
  const label = formatStatusLabel(status);
  return (
    <StatusIcon
      status={status}
      domain="workflow"
      className="workflow-workspace-sidebar-status-icon"
      data-testid="workflow-workspace-sidebar-status-icon"
      aria-label={label ? `Status: ${label}` : undefined}
    />
  );
}

function WorkflowListSidebarRowLink({
  row,
  activeWorkflowId,
  search,
  pinned = false,
}: {
  row: WorkflowListSidebarRow;
  activeWorkflowId: string | null;
  search: URLSearchParams;
  pinned?: boolean;
}) {
  const workflowId = workflowListSidebarRowId(row);
  const active = Boolean(activeWorkflowId && workflowId === activeWorkflowId);
  const status = row.rawState || row.state || row.status || 'unknown';
  const title = row.title?.trim() || workflowId || 'Untitled workflow';
  return (
    <li>
      <a
        className={`workflow-workspace-sidebar-row${pinned ? ' workflow-workspace-sidebar-row-pinned' : ''}`}
        href={workflowDetailHref(workflowId, search)}
        aria-current={active ? 'page' : undefined}
        data-active={active ? 'true' : 'false'}
        data-pinned={pinned ? 'true' : 'false'}
        onClick={() => updateDashboardPreferences({ lastSelectedWorkflowId: workflowId })}
      >
        <span className="workflow-workspace-sidebar-row-main">
          {pinned ? <span className="workflow-workspace-sidebar-kicker">Current workflow</span> : null}
          <span className="workflow-workspace-sidebar-title">{title}</span>
        </span>
        <WorkflowListSidebarStatusIcon status={status} />
      </a>
    </li>
  );
}

function WorkflowListSidebarRows({
  rows,
  activeWorkflowId,
  search,
  ariaLabel = 'Workflow navigation list',
  pinned = false,
}: {
  rows: WorkflowListSidebarRow[];
  activeWorkflowId: string | null;
  search: URLSearchParams;
  ariaLabel?: string;
  pinned?: boolean;
}) {
  if (rows.length === 0) return null;
  return (
    <ul
      className={`workflow-workspace-sidebar-list${pinned ? ' workflow-workspace-sidebar-pinned-list' : ''}`}
      aria-label={ariaLabel}
    >
      {rows.map((row) => (
        <WorkflowListSidebarRowLink
          key={workflowListSidebarRowId(row)}
          row={row}
          activeWorkflowId={activeWorkflowId}
          search={search}
          pinned={pinned}
        />
      ))}
    </ul>
  );
}

export function WorkflowListSidebarSurface({
  payload,
  search,
  activeWorkflowId = null,
  pinnedCurrentRow = null,
}: {
  payload: BootPayload;
  search: URLSearchParams;
  activeWorkflowId?: string | null;
  pinnedCurrentRow?: WorkflowListSidebarRow | null;
}) {
  const listQuery = useMemo(() => workflowListSidebarQuery(search), [search]);
  const workflowsQuery: UseQueryResult<z.infer<typeof WorkflowListSidebarResponseSchema>, Error> = useQuery({
    queryKey: ['workflow-list-sidebar', listQuery],
    queryFn: async () => {
      const response = await fetch(`${payload.apiBase}/executions?${listQuery}`);
      if (!response.ok) {
        throw new Error(`Failed to fetch workflows: ${response.statusText}`);
      }
      return WorkflowListSidebarResponseSchema.parse(await response.json());
    },
    refetchInterval: 5000,
  });
  const rows = workflowsQuery.data?.items || [];
  const filteredRows = rows.filter((row) => workflowListSidebarRowId(row));

  return (
    <aside className="workflow-workspace-sidebar" aria-label="Workflow navigation">
      <div className="workflow-workspace-sidebar-header" aria-hidden="true">
        Workflow
      </div>
      {workflowsQuery.isLoading ? (
        <p className="workflow-workspace-sidebar-state">Loading workflows...</p>
      ) : null}
      {workflowsQuery.isError ? (
        <div className="workflow-workspace-sidebar-state" role="status">
          <p>Workflow navigation is unavailable.</p>
          <button type="button" className="secondary" onClick={() => void workflowsQuery.refetch()}>
            Retry
          </button>
        </div>
      ) : null}
      {!workflowsQuery.isLoading && !workflowsQuery.isError && pinnedCurrentRow ? (
        <WorkflowListSidebarRows
          rows={[pinnedCurrentRow]}
          activeWorkflowId={activeWorkflowId}
          search={search}
          ariaLabel="Current workflow"
          pinned
        />
      ) : null}
      {!workflowsQuery.isLoading && !workflowsQuery.isError && filteredRows.length === 0 ? (
        <p className="workflow-workspace-sidebar-state">No workflows match the current list filters.</p>
      ) : null}
      <WorkflowListSidebarRows rows={filteredRows} activeWorkflowId={activeWorkflowId} search={search} />
    </aside>
  );
}
