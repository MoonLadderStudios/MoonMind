import { DataTable } from '../components/tables/DataTable';
import { useQuery } from '@tanstack/react-query';
import { mountPage } from '../boot/mountPage';

import { z } from 'zod';
import { BootPayload } from '../boot/parseBootPayload';

/** Matches moonmind.schemas.temporal_models.ExecutionModel JSON (camelCase). */
const ExecutionRowSchema = z
  .object({
    taskId: z.string(),
    source: z.string(),
    workflowType: z.string().optional(),
    targetRuntime: z.string().nullable().optional(),
    targetSkill: z.string().nullable().optional(),
    title: z.string(),
    status: z.string(),
    state: z.string(),
    temporalStatus: z.string().optional(),
    scheduledFor: z.string().nullable().optional(),
    startedAt: z.string().nullable().optional(),
    closedAt: z.string().nullable().optional(),
    createdAt: z.string(),
  })
  .passthrough();

const ExecutionListResponseSchema = z.object({
  items: z.array(ExecutionRowSchema),
  nextPageToken: z.string().nullable().optional(),
  count: z.number().nullable().optional(),
});

type ExecutionRow = z.infer<typeof ExecutionRowSchema>;

function formatWhen(iso: string | null | undefined): string {
  if (!iso) {
    return '—';
  }
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) {
    return iso;
  }
  return d.toLocaleString();
}

function statusBadgeClass(status: string): string {
  const s = status.toLowerCase();
  if (s === 'completed') return 'bg-emerald-100 text-emerald-900 dark:bg-emerald-900/40 dark:text-emerald-100';
  if (s === 'failed') return 'bg-red-100 text-red-900 dark:bg-red-900/40 dark:text-red-100';
  if (s === 'running' || s === 'queued' || s === 'waiting' || s === 'awaiting_action')
    return 'bg-blue-100 text-blue-900 dark:bg-blue-900/40 dark:text-blue-100';
  if (s === 'canceled') return 'bg-gray-200 text-gray-800 dark:bg-gray-700 dark:text-gray-100';
  return 'bg-gray-100 text-gray-800 dark:bg-gray-800 dark:text-gray-200';
}

function StatusCell({ row }: { row: ExecutionRow }) {
  const dash = row.status;
  return (
    <span className="inline-flex flex-col gap-0.5">
      <span className={`inline-block rounded px-2 py-0.5 text-xs font-semibold uppercase ${statusBadgeClass(dash)}`}>
        {row.state}
      </span>
      <span className="text-xs text-gray-600 dark:text-gray-400">{dash}</span>
      {row.temporalStatus && row.temporalStatus !== dash ? (
        <span className="text-xs text-gray-500 dark:text-gray-500">{row.temporalStatus}</span>
      ) : null}
    </span>
  );
}

function TasksListPage({ payload }: { payload: BootPayload }) {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['tasks-list', 'temporal'],
    queryFn: async () => {
      const params = new URLSearchParams({
        source: 'temporal',
        pageSize: '100',
      });
      const response = await fetch(`${payload.apiBase}/executions?${params}`);
      if (!response.ok) {
        throw new Error(`Failed to fetch: ${response.statusText}`);
      }
      return ExecutionListResponseSchema.parse(await response.json());
    },
  });

  const countLabel =
    data?.count != null ? `${data.count} task${data.count === 1 ? '' : 's'} (Temporal)` : null;

  return (
    <div className="max-w-[min(100rem,calc(100vw-2rem))] mx-auto p-6 space-y-6">
      <header className="border-b border-gray-200 dark:border-gray-700 pb-4 mb-6">
        <h2 className="text-2xl font-bold text-gray-900 dark:text-gray-100 tracking-tight">Tasks List</h2>
        <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
          Temporal-backed tasks with direct Temporal pagination (same data as the legacy dashboard table).
          {countLabel ? ` ${countLabel}.` : ''}
        </p>
      </header>
      <div className="bg-white dark:bg-gray-900/50 rounded-lg shadow-sm p-6 border border-gray-100 dark:border-gray-800">
        {isLoading ? (
          <p className="text-gray-500 dark:text-gray-400 italic animate-pulse">Loading tasks...</p>
        ) : isError ? (
          <div className="p-4 rounded-md bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-300 border border-red-200 dark:border-red-800 mb-4">
            {(error as Error).message}
          </div>
        ) : (
          <DataTable
            data={data?.items || []}
            columns={[
              {
                key: 'source',
                header: 'Type',
                render: (row: ExecutionRow) => (
                  <span className="capitalize">{row.source === 'temporal' ? 'Temporal' : row.source}</span>
                ),
              },
              {
                key: 'taskId',
                header: 'ID',
                render: (row: ExecutionRow) => (
                  <a
                    href={`/tasks/${encodeURIComponent(row.taskId)}?source=temporal`}
                    className="text-blue-600 dark:text-blue-400 hover:underline font-mono text-xs"
                  >
                    {row.taskId}
                  </a>
                ),
              },
              {
                key: 'targetRuntime',
                header: 'Runtime',
                render: (row: ExecutionRow) => row.targetRuntime || '—',
              },
              {
                key: 'targetSkill',
                header: 'Skill',
                render: (row: ExecutionRow) => row.targetSkill || '—',
              },
              {
                key: 'status',
                header: 'Status',
                render: (row: ExecutionRow) => <StatusCell row={row} />,
              },
              {
                key: 'title',
                header: 'Title',
                render: (row: ExecutionRow) => (
                  <span className="max-w-md whitespace-normal break-words text-gray-800 dark:text-gray-200">
                    {row.title}
                  </span>
                ),
              },
              {
                key: 'scheduledFor',
                header: 'Scheduled',
                render: (row: ExecutionRow) => formatWhen(row.scheduledFor),
              },
              {
                key: 'startedAt',
                header: 'Started',
                render: (row: ExecutionRow) => formatWhen(row.startedAt),
              },
              {
                key: 'closedAt',
                header: 'Finished',
                render: (row: ExecutionRow) => formatWhen(row.closedAt),
              },
            ]}
            emptyMessage="No tasks found."
            getRowKey={(item) => item.taskId}
          />
        )}
      </div>
    </div>
  );
}

mountPage(TasksListPage);
