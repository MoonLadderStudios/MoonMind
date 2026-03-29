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
    <div className="w-full max-w-none mx-auto p-4 md:p-6 text-gray-900 dark:text-gray-100 flex flex-col min-h-screen sm:min-h-[calc(100vh-4rem)]">
      <div className="bg-white/70 dark:bg-gray-900/40 backdrop-blur-md shadow-lg border border-gray-200/50 dark:border-gray-700/50 rounded-2xl flex flex-col flex-grow overflow-hidden transition-all duration-300">
        
        {/* Header Section */}
        <header className="px-4 py-3 sm:px-6 flex flex-wrap items-center justify-between gap-4 bg-white/40 dark:bg-gray-950/20 border-b border-gray-200/50 dark:border-gray-800/50">
          <div className="flex items-center gap-4">
            <h2 className="text-xl font-semibold tracking-tight">Tasks List</h2>
            <label className="flex items-center gap-2 text-xs cursor-pointer select-none text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200 transition-colors">
              <input
                type="checkbox"
                checked={liveUpdates}
                disabled={!listEnabled}
                onChange={(e) => setLiveUpdates(e.target.checked)}
                className="rounded text-blue-500 bg-white/50 border-gray-300 dark:border-gray-600 dark:bg-gray-800/50 focus:ring-blue-500/50"
              />
              Live updates
              {isFetching && liveUpdates && listEnabled ? (
                <span className="animate-pulse text-blue-500 ml-1">●</span>
              ) : null}
            </label>
          </div>

          <form
            className="flex flex-wrap items-center gap-3 text-xs"
            onSubmit={(e) => e.preventDefault()}
          >
            <div className="flex items-center gap-2 bg-white/50 dark:bg-gray-900/50 px-3 py-1.5 rounded-full border border-gray-200/50 dark:border-gray-700/50 shadow-sm">
              <span className="font-medium text-gray-500 dark:text-gray-400">Type</span>
              <select
                className="bg-transparent border-none py-0 pl-1 pr-6 focus:ring-0 text-gray-800 dark:text-gray-200 cursor-pointer text-xs"
                value={workflowType}
                disabled={!listEnabled}
                onChange={(e) => {
                  setWorkflowType(e.target.value);
                  resetToFirstPage();
                }}
              >
                <option value="">All Types</option>
                {WORKFLOW_TYPES.map((w) => (
                  <option key={w} value={w}>{w}</option>
                ))}
              </select>
            </div>
            <div className="flex items-center gap-2 bg-white/50 dark:bg-gray-900/50 px-3 py-1.5 rounded-full border border-gray-200/50 dark:border-gray-700/50 shadow-sm">
              <span className="font-medium text-gray-500 dark:text-gray-400">State</span>
              <select
                className="bg-transparent border-none py-0 pl-1 pr-6 focus:ring-0 text-gray-800 dark:text-gray-200 cursor-pointer text-xs"
                value={temporalState}
                disabled={!listEnabled}
                onChange={(e) => {
                  setTemporalState(e.target.value.toLowerCase());
                  resetToFirstPage();
                }}
              >
                <option value="">All States</option>
                {TEMPORAL_STATES.map((s) => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
            </div>
            <div className="flex items-center gap-2 bg-white/50 dark:bg-gray-900/50 px-3 py-1.5 rounded-full border border-gray-200/50 dark:border-gray-700/50 shadow-sm">
              <span className="font-medium text-gray-500 dark:text-gray-400">Entry</span>
              <select
                className="bg-transparent border-none py-0 pl-1 pr-6 focus:ring-0 text-gray-800 dark:text-gray-200 cursor-pointer text-xs"
                value={entry}
                disabled={!listEnabled}
                onChange={(e) => {
                  setEntry(e.target.value.toLowerCase());
                  resetToFirstPage();
                }}
              >
                <option value="">All Entries</option>
                {ENTRY_OPTIONS.map((en) => (
                  <option key={en} value={en}>{en}</option>
                ))}
              </select>
            </div>
          </form>
        </header>

        {!listEnabled ? (
          <div className="p-4 m-4 rounded-xl bg-amber-500/10 text-amber-600 dark:text-amber-400 border border-amber-500/20 text-sm flex items-center gap-2 shadow-sm backdrop-blur-sm">
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" /></svg>
            Temporal task list is disabled in server configuration.
          </div>
        ) : null}

        {/* Table Area */}
        <div className="flex-grow overflow-x-auto overflow-y-auto relative min-h-[400px]">
          {isLoading ? (
            <div className="absolute inset-0 flex items-center justify-center bg-white/40 dark:bg-gray-900/40 backdrop-blur-sm z-10">
              <div className="flex flex-col items-center gap-3">
                <div className="w-8 h-8 rounded-full border-2 border-blue-500 border-t-transparent animate-spin"></div>
                <p className="text-gray-500 dark:text-gray-400 text-sm animate-pulse">Loading tasks...</p>
              </div>
            </div>
          ) : isError ? (
            <div className="m-6 p-4 rounded-xl bg-red-500/10 text-red-600 dark:text-red-400 border border-red-500/20 shadow-sm backdrop-blur-sm">
              {(error as Error).message}
            </div>
          ) : (
            <table className="min-w-full text-left text-sm whitespace-nowrap">
              <thead className="sticky top-0 z-10 bg-white/80 dark:bg-gray-900/80 backdrop-blur-md shadow-[0_1px_2px_rgba(0,0,0,0.05)] border-b border-gray-200/50 dark:border-gray-800/50">
                <tr>
                  {TABLE_COLUMNS.map(([field, label]) => {
                    const active = sortField === field;
                    return (
                      <th key={field} className="px-4 py-3 align-middle font-medium text-gray-500 dark:text-gray-400">
                        <button
                          type="button"
                          className={[
                            'flex items-center gap-1.5 w-full text-left text-xs uppercase tracking-wider transition-colors',
                            'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-500 rounded-md',
                            active ? 'text-blue-600 dark:text-blue-400 font-semibold' : 'hover:text-gray-800 dark:hover:text-gray-200',
                          ].join(' ')}
                          onClick={() => onHeaderClick(field)}
                        >
                          {label}
                          <span className="font-normal normal-case tracking-normal text-[0.65rem] opacity-80">{sortIndicator(field)}</span>
                        </button>
                      </th>
                    );
                  })}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 dark:divide-gray-800/60 text-gray-600 dark:text-gray-300">
                {sortedItems.length === 0 && listEnabled ? (
                  <tr>
                    <td colSpan={8} className="px-4 py-12 text-center text-gray-500 dark:text-gray-400">
                      <div className="flex flex-col items-center gap-2">
                        <svg className="w-8 h-8 opacity-20" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M20 13V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7m16 0v5a2 2 0 01-2 2H6a2 2 0 01-2-2v-5m16 0h-2.586a1 1 0 00-.707.293l-2.414 2.414a1 1 0 01-.707.293h-3.172a1 1 0 01-.707-.293l-2.414-2.414A1 1 0 006.586 13H4" /></svg>
                        <span>No tasks found.</span>
                      </div>
                    </td>
                  </tr>
                ) : (
                  sortedItems.map((row) => (
                    <tr key={row.taskId} className="hover:bg-blue-50/30 dark:hover:bg-blue-900/20 transition-colors group">
                      <td className="px-4 py-2.5 font-mono text-xs">
                        <a
                          className="text-blue-600 dark:text-blue-400 hover:text-blue-800 dark:hover:text-blue-300 hover:underline decoration-blue-500/30 underline-offset-4 transition-colors"
                          href={`/tasks/${encodeURIComponent(row.taskId)}?source=temporal`}
                        >
                          {row.taskId}
                        </a>
                      </td>
                      <td className="px-4 py-2.5">{row.targetRuntime || '—'}</td>
                      <td className="px-4 py-2.5">{row.targetSkill || '—'}</td>
                      <td className="px-4 py-2.5">
                        <span className={executionStatusPillClasses(row)}>
                          {row.rawState || row.state || row.status || '—'}
                        </span>
                      </td>
                      <td className="px-4 py-2.5 max-w-sm whitespace-normal break-words text-gray-900 dark:text-gray-100 group-hover:text-blue-950 dark:group-hover:text-blue-50 transition-colors">{row.title}</td>
                      <td className="px-4 py-2.5 text-xs opacity-80">{formatWhen(row.scheduledFor)}</td>
                      <td className="px-4 py-2.5 text-xs opacity-80">{formatWhen(row.startedAt)}</td>
                      <td className="px-4 py-2.5 text-xs opacity-80">{formatWhen(row.closedAt)}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          )}
        </div>

        {/* Footer / Pagination */}
        <div className="px-4 py-3 sm:px-6 flex flex-wrap items-center justify-between gap-4 bg-gray-50/50 dark:bg-gray-900/30 border-t border-gray-200/50 dark:border-gray-800/50 text-xs">
          <div className="flex items-center gap-2 text-gray-500 dark:text-gray-400">
            <span className="font-medium text-gray-700 dark:text-gray-300">Page {pageIndex + 1}</span>
            <span className="opacity-40">•</span>
            <span>
              {pageStart}-{pageEnd}
              {typeof totalCount === 'number' ? (
                <> of {totalCount}{countMode && countMode !== 'exact' ? ` (${countMode})` : ''}</>
              ) : null}
            </span>
          </div>
          
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2">
              <span className="text-gray-500 dark:text-gray-400">Show</span>
              <select
                className="bg-white/60 dark:bg-gray-800/60 border border-gray-200 dark:border-gray-700 rounded-md py-1 px-2 pr-6 text-xs focus:ring-1 focus:ring-blue-500 focus:border-blue-500 shadow-sm transition-shadow cursor-pointer"
                value={pageSize}
                disabled={!listEnabled}
                onChange={(e) => {
                  setPageSize(parsePageSize(e.target.value));
                  resetToFirstPage();
                }}
                aria-label="Rows per page"
              >
                {PAGE_SIZE_OPTIONS.map((n) => (
                  <option key={n} value={n}>{n}</option>
                ))}
              </select>
            </div>
            
            <div className="flex items-center gap-2">
              <button
                type="button"
                className="w-8 h-8 flex items-center justify-center rounded-full bg-white/80 dark:bg-gray-800/80 border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 disabled:opacity-30 disabled:hover:bg-white/80 dark:disabled:hover:bg-gray-800/80 shadow-sm transition-all focus:outline-none focus:ring-2 focus:ring-blue-500/50"
                disabled={!listEnabled || cursorStack.length === 0 || sortedItems.length === 0}
                onClick={goPrev}
                aria-label="Previous page"
              >
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" /></svg>
              </button>
              <button
                type="button"
                className="w-8 h-8 flex items-center justify-center rounded-full bg-white/80 dark:bg-gray-800/80 border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 disabled:opacity-30 disabled:hover:bg-white/80 dark:disabled:hover:bg-gray-800/80 shadow-sm transition-all focus:outline-none focus:ring-2 focus:ring-blue-500/50"
                disabled={!listEnabled || !data?.nextPageToken}
                onClick={goNext}
                aria-label="Next page"
              >
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" /></svg>
              </button>
            </div>
          </div>
        </div>

      </div>
    </div>
  );
}

mountPage(TasksListPage);
