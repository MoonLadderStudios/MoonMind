import { useCallback, useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { mountPage } from '../boot/mountPage';

import { z } from 'zod';
import { BootPayload } from '../boot/parseBootPayload';

const PAGE_SIZE_OPTIONS = [20, 25, 50, 100] as const;
const DEFAULT_PAGE_SIZE = 50;
const POLL_MS_DEFAULT = 5000;

type ListDashboardConfig = {
  pollIntervalsMs?: { list?: number };
  features?: {
    temporalDashboard?: {
      listEnabled?: boolean;
    };
  };
};

function readListDashboardConfig(payload: BootPayload): ListDashboardConfig | undefined {
  const raw = payload.initialData as { dashboardConfig?: ListDashboardConfig } | undefined;
  return raw?.dashboardConfig;
}

const WORKFLOW_TYPES = ['MoonMind.Run', 'MoonMind.ManifestIngest'] as const;
const TEMPORAL_STATES = [
  'initializing',
  'planning',
  'executing',
  'awaiting_external',
  'finalizing',
  'succeeded',
  'failed',
  'canceled',
] as const;
const ENTRY_OPTIONS = ['run', 'manifest'] as const;

const TIMESTAMP_SORT_FIELDS = new Set(['scheduledFor', 'createdAt', 'startedAt', 'closedAt']);

/** Column sort keys (excludes removed legacy "type" / source column). */
const TABLE_COLUMNS = [
  ['taskId', 'ID'],
  ['targetRuntime', 'Runtime'],
  ['targetSkill', 'Skill'],
  ['status', 'Status'],
  ['title', 'Title'],
  ['scheduledFor', 'Scheduled'],
  ['startedAt', 'Started'],
  ['closedAt', 'Finished'],
] as const;

const VALID_TABLE_SORT_FIELDS = new Set<string>(TABLE_COLUMNS.map((c) => c[0]));

function normalizeTableSortField(raw: string | null): string {
  const s = (raw || '').trim();
  return VALID_TABLE_SORT_FIELDS.has(s) ? s : 'scheduledFor';
}

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
    rawState: z.string().optional(),
    temporalStatus: z.string().optional(),
    scheduledFor: z.string().nullable().optional(),
    startedAt: z.string().nullable().optional(),
    closedAt: z.string().nullable().optional(),
    createdAt: z.string(),
    entry: z.string().optional(),
  })
  .passthrough();

const ExecutionListResponseSchema = z.object({
  items: z.array(ExecutionRowSchema),
  nextPageToken: z.string().nullable().optional(),
  count: z.number().nullable().optional(),
  countMode: z.string().optional(),
});

type ExecutionRow = z.infer<typeof ExecutionRowSchema>;

function parsePageSize(raw: string | null): number {
  const n = Number(raw || DEFAULT_PAGE_SIZE);
  return PAGE_SIZE_OPTIONS.includes(n as (typeof PAGE_SIZE_OPTIONS)[number])
    ? n
    : DEFAULT_PAGE_SIZE;
}

function formatWhen(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

/** MoonMind dashboard shell tokens — matches `dashboard.tailwind.css` `.status-*` pills. */
function executionStatusPillClasses(row: ExecutionRow): string {
  const raw = (row.rawState || row.state || row.status || '').toLowerCase().trim();
  const key = raw.replace(/\s+/g, '_');
  const base = 'status';
  if (key === 'succeeded' || key === 'completed') return `${base} status-completed`;
  if (key === 'failed') return `${base} status-failed`;
  if (key === 'canceled' || key === 'cancelled') return `${base} status-cancelled`;
  if (key === 'queued' || key === 'scheduling') return `${base} status-queued`;
  if (
    key === 'running' ||
    key === 'executing' ||
    key === 'planning' ||
    key === 'initializing' ||
    key === 'finalizing'
  ) {
    return `${base} status-running`;
  }
  if (key === 'awaiting_action' || key === 'awaiting_external') return `${base} status-awaiting_action`;
  return `${base} status-neutral`;
}

function sortRows(rows: ExecutionRow[], field: string, direction: 'asc' | 'desc'): ExecutionRow[] {
  const dir = direction === 'asc' ? 1 : -1;
  const copy = rows.slice();
  copy.sort((left, right) => {
    let leftVal: string | number;
    let rightVal: string | number;
    if (TIMESTAMP_SORT_FIELDS.has(field)) {
      const getTs = (row: ExecutionRow) => {
        if (field === 'scheduledFor') return row.scheduledFor || row.createdAt;
        return (row as Record<string, unknown>)[field] as string | undefined;
      };
      leftVal = Date.parse(getTs(left) || '') || 0;
      rightVal = Date.parse(getTs(right) || '') || 0;
      if (leftVal !== rightVal) return dir * (leftVal - rightVal);
    } else if (field === 'status') {
      const ls = (left.rawState || left.state || '').toLowerCase();
      const rs = (right.rawState || right.state || '').toLowerCase();
      const c = ls.localeCompare(rs);
      if (c !== 0) return dir * c;
    } else {
      leftVal = String((left as Record<string, unknown>)[field] ?? '').toLowerCase();
      rightVal = String((right as Record<string, unknown>)[field] ?? '').toLowerCase();
      const c = leftVal.localeCompare(rightVal);
      if (c !== 0) return dir * c;
    }
    return right.taskId.localeCompare(left.taskId);
  });
  return copy;
}

function replaceUrlQuery(params: URLSearchParams) {
  const q = params.toString();
  const path = window.location.pathname;
  window.history.replaceState({}, '', q ? `${path}?${q}` : path);
}

function TasksListPage({ payload }: { payload: BootPayload }) {
  const dashboardCfg = useMemo(() => readListDashboardConfig(payload), [payload.initialData]);
  const listPollMs = useMemo(() => {
    const n = dashboardCfg?.pollIntervalsMs?.list;
    return typeof n === 'number' && n > 0 ? n : POLL_MS_DEFAULT;
  }, [dashboardCfg]);
  const listEnabled = dashboardCfg?.features?.temporalDashboard?.listEnabled !== false;

  const initial = useMemo(() => new URLSearchParams(window.location.search), []);

  const [workflowType, setWorkflowType] = useState(() => initial.get('workflowType') || '');
  const [temporalState, setTemporalState] = useState(() => (initial.get('state') || '').toLowerCase());
  const [entry, setEntry] = useState(() => (initial.get('entry') || '').toLowerCase());
  const [pageSize, setPageSize] = useState(() => parsePageSize(initial.get('limit')));
  const [listCursor, setListCursor] = useState<string | null>(() => initial.get('nextPageToken')?.trim() || null);
  const [cursorStack, setCursorStack] = useState<string[]>([]);
  const [liveUpdates, setLiveUpdates] = useState(true);
  const [sortField, setSortField] = useState<string>(() => normalizeTableSortField(initial.get('sort')));
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>(
    () => (initial.get('sortDir') === 'asc' ? 'asc' : 'desc'),
  );

  const syncUrl = useCallback(() => {
    const p = new URLSearchParams();
    if (workflowType) p.set('workflowType', workflowType);
    if (temporalState) p.set('state', temporalState);
    if (entry) p.set('entry', entry);
    p.set('limit', String(pageSize));
    if (listCursor) p.set('nextPageToken', listCursor);
    if (sortField !== 'scheduledFor' || sortDir !== 'desc') {
      p.set('sort', sortField);
      p.set('sortDir', sortDir);
    }
    replaceUrlQuery(p);
  }, [workflowType, temporalState, entry, pageSize, listCursor, sortField, sortDir]);

  useEffect(() => {
    syncUrl();
  }, [syncUrl]);

  const queryKey = [
    'tasks-list',
    'temporal',
    pageSize,
    workflowType,
    temporalState,
    entry,
    listCursor,
  ] as const;

  const { data, isLoading, isError, error, isFetching } = useQuery({
    queryKey,
    enabled: listEnabled,
    queryFn: async () => {
      const params = new URLSearchParams();
      params.set('source', 'temporal');
      params.set('pageSize', String(pageSize));
      if (listCursor) params.set('nextPageToken', listCursor);
      if (workflowType) params.set('workflowType', workflowType);
      if (temporalState) params.set('state', temporalState);
      if (entry) params.set('entry', entry);
      const response = await fetch(`${payload.apiBase}/executions?${params}`);
      if (!response.ok) {
        throw new Error(`Failed to fetch: ${response.statusText}`);
      }
      return ExecutionListResponseSchema.parse(await response.json());
    },
    refetchInterval: liveUpdates && listEnabled ? listPollMs : false,
  });

  const sortedItems = useMemo(() => {
    const items = data?.items || [];
    return sortRows(items, sortField, sortDir);
  }, [data?.items, sortField, sortDir]);

  const pageIndex = cursorStack.length;
  const pageStart = sortedItems.length > 0 ? pageIndex * pageSize + 1 : 0;
  const pageEnd = pageIndex * pageSize + sortedItems.length;
  const totalCount = data?.count;
  const countMode = data?.countMode;

  const resetToFirstPage = () => {
    setListCursor(null);
    setCursorStack([]);
  };

  const onHeaderClick = (field: string) => {
    if (sortField === field) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortField(field);
      setSortDir(TIMESTAMP_SORT_FIELDS.has(field) ? 'desc' : 'asc');
    }
  };

  const sortIndicator = (field: string) =>
    sortField === field ? (sortDir === 'asc' ? ' ▲' : ' ▼') : '';

  const goNext = () => {
    const token = data?.nextPageToken?.trim();
    if (!token) return;
    setCursorStack((s) => [...s, listCursor ?? '']);
    setListCursor(token);
  };

  const goPrev = () => {
    if (cursorStack.length === 0) return;
    const prev = cursorStack.slice(0, -1);
    const last = cursorStack[cursorStack.length - 1];
    setCursorStack(prev);
    const nextCursor = last === undefined || last === '' ? null : last;
    setListCursor(nextCursor);
  };

  return (
    <div className="w-full max-w-none mx-auto p-6 space-y-4 text-gray-900 dark:text-gray-100">
      <header className="border-b border-gray-200 dark:border-gray-700 pb-4 flex flex-wrap items-start justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold tracking-tight">Tasks List</h2>
        </div>
        <label className="flex items-center gap-2 text-sm cursor-pointer select-none">
          <input
            type="checkbox"
            checked={liveUpdates}
            disabled={!listEnabled}
            onChange={(e) => setLiveUpdates(e.target.checked)}
          />
          Live updates
          {isFetching && liveUpdates && listEnabled ? (
            <span className="text-xs text-gray-500">refreshing…</span>
          ) : null}
        </label>
      </header>

      {!listEnabled ? (
        <div
          className="p-4 rounded-md bg-amber-50 dark:bg-amber-900/20 text-amber-900 dark:text-amber-100 border border-amber-200 dark:border-amber-800 text-sm"
          role="status"
        >
          The Temporal task list is disabled in server configuration (
          <code className="text-xs">temporal_dashboard.list_enabled</code>).
        </div>
      ) : null}

      <div className="bg-white dark:bg-gray-900/50 rounded-lg shadow-sm p-4 border border-gray-100 dark:border-gray-800 space-y-4">
        <form
          className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 text-sm"
          onSubmit={(e) => e.preventDefault()}
        >
          <label className="flex flex-col gap-1">
            <span className="font-medium text-gray-700 dark:text-gray-300">Workflow Type</span>
            <select
              className="border rounded px-2 py-1 bg-white dark:bg-gray-950 dark:border-gray-700"
              value={workflowType}
              disabled={!listEnabled}
              onChange={(e) => {
                setWorkflowType(e.target.value);
                resetToFirstPage();
              }}
            >
              <option value="">(all)</option>
              {WORKFLOW_TYPES.map((w) => (
                <option key={w} value={w}>
                  {w}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="font-medium text-gray-700 dark:text-gray-300">Temporal State</span>
            <select
              className="border rounded px-2 py-1 bg-white dark:bg-gray-950 dark:border-gray-700"
              value={temporalState}
              disabled={!listEnabled}
              onChange={(e) => {
                setTemporalState(e.target.value.toLowerCase());
                resetToFirstPage();
              }}
            >
              <option value="">(all)</option>
              {TEMPORAL_STATES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="font-medium text-gray-700 dark:text-gray-300">Entry</span>
            <select
              className="border rounded px-2 py-1 bg-white dark:bg-gray-950 dark:border-gray-700"
              value={entry}
              disabled={!listEnabled}
              onChange={(e) => {
                setEntry(e.target.value.toLowerCase());
                resetToFirstPage();
              }}
            >
              <option value="">(all)</option>
              {ENTRY_OPTIONS.map((en) => (
                <option key={en} value={en}>
                  {en}
                </option>
              ))}
            </select>
          </label>
        </form>

        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 text-sm text-gray-600 dark:text-gray-400">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1 min-w-0">
            <strong className="text-gray-800 dark:text-gray-200 shrink-0">Page {pageIndex + 1}</strong>
            <span className="opacity-40 shrink-0">|</span>
            <span className="min-w-0">
              Showing {pageStart}-{pageEnd}
              {typeof totalCount === 'number' ? (
                <>
                  {' '}
                  of {totalCount}
                  {countMode && countMode !== 'exact' ? ` (${countMode})` : ''} tasks
                </>
              ) : null}
            </span>
          </div>
          <div className="flex flex-wrap items-center gap-2 shrink-0">
            <label className="inline-flex items-center gap-1.5">
              <span className="text-xs text-gray-500 dark:text-gray-500">Per page</span>
              <select
                className="text-xs leading-tight h-7 min-w-[3.25rem] rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-950 px-1.5 py-0"
                value={pageSize}
                disabled={!listEnabled}
                onChange={(e) => {
                  setPageSize(parsePageSize(e.target.value));
                  resetToFirstPage();
                }}
                aria-label="Rows per page"
              >
                {PAGE_SIZE_OPTIONS.map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              className="px-3 py-1 rounded border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-800 disabled:opacity-40 h-7 text-sm"
              disabled={!listEnabled || cursorStack.length === 0 || sortedItems.length === 0}
              onClick={goPrev}
            >
              ← Prev
            </button>
            <button
              type="button"
              className="px-3 py-1 rounded border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-800 disabled:opacity-40 h-7 text-sm"
              disabled={!listEnabled || !data?.nextPageToken}
              onClick={goNext}
            >
              Next →
            </button>
          </div>
        </div>

        {!listEnabled ? (
          <p className="text-sm text-gray-500 dark:text-gray-400">Enable the list in configuration to load tasks.</p>
        ) : isLoading ? (
          <p className="text-gray-500 italic animate-pulse">Loading tasks...</p>
        ) : isError ? (
          <div className="p-4 rounded-md bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-300 border border-red-200 dark:border-red-800">
            {(error as Error).message}
          </div>
        ) : (
          <div className="overflow-x-auto w-full rounded border border-gray-200 dark:border-gray-700">
            <table className="min-w-full text-left text-sm whitespace-nowrap">
              <thead className="border-b border-gray-200 dark:border-gray-700 bg-gray-50/90 dark:bg-gray-950/50">
                <tr>
                  {TABLE_COLUMNS.map(([field, label]) => {
                    const active = sortField === field;
                    return (
                      <th key={field} className="px-3 py-2.5 align-bottom first:pl-0 last:pr-0">
                        <button
                          type="button"
                          className={[
                            'w-full min-w-0 text-left text-xs font-semibold uppercase tracking-wide transition-colors',
                            'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-gray-500 rounded-sm',
                            active
                              ? 'text-gray-900 dark:text-gray-100'
                              : 'text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-300',
                          ].join(' ')}
                          onClick={() => onHeaderClick(field)}
                        >
                          {label}
                          <span className="font-normal normal-case tracking-normal">{sortIndicator(field)}</span>
                        </button>
                      </th>
                    );
                  })}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
                {sortedItems.length === 0 ? (
                  <tr>
                    <td colSpan={8} className="px-3 py-8 text-center text-gray-500">
                      No tasks found.
                    </td>
                  </tr>
                ) : (
                  sortedItems.map((row) => (
                    <tr key={row.taskId} className="hover:bg-gray-50 dark:hover:bg-gray-800/50">
                      <td className="px-3 py-2 font-mono text-xs">
                        <a
                          className="text-blue-600 dark:text-blue-400 hover:underline"
                          href={`/tasks/${encodeURIComponent(row.taskId)}?source=temporal`}
                        >
                          {row.taskId}
                        </a>
                      </td>
                      <td className="px-3 py-2">{row.targetRuntime || '—'}</td>
                      <td className="px-3 py-2">{row.targetSkill || '—'}</td>
                      <td className="px-3 py-2">
                        <span className={executionStatusPillClasses(row)}>
                          {row.rawState || row.state || row.status || '—'}
                        </span>
                      </td>
                      <td className="px-3 py-2 max-w-md whitespace-normal break-words">{row.title}</td>
                      <td className="px-3 py-2">{formatWhen(row.scheduledFor)}</td>
                      <td className="px-3 py-2">{formatWhen(row.startedAt)}</td>
                      <td className="px-3 py-2">{formatWhen(row.closedAt)}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

mountPage(TasksListPage);
