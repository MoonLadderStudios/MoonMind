import { useCallback, useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { z } from 'zod';

import { BootPayload } from '../boot/parseBootPayload';
import { formatRuntimeLabel, formatTaskSkills } from '../utils/formatters';
import { ExecutionStatusPill } from '../components/ExecutionStatusPill';
import { PageSizeSelector, parsePageSize } from '../components/PageSizeSelector';

const POLL_MS_DEFAULT = 5000;

type ListDashboardConfig = {
  pollIntervalsMs?: { list?: number };
  features?: {
    temporalDashboard?: {
      listEnabled?: boolean;
    };
  };
};

const TEMPORAL_STATUSES = [
  'scheduled',
  'initializing',
  'waiting_on_dependencies',
  'planning',
  'awaiting_slot',
  'executing',
  'proposals',
  'awaiting_external',
  'finalizing',
  'completed',
  'failed',
  'canceled',
] as const;
const RUNTIME_FILTER_OPTIONS = [
  'codex_cli',
  'codex',
  'claude_code',
  'claude',
  'gemini_cli',
  'jules',
  'codex_cloud',
] as const;
const TASK_WORKFLOW_TYPE = 'MoonMind.Run';
const TASK_ENTRY = 'run';

const TIMESTAMP_SORT_FIELDS = new Set(['scheduledFor', 'createdAt', 'closedAt']);
const TABLE_COLUMNS = [
  ['taskId', 'ID'],
  ['targetRuntime', 'Runtime'],
  ['targetSkill', 'Skill'],
  ['repository', 'Repository'],
  ['status', 'Status'],
  ['title', 'Title'],
  ['scheduledFor', 'Scheduled'],
  ['createdAt', 'Created'],
  ['closedAt', 'Finished'],
] as const;
type TableColumn = (typeof TABLE_COLUMNS)[number];
type TableField = TableColumn[0];
type FilterField = TableField;
const VALID_TABLE_SORT_FIELDS = new Set<string>([...TABLE_COLUMNS.map((column) => column[0]), 'integration']);
const ACTIVE_FILTER_FIELDS = new Set<string>(['status', 'repository', 'targetRuntime']);

const ExecutionRowSchema = z
  .object({
    taskId: z.string(),
    source: z.string(),
    workflowType: z.string().optional(),
    repository: z.string().nullable().optional(),
    integration: z.string().nullable().optional(),
    targetRuntime: z.string().nullable().optional(),
    targetSkill: z.string().nullable().optional(),
    taskSkills: z.array(z.string()).nullable().optional(),
    title: z.string(),
    status: z.string(),
    state: z.string(),
    rawState: z.string().optional(),
    temporalStatus: z.string().optional(),
    scheduledFor: z.string().nullable().optional(),
    closedAt: z.string().nullable().optional(),
    createdAt: z.string(),
    entry: z.string().optional(),
    dependsOn: z.array(z.string()).optional(),
    blockedOnDependencies: z.boolean().optional(),
  })
  .passthrough();

const ExecutionListResponseSchema = z.object({
  items: z.array(ExecutionRowSchema),
  nextPageToken: z.string().nullable().optional(),
  count: z.number().nullable().optional(),
  countMode: z.string().optional(),
});

type ExecutionRow = z.infer<typeof ExecutionRowSchema>;

function readListDashboardConfig(payload: BootPayload): ListDashboardConfig | undefined {
  const raw = payload.initialData as { dashboardConfig?: ListDashboardConfig } | undefined;
  return raw?.dashboardConfig;
}

function normalizeTableSortField(raw: string | null): string {
  const candidate = (raw || '').trim();
  return VALID_TABLE_SORT_FIELDS.has(candidate) ? candidate : 'scheduledFor';
}

function formatWhen(iso: string | null | undefined): string {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString();
}

function summarizeRuntime(runtime: string | null | undefined): string {
  const label = formatRuntimeLabel(runtime);
  return label === '—' ? '' : label;
}

function hasUnsupportedWorkflowScopeState(params: URLSearchParams): boolean {
  const scope = (params.get('scope') || '').trim().toLowerCase();
  const workflowType = (params.get('workflowType') || '').trim();
  const entry = (params.get('entry') || '').trim().toLowerCase();
  return Boolean(
    (scope && scope !== 'tasks') ||
      (workflowType && workflowType !== TASK_WORKFLOW_TYPE) ||
      (entry && entry !== TASK_ENTRY),
  );
}

function dependencyListSummary(row: ExecutionRow): string {
  const blocked = Boolean(
    row.blockedOnDependencies ||
      String(row.rawState || row.state || '').toLowerCase() === 'waiting_on_dependencies',
  );
  if (!blocked) return '';
  const count = row.dependsOn?.length || 0;
  if (count <= 0) return 'Blocked on dependencies';
  return `Blocked by ${count} prerequisite${count === 1 ? '' : 's'}`;
}

function displayTemporalCount(count: number | null | undefined, countMode: string | undefined): string {
  if (typeof count !== 'number') {
    return '';
  }
  return countMode && countMode !== 'exact' ? `${count} (${countMode})` : String(count);
}

function sortRows(rows: ExecutionRow[], field: string, direction: 'asc' | 'desc'): ExecutionRow[] {
  const dir = direction === 'asc' ? 1 : -1;
  const copy = rows.slice();
  copy.sort((left, right) => {
    let leftVal: string | number;
    let rightVal: string | number;
    if (TIMESTAMP_SORT_FIELDS.has(field)) {
      const getTimestamp = (row: ExecutionRow) => {
        if (field === 'scheduledFor') return row.scheduledFor || row.createdAt;
        return (row as Record<string, unknown>)[field] as string | undefined;
      };
      leftVal = Date.parse(getTimestamp(left) || '') || 0;
      rightVal = Date.parse(getTimestamp(right) || '') || 0;
      if (leftVal !== rightVal) return dir * (leftVal - rightVal);
    } else if (field === 'status') {
      const leftStatus = (left.rawState || left.state || '').toLowerCase();
      const rightStatus = (right.rawState || right.state || '').toLowerCase();
      const compare = leftStatus.localeCompare(rightStatus);
      if (compare !== 0) return dir * compare;
    } else {
      leftVal = String((left as Record<string, unknown>)[field] ?? '').toLowerCase();
      rightVal = String((right as Record<string, unknown>)[field] ?? '').toLowerCase();
      const compare = leftVal.localeCompare(rightVal);
      if (compare !== 0) return dir * compare;
    }
    return right.taskId.localeCompare(left.taskId);
  });
  return copy;
}

function replaceUrlQuery(params: URLSearchParams) {
  const queryText = params.toString();
  const path = window.location.pathname;
  window.history.replaceState({}, '', queryText ? `${path}?${queryText}` : path);
}

export function TasksListPage({ payload }: { payload: BootPayload }) {
  const dashboardCfg = useMemo(() => readListDashboardConfig(payload), [payload.initialData]);
  const listPollMs = useMemo(() => {
    const candidate = dashboardCfg?.pollIntervalsMs?.list;
    return typeof candidate === 'number' && candidate > 0 ? candidate : POLL_MS_DEFAULT;
  }, [dashboardCfg]);
  const listEnabled = dashboardCfg?.features?.temporalDashboard?.listEnabled !== false;

  const initial = useMemo(() => new URLSearchParams(window.location.search), []);

  const [ignoredWorkflowScopeState] = useState(() => hasUnsupportedWorkflowScopeState(initial));
  const [temporalState, setTemporalState] = useState(() => (initial.get('state') || '').toLowerCase());
  const [repository, setRepository] = useState(() => initial.get('repo') || '');
  const [targetRuntime, setTargetRuntime] = useState(() => initial.get('targetRuntime') || '');
  const [openFilter, setOpenFilter] = useState<FilterField | null>(null);
  const [pageSize, setPageSize] = useState(() => parsePageSize(initial.get('limit')));
  const [listCursor, setListCursor] = useState<string | null>(() =>
    ignoredWorkflowScopeState ? null : initial.get('nextPageToken')?.trim() || null,
  );
  const [cursorStack, setCursorStack] = useState<string[]>([]);
  const [liveUpdates, setLiveUpdates] = useState(true);
  const [sortField, setSortField] = useState<string>(() => normalizeTableSortField(initial.get('sort')));
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>(() => {
    const initialSortDir = initial.get('sortDir');
    if (initialSortDir === 'asc' || initialSortDir === 'desc') return initialSortDir;
    return 'desc';
  });
  const normalizedRepository = repository.trim();
  const normalizedTargetRuntime = targetRuntime.trim();

  const syncUrl = useCallback(() => {
    const params = new URLSearchParams();
    if (temporalState) params.set('state', temporalState);
    if (normalizedRepository) params.set('repo', normalizedRepository);
    if (normalizedTargetRuntime) params.set('targetRuntime', normalizedTargetRuntime);
    params.set('limit', String(pageSize));
    if (listCursor) params.set('nextPageToken', listCursor);
    if (sortField !== 'scheduledFor' || sortDir !== 'desc') {
      params.set('sort', sortField);
      params.set('sortDir', sortDir);
    }
    replaceUrlQuery(params);
  }, [
    temporalState,
    normalizedRepository,
    normalizedTargetRuntime,
    pageSize,
    listCursor,
    sortField,
    sortDir,
  ]);

  useEffect(() => {
    syncUrl();
  }, [syncUrl]);

  const queryKey = [
    'tasks-list',
    'temporal',
    pageSize,
    temporalState,
    normalizedRepository,
    normalizedTargetRuntime,
    listCursor,
  ] as const;

  const { data, isLoading, isError, error } = useQuery({
    queryKey,
    enabled: listEnabled,
    queryFn: async () => {
      const params = new URLSearchParams();
      params.set('source', 'temporal');
      params.set('pageSize', String(pageSize));
      params.set('scope', 'tasks');
      if (listCursor) params.set('nextPageToken', listCursor);
      if (temporalState) params.set('state', temporalState);
      if (normalizedRepository) params.set('repo', normalizedRepository);
      if (normalizedTargetRuntime) params.set('targetRuntime', normalizedTargetRuntime);
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
  const countSummary = displayTemporalCount(data?.count, data?.countMode);
  const hasPaginationContext = cursorStack.length > 0 || Boolean(listCursor);

  const resetToFirstPage = useCallback(() => {
    setListCursor(null);
    setCursorStack([]);
  }, []);

  const onHeaderClick = (field: string) => {
    if (sortField === field) {
      setSortDir((current) => (current === 'asc' ? 'desc' : 'asc'));
      return;
    }
    setSortField(field);
    setSortDir(TIMESTAMP_SORT_FIELDS.has(field) ? 'desc' : 'asc');
  };

  const sortIndicator = (field: string) =>
    sortField === field ? (sortDir === 'asc' ? ' ▲' : ' ▼') : '';

  const sortAccessibilityProps = (field: string, label: string) => {
    const isSorted = sortField === field;
    const ariaSort: 'none' | 'ascending' | 'descending' = !isSorted
      ? 'none'
      : sortDir === 'asc'
        ? 'ascending'
        : 'descending';
    const sortHint = !isSorted
      ? 'Not sorted. Activate to sort ascending.'
      : sortDir === 'asc'
        ? 'Sorted ascending. Activate to sort descending.'
        : 'Sorted descending. Activate to sort ascending.';
    return {
      ariaSort,
      ariaLabel: `${label}. ${sortHint}`,
      sortHint,
    };
  };

  const goNext = () => {
    const token = data?.nextPageToken?.trim();
    if (!token) return;
    setCursorStack((stack) => [...stack, listCursor ?? '']);
    setListCursor(token);
  };

  const goPrev = () => {
    if (cursorStack.length === 0) return;
    const previousStack = cursorStack.slice(0, -1);
    const previousCursor = cursorStack[cursorStack.length - 1];
    setCursorStack(previousStack);
    setListCursor(previousCursor === undefined || previousCursor === '' ? null : previousCursor);
  };

  const pageSummary = [
    `Page ${pageIndex + 1}`,
    pageEnd > 0 ? `${pageStart}-${pageEnd}` : null,
    countSummary || null,
  ]
    .filter(Boolean)
    .join(' · ');
  const filterValueForField = useCallback(
    (field: string): string => {
      if (field === 'status') return temporalState;
      if (field === 'repository') return normalizedRepository;
      if (field === 'targetRuntime') return normalizedTargetRuntime;
      return '';
    },
    [normalizedRepository, normalizedTargetRuntime, temporalState],
  );
  const activeFilters = useMemo(
    () =>
      [
        temporalState ? { field: 'status' as FilterField, label: 'Status', value: temporalState } : null,
        normalizedRepository
          ? { field: 'repository' as FilterField, label: 'Repository', value: normalizedRepository }
          : null,
        normalizedTargetRuntime
          ? {
              field: 'targetRuntime' as FilterField,
              label: 'Runtime',
              value: formatRuntimeLabel(normalizedTargetRuntime),
            }
          : null,
      ].filter(
        (filter): filter is { field: FilterField; label: string; value: string } => Boolean(filter),
      ),
    [temporalState, normalizedRepository, normalizedTargetRuntime],
  );
  const hasActiveFilters = activeFilters.length > 0;
  const clearFilters = useCallback(() => {
    setTemporalState('');
    setRepository('');
    setTargetRuntime('');
    setOpenFilter(null);
    resetToFirstPage();
  }, [resetToFirstPage]);

  const filterAccessibilityLabel = (field: string, label: string): string => {
    const value = filterValueForField(field);
    if (!ACTIVE_FILTER_FIELDS.has(field)) return `Filter ${label}. No filter available.`;
    if (!value) return `Filter ${label}. No filter applied.`;
    return `Filter ${label}. Filter active: ${field === 'targetRuntime' ? formatRuntimeLabel(value) : value}.`;
  };

  const renderFilterControl = (field: FilterField, labelPrefix = '') => {
    if (field === 'status') {
      return (
        <label className="queue-inline-filter task-list-header-filter-control">
          {labelPrefix}Status filter value
          <select
            value={temporalState}
            disabled={!listEnabled}
            onChange={(event) => {
              setTemporalState(event.target.value.toLowerCase());
              resetToFirstPage();
            }}
          >
            <option value="">All Statuses</option>
            {TEMPORAL_STATUSES.map((status) => (
              <option key={status} value={status}>
                {status}
              </option>
            ))}
          </select>
        </label>
      );
    }

    if (field === 'repository') {
      return (
        <label className="queue-inline-filter task-list-header-filter-control">
          {labelPrefix}Repository filter value
          <input
            type="text"
            value={repository}
            disabled={!listEnabled}
            placeholder="owner/repo"
            onChange={(event) => {
              setRepository(event.target.value);
              resetToFirstPage();
            }}
          />
        </label>
      );
    }

    if (field === 'targetRuntime') {
      const runtimeOptions = normalizedTargetRuntime
        ? Array.from(new Set([normalizedTargetRuntime, ...RUNTIME_FILTER_OPTIONS]))
        : [...RUNTIME_FILTER_OPTIONS];
      return (
        <label className="queue-inline-filter task-list-header-filter-control">
          {labelPrefix}Runtime filter value
          <select
            value={targetRuntime}
            disabled={!listEnabled}
            onChange={(event) => {
              setTargetRuntime(event.target.value);
              resetToFirstPage();
            }}
          >
            <option value="">All Runtimes</option>
            {runtimeOptions.map((runtime) => (
              <option key={runtime} value={runtime}>
                {formatRuntimeLabel(runtime)}
              </option>
            ))}
          </select>
        </label>
      );
    }

    return null;
  };

  const renderFilterPopover = (field: FilterField, label: string) => {
    if (openFilter !== field) return null;

    const control = renderFilterControl(field) || (
      <p className="small">No filter is available for {label} yet.</p>
    );

    return (
      <div className="task-list-header-filter-popover" role="dialog" aria-label={`${label} filter`}>
        {control}
      </div>
    );
  };

  return (
    <div className="stack">
      <section className="task-list-control-deck" aria-labelledby="task-list-title">
        <div className="toolbar">
          <div>
            <h2 className="page-title" id="task-list-title">Tasks List</h2>
          </div>
          <div className="toolbar-controls task-list-utility-cluster">
            <label className="queue-inline-toggle toolbar-live-toggle">
              <input
                type="checkbox"
                checked={liveUpdates}
                disabled={!listEnabled}
                onChange={(event) => setLiveUpdates(event.target.checked)}
              />
              Live updates
            </label>
            <span className="small">
              {liveUpdates && listEnabled
                ? `Polling every ${Math.round(listPollMs / 1000)}s`
                : 'Updates paused to keep selections stable.'}
            </span>
          </div>
        </div>

        {!listEnabled ? (
          <div className="notice error">Temporal task list is disabled in server configuration.</div>
        ) : null}
        {ignoredWorkflowScopeState ? (
          <div className="notice warning">
            Workflow scope filters are not available on Tasks List. Showing task runs only.
          </div>
        ) : null}

        <div className="task-list-filter-row" aria-live="polite">
          {hasActiveFilters ? (
            <div className="task-list-filter-chips" aria-label="Active filters">
              {activeFilters.map(({ field, label, value }) => (
                <button
                  type="button"
                  className="task-list-filter-chip"
                  key={`${label}:${value}`}
                  onClick={() => setOpenFilter(field)}
                  aria-label={`${label} filter: ${value}`}
                >
                  <span>{label}</span>
                  <strong>{value}</strong>
                </button>
              ))}
            </div>
          ) : (
            <span className="small">Showing all task executions.</span>
          )}
          <button
            type="button"
            className="secondary task-list-clear-filters"
            disabled={!listEnabled || !hasActiveFilters}
            onClick={clearFilters}
          >
            Clear filters
          </button>
        </div>
        <div className="task-list-mobile-filter-controls" aria-label="Mobile task filters">
          {renderFilterControl('status', 'Mobile ')}
          {renderFilterControl('repository', 'Mobile ')}
          {renderFilterControl('targetRuntime', 'Mobile ')}
        </div>
      </section>

      {isLoading ? (
        <p className="loading">Loading tasks...</p>
      ) : isError ? (
        <div className="notice error">{(error as Error).message}</div>
      ) : sortedItems.length === 0 && !hasPaginationContext ? (
        <p className="small">No tasks found for the current filters.</p>
      ) : (
        <section className="queue-layouts panel--data task-list-data-slab" aria-label="Task results">
          <div className="queue-results-toolbar">
            <span className="small">{pageSummary}</span>
            <div className="queue-pagination">
              <PageSizeSelector
                pageSize={pageSize}
                disabled={!listEnabled}
                onPageSizeChange={(size) => {
                  setPageSize(size);
                  resetToFirstPage();
                }}
              />
              <nav aria-label="Pagination" style={{ display: 'inline-flex', gap: '0.45rem' }}>
                <button
                  type="button"
                  className="secondary queue-pagination-button"
                  disabled={!listEnabled || cursorStack.length === 0}
                  onClick={goPrev}
                  aria-label="Previous page"
                >
                  <span aria-hidden="true">&larr;</span>
                </button>
                <button
                  type="button"
                  className="secondary queue-pagination-button"
                  disabled={!listEnabled || !data?.nextPageToken}
                  onClick={goNext}
                  aria-label="Next page"
                >
                  <span aria-hidden="true">&rarr;</span>
                </button>
              </nav>
            </div>
          </div>
          {sortedItems.length === 0 ? (
            <div className="card small">No tasks found for the current filters.</div>
          ) : (
            <>
              <div className="queue-table-wrapper" data-layout="table">
                <table>
                  <colgroup>
                    <col className="queue-table-column-id" />
                    <col className="queue-table-column-runtime" />
                    <col className="queue-table-column-skill" />
                    <col className="queue-table-column-repository" />
                    <col className="queue-table-column-status" />
                    <col className="queue-table-column-title" />
                    <col className="queue-table-column-date" />
                    <col className="queue-table-column-date" />
                    <col className="queue-table-column-date" />
                  </colgroup>
                  <thead>
                    <tr>
                      {TABLE_COLUMNS.map(([field, label]) => {
                        const { ariaSort, ariaLabel, sortHint } = sortAccessibilityProps(field, label);
                        return (
                          <th key={field} aria-sort={ariaSort} className="task-list-compound-header-cell">
                            <div className="task-list-compound-header">
                              <button
                                type="button"
                                className="table-sort-button"
                                onClick={() => onHeaderClick(field)}
                                aria-label={ariaLabel}
                              >
                                {label}
                                {sortIndicator(field)}
                                <span className="sr-only">{sortHint}</span>
                              </button>
                              <button
                                type="button"
                                className={`task-list-column-filter-button${
                                  filterValueForField(field) ? ' is-active' : ''
                                }`}
                                onClick={() => setOpenFilter((current) => (current === field ? null : field))}
                                aria-label={filterAccessibilityLabel(field, label)}
                                aria-expanded={openFilter === field}
                              >
                                <span aria-hidden="true">Filter</span>
                              </button>
                            </div>
                            {renderFilterPopover(field, label)}
                          </th>
                        );
                      })}
                    </tr>
                  </thead>
                  <tbody>
                    {sortedItems.map((row) => {
                      const depsSummary = dependencyListSummary(row);
                      return (
                        <tr key={row.taskId}>
                          <td className="queue-table-cell-id">
                            <a href={`/tasks/${encodeURIComponent(row.taskId)}?source=temporal`}>
                              <code>{row.taskId}</code>
                            </a>
                          </td>
                          <td className="queue-table-cell-compact">{formatRuntimeLabel(row.targetRuntime)}</td>
                          <td className="queue-table-cell-compact">
                            {formatTaskSkills(row.taskSkills, row.targetSkill)}
                          </td>
                          <td className="queue-table-cell-compact">{row.repository || '—'}</td>
                          <td className="queue-table-cell-status">
                            <ExecutionStatusPill status={row.rawState || row.state || row.status} />
                          </td>
                          <td className="queue-table-cell-title">
                            <div>{row.title}</div>
                            {depsSummary ? (
                              <div className="small">{depsSummary}</div>
                            ) : null}
                          </td>
                          <td className="queue-table-cell-date">{formatWhen(row.scheduledFor)}</td>
                          <td className="queue-table-cell-date">{formatWhen(row.createdAt)}</td>
                          <td className="queue-table-cell-date">{formatWhen(row.closedAt)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <ul className="queue-card-list" data-layout="card" role="list">
                {sortedItems.map((row) => {
                      const depsSummary = dependencyListSummary(row);
                      return (
                  <li key={row.taskId} className="queue-card">
                    <div className="queue-card-header">
                      <div>
                        <a
                          href={`/tasks/${encodeURIComponent(row.taskId)}?source=temporal`}
                          className="queue-card-title"
                        >
                          {row.title}
                        </a>
                        <p className="queue-card-meta">
                          <code>{row.taskId}</code>
                          {` · ${
                            [summarizeRuntime(row.targetRuntime), row.targetSkill, row.workflowType]
                              .filter(Boolean)
                              .join(' · ') || 'Temporal task'
                          }`}
                        </p>
                      </div>
                      <div className="queue-card-status">
                        <ExecutionStatusPill status={row.rawState || row.state || row.status} />
                      </div>
                    </div>
                    <dl className="queue-card-fields">
                      <div>
                        <dt>ID</dt>
                        <dd>
                          <code>{row.taskId}</code>
                        </dd>
                      </div>
                      <div>
                        <dt>Runtime</dt>
                        <dd>{formatRuntimeLabel(row.targetRuntime)}</dd>
                      </div>
                      <div>
                        <dt>Skill</dt>
                        <dd>{formatTaskSkills(row.taskSkills, row.targetSkill)}</dd>
                      </div>
                      <div>
                        <dt>Repository</dt>
                        <dd>{row.repository || '—'}</dd>
                      </div>
                      <div>
                        <dt>Scheduled</dt>
                        <dd>{formatWhen(row.scheduledFor)}</dd>
                      </div>
                      <div>
                        <dt>Created</dt>
                        <dd>{formatWhen(row.createdAt)}</dd>
                      </div>
                      <div>
                        <dt>Finished</dt>
                        <dd>{formatWhen(row.closedAt)}</dd>
                      </div>
                      {depsSummary ? (
                        <div>
                          <dt>Dependencies</dt>
                          <dd>{depsSummary}</dd>
                        </div>
                      ) : null}
                    </dl>
                    <div className="queue-card-actions">
                      <a
                        href={`/tasks/${encodeURIComponent(row.taskId)}?source=temporal`}
                        className="button secondary queue-card-details-action"
                        role="button"
                      >
                        View details
                      </a>
                    </div>
                  </li>
                      );
                    })}
              </ul>
            </>
          )}
        </section>
      )}
    </div>
  );
}
export default TasksListPage;
