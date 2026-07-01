import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { FormEvent, useEffect, useMemo, useRef, useState } from 'react';
import { DataTable } from '../components/tables/DataTable';
import { DashboardActionDialog } from '../components/DashboardActionDialog';

import { z } from 'zod';
import { BootPayload } from '../boot/parseBootPayload';
import { navigateTo } from '../lib/navigation';
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
  delete?: string | undefined;
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
                delete: z.string().optional(),
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
            delete: z.string().optional(),
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
  key: 'detail' | 'update' | 'runNow' | 'runs' | 'delete',
  definitionId: string,
): string {
  const fallbackPath = key === 'runNow'
    ? '/recurring-workflows/{definitionId}/run'
    : key === 'runs'
      ? '/recurring-workflows/{definitionId}/runs?limit=200'
      : '/recurring-workflows/{definitionId}';
  const template = scheduleSources(payload)?.[key] || `${payload.apiBase || '/api'}${fallbackPath}`;
  const encoded = encodeURIComponent(definitionId);
  return template.replaceAll('{definitionId}', encoded);
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

class ScheduleRequestError extends Error {
  status: number;

  constructor(status: number, statusText: string) {
    super(scheduleDetailErrorMessage(status, statusText));
    this.status = status;
  }
}

function scheduleDetailErrorMessage(status: number, statusText: string): string {
  if (status === 403) {
    return 'You do not have access to this recurring schedule.';
  }
  if (status === 404) {
    return 'Recurring schedule not found.';
  }
  return `Failed to fetch schedule: ${statusText || status}`;
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

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function booleanValue(record: Record<string, unknown> | null, keys: string[]): boolean | undefined {
  if (!record) {
    return undefined;
  }
  for (const key of keys) {
    const value = record[key];
    if (typeof value === 'boolean') {
      return value;
    }
  }
  return undefined;
}

function stringValue(record: Record<string, unknown> | null, keys: string[]): string | undefined {
  if (!record) {
    return undefined;
  }
  for (const key of keys) {
    const value = record[key];
    if (typeof value === 'string' && value.trim()) {
      return value;
    }
  }
  return undefined;
}

function explicitScheduleBoolean(schedule: Schedule, keys: string[]): boolean | undefined {
  const root = asRecord(schedule);
  const containers = [
    root,
    asRecord(root?.permissions),
    asRecord(root?.actions),
    asRecord(root?.actionAvailability),
  ];
  for (const container of containers) {
    const value = booleanValue(container, keys);
    if (typeof value === 'boolean') {
      return value;
    }
  }
  return undefined;
}

function explicitScheduleReason(schedule: Schedule, keys: string[]): string | undefined {
  const root = asRecord(schedule);
  const disabledReasons = asRecord(root?.disabledReasons);
  const containers = [
    disabledReasons,
    asRecord(asRecord(root?.actions)?.disabledReasons),
    asRecord(asRecord(root?.actionAvailability)?.disabledReasons),
    asRecord(asRecord(root?.permissions)?.disabledReasons),
  ];
  for (const container of containers) {
    const value = stringValue(container, keys);
    if (value) {
      return value;
    }
  }
  return undefined;
}

function scheduleActionAvailability(schedule: Schedule, sources: ScheduleSources | undefined) {
  const canEdit = explicitScheduleBoolean(schedule, ['canEdit', 'canUpdate', 'canManage', 'edit', 'update']);
  const canRun = explicitScheduleBoolean(schedule, ['canRunNow', 'canRun', 'runNow', 'run']);
  const canDelete = explicitScheduleBoolean(schedule, ['canDelete', 'delete']);
  const deleteContractAvailable = Boolean(sources?.delete);
  return {
    canEdit: canEdit ?? true,
    canRun: canRun ?? true,
    canDelete: deleteContractAvailable && Boolean(canDelete),
    deleteContractAvailable,
    editReason: explicitScheduleReason(schedule, ['canEdit', 'canUpdate', 'edit', 'update']) || 'You can view this schedule, but you do not have permission to edit it.',
    runReason: explicitScheduleReason(schedule, ['canRunNow', 'canRun', 'runNow', 'run']) || 'You can view this schedule, but you do not have permission to run it manually.',
    deleteReason: explicitScheduleReason(schedule, ['canDelete', 'delete']) || (
      deleteContractAvailable
        ? 'You can view this schedule, but you do not have permission to delete it.'
        : 'Schedule deletion is not available yet.'
    ),
  };
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
  overlapMode: string;
  catchupMode: string;
  jitterSeconds: string;
  targetJson: string;
};

const SUPPORTED_CATCHUP_MODES = new Set(['none', 'last', 'all']);

function editFormFromSchedule(schedule: Schedule): ScheduleEditForm {
  const overlap = schedule.policy?.overlap;
  const catchup = schedule.policy?.catchup;
  const jitterSeconds = schedule.policy?.jitterSeconds;
  const catchupMode = catchup && typeof catchup === 'object' && 'mode' in catchup
    ? String((catchup as { mode?: unknown }).mode || 'last')
    : 'last';
  return {
    name: schedule.name,
    description: schedule.description || '',
    enabled: schedule.enabled,
    cron: schedule.cron,
    timezone: schedule.timezone,
    overlapMode: overlap && typeof overlap === 'object' && 'mode' in overlap
      ? String((overlap as { mode?: unknown }).mode || 'skip')
      : 'skip',
    catchupMode,
    jitterSeconds: typeof jitterSeconds === 'number' || typeof jitterSeconds === 'string'
      ? String(jitterSeconds)
      : '0',
    targetJson: JSON.stringify(schedule.target || {}, null, 2),
  };
}

type ScheduleEditErrors = Partial<Record<keyof ScheduleEditForm, string | undefined>>;

function parseCronField(
  raw: string,
  min: number,
  max: number,
  fieldName: string,
  allowSundaySeven = false,
): string | null {
  for (const part of raw.split(',')) {
    const segment = part.trim();
    if (!segment) {
      return `${fieldName} contains an empty segment`;
    }
    const [rangePart, stepPart] = segment.split('/');
    if (!rangePart || segment.split('/').length > 2) {
      return `${fieldName} contains an invalid step`;
    }
    if (stepPart !== undefined) {
      const step = Number(stepPart);
      if (!Number.isInteger(step) || step <= 0) {
        return `${fieldName} step must be a positive integer`;
      }
    }
    const bounds = rangePart === '*' ? [String(min), String(max)] : rangePart.split('-');
    if (bounds.length > 2 || !bounds[0] || !bounds[bounds.length - 1]) {
      return `${fieldName} contains an invalid range`;
    }
    const start = Number(bounds[0]);
    const end = Number(bounds[bounds.length - 1]);
    if (!Number.isInteger(start) || !Number.isInteger(end) || start > end) {
      return `${fieldName} contains an invalid value`;
    }
    const normalizedStart = allowSundaySeven && start === 7 ? 0 : start;
    const normalizedEnd = allowSundaySeven && end === 7 ? 0 : end;
    if (
      normalizedStart < min
      || normalizedStart > max
      || normalizedEnd < min
      || normalizedEnd > max
    ) {
      return `${fieldName} value is out of range`;
    }
  }
  return null;
}

function validateCronExpression(value: string): string | null {
  const parts = value.trim().split(/\s+/).filter(Boolean);
  if (parts.length !== 5) {
    return 'Cron must contain exactly 5 fields.';
  }
  return parseCronField(parts[0] || '', 0, 59, 'Minute')
    || parseCronField(parts[1] || '', 0, 23, 'Hour')
    || parseCronField(parts[2] || '', 1, 31, 'Day')
    || parseCronField(parts[3] || '', 1, 12, 'Month')
    || parseCronField(parts[4] || '', 0, 6, 'Weekday', true);
}

function validateTimezone(value: string): string | null {
  const timezone = value.trim();
  if (!timezone) {
    return 'Timezone is required.';
  }
  try {
    new Intl.DateTimeFormat('en-US', { timeZone: timezone }).format(new Date());
    return null;
  } catch {
    return `Timezone '${timezone}' is invalid.`;
  }
}

function parseTargetJson(value: string): { value?: Record<string, unknown>; error?: string } {
  try {
    const parsed = JSON.parse(value);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return { error: 'Target must be a JSON object.' };
    }
    return { value: parsed as Record<string, unknown> };
  } catch {
    return { error: 'Target must be valid JSON.' };
  }
}

function validateScheduleEditForm(form: ScheduleEditForm): ScheduleEditErrors {
  const errors: ScheduleEditErrors = {};
  if (!form.name.trim()) {
    errors.name = 'Name is required.';
  }
  const cronError = validateCronExpression(form.cron);
  if (cronError) {
    errors.cron = cronError;
  }
  const timezoneError = validateTimezone(form.timezone);
  if (timezoneError) {
    errors.timezone = timezoneError;
  }
  const jitter = Number(form.jitterSeconds);
  if (!Number.isInteger(jitter) || jitter < 0) {
    errors.jitterSeconds = 'Jitter seconds must be a non-negative integer.';
  }
  if (!SUPPORTED_CATCHUP_MODES.has(form.catchupMode)) {
    errors.catchupMode = `Catchup policy '${form.catchupMode}' is not supported for editing.`;
  }
  const targetResult = parseTargetJson(form.targetJson);
  if (targetResult.error) {
    errors.targetJson = targetResult.error;
  }
  return errors;
}

function hasFormErrors(errors: ScheduleEditErrors): boolean {
  return Object.values(errors).some(Boolean);
}

function stableJson(value: unknown): string {
  return JSON.stringify(value, (_, candidate) => {
    if (candidate && typeof candidate === 'object' && !Array.isArray(candidate)) {
      return Object.keys(candidate as Record<string, unknown>)
        .sort()
        .reduce<Record<string, unknown>>((result, key) => {
          result[key] = (candidate as Record<string, unknown>)[key];
          return result;
        }, {});
    }
    return candidate;
  });
}

function buildPolicyPayload(schedule: Schedule, form: ScheduleEditForm): Record<string, unknown> {
  const policy = { ...(schedule.policy || {}) };
  const overlap = policy.overlap && typeof policy.overlap === 'object'
    ? { ...(policy.overlap as Record<string, unknown>) }
    : {};
  overlap.mode = form.overlapMode;
  policy.overlap = overlap;
  const catchup = policy.catchup && typeof policy.catchup === 'object'
    ? { ...(policy.catchup as Record<string, unknown>) }
    : {};
  catchup.mode = form.catchupMode;
  policy.catchup = catchup;
  policy.jitterSeconds = Number(form.jitterSeconds);
  return policy;
}

function buildSchedulePatchPayload(schedule: Schedule, form: ScheduleEditForm): Record<string, unknown> {
  const payload: Record<string, unknown> = {};
  if (form.name !== schedule.name) {
    payload.name = form.name;
  }
  if (form.description !== (schedule.description || '')) {
    payload.description = form.description;
  }
  if (form.enabled !== schedule.enabled) {
    payload.enabled = form.enabled;
  }
  if (form.cron !== schedule.cron) {
    payload.cron = form.cron;
  }
  if (form.timezone !== schedule.timezone) {
    payload.timezone = form.timezone;
  }
  const initialForm = editFormFromSchedule(schedule);
  if (
    form.overlapMode !== initialForm.overlapMode
    || form.catchupMode !== initialForm.catchupMode
    || form.jitterSeconds !== initialForm.jitterSeconds
  ) {
    payload.policy = buildPolicyPayload(schedule, form);
  }
  const target = parseTargetJson(form.targetJson).value || {};
  if (stableJson(target) !== stableJson(schedule.target || {})) {
    payload.target = target;
  }
  return payload;
}

async function responseErrorMessage(response: Response, fallback: string): Promise<string> {
  try {
    const body = await response.json();
    const detail = body?.detail;
    if (typeof detail === 'string' && detail.trim()) {
      return detail;
    }
    if (detail && typeof detail === 'object') {
      const message = detail.message || detail.error;
      if (typeof message === 'string' && message.trim()) {
        return message;
      }
    }
  } catch {
    // Ignore malformed error bodies and use the status text below.
  }
  return `${fallback}: ${response.statusText}`;
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
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [editForm, setEditForm] = useState<ScheduleEditForm | null>(null);
  const [submitErrors, setSubmitErrors] = useState<ScheduleEditErrors>({});
  const isEditingRef = useRef(false);

  useEffect(() => {
    isEditingRef.current = isEditing;
  }, [isEditing]);
  const detailEndpoint = useMemo(() => scheduleEndpoint(payload, 'detail', definitionId), [payload, definitionId]);
  const updateEndpoint = useMemo(() => scheduleEndpoint(payload, 'update', definitionId), [payload, definitionId]);
  const runNowEndpoint = useMemo(() => scheduleEndpoint(payload, 'runNow', definitionId), [payload, definitionId]);
  const runsEndpoint = useMemo(() => scheduleEndpoint(payload, 'runs', definitionId), [payload, definitionId]);
  const deleteEndpoint = useMemo(() => scheduleEndpoint(payload, 'delete', definitionId), [payload, definitionId]);
  const sources = useMemo(() => scheduleSources(payload), [payload]);

  const detailQuery = useQuery({
    queryKey: ['schedule-detail', definitionId, detailEndpoint],
    queryFn: async () => {
      const response = await fetch(detailEndpoint, { credentials: 'include' });
      if (!response.ok) {
        throw new ScheduleRequestError(response.status, response.statusText);
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
    mutationFn: async ({ patchPayload }: { patchPayload: Record<string, unknown> }) => {
      const response = await fetch(updateEndpoint, {
        method: 'PATCH',
        credentials: 'include',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(patchPayload),
      });
      if (!response.ok) {
        throw new Error(await responseErrorMessage(response, 'Failed to update schedule'));
      }
      return ScheduleSchema.parse(await response.json());
    },
    onSuccess: async (updated) => {
      queryClient.setQueryData(['schedule-detail', definitionId, detailEndpoint], updated);
      setEditForm(editFormFromSchedule(updated));
      setSubmitErrors({});
      isEditingRef.current = false;
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
        throw new Error(await responseErrorMessage(response, 'Failed to run schedule'));
      }
      return ScheduleRunSchema.parse(await response.json());
    },
    onSuccess: async () => {
      await refreshDetail();
    },
  });

  const deleteMutation = useMutation({
    mutationFn: async () => {
      const response = await fetch(deleteEndpoint, {
        method: 'DELETE',
        credentials: 'include',
      });
      if (!response.ok) {
        throw new Error(
          await responseErrorMessage(response, 'Failed to delete schedule'),
        );
      }
    },
    onSuccess: () => {
      navigateTo('/schedules');
    },
  });

  const pauseResumeMutation = useMutation({
    mutationFn: async (enabled: boolean) => {
      const response = await fetch(updateEndpoint, {
        method: 'PATCH',
        credentials: 'include',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ enabled }),
      });
      if (!response.ok) {
        throw new Error(`Failed to ${enabled ? 'resume' : 'pause'} schedule: ${response.statusText}`);
      }
      return ScheduleSchema.parse(await response.json());
    },
    onSuccess: async (updated) => {
      queryClient.setQueryData(['schedule-detail', definitionId, detailEndpoint], updated);
      setEditForm((previous) => {
        if (isEditingRef.current && previous) {
          return { ...previous, enabled: updated.enabled };
        }
        return editFormFromSchedule(updated);
      });
      await refreshDetail();
    },
  });

  const schedule = detailQuery.data;
  const runs = runsQuery.data?.items || [];
  const currentForm = editForm || (schedule ? editFormFromSchedule(schedule) : null);
  const actions = schedule ? scheduleActionAvailability(schedule, sources) : null;
  const visibleFormErrors = submitErrors;

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (currentForm && schedule && actions?.canEdit) {
      const errors = validateScheduleEditForm(currentForm);
      setSubmitErrors(errors);
      if (hasFormErrors(errors)) {
        return;
      }
      const patchPayload = buildSchedulePatchPayload(schedule, currentForm);
      if (Object.keys(patchPayload).length === 0) {
        isEditingRef.current = false;
        setIsEditing(false);
        return;
      }
      updateMutation.mutate({ patchPayload });
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
              setSubmitErrors({});
              setIsEditing((value) => {
                const next = !value;
                isEditingRef.current = next;
                return next;
              });
            }}
            disabled={!actions?.canEdit}
            title={!actions?.canEdit ? actions?.editReason : undefined}
          >
            {isEditing ? 'Cancel edit' : 'Edit schedule'}
          </button>
          <button
            type="button"
            className="button"
            onClick={() => runNowMutation.mutate()}
            disabled={runNowMutation.isPending || !actions?.canRun}
            title={!actions?.canRun ? actions?.runReason : undefined}
          >
            {runNowMutation.isPending ? 'Running' : 'Run now'}
          </button>
          {actions?.canDelete ? (
            <button
              type="button"
              className="secondary"
              onClick={() => setDeleteDialogOpen(true)}
              disabled={deleteMutation.isPending}
            >
              {deleteMutation.isPending ? 'Deleting' : 'Delete schedule'}
            </button>
          ) : null}
          <button
            type="button"
            className="secondary"
            onClick={() => pauseResumeMutation.mutate(!schedule.enabled)}
            disabled={pauseResumeMutation.isPending || isEditing || updateMutation.isPending || !actions?.canEdit}
            title={!actions?.canEdit ? actions?.editReason : undefined}
          >
            {pauseResumeMutation.isPending
              ? (schedule.enabled ? 'Pausing' : 'Resuming')
              : (schedule.enabled ? 'Pause schedule' : 'Resume schedule')}
          </button>
        </div>
      </header>

      <DashboardActionDialog
        open={deleteDialogOpen}
        title="Delete schedule"
        subject={schedule.name}
        compactId={definitionId}
        consequence="Delete this recurring schedule. Future runs will stop, but prior workflow executions and artifacts remain available."
        confirmLabel={deleteMutation.isPending ? 'Deleting' : 'Delete schedule'}
        confirmPending={deleteMutation.isPending}
        danger
        destructive
        confirmationText="DELETE"
        disabledReason={actions?.canDelete ? null : actions?.deleteReason}
        error={deleteMutation.isError ? errorMessage(deleteMutation.error, 'Failed to delete schedule') : null}
        onCancel={() => setDeleteDialogOpen(false)}
        onConfirm={() => deleteMutation.mutate()}
      />

      {actions && (!actions.canEdit || !actions.canRun || (actions.deleteContractAvailable && !actions.canDelete)) ? (
        <div className="schedules-error" role="note">
          {[
            !actions.canEdit ? actions.editReason : null,
            !actions.canRun ? actions.runReason : null,
            actions.deleteContractAvailable && !actions.canDelete ? actions.deleteReason : null,
          ].filter(Boolean).join(' ')}
        </div>
      ) : null}

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
      {deleteMutation.isError && (
        <div className="schedules-error" role="alert">
          {errorMessage(deleteMutation.error, 'Failed to delete schedule')}
        </div>
      )}
      {pauseResumeMutation.isError && (
        <div className="schedules-error" role="alert">
          {errorMessage(pauseResumeMutation.error, schedule.enabled ? 'Failed to pause schedule' : 'Failed to resume schedule')}
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
                {visibleFormErrors.name && <span className="schedules-field-error">{visibleFormErrors.name}</span>}
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
                    setSubmitErrors((previous) => ({ ...previous, cron: undefined }));
                  }}
                  required
                  aria-invalid={Boolean(visibleFormErrors.cron)}
                />
                {visibleFormErrors.cron && <span className="schedules-field-error">{visibleFormErrors.cron}</span>}
              </label>
              <label>
                <span>Timezone</span>
                <input
                  value={currentForm.timezone}
                  onChange={(event) => {
                    const value = event.currentTarget.value;
                    setEditForm((previous) => previous ? { ...previous, timezone: value } : null);
                    setSubmitErrors((previous) => ({ ...previous, timezone: undefined }));
                  }}
                  required
                  aria-invalid={Boolean(visibleFormErrors.timezone)}
                />
                {visibleFormErrors.timezone && <span className="schedules-field-error">{visibleFormErrors.timezone}</span>}
              </label>
              <label>
                <span>Overlap Policy</span>
                <select
                  value={currentForm.overlapMode}
                  onChange={(event) => {
                    const value = event.currentTarget.value;
                    setEditForm((previous) => previous ? { ...previous, overlapMode: value } : null);
                  }}
                >
                  <option value="skip">Skip overlapping run</option>
                  <option value="allow">Allow overlapping runs</option>
                  <option value="buffer_one">Buffer one run</option>
                  <option value="cancel_previous">Cancel previous run</option>
                </select>
              </label>
              <label>
                <span>Catchup Policy</span>
                <select
                  value={currentForm.catchupMode}
                  onChange={(event) => {
                    const value = event.currentTarget.value;
                    setEditForm((previous) => previous ? { ...previous, catchupMode: value } : null);
                    setSubmitErrors((previous) => ({ ...previous, catchupMode: undefined }));
                  }}
                  aria-invalid={Boolean(visibleFormErrors.catchupMode)}
                >
                  <option value="none">Do not catch up</option>
                  <option value="last">Run latest missed occurrence</option>
                  <option value="all">Run all missed occurrences</option>
                </select>
                {visibleFormErrors.catchupMode && <span className="schedules-field-error">{visibleFormErrors.catchupMode}</span>}
              </label>
              <label>
                <span>Jitter Seconds</span>
                <input
                  type="number"
                  min="0"
                  step="1"
                  value={currentForm.jitterSeconds}
                  onChange={(event) => {
                    const value = event.currentTarget.value;
                    setEditForm((previous) => previous ? { ...previous, jitterSeconds: value } : null);
                    setSubmitErrors((previous) => ({ ...previous, jitterSeconds: undefined }));
                  }}
                  aria-invalid={Boolean(visibleFormErrors.jitterSeconds)}
                />
                {visibleFormErrors.jitterSeconds && <span className="schedules-field-error">{visibleFormErrors.jitterSeconds}</span>}
              </label>
              <label>
                <span>Target Workflow Parameters</span>
                <textarea
                  value={currentForm.targetJson}
                  onChange={(event) => {
                    const value = event.currentTarget.value;
                    setEditForm((previous) => previous ? { ...previous, targetJson: value } : null);
                    setSubmitErrors((previous) => ({ ...previous, targetJson: undefined }));
                  }}
                  rows={9}
                  spellCheck={false}
                  aria-invalid={Boolean(visibleFormErrors.targetJson)}
                />
                {visibleFormErrors.targetJson && <span className="schedules-field-error">{visibleFormErrors.targetJson}</span>}
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
                    setSubmitErrors({});
                    isEditingRef.current = false;
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
        <DataTable
            data={runs}
            isLoading={runsQuery.isLoading}
            loadingMessage="Loading schedule runs..."
            isError={runsQuery.isError}
            errorMessage={errorMessage(runsQuery.error, 'Failed to fetch schedule runs')}
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
            emptyMessage="No runs recorded for this schedule."
            getRowKey={(item) => item.id}
            ariaLabel="Schedule runs"
          />
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
          <a
            href="/workflows/new?scheduleMode=recurring"
            className="button queue-step-icon-button"
            aria-label="Create recurring schedule"
          >
            +
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

      <section className="schedules-table-panel" aria-label="Recurring schedule list">
        <DataTable
            data={schedules}
            isLoading={isLoading}
            loadingMessage="Loading recurring schedules..."
            isError={isError}
            errorMessage={errorMessage(error, 'Failed to fetch schedules')}
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
      </section>
    </div>
  );
}
export default SchedulesPage;
