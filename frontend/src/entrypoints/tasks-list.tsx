import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { z } from 'zod';

import { BootPayload } from '../boot/parseBootPayload';
import { formatRuntimeLabel, formatStatusLabel, formatTaskSkills } from '../utils/formatters';
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
type FilterField =
  | 'taskId'
  | 'status'
  | 'repository'
  | 'targetRuntime'
  | 'targetSkill'
  | 'title'
  | 'scheduledFor'
  | 'createdAt'
  | 'closedAt';
const VALID_TABLE_SORT_FIELDS = new Set<string>([...TABLE_COLUMNS.map((column) => column[0]), 'integration']);
const ACTIVE_FILTER_FIELDS = new Set<FilterField>([
  'taskId',
  'status',
  'repository',
  'targetRuntime',
  'targetSkill',
  'title',
  'scheduledFor',
  'createdAt',
  'closedAt',
]);
function isFilterField(field: string): field is FilterField {
  return ACTIVE_FILTER_FIELDS.has(field as FilterField);
}
type ValueFilter = { mode: 'include' | 'exclude'; values: string[]; blank?: 'include' | 'exclude' | '' };
type RepositoryFilter = ValueFilter & { exactText?: string };
type TextFilter = { contains?: string };
type DateFilter = { from?: string; to?: string; blank?: 'include' | 'exclude' | '' };
type ColumnFilters = {
  taskId: TextFilter;
  status: ValueFilter;
  repository: RepositoryFilter;
  targetRuntime: ValueFilter;
  targetSkill: ValueFilter;
  title: TextFilter;
  scheduledFor: DateFilter;
  createdAt: DateFilter;
  closedAt: DateFilter;
};

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

const ExecutionFacetResponseSchema = z.object({
  facet: z.enum(['status', 'targetRuntime', 'targetSkill', 'repository', 'integration']),
  items: z.array(
    z.object({
      value: z.string(),
      label: z.string(),
      count: z.number(),
    }),
  ),
  blankCount: z.number().nullable().optional(),
  countMode: z.string().optional(),
  truncated: z.boolean().optional(),
  nextPageToken: z.string().nullable().optional(),
  source: z.string().optional(),
});

type ExecutionFacetResponse = z.infer<typeof ExecutionFacetResponseSchema>;

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

function emptyValueFilter(): ValueFilter {
  return { mode: 'include', values: [], blank: '' };
}

function emptyFilters(): ColumnFilters {
  return {
    taskId: {},
    status: emptyValueFilter(),
    repository: { ...emptyValueFilter(), exactText: '' },
    targetRuntime: emptyValueFilter(),
    targetSkill: emptyValueFilter(),
    title: {},
    scheduledFor: {},
    createdAt: {},
    closedAt: {},
  };
}

function uniqueValues(values: Array<string | null | undefined>): string[] {
  return Array.from(new Set(values.map((value) => (value || '').trim()).filter(Boolean)));
}

function splitParamValues(values: string[]): string[] {
  return uniqueValues(values.flatMap((value) => value.split(',')));
}

function splitParam(params: URLSearchParams, key: string): string[] {
  return splitParamValues(params.getAll(key));
}

function validateCanonicalFilterPair(
  params: URLSearchParams,
  includeParam: string,
  excludeParam: string,
): string | null {
  const includeValues = splitParam(params, includeParam);
  const excludeValues = splitParam(params, excludeParam);
  if (includeValues.length > 0 && excludeValues.length > 0) {
    return `Cannot combine ${includeParam} and ${excludeParam}.`;
  }
  return null;
}

function validateInitialFilterParams(params: URLSearchParams): string[] {
  return [
    validateCanonicalFilterPair(params, 'stateIn', 'stateNotIn'),
    validateCanonicalFilterPair(params, 'repoIn', 'repoNotIn'),
    validateCanonicalFilterPair(params, 'targetRuntimeIn', 'targetRuntimeNotIn'),
    validateCanonicalFilterPair(params, 'targetSkillIn', 'targetSkillNotIn'),
  ].filter((message): message is string => Boolean(message));
}

function parseInitialFilters(params: URLSearchParams): ColumnFilters {
  const filters = emptyFilters();
  const stateIn = splitParam(params, 'stateIn');
  const stateNotIn = splitParam(params, 'stateNotIn');
  const legacyState = (params.get('state') || '').trim().toLowerCase();
  if (stateNotIn.length > 0) {
    filters.status = { mode: 'exclude', values: stateNotIn, blank: '' };
  } else if (stateIn.length > 0 || legacyState) {
    filters.status = { mode: 'include', values: stateIn.length > 0 ? stateIn : [legacyState], blank: '' };
  }

  const repoIn = splitParam(params, 'repoIn');
  const repoNotIn = splitParam(params, 'repoNotIn');
  const repoExact = (params.get('repoExact') || params.get('repo') || '').trim();
  if (repoNotIn.length > 0) {
    filters.repository = { mode: 'exclude', values: repoNotIn, exactText: repoExact, blank: '' };
  } else {
    filters.repository = { mode: 'include', values: repoIn, exactText: repoExact, blank: '' };
  }
  filters.taskId = { contains: params.get('taskIdContains') || params.get('taskId') || '' };
  filters.title = { contains: params.get('titleContains') || '' };

  const runtimeIn = splitParam(params, 'targetRuntimeIn');
  const runtimeNotIn = splitParam(params, 'targetRuntimeNotIn');
  const legacyRuntime = (params.get('targetRuntime') || '').trim();
  if (runtimeNotIn.length > 0) {
    filters.targetRuntime = { mode: 'exclude', values: runtimeNotIn, blank: '' };
  } else if (runtimeIn.length > 0 || legacyRuntime) {
    filters.targetRuntime = {
      mode: 'include',
      values: runtimeIn.length > 0 ? runtimeIn : [legacyRuntime],
      blank: '',
    };
  }

  const skillIn = splitParam(params, 'targetSkillIn');
  const skillNotIn = splitParam(params, 'targetSkillNotIn');
  if (skillNotIn.length > 0) {
    filters.targetSkill = { mode: 'exclude', values: skillNotIn, blank: '' };
  } else if (skillIn.length > 0) {
    filters.targetSkill = { mode: 'include', values: skillIn, blank: '' };
  }

  filters.scheduledFor = {
    from: params.get('scheduledFrom') || '',
    to: params.get('scheduledTo') || '',
    blank: (params.get('scheduledBlank') as DateFilter['blank']) || '',
  };
  filters.createdAt = {
    from: params.get('createdFrom') || '',
    to: params.get('createdTo') || '',
  };
  filters.closedAt = {
    from: params.get('finishedFrom') || '',
    to: params.get('finishedTo') || '',
    blank: (params.get('finishedBlank') as DateFilter['blank']) || '',
  };
  return filters;
}

function appendValueParams(
  params: URLSearchParams,
  filter: ValueFilter,
  includeParam: string,
  excludeParam: string,
  blankParam?: string,
) {
  if (filter.values.length > 0) {
    params.set(filter.mode === 'exclude' ? excludeParam : includeParam, filter.values.join(','));
  }
  if (blankParam && filter.blank) {
    params.set(blankParam, filter.blank);
  }
}

function appendDateParams(
  params: URLSearchParams,
  filter: DateFilter,
  fromParam: string,
  toParam: string,
  blankParam?: string,
) {
  if (filter.from) params.set(fromParam, filter.from);
  if (filter.to) params.set(toParam, filter.to);
  if (blankParam && filter.blank) params.set(blankParam, filter.blank);
}

function appendFilterParams(params: URLSearchParams, filters: ColumnFilters) {
  if (filters.taskId.contains?.trim()) params.set('taskIdContains', filters.taskId.contains.trim());
  appendValueParams(params, filters.status, 'stateIn', 'stateNotIn');
  if (filters.repository.exactText?.trim()) {
    params.set('repoExact', filters.repository.exactText.trim());
  }
  appendValueParams(params, filters.repository, 'repoIn', 'repoNotIn', 'repoBlank');
  appendValueParams(params, filters.targetRuntime, 'targetRuntimeIn', 'targetRuntimeNotIn', 'targetRuntimeBlank');
  appendValueParams(params, filters.targetSkill, 'targetSkillIn', 'targetSkillNotIn', 'targetSkillBlank');
  if (filters.title.contains?.trim()) params.set('titleContains', filters.title.contains.trim());
  appendDateParams(params, filters.scheduledFor, 'scheduledFrom', 'scheduledTo', 'scheduledBlank');
  appendDateParams(params, filters.createdAt, 'createdFrom', 'createdTo');
  appendDateParams(params, filters.closedAt, 'finishedFrom', 'finishedTo', 'finishedBlank');
}

function facetForFilterField(field: FilterField | null): ExecutionFacetResponse['facet'] | null {
  if (field === 'status') return 'status';
  if (field === 'targetRuntime') return 'targetRuntime';
  if (field === 'targetSkill') return 'targetSkill';
  if (field === 'repository') return 'repository';
  return null;
}

function filterSummary(field: FilterField, filters: ColumnFilters): string {
  const summarizeValues = (filter: ValueFilter, formatter = (value: string) => value) => {
    if (filter.blank === 'include' && filter.values.length === 0) return 'blank';
    if (filter.blank === 'exclude' && filter.values.length === 0) return 'not blank';
    if (filter.values.length === 0) return '';
    const first = formatter(filter.values[0]!);
    const suffix = filter.values.length > 1 ? ` +${filter.values.length - 1}` : '';
    return `${filter.mode === 'exclude' ? 'not ' : ''}${first}${suffix}`;
  };
  if (field === 'taskId') return filters.taskId.contains?.trim() || '';
  if (field === 'status') return summarizeValues(filters.status, formatStatusLabel);
  if (field === 'targetRuntime') return summarizeValues(filters.targetRuntime, formatRuntimeLabel);
  if (field === 'targetSkill') return summarizeValues(filters.targetSkill);
  if (field === 'repository') {
    if (filters.repository.exactText?.trim()) return filters.repository.exactText.trim();
    return summarizeValues(filters.repository);
  }
  if (field === 'title') return filters.title.contains?.trim() || '';
  const dateFilter = field === 'scheduledFor' ? filters.scheduledFor : field === 'createdAt' ? filters.createdAt : filters.closedAt;
  if (dateFilter.blank === 'include' && !dateFilter.from && !dateFilter.to) return 'blank';
  if (dateFilter.blank === 'exclude' && !dateFilter.from && !dateFilter.to) return 'not blank';
  const parts = [];
  if (dateFilter.from) parts.push(`from ${dateFilter.from}`);
  if (dateFilter.to) parts.push(`to ${dateFilter.to}`);
  if (dateFilter.blank === 'include') parts.push('blank');
  if (dateFilter.blank === 'exclude') parts.push('not blank');
  return parts.join(', ');
}

export function TasksListPage({ payload }: { payload: BootPayload }) {
  const dashboardCfg = useMemo(() => readListDashboardConfig(payload), [payload.initialData]);
  const listPollMs = useMemo(() => {
    const candidate = dashboardCfg?.pollIntervalsMs?.list;
    return typeof candidate === 'number' && candidate > 0 ? candidate : POLL_MS_DEFAULT;
  }, [dashboardCfg]);
  const listEnabled = dashboardCfg?.features?.temporalDashboard?.listEnabled !== false;

  const initial = useMemo(() => new URLSearchParams(window.location.search), []);

  const initialFilterValidationErrors = useMemo(() => validateInitialFilterParams(initial), [initial]);
  const [ignoredWorkflowScopeState] = useState(() => hasUnsupportedWorkflowScopeState(initial));
  const [filters, setFilters] = useState(() => parseInitialFilters(initial));
  const [draftFilters, setDraftFilters] = useState(() => parseInitialFilters(initial));
  const [hasEditedFilters, setHasEditedFilters] = useState(false);
  const [openFilter, setOpenFilter] = useState<FilterField | null>(null);
  const [pendingFocusField, setPendingFocusField] = useState<FilterField | null>(null);
  const popoverRef = useRef<HTMLDivElement | null>(null);
  const filterTriggerRef = useRef<HTMLButtonElement | null>(null);
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
  const filterValidationErrors = useMemo(() => {
    if (!hasEditedFilters) return initialFilterValidationErrors;
    const params = new URLSearchParams();
    appendFilterParams(params, filters);
    return validateInitialFilterParams(params);
  }, [filters, hasEditedFilters, initialFilterValidationErrors]);

  const syncUrl = useCallback(() => {
    if (filterValidationErrors.length > 0) return;
    const params = new URLSearchParams();
    appendFilterParams(params, filters);
    params.set('limit', String(pageSize));
    if (listCursor) params.set('nextPageToken', listCursor);
    if (sortField !== 'scheduledFor' || sortDir !== 'desc') {
      params.set('sort', sortField);
      params.set('sortDir', sortDir);
    }
    replaceUrlQuery(params);
  }, [
    filters,
    filterValidationErrors.length,
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
    filters,
    listCursor,
  ] as const;

  const { data, isLoading, isError, error } = useQuery({
    queryKey,
    enabled: listEnabled && filterValidationErrors.length === 0,
    queryFn: async () => {
      const params = new URLSearchParams();
      params.set('source', 'temporal');
      params.set('pageSize', String(pageSize));
      params.set('scope', 'tasks');
      if (listCursor) params.set('nextPageToken', listCursor);
      appendFilterParams(params, filters);
      const response = await fetch(`${payload.apiBase}/executions?${params}`);
      if (!response.ok) {
        throw new Error(`Failed to fetch: ${response.statusText}`);
      }
      return ExecutionListResponseSchema.parse(await response.json());
    },
    refetchInterval: liveUpdates && listEnabled && !openFilter ? listPollMs : false,
  });

  const openFacet = facetForFilterField(openFilter);
  const {
    data: facetData,
    isError: isFacetError,
    isFetching: isFacetFetching,
  } = useQuery({
    queryKey: ['tasks-list-facet', openFacet, filters] as const,
    enabled: listEnabled && filterValidationErrors.length === 0 && Boolean(openFacet),
    queryFn: async () => {
      const params = new URLSearchParams();
      params.set('source', 'temporal');
      params.set('facet', openFacet as string);
      params.set('pageSize', '50');
      params.set('scope', 'tasks');
      appendFilterParams(params, filters);
      const response = await fetch(`${payload.apiBase}/executions/facets?${params}`);
      if (!response.ok) {
        throw new Error(`Failed to fetch facets: ${response.statusText}`);
      }
      return ExecutionFacetResponseSchema.parse(await response.json());
    },
    staleTime: listPollMs,
    retry: false,
  });

  useEffect(() => {
    if (!openFilter) return;
    setDraftFilters(filters);
  }, [openFilter, filters]);

  const closeFilter = useCallback((nextDraftFilters = filters, fieldToFocus = openFilter) => {
    const fallback = fieldToFocus
      ? document.querySelector<HTMLButtonElement>(
          `.task-list-column-filter-button[data-filter-field="${fieldToFocus}"]`,
        )
      : null;
    if (fieldToFocus) {
      (filterTriggerRef.current?.isConnected ? filterTriggerRef.current : fallback)?.focus();
    }
    setOpenFilter(null);
    setDraftFilters(nextDraftFilters);
    setPendingFocusField(fieldToFocus);
  }, [filters, openFilter]);

  useEffect(() => {
    if (openFilter || !pendingFocusField) return;
    const fallback = document.querySelector<HTMLButtonElement>(
      `.task-list-column-filter-button[data-filter-field="${pendingFocusField}"]`,
    );
    (filterTriggerRef.current?.isConnected ? filterTriggerRef.current : fallback)?.focus();
    setPendingFocusField(null);
  }, [openFilter, pendingFocusField]);

  useEffect(() => {
    if (!openFilter) return;
    window.requestAnimationFrame(() => {
      const firstControl = popoverRef.current?.querySelector<HTMLElement>(
        'input:not([disabled]), select:not([disabled]), button:not([disabled])',
      );
      firstControl?.focus();
    });
  }, [openFilter]);

  useEffect(() => {
    if (!openFilter) return;
    const onPointerDown = (event: MouseEvent) => {
      const target = event.target as Node | null;
      const targetElement = event.target as Element | null;
      if (target && popoverRef.current?.contains(target)) return;
      if (targetElement?.closest('.task-list-column-filter-button, .task-list-filter-chip-open')) return;
      closeFilter(filters, openFilter);
    };
    document.addEventListener('mousedown', onPointerDown);
    return () => document.removeEventListener('mousedown', onPointerDown);
  }, [closeFilter, openFilter, filters]);

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
      if (!isFilterField(field)) return '';
      return filterSummary(field, filters);
    },
    [filters],
  );
  const activeFilters = useMemo(
    () =>
      TABLE_COLUMNS.map(([field, label]) => {
        if (!isFilterField(field)) return null;
        const value = filterSummary(field, filters);
        return value ? { field, label, value } : null;
      }).filter(
        (filter): filter is { field: FilterField; label: TableColumn[1]; value: string } => Boolean(filter),
      ),
    [filters],
  );
  const hasActiveFilters = activeFilters.length > 0;
  const clearFilters = useCallback(() => {
    setHasEditedFilters(true);
    setFilters(emptyFilters());
    setDraftFilters(emptyFilters());
    setOpenFilter(null);
    resetToFirstPage();
  }, [resetToFirstPage]);
  const toggleFilter = useCallback((field: FilterField) => {
    setOpenFilter((current) => (current === field ? null : field));
  }, []);

  const applyFilters = useCallback(
    (nextFilters: ColumnFilters, focusField: FilterField | null = openFilter) => {
      setHasEditedFilters(true);
      setFilters(nextFilters);
      setDraftFilters(nextFilters);
      closeFilter(nextFilters, focusField);
      resetToFirstPage();
    },
    [closeFilter, openFilter, resetToFirstPage],
  );

  const updateDraftText = (field: 'taskId' | 'title', value: string) => {
    setDraftFilters((current) => ({
      ...current,
      [field]: { contains: value },
    }));
  };

  const updateDraftValue = (field: 'status' | 'targetRuntime' | 'targetSkill', value: string) => {
    setDraftFilters((current) => ({
      ...current,
      [field]: value ? { ...current[field], values: [value], blank: '' } : emptyValueFilter(),
    }));
  };

  const updateDraftValueMode = (field: 'targetRuntime' | 'targetSkill', mode: ValueFilter['mode']) => {
    setDraftFilters((current) => ({
      ...current,
      [field]: { ...current[field], mode },
    }));
  };

  const updateDraftRepository = (value: string) => {
    setDraftFilters((current) => ({
      ...current,
      repository: { ...current.repository, mode: 'include', values: [], exactText: value },
    }));
  };

  const updateDraftDate = (field: 'scheduledFor' | 'createdAt' | 'closedAt', patch: DateFilter) => {
    setDraftFilters((current) => ({ ...current, [field]: { ...current[field], ...patch } }));
  };

  const applyMobileValue = (field: 'status' | 'targetRuntime', value: string) => {
    applyFilters({
      ...filters,
      [field]:
        value && field === 'targetRuntime'
          ? { ...filters.targetRuntime, values: [value], blank: '' }
          : value
            ? { mode: 'include', values: [value], blank: '' }
            : emptyValueFilter(),
    });
  };

  const applyMobileRepository = (value: string) => {
    applyFilters({
      ...filters,
      repository: { ...filters.repository, mode: 'include', values: [], exactText: value },
    });
  };

  const applyMobileDate = (field: 'scheduledFor' | 'createdAt' | 'closedAt', patch: DateFilter) => {
    applyFilters({
      ...filters,
      [field]: { ...filters[field], ...patch },
    });
  };

  const filterAccessibilityLabel = (field: string, label: string): string => {
    const value = filterValueForField(field);
    if (!isFilterField(field)) return `Filter ${label}. No filter available.`;
    if (!value) return `Filter ${label}. No filter applied.`;
    return `Filter ${label}. Filter active: ${value}.`;
  };

  const valueOptionsForField = (field: FilterField): string[] => {
    const facetValues =
      facetData && facetForFilterField(field) === facetData.facet
        ? facetData.items.map((item) => item.value)
        : [];
    if (field === 'status') return uniqueValues([...facetValues, ...TEMPORAL_STATUSES]);
    if (field === 'targetRuntime') {
      return uniqueValues([
        ...filters.targetRuntime.values,
        ...draftFilters.targetRuntime.values,
        ...facetValues,
        ...RUNTIME_FILTER_OPTIONS,
      ]);
    }
    if (field === 'targetSkill') {
      return uniqueValues([
        ...filters.targetSkill.values,
        ...draftFilters.targetSkill.values,
        ...facetValues,
        ...(data?.items || []).flatMap((row) => [row.targetSkill, ...(row.taskSkills || [])]),
      ]);
    }
    if (field === 'repository') {
      return uniqueValues([
        ...filters.repository.values,
        ...draftFilters.repository.values,
        ...facetValues,
        ...(data?.items || []).map((row) => row.repository),
      ]);
    }
    return [];
  };

  const renderFacetNotice = (field: FilterField) => {
    if (facetForFilterField(field) !== openFacet) return null;
    if (isFacetError) {
      return (
        <p className="small task-list-facet-notice" role="status">
          Facet values unavailable. Showing current page values only.
        </p>
      );
    }
    if (isFacetFetching) {
      return <p className="small task-list-facet-notice">Loading facet values...</p>;
    }
    if (facetData?.truncated) {
      return <p className="small task-list-facet-notice">Facet values truncated by the server.</p>;
    }
    return null;
  };

  const renderFilterControl = (field: FilterField, labelPrefix = '') => {
    const isMobile = Boolean(labelPrefix);
    if (field === 'taskId' || field === 'title') {
      const label = field === 'taskId' ? 'ID' : 'Title';
      const draft = isMobile ? filters[field] : draftFilters[field];
      return (
        <div className="queue-inline-filter task-list-header-filter-control">
          <label>
            {labelPrefix}{label} filter value
            <input
              type="text"
              value={draft.contains || ''}
              disabled={!listEnabled}
              placeholder={field === 'taskId' ? 'task id' : 'title text'}
              onChange={(event) => {
              if (isMobile) applyFilters({ ...filters, [field]: { contains: event.target.value } }, null);
                else updateDraftText(field, event.target.value);
              }}
            />
          </label>
        </div>
      );
    }

    if (field === 'status') {
      const selected = isMobile ? filters.status.values[0] || '' : draftFilters.status.values[0] || '';
      return (
        <div className="queue-inline-filter task-list-header-filter-control">
          <label>
            {labelPrefix}Status filter value
            <select
              value={selected}
              disabled={!listEnabled}
              onChange={(event) => {
                const value = event.target.value.toLowerCase();
                if (isMobile) applyMobileValue('status', value);
                else updateDraftValue('status', value);
              }}
            >
              <option value="">All Statuses</option>
              {TEMPORAL_STATUSES.map((status) => (
                <option key={status} value={status}>
                  {formatStatusLabel(status)}
                </option>
              ))}
            </select>
          </label>
          {!isMobile ? (
            <label className="task-list-filter-checkbox">
              <input
                type="checkbox"
                checked={draftFilters.status.mode === 'exclude' && draftFilters.status.values.includes('canceled')}
                onChange={(event) => {
                  setDraftFilters((current) => ({
                    ...current,
                    status: event.target.checked
                      ? { mode: 'exclude', values: ['canceled'], blank: '' }
                      : emptyValueFilter(),
                  }));
                }}
              />
              Exclude canceled
            </label>
          ) : null}
          {renderFacetNotice('status')}
        </div>
      );
    }

    if (field === 'repository') {
      return (
        <div className="queue-inline-filter task-list-header-filter-control">
          <label>
            {labelPrefix}Repository filter value
            <input
              type="text"
              value={isMobile ? filters.repository.exactText || '' : draftFilters.repository.exactText || ''}
              disabled={!listEnabled}
              placeholder="owner/repo"
              onChange={(event) => {
                if (isMobile) applyMobileRepository(event.target.value);
                else updateDraftRepository(event.target.value);
              }}
            />
          </label>
          {!isMobile && valueOptionsForField('repository').length > 0 ? (
            <label>
              Repository values
              <select
                aria-label="Repository value selection"
                value={draftFilters.repository.values[0] || ''}
                disabled={!listEnabled}
                onChange={(event) => {
                  setDraftFilters((current) => ({
                    ...current,
                    repository: {
                      ...current.repository,
                      mode: 'include',
                      values: event.target.value ? [event.target.value] : [],
                      exactText: event.target.value ? '' : current.repository.exactText || '',
                    },
                  }));
                }}
              >
                <option value="">Repository values</option>
                {valueOptionsForField('repository').map((repo) => (
                  <option key={repo} value={repo}>
                    {repo}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          {renderFacetNotice('repository')}
        </div>
      );
    }

    if (field === 'targetRuntime') {
      const runtimeOptions = valueOptionsForField('targetRuntime');
      const draft = isMobile ? filters.targetRuntime : draftFilters.targetRuntime;
      return (
        <div className="queue-inline-filter task-list-header-filter-control">
          <label>
            {labelPrefix}Runtime filter mode
            <select
              value={draft.mode}
              disabled={!listEnabled}
              onChange={(event) => {
                const mode = event.target.value as ValueFilter['mode'];
                if (isMobile) applyFilters({ ...filters, targetRuntime: { ...filters.targetRuntime, mode } }, null);
                else updateDraftValueMode('targetRuntime', mode);
              }}
            >
              <option value="include">Include selected</option>
              <option value="exclude">Exclude selected</option>
            </select>
          </label>
          <label>
            {labelPrefix}Runtime filter value
            <select
              value={draft.values[0] || ''}
              disabled={!listEnabled}
              onChange={(event) => {
                if (isMobile) applyMobileValue('targetRuntime', event.target.value);
                else updateDraftValue('targetRuntime', event.target.value);
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
          {renderFacetNotice('targetRuntime')}
        </div>
      );
    }

    if (field === 'targetSkill') {
      const skillOptions = valueOptionsForField('targetSkill');
      const draft = isMobile ? filters.targetSkill : draftFilters.targetSkill;
      return (
        <div className="queue-inline-filter task-list-header-filter-control">
          <label>
            {labelPrefix}Skill filter mode
            <select
              value={draft.mode}
              disabled={!listEnabled}
              onChange={(event) => {
                const mode = event.target.value as ValueFilter['mode'];
                if (isMobile) applyFilters({ ...filters, targetSkill: { ...filters.targetSkill, mode } }, null);
                else updateDraftValueMode('targetSkill', mode);
              }}
            >
              <option value="include">Include selected</option>
              <option value="exclude">Exclude selected</option>
            </select>
          </label>
          <label>
            {labelPrefix}Skill filter value
            <select
              value={draft.values[0] || ''}
              disabled={!listEnabled}
              onChange={(event) => {
                if (isMobile) {
                  applyFilters({
                    ...filters,
                    targetSkill: event.target.value
                      ? { ...filters.targetSkill, values: [event.target.value], blank: '' }
                      : emptyValueFilter(),
                  }, null);
                } else {
                  updateDraftValue('targetSkill', event.target.value);
                }
              }}
            >
              <option value="">All Skills</option>
              {skillOptions.map((skill) => (
                <option key={skill} value={skill}>
                  {skill}
                </option>
              ))}
            </select>
          </label>
          {renderFacetNotice('targetSkill')}
        </div>
      );
    }

    if (field === 'scheduledFor' || field === 'createdAt' || field === 'closedAt') {
      const label = field === 'scheduledFor' ? 'Scheduled' : field === 'createdAt' ? 'Created' : 'Finished';
      const draft = isMobile ? filters[field] : draftFilters[field];
      return (
        <div className="queue-inline-filter task-list-header-filter-control">
          <label>
            {labelPrefix}{label} from
            <input
              type="date"
              value={draft.from || ''}
              disabled={!listEnabled}
              onChange={(event) => {
                if (isMobile) applyMobileDate(field, { from: event.target.value });
                else updateDraftDate(field, { from: event.target.value });
              }}
            />
          </label>
          <label>
            {labelPrefix}{label} to
            <input
              type="date"
              value={draft.to || ''}
              disabled={!listEnabled}
              onChange={(event) => {
                if (isMobile) applyMobileDate(field, { to: event.target.value });
                else updateDraftDate(field, { to: event.target.value });
              }}
            />
          </label>
          {field !== 'createdAt' ? (
            <label>
              {labelPrefix}{label} blank values
              <select
                value={draft.blank || ''}
                disabled={!listEnabled}
                onChange={(event) => {
                  const blank = event.target.value as NonNullable<DateFilter['blank']>;
                  if (isMobile) applyMobileDate(field, { blank });
                  else updateDraftDate(field, { blank });
                }}
              >
                <option value="">Ignore blanks</option>
                <option value="include">Include blanks</option>
                <option value="exclude">Exclude blanks</option>
              </select>
            </label>
          ) : null}
        </div>
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
      <div
        className="task-list-header-filter-popover"
        role="dialog"
        aria-label={`${label} filter`}
        ref={popoverRef}
        onKeyDown={(event) => {
          if (event.key === 'Escape') {
            closeFilter(filters, field);
          }
          if (event.key === 'Enter' && event.target instanceof HTMLElement) {
            const tagName = event.target.tagName.toLowerCase();
            if (tagName !== 'textarea' && tagName !== 'button' && !event.defaultPrevented) {
              event.preventDefault();
              applyFilters(draftFilters, field);
            }
          }
        }}
      >
        {control}
        {isFilterField(field) ? (
          <div className="task-list-filter-actions">
            <button
              type="button"
              className="secondary"
              onClick={() => {
                const next = { ...draftFilters };
                if (field === 'taskId' || field === 'title') next[field] = {};
                else if (field === 'repository') next.repository = { ...emptyValueFilter(), exactText: '' };
                else if (field === 'scheduledFor' || field === 'createdAt' || field === 'closedAt') next[field] = {};
                else next[field] = emptyValueFilter();
                applyFilters(next, field);
              }}
              disabled={!listEnabled}
            >
              Clear
            </button>
            <button
              type="button"
              className="secondary"
              onClick={() => {
                closeFilter(filters, field);
              }}
              aria-label={`Cancel ${label} filter`}
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={() => applyFilters(draftFilters, field)}
              disabled={!listEnabled}
              aria-label={`Apply ${label} filter`}
            >
              Apply
            </button>
          </div>
        ) : null}
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
        {filterValidationErrors.length > 0 ? (
          <div className="notice error" role="alert">
            {filterValidationErrors.map((message) => (
              <div key={message}>{message}</div>
            ))}
          </div>
        ) : null}

        <div className="task-list-filter-row" aria-live="polite">
          {hasActiveFilters ? (
            <div className="task-list-filter-chips" aria-label="Active filters">
              {activeFilters.map(({ field, label, value }) => (
                <span className="task-list-filter-chip" key={`${label}:${value}`}>
                  <button
                    type="button"
                    className="task-list-filter-chip-open"
                    data-filter-field={field}
                    onMouseDown={(event) => event.stopPropagation()}
                    onClick={(event) => {
                      event.stopPropagation();
                      filterTriggerRef.current = event.currentTarget;
                      setOpenFilter(field);
                    }}
                    aria-label={`${label} filter: ${value}`}
                  >
                    <span>{label}</span>
                    <strong>{value}</strong>
                  </button>
                  <button
                    type="button"
                    className="task-list-filter-chip-remove"
                    onClick={() => {
                      const next = { ...filters };
                      if (field === 'taskId' || field === 'title') next[field] = {};
                      else if (field === 'repository') next.repository = { ...emptyValueFilter(), exactText: '' };
                      else if (field === 'scheduledFor' || field === 'createdAt' || field === 'closedAt') next[field] = {};
                      else next[field] = emptyValueFilter();
                      applyFilters(next);
                    }}
                    aria-label={`Remove ${label} filter`}
                  >
                    ×
                  </button>
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
        <div className="task-list-mobile-filter-controls" aria-label="Mobile task filters">
          {TABLE_COLUMNS.map(([field]) =>
            isFilterField(field) ? <div key={field}>{renderFilterControl(field, 'Mobile ')}</div> : null,
          )}
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
                        const filterField = isFilterField(field) ? field : null;
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
                                data-filter-field={field}
                                ref={
                                  filterField && pendingFocusField === filterField
                                    ? (node) => node?.focus()
                                    : undefined
                                }
                                onMouseDown={(event) => event.stopPropagation()}
                                onClick={(event) => {
                                  event.stopPropagation();
                                  if (filterField) {
                                    filterTriggerRef.current = event.currentTarget;
                                    toggleFilter(filterField);
                                  }
                                }}
                                aria-label={filterAccessibilityLabel(field, label)}
                                aria-expanded={filterField ? openFilter === filterField : false}
                              >
                                <span aria-hidden="true">Filter</span>
                              </button>
                            </div>
                            {filterField ? renderFilterPopover(filterField, label) : null}
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
