import { parsePageSize } from '../components/PageSizeSelector';

// The Recurring table's URL query, shared with the dashboard shell so both read
// the same list from a URL. The table rewrites its URL to this canonical form
// after mount; the shell treats that rewrite as the same list.

export type RecurringSortKey = 'updatedAt' | 'name' | 'state' | 'target' | 'repository' | 'cron' | 'timezone' | 'nextRunAt' | 'lastScheduledFor' | 'dispatch';
export type RecurringSortDirection = 'asc' | 'desc';
export type RecurringFilterKey = 'schedule' | 'state' | 'target' | 'repository' | 'cadence' | 'nextRun' | 'lastScheduled' | 'dispatch' | 'updated';

export type RecurringFilters = {
  schedule: string;
  state: string;
  target: string;
  repository: string;
  cadence: string;
  nextRun: string;
  lastScheduled: string;
  dispatch: string;
  updated: string;
};

/** Filter keys in the order the table shows and serializes them. */
export const RECURRING_FILTER_KEYS: readonly RecurringFilterKey[] = [
  'schedule',
  'state',
  'target',
  'repository',
  'cadence',
  'nextRun',
  'lastScheduled',
  'dispatch',
  'updated',
];

export function cleanQueryText(value: string | null): string {
  return String(value || '').trim().slice(0, 160);
}

export function cleanCursor(value: string | null): string {
  return String(value || '').trim().slice(0, 512);
}

export function parseRecurringFilters(params: URLSearchParams): RecurringFilters {
  return {
    schedule: cleanQueryText(params.get('schedule')),
    state: cleanQueryText(params.get('state')),
    target: cleanQueryText(params.get('target')),
    repository: cleanQueryText(params.get('repository')),
    cadence: cleanQueryText(params.get('cadence')),
    nextRun: cleanQueryText(params.get('nextRun')),
    lastScheduled: cleanQueryText(params.get('lastScheduled')),
    dispatch: cleanQueryText(params.get('dispatch')),
    updated: cleanQueryText(params.get('updated')),
  };
}

export function parseRecurringSortKey(raw: string | null): RecurringSortKey {
  const value = String(raw || '').trim();
  return (
    value === 'name'
    || value === 'state'
    || value === 'target'
    || value === 'repository'
    || value === 'cron'
    || value === 'timezone'
    || value === 'nextRunAt'
    || value === 'lastScheduledFor'
    || value === 'dispatch'
    || value === 'updatedAt'
  ) ? value : 'updatedAt';
}

export function parseRecurringSortDirection(raw: string | null): RecurringSortDirection {
  return String(raw || '').trim() === 'asc' ? 'asc' : 'desc';
}

export function activeRecurringFilterEntries(filters: RecurringFilters): Array<[RecurringFilterKey, string]> {
  return RECURRING_FILTER_KEYS
    .map((key) => [key, filters[key].trim()] as [RecurringFilterKey, string])
    .filter(([, value]) => Boolean(value));
}

export type RecurringListUrlState = {
  filters: RecurringFilters;
  pageSize: number;
  cursor: string;
  sort: RecurringSortKey;
  sortDir: RecurringSortDirection;
};

/** The table's shareable URL query; the shell reads it back to open the first visible row. */
export function recurringListUrlQuery({ filters, pageSize, cursor, sort, sortDir }: RecurringListUrlState): string {
  const params = new URLSearchParams();
  params.set('limit', String(pageSize));
  params.set('sort', sort);
  params.set('sortDir', sortDir);
  if (cursor) {
    params.set('cursor', cursor);
  }
  for (const [key, value] of activeRecurringFilterEntries(filters)) {
    params.set(key, value);
  }
  return params.toString();
}

export function recurringListUrlStateFromSearch(params: URLSearchParams): RecurringListUrlState {
  return {
    filters: parseRecurringFilters(params),
    pageSize: parsePageSize(params.get('limit')),
    cursor: cleanCursor(params.get('cursor')),
    sort: parseRecurringSortKey(params.get('sort')),
    sortDir: parseRecurringSortDirection(params.get('sortDir')),
  };
}

/** The canonical list query for a URL search, so equivalent URLs compare equal. */
export function canonicalRecurringListSearch(search: string): string {
  return recurringListUrlQuery(recurringListUrlStateFromSearch(new URLSearchParams(search)));
}
