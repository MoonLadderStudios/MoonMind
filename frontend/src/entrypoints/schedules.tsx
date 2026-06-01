import { useQuery } from '@tanstack/react-query';
import { useMemo } from 'react';
import { DataTable } from '../components/tables/DataTable';

import { z } from 'zod';
import { BootPayload } from '../boot/parseBootPayload';
import { formatStatusLabel } from '../utils/formatters';

const ScheduleSchema = z.object({
  id: z.string(),
  name: z.string(),
  description: z.string().nullable().optional(),
  enabled: z.boolean(),
  scheduleType: z.string().optional(),
  cron: z.string(),
  timezone: z.string(),
  lastDispatchStatus: z.string().nullable().optional(),
  lastDispatchError: z.string().nullable().optional(),
  nextRunAt: z.string().nullable().optional(),
  lastScheduledFor: z.string().nullable().optional(),
  scopeType: z.string().optional(),
  scopeRef: z.string().nullable().optional(),
  target: z.record(z.string(), z.unknown()).optional(),
  policy: z.record(z.string(), z.unknown()).optional(),
  updatedAt: z.string().optional(),
}).passthrough();

const SchedulesResponseSchema = z.object({
  items: z.array(ScheduleSchema),
});

type Schedule = z.infer<typeof ScheduleSchema>;

function formatWhen(value: string | null | undefined): string {
  const raw = String(value || '').trim();
  if (!raw) {
    return '-';
  }
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) {
    return raw;
  }
  return date.toLocaleString();
}

function compactId(id: string): string {
  return id.length > 12 ? `${id.slice(0, 8)}...${id.slice(-4)}` : id;
}

function displayValue(value: string | null | undefined): string {
  const normalized = String(value || '').trim();
  return normalized || '-';
}

function titleCaseLabel(value: string): string {
  return value.replace(/\b[a-z]/g, (match) => match.toUpperCase());
}

function scheduleState(schedule: Schedule): 'active' | 'paused' | 'attention' {
  if (!schedule.enabled) {
    return 'paused';
  }
  const status = String(schedule.lastDispatchStatus || '').toLowerCase();
  return status.includes('error') || status.includes('failed') ? 'attention' : 'active';
}

function stateLabel(schedule: Schedule): string {
  const state = scheduleState(schedule);
  if (state === 'attention') {
    return 'Needs attention';
  }
  return state === 'active' ? 'Active' : 'Paused';
}

function targetKind(schedule: Schedule): string {
  const raw = schedule.target?.kind;
  return typeof raw === 'string' && raw.trim() ? titleCaseLabel(formatStatusLabel(raw)) : 'Queue task';
}

function targetRepository(schedule: Schedule): string {
  const job = schedule.target?.job;
  if (!job || typeof job !== 'object' || !('payload' in job)) {
    return '-';
  }
  const payload = (job as { payload?: unknown }).payload;
  if (!payload || typeof payload !== 'object' || !('repository' in payload)) {
    return '-';
  }
  const repository = (payload as { repository?: unknown }).repository;
  return typeof repository === 'string' && repository.trim() ? repository : '-';
}

function policySummary(schedule: Schedule): string {
  const overlap = schedule.policy?.overlap;
  const catchup = schedule.policy?.catchup;
  const overlapMode = overlap && typeof overlap === 'object' && 'mode' in overlap
    ? String((overlap as { mode?: unknown }).mode || '').trim()
    : '';
  const catchupMode = catchup && typeof catchup === 'object' && 'mode' in catchup
    ? String((catchup as { mode?: unknown }).mode || '').trim()
    : '';
  return [overlapMode, catchupMode].filter(Boolean).map((value) => titleCaseLabel(formatStatusLabel(value))).join(' / ') || '-';
}

function isDueSoon(schedule: Schedule, now: number): boolean {
  if (!schedule.enabled || !schedule.nextRunAt) {
    return false;
  }
  const nextRun = new Date(schedule.nextRunAt).getTime();
  return Number.isFinite(nextRun) && nextRun >= now && nextRun <= now + 24 * 60 * 60 * 1000;
}

export function SchedulesPage({ payload }: { payload: BootPayload }) {
  const { data, isLoading, isError, error, isFetching, refetch } = useQuery({
    queryKey: ['schedules'],
    queryFn: async () => {
      const response = await fetch(`${payload.apiBase}/recurring-tasks?scope=personal`);
      if (!response.ok) {
        throw new Error(`Failed to fetch: ${response.statusText}`);
      }
      return SchedulesResponseSchema.parse(await response.json());
    },
  });

  const schedules = data?.items || [];
  const stats = useMemo(() => {
    const now = Date.now();
    const active = schedules.filter((schedule) => schedule.enabled).length;
    const attention = schedules.filter((schedule) => scheduleState(schedule) === 'attention').length;
    const dueSoon = schedules.filter((schedule) => isDueSoon(schedule, now)).length;
    return { active, attention, dueSoon, total: schedules.length };
  }, [schedules]);

  return (
    <div className="schedules-page stack">
      <header className="toolbar schedules-toolbar">
        <div>
          <h2 className="page-title">Recurring Schedules</h2>
          <p className="page-meta">Managed recurring schedules for queue and manifest targets.</p>
        </div>
        <div className="toolbar-controls">
          <button type="button" className="secondary" onClick={() => void refetch()} disabled={isFetching}>
            {isFetching ? 'Refreshing' : 'Refresh'}
          </button>
          <a href="/workflows/new?scheduleMode=recurring" className="button">
            Create from workflow page
          </a>
        </div>
      </header>

      <section className="schedules-summary-grid" aria-label="Schedule summary">
        <div className="schedules-summary-item">
          <span>Total</span>
          <strong>{stats.total}</strong>
        </div>
        <div className="schedules-summary-item">
          <span>Active</span>
          <strong>{stats.active}</strong>
        </div>
        <div className="schedules-summary-item">
          <span>Next 24h</span>
          <strong>{stats.dueSoon}</strong>
        </div>
        <div className="schedules-summary-item">
          <span>Attention</span>
          <strong>{stats.attention}</strong>
        </div>
      </section>

      <section className="panel--data schedules-table-panel" aria-label="Recurring schedule list">
        {isLoading ? (
          <p className="loading">Loading recurring schedules...</p>
        ) : isError ? (
          <div className="schedules-error" role="alert">{(error as Error).message}</div>
        ) : (
          <DataTable
            data={schedules}
            columns={[
              {
                key: 'name',
                header: 'Schedule',
                render: (item) => (
                  <div className="schedules-primary-cell">
                    <a href={`/schedules/${encodeURIComponent(item.id)}`}>{item.name}</a>
                    <span title={item.id}>{compactId(item.id)}</span>
                  </div>
                ),
              },
              {
                key: 'enabled',
                header: 'State',
                render: (item) => (
                  <span className={`schedules-state schedules-state--${scheduleState(item)}`}>
                    {stateLabel(item)}
                  </span>
                ),
              },
              {
                key: 'target',
                header: 'Target',
                render: (item) => (
                  <div className="schedules-secondary-cell">
                    <strong>{targetKind(item)}</strong>
                    <span>{targetRepository(item)}</span>
                  </div>
                ),
              },
              {
                key: 'cron',
                header: 'Cadence',
                render: (item) => (
                  <div className="schedules-secondary-cell">
                    <code>{item.cron}</code>
                    <span>{displayValue(item.timezone)}</span>
                  </div>
                ),
              },
              {
                key: 'nextRunAt',
                header: 'Next Run',
                render: (item) => formatWhen(item.nextRunAt),
              },
              {
                key: 'lastScheduledFor',
                header: 'Last Scheduled',
                render: (item) => formatWhen(item.lastScheduledFor),
              },
              {
                key: 'lastDispatchStatus',
                header: 'Dispatch',
                render: (item) => (
                  <div className="schedules-secondary-cell">
                    <strong>{item.lastDispatchStatus ? titleCaseLabel(formatStatusLabel(item.lastDispatchStatus)) : '-'}</strong>
                    <span>{displayValue(item.lastDispatchError)}</span>
                  </div>
                ),
              },
              {
                key: 'policy',
                header: 'Policy',
                render: (item) => policySummary(item),
              },
            ]}
            emptyMessage="No recurring schedules yet. Create one from the workflow page."
            getRowKey={(item) => item.id}
            ariaLabel="Recurring schedules"
          />
        )}
      </section>
    </div>
  );
}
export default SchedulesPage;
