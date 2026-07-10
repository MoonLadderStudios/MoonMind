import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { FormEvent, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { LoadingPlaceholder } from '../components/dashboard/LoadingPlaceholder';
import { PageSizeSelector, parsePageSize } from '../components/PageSizeSelector';
import { DataTable } from '../components/tables/DataTable';
import { DashboardActionDialog } from '../components/DashboardActionDialog';
import { EntityDetailFrame } from '../components/EntityDetailFrame';
import { CollectionSidebar, type CollectionSidebarRow } from '../components/CollectionSidebar';
import { WorkflowColumnFilterButton, WorkflowColumnHeader } from '../components/WorkflowColumnHeader';

import { z } from 'zod';
import { BootPayload } from '../boot/parseBootPayload';
import { navigateTo } from '../lib/navigation';
import {
  clearRecurringScheduleFocusRequest,
  readRecurringScheduleFocusRequest,
} from '../lib/recurringScheduleFocus';
import { formatStatusLabel } from '../utils/formatters';

const SCHEDULES_MOBILE_MEDIA_QUERY = '(max-width: 720px)';

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
  count: z.number().optional(),
  nextPageToken: z.string().nullable().optional(),
  activeCount: z.number().optional(),
  next24hCount: z.number().optional(),
  attentionCount: z.number().optional(),
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
type RecurringSortKey = 'updatedAt' | 'name' | 'state' | 'target' | 'repository' | 'cron' | 'timezone' | 'nextRunAt' | 'lastScheduledFor' | 'dispatch';
type RecurringSortDirection = 'asc' | 'desc';
type RecurringFilterKey = 'schedule' | 'state' | 'target' | 'repository' | 'cadence' | 'nextRun' | 'lastScheduled' | 'dispatch' | 'updated';

type RecurringFilters = {
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

const EMPTY_RECURRING_FILTERS: RecurringFilters = {
  schedule: '',
  state: '',
  target: '',
  repository: '',
  cadence: '',
  nextRun: '',
  lastScheduled: '',
  dispatch: '',
  updated: '',
};

const RECURRING_FILTER_LABELS: Record<RecurringFilterKey, string> = {
  schedule: 'Schedule',
  state: 'State',
  target: 'Target',
  repository: 'Repository',
  cadence: 'Cadence / Timezone',
  nextRun: 'Next run',
  lastScheduled: 'Last scheduled',
  dispatch: 'Dispatch',
  updated: 'Updated',
};

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
    recurringListDisplayMode: z.enum(['hidden', 'sidebar', 'table']).optional(),
    recurringListDisplayStatus: z.string().nullable().optional(),
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
  return schedules?.list || `${payload.apiBase || '/api'}/recurring-workflows?scope=personal`;
}

function safeRecurringSearchParams(): URLSearchParams {
  if (typeof window === 'undefined') {
    return new URLSearchParams();
  }
  return new URLSearchParams(window.location.search);
}

function cleanQueryText(value: string | null): string {
  return String(value || '').trim().slice(0, 160);
}

function cleanCursor(value: string | null): string {
  return String(value || '').trim().slice(0, 512);
}

function parseRecurringFilters(params: URLSearchParams): RecurringFilters {
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

function parseRecurringSortKey(raw: string | null): RecurringSortKey {
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

function parseRecurringSortDirection(raw: string | null): RecurringSortDirection {
  return String(raw || '').trim() === 'asc' ? 'asc' : 'desc';
}

function activeRecurringFilterEntries(filters: RecurringFilters): Array<[RecurringFilterKey, string]> {
  return (Object.keys(RECURRING_FILTER_LABELS) as RecurringFilterKey[])
    .map((key) => [key, filters[key].trim()] as [RecurringFilterKey, string])
    .filter(([, value]) => Boolean(value));
}

function appendScheduleListParams(
  endpoint: string,
  filters: RecurringFilters,
  {
    pageSize,
    cursor,
    sort,
    sortDir,
  }: {
    pageSize: number;
    cursor: string;
    sort: RecurringSortKey;
    sortDir: RecurringSortDirection;
  },
): string {
  const activeFilters = activeRecurringFilterEntries(filters);
  if (
    activeFilters.length === 0
    && !cursor
    && pageSize === 50
    && sort === 'updatedAt'
    && sortDir === 'desc'
    && hasActiveScheduleListFilters(endpoint)
  ) {
    return endpoint;
  }

  const [base, hash = ''] = endpoint.split('#', 2);
  const [path, query = ''] = (base || '').split('?', 2);
  const params = new URLSearchParams(query);
  params.set('limit', String(pageSize));
  params.set('sort', sort);
  params.set('sortDir', sortDir);
  if (cursor) {
    params.set('cursor', cursor);
  } else {
    params.delete('cursor');
  }
  for (const [key, value] of activeFilters) {
    params.set(key, value);
  }
  const serialized = params.toString();
  return `${path}${serialized ? `?${serialized}` : ''}${hash ? `#${hash}` : ''}`;
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

function recurringListDisplayMode(payload: BootPayload, hasRouteDefinition: boolean): 'hidden' | 'sidebar' | 'table' {
  const mode = scheduleBootData(payload)?.recurringListDisplayMode;
  if (mode) {
    return mode;
  }
  return hasRouteDefinition ? 'sidebar' : 'table';
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

function hasActiveScheduleListFilters(endpoint: string): boolean {
  try {
    const parsed = new URL(endpoint, 'http://moonmind.local');
    for (const [key, value] of parsed.searchParams.entries()) {
      const normalizedKey = key.trim().toLowerCase().replace(/_/g, '');
      const normalizedValue = value.trim();
      if (
        !normalizedValue
        || normalizedKey === 'scope'
        || normalizedKey === 'limit'
        || normalizedKey === 'cursor'
        || normalizedKey === 'sort'
        || normalizedKey === 'sortdir'
      ) {
        continue;
      }
      if (
        normalizedKey === 'q'
        || normalizedKey === 'search'
        || normalizedKey === 'state'
        || normalizedKey === 'target'
        || normalizedKey === 'repository'
        || normalizedKey === 'repo'
        || normalizedKey === 'cadence'
        || normalizedKey === 'timezone'
        || normalizedKey === 'dispatch'
        || normalizedKey === 'updated'
        || normalizedKey.startsWith('filter')
        || normalizedKey.startsWith('next')
        || normalizedKey.startsWith('lastscheduled')
      ) {
        return true;
      }
    }
  } catch {
    return false;
  }
  return false;
}

function focusRecurringElement(element: HTMLElement | null | undefined): boolean {
  if (!element) {
    return false;
  }
  element.focus({ preventScroll: true });
  clearRecurringScheduleFocusRequest();
  return true;
}

function findRecurringScheduleFocusElement(attribute: string, definitionId: string): HTMLElement | null {
  const candidates = document.querySelectorAll<HTMLElement>(`[${attribute}]`);
  for (const candidate of candidates) {
    if (candidate.getAttribute(attribute) === definitionId) {
      return candidate;
    }
  }
  return null;
}

class ScheduleRequestError extends Error {
  status: number;

  constructor(status: number, statusText: string) {
    super(scheduleDetailErrorMessage(status, statusText));
    this.status = status;
  }
}

class ScheduleListRequestError extends Error {
  status: number;

  constructor(status: number, statusText: string) {
    super(scheduleListErrorMessage(status, statusText));
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

function scheduleListErrorMessage(status: number, statusText: string): string {
  if (status === 403) {
    return 'You do not have access to recurring schedules.';
  }
  return `Failed to fetch schedules: ${statusText || status}`;
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

function dispatchAttentionLabel(schedule: Schedule): string {
  const error = schedule.lastDispatchError?.trim();
  const status = schedule.lastDispatchStatus?.trim();
  if (scheduleState(schedule) === 'attention') {
    return error
      ? `Needs attention: ${error}`
      : 'Needs attention';
  }
  const statusLabel = status ? titleCaseLabel(formatStatusLabel(status)) : 'No dispatch attention';
  return error ? `${statusLabel}: ${error}` : statusLabel;
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

function RecurringScheduleSidebar({
  definitionId,
  schedules,
  isLoading,
  error,
  pinnedSchedule,
  onRetry,
}: {
  definitionId: string;
  schedules: Schedule[];
  isLoading: boolean;
  error: unknown;
  pinnedSchedule?: Schedule | null | undefined;
  onRetry?: (() => void) | undefined;
}) {
  const rows = useMemo(() => schedules.map((schedule): CollectionSidebarRow => ({
    id: schedule.id,
    href: `/schedules/${encodeURIComponent(schedule.id)}`,
    primaryText: schedule.name,
    metadata: <>{stateLabel(schedule)} · next {formatWhen(schedule.nextRunAt)}</>,
  })), [schedules]);
  const pinnedRow = useMemo(
    (): CollectionSidebarRow | null => (pinnedSchedule ? {
      id: pinnedSchedule.id,
      href: `/schedules/${encodeURIComponent(pinnedSchedule.id)}`,
      primaryText: pinnedSchedule.name,
      metadata: <>{stateLabel(pinnedSchedule)} · next {formatWhen(pinnedSchedule.nextRunAt)}</>,
    } : null),
    [pinnedSchedule],
  );
  return (
    <CollectionSidebar
      landmarkLabel="Recurring schedule navigation"
      tableLabel="Recurring schedule list table slice"
      header="Recurring"
      filterLabel="Recurring schedule sidebar filter"
      filterPlaceholder="Filter recurring schedules"
      rows={rows}
      activeId={definitionId}
      pinnedRow={pinnedRow}
      isLoading={isLoading}
      error={error}
      {...(onRetry ? { onRetry } : {})}
      loadingCopy="Loading recurring schedules..."
      emptyCopy="No recurring schedules yet."
      filteredEmptyCopy="No recurring schedules match the current filter."
      errorCopy="Recurring schedule navigation is unavailable."
      currentRowCopy="Current recurring schedule"
      rowFocusAttribute="data-recurring-sidebar-row-focus"
    />
  );
}

function RecurringScheduleWorkspace({
  definitionId,
  listDisplayMode,
  schedules,
  isLoading,
  error,
  pinnedSchedule,
  onRetry,
  children,
}: {
  definitionId: string;
  listDisplayMode: 'hidden' | 'sidebar' | 'table';
  schedules: Schedule[];
  isLoading: boolean;
  error: unknown;
  pinnedSchedule?: Schedule | null | undefined;
  onRetry?: (() => void) | undefined;
  children: ReactNode;
}) {
  if (listDisplayMode !== 'sidebar') {
    return <>{children}</>;
  }
  return (
    <div className="workflow-workspace-shell" data-recurring-list-display-mode="sidebar">
      <RecurringScheduleSidebar
        definitionId={definitionId}
        schedules={schedules}
        isLoading={isLoading}
        error={error}
        pinnedSchedule={pinnedSchedule ?? null}
        {...(onRetry ? { onRetry } : {})}
      />
      <div className="workflow-workspace-detail">
        {children}
      </div>
    </div>
  );
}

function ScheduleDetailPage({
  payload,
  definitionId,
  listDisplayMode = 'sidebar',
  sidebarSchedules = [],
  isSidebarLoading = false,
  sidebarError = null,
  onSidebarRetry,
}: {
  payload: BootPayload;
  definitionId: string;
  listDisplayMode?: 'hidden' | 'sidebar' | 'table';
  sidebarSchedules?: Schedule[];
  isSidebarLoading?: boolean;
  sidebarError?: unknown;
  onSidebarRetry?: () => void;
}) {
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
  const listEndpoint = useMemo(() => scheduleListEndpoint(payload), [payload]);
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
      queryClient.invalidateQueries({ queryKey: ['schedules', listEndpoint] }),
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
      void queryClient.invalidateQueries({ queryKey: ['schedules', listEndpoint] });
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

  useEffect(() => {
    const request = readRecurringScheduleFocusRequest();
    if (!request || (request.definitionId && request.definitionId !== definitionId)) {
      return;
    }
    if (request.target === 'detail-heading' && schedule) {
      focusRecurringElement(document.querySelector<HTMLElement>('[data-recurring-detail-heading]'));
    } else if (request.target === 'sidebar-row' && listDisplayMode === 'sidebar') {
      focusRecurringElement(
        findRecurringScheduleFocusElement('data-recurring-sidebar-row-focus', definitionId),
      );
    }
  }, [definitionId, listDisplayMode, schedule, sidebarSchedules]);

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
      <RecurringScheduleWorkspace
        definitionId={definitionId}
        listDisplayMode={listDisplayMode}
        schedules={sidebarSchedules}
        isLoading={isSidebarLoading}
        error={sidebarError}
        onRetry={onSidebarRetry}
      >
        <div className="schedules-page stack">
          <p className="loading">Loading recurring schedule...</p>
        </div>
      </RecurringScheduleWorkspace>
    );
  }

  if (detailQuery.isError || !schedule) {
    return (
      <RecurringScheduleWorkspace
        definitionId={definitionId}
        listDisplayMode={listDisplayMode}
        schedules={sidebarSchedules}
        isLoading={isSidebarLoading}
        error={sidebarError}
        onRetry={onSidebarRetry}
      >
        <div className="schedules-page stack">
          <a href="/schedules" className="secondary">Back to recurring schedules</a>
          <div className="schedules-error" role="alert">{errorMessage(detailQuery.error, 'Schedule not found')}</div>
        </div>
      </RecurringScheduleWorkspace>
    );
  }

  return (
    <RecurringScheduleWorkspace
      definitionId={definitionId}
      listDisplayMode={listDisplayMode}
      schedules={sidebarSchedules}
      isLoading={isSidebarLoading}
      error={sidebarError}
      pinnedSchedule={schedule}
      onRetry={onSidebarRetry}
    >
    <EntityDetailFrame entity="recurring">
    <div className="schedules-page schedules-detail-page stack">
      <header className="toolbar schedules-toolbar">
        <div className="schedules-detail-title">
          <nav className="page-meta" aria-label="Breadcrumb">
            <a href="/schedules">Recurring</a>
            <span>/</span>
            <span>{schedule.name}</span>
          </nav>
          <h2 className="page-title" tabIndex={-1} data-recurring-detail-heading>{schedule.name}</h2>
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

      <section id="schedule-overview" className="schedules-summary-grid" aria-label="Schedule detail summary">
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

      <nav className="entity-detail-frame__tabs" aria-label="Schedule detail sections">
        <a href="#schedule-overview">Overview</a>
        <a href="#schedule-runs">Runs</a>
        <a href="#schedule-configuration">Configuration</a>
      </nav>

      <div className="schedules-detail-grid">
        <section id="schedule-configuration" className="panel--data schedules-detail-panel" aria-label="Schedule configuration">
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

      <section id="schedule-runs" className="panel--data schedules-detail-panel" aria-label="Schedule run history">
        <div className="section-heading-row">
          <h3>Runs</h3>
        </div>
        <DataTable
            data={runs}
            isLoading={runsQuery.isLoading}
            loadingMessage={
              <LoadingPlaceholder
                surface="schedules"
                region="runs"
                variant="table"
                density="compact"
                preserveContext
              />
            }
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
    </EntityDetailFrame>
    </RecurringScheduleWorkspace>
  );
}

function RecurringScheduleMobileList({
  schedules,
  isLoading,
  isError,
  error,
  emptyMessage,
}: {
  schedules: Schedule[];
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  emptyMessage: string;
}) {
  if (isLoading) {
    return (
      <div className="schedules-mobile-card-list" aria-busy="true">
        <LoadingPlaceholder
          surface="schedules"
          region="mobile-list"
          variant="table"
          density="compact"
          preserveContext
        />
      </div>
    );
  }
  if (isError) {
    return (
      <div className="schedules-mobile-card-list schedules-mobile-card-list-state" role="alert">
        {errorMessage(error, 'Failed to fetch schedules')}
      </div>
    );
  }
  if (schedules.length === 0) {
    return (
      <div className="schedules-mobile-card-list schedules-mobile-card-list-state">
        {emptyMessage}
      </div>
    );
  }
  return (
    <ul className="schedules-mobile-card-list" aria-label="Recurring schedule cards">
      {schedules.map((schedule) => (
        <li key={schedule.id} className="schedules-mobile-card">
          <a className="schedules-mobile-card-link" href={`/schedules/${encodeURIComponent(schedule.id)}`}>
            <span className="schedules-mobile-card-title">{schedule.name}</span>
            <span className={`schedules-state schedules-state--${scheduleState(schedule)}`}>
              {stateLabel(schedule)}
            </span>
          </a>
          <dl className="schedules-mobile-card-facts">
            <div>
              <dt>Cadence</dt>
              <dd>
                <code>{schedule.cron}</code>
                <span>{displayValue(schedule.timezone)}</span>
              </dd>
            </div>
            <div>
              <dt>Next run</dt>
              <dd>{formatWhen(schedule.nextRunAt)}</dd>
            </div>
            <div>
              <dt>Target</dt>
              <dd>
                <strong>{targetKind(schedule)}</strong>
                <span>{targetRepository(schedule)}</span>
              </dd>
            </div>
            <div>
              <dt>Dispatch</dt>
              <dd>{dispatchAttentionLabel(schedule)}</dd>
            </div>
          </dl>
        </li>
      ))}
    </ul>
  );
}

function useSchedulesMobileLayout(): boolean {
  const [isMobile, setIsMobile] = useState(false);

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
      return;
    }
    const query = window.matchMedia(SCHEDULES_MOBILE_MEDIA_QUERY);
    const update = (event: MediaQueryList | MediaQueryListEvent) => setIsMobile(event.matches);
    update(query);
    query.addEventListener?.('change', update);
    return () => query.removeEventListener?.('change', update);
  }, []);

  return isMobile;
}

function ScheduleRowActions({
  schedule,
  payload,
  sources,
}: {
  schedule: Schedule;
  payload: BootPayload;
  sources: ScheduleSources | undefined;
}) {
  const queryClient = useQueryClient();
  const availability = scheduleActionAvailability(schedule, sources);
  const runNowEndpoint = scheduleEndpoint(payload, 'runNow', schedule.id);
  const updateEndpoint = scheduleEndpoint(payload, 'update', schedule.id);

  const invalidateList = () =>
    queryClient.invalidateQueries({ queryKey: ['schedules'] });

  const runNowMutation = useMutation({
    mutationFn: async () => {
      const response = await fetch(runNowEndpoint, { method: 'POST', credentials: 'include' });
      if (!response.ok) {
        throw new Error(await responseErrorMessage(response, 'Failed to run schedule'));
      }
      return ScheduleRunSchema.parse(await response.json());
    },
    onSuccess: () => invalidateList(),
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
        throw new Error(
          await responseErrorMessage(response, `Failed to ${enabled ? 'resume' : 'pause'} schedule`),
        );
      }
      return ScheduleSchema.parse(await response.json());
    },
    onSuccess: () => invalidateList(),
  });

  const busy = runNowMutation.isPending || pauseResumeMutation.isPending;
  const pauseLabel = schedule.enabled ? 'Pause' : 'Resume';

  // Safe, common row actions only. Destructive delete stays on detail
  // (docs/UI/RecurringSchedulesPage.md#s21) with confirmation + permission handling.
  return (
    <div className="schedules-row-actions">
      <button
        type="button"
        className="secondary"
        onClick={() => runNowMutation.mutate()}
        disabled={busy || !availability.canRun}
        {...(runNowMutation.isError
          ? { title: errorMessage(runNowMutation.error, 'Failed to run schedule') }
          : availability.canRun
          ? {}
          : { title: availability.runReason })}
        aria-label={`Run ${schedule.name} now`}
      >
        {runNowMutation.isPending ? 'Running' : 'Run now'}
      </button>
      <button
        type="button"
        className="secondary"
        onClick={() => pauseResumeMutation.mutate(!schedule.enabled)}
        disabled={busy || !availability.canEdit}
        {...(pauseResumeMutation.isError
          ? {
              title: errorMessage(
                pauseResumeMutation.error,
                `Failed to ${schedule.enabled ? 'pause' : 'resume'} schedule`,
              ),
            }
          : availability.canEdit
          ? {}
          : { title: availability.editReason })}
        aria-label={`${pauseLabel} ${schedule.name}`}
      >
        {pauseResumeMutation.isPending ? 'Updating' : pauseLabel}
      </button>
    </div>
  );
}

function RecurringFilterForm({
  filters,
  onChange,
}: {
  filters: RecurringFilters;
  onChange: (key: RecurringFilterKey, value: string) => void;
}) {
  return (
    <div className="manifests-filter-grid schedules-filter-grid">
      {(Object.keys(RECURRING_FILTER_LABELS) as RecurringFilterKey[]).map((key) => (
        <label className="workflow-list-filter-control" key={key}>
          <span>{RECURRING_FILTER_LABELS[key]}</span>
          <input
            value={filters[key]}
            onChange={(event) => onChange(key, event.currentTarget.value)}
            aria-label={`${RECURRING_FILTER_LABELS[key]} filter value`}
          />
        </label>
      ))}
    </div>
  );
}

function RecurringColumnFilter({
  filterKey,
  filters,
  openFilter,
  setOpenFilter,
  setFilter,
}: {
  filterKey: RecurringFilterKey;
  filters: RecurringFilters;
  openFilter: RecurringFilterKey | null;
  setOpenFilter: (key: RecurringFilterKey | null) => void;
  setFilter: (key: RecurringFilterKey, value: string) => void;
}) {
  const label = RECURRING_FILTER_LABELS[filterKey];
  const active = Boolean(filters[filterKey].trim());
  const expanded = openFilter === filterKey;
  return (
    <WorkflowColumnHeader
      label={label}
      filterButton={
        <WorkflowColumnFilterButton
          active={active}
          expanded={expanded}
          ariaLabel={active ? `${label} filter: ${filters[filterKey]}` : `${label} filter. No filter applied.`}
          onClick={() => setOpenFilter(expanded ? null : filterKey)}
        />
      }
    >
      {expanded ? (
        <div className="workflow-list-column-filter-popover" role="dialog" aria-label={`${label} filter`}>
          <div className="workflow-list-column-filter-title">{label} filter</div>
          <label className="workflow-list-filter-control">
            <span>{label}</span>
            <input
              autoFocus
              value={filters[filterKey]}
              onChange={(event) => setFilter(filterKey, event.currentTarget.value)}
              aria-label={`${label} filter value`}
            />
          </label>
        </div>
      ) : null}
    </WorkflowColumnHeader>
  );
}

export function SchedulesPage({ payload }: { payload: BootPayload }) {
  const routeDefinitionId = useMemo(() => scheduleRouteDefinitionId(payload), [payload]);
  const listDisplayMode = recurringListDisplayMode(payload, Boolean(routeDefinitionId));

  const initialParams = useMemo(() => safeRecurringSearchParams(), []);
  const [filters, setFilters] = useState<RecurringFilters>(() => parseRecurringFilters(initialParams));
  const [pageSize, setPageSize] = useState(() => parsePageSize(initialParams.get('limit')));
  const [cursor, setCursor] = useState(() => cleanCursor(initialParams.get('cursor')));
  const [cursorStack, setCursorStack] = useState<string[]>([]);
  const [sort, setSort] = useState<RecurringSortKey>(() => parseRecurringSortKey(initialParams.get('sort')));
  const [sortDir, setSortDir] = useState<RecurringSortDirection>(() => parseRecurringSortDirection(initialParams.get('sortDir')));
  const [openFilter, setOpenFilter] = useState<RecurringFilterKey | null>(null);
  const [mobileFiltersOpen, setMobileFiltersOpen] = useState(false);
  const baseListEndpoint = useMemo(() => scheduleListEndpoint(payload), [payload]);
  const listEndpoint = useMemo(
    () => appendScheduleListParams(baseListEndpoint, filters, { pageSize, cursor, sort, sortDir }),
    [baseListEndpoint, cursor, filters, pageSize, sort, sortDir],
  );
  const sources = useMemo(() => scheduleSources(payload), [payload]);
  const hasActiveFilters = useMemo(
    () => activeRecurringFilterEntries(filters).length > 0 || hasActiveScheduleListFilters(baseListEndpoint),
    [baseListEndpoint, filters],
  );
  const emptyMessage = hasActiveFilters
    ? 'No recurring schedules match the current filters.'
    : 'No recurring schedules yet. Create one from the workflow page.';
  const { data, isLoading, isError, error, isFetching, refetch } = useQuery({
    queryKey: ['schedules', listEndpoint],
    enabled: !routeDefinitionId || listDisplayMode === 'sidebar',
    queryFn: async () => {
      const response = await fetch(listEndpoint, { credentials: 'include' });
      if (!response.ok) {
        throw new ScheduleListRequestError(response.status, response.statusText);
      }
      return SchedulesResponseSchema.parse(await response.json());
    },
  });

  const schedules = data?.items || [];
  const activeFilters = activeRecurringFilterEntries(filters);
  const nextCursor = cleanCursor(data?.nextPageToken || null);
  const pageIndex = cursorStack.length;
  const totalCount = typeof data?.count === 'number' ? data.count : schedules.length;
  const stats = useMemo(() => {
    const now = Date.now();
    const active = schedules.filter((schedule) => schedule.enabled).length;
    const attention = schedules.filter((schedule) => scheduleState(schedule) === 'attention').length;
    const dueSoon = schedules.filter((schedule) => isDueSoon(schedule, now)).length;
    return {
      active: typeof data?.activeCount === 'number' ? data.activeCount : active,
      attention: typeof data?.attentionCount === 'number' ? data.attentionCount : attention,
      dueSoon: typeof data?.next24hCount === 'number' ? data.next24hCount : dueSoon,
      total: totalCount,
    };
  }, [data?.activeCount, data?.attentionCount, data?.next24hCount, schedules, totalCount]);
  const isMobileLayout = useSchedulesMobileLayout();

  useEffect(() => {
    if (typeof window === 'undefined' || routeDefinitionId) {
      return;
    }
    const timer = window.setTimeout(() => {
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
    const query = params.toString();
    const nextUrl = `${window.location.pathname}${query ? `?${query}` : ''}`;
    if (`${window.location.pathname}${window.location.search}` !== nextUrl) {
      window.history.replaceState(window.history.state, '', nextUrl);
    }
    }, 250);
    return () => window.clearTimeout(timer);
  }, [cursor, filters, pageSize, routeDefinitionId, sort, sortDir]);

  const setFilter = (key: RecurringFilterKey, value: string) => {
    setFilters((current) => ({ ...current, [key]: value.slice(0, 160) }));
    setCursor('');
    setCursorStack([]);
  };

  const clearFilter = (key: RecurringFilterKey) => {
    setFilter(key, '');
    setOpenFilter(key);
  };

  const changePageSize = (size: number) => {
    setPageSize(size);
    setCursor('');
    setCursorStack([]);
  };

  const changeSort = (key: RecurringSortKey) => {
    setSort((current) => {
      if (current === key) {
        setSortDir((direction) => (direction === 'asc' ? 'desc' : 'asc'));
        return current;
      }
      setSortDir('asc');
      return key;
    });
    setCursor('');
    setCursorStack([]);
  };

  const sortableHeader = (key: RecurringSortKey, label: string) => (
    <button
      type="button"
      className="data-table__sort"
      onClick={() => changeSort(key)}
      aria-label={`${label}. Server sorted ${sort === key ? sortDir : 'not active'}.`}
      title="Sorting is server-authoritative."
    >
      <span>{label}</span>
      <span aria-hidden="true" className="data-table__sort-indicator">
        {sort === key ? (sortDir === 'asc' ? '▲' : '▼') : '↕'}
      </span>
    </button>
  );

  useEffect(() => {
    if (routeDefinitionId) {
      return;
    }
    const request = readRecurringScheduleFocusRequest();
    if (!request || (request.target !== 'table-row' && request.target !== 'table-title')) {
      return;
    }
    if (request.target === 'table-row' && request.definitionId) {
      const rowLink = findRecurringScheduleFocusElement(
        'data-recurring-table-row-focus',
        request.definitionId,
      );
      if (focusRecurringElement(rowLink)) {
        return;
      }
    }
    if (!isLoading) {
      focusRecurringElement(document.querySelector<HTMLElement>('[data-recurring-table-title]'));
    }
  }, [isLoading, routeDefinitionId, schedules]);

  if (routeDefinitionId) {
    return (
      <ScheduleDetailPage
        payload={payload}
        definitionId={routeDefinitionId}
        listDisplayMode={listDisplayMode}
        sidebarSchedules={schedules}
        isSidebarLoading={isLoading}
        sidebarError={isError ? error : null}
        onSidebarRetry={() => void refetch()}
      />
    );
  }

  return (
    <div className="schedules-page stack">
      <header className="toolbar schedules-toolbar">
        <div>
          <h2 className="page-title" tabIndex={-1} data-recurring-table-title>Recurring Schedules</h2>
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
        {isLoading ? (
          <LoadingPlaceholder
            surface="schedules"
            region="summary"
            variant="metric-strip"
            density="compact"
            preserveContext
          />
        ) : (
          <>
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
          </>
        )}
      </section>

      <section className="schedules-table-panel" aria-label="Recurring schedule list">
        <div className="workflow-list-filter-toolbar">
          <button
            type="button"
            className="secondary"
            onClick={() => setMobileFiltersOpen(true)}
            aria-label="Filters"
          >
            Filters
          </button>
          <span className="page-meta">Sorting is server-authoritative.</span>
        </div>
        {activeFilters.length > 0 ? (
          <div className="workflow-list-filter-chips" aria-label="Active filters" aria-live="polite">
            {activeFilters.map(([key, value]) => (
              <span className="workflow-list-filter-chip" key={key}>
                <button
                  type="button"
                  className="workflow-list-filter-chip-open"
                  onClick={() => {
                    setOpenFilter(key);
                    setMobileFiltersOpen(true);
                  }}
                  aria-label={`${RECURRING_FILTER_LABELS[key]} filter: ${value}`}
                >
                  <strong>{RECURRING_FILTER_LABELS[key]}</strong>: {value}
                </button>
                <button
                  type="button"
                  className="workflow-list-filter-chip-remove"
                  onClick={() => clearFilter(key)}
                  aria-label={`Remove ${RECURRING_FILTER_LABELS[key]} filter`}
                >
                  x
                </button>
              </span>
            ))}
          </div>
        ) : null}
        {mobileFiltersOpen ? (
          <div className="workflow-list-advanced-filter-backdrop" role="presentation">
            <div className="workflow-list-advanced-filter-drawer" role="dialog" aria-modal="true" aria-label="Recurring filters">
              <div className="section-heading-row">
                <h3>Filters</h3>
                <button type="button" className="secondary" onClick={() => setMobileFiltersOpen(false)} aria-label="Close filters">
                  Close
                </button>
              </div>
              <RecurringFilterForm filters={filters} onChange={setFilter} />
              <div className="workflow-list-filter-actions">
                <button
                  type="button"
                  className="secondary"
                  onClick={() => {
                    setFilters(EMPTY_RECURRING_FILTERS);
                    setCursor('');
                    setCursorStack([]);
                  }}
                >
                  Reset filters
                </button>
                <button type="button" className="button" onClick={() => setMobileFiltersOpen(false)}>
                  Apply filters
                </button>
              </div>
            </div>
          </div>
        ) : null}
        {isMobileLayout ? (
          <RecurringScheduleMobileList
            schedules={schedules}
            isLoading={isLoading}
            isError={isError}
            error={error}
            emptyMessage={emptyMessage}
          />
        ) : null}
        <DataTable
            data={schedules}
            isLoading={isLoading}
            loadingMessage={
              <LoadingPlaceholder
                surface="schedules"
                region="list"
                variant="table"
                density="compact"
                preserveContext
              />
            }
            isError={isError}
            errorMessage={errorMessage(error, 'Failed to fetch schedules')}
            columns={[
              {
                key: 'name',
                header: (
                  <RecurringColumnFilter
                    filterKey="schedule"
                    filters={filters}
                    openFilter={openFilter}
                    setOpenFilter={setOpenFilter}
                    setFilter={setFilter}
                  />
                ),
                render: (item) => (
                  <div className="schedules-primary-cell">
                    <a
                      href={`/schedules/${encodeURIComponent(item.id)}`}
                      data-recurring-table-row-focus={item.id}
                    >
                      {item.name}
                    </a>
                    <span title={item.id}>{compactId(item.id)}</span>
                  </div>
                ),
              },
              {
                key: 'enabled',
                header: (
                  <RecurringColumnFilter
                    filterKey="state"
                    filters={filters}
                    openFilter={openFilter}
                    setOpenFilter={setOpenFilter}
                    setFilter={setFilter}
                  />
                ),
                render: (item) => (
                  <span className={`schedules-state schedules-state--${scheduleState(item)}`}>
                    {stateLabel(item)}
                  </span>
                ),
              },
              {
                key: 'target',
                header: (
                  <RecurringColumnFilter
                    filterKey="target"
                    filters={filters}
                    openFilter={openFilter}
                    setOpenFilter={setOpenFilter}
                    setFilter={setFilter}
                  />
                ),
                render: (item) => (
                  <div className="schedules-secondary-cell">
                    <strong>{targetKind(item)}</strong>
                    <span>{targetRepository(item)}</span>
                  </div>
                ),
              },
              {
                key: 'cron',
                header: (
                  <RecurringColumnFilter
                    filterKey="cadence"
                    filters={filters}
                    openFilter={openFilter}
                    setOpenFilter={setOpenFilter}
                    setFilter={setFilter}
                  />
                ),
                render: (item) => (
                  <div className="schedules-secondary-cell">
                    <code>{item.cron}</code>
                    <span>{displayValue(item.timezone)}</span>
                  </div>
                ),
              },
              {
                key: 'nextRunAt',
                header: sortableHeader('nextRunAt', 'Next Run'),
                render: (item) => formatWhen(item.nextRunAt),
              },
              {
                key: 'lastScheduledFor',
                header: sortableHeader('lastScheduledFor', 'Last Scheduled'),
                render: (item) => formatWhen(item.lastScheduledFor),
              },
              {
                key: 'lastDispatchStatus',
                header: (
                  <RecurringColumnFilter
                    filterKey="dispatch"
                    filters={filters}
                    openFilter={openFilter}
                    setOpenFilter={setOpenFilter}
                    setFilter={setFilter}
                  />
                ),
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
              {
                key: 'updatedAt',
                header: sortableHeader('updatedAt', 'Updated'),
                render: (item) => formatWhen(item.updatedAt),
              },
            ]}
            rowActions={(item) => (
              <ScheduleRowActions
                schedule={item}
                payload={payload}
                sources={sources}
              />
            )}
            emptyMessage={emptyMessage}
            getRowKey={(item) => item.id}
            ariaLabel="Recurring schedules"
          />
        <div className="queue-pagination workflow-list-footer-pagination">
          <PageSizeSelector pageSize={pageSize} onPageSizeChange={changePageSize} disabled={isLoading || isFetching} />
          <span className="workflow-list-footer-page-summary">
            Page {pageIndex + 1} · {schedules.length} shown{typeof data?.count === 'number' ? ` · ${totalCount} total` : ''}
          </span>
          <button
            type="button"
            className="secondary queue-pagination-button"
            disabled={cursorStack.length === 0 || isLoading || isFetching}
            onClick={() => {
              const previousStack = cursorStack.slice(0, -1);
              setCursor(cursorStack[cursorStack.length - 1] || '');
              setCursorStack(previousStack);
            }}
          >
            Previous
          </button>
          <button
            type="button"
            className="secondary queue-pagination-button"
            disabled={!nextCursor || isLoading || isFetching}
            onClick={() => {
              setCursorStack((current) => [...current, cursor]);
              setCursor(nextCursor);
            }}
          >
            Next
          </button>
        </div>
      </section>
    </div>
  );
}
export default SchedulesPage;
