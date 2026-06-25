import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { FormEvent, useEffect, useMemo, useState } from 'react';
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
  updatedAt: z.string().optional(),
}).passthrough();

const SchedulesResponseSchema = z.object({
  items: z.array(ScheduleSchema),
});

const ScheduleRunSchema = z.object({
  id: z.string(),
  definitionId: z.string(),
  scheduledFor: z.string(),
  trigger: z.string(),
  outcome: z.string(),
  dispatchAttempts: z.number(),
  dispatchAfter: z.string().nullable().optional(),
  startedAt: z.string().nullable().optional(),
  temporalWorkflowId: z.string().nullable().optional(),
  temporalRunId: z.string().nullable().optional(),
  message: z.string().nullable().optional(),
  createdAt: z.string(),
  updatedAt: z.string(),
}).passthrough();

const ScheduleRunsResponseSchema = z.object({
  items: z.array(ScheduleRunSchema),
});

type Schedule = z.infer<typeof ScheduleSchema>;
type ScheduleRun = z.infer<typeof ScheduleRunSchema>;

type ScheduleSources = {
  list?: string | undefined;
  detail?: string | undefined;
  update?: string | undefined;
  runNow?: string | undefined;
  runs?: string | undefined;
};

const SchedulesBootDataSchema = z
  .object({
    initialPath: z.string().optional(),
    dashboardConfig: z
      .object({
        initialPath: z.string().optional(),
        sources: z
          .object({
            schedules: z
              .object({
                list: z.string().optional(),
                detail: z.string().optional(),
                update: z.string().optional(),
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
            update: z.string().optional(),
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

function scheduleBootData(payload: BootPayload) {
  const parsed = SchedulesBootDataSchema.safeParse(payload.initialData || {});
  return parsed.success ? parsed.data : undefined;
}

function scheduleSources(payload: BootPayload): ScheduleSources | undefined {
  const bootData = scheduleBootData(payload);
  return bootData?.dashboardConfig?.sources?.schedules || bootData?.sources?.schedules;
}

function scheduleListEndpoint(payload: BootPayload): string {
  const schedules = scheduleSources(payload);
  return schedules?.list || `${payload.apiBase || '/api'}/recurring-tasks?scope=personal`;
}

function scheduleRouteDefinitionId(payload: BootPayload): string | null {
  const bootData = scheduleBootData(payload);
  const rawPath = bootData?.dashboardConfig?.initialPath || bootData?.initialPath || '';
  const path = rawPath.split('?')[0]?.split('#')[0] || '';
  const match = path.match(/^\/schedules\/([^/]+)$/);
  if (!match) {
    return null;
  }
  try {
    const definitionId = decodeURIComponent(match[1] || '').trim();
    return definitionId && definitionId.toLowerCase() !== 'new' ? definitionId : null;
  } catch {
    return null;
  }
}

function scheduleEndpoint(
  payload: BootPayload,
  key: 'detail' | 'update' | 'runNow' | 'runs',
  definitionId: string,
): string {
  const fallbackPath = key === 'runNow'
    ? '/recurring-workflows/{definitionId}/run'
    : key === 'runs'
      ? '/recurring-workflows/{definitionId}/runs?limit=200'
      : '/recurring-workflows/{definitionId}';
  const template = scheduleSources(payload)?.[key] || `${payload.apiBase || '/api'}${fallbackPath}`;
  const encoded = encodeURIComponent(definitionId);
  return template.replaceAll('{definitionId}', encoded).replaceAll('{id}', encoded);
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

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : String(error || fallback);
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

function formatJsonValue(value: unknown): string {
  if (!value || (typeof value === 'object' && Object.keys(value as Record<string, unknown>).length === 0)) {
    return '-';
  }
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function runWorkflowHref(run: ScheduleRun): string | null {
  return run.temporalWorkflowId ? `/workflows/${encodeURIComponent(run.temporalWorkflowId)}?source=temporal` : null;
}

type ScheduleEditForm = {
  name: string;
  description: string;
  enabled: boolean;
  cron: string;
  timezone: string;
};

function editFormFromSchedule(schedule: Schedule): ScheduleEditForm {
  return {
    name: schedule.name,
    description: schedule.description || '',
    enabled: schedule.enabled,
    cron: schedule.cron,
    timezone: schedule.timezone,
  };
}

function isDueSoon(schedule: Schedule, now: number): boolean {
  if (!schedule.enabled || !schedule.nextRunAt) {
    return false;
  }
  const nextRun = new Date(schedule.nextRunAt).getTime();
  return Number.isFinite(nextRun) && nextRun >= now && nextRun <= now + 24 * 60 * 60 * 1000;
}

function ScheduleDetailPage({ payload, definitionId }: { payload: BootPayload; definitionId: string }) {
  const queryClient = useQueryClient();
  const [isEditing, setIsEditing] = useState(false);
  const [editForm, setEditForm] = useState<ScheduleEditForm | null>(null);
  const detailEndpoint = useMemo(() => scheduleEndpoint(payload, 'detail', definitionId), [payload, definitionId]);
  const updateEndpoint = useMemo(() => scheduleEndpoint(payload, 'update', definitionId), [payload, definitionId]);
  const runNowEndpoint = useMemo(() => scheduleEndpoint(payload, 'runNow', definitionId), [payload, definitionId]);
  const runsEndpoint = useMemo(() => scheduleEndpoint(payload, 'runs', definitionId), [payload, definitionId]);

  const detailQuery = useQuery({
    queryKey: ['schedule-detail', definitionId, detailEndpoint],
    queryFn: async () => {
      const response = await fetch(detailEndpoint, { credentials: 'include' });
      if (!response.ok) {
        throw new Error(`Failed to fetch schedule: ${response.statusText}`);
      }
      return ScheduleSchema.parse(await response.json());
    },
  });

  const runsQuery = useQuery({
    queryKey: ['schedule-runs', definitionId, runsEndpoint],
    queryFn: async () => {
      const response = await fetch(runsEndpoint, { credentials: 'include' });
      if (!response.ok) {
        throw new Error(`Failed to fetch schedule runs: ${response.statusText}`);
      }
      return ScheduleRunsResponseSchema.parse(await response.json());
    },
  });

  useEffect(() => {
    if (detailQuery.data && !isEditing) {
      setEditForm(editFormFromSchedule(detailQuery.data));
    }
  }, [detailQuery.data, isEditing]);

  const refreshDetail = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['schedule-detail', definitionId] }),
      queryClient.invalidateQueries({ queryKey: ['schedule-runs', definitionId] }),
    ]);
  };

  const updateMutation = useMutation({
    mutationFn: async (form: ScheduleEditForm) => {
      const response = await fetch(updateEndpoint, {
        method: 'PATCH',
        credentials: 'include',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          name: form.name,
          description: form.description,
          enabled: form.enabled,
          cron: form.cron,
          timezone: form.timezone,
        }),
      });
      if (!response.ok) {
        throw new Error(`Failed to update schedule: ${response.statusText}`);
      }
      return ScheduleSchema.parse(await response.json());
    },
    onSuccess: async (updated) => {
      queryClient.setQueryData(['schedule-detail', definitionId, detailEndpoint], updated);
      setEditForm(editFormFromSchedule(updated));
      setIsEditing(false);
      await refreshDetail();
    },
  });

  const runNowMutation = useMutation({
    mutationFn: async () => {
      const response = await fetch(runNowEndpoint, {
        method: 'POST',
        credentials: 'include',
      });
      if (!response.ok) {
        throw new Error(`Failed to run schedule: ${response.statusText}`);
      }
      return ScheduleRunSchema.parse(await response.json());
    },
    onSuccess: async () => {
      await refreshDetail();
    },
  });

  const schedule = detailQuery.data;
  const runs = runsQuery.data?.items || [];
  const currentForm = editForm || (schedule ? editFormFromSchedule(schedule) : null);

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (currentForm) {
      updateMutation.mutate(currentForm);
    }
  };

  if (detailQuery.isLoading) {
    return (
      <div className="schedules-page stack">
        <p className="loading">Loading recurring schedule...</p>
      </div>
    );
  }

  if (detailQuery.isError || !schedule) {
    return (
      <div className="schedules-page stack">
        <a href="/schedules" className="secondary">Back to schedules</a>
        <div className="schedules-error" role="alert">{errorMessage(detailQuery.error, 'Schedule not found')}</div>
      </div>
    );
  }

  return (
    <div className="schedules-page schedules-detail-page stack">
      <header className="toolbar schedules-toolbar">
        <div className="schedules-detail-title">
          <nav className="page-meta" aria-label="Breadcrumb">
            <a href="/schedules">Schedules</a>
            <span>/</span>
            <span>{schedule.name}</span>
          </nav>
          <h2 className="page-title">{schedule.name}</h2>
          <p className="page-meta">{displayValue(schedule.description)}</p>
          <p className="page-meta" title={definitionId}>Definition ID: {definitionId}</p>
        </div>
        <div className="toolbar-controls">
          <span className={`schedules-state schedules-state--${scheduleState(schedule)}`}>
            {stateLabel(schedule)}
          </span>
          <button
            type="button"
            className="secondary"
            onClick={() => void refreshDetail()}
            disabled={detailQuery.isFetching || runsQuery.isFetching}
          >
            {detailQuery.isFetching || runsQuery.isFetching ? 'Refreshing' : 'Refresh'}
          </button>
          <button
            type="button"
            className="secondary"
            onClick={() => {
              setEditForm(editFormFromSchedule(schedule));
              setIsEditing((value) => !value);
            }}
          >
            {isEditing ? 'Cancel edit' : 'Edit schedule'}
          </button>
          <button
            type="button"
            className="button"
            onClick={() => runNowMutation.mutate()}
            disabled={runNowMutation.isPending}
          >
            {runNowMutation.isPending ? 'Running' : 'Run now'}
          </button>
        </div>
      </header>

      {updateMutation.isError && (
        <div className="schedules-error" role="alert">
          {errorMessage(updateMutation.error, 'Failed to update schedule')}
        </div>
      )}
      {runNowMutation.isError && (
        <div className="schedules-error" role="alert">
          {errorMessage(runNowMutation.error, 'Failed to run schedule')}
        </div>
      )}

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
          <span>Last Run</span>
          <strong>{formatWhen(schedule.lastScheduledFor)}</strong>
        </div>
        <div className="schedules-summary-item">
          <span>Dispatch</span>
          <strong>{schedule.lastDispatchStatus ? titleCaseLabel(formatStatusLabel(schedule.lastDispatchStatus)) : '-'}</strong>
        </div>
      </section>

      <div className="schedules-detail-grid">
        <section className="panel--data schedules-detail-panel" aria-label="Schedule configuration">
          <div className="section-heading-row">
            <h3>Configuration</h3>
          </div>
          {isEditing && currentForm ? (
            <form className="schedules-edit-form" onSubmit={onSubmit}>
              <label>
                <span>Name</span>
                <input
                  value={currentForm.name}
                  onChange={(event) => {
                    const value = event.currentTarget.value;
                    setEditForm((previous) => previous ? { ...previous, name: value } : null);
                  }}
                  required
                />
              </label>
              <label>
                <span>Description</span>
                <textarea
                  value={currentForm.description}
                  onChange={(event) => {
                    const value = event.currentTarget.value;
                    setEditForm((previous) => previous ? { ...previous, description: value } : null);
                  }}
                  rows={3}
                />
              </label>
              <label>
                <span>Cron</span>
                <input
                  value={currentForm.cron}
                  onChange={(event) => {
                    const value = event.currentTarget.value;
                    setEditForm((previous) => previous ? { ...previous, cron: value } : null);
                  }}
                  required
                />
              </label>
              <label>
                <span>Timezone</span>
                <input
                  value={currentForm.timezone}
                  onChange={(event) => {
                    const value = event.currentTarget.value;
                    setEditForm((previous) => previous ? { ...previous, timezone: value } : null);
                  }}
                  required
                />
              </label>
              <label className="schedules-checkbox-label">
                <input
                  type="checkbox"
                  checked={currentForm.enabled}
                  onChange={(event) => {
                    const value = event.currentTarget.checked;
                    setEditForm((previous) => previous ? { ...previous, enabled: value } : null);
                  }}
                />
                <span>Enabled</span>
              </label>
              <div className="toolbar-controls">
                <button type="submit" className="button" disabled={updateMutation.isPending}>
                  {updateMutation.isPending ? 'Saving' : 'Save schedule'}
                </button>
                <button
                  type="button"
                  className="secondary"
                  onClick={() => {
                    setEditForm(editFormFromSchedule(schedule));
                    setIsEditing(false);
                  }}
                >
                  Cancel
                </button>
              </div>
            </form>
          ) : (
            <dl className="schedules-detail-list">
              <div>
                <dt>Cron</dt>
                <dd><code>{schedule.cron}</code></dd>
              </div>
              <div>
                <dt>Timezone</dt>
                <dd>{displayValue(schedule.timezone)}</dd>
              </div>
              <div>
                <dt>Target</dt>
                <dd>{targetKind(schedule)}</dd>
              </div>
              <div>
                <dt>Repository</dt>
                <dd>{targetRepository(schedule)}</dd>
              </div>
              <div>
                <dt>Policy</dt>
                <dd>{policySummary(schedule)}</dd>
              </div>
              <div>
                <dt>Last Dispatch Error</dt>
                <dd>{displayValue(schedule.lastDispatchError)}</dd>
              </div>
            </dl>
          )}
        </section>

        <aside className="panel--data schedules-detail-panel" aria-label="Schedule facts">
          <div className="section-heading-row">
            <h3>Facts</h3>
          </div>
          <dl className="schedules-detail-list">
            <div>
              <dt>Definition ID</dt>
              <dd title={definitionId}>{definitionId}</dd>
            </div>
            <div>
              <dt>Temporal Schedule ID</dt>
              <dd>{displayValue(schedule.temporalScheduleId)}</dd>
            </div>
            <div>
              <dt>Scope</dt>
              <dd>{displayValue(schedule.scopeType)} / {displayValue(schedule.scopeRef)}</dd>
            </div>
            <div>
              <dt>Type</dt>
              <dd>{displayValue(schedule.scheduleType)}</dd>
            </div>
            <div>
              <dt>Updated</dt>
              <dd>{formatWhen(schedule.updatedAt)}</dd>
            </div>
          </dl>
        </aside>
      </div>

      <section className="panel--data schedules-detail-panel" aria-label="Schedule run history">
        <div className="section-heading-row">
          <h3>Runs</h3>
        </div>
        {runsQuery.isError ? (
          <div className="schedules-error" role="alert">{errorMessage(runsQuery.error, 'Failed to fetch schedule runs')}</div>
        ) : (
          <DataTable
            data={runs}
            columns={[
              {
                key: 'scheduledFor',
                header: 'Scheduled For',
                render: (item) => formatWhen(item.scheduledFor),
              },
              {
                key: 'startedAt',
                header: 'Actual Start',
                render: (item) => formatWhen(item.startedAt),
              },
              {
                key: 'outcome',
                header: 'Status',
                render: (item) => titleCaseLabel(formatStatusLabel(item.outcome)),
              },
              {
                key: 'trigger',
                header: 'Trigger',
                render: (item) => titleCaseLabel(formatStatusLabel(item.trigger)),
              },
              {
                key: 'temporalWorkflowId',
                header: 'Workflow',
                render: (item) => {
                  const href = runWorkflowHref(item);
                  return href ? <a href={href}>{item.temporalWorkflowId}</a> : '-';
                },
              },
              {
                key: 'message',
                header: 'Message',
                render: (item) => displayValue(item.message),
              },
            ]}
            emptyMessage={runsQuery.isLoading ? 'Loading schedule runs...' : 'No runs recorded for this schedule.'}
            getRowKey={(item) => item.id}
            ariaLabel="Schedule runs"
          />
        )}
      </section>

      <section className="panel--data schedules-detail-panel" aria-label="Schedule target payload">
        <div className="section-heading-row">
          <h3>Target Payload</h3>
        </div>
        <pre className="schedules-json-block">{formatJsonValue(schedule.target)}</pre>
      </section>
    </div>
  );
}

export function SchedulesPage({ payload }: { payload: BootPayload }) {
  const routeDefinitionId = useMemo(() => scheduleRouteDefinitionId(payload), [payload]);
  if (routeDefinitionId) {
    return <ScheduleDetailPage payload={payload} definitionId={routeDefinitionId} />;
  }

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
          <div className="schedules-error" role="alert">{errorMessage(error, 'Failed to fetch schedules')}</div>
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
