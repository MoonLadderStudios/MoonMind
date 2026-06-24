import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { DataTable } from '../components/tables/DataTable';

import { z } from 'zod';
import { BootPayload } from '../boot/parseBootPayload';
import { formatStatusLabel } from '../utils/formatters';

const JsonRecordSchema = z.record(z.string(), z.unknown());

const ScheduleSchema = z.object({
  id: z.string(),
  name: z.string(),
  description: z.string().nullable().optional(),
  enabled: z.boolean(),
  scheduleType: z.string().optional(),
  cron: z.string(),
  timezone: z.string(),
  temporalScheduleId: z.string().nullable().optional(),
  lastDispatchStatus: z.string().nullable().optional(),
  lastDispatchError: z.string().nullable().optional(),
  nextRunAt: z.string().nullable().optional(),
  lastScheduledFor: z.string().nullable().optional(),
  scopeType: z.string().optional(),
  scopeRef: z.string().nullable().optional(),
  ownerUserId: z.string().nullable().optional(),
  target: JsonRecordSchema.optional(),
  policy: JsonRecordSchema.optional(),
  version: z.number().optional(),
  createdAt: z.string().optional(),
  updatedAt: z.string().optional(),
}).passthrough();

const ScheduleRunSchema = z.object({
  id: z.string(),
  definitionId: z.string(),
  scheduledFor: z.string(),
  trigger: z.string(),
  outcome: z.string(),
  dispatchAttempts: z.number(),
  dispatchAfter: z.string().nullable().optional(),
  temporalWorkflowId: z.string().nullable().optional(),
  temporalRunId: z.string().nullable().optional(),
  message: z.string().nullable().optional(),
  createdAt: z.string().optional(),
  updatedAt: z.string().optional(),
}).passthrough();

const SchedulesResponseSchema = z.object({
  items: z.array(ScheduleSchema),
});

const ScheduleRunsResponseSchema = z.object({
  items: z.array(ScheduleRunSchema),
});

type Schedule = z.infer<typeof ScheduleSchema>;
type ScheduleRun = z.infer<typeof ScheduleRunSchema>;

const ScheduleSourcesSchema = z.object({
  list: z.string().optional(),
  create: z.string().optional(),
  detail: z.string().optional(),
  update: z.string().optional(),
  runNow: z.string().optional(),
  runs: z.string().optional(),
}).partial();

const SchedulesBootDataSchema = z
  .object({
    initialPath: z.string().optional(),
    dashboardConfig: z
      .object({
        sources: z
          .object({
            schedules: ScheduleSourcesSchema.optional(),
          })
          .partial()
          .optional(),
      })
      .partial()
      .optional(),
    sources: z
      .object({
        schedules: ScheduleSourcesSchema.optional(),
      })
      .partial()
      .optional(),
  })
  .passthrough();

type ScheduleSources = z.infer<typeof ScheduleSourcesSchema>;

class HttpStatusError extends Error {
  status: number;

  constructor(status: number, statusText: string) {
    super(`Failed to fetch: ${statusText || status}`);
    this.status = status;
  }
}

function scheduleSources(payload: BootPayload): ScheduleSources {
  const parsed = SchedulesBootDataSchema.safeParse(payload.initialData || {});
  if (!parsed.success) {
    return {};
  }
  return parsed.data.dashboardConfig?.sources?.schedules || parsed.data.sources?.schedules || {};
}

function scheduleListEndpoint(payload: BootPayload): string {
  const sources = scheduleSources(payload);
  return sources.list || `${payload.apiBase || '/api'}/recurring-workflows?scope=personal`;
}

function definitionIdFromPath(payload: BootPayload): string | null {
  const parsed = SchedulesBootDataSchema.safeParse(payload.initialData || {});
  const bootPath = parsed.success ? parsed.data.initialPath : undefined;
  const path = bootPath || window.location.pathname;
  const match = path.match(/^\/schedules\/([^/?#]+)/);
  const rawDefinitionId = match?.[1];
  if (!rawDefinitionId || rawDefinitionId === 'new') {
    return null;
  }
  try {
    return decodeURIComponent(rawDefinitionId);
  } catch {
    return rawDefinitionId;
  }
}

function endpointFromTemplate(template: string | undefined, definitionId: string, fallback: string): string {
  const source = template || fallback;
  const encoded = encodeURIComponent(definitionId);
  return source.replaceAll('{definitionId}', encoded).replaceAll('{id}', encoded);
}

async function fetchJson<T>(endpoint: string, schema: z.ZodType<T>): Promise<T> {
  const response = await fetch(endpoint, { credentials: 'include' });
  if (!response.ok) {
    throw new HttpStatusError(response.status, response.statusText);
  }
  return schema.parse(await response.json());
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

function displayValue(value: string | number | boolean | null | undefined): string {
  const normalized = String(value ?? '').trim();
  return normalized || '-';
}

function titleCaseLabel(value: string): string {
  return value.replace(/\b[a-z]/g, (match) => match.toUpperCase());
}

function normalizedLabel(value: string | null | undefined): string {
  return value ? titleCaseLabel(formatStatusLabel(value)) : '-';
}

function scheduleState(schedule: Schedule): 'active' | 'paused' | 'attention' {
  if (!schedule.enabled) {
    return 'paused';
  }
  return scheduleHasDispatchAttention(schedule) ? 'attention' : 'active';
}

function scheduleHasDispatchAttention(schedule: Schedule): boolean {
  const status = String(schedule.lastDispatchStatus || '').toLowerCase();
  return Boolean(schedule.lastDispatchError) || status.includes('error') || status.includes('failed');
}

function stateLabel(schedule: Schedule): string {
  const state = scheduleState(schedule);
  if (state === 'attention') {
    return 'Needs attention';
  }
  return state === 'active' ? 'Active' : 'Paused';
}

function targetKind(schedule: Schedule): string {
  const raw = schedule.target?.kind || schedule.target?.workflowType;
  return typeof raw === 'string' && raw.trim() ? titleCaseLabel(formatStatusLabel(raw)) : 'Queue task';
}

function targetPayload(schedule: Schedule): Record<string, unknown> {
  const job = schedule.target?.job;
  if (job && typeof job === 'object' && 'payload' in job) {
    const payload = (job as { payload?: unknown }).payload;
    return payload && typeof payload === 'object' ? payload as Record<string, unknown> : {};
  }
  const initialParameters = schedule.target?.initialParameters;
  if (initialParameters && typeof initialParameters === 'object') {
    return initialParameters as Record<string, unknown>;
  }
  return {};
}

function targetRepository(schedule: Schedule): string {
  const payload = targetPayload(schedule);
  const repository = payload.repository;
  return typeof repository === 'string' && repository.trim() ? repository : '-';
}

function targetRuntime(schedule: Schedule): string {
  const payload = targetPayload(schedule);
  const runtime = payload.runtime || payload.provider;
  return typeof runtime === 'string' && runtime.trim() ? runtime : '-';
}

function targetModel(schedule: Schedule): string {
  const payload = targetPayload(schedule);
  const model = payload.model;
  return typeof model === 'string' && model.trim() ? model : '-';
}

function policyMode(policy: Record<string, unknown> | undefined, key: string): string {
  const value = policy?.[key];
  if (value && typeof value === 'object' && 'mode' in value) {
    return displayValue((value as { mode?: unknown }).mode as string | undefined);
  }
  return typeof value === 'string' ? value : '';
}

function policySummary(schedule: Schedule): string {
  const overlapMode = policyMode(schedule.policy, 'overlap');
  const catchupMode = policyMode(schedule.policy, 'catchup');
  return [overlapMode, catchupMode].filter(Boolean).map((value) => titleCaseLabel(formatStatusLabel(value))).join(' / ') || '-';
}

function isDueSoon(schedule: Schedule, now: number): boolean {
  if (!schedule.enabled || !schedule.nextRunAt) {
    return false;
  }
  const nextRun = new Date(schedule.nextRunAt).getTime();
  return Number.isFinite(nextRun) && nextRun >= now && nextRun <= now + 24 * 60 * 60 * 1000;
}

function prettyJson(value: unknown): string {
  return JSON.stringify(value || {}, null, 2);
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'Unexpected schedule error';
}

function readOnlyField(label: string, value: string) {
  return (
    <div className="schedules-fact" key={label}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function ScheduleRunsPanel({ runs, isLoading, isError, error }: {
  runs: ScheduleRun[];
  isLoading: boolean;
  isError: boolean;
  error: unknown;
}) {
  return (
    <section className="panel--data schedules-detail-panel" aria-label="Run history">
      <div className="schedules-panel-heading">
        <h3>Run History</h3>
        <p>Workflow executions spawned by this schedule.</p>
      </div>
      {isLoading ? (
        <p className="loading">Loading schedule runs...</p>
      ) : isError ? (
        <div className="schedules-error" role="alert">{errorMessage(error)}</div>
      ) : (
        <DataTable
          data={runs}
          columns={[
            {
              key: 'temporalWorkflowId',
              header: 'Workflow',
              render: (item) => item.temporalWorkflowId ? (
                <a href={`/workflows/${encodeURIComponent(item.temporalWorkflowId)}?source=temporal`}>
                  {compactId(item.temporalWorkflowId)}
                </a>
              ) : '-',
            },
            {
              key: 'scheduledFor',
              header: 'Scheduled',
              render: (item) => formatWhen(item.scheduledFor),
            },
            {
              key: 'dispatchAfter',
              header: 'Dispatch After',
              render: (item) => formatWhen(item.dispatchAfter),
            },
            {
              key: 'outcome',
              header: 'Outcome',
              render: (item) => normalizedLabel(item.outcome),
            },
            {
              key: 'trigger',
              header: 'Trigger',
              render: (item) => normalizedLabel(item.trigger),
            },
            {
              key: 'dispatchAttempts',
              header: 'Attempts',
              render: (item) => item.dispatchAttempts,
            },
            {
              key: 'message',
              header: 'Message',
              render: (item) => displayValue(item.message),
            },
          ]}
          emptyMessage="No schedule runs yet."
          getRowKey={(item) => item.id}
          ariaLabel="Schedule run history"
        />
      )}
    </section>
  );
}

function ScheduleDetailPage({ payload, definitionId }: { payload: BootPayload; definitionId: string }) {
  const queryClient = useQueryClient();
  const sources = useMemo(() => scheduleSources(payload), [payload]);
  const apiBase = payload.apiBase || '/api';
  const detailEndpoint = endpointFromTemplate(
    sources.detail,
    definitionId,
    `${apiBase}/recurring-workflows/{definitionId}`,
  );
  const runsEndpoint = endpointFromTemplate(
    sources.runs,
    definitionId,
    `${apiBase}/recurring-workflows/{definitionId}/runs?limit=200`,
  );
  const updateEndpoint = endpointFromTemplate(
    sources.update,
    definitionId,
    `${apiBase}/recurring-workflows/{definitionId}`,
  );
  const runNowEndpoint = endpointFromTemplate(
    sources.runNow,
    definitionId,
    `${apiBase}/recurring-workflows/{definitionId}/run`,
  );
  const detailQuery = useQuery({
    queryKey: ['schedules', 'detail', detailEndpoint],
    queryFn: () => fetchJson(detailEndpoint, ScheduleSchema),
  });
  const runsQuery = useQuery({
    queryKey: ['schedules', 'runs', runsEndpoint],
    queryFn: () => fetchJson(runsEndpoint, ScheduleRunsResponseSchema),
    enabled: detailQuery.isSuccess,
  });
  const [isEditing, setIsEditing] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);
  const [form, setForm] = useState({
    name: '',
    description: '',
    enabled: true,
    cron: '',
    timezone: 'UTC',
    target: '{}',
    policy: '{}',
  });

  const invalidateSchedule = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['schedules', 'detail', detailEndpoint] }),
      queryClient.invalidateQueries({ queryKey: ['schedules', 'runs', runsEndpoint] }),
    ]);
  };

  const updateMutation = useMutation({
    mutationFn: async (body: Record<string, unknown>) => {
      const response = await fetch(updateEndpoint, {
        method: 'PATCH',
        credentials: 'include',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!response.ok) {
        throw new HttpStatusError(response.status, response.statusText);
      }
      return ScheduleSchema.parse(await response.json());
    },
    onSuccess: async () => {
      setIsEditing(false);
      setEditError(null);
      await invalidateSchedule();
    },
  });

  const runNowMutation = useMutation({
    mutationFn: async () => {
      const response = await fetch(runNowEndpoint, {
        method: 'POST',
        credentials: 'include',
      });
      if (!response.ok) {
        throw new HttpStatusError(response.status, response.statusText);
      }
      return ScheduleRunSchema.parse(await response.json());
    },
    onSuccess: invalidateSchedule,
  });

  if (detailQuery.isLoading) {
    return <p className="loading">Loading recurring schedule...</p>;
  }

  if (detailQuery.isError || !detailQuery.data) {
    const status = detailQuery.error instanceof HttpStatusError ? detailQuery.error.status : 0;
    return (
      <div className="schedules-page stack">
        <section className="panel--data schedules-not-found" role={status === 404 ? undefined : 'alert'}>
          <h2>{status === 404 ? 'Schedule not found' : 'Schedule unavailable'}</h2>
          <p>{status === 404 ? 'This recurring schedule no longer exists or is not visible to your account.' : errorMessage(detailQuery.error)}</p>
          <a className="button secondary" href="/schedules">Back to schedules</a>
        </section>
      </div>
    );
  }

  const schedule = detailQuery.data;
  const status = scheduleState(schedule);
  const dispatchAttention = scheduleHasDispatchAttention(schedule);
  const runs = runsQuery.data?.items || [];

  const beginEdit = () => {
    setForm({
      name: schedule.name,
      description: schedule.description || '',
      enabled: schedule.enabled,
      cron: schedule.cron,
      timezone: schedule.timezone,
      target: prettyJson(schedule.target),
      policy: prettyJson(schedule.policy),
    });
    setEditError(null);
    setIsEditing(true);
  };

  const saveEdit = () => {
    setEditError(null);
    let target: Record<string, unknown>;
    let policy: Record<string, unknown>;
    try {
      target = JsonRecordSchema.parse(JSON.parse(form.target || '{}'));
      policy = JsonRecordSchema.parse(JSON.parse(form.policy || '{}'));
    } catch (error) {
      setEditError(errorMessage(error));
      return;
    }
    updateMutation.mutate({
      name: form.name,
      description: form.description || null,
      enabled: form.enabled,
      cron: form.cron,
      timezone: form.timezone,
      target,
      policy,
    });
  };

  const toggleEnabled = () => {
    updateMutation.mutate({ enabled: !schedule.enabled });
  };

  return (
    <div className="schedules-page schedules-detail-page stack">
      <header className="toolbar schedules-toolbar">
        <div>
          <p className="page-meta"><a href="/schedules">Schedules</a> / {schedule.name}</p>
          <h2 className="page-title">{schedule.name}</h2>
          <p className="page-meta">{displayValue(schedule.description || `${targetKind(schedule)} cadence`)}</p>
        </div>
        <div className="toolbar-controls">
          <span className={`schedules-state schedules-state--${status}`}>{stateLabel(schedule)}</span>
          <button type="button" className="secondary" onClick={beginEdit}>Edit schedule</button>
          <button type="button" className="secondary" onClick={() => runNowMutation.mutate()} disabled={runNowMutation.isPending}>
            {runNowMutation.isPending ? 'Dispatching' : 'Run now'}
          </button>
          <button type="button" className="secondary" onClick={toggleEnabled} disabled={updateMutation.isPending}>
            {schedule.enabled ? 'Pause' : 'Resume'}
          </button>
        </div>
      </header>

      {runNowMutation.isError ? <div className="schedules-error" role="alert">{errorMessage(runNowMutation.error)}</div> : null}
      {updateMutation.isError ? <div className="schedules-error" role="alert">{errorMessage(updateMutation.error)}</div> : null}
      {dispatchAttention ? (
        <div className="schedules-error" role="status">
          Dispatch needs attention: {displayValue(schedule.lastDispatchError || schedule.lastDispatchStatus)}
        </div>
      ) : null}

      <section className="schedules-summary-grid" aria-label="Schedule detail summary">
        <div className="schedules-summary-item">
          <span>Next Run</span>
          <strong>{formatWhen(schedule.nextRunAt)}</strong>
        </div>
        <div className="schedules-summary-item">
          <span>Cadence</span>
          <strong>{schedule.cron}</strong>
        </div>
        <div className="schedules-summary-item">
          <span>Last Scheduled</span>
          <strong>{formatWhen(schedule.lastScheduledFor)}</strong>
        </div>
        <div className="schedules-summary-item">
          <span>Attention</span>
          <strong>{dispatchAttention ? 'Review' : 'Clear'}</strong>
        </div>
      </section>

      <div className="schedules-detail-grid">
        <main className="schedules-detail-main stack">
          <section className="panel--data schedules-detail-panel" aria-label="Schedule overview">
            <div className="schedules-panel-heading">
              <h3>Overview</h3>
              <p>Current schedule timing, target, policy, and dispatch state.</p>
            </div>
            <div className="schedules-facts-grid">
              {readOnlyField('State', stateLabel(schedule))}
              {readOnlyField('Cron', schedule.cron)}
              {readOnlyField('Timezone', schedule.timezone)}
              {readOnlyField('Target', targetKind(schedule))}
              {readOnlyField('Repository', targetRepository(schedule))}
              {readOnlyField('Runtime', targetRuntime(schedule))}
              {readOnlyField('Model', targetModel(schedule))}
              {readOnlyField('Policy', policySummary(schedule))}
              {readOnlyField('Dispatch', normalizedLabel(schedule.lastDispatchStatus))}
              {readOnlyField('Dispatch Error', displayValue(schedule.lastDispatchError))}
            </div>
          </section>

          <ScheduleRunsPanel
            runs={runs}
            isLoading={runsQuery.isLoading}
            isError={runsQuery.isError}
            error={runsQuery.error}
          />

          <section className="panel--data schedules-detail-panel" aria-label="Schedule configuration">
            <div className="schedules-panel-heading">
              <h3>Configuration</h3>
              <p>Editable schedule fields backed by the recurring workflow update contract.</p>
            </div>
            {isEditing ? (
              <div className="schedules-edit-form">
                <label>Name<input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></label>
                <label>Description<textarea value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} /></label>
                <label className="schedules-checkbox"><input type="checkbox" checked={form.enabled} onChange={(event) => setForm({ ...form, enabled: event.target.checked })} /> Enabled</label>
                <label>Cron<input value={form.cron} onChange={(event) => setForm({ ...form, cron: event.target.value })} /></label>
                <label>Timezone<input value={form.timezone} onChange={(event) => setForm({ ...form, timezone: event.target.value })} /></label>
                <label>Target JSON<textarea value={form.target} onChange={(event) => setForm({ ...form, target: event.target.value })} /></label>
                <label>Policy JSON<textarea value={form.policy} onChange={(event) => setForm({ ...form, policy: event.target.value })} /></label>
                {editError ? <div className="schedules-error" role="alert">{editError}</div> : null}
                <div className="toolbar-controls">
                  <button type="button" onClick={saveEdit} disabled={updateMutation.isPending}>{updateMutation.isPending ? 'Saving' : 'Save changes'}</button>
                  <button type="button" className="secondary" onClick={() => setIsEditing(false)}>Cancel</button>
                </div>
              </div>
            ) : (
              <div className="schedules-config-readonly">
                <div className="schedules-facts-grid">
                  {readOnlyField('Name', schedule.name)}
                  {readOnlyField('Description', displayValue(schedule.description))}
                  {readOnlyField('Enabled', schedule.enabled ? 'Enabled' : 'Paused')}
                  {readOnlyField('Schedule Type', displayValue(schedule.scheduleType))}
                  {readOnlyField('Cron', schedule.cron)}
                  {readOnlyField('Timezone', schedule.timezone)}
                </div>
                <details>
                  <summary>Target JSON</summary>
                  <pre>{prettyJson(schedule.target)}</pre>
                </details>
                <details>
                  <summary>Policy JSON</summary>
                  <pre>{prettyJson(schedule.policy)}</pre>
                </details>
              </div>
            )}
          </section>
        </main>

        <aside className="panel--data schedules-detail-panel schedules-facts-rail" aria-label="Schedule facts">
          <div className="schedules-panel-heading">
            <h3>Facts</h3>
            <p>Identifiers, ownership, and freshness.</p>
          </div>
          <div className="schedules-facts-grid">
            {readOnlyField('Definition ID', schedule.id)}
            {schedule.temporalScheduleId ? readOnlyField('Temporal Schedule ID', schedule.temporalScheduleId) : null}
            {readOnlyField('Scope', normalizedLabel(schedule.scopeType))}
            {readOnlyField('Scope Ref', displayValue(schedule.scopeRef))}
            {readOnlyField('Owner User ID', displayValue(schedule.ownerUserId))}
            {readOnlyField('Version', displayValue(schedule.version))}
            {readOnlyField('Created', formatWhen(schedule.createdAt))}
            {readOnlyField('Updated', formatWhen(schedule.updatedAt))}
          </div>
        </aside>
      </div>
    </div>
  );
}

export function SchedulesPage({ payload }: { payload: BootPayload }) {
  const definitionId = useMemo(() => definitionIdFromPath(payload), [payload]);
  const listEndpoint = useMemo(() => scheduleListEndpoint(payload), [payload]);
  const { data, isLoading, isError, error, isFetching, refetch } = useQuery({
    queryKey: ['schedules', listEndpoint],
    queryFn: async () => fetchJson(listEndpoint, SchedulesResponseSchema),
    enabled: !definitionId,
  });

  if (definitionId) {
    return <ScheduleDetailPage payload={payload} definitionId={definitionId} />;
  }

  const schedules = data?.items || [];
  const stats = (() => {
    const now = Date.now();
    const active = schedules.filter((schedule) => schedule.enabled).length;
    const attention = schedules.filter((schedule) => scheduleState(schedule) === 'attention').length;
    const dueSoon = schedules.filter((schedule) => isDueSoon(schedule, now)).length;
    return { active, attention, dueSoon, total: schedules.length };
  })();

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
          <div className="schedules-error" role="alert">{errorMessage(error)}</div>
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
                    <strong>{normalizedLabel(item.lastDispatchStatus)}</strong>
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
