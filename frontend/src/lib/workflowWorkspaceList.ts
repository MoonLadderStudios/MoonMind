import { z } from 'zod';
import { workflowListContextParams } from './workflowListContext';

export const WorkflowWorkspaceRowSchema = z
  .object({
    taskId: z.string().optional(),
    workflowId: z.string().optional(),
    title: z.string().optional(),
    status: z.string().optional(),
    state: z.string().optional(),
    rawState: z.string().optional(),
    createdAt: z.string().nullable().optional(),
    updatedAt: z.string().nullable().optional(),
    scheduledFor: z.string().nullable().optional(),
    closedAt: z.string().nullable().optional(),
    repository: z.string().nullable().optional(),
    targetRuntime: z.string().nullable().optional(),
  })
  .strip();

export const WorkflowWorkspaceListResponseSchema = z.object({
  items: z.array(WorkflowWorkspaceRowSchema),
});

export type WorkflowWorkspaceRow = z.infer<typeof WorkflowWorkspaceRowSchema>;

type WorkflowWorkspaceDetailSource = {
  taskId?: string;
  workflowId?: string;
  title?: string;
  status?: string;
  state?: string;
  rawState?: string;
  createdAt?: string | null;
  updatedAt?: string | null;
  scheduledFor?: string | null;
  closedAt?: string | null;
  repository?: string | null;
  targetRuntime?: string | null;
};

export function workflowWorkspaceRowId(row: WorkflowWorkspaceRow): string {
  return row.workflowId || row.taskId || '';
}

export function workflowWorkspaceRowFromDetail(
  detail: WorkflowWorkspaceDetailSource,
): WorkflowWorkspaceRow {
  return {
    taskId: detail.taskId,
    workflowId: detail.workflowId,
    title: detail.title,
    status: detail.status,
    state: detail.state,
    rawState: detail.rawState,
    createdAt: detail.createdAt,
    updatedAt: detail.updatedAt,
    scheduledFor: detail.scheduledFor,
    closedAt: detail.closedAt,
    repository: detail.repository,
    targetRuntime: detail.targetRuntime,
  };
}

export function workflowWorkspaceListQuery(search: URLSearchParams, defaultSource?: string): string {
  const pageSize = search.get('limit') || search.get('pageSize') || '25';
  const params = workflowListContextParams(search);
  params.delete('limit');
  if (defaultSource && !params.has('source')) {
    params.set('source', defaultSource);
  }
  params.set('pageSize', pageSize);
  return params.toString();
}

export function workflowSidebarMatchesFilter(row: WorkflowWorkspaceRow, filter: string): boolean {
  const normalized = filter.trim().toLowerCase();
  if (!normalized) {
    return true;
  }
  return [
    row.title,
    row.workflowId,
    row.taskId,
    row.repository,
    row.targetRuntime,
    row.rawState,
    row.state,
    row.status,
  ].some((value) => String(value || '').toLowerCase().includes(normalized));
}
