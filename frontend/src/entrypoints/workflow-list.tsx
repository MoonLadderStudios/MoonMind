import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { z } from 'zod';

import { BootPayload } from '../boot/parseBootPayload';
import { formatRuntimeLabel, formatStatusLabel } from '../utils/formatters';
import { ExecutionStatusPill } from '../components/ExecutionStatusPill';
import { PageSizeSelector, parsePageSize } from '../components/PageSizeSelector';
import { WorkflowRowActionsMenu } from '../components/WorkflowRowActionsMenu';
import {
  TOGGLEABLE_WORKFLOW_LIST_COLUMNS,
  readDashboardPreferences,
  resetDashboardPreferences,
  updateDashboardPreferences,
  type ToggleableWorkflowListColumn,
  type WorkflowListDensity,
} from '../utils/dashboardPreferences';

const POLL_MS_DEFAULT = 5000;

// MM-954: the workflow list uses cursor pagination and sorts only the rows on the
// currently loaded page (client-side). This notice keeps the UI honest about that
// scope instead of implying a global server-side sort across the full result set.
const CURRENT_PAGE_SORT_NOTICE = 'Sorting applies to the current page only.';

type ListDashboardConfig = {
  pollIntervalsMs?: { list?: number };
  features?: {
    temporalDashboard?: {
      listEnabled?: boolean;
      actionsEnabled?: boolean;
      temporalWorkflowEditing?: boolean;
      temporalTaskEditing?: boolean;
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
  'intervention_requested',
  'finalizing',
  'completed',
  'failed',
  'canceled',
] as const;
const RUNTIME_FILTER_OPTIONS = [
  'codex_cli',
  'claude_code',
  'gemini_cli',
  'jules',
  'codex_cloud',
] as const;
const RUNTIME_FILTER_VALUE_ALIASES: Record<string, string> = {
  codex_cli: 'codex_cli',
  codex_cloud: 'codex_cloud',
  claude_code: 'claude_code',
  gemini_cli: 'gemini_cli',
  jules: 'jules',
};
const TASK_WORKFLOW_TYPE = 'MoonMind.UserWorkflow';
const TASK_ENTRY = 'user_workflow';

const TIMESTAMP_SORT_FIELDS = new Set(['updatedAt', 'scheduledFor', 'createdAt', 'closedAt']);
type FilterField =
  | 'workflowId'
  | 'status'
  | 'repository'
  | 'targetRuntime'
  | 'targetSkill'
  | 'title'
  | 'scheduledFor'
  | 'updatedAt'
  | 'createdAt'
  | 'closedAt';

// Matches the 768px breakpoint that switches dashboard.css between the mobile
// card list and the desktop table. Above it, the table headers carry the
// per-column filters and the "View options" control, so the results header row
// (Filters trigger + chips) is not rendered.
const DESKTOP_MEDIA_QUERY = '(min-width: 768px)';

// Scan-first desktop columns, ordered left-to-right. `field` is the sort/identity
// key and `sortable` controls whether the header renders a sort button or a static
// label. Workflow title leads as the primary anchor; raw/debug identifiers move
// out of the default table.
type TableColumnDef = {
  field: string;
  label: string;
  sortable: boolean;
  colClassName: string;
};
const TABLE_COLUMNS: TableColumnDef[] = [
  { field: 'title', label: 'Workflow', sortable: true, colClassName: 'queue-table-column-workflow' },
  { field: 'status', label: 'Status', sortable: true, colClassName: 'queue-table-column-status' },
  { field: 'nextAction', label: 'Next action', sortable: false, colClassName: 'queue-table-column-next-action' },
  { field: 'repository', label: 'Repository', sortable: true, colClassName: 'queue-table-column-repository' },
  { field: 'targetRuntime', label: 'Runtime', sortable: true, colClassName: 'queue-table-column-runtime' },
  { field: 'updatedAt', label: 'Updated', sortable: true, colClassName: 'queue-table-column-date' },
];
const TABLE_COLUMN_FILTER_FIELDS: Partial<Record<string, FilterField>> = {
  title: 'title',
  status: 'status',
  repository: 'repository',
  targetRuntime: 'targetRuntime',
  updatedAt: 'updatedAt',
};

// All filterable columns. This is the durable filter data model and feeds the
// advanced filter drawer sections and the active filter chips.
const FILTER_FIELDS = [
  ['workflowId', 'ID'],
  ['title', 'Title'],
  ['status', 'Status'],
  ['repository', 'Repository'],
  ['targetRuntime', 'Runtime'],
  ['targetSkill', 'Skill'],
  ['updatedAt', 'Updated'],
  ['scheduledFor', 'Scheduled'],
  ['createdAt', 'Created'],
  ['closedAt', 'Finished'],
] as const;
type FilterColumn = (typeof FILTER_FIELDS)[number];
const ACTIVE_FILTER_FIELDS = new Set<FilterField>([
  'workflowId',
  'status',
  'repository',
  'targetRuntime',
  'targetSkill',
  'title',
  'updatedAt',
  'scheduledFor',
  'createdAt',
  'closedAt',
]);
// Advanced filter drawer field order. The drawer groups the precise
// include/exclude/date/blank filters that previously lived in every desktop
// table header. Labels match the column labels so chips, sections, and the
// underlying filter state share one vocabulary.
const DRAWER_FILTER_FIELDS: Array<[FilterField, string]> = [
  ['workflowId', 'ID'],
  ['title', 'Title'],
  ['status', 'Status'],
  ['repository', 'Repository'],
  ['targetRuntime', 'Runtime'],
  ['targetSkill', 'Skill'],
  ['updatedAt', 'Updated'],
  ['scheduledFor', 'Scheduled'],
  ['createdAt', 'Created'],
  ['closedAt', 'Finished'],
];
function isFilterField(field: string): field is FilterField {
  return ACTIVE_FILTER_FIELDS.has(field as FilterField);
}
type ValueFilter = { mode: 'include' | 'exclude'; values: string[]; blank?: 'include' | 'exclude' | '' };
type RepositoryFilter = ValueFilter & { exactText?: string };
type TextFilter = { contains?: string };
type DateFilter = { from?: string; to?: string; blank?: 'include' | 'exclude' | '' };
type ColumnFilters = {
  workflowId: TextFilter;
  status: ValueFilter;
  repository: RepositoryFilter;
  targetRuntime: ValueFilter;
  targetSkill: ValueFilter;
  title: TextFilter;
  updatedAt: DateFilter;
  scheduledFor: DateFilter;
  createdAt: DateFilter;
  closedAt: DateFilter;
};

const ExecutionRowSchema = z
  .object({
    taskId: z.string().optional(),
    workflowId: z.string().optional(),
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
    attentionRequired: z.boolean().optional(),
  })
  .passthrough();

const ExecutionListResponseSchema = z.object({
  items: z.array(ExecutionRowSchema),
  nextPageToken: z.string().nullable().optional(),
  count: z.number().nullable().optional(),
  countMode: z.string().optional(),
});

type ExecutionRow = z.infer<typeof ExecutionRowSchema>;

function rowWorkflowId(row: ExecutionRow): string {
  return row.workflowId || row.taskId || '';
}

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

function formatWhen(iso: string | null | undefined): string {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString(undefined, {
    year: '2-digit',
    month: 'numeric',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    second: '2-digit',
  });
}

function hasUnsupportedWorkflowScopeState(params: URLSearchParams): boolean {
  const scope = (params.get('scope') || '').trim().toLowerCase();
  const workflowType = (params.get('workflowType') || '').trim();
  const entry = (params.get('entry') || '').trim().toLowerCase();
  return Boolean(
    scope ||
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

function interventionListSummary(row: ExecutionRow): string {
  const state = String(row.rawState || row.state || row.status || '').toLowerCase();
  if (row.attentionRequired || state === 'intervention_requested') {
    return 'Intervention requested';
  }
  return '';
}

// Next-action signals for the scan-first status area. Reuses the dependency and
// intervention summaries and falls back to a terminal/waiting reason so operators
// can see what needs attention without opening the workflow.
function nextActionItems(row: ExecutionRow): string[] {
  const items: string[] = [];
  const intervention = interventionListSummary(row);
  if (intervention) items.push(intervention);
  const deps = dependencyListSummary(row);
  if (deps) items.push(deps);
  if (items.length === 0) {
    const state = String(row.rawState || row.state || row.status || '').toLowerCase();
    if (state === 'failed') items.push('Failed — needs review');
    else if (state === 'awaiting_external') items.push('Waiting on external response');
  }
  return items;
}

function rowUpdatedAt(row: ExecutionRow): string | null | undefined {
  return row.closedAt || row.scheduledFor || row.createdAt;
}

const RELATIVE_TIME_UNITS: Array<[string, number]> = [
  ['y', 31536000],
  ['mo', 2592000],
  ['w', 604800],
  ['d', 86400],
  ['h', 3600],
  ['m', 60],
];

function formatRelative(iso: string | null | undefined): string {
  if (!iso) return '—';
  const date = new Date(iso);
  const ms = date.getTime();
  if (Number.isNaN(ms)) return iso;
  const diffSeconds = Math.round((Date.now() - ms) / 1000);
  const absSeconds = Math.abs(diffSeconds);
  if (absSeconds < 45) return 'just now';
  const suffix = diffSeconds >= 0 ? 'ago' : 'from now';
  for (const [label, unitSeconds] of RELATIVE_TIME_UNITS) {
    if (absSeconds >= unitSeconds) {
      return `${Math.floor(absSeconds / unitSeconds)}${label} ${suffix}`;
    }
  }
  return `${absSeconds}s ${suffix}`;
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
        if (field === 'updatedAt') return rowUpdatedAt(row);
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
    return rowWorkflowId(right).localeCompare(rowWorkflowId(left));
  });
  return copy;
}

function replaceUrlQuery(params: URLSearchParams) {
  const queryText = params.toString();
  const path = window.location.pathname;
  window.history.replaceState({}, '', queryText ? `${path}?${queryText}` : path);
}

function sanitizeApiErrorMessage(message: string): string {
  return message.replace(/\s+/g, ' ').trim().slice(0, 500);
}

function apiErrorMessageFromPayload(payload: unknown): string | null {
  if (typeof payload === 'string') {
    return sanitizeApiErrorMessage(payload) || null;
  }
  if (!payload || typeof payload !== 'object') return null;
  const record = payload as Record<string, unknown>;
  if (typeof record.detail === 'string') {
    return sanitizeApiErrorMessage(record.detail) || null;
  }
  if (record.detail && typeof record.detail === 'object' && !Array.isArray(record.detail)) {
    const detail = record.detail as Record<string, unknown>;
    if (typeof detail.message === 'string') {
      return sanitizeApiErrorMessage(detail.message) || null;
    }
  }
  if (Array.isArray(record.detail)) {
    const firstMessage = record.detail
      .map((item) => (item && typeof item === 'object' ? (item as Record<string, unknown>).msg : null))
      .find((message): message is string => typeof message === 'string');
    if (firstMessage) return sanitizeApiErrorMessage(firstMessage) || null;
  }
  if (typeof record.message === 'string') {
    return sanitizeApiErrorMessage(record.message) || null;
  }
  return null;
}

async function taskListErrorMessage(response: Response): Promise<string> {
  try {
    const payload = await response.json();
    const message = apiErrorMessageFromPayload(payload);
    if (message) return message;
  } catch {
    // Fall back to status text below when the body is empty or not JSON.
  }
  const statusText = sanitizeApiErrorMessage(response.statusText || '');
  return statusText ? `Failed to fetch: ${statusText}` : 'Failed to fetch workflows.';
}

function emptyValueFilter(): ValueFilter {
  return { mode: 'include', values: [], blank: '' };
}

function emptyFilters(): ColumnFilters {
  return {
    workflowId: {},
    status: emptyValueFilter(),
    repository: { ...emptyValueFilter(), exactText: '' },
    targetRuntime: emptyValueFilter(),
    targetSkill: emptyValueFilter(),
    title: {},
    updatedAt: {},
    scheduledFor: {},
    createdAt: {},
    closedAt: {},
  };
}

function uniqueValues(values: Array<string | null | undefined>): string[] {
  return Array.from(new Set(values.map((value) => (value || '').trim()).filter(Boolean)));
}

function normalizeRuntimeFilterValue(value: string | null | undefined): string {
  const raw = (value || '').trim();
  if (!raw) return '';
  const key = raw.toLowerCase().replace(/[\s-]+/g, '_');
  return RUNTIME_FILTER_VALUE_ALIASES[key] || key;
}

function uniqueRuntimeValues(values: Array<string | null | undefined>): string[] {
  return uniqueValues(values.map(normalizeRuntimeFilterValue));
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
  const repoExact = (params.get('repoContains') || params.get('repoExact') || params.get('repo') || '').trim();
  if (repoNotIn.length > 0) {
    filters.repository = { mode: 'exclude', values: repoNotIn, exactText: repoExact, blank: '' };
  } else {
    filters.repository = { mode: 'include', values: repoIn, exactText: repoExact, blank: '' };
  }
  filters.workflowId = { contains: params.get('workflowIdContains') || params.get('workflowId') || '' };
  filters.title = { contains: params.get('titleContains') || '' };

  const runtimeIn = splitParam(params, 'targetRuntimeIn');
  const runtimeNotIn = splitParam(params, 'targetRuntimeNotIn');
  const normalizedRuntimeIn = uniqueRuntimeValues(runtimeIn);
  const normalizedRuntimeNotIn = uniqueRuntimeValues(runtimeNotIn);
  const legacyRuntime = normalizeRuntimeFilterValue(params.get('targetRuntime'));
  if (normalizedRuntimeNotIn.length > 0) {
    filters.targetRuntime = { mode: 'exclude', values: normalizedRuntimeNotIn, blank: '' };
  } else if (normalizedRuntimeIn.length > 0 || legacyRuntime) {
    filters.targetRuntime = {
      mode: 'include',
      values: normalizedRuntimeIn.length > 0 ? normalizedRuntimeIn : [legacyRuntime],
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
  filters.updatedAt = {
    from: params.get('updatedFrom') || '',
    to: params.get('updatedTo') || '',
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
  if (filters.workflowId.contains?.trim()) params.set('workflowIdContains', filters.workflowId.contains.trim());
  appendValueParams(params, filters.status, 'stateIn', 'stateNotIn');
  if (filters.repository.exactText?.trim()) {
    params.set('repoContains', filters.repository.exactText.trim());
  }
  appendValueParams(params, filters.repository, 'repoIn', 'repoNotIn', 'repoBlank');
  appendValueParams(params, filters.targetRuntime, 'targetRuntimeIn', 'targetRuntimeNotIn', 'targetRuntimeBlank');
  appendValueParams(params, filters.targetSkill, 'targetSkillIn', 'targetSkillNotIn', 'targetSkillBlank');
  if (filters.title.contains?.trim()) params.set('titleContains', filters.title.contains.trim());
  appendDateParams(params, filters.scheduledFor, 'scheduledFrom', 'scheduledTo', 'scheduledBlank');
  appendDateParams(params, filters.updatedAt, 'updatedFrom', 'updatedTo');
  appendDateParams(params, filters.createdAt, 'createdFrom', 'createdTo');
  appendDateParams(params, filters.closedAt, 'finishedFrom', 'finishedTo', 'finishedBlank');
}

// The four value fields that resolve to a server-side facet. `integration` is a
// valid response facet but is not a workflow-list filter field, so it is not
// part of this map.
type FacetField = 'status' | 'targetRuntime' | 'targetSkill' | 'repository';

function facetForFilterField(field: FilterField | null): FacetField | null {
  if (field === 'status') return 'status';
  if (field === 'targetRuntime') return 'targetRuntime';
  if (field === 'targetSkill') return 'targetSkill';
  if (field === 'repository') return 'repository';
  return null;
}

type PillMultiSelectProps = {
  id?: string;
  values: string[];
  options: string[];
  formatValue?: (value: string) => string;
  disabled?: boolean;
  ariaLabelAdd: string;
  ariaLabelSelected: string;
  addPlaceholder?: string;
  emptyMessage?: string;
  onChange: (next: string[]) => void;
};

function FilterPillMultiSelect({
  id,
  values,
  options,
  formatValue,
  disabled,
  ariaLabelAdd,
  ariaLabelSelected,
  addPlaceholder = 'Add value',
  emptyMessage = 'No values selected',
  onChange,
}: PillMultiSelectProps) {
  const fmt = formatValue || ((value: string) => value);
  const available = options.filter((option) => !values.includes(option));
  const noneSelected = values.length === 0;
  return (
    <div className="workflow-list-pill-multiselect">
      <ul
        className={`workflow-list-pill-list${noneSelected ? ' is-empty' : ''}`}
        aria-label={ariaLabelSelected}
      >
        {noneSelected ? (
          <li className="workflow-list-pill-empty small">{emptyMessage}</li>
        ) : (
          values.map((value) => (
            <li key={value} className="workflow-list-pill">
              <span className="workflow-list-pill-label">{fmt(value)}</span>
              <button
                type="button"
                className="workflow-list-pill-remove"
                disabled={disabled}
                aria-label={`Remove ${fmt(value)}`}
                onClick={() => onChange(values.filter((entry) => entry !== value))}
              >
                <span aria-hidden="true">×</span>
              </button>
            </li>
          ))
        )}
      </ul>
      <select
        id={id}
        aria-label={ariaLabelAdd}
        value=""
        disabled={disabled || available.length === 0}
        onChange={(event) => {
          const next = event.target.value;
          if (!next) return;
          onChange([...values, next]);
        }}
      >
        <option value="">
          {available.length === 0 ? 'All values selected' : addPlaceholder}
        </option>
        {available.map((option) => (
          <option key={option} value={option}>
            {fmt(option)}
          </option>
        ))}
      </select>
    </div>
  );
}

function summarizeValues(
  filter: ValueFilter,
  formatter = (value: string) => value,
  options: { maxVisibleValues?: number } = {},
): string {
  if (filter.blank === 'include' && filter.values.length === 0) return 'blank';
  if (filter.blank === 'exclude' && filter.values.length === 0) return 'not blank';
  if (filter.values.length === 0) return '';

  const maxVisibleValues = Math.max(1, options.maxVisibleValues ?? 1);
  const visibleLabels = filter.values
    .slice(0, maxVisibleValues)
    .map((value) => formatter(value));
  const hiddenCount = filter.values.length - visibleLabels.length;
  const label = `${visibleLabels.join(', ')}${hiddenCount > 0 ? ` +${hiddenCount}` : ''}`;
  if (filter.mode !== 'exclude') return label;
  return filter.values.length > 1 ? `not (${label})` : `not ${label}`;
}

function filterSummary(field: FilterField, filters: ColumnFilters): string {
  if (field === 'workflowId') return filters.workflowId.contains?.trim() || '';
  if (field === 'status') return summarizeValues(filters.status, formatStatusLabel, { maxVisibleValues: 3 });
  if (field === 'targetRuntime') return summarizeValues(filters.targetRuntime, formatRuntimeLabel);
  if (field === 'targetSkill') return summarizeValues(filters.targetSkill);
  if (field === 'repository') {
    if (filters.repository.exactText?.trim()) return filters.repository.exactText.trim();
    return summarizeValues(filters.repository);
  }
  if (field === 'title') return filters.title.contains?.trim() || '';
  const dateFilter =
    field === 'scheduledFor'
      ? filters.scheduledFor
      : field === 'updatedAt'
        ? filters.updatedAt
        : field === 'createdAt'
          ? filters.createdAt
          : filters.closedAt;
  if (dateFilter.blank === 'include' && !dateFilter.from && !dateFilter.to) return 'blank';
  if (dateFilter.blank === 'exclude' && !dateFilter.from && !dateFilter.to) return 'not blank';
  const parts = [];
  if (dateFilter.from) parts.push(`from ${dateFilter.from}`);
  if (dateFilter.to) parts.push(`to ${dateFilter.to}`);
  if (dateFilter.blank === 'include') parts.push('blank');
  if (dateFilter.blank === 'exclude') parts.push('not blank');
  return parts.join(', ');
}

function clearFilterField(filters: ColumnFilters, field: FilterField): ColumnFilters {
  const next = { ...filters };
  if (field === 'workflowId' || field === 'title') next[field] = {};
  else if (field === 'repository') next.repository = { ...emptyValueFilter(), exactText: '' };
  else if (field === 'scheduledFor' || field === 'updatedAt' || field === 'createdAt' || field === 'closedAt') next[field] = {};
  else next[field] = emptyValueFilter();
  return next;
}

export function WorkflowListPage({ payload }: { payload: BootPayload }) {
  const dashboardCfg = useMemo(() => readListDashboardConfig(payload), [payload.initialData]);
  const listPollMs = useMemo(() => {
    const candidate = dashboardCfg?.pollIntervalsMs?.list;
    return typeof candidate === 'number' && candidate > 0 ? candidate : POLL_MS_DEFAULT;
  }, [dashboardCfg]);
  const listEnabled = dashboardCfg?.features?.temporalDashboard?.listEnabled !== false;
  const actionsEnabled = Boolean(dashboardCfg?.features?.temporalDashboard?.actionsEnabled);
  const taskEditingEnabled = Boolean(
    dashboardCfg?.features?.temporalDashboard?.temporalWorkflowEditing ??
      dashboardCfg?.features?.temporalDashboard?.temporalTaskEditing,
  );

  const initial = useMemo(() => new URLSearchParams(window.location.search), []);

  const initialFilterValidationErrors = useMemo(() => validateInitialFilterParams(initial), [initial]);
  const [ignoredWorkflowScopeState] = useState(() => hasUnsupportedWorkflowScopeState(initial));
  const [filters, setFilters] = useState(() => parseInitialFilters(initial));
  const [draftFilters, setDraftFilters] = useState(() => parseInitialFilters(initial));
  const [hasEditedFilters, setHasEditedFilters] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [desktopFilterField, setDesktopFilterField] = useState<FilterField | null>(null);
  const drawerRef = useRef<HTMLDivElement | null>(null);
  const drawerToggleRef = useRef<HTMLButtonElement | null>(null);
  const desktopFilterRef = useRef<HTMLDivElement | null>(null);
  // The element that opened the drawer (the Filters trigger or a chip). Focus is
  // returned here when the drawer closes so keyboard users keep their place.
  const filterTriggerRef = useRef<HTMLElement | null>(null);
  // Field whose first control should receive focus when the drawer opens.
  const pendingDrawerFocusRef = useRef<FilterField | null>(null);
  // MM-964: local-first dashboard preferences. Read once on mount; mutations are
  // mirrored back to localStorage so they survive reload. An explicit `limit` in
  // the URL still wins over the stored page-size preference so shared links keep
  // their page size.
  const initialPrefs = useMemo(() => readDashboardPreferences(), []);
  const [density, setDensity] = useState<WorkflowListDensity>(initialPrefs.workflowListDensity);
  const [columnVisibility, setColumnVisibility] = useState(
    initialPrefs.workflowListColumnVisibility,
  );
  const [liveUpdatesPref, setLiveUpdatesPref] = useState(initialPrefs.liveUpdatesEnabled);
  const [prefsMenuOpen, setPrefsMenuOpen] = useState(false);
  const prefsMenuRef = useRef<HTMLDivElement | null>(null);
  // The desktop table and the mobile card list are separate surfaces. On desktop
  // the per-column filter buttons replace the Filters trigger and the "View
  // options" control moves into the Actions header, so the results header row
  // above the table is dropped entirely. matchMedia is unavailable under jsdom;
  // fall back to the mobile layout there so the test surface keeps the Filters
  // trigger and text "View options" button.
  const [isDesktop, setIsDesktop] = useState(() =>
    typeof window !== 'undefined' && typeof window.matchMedia === 'function'
      ? window.matchMedia(DESKTOP_MEDIA_QUERY).matches
      : false,
  );
  const [pageSize, setPageSize] = useState(() => {
    const limitParam = initial.get('limit');
    return limitParam !== null ? parsePageSize(limitParam) : initialPrefs.workflowListPageSize;
  });
  const [listCursor, setListCursor] = useState<string | null>(() =>
    ignoredWorkflowScopeState ? null : initial.get('nextPageToken')?.trim() || null,
  );
  const [cursorStack, setCursorStack] = useState<string[]>([]);
  // MM-954: sorting is current-page-only. The sort field/direction live in
  // component state only — they are intentionally neither seeded from nor written
  // to the URL, so a shared link never implies a global server-side sort across
  // the full filtered result set.
  const [sortField, setSortField] = useState<string>('updatedAt');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
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
    // MM-954: do not persist sort/sortDir to the URL. Sorting only reorders the
    // currently loaded page, so writing it to the URL would falsely imply a
    // global server-side sort across the full filtered result set.
    replaceUrlQuery(params);
  }, [
    filters,
    filterValidationErrors.length,
    pageSize,
    listCursor,
  ]);

  useEffect(() => {
    syncUrl();
  }, [syncUrl]);

  const queryKey = [
    'workflow-list',
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
      if (listCursor) params.set('nextPageToken', listCursor);
      appendFilterParams(params, filters);
      const response = await fetch(`${payload.apiBase}/executions?${params}`);
      if (!response.ok) {
        throw new Error(await taskListErrorMessage(response));
      }
      return ExecutionListResponseSchema.parse(await response.json());
    },
    refetchInterval:
      listEnabled && liveUpdatesPref && !drawerOpen && desktopFilterField === null
        ? listPollMs
        : false,
    // Honour the live-updates pause on tab focus too: without this the shared
    // dashboard query client falls back to React Query's stale-on-focus default
    // and would refetch /executions when returning to the tab even while paused.
    refetchOnWindowFocus: liveUpdatesPref,
  });

  // Facets enrich the include/exclude dropdowns. The mobile drawer can show
  // every value field at once; desktop column popovers request the active field.
  // This reuses the existing single-facet backend contract without API changes.
  const getFacetQueryOptions = (facet: ExecutionFacetResponse['facet']) => ({
    queryKey: ['workflow-list-facet', facet, filters] as const,
    enabled:
      listEnabled &&
      filterValidationErrors.length === 0 &&
      (drawerOpen || facetForFilterField(desktopFilterField) === facet),
    queryFn: async () => {
      const params = new URLSearchParams();
      params.set('source', 'temporal');
      params.set('facet', facet);
      params.set('pageSize', '50');
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

  const facetByField = {
    status: useQuery(getFacetQueryOptions('status')),
    targetRuntime: useQuery(getFacetQueryOptions('targetRuntime')),
    targetSkill: useQuery(getFacetQueryOptions('targetSkill')),
    repository: useQuery(getFacetQueryOptions('repository')),
  } as const;

  const resetToFirstPage = useCallback(() => {
    setListCursor(null);
    setCursorStack([]);
  }, []);

  // MM-964: column visibility. The workflow title column is the primary anchor
  // and is always shown; every other column honors the stored preference.
  const isColumnVisible = useCallback(
    (field: string): boolean => {
      if (field === 'title') return true;
      if (!(TOGGLEABLE_WORKFLOW_LIST_COLUMNS as readonly string[]).includes(field)) return true;
      return columnVisibility[field as ToggleableWorkflowListColumn] !== false;
    },
    [columnVisibility],
  );
  const visibleColumns = useMemo(
    () => TABLE_COLUMNS.filter((column) => isColumnVisible(column.field)),
    [isColumnVisible],
  );

  const handleDensityChange = useCallback((next: WorkflowListDensity) => {
    setDensity(next);
    updateDashboardPreferences({ workflowListDensity: next });
  }, []);

  const handleToggleColumn = useCallback((field: ToggleableWorkflowListColumn, visible: boolean) => {
    setColumnVisibility((current) => {
      const next = { ...current, [field]: visible };
      updateDashboardPreferences({ workflowListColumnVisibility: next });
      return next;
    });
  }, []);

  const handleLiveUpdatesChange = useCallback((enabled: boolean) => {
    setLiveUpdatesPref(enabled);
    updateDashboardPreferences({ liveUpdatesEnabled: enabled });
  }, []);

  const handlePageSizeChange = useCallback(
    (size: number) => {
      setPageSize(size);
      updateDashboardPreferences({ workflowListPageSize: size });
      resetToFirstPage();
    },
    [resetToFirstPage],
  );

  const handleResetPreferences = useCallback(() => {
    const defaults = resetDashboardPreferences();
    setDensity(defaults.workflowListDensity);
    setColumnVisibility(defaults.workflowListColumnVisibility);
    setLiveUpdatesPref(defaults.liveUpdatesEnabled);
    setPageSize(defaults.workflowListPageSize);
    resetToFirstPage();
  }, [resetToFirstPage]);

  const restoreTriggerFocus = useCallback(() => {
    const trigger = filterTriggerRef.current;
    window.requestAnimationFrame(() => {
      if (trigger?.isConnected) {
        trigger.focus();
      } else {
        drawerToggleRef.current?.focus();
      }
    });
  }, []);

  const closeDesktopFilter = useCallback(() => {
    setDesktopFilterField(null);
    setDraftFilters(filters);
  }, [filters]);

  const openDrawer = useCallback(
    (field: FilterField | null, trigger: HTMLElement | null) => {
      filterTriggerRef.current = trigger;
      pendingDrawerFocusRef.current = field;
      setDraftFilters(filters);
      setDrawerOpen(true);
    },
    [filters],
  );

  const closeDrawer = useCallback(
    (options: { restoreFocus?: boolean } = {}) => {
      setDrawerOpen(false);
      setDraftFilters(filters);
      if (options.restoreFocus !== false) {
        restoreTriggerFocus();
      }
    },
    [filters, restoreTriggerFocus],
  );

  const applyDraftFilters = useCallback(() => {
    setHasEditedFilters(true);
    setFilters(draftFilters);
    resetToFirstPage();
    setDrawerOpen(false);
    setDesktopFilterField(null);
    restoreTriggerFocus();
  }, [draftFilters, resetToFirstPage, restoreTriggerFocus]);

  const resetDraftFilters = useCallback(() => {
    const cleared = emptyFilters();
    setHasEditedFilters(true);
    setDraftFilters(cleared);
    setFilters(cleared);
    resetToFirstPage();
    setDesktopFilterField(null);
  }, [resetToFirstPage]);

  const removeActiveFilter = useCallback(
    (field: FilterField) => {
      setHasEditedFilters(true);
      const next = clearFilterField(filters, field);
      setFilters(next);
      setDraftFilters(next);
      resetToFirstPage();
      setDesktopFilterField(null);
    },
    [filters, resetToFirstPage],
  );

  useEffect(() => {
    if (desktopFilterField === null) return;
    const onMouseDown = (event: MouseEvent) => {
      const root = desktopFilterRef.current;
      if (root && !root.contains(event.target as Node)) {
        closeDesktopFilter();
      }
    };
    document.addEventListener('mousedown', onMouseDown);
    return () => document.removeEventListener('mousedown', onMouseDown);
  }, [closeDesktopFilter, desktopFilterField]);

  // Track the desktop breakpoint so the toolbar layout (Filters trigger vs.
  // per-column filters, results header vs. Actions-header "View options") stays
  // in sync with the active responsive surface.
  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return;
    const query = window.matchMedia(DESKTOP_MEDIA_QUERY);
    const onChange = (event: MediaQueryListEvent) => setIsDesktop(event.matches);
    setIsDesktop(query.matches);
    query.addEventListener('change', onChange);
    return () => query.removeEventListener('change', onChange);
  }, []);

  // MM-964: close the dashboard preferences popover on an outside click.
  useEffect(() => {
    if (!prefsMenuOpen) return;
    const onMouseDown = (event: MouseEvent) => {
      const root = prefsMenuRef.current;
      if (root && !root.contains(event.target as Node)) {
        setPrefsMenuOpen(false);
      }
    };
    document.addEventListener('mousedown', onMouseDown);
    return () => document.removeEventListener('mousedown', onMouseDown);
  }, [prefsMenuOpen]);

  useEffect(() => {
    if (desktopFilterField === null) return;
    const frame = window.requestAnimationFrame(() => {
      desktopFilterRef.current
        ?.querySelector<HTMLElement>('input:not([disabled]), select:not([disabled]), button:not([disabled])')
        ?.focus();
    });
    return () => window.cancelAnimationFrame(frame);
  }, [desktopFilterField]);

  // When the drawer opens, move focus to the first control of the requested
  // field (or the first control overall). This drives the keyboard "open the
  // drawer" and "navigate fields" behaviors.
  useEffect(() => {
    if (!drawerOpen) return;
    const frame = window.requestAnimationFrame(() => {
      const field = pendingDrawerFocusRef.current;
      pendingDrawerFocusRef.current = null;
      const root = drawerRef.current;
      if (!root) return;
      // Focus the requested field's first control, or fall back to the first
      // control in the filter body (not the header close button).
      const scope =
        (field && root.querySelector<HTMLElement>(`[data-filter-section="${field}"]`)) ||
        root.querySelector<HTMLElement>('.workflow-list-filter-drawer-body') ||
        root;
      const focusTarget = scope.querySelector<HTMLElement>(
        'input:not([disabled]), select:not([disabled]), button:not([disabled])',
      );
      focusTarget?.focus();
    });
    return () => window.cancelAnimationFrame(frame);
  }, [drawerOpen]);

  const sortedItems = useMemo(() => {
    const items = data?.items || [];
    return sortRows(items, sortField, sortDir);
  }, [data?.items, sortField, sortDir]);

  const pageIndex = cursorStack.length;
  const pageStart = sortedItems.length > 0 ? pageIndex * pageSize + 1 : 0;
  const pageEnd = pageIndex * pageSize + sortedItems.length;
  const countSummary = displayTemporalCount(data?.count, data?.countMode);
  const hasPaginationContext = cursorStack.length > 0 || Boolean(listCursor);

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
      ? 'Not sorted. Activate to sort the current page ascending.'
      : sortDir === 'asc'
        ? 'Sorted ascending, current page only. Activate to sort descending.'
        : 'Sorted descending, current page only. Activate to sort ascending.';
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

  const pageRangeSummary = sortedItems.length > 0 ? `${pageStart} - ${pageEnd}` : '0 - 0';
  const totalEntriesSummary = countSummary ? `${countSummary} total entries` : '';
  const resultsFooter = (
    <div className="queue-results-toolbar workflow-list-results-footer">
      <div className="workflow-list-footer-live">
        <span className="small">
          {!listEnabled
            ? 'Live updates unavailable while the list is disabled.'
            : liveUpdatesPref
              ? `Live updates enabled. Polling every ${Math.round(listPollMs / 1000)}s`
              : 'Live updates paused.'}
        </span>
        {sortedItems.length > 0 ? (
          <span className="small workflow-list-sort-scope-note">{CURRENT_PAGE_SORT_NOTICE}</span>
        ) : null}
      </div>
      <div className="queue-pagination workflow-list-footer-pagination">
        <PageSizeSelector
          pageSize={pageSize}
          disabled={!listEnabled}
          onPageSizeChange={handlePageSizeChange}
        />
        <div className="workflow-list-footer-page-summary" aria-label="Pagination summary">
          <span className="small">{pageRangeSummary}</span>
          {totalEntriesSummary ? <span className="small">{totalEntriesSummary}</span> : null}
        </div>
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
  );
  const activeFilters = useMemo(
    () =>
      FILTER_FIELDS.map(([field, label]) => {
        if (!isFilterField(field)) return null;
        const value = filterSummary(field, filters);
        return value ? { field, label, value } : null;
      }).filter(
        (filter): filter is { field: FilterField; label: FilterColumn[1]; value: string } => Boolean(filter),
      ),
    [filters],
  );
  const hasActiveFilters = activeFilters.length > 0;
  const hasWorkflowListNotices =
    !listEnabled ||
    Boolean(ignoredWorkflowScopeState) ||
    filterValidationErrors.length > 0;

  const updateDraftText = (field: 'workflowId' | 'title', value: string) => {
    setDraftFilters((current) => ({
      ...current,
      [field]: { contains: value },
    }));
  };

  const updateDraftValues = (
    field: 'status' | 'targetRuntime' | 'targetSkill',
    values: string[],
  ) => {
    setDraftFilters((current) => {
      const dedupedValues = field === 'targetRuntime' ? uniqueRuntimeValues(values) : uniqueValues(values);
      if (dedupedValues.length === 0) {
        return { ...current, [field]: { ...emptyValueFilter(), mode: current[field].mode } };
      }
      return {
        ...current,
        [field]: { ...current[field], values: dedupedValues, blank: '' },
      };
    });
  };

  const updateDraftValueMode = (
    field: 'status' | 'targetRuntime' | 'targetSkill',
    mode: ValueFilter['mode'],
  ) => {
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

  const updateDraftDate = (field: 'scheduledFor' | 'updatedAt' | 'createdAt' | 'closedAt', patch: DateFilter) => {
    setDraftFilters((current) => ({ ...current, [field]: { ...current[field], ...patch } }));
  };

  const valueOptionsForField = (field: FilterField): string[] => {
    const facetKey = facetForFilterField(field);
    const facetData = facetKey ? facetByField[facetKey].data : undefined;
    const facetValues =
      facetData && facetData.facet === facetKey ? facetData.items.map((item) => item.value) : [];
    if (field === 'status') return uniqueValues([...facetValues, ...TEMPORAL_STATUSES]);
    if (field === 'targetRuntime') {
      return uniqueRuntimeValues([
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
    const facetKey = facetForFilterField(field);
    if (!facetKey) return null;
    const facetQuery = facetByField[facetKey];
    if (facetQuery.isError) {
      return (
        <p className="small workflow-list-facet-notice" role="status">
          Facet values unavailable. Showing current page values only.
        </p>
      );
    }
    if (facetQuery.isFetching) {
      return <p className="small workflow-list-facet-notice">Loading facet values...</p>;
    }
    if (facetQuery.data?.truncated) {
      return <p className="small workflow-list-facet-notice">Facet values truncated by the server.</p>;
    }
    return null;
  };

  // Renders one filter's controls bound to the draft filter state. Edits stage
  // into `draftFilters` until the user applies them.
  const renderFilterControl = (field: FilterField) => {
    if (field === 'workflowId' || field === 'title') {
      const label = field === 'workflowId' ? 'ID' : 'Title';
      const draft = draftFilters[field];
      return (
        <div className="queue-inline-filter workflow-list-filter-control">
          <label>
            {label} filter value
            <input
              type="text"
              value={draft.contains || ''}
              disabled={!listEnabled}
              placeholder={field === 'workflowId' ? 'id starts with…' : 'title word…'}
              title={
                field === 'workflowId'
                  ? 'Prefix match: finds workflow IDs that start with this text.'
                  : 'Word match: finds titles containing this whole word.'
              }
              onChange={(event) => updateDraftText(field, event.target.value)}
            />
          </label>
        </div>
      );
    }

    if (field === 'status') {
      const draft = draftFilters.status;
      return (
        <div className="queue-inline-filter workflow-list-filter-control">
          <label>
            Status filter mode
            <select
              value={draft.mode}
              disabled={!listEnabled}
              onChange={(event) => updateDraftValueMode('status', event.target.value as ValueFilter['mode'])}
            >
              <option value="include">Include selected</option>
              <option value="exclude">Exclude selected</option>
            </select>
          </label>
          <FilterPillMultiSelect
            values={draft.values}
            options={[...TEMPORAL_STATUSES]}
            formatValue={formatStatusLabel}
            disabled={!listEnabled}
            ariaLabelAdd="Status filter value"
            ariaLabelSelected="Selected status filters"
            addPlaceholder="Add status"
            emptyMessage="No status filters selected"
            onChange={(next) => updateDraftValues('status', next.map((value) => value.toLowerCase()))}
          />
          {renderFacetNotice('status')}
        </div>
      );
    }

    if (field === 'repository') {
      const repositoryOptions = valueOptionsForField('repository');
      return (
        <div className="queue-inline-filter workflow-list-filter-control">
          <label>
            Repository filter value
            <input
              type="text"
              value={draftFilters.repository.exactText || ''}
              disabled={!listEnabled}
              placeholder="repo starts with…"
              title="Prefix match: finds repository names that start with this text."
              onChange={(event) => updateDraftRepository(event.target.value)}
            />
          </label>
          {repositoryOptions.length > 0 ? (
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
                {repositoryOptions.map((repo) => (
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
      const draft = draftFilters.targetRuntime;
      return (
        <div className="queue-inline-filter workflow-list-filter-control">
          <label>
            Runtime filter mode
            <select
              value={draft.mode}
              disabled={!listEnabled}
              onChange={(event) => updateDraftValueMode('targetRuntime', event.target.value as ValueFilter['mode'])}
            >
              <option value="include">Include selected</option>
              <option value="exclude">Exclude selected</option>
            </select>
          </label>
          <FilterPillMultiSelect
            values={draft.values}
            options={runtimeOptions}
            formatValue={formatRuntimeLabel}
            disabled={!listEnabled}
            ariaLabelAdd="Runtime filter value"
            ariaLabelSelected="Selected runtime filters"
            addPlaceholder="Add runtime"
            emptyMessage="No runtime filters selected"
            onChange={(next) => updateDraftValues('targetRuntime', next)}
          />
          {renderFacetNotice('targetRuntime')}
        </div>
      );
    }

    if (field === 'targetSkill') {
      const skillOptions = valueOptionsForField('targetSkill');
      const draft = draftFilters.targetSkill;
      return (
        <div className="queue-inline-filter workflow-list-filter-control">
          <label>
            Skill filter mode
            <select
              value={draft.mode}
              disabled={!listEnabled}
              onChange={(event) => updateDraftValueMode('targetSkill', event.target.value as ValueFilter['mode'])}
            >
              <option value="include">Include selected</option>
              <option value="exclude">Exclude selected</option>
            </select>
          </label>
          <FilterPillMultiSelect
            values={draft.values}
            options={skillOptions}
            disabled={!listEnabled}
            ariaLabelAdd="Skill filter value"
            ariaLabelSelected="Selected skill filters"
            addPlaceholder="Add skill"
            emptyMessage="No skill filters selected"
            onChange={(next) => updateDraftValues('targetSkill', next)}
          />
          {renderFacetNotice('targetSkill')}
        </div>
      );
    }

    if (field === 'scheduledFor' || field === 'updatedAt' || field === 'createdAt' || field === 'closedAt') {
      const label =
        field === 'scheduledFor'
          ? 'Scheduled'
          : field === 'updatedAt'
            ? 'Updated'
            : field === 'createdAt'
              ? 'Created'
              : 'Finished';
      const draft = draftFilters[field];
      return (
        <div className="queue-inline-filter workflow-list-filter-control">
          <label>
            {label} from
            <input
              type="date"
              value={draft.from || ''}
              disabled={!listEnabled}
              onChange={(event) => updateDraftDate(field, { from: event.target.value })}
            />
          </label>
          <label>
            {label} to
            <input
              type="date"
              value={draft.to || ''}
              disabled={!listEnabled}
              onChange={(event) => updateDraftDate(field, { to: event.target.value })}
            />
          </label>
          {field !== 'createdAt' && field !== 'updatedAt' ? (
            <label>
              {label} blank values
              <select
                value={draft.blank || ''}
                disabled={!listEnabled}
                onChange={(event) =>
                  updateDraftDate(field, { blank: event.target.value as NonNullable<DateFilter['blank']> })
                }
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

  // The "View options" control is a single instance whose placement follows the
  // active surface: an icon button in the Actions header on desktop, the labelled
  // button in the results header otherwise.
  const renderViewOptions = (variant: 'text' | 'icon') => (
    <div
      className={`workflow-list-view-options${variant === 'icon' ? ' workflow-list-view-options--icon' : ''}`}
      ref={prefsMenuRef}
    >
      <button
        type="button"
        className={
          variant === 'icon'
            ? 'workflow-list-view-options-trigger workflow-list-view-options-trigger--icon'
            : 'secondary workflow-list-view-options-trigger'
        }
        aria-haspopup="dialog"
        aria-expanded={prefsMenuOpen}
        aria-label="View options"
        title={variant === 'icon' ? 'View options' : undefined}
        onClick={() => setPrefsMenuOpen((open) => !open)}
      >
        {variant === 'icon' ? (
          <svg
            aria-hidden="true"
            className="workflow-list-view-options-icon"
            viewBox="0 0 16 16"
            focusable="false"
          >
            <g fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round">
              <line x1="2.5" y1="4.5" x2="13.5" y2="4.5" />
              <line x1="2.5" y1="8" x2="13.5" y2="8" />
              <line x1="2.5" y1="11.5" x2="13.5" y2="11.5" />
              <circle cx="6" cy="4.5" r="1.7" fill="currentColor" stroke="none" />
              <circle cx="10.5" cy="8" r="1.7" fill="currentColor" stroke="none" />
              <circle cx="5" cy="11.5" r="1.7" fill="currentColor" stroke="none" />
            </g>
          </svg>
        ) : (
          'View options'
        )}
      </button>
      {prefsMenuOpen ? (
        <div
          className="workflow-list-view-options-popover"
          role="dialog"
          aria-label="Workflow list view options"
          onKeyDown={(event) => {
            if (event.key === 'Escape') {
              event.stopPropagation();
              setPrefsMenuOpen(false);
            }
          }}
        >
          <fieldset className="workflow-list-view-options-group">
            <legend>Density</legend>
            <label className="checkbox">
              <input
                type="radio"
                name="workflow-list-density"
                checked={density === 'comfortable'}
                onChange={() => handleDensityChange('comfortable')}
              />
              Comfortable
            </label>
            <label className="checkbox">
              <input
                type="radio"
                name="workflow-list-density"
                checked={density === 'compact'}
                onChange={() => handleDensityChange('compact')}
              />
              Compact
            </label>
          </fieldset>
          <fieldset className="workflow-list-view-options-group">
            <legend>Columns</legend>
            {TABLE_COLUMNS.filter((column) =>
              (TOGGLEABLE_WORKFLOW_LIST_COLUMNS as readonly string[]).includes(column.field),
            ).map((column) => (
              <label className="checkbox" key={column.field}>
                <input
                  type="checkbox"
                  checked={isColumnVisible(column.field)}
                  onChange={(event) =>
                    handleToggleColumn(
                      column.field as ToggleableWorkflowListColumn,
                      event.target.checked,
                    )
                  }
                />
                {column.label}
              </label>
            ))}
          </fieldset>
          <fieldset className="workflow-list-view-options-group">
            <legend>Live updates</legend>
            <label className="checkbox">
              <input
                type="checkbox"
                checked={liveUpdatesPref}
                onChange={(event) => handleLiveUpdatesChange(event.target.checked)}
              />
              Poll for live updates
            </label>
          </fieldset>
          <div className="workflow-list-view-options-actions">
            <button
              type="button"
              className="secondary"
              onClick={handleResetPreferences}
              aria-label="Reset dashboard preferences to defaults"
            >
              Reset to defaults
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );

  // Desktop entry point to the full advanced-filters drawer. When the results
  // header row is dropped on desktop, the per-column buttons only cover
  // TABLE_COLUMN_FILTER_FIELDS, so this keeps every drawer-only field (ID, Skill,
  // Scheduled, Created, Finished, and any column hidden via View options)
  // reachable and surfaces the active-filter count so those filters can still be
  // reviewed and cleared without hand-editing the URL.
  const renderAdvancedFiltersTrigger = () => (
    <button
      type="button"
      className={`workflow-list-advanced-filters-trigger${hasActiveFilters ? ' is-active' : ''}`}
      ref={drawerToggleRef}
      aria-haspopup="dialog"
      aria-expanded={drawerOpen}
      aria-label={
        hasActiveFilters
          ? `Advanced filters. ${activeFilters.length} active.`
          : 'Advanced filters'
      }
      title="Advanced filters"
      onClick={(event) => openDrawer(null, event.currentTarget)}
    >
      <svg
        aria-hidden="true"
        className="workflow-list-advanced-filters-icon"
        viewBox="0 0 16 16"
        focusable="false"
      >
        <path d="M2 3h12l-4.8 5.4v3.4l-2.4 1.2V8.4L2 3Z" />
      </svg>
      {hasActiveFilters ? (
        <span className="workflow-list-advanced-filters-count" aria-hidden="true">
          {activeFilters.length}
        </span>
      ) : null}
    </button>
  );

  // Desktop promotes the per-column filter buttons and an Actions-header "View
  // options" icon, dropping the results header row. The icon lives inside the
  // rendered table header, so it can only host "View options" when the table is
  // actually on screen. The labelled control falls back to the results header
  // otherwise — on mobile, when there is no Actions column, and on the
  // loading/error/empty states where the table (and its column filters) are
  // replaced by a message and the Filters trigger is the only filter affordance.
  const tableHasRows = !isLoading && !isError && sortedItems.length > 0;
  const showViewOptionsIcon = isDesktop && actionsEnabled && tableHasRows;
  const showResultsHeader = !showViewOptionsIcon;

  return (
    <div className="stack">
      {hasWorkflowListNotices ? (
        <section className="workflow-list-notices-deck" aria-label="Workflow list notices">
          {!listEnabled ? (
            <div className="notice error">Temporal workflow list is disabled in server configuration.</div>
          ) : null}
          {ignoredWorkflowScopeState ? (
            <div className="notice warning">
              Workflow scope filters are not available on Workflows. Showing workflow runs only.
            </div>
          ) : null}
          {filterValidationErrors.length > 0 ? (
            <div className="notice error" role="alert">
              {filterValidationErrors.map((message) => (
                <div key={message}>{message}</div>
              ))}
            </div>
          ) : null}
        </section>
      ) : null}

      {drawerOpen ? (
        <div
          className="workflow-list-filter-drawer-overlay"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) closeDrawer();
          }}
        >
          <div
            className="workflow-list-filter-drawer"
            role="dialog"
            aria-modal="true"
            aria-label="Advanced filters"
            ref={drawerRef}
            onKeyDown={(event) => {
              if (event.key === 'Escape') {
                event.stopPropagation();
                closeDrawer();
                return;
              }
              if (event.key === 'Tab') {
                // Trap focus inside the modal drawer so keyboard users cannot
                // Tab/Shift+Tab into the inert background while it is open.
                const root = drawerRef.current;
                if (!root) return;
                const focusable = Array.from(
                  root.querySelectorAll<HTMLElement>(
                    'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
                  ),
                );
                const first = focusable[0];
                const last = focusable[focusable.length - 1];
                if (!first || !last) {
                  event.preventDefault();
                  return;
                }
                const active = document.activeElement as HTMLElement | null;
                if (event.shiftKey) {
                  if (active === first || !root.contains(active)) {
                    event.preventDefault();
                    last.focus();
                  }
                } else if (active === last || !root.contains(active)) {
                  event.preventDefault();
                  first.focus();
                }
                return;
              }
              if (
                event.key === 'Enter' &&
                event.target instanceof HTMLInputElement &&
                !event.defaultPrevented
              ) {
                event.preventDefault();
                applyDraftFilters();
              }
            }}
          >
            <header className="workflow-list-filter-drawer-header">
              <h2 className="workflow-list-filter-drawer-title">Advanced filters</h2>
              <button
                type="button"
                className="secondary workflow-list-filter-drawer-close"
                onClick={() => closeDrawer()}
                aria-label="Close filters"
              >
                <span aria-hidden="true">×</span>
              </button>
            </header>
            <div className="workflow-list-filter-drawer-body">
              {DRAWER_FILTER_FIELDS.map(([field, label]) => (
                <section
                  key={field}
                  className="workflow-list-filter-section"
                  data-filter-section={field}
                  aria-label={`${label} filter`}
                >
                  {renderFilterControl(field)}
                </section>
              ))}
            </div>
            <footer className="workflow-list-filter-drawer-actions workflow-list-filter-actions">
              <button
                type="button"
                className="secondary"
                onClick={resetDraftFilters}
                disabled={!listEnabled || !hasActiveFilters}
                aria-label="Reset filters"
              >
                Reset
              </button>
              <button
                type="button"
                className="secondary"
                onClick={() => closeDrawer()}
                aria-label="Cancel filters"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={applyDraftFilters}
                disabled={!listEnabled}
                aria-label="Apply filters"
              >
                Apply
              </button>
            </footer>
          </div>
        </div>
      ) : null}

      <section
        className="queue-layouts panel--data workflow-list-data-slab"
        aria-label="Workflow list"
      >
        {showResultsHeader ? (
        <header className="workflow-list-results-header">
          <div className="workflow-list-filter-bar">
            <button
              type="button"
              className={`workflow-list-filter-trigger${hasActiveFilters ? ' is-active' : ''}`}
              ref={drawerToggleRef}
              aria-haspopup="dialog"
              aria-expanded={drawerOpen}
              onClick={(event) => openDrawer(null, event.currentTarget)}
            >
              <svg
                aria-hidden="true"
                className="workflow-list-filter-trigger-icon"
                viewBox="0 0 16 16"
                focusable="false"
              >
                <path d="M2 3h12l-4.8 5.4v3.4l-2.4 1.2V8.4L2 3Z" />
              </svg>
              <span>Filters</span>
              {hasActiveFilters ? (
                <span className="workflow-list-filter-trigger-count" aria-hidden="true">
                  {activeFilters.length}
                </span>
              ) : null}
            </button>
            {hasActiveFilters ? (
              <div className="workflow-list-filter-chips" aria-label="Active filters" aria-live="polite">
                {activeFilters.map(({ field, label, value }) => (
                  <span className="workflow-list-filter-chip" key={`${label}:${value}`}>
                    <button
                      type="button"
                      className="workflow-list-filter-chip-open"
                      data-filter-field={field}
                      onClick={(event) => openDrawer(field, event.currentTarget)}
                      aria-label={`${label} filter: ${value}`}
                    >
                      <span>{label}</span>
                      <strong>{value}</strong>
                    </button>
                    <button
                      type="button"
                      className="workflow-list-filter-chip-remove"
                      onClick={() => removeActiveFilter(field)}
                      aria-label={`Remove ${label} filter`}
                    >
                      ×
                    </button>
                  </span>
                ))}
              </div>
            ) : null}
          </div>
          {renderViewOptions('text')}
        </header>
        ) : null}
        {isLoading ? (
          <p className="loading workflow-list-empty-message">Loading workflows...</p>
        ) : isError ? (
          <>
            <div className="notice error workflow-list-empty-message">{(error as Error).message}</div>
            {resultsFooter}
          </>
        ) : sortedItems.length === 0 ? (
          <>
            {!hasPaginationContext ? (
              <p className="small workflow-list-empty-message">No workflows found for the current filters.</p>
            ) : (
              <div className="card small workflow-list-empty-message">No workflows found for the current filters.</div>
            )}
            {resultsFooter}
          </>
        ) : (
          <>
            <div className="queue-table-wrapper" data-layout="table" data-density={density}>
                <table>
                  <colgroup>
                    {visibleColumns.map((column) => (
                      <col key={column.field} className={column.colClassName} />
                    ))}
                    {actionsEnabled ? <col className="queue-table-column-actions" /> : null}
                  </colgroup>
                  <thead>
                    <tr>
                      {visibleColumns.map(({ field, label, sortable }) => {
                        const { ariaSort, ariaLabel, sortHint } = sortAccessibilityProps(field, label);
                        const filterField = TABLE_COLUMN_FILTER_FIELDS[field];
                        const filterValue = filterField ? filterSummary(filterField, filters) : '';
                        const isFilterOpen = filterField === desktopFilterField;
                        return (
                          <th
                            key={field}
                            aria-sort={sortable ? ariaSort : undefined}
                            className="workflow-list-header-cell"
                          >
                            <div className="workflow-list-column-header">
                              {sortable ? (
                                <button
                                  type="button"
                                  className="table-sort-button"
                                  onClick={() => onHeaderClick(field)}
                                  aria-label={ariaLabel}
                                  title={CURRENT_PAGE_SORT_NOTICE}
                                >
                                  {label}
                                  {sortIndicator(field)}
                                  <span className="sr-only">{sortHint}</span>
                                </button>
                              ) : (
                                <span className="workflow-list-static-header">{label}</span>
                              )}
                              {filterField ? (
                                <div
                                  className="workflow-list-column-filter"
                                  ref={isFilterOpen ? desktopFilterRef : null}
                                >
                                  <button
                                    type="button"
                                    className={`workflow-list-column-filter-button${filterValue ? ' is-active' : ''}`}
                                    aria-label={
                                      filterValue
                                        ? `${label} column filter: ${filterValue}`
                                        : `${label} filter. No filter applied.`
                                    }
                                    aria-haspopup="dialog"
                                    aria-expanded={isFilterOpen}
                                    onClick={() => {
                                      setDraftFilters(filters);
                                      setDesktopFilterField((current) => (current === filterField ? null : filterField));
                                    }}
                                  >
                                    <svg
                                      aria-hidden="true"
                                      className="workflow-list-column-filter-icon"
                                      viewBox="0 0 16 16"
                                      focusable="false"
                                    >
                                      <path d="M2 3h12l-4.8 5.4v3.4l-2.4 1.2V8.4L2 3Z" />
                                    </svg>
                                  </button>
                                  {isFilterOpen ? (
                                    <div
                                      className="workflow-list-column-filter-popover"
                                      role="dialog"
                                      aria-label={`${label} filter`}
                                      onKeyDown={(event) => {
                                        if (event.key === 'Escape') {
                                          event.stopPropagation();
                                          closeDesktopFilter();
                                        }
                                        if (
                                          event.key === 'Enter' &&
                                          event.target instanceof HTMLInputElement &&
                                          !event.defaultPrevented
                                        ) {
                                          event.preventDefault();
                                          applyDraftFilters();
                                        }
                                      }}
                                    >
                                      <div className="workflow-list-column-filter-title">{label} filter</div>
                                      {renderFilterControl(filterField)}
                                      <div className="workflow-list-filter-actions">
                                        <button
                                          type="button"
                                          className="secondary"
                                          onClick={() => removeActiveFilter(filterField)}
                                          disabled={!listEnabled || !filterValue}
                                          aria-label={`Reset ${label} filter`}
                                        >
                                          Reset
                                        </button>
                                        <button
                                          type="button"
                                          className="secondary"
                                          onClick={closeDesktopFilter}
                                          aria-label={`Cancel ${label} filter`}
                                        >
                                          Cancel
                                        </button>
                                        <button
                                          type="button"
                                          onClick={applyDraftFilters}
                                          disabled={!listEnabled}
                                          aria-label={`Apply ${label} filter`}
                                        >
                                          Apply
                                        </button>
                                      </div>
                                    </div>
                                  ) : null}
                                </div>
                              ) : null}
                            </div>
                          </th>
                        );
                      })}
                      {actionsEnabled ? (
                        <th scope="col" className="queue-table-actions-header">
                          <div className="queue-table-actions-header-inner">
                            <span>Actions</span>
                            {showViewOptionsIcon ? renderAdvancedFiltersTrigger() : null}
                            {showViewOptionsIcon ? renderViewOptions('icon') : null}
                          </div>
                        </th>
                      ) : null}
                    </tr>
                  </thead>
                  <tbody>
                    {sortedItems.map((row) => {
                      const actionItems = nextActionItems(row);
                      const updatedAt = rowUpdatedAt(row);
                      return (
                        <tr key={rowWorkflowId(row)}>
                          <td className="queue-table-cell-workflow">
                            <a
                              href={`/workflows/${encodeURIComponent(rowWorkflowId(row))}?source=temporal`}
                              className="workflow-list-row-title"
                            >
                              {row.title}
                            </a>
                            <code className="workflow-list-row-id">{rowWorkflowId(row)}</code>
                          </td>
                          {isColumnVisible('status') ? (
                            <td className="queue-table-cell-status">
                              <ExecutionStatusPill status={row.rawState || row.state || row.status} />
                            </td>
                          ) : null}
                          {isColumnVisible('nextAction') ? (
                            <td className="queue-table-cell-next-action">
                              {actionItems.length > 0 ? (
                                actionItems.map((item) => (
                                  <div key={item} className="workflow-list-next-action-item small">
                                    {item}
                                  </div>
                                ))
                              ) : (
                                <span className="workflow-list-next-action-empty">—</span>
                              )}
                            </td>
                          ) : null}
                          {isColumnVisible('repository') ? (
                            <td className="queue-table-cell-compact">{row.repository || '—'}</td>
                          ) : null}
                          {isColumnVisible('targetRuntime') ? (
                            <td className="queue-table-cell-compact">{formatRuntimeLabel(row.targetRuntime)}</td>
                          ) : null}
                          {isColumnVisible('updatedAt') ? (
                            <td className="queue-table-cell-date" title={formatWhen(updatedAt)}>
                              {formatRelative(updatedAt)}
                            </td>
                          ) : null}
                          {actionsEnabled ? (
                            <td className="queue-table-cell-actions">
                              <WorkflowRowActionsMenu
                                workflowId={rowWorkflowId(row)}
                                apiBase={payload.apiBase}
                                actionsEnabled={actionsEnabled}
                                taskEditingEnabled={taskEditingEnabled}
                              />
                            </td>
                          ) : null}
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <ul className="queue-card-list" data-layout="card" role="list">
                {sortedItems.map((row) => {
                      const actionItems = nextActionItems(row);
                      const updatedAt = rowUpdatedAt(row);
                      return (
                  <li key={rowWorkflowId(row)} className="queue-card">
                    <div className="queue-card-header">
                      <div>
                        <a
                          href={`/workflows/${encodeURIComponent(rowWorkflowId(row))}?source=temporal`}
                          className="queue-card-title"
                        >
                          {row.title}
                        </a>
                      </div>
                      <div className="queue-card-status">
                        <ExecutionStatusPill status={row.rawState || row.state || row.status} />
                      </div>
                    </div>
                    {actionItems.length > 0 ? (
                      <div className="queue-card-next-action">
                        <span className="queue-card-next-action-label small">Next action</span>
                        {actionItems.map((item) => (
                          <p key={item} className="queue-card-next-action-item">
                            {item}
                          </p>
                        ))}
                      </div>
                    ) : null}
                    <dl className="queue-card-fields">
                      <div>
                        <dt>ID</dt>
                        <dd>
                          <code>{rowWorkflowId(row)}</code>
                        </dd>
                      </div>
                      <div>
                        <dt>Runtime</dt>
                        <dd>{formatRuntimeLabel(row.targetRuntime)}</dd>
                      </div>
                      <div>
                        <dt>Repository</dt>
                        <dd>{row.repository || '—'}</dd>
                      </div>
                      <div>
                        <dt>Updated</dt>
                        <dd title={formatWhen(updatedAt)}>{formatRelative(updatedAt)}</dd>
                      </div>
                    </dl>
                    <div className="queue-card-actions">
                      <a
                        href={`/workflows/${encodeURIComponent(rowWorkflowId(row))}?source=temporal`}
                        className="button secondary queue-card-details-action"
                        role="button"
                      >
                        View details
                      </a>
                      {actionsEnabled ? (
                        <WorkflowRowActionsMenu
                          workflowId={rowWorkflowId(row)}
                          apiBase={payload.apiBase}
                          actionsEnabled={actionsEnabled}
                          taskEditingEnabled={taskEditingEnabled}
                        />
                      ) : null}
                    </div>
                  </li>
                      );
                    })}
              </ul>
              {resultsFooter}
            </>
          )}
        </section>
    </div>
  );
}
export default WorkflowListPage;
