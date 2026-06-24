import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
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
  temporalScheduleId: z.string().nullable().optional(),
  createdAt: z.string().optional(),
  updatedAt: z.string().optional(),
  activity: z.array(z.record(z.string(), z.unknown())).optional(),
}).passthrough();

const SchedulesResponseSchema = z.object({
  items: z.array(ScheduleSchema),
});

const ScheduleRunsResponseSchema = z.object({
  items: z.array(z.object({
    id: z.string(),
    definitionId: z.string().optional(),
    scheduledFor: z.string(),
    trigger: z.string().optional(),
    outcome: z.string(),
    dispatchAttempts: z.number().optional(),
    dispatchAfter: z.string().nullable().optional(),
    temporalWorkflowId: z.string().nullable().optional(),
    temporalRunId: z.string().nullable().optional(),
    message: z.string().nullable().optional(),
    createdAt: z.string().optional(),
    updatedAt: z.string().optional(),
  }).passthrough()),
});

type Schedule = z.infer<typeof ScheduleSchema>;
type ScheduleRun = z.infer<typeof ScheduleRunsResponseSchema>['items'][number];
type ScheduleDetailTab = 'overview' | 'runs' | 'configuration' | 'activity';

const SchedulesBootDataSchema = z
  .object({
    dashboardConfig: z
      .object({
        sources: z
          .object({
            schedules: z
              .object({
                list: z.string().optional(),
                detail: z.string().optional(),
                runNow: z.string().optional(),
                runs: z.string().optional(),
              })
              .partial()
              .optional(),
          })
          .partial()
          .optional(),
      })
      .partial()
      .optional(),
    sources: z
      .object({
        schedules: z
          .object({
            list: z.string().optional(),
            detail: z.string().optional(),
            runNow: z.string().optional(),
            runs: z.string().optional(),
          })
          .partial()
          .optional(),
      })
      .partial()
      .optional(),
  })
  .passthrough();

function scheduleListEndpoint(payload: BootPayload): string {
  const schedules = scheduleSources(payload);
  return schedules?.list || `${payload.apiBase || '/api'}/recurring-tasks?scope=personal`;
}

function scheduleSources(payload: BootPayload) {
  const parsed = SchedulesBootDataSchema.safeParse(payload.initialData || {});
  return parsed.success
    ? parsed.data.dashboardConfig?.sources?.schedules || parsed.data.sources?.schedules
    : undefined;
}

function applyScheduleEndpointTemplate(template: string, definitionId: string): string {
  const encoded = encodeURIComponent(definitionId);
  return template
    .replaceAll('{definitionId}', encoded)
    .replaceAll('{definition_id}', encoded)
    .replaceAll('{id}', encoded);
}

function scheduleDetailEndpoint(payload: BootPayload, definitionId: string): string {
  const template = scheduleSources(payload)?.detail || `${payload.apiBase || '/api'}/recurring-workflows/{id}`;
  return applyScheduleEndpointTemplate(template, definitionId);
}

function scheduleRunNowEndpoint(payload: BootPayload, definitionId: string): string {
  const template = scheduleSources(payload)?.runNow || `${payload.apiBase || '/api'}/recurring-workflows/{id}/run`;
  return applyScheduleEndpointTemplate(template, definitionId);
}

function scheduleRunsEndpoint(payload: BootPayload, definitionId: string): string {
  const template = scheduleSources(payload)?.runs || `${payload.apiBase || '/api'}/recurring-workflows/{id}/runs?limit=200`;
  return applyScheduleEndpointTemplate(template, definitionId);
}

function scheduleIdFromLocation(): string | null {
  const match = window.location.pathname.match(/^\/schedules\/([^/]+)$/);
  return match?.[1] ? decodeURIComponent(match[1]) : null;
}

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

function displayUnknown(value: unknown): string {
  if (typeof value === 'string') {
    return displayValue(value);
  }
  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value);
  }
  return '-';
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

function targetPayload(schedule: Schedule): Record<string, unknown> {
  const job = schedule.target?.job;
  if (!job || typeof job !== 'object' || !('payload' in job)) {
    return {};
  }
  const payload = (job as { payload?: unknown }).payload;
  return payload && typeof payload === 'object' ? payload as Record<string, unknown> : {};
}

function targetRepository(schedule: Schedule): string {
  return displayUnknown(targetPayload(schedule).repository);
}

function targetFact(schedule: Schedule, key: string): string {
  return displayUnknown(targetPayload(schedule)[key] ?? schedule.target?.[key]);
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

function scheduleDetailTabFromHash(hasActivity: boolean): ScheduleDetailTab {
  const raw = window.location.hash.replace(/^#/, '').toLowerCase();
  if (raw === 'runs' || raw === 'configuration' || (raw === 'activity' && hasActivity)) {
    return raw;
  }
  return 'overview';
}

function scheduleStatusClass(schedule: Schedule): string {
  const state = scheduleState(schedule);
  if (state === 'active') {
    return 'status status-running';
  }
  if (state === 'attention') {
    return 'status status-failed';
  }
  return 'status status-waiting';
}

function DetailFact({ label, value }: { label: string; value: string }) {
  return (
    <div className="schedule-detail-fact">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function ScheduleDetailNav({
  current,
  hasActivity,
  onSelect,
}: {
  current: ScheduleDetailTab;
  hasActivity: boolean;
  onSelect: (tab: ScheduleDetailTab) => void;
}) {
  const tabs: Array<{ id: ScheduleDetailTab; label: string }> = [
    { id: 'overview', label: 'Overview' },
    { id: 'runs', label: 'Runs' },
    { id: 'configuration', label: 'Configuration' },
    ...(hasActivity ? [{ id: 'activity' as const, label: 'Activity' }] : []),
  ];
  return (
    <nav className="td-subroute-nav" aria-label="Schedule detail sections">
      {tabs.map((tab) => (
        <a
          key={tab.id}
          href={`#${tab.id}`}
          aria-current={current === tab.id ? 'page' : undefined}
          onClick={(event) => {
            event.preventDefault();
            window.history.replaceState({}, '', `#${tab.id}`);
            onSelect(tab.id);
          }}
        >
          {tab.label}
        </a>
      ))}
    </nav>
  );
}

function ScheduleSummaryGrid({ schedule }: { schedule: Schedule }) {
  return (
    <section className="schedules-summary-grid" aria-label="Schedule summary">
      <div className="schedules-summary-item">
        <span>Next run</span>
        <strong>{formatWhen(schedule.nextRunAt)}</strong>
      </div>
      <div className="schedules-summary-item">
        <span>Cadence</span>
        <strong>{schedule.cron}</strong>
      </div>
      <div className="schedules-summary-item">
        <span>Last run</span>
        <strong>{formatWhen(schedule.lastScheduledFor)}</strong>
      </div>
      <div className="schedules-summary-item">
        <span>Dispatch result</span>
        <strong>{schedule.lastDispatchStatus ? titleCaseLabel(formatStatusLabel(schedule.lastDispatchStatus)) : '-'}</strong>
      </div>
    </section>
  );
}

function ScheduleFactsRail({ schedule }: { schedule: Schedule }) {
  return (
    <aside className="td-facts-region schedule-detail-facts" aria-label="Schedule facts">
      <DetailFact label="Schedule definition ID" value={schedule.id} />
      <DetailFact label="Temporal Schedule ID" value={displayValue(schedule.temporalScheduleId)} />
      <DetailFact label="Schedule state" value={stateLabel(schedule)} />
      <DetailFact label="Target workflow type" value={targetKind(schedule)} />
      <DetailFact label="Runtime" value={targetFact(schedule, 'runtime')} />
      <DetailFact label="Model" value={targetFact(schedule, 'model')} />
      <DetailFact label="Repository" value={targetRepository(schedule)} />
      <DetailFact label="Publish mode" value={targetFact(schedule, 'publishMode')} />
      <DetailFact label="Updated time" value={formatWhen(schedule.updatedAt)} />
    </aside>
  );
}

function ScheduleRunsTable({ runs, isLoading, isError, error }: {
  runs: ScheduleRun[];
  isLoading: boolean;
  isError: boolean;
  error: unknown;
}) {
  if (isLoading) {
    return <p className="loading">Loading schedule runs...</p>;
  }
  if (isError) {
    return <div className="notice error" role="alert">{(error as Error).message}</div>;
  }
  return (
    <DataTable
      data={runs}
      columns={[
        { key: 'scheduledFor', header: 'Scheduled for', render: (run) => formatWhen(run.scheduledFor) },
        { key: 'outcome', header: 'Dispatch result', render: (run) => titleCaseLabel(formatStatusLabel(run.outcome)) },
        {
          key: 'temporalWorkflowId',
          header: 'Workflow',
          render: (run) => run.temporalWorkflowId ? (
            <a href={`/workflows/${encodeURIComponent(run.temporalWorkflowId)}?source=temporal`}>
              {run.temporalWorkflowId}
            </a>
          ) : '-',
        },
        { key: 'trigger', header: 'Trigger', render: (run) => run.trigger ? titleCaseLabel(formatStatusLabel(run.trigger)) : '-' },
        { key: 'updatedAt', header: 'Updated time', render: (run) => formatWhen(run.updatedAt) },
      ]}
      emptyMessage="No runs have been dispatched for this schedule yet."
      getRowKey={(run) => run.id}
      ariaLabel="Schedule runs"
    />
  );
}

function ScheduleTabPanel({
  schedule,
  runs,
  runsLoading,
  runsError,
  runsQueryError,
  tab,
}: {
  schedule: Schedule;
  runs: ScheduleRun[];
  runsLoading: boolean;
  runsError: boolean;
  runsQueryError: unknown;
  tab: ScheduleDetailTab;
}) {
  if (tab === 'runs') {
    return <ScheduleRunsTable runs={runs} isLoading={runsLoading} isError={runsError} error={runsQueryError} />;
  }
  if (tab === 'configuration') {
    return (
      <div className="schedule-detail-config-grid">
        <DetailFact label="Schedule name" value={schedule.name} />
        <DetailFact label="Schedule state" value={stateLabel(schedule)} />
        <DetailFact label="Cron" value={schedule.cron} />
        <DetailFact label="Timezone" value={displayValue(schedule.timezone)} />
        <DetailFact label="Scope" value={[schedule.scopeType, schedule.scopeRef].filter(Boolean).join(' / ') || '-'} />
        <DetailFact label="Policy" value={policySummary(schedule)} />
        <DetailFact label="Target facts" value={[targetKind(schedule), targetRepository(schedule)].filter((value) => value !== '-').join(' / ') || '-'} />
        <DetailFact label="Updated time" value={formatWhen(schedule.updatedAt)} />
      </div>
    );
  }
  if (tab === 'activity') {
    return (
      <div className="schedule-detail-activity-list">
        {(schedule.activity || []).map((event, index) => (
          <div className="schedule-detail-activity-item" key={`${displayUnknown(event.id)}-${index}`}>
            <strong>{displayUnknown(event.title ?? event.type ?? event.status)}</strong>
            <span>{formatWhen(displayUnknown(event.updatedAt ?? event.createdAt ?? event.time))}</span>
            <p>{displayUnknown(event.message ?? event.summary)}</p>
          </div>
        ))}
      </div>
    );
  }
  return (
    <div className="schedule-detail-overview">
      <DetailFact label="Schedule name" value={schedule.name} />
      <DetailFact label="Schedule state" value={stateLabel(schedule)} />
      <DetailFact label="Schedule definition ID" value={schedule.id} />
      <DetailFact label="Temporal Schedule ID" value={displayValue(schedule.temporalScheduleId)} />
      <DetailFact label="Target facts" value={[targetKind(schedule), targetRepository(schedule), targetFact(schedule, 'runtime'), targetFact(schedule, 'model')].filter((value) => value !== '-').join(' / ') || '-'} />
      <DetailFact label="Next run" value={formatWhen(schedule.nextRunAt)} />
      <DetailFact label="Last run" value={formatWhen(schedule.lastScheduledFor)} />
      <DetailFact label="Dispatch result" value={schedule.lastDispatchStatus ? titleCaseLabel(formatStatusLabel(schedule.lastDispatchStatus)) : '-'} />
      <DetailFact label="Updated time" value={formatWhen(schedule.updatedAt)} />
      {schedule.lastDispatchError ? <div className="notice error">{schedule.lastDispatchError}</div> : null}
    </div>
  );
}

function ScheduleDetailPage({ payload, definitionId }: { payload: BootPayload; definitionId: string }) {
  const queryClient = useQueryClient();
  const detailEndpoint = useMemo(() => scheduleDetailEndpoint(payload, definitionId), [payload, definitionId]);
  const runsEndpoint = useMemo(() => scheduleRunsEndpoint(payload, definitionId), [payload, definitionId]);
  const runNowEndpoint = useMemo(() => scheduleRunNowEndpoint(payload, definitionId), [payload, definitionId]);
  const detailQuery = useQuery({
    queryKey: ['schedule-detail', detailEndpoint],
    queryFn: async () => {
      const response = await fetch(detailEndpoint, { credentials: 'include' });
      if (!response.ok) {
        throw new Error(`Failed to fetch schedule: ${response.statusText}`);
      }
      return ScheduleSchema.parse(await response.json());
    },
  });
  const runsQuery = useQuery({
    queryKey: ['schedule-runs', runsEndpoint],
    queryFn: async () => {
      const response = await fetch(runsEndpoint, { credentials: 'include' });
      if (!response.ok) {
        throw new Error(`Failed to fetch schedule runs: ${response.statusText}`);
      }
      return ScheduleRunsResponseSchema.parse(await response.json());
    },
  });
  const hasActivity = Boolean(detailQuery.data?.activity?.length);
  const [tab, setTab] = useState<ScheduleDetailTab>(() => scheduleDetailTabFromHash(hasActivity));
  const runNow = useMutation({
    mutationFn: async () => {
      const response = await fetch(runNowEndpoint, { method: 'POST', credentials: 'include' });
      if (!response.ok) {
        throw new Error(`Run now failed: ${response.statusText}`);
      }
      return response.json();
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['schedule-detail', detailEndpoint] });
      await queryClient.invalidateQueries({ queryKey: ['schedule-runs', runsEndpoint] });
    },
  });

  const schedule = detailQuery.data;
  const currentTab = tab === 'activity' && !hasActivity ? 'overview' : tab;
  return (
    <div className="stack workflow-detail-page schedule-detail-page">
      <div className="toolbar">
        <div>
          <h2 className="page-title">Schedule Detail</h2>
          <div className="toolbar-identity-row">
            <p className="page-meta">Schedules / {definitionId}</p>
            {schedule ? <span className={scheduleStatusClass(schedule)}>{stateLabel(schedule)}</span> : null}
          </div>
        </div>
        <div className="toolbar-controls">
          <a href="/schedules" className="secondary">Back to schedules</a>
          <a href={`/workflows/new?scheduleMode=recurring&scheduleId=${encodeURIComponent(definitionId)}`} className="secondary">Edit schedule</a>
          <button type="button" onClick={() => runNow.mutate()} disabled={runNow.isPending || !schedule}>
            {runNow.isPending ? 'Running now' : 'Run now'}
          </button>
        </div>
      </div>

      <ScheduleDetailNav current={currentTab} hasActivity={hasActivity} onSelect={setTab} />
      {runNow.isError ? <div className="notice error" role="alert">{(runNow.error as Error).message}</div> : null}

      {detailQuery.isLoading ? (
        <p className="loading">Loading schedule...</p>
      ) : detailQuery.isError ? (
        <div className="notice error" role="alert">{(detailQuery.error as Error).message}</div>
      ) : schedule ? (
        <>
          <div className="td-hero">
            <div className="td-hero-body">
              <div className="td-hero-headline">
                <h3 className="td-title-text">{schedule.name}</h3>
                <p className="meta-inline">
                  Schedule definition
                  <span className="dot">·</span>
                  {schedule.id}
                  {schedule.temporalScheduleId ? (
                    <>
                      <span className="dot">·</span>
                      {schedule.temporalScheduleId}
                    </>
                  ) : null}
                </p>
                {schedule.description ? <p className="page-meta">{schedule.description}</p> : null}
              </div>
            </div>
          </div>
          <ScheduleSummaryGrid schedule={schedule} />
          <div className="schedule-detail-layout">
            <section className="td-evidence-region schedule-detail-main" aria-label="Schedule detail panel">
              <ScheduleTabPanel
                schedule={schedule}
                runs={runsQuery.data?.items || []}
                runsLoading={runsQuery.isLoading}
                runsError={runsQuery.isError}
                runsQueryError={runsQuery.error}
                tab={currentTab}
              />
            </section>
            <ScheduleFactsRail schedule={schedule} />
          </div>
        </>
      ) : (
        <p>No schedule details.</p>
      )}
    </div>
  );
}

function ScheduleListPage({ payload }: { payload: BootPayload }) {
  const listEndpoint = useMemo(() => scheduleListEndpoint(payload), [payload]);
  const { data, isLoading, isError, error, isFetching, refetch } = useQuery({
    queryKey: ['schedules', listEndpoint],
    queryFn: async () => {
      const response = await fetch(listEndpoint, { credentials: 'include' });
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

export function SchedulesPage({ payload }: { payload: BootPayload }) {
  const definitionId = scheduleIdFromLocation();
  return definitionId ? (
    <ScheduleDetailPage payload={payload} definitionId={definitionId} />
  ) : (
    <ScheduleListPage payload={payload} />
  );
}
export default SchedulesPage;
