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

const USER_WORKFLOW_TYPES = ['MoonMind.Run', 'MoonMind.ManifestIngest'] as const;
const SYSTEM_WORKFLOW_TYPES = ['MoonMind.ProviderProfileManager'] as const;
const WORKFLOW_TYPES = [...USER_WORKFLOW_TYPES, ...SYSTEM_WORKFLOW_TYPES] as const;
const LIST_SCOPES = [
  ['tasks', 'Tasks'],
  ['user', 'User Workflows'],
  ['system', 'System Workflows'],
  ['all', 'All Workflows'],
] as const;
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
const ENTRY_OPTIONS = ['run', 'manifest'] as const;

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
const VALID_TABLE_SORT_FIELDS = new Set<string>([...TABLE_COLUMNS.map((column) => column[0]), 'integration']);

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

function normalizeListScope(raw: string | null): string {
  const candidate = (raw || '').trim().toLowerCase();
  return LIST_SCOPES.some(([value]) => value === candidate) ? candidate : 'tasks';
}

function scopeLabel(value: string): string {
  return LIST_SCOPES.find(([scopeValue]) => scopeValue === value)?.[1] || value;
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

  const [listScope, setListScope] = useState(() =>
    normalizeListScope(initial.get('scope') || (initial.get('workflowType') || initial.get('entry') ? 'all' : null)),
  );
  const [workflowType, setWorkflowType] = useState(() => initial.get('workflowType') || '');
  const [temporalState, setTemporalState] = useState(() => (initial.get('state') || '').toLowerCase());
  const [entry, setEntry] = useState(() => (initial.get('entry') || '').toLowerCase());
  const [repository, setRepository] = useState(() => initial.get('repo') || '');
  const [pageSize, setPageSize] = useState(() => parsePageSize(initial.get('limit')));
  const [listCursor, setListCursor] = useState<string | null>(() => initial.get('nextPageToken')?.trim() || null);
  const [cursorStack, setCursorStack] = useState<string[]>([]);
  const [liveUpdates, setLiveUpdates] = useState(true);
  const [sortField, setSortField] = useState<string>(() => normalizeTableSortField(initial.get('sort')));
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>(() => {
    const initialSortDir = initial.get('sortDir');
    if (initialSortDir === 'asc' || initialSortDir === 'desc') return initialSortDir;
    return 'desc';
  });
  const normalizedRepository = repository.trim();
  const workflowTypeOptions = useMemo(() => {
    if (listScope === 'user') return USER_WORKFLOW_TYPES;
    if (listScope === 'system') return SYSTEM_WORKFLOW_TYPES;
    return WORKFLOW_TYPES;
  }, [listScope]);

  const syncUrl = useCallback(() => {
    const params = new URLSearchParams();
    if (listScope !== 'tasks') params.set('scope', listScope);
    if (listScope !== 'tasks' && workflowType) params.set('workflowType', workflowType);
    if (temporalState) params.set('state', temporalState);
    if (entry) params.set('entry', entry);
    if (normalizedRepository) params.set('repo', normalizedRepository);
    params.set('limit', String(pageSize));
    if (listCursor) params.set('nextPageToken', listCursor);
    if (sortField !== 'scheduledFor' || sortDir !== 'desc') {
      params.set('sort', sortField);
      params.set('sortDir', sortDir);
    }
    replaceUrlQuery(params);
  }, [
    listScope,
    workflowType,
    temporalState,
    entry,
    normalizedRepository,
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
    listScope,
    workflowType,
    temporalState,
    entry,
    normalizedRepository,
    listCursor,
  ] as const;

  const { data, isLoading, isError, error } = useQuery({
    queryKey,
    enabled: listEnabled,
    queryFn: async () => {
      const params = new URLSearchParams();
      params.set('source', 'temporal');
      params.set('pageSize', String(pageSize));
      params.set('scope', listScope);
      if (listCursor) params.set('nextPageToken', listCursor);
      if (listScope !== 'tasks' && workflowType) params.set('workflowType', workflowType);
      if (temporalState) params.set('state', temporalState);
      if (entry) params.set('entry', entry);
      if (normalizedRepository) params.set('repo', normalizedRepository);
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
  const activeFilters = useMemo(
    () =>
      [
        listScope !== 'tasks' ? ['Scope', scopeLabel(listScope)] : null,
        listScope !== 'tasks' && workflowType ? ['Workflow', workflowType] : null,
        temporalState ? ['Status', temporalState] : null,
        entry ? ['Entry', entry] : null,
        normalizedRepository ? ['Repository', normalizedRepository] : null,
      ].filter((filter): filter is [string, string] => Boolean(filter)),
    [listScope, workflowType, temporalState, entry, normalizedRepository],
  );
  const hasActiveFilters = activeFilters.length > 0;
  const clearFilters = useCallback(() => {
    setListScope('tasks');
    setWorkflowType('');
    setTemporalState('');
    setEntry('');
    setRepository('');
    resetToFirstPage();
  }, [resetToFirstPage]);

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

        <form className="task-list-control-grid" onSubmit={(event) => event.preventDefault()}>
          <label>
            Scope
            <select
              value={listScope}
              disabled={!listEnabled}
              onChange={(event) => {
                const nextScope = normalizeListScope(event.target.value);
                setListScope(nextScope);
                const nextWorkflowTypes =
                  nextScope === 'user'
                    ? USER_WORKFLOW_TYPES
                    : nextScope === 'system'
                      ? SYSTEM_WORKFLOW_TYPES
                      : WORKFLOW_TYPES;
                if (nextScope === 'tasks' || !nextWorkflowTypes.some((type) => type === workflowType)) {
                  setWorkflowType('');
                }
                resetToFirstPage();
              }}
            >
              {LIST_SCOPES.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label>
            Workflow Type
            <select
              value={workflowType}
              disabled={!listEnabled || listScope === 'tasks'}
              onChange={(event) => {
                setWorkflowType(event.target.value);
                resetToFirstPage();
              }}
            >
              <option value="">All Types</option>
              {workflowTypeOptions.map((workflow) => (
                <option key={workflow} value={workflow}>
                  {workflow}
                </option>
              ))}
            </select>
          </label>
          <label>
            Status
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
          <label>
            Entry
            <select
              value={entry}
              disabled={!listEnabled}
              onChange={(event) => {
                setEntry(event.target.value.toLowerCase());
                resetToFirstPage();
              }}
            >
              <option value="">All Entries</option>
              {ENTRY_OPTIONS.map((entryOption) => (
                <option key={entryOption} value={entryOption}>
                  {entryOption}
                </option>
              ))}
            </select>
          </label>
          <label>
            Repository
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
        </form>

        <div className="task-list-filter-row" aria-live="polite">
          {hasActiveFilters ? (
            <div className="task-list-filter-chips" aria-label="Active filters">
              {activeFilters.map(([label, value]) => (
                <span className="task-list-filter-chip" key={`${label}:${value}`}>
                  <span>{label}</span>
                  <strong>{value}</strong>
                </span>
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
                          <th key={field} aria-sort={ariaSort}>
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
