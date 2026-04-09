import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import Anser from 'anser';
import { Virtuoso } from 'react-virtuoso';
import { z } from 'zod';
import { BootPayload } from '../boot/parseBootPayload';
import { executionStatusPillClasses } from '../utils/executionStatusPillClasses';
import { SkillProvenanceBadge } from '../components/skills/SkillProvenanceBadge';
import { formatRuntimeLabel } from '../utils/formatters';

type DashboardConfig = {
  pollIntervalsMs?: { list?: number; detail?: number; events?: number };
  features?: {
    temporalDashboard?: {
      actionsEnabled?: boolean;
      debugFieldsEnabled?: boolean;
    };
    logStreamingEnabled?: boolean;
    liveLogsSessionTimelineEnabled?: boolean;
    liveLogsSessionTimelineRollout?: string;
  };
  sources?: {
    temporal?: Record<string, string>;
    taskRuns?: Record<string, string>;
  };
};

const GITHUB_PULL_REQUEST_PATH_PATTERN = /^\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+\/pull\/\d+$/i;
const SESSION_PROJECTION_POLL_MS = 5000;

export function getSessionProjectionRefetchInterval(
  isTerminal: boolean,
  hasProjection: boolean,
  hasError: boolean,
): number | false {
  if (isTerminal || hasProjection || hasError) {
    return false;
  }
  return SESSION_PROJECTION_POLL_MS;
}

function normalizeGitHubPullRequestUrl(value: string | null | undefined): string | null {
  if (!value) {
    return null;
  }

  try {
    const parsed = new URL(value);
    if (parsed.protocol !== 'https:' || parsed.hostname.toLowerCase() !== 'github.com') {
      return null;
    }

    const normalizedPath = parsed.pathname.replace(/\/+$/, '');
    if (!GITHUB_PULL_REQUEST_PATH_PATTERN.test(normalizedPath)) {
      return null;
    }

    return `https://github.com${normalizedPath}`;
  } catch {
    return null;
  }
}

const DependencyOutcomeSchema = z
  .object({
    workflowId: z.string(),
    terminalState: z.string().nullable().optional(),
    closeStatus: z.string().nullable().optional(),
    resolvedAt: z.string().nullable().optional(),
    failureCategory: z.string().nullable().optional(),
    message: z.string().nullable().optional(),
  })
  .passthrough();

const DependencySummarySchema = z
  .object({
    workflowId: z.string(),
    title: z.string().nullable().optional(),
    summary: z.string().nullable().optional(),
    state: z.string().nullable().optional(),
    closeStatus: z.string().nullable().optional(),
    workflowType: z.string().nullable().optional(),
  })
  .passthrough();

const ExecutionDetailSchema = z
  .object({
    taskId: z.string(),
    workflowId: z.string().optional(),
    namespace: z.string(),
    temporalRunId: z.string().optional(),
    runId: z.string().optional(),
    source: z.string(),
    workflowType: z.string().optional(),
    entry: z.string().optional(),
    title: z.string(),
    summary: z.string(),
    status: z.string(),
    state: z.string(),
    rawState: z.string().optional(),
    temporalStatus: z.string().optional(),
    closeStatus: z.string().nullable().optional(),
    waitingReason: z.string().nullable().optional(),
    dependsOn: z.array(z.string()).default([]),
    hasDependencies: z.boolean().optional(),
    dependencyWaitOccurred: z.boolean().optional(),
    dependencyWaitDurationMs: z.number().nullable().optional(),
    dependencyResolution: z.string().nullable().optional(),
    failedDependencyId: z.string().nullable().optional(),
    blockedOnDependencies: z.boolean().optional(),
    dependencyOutcomes: z.array(DependencyOutcomeSchema).default([]),
    prerequisites: z.array(DependencySummarySchema).default([]),
    dependents: z.array(DependencySummarySchema).default([]),
    attentionRequired: z.boolean().optional(),
    targetRuntime: z.string().nullable().optional(),
    targetSkill: z.string().nullable().optional(),
    model: z.string().nullable().optional(),
    profileId: z.string().nullable().optional(),
    providerId: z.string().nullable().optional(),
    providerLabel: z.string().nullable().optional(),
    effort: z.string().nullable().optional(),
    startingBranch: z.string().nullable().optional(),
    targetBranch: z.string().nullable().optional(),
    repository: z.string().nullable().optional(),
    prUrl: z.string().nullable().optional(),
    resolvedSkillsetRef: z.string().nullable().optional(),
    taskSkills: z.array(z.string()).nullable().optional(),
    publishMode: z.string().nullable().optional(),
    summaryArtifactRef: z.string().nullable().optional(),
    summary_artifact_ref: z.string().nullable().optional(),
    scheduledFor: z.string().nullable().optional(),
    createdAt: z.string(),
    startedAt: z.string().nullable().optional(),
    updatedAt: z.string().optional(),
    closedAt: z.string().nullable().optional(),
    taskRunId: z.string().nullable().optional(),
    task_run_id: z.string().nullable().optional(),
    stepsHref: z.string().nullable().optional(),
    debugFields: z
      .object({
        workflowId: z.string().optional(),
        temporalRunId: z.string().optional(),
        namespace: z.string().optional(),
        temporalStatus: z.string().optional(),
        rawState: z.string().optional(),
        closeStatus: z.string().nullable().optional(),
        waitingReason: z.string().nullable().optional(),
        attentionRequired: z.boolean().optional(),
      })
      .passthrough()
      .nullable()
      .optional(),
    actions: z
      .object({
        canSetTitle: z.boolean().optional(),
        canRerun: z.boolean().optional(),
        canApprove: z.boolean().optional(),
        canPause: z.boolean().optional(),
        canResume: z.boolean().optional(),
        canCancel: z.boolean().optional(),
        canReject: z.boolean().optional(),
        canSendMessage: z.boolean().optional(),
        disabledReasons: z.record(z.string(), z.string()).optional(),
      })
      .passthrough()
      .optional(),
    interventionAudit: z
      .array(
        z
          .object({
            action: z.string(),
            transport: z.string(),
            summary: z.string(),
            detail: z.string().nullable().optional(),
            createdAt: z.string(),
          })
          .passthrough(),
      )
      .default([]),
  })
  .passthrough();

const ArtifactSummarySchema = z
  .object({
    artifactId: z.string(),
    contentType: z.string().nullable().optional(),
    sizeBytes: z.number().nullable().optional(),
    status: z.string().optional(),
    downloadUrl: z.string().nullable().optional(),
  })
  .passthrough();

const ArtifactRefSummarySchema = z
  .object({
    artifact_id: z.string(),
  })
  .passthrough();

const ArtifactSessionProjectionSchema = z.object({
  task_run_id: z.string(),
  session_id: z.string(),
  session_epoch: z.number(),
  grouped_artifacts: z
    .array(
      z.object({
        group_key: z.string(),
        title: z.string(),
        artifacts: z
          .array(
            z
              .object({
                artifact_id: z.string().optional(),
                artifactId: z.string().optional(),
                status: z.string().optional(),
              })
              .passthrough()
              .transform((artifact) =>
                ArtifactSummarySchema.parse({
                  ...artifact,
                  artifactId: artifact.artifactId ?? artifact.artifact_id,
                }),
              ),
          )
          .default([]),
      }),
    )
    .default([]),
  latest_summary_ref: ArtifactRefSummarySchema.nullable().optional(),
  latest_checkpoint_ref: ArtifactRefSummarySchema.nullable().optional(),
  latest_control_event_ref: ArtifactRefSummarySchema.nullable().optional(),
  latest_reset_boundary_ref: ArtifactRefSummarySchema.nullable().optional(),
});

const ArtifactSessionControlResponseSchema = z.object({
  action: z.enum(['send_follow_up', 'clear_session']),
  projection: ArtifactSessionProjectionSchema,
});

const SessionSnapshotSchema = z
  .object({
    sessionId: z.string(),
    sessionEpoch: z.number(),
    containerId: z.string(),
    threadId: z.string(),
    activeTurnId: z.string().nullable().optional(),
    status: z.string().optional(),
    latestSummaryRef: z.string().nullable().optional(),
    latestCheckpointRef: z.string().nullable().optional(),
    latestControlEventRef: z.string().nullable().optional(),
    latestResetBoundaryRef: z.string().nullable().optional(),
  })
  .passthrough();

const ObservabilitySummarySchema = z.object({
  supportsLiveStreaming: z.boolean().default(false),
  liveStreamStatus: z.string().default('unavailable'),
  status: z.string().default(''),
  sessionSnapshot: SessionSnapshotSchema.nullable().optional(),
});

const ObservabilityEventSchema = z
  .object({
    sequence: z.number(),
    timestamp: z.string(),
    stream: z.enum(['stdout', 'stderr', 'system', 'session']),
    text: z.string(),
    offset: z.number().nullable().optional(),
    kind: z.string().nullable().optional(),
    session_id: z.string().nullable().optional(),
    session_epoch: z.number().nullable().optional(),
    container_id: z.string().nullable().optional(),
    thread_id: z.string().nullable().optional(),
    turn_id: z.string().nullable().optional(),
    active_turn_id: z.string().nullable().optional(),
    metadata: z.record(z.string(), z.unknown()).optional(),
  })
  .passthrough();

const ObservabilityEventsResponseSchema = z.object({
  events: z.array(ObservabilityEventSchema).default([]),
  truncated: z.boolean().default(false),
  sessionSnapshot: SessionSnapshotSchema.nullable().optional(),
});

const ArtifactListSchema = z.object({
  artifacts: z
    .array(
      z
        .object({
          artifactId: z.string().optional(),
          artifact_id: z.string().optional(),
          contentType: z.string().nullable().optional(),
          content_type: z.string().nullable().optional(),
          sizeBytes: z.number().nullable().optional(),
          size_bytes: z.number().nullable().optional(),
          status: z.string().optional(),
          downloadUrl: z.string().nullable().optional(),
          download_url: z.string().nullable().optional(),
        })
        .passthrough()
        .transform((artifact) =>
          ArtifactSummarySchema.parse({
            ...artifact,
            artifactId: artifact.artifactId ?? artifact.artifact_id,
            contentType: artifact.contentType ?? artifact.content_type ?? null,
            sizeBytes: artifact.sizeBytes ?? artifact.size_bytes ?? null,
            downloadUrl: artifact.downloadUrl ?? artifact.download_url ?? null,
          }),
        ),
    )
    .default([]),
});

const StepLedgerToolSchema = z
  .object({
    type: z.string().nullable().optional(),
    name: z.string().nullable().optional(),
    version: z.string().nullable().optional(),
  })
  .passthrough();

const StepLedgerCheckSchema = z
  .object({
    kind: z.string(),
    status: z.string(),
    summary: z.string().nullable().optional(),
    retryCount: z.number().default(0),
    artifactRef: z.string().nullable().optional(),
  })
  .passthrough();

const StepLedgerRefsSchema = z
  .object({
    childWorkflowId: z.string().nullable().optional(),
    childRunId: z.string().nullable().optional(),
    taskRunId: z.string().nullable().optional(),
  })
  .default({
    childWorkflowId: null,
    childRunId: null,
    taskRunId: null,
  });

const StepLedgerArtifactsSchema = z
  .object({
    outputSummary: z.string().nullable().optional(),
    outputPrimary: z.string().nullable().optional(),
    runtimeStdout: z.string().nullable().optional(),
    runtimeStderr: z.string().nullable().optional(),
    runtimeMergedLogs: z.string().nullable().optional(),
    runtimeDiagnostics: z.string().nullable().optional(),
    providerSnapshot: z.string().nullable().optional(),
  })
  .default({
    outputSummary: null,
    outputPrimary: null,
    runtimeStdout: null,
    runtimeStderr: null,
    runtimeMergedLogs: null,
    runtimeDiagnostics: null,
    providerSnapshot: null,
  });

const StepLedgerRowSchema = z
  .object({
    logicalStepId: z.string(),
    order: z.number(),
    title: z.string(),
    tool: StepLedgerToolSchema.default({}),
    dependsOn: z.array(z.string()).default([]),
    status: z.string(),
    waitingReason: z.string().nullable().optional(),
    attentionRequired: z.boolean().optional(),
    attempt: z.number().default(0),
    startedAt: z.string().nullable().optional(),
    updatedAt: z.string().nullable().optional(),
    summary: z.string().nullable().optional(),
    checks: z.array(StepLedgerCheckSchema).default([]),
    refs: StepLedgerRefsSchema,
    artifacts: StepLedgerArtifactsSchema,
    lastError: z.unknown().nullable().optional(),
  })
  .passthrough();

const StepLedgerSnapshotSchema = z.object({
  workflowId: z.string(),
  runId: z.string(),
  runScope: z.string().default('latest'),
  steps: z.array(StepLedgerRowSchema).default([]),
});

const RunSummaryArtifactSchema = z
  .object({
    finishOutcome: z
      .object({
        code: z.string().optional(),
        stage: z.string().optional(),
        reason: z.string().optional(),
      })
      .passthrough()
      .optional(),
    publish: z
      .object({
        mode: z.string().optional(),
        status: z.string().optional(),
        reason: z.string().optional(),
      })
      .passthrough()
      .optional(),
    operatorSummary: z.string().nullable().optional(),
    nextAction: z.string().nullable().optional(),
    lastStep: z
      .object({
        id: z.string().nullable().optional(),
        summary: z.string().nullable().optional(),
        diagnosticsRef: z.string().nullable().optional(),
      })
      .passthrough()
      .optional(),
    publishContext: z
      .object({
        branch: z.string().nullable().optional(),
        baseRef: z.string().nullable().optional(),
        commitCount: z.union([z.number(), z.string()]).nullable().optional(),
        pullRequestUrl: z.string().nullable().optional(),
      })
      .passthrough()
      .optional(),
  })
  .passthrough();

function readDashboardConfig(payload: BootPayload): DashboardConfig | undefined {
  const raw = payload.initialData as { dashboardConfig?: DashboardConfig } | undefined;
  return raw?.dashboardConfig;
}

function decodeTaskPathSegment(segment: string | null | undefined): string | null {
  if (!segment) return null;
  try {
    return decodeURIComponent(segment);
  } catch {
    return segment;
  }
}

export function expandRouteTemplate(
  template: string | null | undefined,
  params: Record<string, string | null | undefined>,
): string | null {
  if (!template) return null;
  let path = template;
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined) {
      return null;
    }
    path = path.replaceAll(`{${key}}`, encodeURIComponent(value));
  }
  return path.includes('{') && path.includes('}') ? null : path;
}

function joinApiBasePath(apiBase: string, path: string): string {
  const base = apiBase.replace(/\/+$/g, '');
  const suffix = path.startsWith('/') ? path : `/${path}`;
  return `${base}${suffix}`;
}

function resolveApiBaseTemplate(apiBase: string, expandedTemplate: string): string {
  const template = expandedTemplate.trim();
  if (!template) return template;
  if (/^[a-z][a-z\d+.-]*:\/\//i.test(template)) return template;

  const normalizedApiBase = apiBase.replace(/\/+$/g, '');
  if (!normalizedApiBase) return template;
  if (template.startsWith(normalizedApiBase)) return template;

  if (template === '/api') {
    return normalizedApiBase;
  }
  if (template.startsWith('/api/')) {
    return joinApiBasePath(normalizedApiBase, template.slice('/api'.length));
  }
  return joinApiBasePath(normalizedApiBase, template);
}

function taskRunRoute(
  apiBase: string,
  template: string | null | undefined,
  fallback: string,
  params: Record<string, string | null | undefined>,
): string {
  const expandedTemplate = expandRouteTemplate(template, params);
  if (expandedTemplate) {
    return resolveApiBaseTemplate(apiBase, expandedTemplate);
  }
  return joinApiBasePath(apiBase, fallback);
}

function formatWhen(iso: string | null | undefined): string {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString();
}

function Card({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <div className="card">
      <strong>{label}:</strong> <span className="break-words">{children}</span>
    </div>
  );
}

function renderProviderProfileSummary(
  execution: z.infer<typeof ExecutionDetailSchema>,
): ReactNode {
  const providerLabel = execution.providerLabel?.trim();
  const providerId = execution.providerId?.trim();
  const profileId = execution.profileId?.trim();
  const primary = providerLabel || providerId || profileId;
  if (!primary) return '—';

  return (
    <span className="stack gap-1">
      <code className="text-xs break-all">{primary}</code>
      {profileId && profileId !== primary ? (
        <span className="small">
          Profile ID: <code className="text-xs break-all">{profileId}</code>
        </span>
      ) : null}
      {providerId && providerId !== primary ? (
        <span className="small">
          Provider ID: <code className="text-xs break-all">{providerId}</code>
        </span>
      ) : null}
    </span>
  );
}

function formatDurationMs(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  if (value < 1000) return `${value} ms`;
  const totalSeconds = Math.round(value / 1000);
  if (totalSeconds < 60) {
    const seconds = value / 1000;
    return `${seconds.toFixed(seconds >= 10 ? 0 : 1)} s`;
  }
  const minutes = Math.floor(totalSeconds / 60);
  const remainingSeconds = totalSeconds % 60;
  return `${minutes}m ${remainingSeconds}s`;
}

function formatDependencyResolution(value: string | null | undefined): string {
  const normalized = String(value || '').trim();
  if (!normalized) return '—';
  return normalized.replaceAll('_', ' ');
}

function dependencyHref(workflowId: string): string {
  return `/tasks/${encodeURIComponent(workflowId)}?source=temporal`;
}

function formatDebugValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function buildDebugFieldEntries(execution: z.infer<typeof ExecutionDetailSchema>) {
  const debugFields = execution.debugFields || {};
  const primaryEntries: Array<[string, unknown]> = [
    ['Workflow ID', debugFields.workflowId || execution.workflowId || execution.taskId],
    ['Temporal Run ID', debugFields.temporalRunId || execution.temporalRunId || execution.runId],
    ['Namespace', debugFields.namespace || execution.namespace],
    ['Temporal Status', debugFields.temporalStatus || execution.temporalStatus],
    ['Raw State', debugFields.rawState || execution.rawState || execution.state],
    ['Close Status', debugFields.closeStatus ?? execution.closeStatus],
    ['Waiting Reason', debugFields.waitingReason ?? execution.waitingReason],
    ['Attention Required', debugFields.attentionRequired ?? execution.attentionRequired],
  ];
  const knownKeys = new Set([
    'workflowId',
    'temporalRunId',
    'namespace',
    'temporalStatus',
    'rawState',
    'closeStatus',
    'waitingReason',
    'attentionRequired',
  ]);
  const extraEntries = Object.entries(debugFields)
    .filter(([key]) => !knownKeys.has(key))
    .map(([key, value]) => [key, value] as [string, unknown]);
  return [...primaryEntries, ...extraEntries];
}

const TERMINAL_STATES = new Set(['succeeded', 'failed', 'canceled', 'cancelled', 'completed']);

type LogViewerState = 'not_available' | 'starting' | 'live' | 'ended' | 'error';

class ObservabilityRequestError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = 'ObservabilityRequestError';
    this.status = status;
  }
}

function buildObservabilityRequestError(status: number): ObservabilityRequestError {
  if (status === 403) {
    return new ObservabilityRequestError(status, 'You do not have permission to view observability for this run.');
  }
  return new ObservabilityRequestError(status, `Observability request failed: ${status}`);
}

async function fetchMergedTail(
  apiBase: string,
  taskRunId: string,
  routeTemplate?: string | null,
): Promise<string> {
  const resp = await fetch(
    taskRunRoute(apiBase, routeTemplate, `/task-runs/${encodeURIComponent(taskRunId)}/logs/merged`, {
      taskRunId,
    }),
    { credentials: 'include' },
  );
  if (!resp.ok) {
    if (resp.status === 404) return '';
    throw buildObservabilityRequestError(resp.status);
  }
  return resp.text();
}

async function fetchStream(
  apiBase: string,
  taskRunId: string,
  stream: 'stdout' | 'stderr',
  routeTemplate?: string | null,
): Promise<string> {
  const resp = await fetch(
    taskRunRoute(
      apiBase,
      routeTemplate,
      `/task-runs/${encodeURIComponent(taskRunId)}/logs/${stream}`,
      { taskRunId },
    ),
    { credentials: 'include' },
  );
  if (!resp.ok) {
    if (resp.status === 404) return '';
    throw buildObservabilityRequestError(resp.status);
  }
  return resp.text();
}

async function fetchDiagnostics(
  apiBase: string,
  taskRunId: string,
  routeTemplate?: string | null,
): Promise<string> {
  const resp = await fetch(
    taskRunRoute(
      apiBase,
      routeTemplate,
      `/task-runs/${encodeURIComponent(taskRunId)}/diagnostics`,
      { taskRunId },
    ),
    { credentials: 'include' },
  );
  if (!resp.ok) {
    if (resp.status === 404) return '';
    throw buildObservabilityRequestError(resp.status);
  }
  return resp.text();
}

async function fetchRunSummaryArtifact(
  apiBase: string,
  artifactId: string,
): Promise<z.infer<typeof RunSummaryArtifactSchema> | null> {
  const resp = await fetch(
    `${apiBase}/artifacts/${encodeURIComponent(artifactId)}/download`,
    { credentials: 'include' },
  );
  if (!resp.ok) {
    if (resp.status === 404) return null;
    throw new Error(`Run summary: ${resp.statusText}`);
  }
  const text = await resp.text();
  if (!text.trim()) return null;
  return RunSummaryArtifactSchema.parse(JSON.parse(text));
}

function deriveCodexSessionId(
  taskRunId: string | null | undefined,
  runtimeId: string | null | undefined,
): string | null {
  const normalizedRuntime = String(runtimeId || '').trim().toLowerCase();
  if (!taskRunId || (normalizedRuntime !== 'codex' && normalizedRuntime !== 'codex_cli')) {
    return null;
  }
  return `sess:${taskRunId}:codex_cli`;
}

async function fetchArtifactSessionProjection(
  apiBase: string,
  taskRunId: string,
  sessionId: string,
  routeTemplate?: string | null,
): Promise<z.infer<typeof ArtifactSessionProjectionSchema> | null> {
  const resp = await fetch(
    taskRunRoute(
      apiBase,
      routeTemplate,
      `/task-runs/${encodeURIComponent(taskRunId)}/artifact-sessions/${encodeURIComponent(sessionId)}`,
      { taskRunId, sessionId },
    ),
    { credentials: 'include' },
  );
  if (!resp.ok) {
    if (resp.status === 404) return null;
    throw new Error(`Session continuity: ${resp.status}`);
  }
  return ArtifactSessionProjectionSchema.parse(await resp.json());
}

async function controlArtifactSession(
  apiBase: string,
  taskRunId: string,
  sessionId: string,
  body: { action: 'send_follow_up' | 'clear_session'; message?: string; reason?: string },
  routeTemplate?: string | null,
): Promise<z.infer<typeof ArtifactSessionControlResponseSchema>> {
  const resp = await fetch(
    taskRunRoute(
      apiBase,
      routeTemplate,
      `/task-runs/${encodeURIComponent(taskRunId)}/artifact-sessions/${encodeURIComponent(sessionId)}/control`,
      { taskRunId, sessionId },
    ),
    {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(body),
    },
  );
  if (!resp.ok) {
    throw new Error(`Session control: ${resp.status}`);
  }
  return ArtifactSessionControlResponseSchema.parse(await resp.json());
}

/** Fetch the observability summary for a task run. */
async function fetchObservabilitySummary(
  apiBase: string,
  taskRunId: string,
  routeTemplate?: string | null,
): Promise<z.infer<typeof ObservabilitySummarySchema> | null> {
  const resp = await fetch(
    taskRunRoute(
      apiBase,
      routeTemplate,
      `/task-runs/${encodeURIComponent(taskRunId)}/observability-summary`,
      { taskRunId },
    ),
    { credentials: 'include' },
  );
  if (!resp.ok) {
    if (resp.status === 404) return null;
    throw buildObservabilityRequestError(resp.status);
  }
  const body = (await resp.json()) as { summary: Record<string, unknown> };
  return ObservabilitySummarySchema.parse(body.summary);
}

async function fetchObservabilityEvents(
  apiBase: string,
  taskRunId: string,
  routeTemplate?: string | null,
): Promise<z.infer<typeof ObservabilityEventsResponseSchema> | null> {
  const resp = await fetch(
    taskRunRoute(
      apiBase,
      routeTemplate,
      `/task-runs/${encodeURIComponent(taskRunId)}/observability/events`,
      { taskRunId },
    ),
    { credentials: 'include' },
  );
  if (!resp.ok) {
    if (resp.status === 404) return null;
    throw buildObservabilityRequestError(resp.status);
  }
  return ObservabilityEventsResponseSchema.parse(await resp.json());
}

async function fetchStepLedger(stepsHref: string): Promise<z.infer<typeof StepLedgerSnapshotSchema>> {
  const resp = await fetch(stepsHref, { credentials: 'include' });
  if (!resp.ok) {
    const statusText = resp.statusText.trim();
    const detail = statusText ? ` ${statusText}` : '';
    throw new Error(`Steps: ${resp.status}${detail} (${stepsHref})`);
  }
  return StepLedgerSnapshotSchema.parse(await resp.json());
}

const TERMINAL_RUN_STATUSES = new Set([
  'completed',
  'failed',
  'canceled',
  'cancelled',
  'timed_out',
]);

function usePageVisibility() {
  const [isVisible, setIsVisible] = useState(!document.hidden);

  useEffect(() => {
    const handleVisibilityChange = () => {
      setIsVisible(!document.hidden);
    };
    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () => {
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, []);

  return isVisible;
}

type ObservabilityEvent = z.infer<typeof ObservabilityEventSchema>;
type SessionSnapshot = z.infer<typeof SessionSnapshotSchema>;
type TimelineStream = 'stdout' | 'stderr' | 'system' | 'session' | 'unknown';
type TimelineRow = {
  id: string;
  text: string;
  stream: TimelineStream;
  kind: string | null;
  sequence: number | null;
  timestamp: string | null;
  sessionId: string | null;
  sessionEpoch: number | null;
  containerId: string | null;
  threadId: string | null;
  turnId: string | null;
  activeTurnId: string | null;
  metadata: Record<string, unknown>;
  rowType: 'output' | 'system' | 'session' | 'approval' | 'publication' | 'boundary' | 'fallback';
};

function splitLogText(content: string): string[] {
  if (!content) return [];
  const normalized = content.endsWith('\n') ? content.slice(0, -1) : content;
  return normalized ? normalized.split('\n') : [];
}

function copyTextToClipboard(text: string): void {
  if (
    typeof navigator === 'undefined' ||
    !navigator.clipboard ||
    typeof navigator.clipboard.writeText !== 'function'
  ) {
    return;
  }
  try {
    const maybePromise = navigator.clipboard.writeText(text);
    if (maybePromise && typeof maybePromise.catch === 'function') {
      void maybePromise.catch(() => {});
    }
  } catch {
    // Ignore synchronous clipboard failures for now; the UI should stay stable.
  }
}

function parseArtifactToRows(content: string): TimelineRow[] {
  const lines = splitLogText(content);
  let currentStream: TimelineStream = 'unknown';

  return lines.map((line, i) => {
    if (line.startsWith('--- stdout ---')) currentStream = 'stdout';
    else if (line.startsWith('--- stderr ---')) currentStream = 'stderr';
    else if (line.startsWith('--- system ---')) currentStream = 'system';
    else if (line.startsWith('--- session ---')) currentStream = 'session';

    return {
      id: `artifact-${i}`,
      text: line,
      stream: currentStream,
      kind: null,
      sequence: null,
      timestamp: null,
      sessionId: null,
      sessionEpoch: null,
      containerId: null,
      threadId: null,
      turnId: null,
      activeTurnId: null,
      metadata: {},
      rowType: 'fallback',
    };
  });
}

function classifyTimelineRow(event: ObservabilityEvent): TimelineRow['rowType'] {
  if (event.kind === 'session_reset_boundary') {
    return 'boundary';
  }
  if (event.stream === 'system') {
    return 'system';
  }
  if (event.stream === 'session') {
    if ((event.kind ?? '').startsWith('approval_')) {
      return 'approval';
    }
    if ((event.kind ?? '').endsWith('_published')) {
      return 'publication';
    }
    return 'session';
  }
  return 'output';
}

function eventToTimelineRows(event: ObservabilityEvent): TimelineRow[] {
  const stream = event.stream as TimelineStream;
  const rowType = classifyTimelineRow(event);
  const lines = splitLogText(event.text);
  const sourceLines = lines.length > 0 ? lines : [event.text];
  return sourceLines.map((line, index) => ({
    id: `${event.sequence}-${index}-${event.kind ?? 'event'}`,
    text: line,
    stream,
    kind: event.kind ?? null,
    sequence: event.sequence,
    timestamp: event.timestamp ?? null,
    sessionId: event.session_id ?? null,
    sessionEpoch: event.session_epoch ?? null,
    containerId: event.container_id ?? null,
    threadId: event.thread_id ?? null,
    turnId: event.turn_id ?? null,
    activeTurnId: event.active_turn_id ?? null,
    metadata: event.metadata ?? {},
    rowType,
  }));
}

function mapEventsToTimelineRows(
  payload: z.infer<typeof ObservabilityEventsResponseSchema> | null | undefined,
): TimelineRow[] {
  if (!payload) return [];
  return payload.events.flatMap((event) => eventToTimelineRows(event));
}

function deriveSessionSnapshotFromEvent(
  event: ObservabilityEvent,
  previous: SessionSnapshot | null,
): SessionSnapshot | null {
  if (!event.session_id || typeof event.session_epoch !== 'number') {
    return previous;
  }
  return {
    sessionId: event.session_id,
    sessionEpoch: event.session_epoch,
    containerId: event.container_id ?? previous?.containerId ?? '',
    threadId: event.thread_id ?? previous?.threadId ?? '',
    activeTurnId: event.active_turn_id ?? previous?.activeTurnId ?? null,
    status: previous?.status,
    latestSummaryRef: previous?.latestSummaryRef ?? null,
    latestCheckpointRef: previous?.latestCheckpointRef ?? null,
    latestControlEventRef: previous?.latestControlEventRef ?? null,
    latestResetBoundaryRef: previous?.latestResetBoundaryRef ?? null,
  };
}

function renderAnsiFragments(text: string): ReactNode {
  const fragments = Anser.ansiToJson(text, { json: true, remove_empty: true });
  if (fragments.length === 0) {
    return text;
  }
  return fragments.map((fragment, index) => {
    const style: Record<string, string> = {};
    const foreground = fragment.fg_truecolor || fragment.fg;
    const background = fragment.bg_truecolor || fragment.bg;
    if (foreground) {
      style.color = foreground;
    }
    if (background) {
      style.backgroundColor = background;
    }
    if (fragment.decorations.includes('bold')) {
      style.fontWeight = '700';
    }
    if (fragment.decorations.includes('italic')) {
      style.fontStyle = 'italic';
    }
    const textDecoration = [
      fragment.decorations.includes('underline') ? 'underline' : null,
      fragment.decorations.includes('strikethrough') ? 'line-through' : null,
    ]
      .filter(Boolean)
      .join(' ');
    if (textDecoration) {
      style.textDecoration = textDecoration;
    }
    return (
      <span key={`${fragment.content}-${index}`} data-ansi-fragment="true" style={style}>
        {fragment.content}
      </span>
    );
  });
}

function renderTimelineRowText(row: TimelineRow, timelineViewerEnabled: boolean): ReactNode {
  if (!timelineViewerEnabled) {
    return row.text;
  }
  if (row.stream === 'stdout' || row.stream === 'stderr') {
    return renderAnsiFragments(row.text);
  }
  return row.text;
}

function getCopyableRowText(row: TimelineRow): string {
  if (row.stream === 'stdout' || row.stream === 'stderr') {
    return Anser.ansiToText(row.text, { remove_empty: true });
  }
  return row.text;
}

function renderTimelineRow(
  row: TimelineRow,
  wrapLines: boolean,
  timelineViewerEnabled: boolean,
): ReactNode {
  const rowClasses = [
    'live-logs-row',
    `live-logs-row-${row.rowType}`,
    `live-logs-stream-${row.stream}`,
    wrapLines ? 'is-wrapped' : 'is-unwrapped',
  ].join(' ');

  if (timelineViewerEnabled && row.rowType === 'boundary') {
    return (
      <div
        key={row.id}
        className={rowClasses}
      >
        <div className="live-logs-boundary-label">Session reset boundary</div>
        <div
          className="live-logs-row-text"
          data-stream={row.stream}
          data-kind={row.kind ?? undefined}
          data-row-type={row.rowType}
        >
          {row.text}
        </div>
      </div>
    );
  }

  return (
    <div
      key={row.id}
      className={rowClasses}
    >
      {timelineViewerEnabled && row.kind ? (
        <span className="live-logs-kind-chip">{row.kind.replaceAll('_', ' ')}</span>
      ) : null}
      <div
        className="live-logs-row-text"
        data-stream={row.stream}
        data-kind={row.kind ?? undefined}
        data-row-type={row.rowType}
      >
        {renderTimelineRowText(row, timelineViewerEnabled)}
      </div>
    </div>
  );
}

type TaskRunRouteTemplates = {
  observabilitySummary?: string | undefined;
  observabilityEvents?: string | undefined;
  logsStream?: string | undefined;
  logsStdout?: string | undefined;
  logsStderr?: string | undefined;
  logsMerged?: string | undefined;
  diagnostics?: string | undefined;
  artifactSession?: string | undefined;
  artifactSessionControl?: string | undefined;
};

function readTaskRunRouteTemplates(config: DashboardConfig | undefined): TaskRunRouteTemplates {
  return {
    observabilitySummary: config?.sources?.taskRuns?.observabilitySummary,
    observabilityEvents: config?.sources?.taskRuns?.observabilityEvents,
    logsStream: config?.sources?.taskRuns?.logsStream,
    logsStdout: config?.sources?.taskRuns?.logsStdout,
    logsStderr: config?.sources?.taskRuns?.logsStderr,
    logsMerged: config?.sources?.taskRuns?.logsMerged,
    diagnostics: config?.sources?.taskRuns?.diagnostics,
    artifactSession: config?.sources?.taskRuns?.artifactSession,
    artifactSessionControl: config?.sources?.taskRuns?.artifactSessionControl,
  };
}

function formatStepToolLabel(tool: z.infer<typeof StepLedgerToolSchema>): string {
  const name = String(tool.name || '').trim();
  const type = String(tool.type || '').trim();
  if (name) return name;
  if (type) return type;
  return 'unknown';
}

function formatStepLastError(lastError: unknown): string | null {
  if (!lastError) return null;
  if (typeof lastError === 'string') return lastError;
  if (typeof lastError === 'object') {
    const candidate = (lastError as { summary?: unknown; message?: unknown }).summary
      ?? (lastError as { summary?: unknown; message?: unknown }).message;
    return candidate ? String(candidate) : JSON.stringify(lastError);
  }
  return String(lastError);
}

function stepTerminal(status: string | null | undefined): boolean {
  const normalized = String(status || '').trim().toLowerCase();
  return normalized === 'succeeded'
    || normalized === 'failed'
    || normalized === 'canceled'
    || normalized === 'cancelled'
    || normalized === 'skipped';
}

function stepCheckStatusClass(status: string | null | undefined): string {
  const normalized = String(status || '')
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
  return `check-${normalized || 'unknown'}`;
}

function StepCheckBadge({ check }: { check: z.infer<typeof StepLedgerCheckSchema> }) {
  const checkStatusClass = stepCheckStatusClass(check.status);
  return (
    <span className={`step-check-badge ${checkStatusClass} ${executionStatusPillClasses(check.status)}`}>
      {check.kind.replaceAll('_', ' ')}: {check.status.replaceAll('_', ' ')}
    </span>
  );
}

function StepCheckDetails({ check }: { check: z.infer<typeof StepLedgerCheckSchema> }) {
  return (
    <div className="step-check-details">
      {typeof check.retryCount === 'number' ? (
        <span className="small">Retry count: {check.retryCount}</span>
      ) : null}
      {check.artifactRef ? (
        <span className="small">
          Review artifact: <code className="text-xs break-all">{check.artifactRef}</code>
        </span>
      ) : (
        <span className="small">No review artifact linked yet.</span>
      )}
    </div>
  );
}

function StepArtifactsList({
  artifacts,
}: {
  artifacts: z.infer<typeof StepLedgerArtifactsSchema>;
}) {
  const entries = [
    ['Output summary', artifacts.outputSummary],
    ['Output primary', artifacts.outputPrimary],
    ['Runtime stdout', artifacts.runtimeStdout],
    ['Runtime stderr', artifacts.runtimeStderr],
    ['Runtime merged logs', artifacts.runtimeMergedLogs],
    ['Runtime diagnostics', artifacts.runtimeDiagnostics],
    ['Provider snapshot', artifacts.providerSnapshot],
  ].filter(([, value]) => Boolean(value)) as Array<[string, string]>;

  if (entries.length === 0) {
    return <p className="small">No step artifacts linked yet.</p>;
  }

  return (
    <ul className="step-detail-list">
      {entries.map(([label, value]) => (
        <li key={`${label}-${value}`}>
          <strong>{label}:</strong> <code className="text-xs break-all">{value}</code>
        </li>
      ))}
    </ul>
  );
}

function StepMetadataList({
  row,
  runId,
}: {
  row: z.infer<typeof StepLedgerRowSchema>;
  runId: string;
}) {
  return (
    <ul className="step-detail-list">
      <li><strong>Logical step id:</strong> <code className="text-xs break-all">{row.logicalStepId}</code></li>
      <li><strong>Run id:</strong> <code className="text-xs break-all">{runId}</code></li>
      <li><strong>Tool:</strong> <code className="text-xs break-all">{formatStepToolLabel(row.tool)}</code></li>
      <li><strong>Attempt:</strong> {row.attempt}</li>
      <li><strong>Depends on:</strong> {row.dependsOn.length > 0 ? row.dependsOn.join(', ') : 'None'}</li>
      <li><strong>Child workflow:</strong> {row.refs.childWorkflowId ? <code className="text-xs break-all">{row.refs.childWorkflowId}</code> : '—'}</li>
      <li><strong>Child run:</strong> {row.refs.childRunId ? <code className="text-xs break-all">{row.refs.childRunId}</code> : '—'}</li>
      <li><strong>Task run:</strong> {row.refs.taskRunId ? <code className="text-xs break-all">{row.refs.taskRunId}</code> : '—'}</li>
      <li><strong>Started:</strong> {formatWhen(row.startedAt)}</li>
      <li><strong>Updated:</strong> {formatWhen(row.updatedAt)}</li>
    </ul>
  );
}

function StepObservabilityGroup({
  apiBase,
  logStreamingEnabled,
  sessionTimelineEnabled,
  row,
  routes,
}: {
  apiBase: string;
  logStreamingEnabled: boolean;
  sessionTimelineEnabled: boolean;
  row: z.infer<typeof StepLedgerRowSchema>;
  routes: TaskRunRouteTemplates;
}) {
  if (!logStreamingEnabled) {
    return (
      <p className="small">Live log streaming is disabled in the server dashboard config.</p>
    );
  }

  const taskRunId = row.refs.taskRunId;
  if (!taskRunId) {
    return (
      <p className="small">
        {renderMissingTaskRunCopy(
          row.status === 'running' || row.status === 'awaiting_external'
            ? 'waiting_for_launch'
            : 'binding_missing',
        )}
      </p>
    );
  }

  return (
    <div className="stack">
      <LiveLogsPanel
        apiBase={apiBase}
        taskRunId={taskRunId}
        isTerminal={stepTerminal(row.status)}
        autoExpand
        routes={routes}
        sessionTimelineEnabled={sessionTimelineEnabled}
      />
      <StaticLogPanel
        apiBase={apiBase}
        taskRunId={taskRunId}
        stream="stdout"
        routes={routes}
      />
      <StaticLogPanel
        apiBase={apiBase}
        taskRunId={taskRunId}
        stream="stderr"
        routes={routes}
      />
      <DiagnosticsPanel
        apiBase={apiBase}
        taskRunId={taskRunId}
        routes={routes}
      />
    </div>
  );
}

function StepLedgerRowCard({
  apiBase,
  logStreamingEnabled,
  sessionTimelineEnabled,
  row,
  runId,
  expanded,
  onToggle,
  routes,
}: {
  apiBase: string;
  logStreamingEnabled: boolean;
  sessionTimelineEnabled: boolean;
  row: z.infer<typeof StepLedgerRowSchema>;
  runId: string;
  expanded: boolean;
  onToggle: () => void;
  routes: TaskRunRouteTemplates;
}) {
  const lastError = formatStepLastError(row.lastError);

  return (
    <article className="step-row-card">
      <div className="step-row-header">
        <div className="step-row-header-main">
          <button
            type="button"
            className="step-row-toggle"
            onClick={onToggle}
            aria-expanded={expanded}
            aria-label={expanded ? `Hide details for ${row.title}` : `Show details for ${row.title}`}
          >
            {expanded ? 'Hide details' : 'Show details'}
          </button>
          <div className="step-row-title-block">
            <strong>{row.title}</strong>
            <div className="step-row-meta">
              <code className="text-xs break-all">{row.logicalStepId}</code>
              <code className="text-xs break-all">{formatStepToolLabel(row.tool)}</code>
            </div>
          </div>
        </div>
        <div className="step-row-statuses">
          <span className={executionStatusPillClasses(row.status)}>{row.status.replaceAll('_', ' ')}</span>
          <span className="step-attempt-pill">Attempt {row.attempt}</span>
        </div>
      </div>
      <div className="step-row-summary">
        <p className="small">{row.summary || 'No step summary yet.'}</p>
        {row.checks.length > 0 ? (
          <div className="step-check-badges">
            {row.checks.map((check, index) => (
              <StepCheckBadge key={`${check.kind}-${check.status}-${index}`} check={check} />
            ))}
          </div>
        ) : null}
      </div>
      {expanded ? (
        <div className="step-row-details stack">
          <section className="stack">
            <h4>Summary</h4>
            <p className="small">{row.summary || 'No step summary yet.'}</p>
            {row.waitingReason ? <p className="small">Waiting reason: {row.waitingReason}</p> : null}
            {lastError ? <p className="small">Last error: {lastError}</p> : null}
          </section>
          <section className="stack">
            <h4>Checks</h4>
            {row.checks.length > 0 ? (
              <ul className="step-detail-list">
                {row.checks.map((check, index) => (
                  <li key={`${check.kind}-${check.status}-${index}`}>
                    <StepCheckBadge check={check} />
                    {check.summary ? <span className="small"> {check.summary}</span> : null}
                    <StepCheckDetails check={check} />
                  </li>
                ))}
              </ul>
            ) : (
              <p className="small">No structured checks for this step yet.</p>
            )}
          </section>
          <section className="stack">
            <h4>Logs & Diagnostics</h4>
            <StepObservabilityGroup
              apiBase={apiBase}
              logStreamingEnabled={logStreamingEnabled}
              sessionTimelineEnabled={sessionTimelineEnabled}
              row={row}
              routes={routes}
            />
          </section>
          <section className="stack">
            <h4>Artifacts</h4>
            <StepArtifactsList artifacts={row.artifacts} />
          </section>
          <section className="stack">
            <h4>Metadata</h4>
            <StepMetadataList row={row} runId={runId} />
          </section>
        </div>
      ) : null}
    </article>
  );
}

function LiveLogsPanel({
  apiBase,
  taskRunId,
  isTerminal,
  autoExpand = false,
  routes,
  sessionTimelineEnabled,
}: {
  apiBase: string;
  taskRunId: string;
  isTerminal: boolean;
  autoExpand?: boolean;
  routes: TaskRunRouteTemplates;
  sessionTimelineEnabled: boolean;
}) {
  const [logContent, setLogContent] = useState<TimelineRow[]>([]);
  const [viewerState, setViewerState] = useState<LogViewerState>('starting');
  const [expanded, setExpanded] = useState(false);
  const isVisible = usePageVisibility();
  const lastSeqRef = useRef<number | null>(null);
  const esRef = useRef<EventSource | null>(null);
  const isTerminalRef = useRef(isTerminal);
  const [sessionSnapshot, setSessionSnapshot] = useState<SessionSnapshot | null>(null);

  // Keep isTerminalRef current so the onerror handler always sees the latest value.
  useEffect(() => {
    isTerminalRef.current = isTerminal;
  }, [isTerminal]);

  // Reset log state whenever we switch to a different task run.
  useEffect(() => {
    setLogContent([]);
    lastSeqRef.current = null;
    setViewerState('starting');
  }, [taskRunId]);

  useEffect(() => {
    if (autoExpand) {
      setExpanded(true);
    }
  }, [autoExpand]);

  // Query for observability summary
  const summaryQuery = useQuery({
    queryKey: ['observability-summary', taskRunId],
    queryFn: () => fetchObservabilitySummary(apiBase, taskRunId, routes.observabilitySummary),
    enabled: !!taskRunId && expanded,
    // The summary indicates stream availability; refetch occasionally if not terminal
    staleTime: 1000 * 10,
  });

  const historyQuery = useQuery({
    queryKey: ['task-run-observability-events', taskRunId],
    queryFn: () => fetchObservabilityEvents(apiBase, taskRunId, routes.observabilityEvents),
    enabled: !!taskRunId && expanded && summaryQuery.isSuccess,
    staleTime: Infinity,
    retry: false,
  });
  const historyUnavailable = historyQuery.isError || historyQuery.data === null;

  // Legacy fallback: keep merged text available for older runs or partial failures.
  const tailQuery = useQuery({
    queryKey: ['task-run-tail', taskRunId],
    queryFn: () => fetchMergedTail(apiBase, taskRunId, routes.logsMerged),
    enabled:
      !!taskRunId &&
      expanded &&
      summaryQuery.isSuccess &&
      historyUnavailable,
    staleTime: Infinity,
    retry: false,
  });

  // Keep viewerState in sync with query boundaries
  useEffect(() => {
    if (!expanded) {
      setViewerState('starting');
      return;
    }
    if (historyQuery.isError && tailQuery.isError) {
      setViewerState('error');
    } else if (
      summaryQuery.isSuccess &&
      (historyQuery.isSuccess || tailQuery.isSuccess)
    ) {
      const summary = summaryQuery.data;
      const runIsTerminal =
        isTerminalRef.current || (summary ? TERMINAL_RUN_STATUSES.has(summary.status) : false);
      const supportsStreaming = summary?.supportsLiveStreaming ?? false;

      if (!supportsStreaming) {
        setViewerState(
          (historyQuery.data?.events.length ?? 0) > 0 || Boolean(tailQuery.data)
            ? 'ended'
            : 'not_available',
        );
      } else if (runIsTerminal) {
        setViewerState('ended');
      }
    }
  }, [
    expanded,
    historyQuery.data,
    historyQuery.isError,
    historyQuery.isSuccess,
    summaryQuery.data,
    summaryQuery.isSuccess,
    tailQuery.data,
    tailQuery.isError,
    tailQuery.isSuccess,
  ]);

  useEffect(() => {
    if (summaryQuery.data?.sessionSnapshot) {
      setSessionSnapshot(summaryQuery.data.sessionSnapshot);
    }
  }, [summaryQuery.data]);

  // Sync structured history into the local timeline when history fetch completes.
  useEffect(() => {
    if (historyQuery.isSuccess) {
      const rows = mapEventsToTimelineRows(historyQuery.data);
      const sequences = historyQuery.data?.events
        .map((event) => event.sequence)
        .filter((sequence) => Number.isFinite(sequence));
      if (lastSeqRef.current === null) {
        setLogContent(rows);
      }
      lastSeqRef.current = sequences && sequences.length > 0 ? Math.max(...sequences) : null;
      if (historyQuery.data?.sessionSnapshot) {
        setSessionSnapshot(historyQuery.data.sessionSnapshot);
      } else {
        const latestSessionEvent = (historyQuery.data?.events ?? []).findLast(
          (event) => event.session_id && typeof event.session_epoch === 'number',
        );
        if (latestSessionEvent) {
          setSessionSnapshot((prev) => deriveSessionSnapshotFromEvent(latestSessionEvent, prev));
        }
      }
    }
  }, [historyQuery.data, historyQuery.isSuccess]);

  // Sync legacy merged-text fallback only when structured history is unavailable.
  useEffect(() => {
    if (tailQuery.isSuccess && tailQuery.data && historyUnavailable) {
      if (lastSeqRef.current === null) {
        setLogContent(parseArtifactToRows(tailQuery.data));
      }
    }
  }, [historyUnavailable, tailQuery.data, tailQuery.isSuccess]);

  // Connect to SSE only after tail succeeds, if streaming is supported and active
  useEffect(() => {
    if (!taskRunId || !expanded || !summaryQuery.isSuccess || !isVisible) return;
    if (!historyQuery.isSuccess && !tailQuery.isSuccess) return;

    const summary = summaryQuery.data;
    const runIsTerminal =
      isTerminalRef.current || (summary ? TERMINAL_RUN_STATUSES.has(summary.status) : false);
    const supportsStreaming = summary?.supportsLiveStreaming ?? false;

    if (runIsTerminal || !supportsStreaming) return;

    let cancelled = false;

    const nextSince = lastSeqRef.current != null ? lastSeqRef.current + 1 : null;
    const since = nextSince != null ? `?since=${nextSince}` : '';
    const streamUrl = taskRunRoute(
      apiBase,
      routes.logsStream,
      `/task-runs/${encodeURIComponent(taskRunId)}/logs/stream`,
      { taskRunId },
    );
    const url = `${streamUrl}${since}`;
    const es = new EventSource(url, { withCredentials: true });
    esRef.current = es;

    es.onopen = () => {
      if (!cancelled) setViewerState('live');
    };

    const handleLogChunk = (event: MessageEvent) => {
      if (cancelled) return;
      try {
        const data = ObservabilityEventSchema.parse(JSON.parse(event.data));
        lastSeqRef.current = data.sequence;

        setLogContent((prev) => {
          return [...prev, ...eventToTimelineRows(data)];
        });
        setSessionSnapshot((prev) => deriveSessionSnapshotFromEvent(data, prev));
      } catch {
        // ignore malformed events
      }
    };

    es.onmessage = handleLogChunk;
    es.addEventListener('log_chunk', handleLogChunk);

    es.onerror = () => {
      es.close();
      esRef.current = null;
      if (cancelled) return;
      // Degrade gracefully
      setViewerState(isTerminalRef.current ? 'ended' : 'error');
    };

    return () => {
      cancelled = true;
      if (esRef.current) {
        esRef.current.close();
        esRef.current = null;
      }
    };
  }, [
    apiBase,
    expanded,
    historyQuery.isSuccess,
    isVisible,
    summaryQuery.data,
    summaryQuery.isSuccess,
    tailQuery.isSuccess,
    taskRunId,
  ]);

  // Close the stream once the task reaches a terminal state.
  useEffect(() => {
    if (isTerminal && esRef.current) {
      esRef.current.close();
      esRef.current = null;
      setViewerState('ended');
    }
  }, [isTerminal]);

  const statusLabel =
    viewerState === 'live'
      ? 'Connected'
      : viewerState === 'ended'
        ? 'Stream ended'
        : viewerState === 'error'
          ? 'Disconnected — showing artifact backup'
          : viewerState === 'not_available'
            ? 'Not yet available'
            : 'Loading…';

  const emptyLabel =
    viewerState === 'not_available'
      ? '(no log output available yet)'
      : '(waiting for output…)';

  const [wrapLines, setWrapLines] = useState(true);

  const handleCopy = () => {
    if (logContent.length === 0) return;
    copyTextToClipboard(logContent.map((line) => getCopyableRowText(line)).join('\n'));
  };

  const downloadUrl = taskRunRoute(
    apiBase,
    routes.logsMerged,
    `/task-runs/${encodeURIComponent(taskRunId)}/logs/merged`,
    { taskRunId },
  );
  const summaryErrorMessage = summaryQuery.isError ? (summaryQuery.error as Error).message : null;
  const liveStatusValue =
    summaryQuery.data?.liveStreamStatus
    ?? sessionSnapshot?.status
    ?? (viewerState === 'live' ? 'live' : viewerState);
  const sessionBadges = sessionSnapshot
    ? [
        ['Session', sessionSnapshot.sessionId],
        ['Epoch', String(sessionSnapshot.sessionEpoch)],
        ['Container', sessionSnapshot.containerId],
        ['Thread', sessionSnapshot.threadId],
        ['Active Turn', sessionSnapshot.activeTurnId ?? null],
        ['Live', liveStatusValue],
      ].filter(([, value]) => value) as Array<[string, string]>
    : [];

  return (
    <details
      className="stack"
      open={expanded}
    >
      <summary
        onClick={(e) => {
          e.preventDefault();
          setExpanded((prev) => !prev);
        }}
        style={{ cursor: 'pointer', fontWeight: 'bold', fontSize: '1.2rem', marginBottom: '0.5rem' }}
      >
        <span>Live Logs</span>
      </summary>
      <div className="stack live-logs-panel">
        {summaryErrorMessage ? <div className="notice error">{summaryErrorMessage}</div> : null}
        {expanded ? (
          <div className="button-group live-logs-toolbar">
            <label className="live-logs-wrap-toggle">
              <input type="checkbox" checked={wrapLines} onChange={(e) => setWrapLines(e.target.checked)} />
              <span className="small">Wrap lines</span>
            </label>
            <button className="secondary small" onClick={handleCopy}>Copy</button>
            <a className="button secondary small" href={downloadUrl} target="_blank" rel="noreferrer">Download</a>
          </div>
        ) : null}
        <p className="small">
          Task run <code className="text-xs">{taskRunId}</code> — {statusLabel}
        </p>
        {sessionBadges.length > 0 ? (
          <div className="live-logs-session-badges">
            {sessionBadges.map(([label, value]) => (
              <span key={`${label}-${value}`} className="card live-logs-session-badge">
                <strong>{label}:</strong> <code className="text-xs break-all">{value}</code>
              </span>
            ))}
          </div>
        ) : null}
        <div className={`live-logs-viewer-shell ${wrapLines ? 'is-wrapped' : 'is-unwrapped'}`}>
          {logContent.length === 0 ? (
            <div className="live-logs-empty">{emptyLabel}</div>
          ) : sessionTimelineEnabled ? (
            <div data-testid="live-logs-timeline-viewer" className="live-logs-viewer">
              <Virtuoso
                style={{ height: 400 }}
                data={logContent}
                computeItemKey={(_, row) => row.id}
                itemContent={(_, row) => renderTimelineRow(row, wrapLines, true)}
              />
            </div>
          ) : (
            <div data-testid="live-logs-legacy-viewer" className="live-logs-legacy-viewer">
              {logContent.map((line) => renderTimelineRow(line, wrapLines, false))}
            </div>
          )}
        </div>
      </div>
    </details>
  );
}

function InterventionPanel({
  actions,
  busy,
  audit,
  onPause,
  onResume,
  onApprove,
  onCancel,
  onReject,
  onSendMessage,
}: {
  actions: NonNullable<z.infer<typeof ExecutionDetailSchema>['actions']>;
  busy: boolean;
  audit: Array<{
    action: string;
    transport: string;
    summary: string;
    detail?: string | null | undefined;
    createdAt: string;
  }>;
  onPause: () => void;
  onResume: () => void;
  onApprove: () => void;
  onCancel: () => void;
  onReject: () => void;
  onSendMessage: (message: string) => void;
}) {
  const [operatorMessage, setOperatorMessage] = useState('');
  const hasControls = Boolean(
    actions.canPause ||
      actions.canResume ||
      actions.canApprove ||
      actions.canCancel ||
      actions.canReject ||
      actions.canSendMessage,
  );

  const submitMessage = () => {
    const message = operatorMessage.trim();
    if (!message) return;
    onSendMessage(message);
    setOperatorMessage('');
  };

  return (
    <section className="stack">
      <div>
        <h3>Intervention</h3>
        <p className="small">
          Controls use Temporal or provider-native APIs and do not require a live log connection.
        </p>
      </div>

      {hasControls ? (
        <div className="actions">
          {actions.canPause ? (
            <button type="button" disabled={busy} className="secondary" onClick={onPause}>
              Pause
            </button>
          ) : null}
          {actions.canResume ? (
            <button type="button" disabled={busy} className="queue-action" onClick={onResume}>
              Resume
            </button>
          ) : null}
          {actions.canApprove ? (
            <button type="button" disabled={busy} className="queue-action" onClick={onApprove}>
              Approve
            </button>
          ) : null}
          {actions.canReject ? (
            <button
              type="button"
              disabled={busy}
              className="queue-action queue-action-danger"
              onClick={onReject}
            >
              Reject
            </button>
          ) : null}
          {actions.canCancel ? (
            <button
              type="button"
              disabled={busy}
              className="queue-action queue-action-danger"
              onClick={onCancel}
            >
              Cancel
            </button>
          ) : null}
        </div>
      ) : (
        <p className="small">No intervention controls are available for the current task state.</p>
      )}

      {actions.canSendMessage ? (
        <div className="stack">
          <label htmlFor="operator-message">Operator message</label>
          <textarea
            id="operator-message"
            value={operatorMessage}
            onChange={(event) => setOperatorMessage(event.target.value)}
            rows={3}
            placeholder="Send an explicit operator message without using the log viewer."
          />
          <div className="actions">
            <button
              type="button"
              className="secondary"
              disabled={busy || !operatorMessage.trim()}
              onClick={submitMessage}
            >
              Send Message
            </button>
          </div>
        </div>
      ) : null}

      <div className="stack">
        <h4>Intervention History</h4>
        {audit.length === 0 ? (
          <p className="small">No intervention actions recorded yet.</p>
        ) : (
          <ul className="stack" style={{ listStyle: 'none', padding: 0, margin: 0 }}>
            {audit.map((entry, index) => (
              <li key={`${entry.createdAt}-${entry.action}-${index}`} className="card">
                <strong>{entry.summary}</strong>
                <div className="small">{formatWhen(entry.createdAt)}</div>
                <div className="small">
                  <code>{entry.transport}</code>
                </div>
                {entry.detail ? <p className="small">{entry.detail}</p> : null}
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

function StaticLogPanel({
  apiBase,
  taskRunId,
  stream,
  routes,
}: {
  apiBase: string;
  taskRunId: string;
  stream: 'stdout' | 'stderr';
  routes: TaskRunRouteTemplates;
}) {
  const [expanded, setExpanded] = useState(false);
  const [wrapLines, setWrapLines] = useState(true);

  const streamQuery = useQuery({
    queryKey: ['task-run-stream', taskRunId, stream],
    queryFn: () =>
      fetchStream(
        apiBase,
        taskRunId,
        stream,
        stream === 'stdout' ? routes.logsStdout : routes.logsStderr,
      ),
    enabled: !!taskRunId && expanded,
    retry: false,
  });

  const title = stream === 'stdout' ? 'Stdout' : 'Stderr';

  const handleCopy = () => {
    if (!streamQuery.data) return;
    copyTextToClipboard(streamQuery.data);
  };

  const downloadUrl = taskRunRoute(
    apiBase,
    stream === 'stdout' ? routes.logsStdout : routes.logsStderr,
    `/task-runs/${encodeURIComponent(taskRunId)}/logs/${stream}`,
    { taskRunId },
  );

  return (
    <details className="stack" open={expanded}>
      <summary
        onClick={(e) => {
          e.preventDefault();
          setExpanded((prev) => !prev);
        }}
        style={{ cursor: 'pointer', fontWeight: 'bold', fontSize: '1.2rem', marginBottom: '0.5rem' }}
      >
        <span>{title}</span>
      </summary>
      <div className="stack">
        {expanded ? (
          <div className="button-group" style={{ fontSize: '0.9rem', fontWeight: 'normal' }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: '0.25rem' }}>
              <input type="checkbox" checked={wrapLines} onChange={(e) => setWrapLines(e.target.checked)} />
              <span className="small">Wrap lines</span>
            </label>
            <button className="secondary small" onClick={handleCopy}>Copy</button>
            <a className="button secondary small" href={downloadUrl} target="_blank" rel="noreferrer">Download</a>
          </div>
        ) : null}
        <div style={{ maxHeight: '400px', overflowY: 'auto' }}>
          <pre
            style={{
              background: '#111',
              color: '#e8e8e8',
              padding: '0.75rem',
              fontSize: '0.7rem',
              lineHeight: 1.4,
              whiteSpace: wrapLines ? 'pre-wrap' : 'pre',
              wordBreak: wrapLines ? 'break-all' : 'normal',
              borderRadius: '4px',
              margin: 0,
            }}
          >
            {streamQuery.isLoading ? 'Loading...' : streamQuery.isError ? `Error loading ${stream}` : streamQuery.data || `(no ${stream} output)`}
          </pre>
        </div>
      </div>
    </details>
  );
}

function DiagnosticsPanel({
  apiBase,
  taskRunId,
  routes,
}: {
  apiBase: string;
  taskRunId: string;
  routes: TaskRunRouteTemplates;
}) {
  const [expanded, setExpanded] = useState(false);
  const [wrapLines, setWrapLines] = useState(true);

  const diagQuery = useQuery({
    queryKey: ['task-run-diagnostics', taskRunId],
    queryFn: () => fetchDiagnostics(apiBase, taskRunId, routes.diagnostics),
    enabled: !!taskRunId && expanded,
    retry: false,
  });

  const handleCopy = () => {
    if (!diagQuery.data) return;
    copyTextToClipboard(diagQuery.data);
  };

  const downloadUrl = taskRunRoute(
    apiBase,
    routes.diagnostics,
    `/task-runs/${encodeURIComponent(taskRunId)}/diagnostics`,
    { taskRunId },
  );

  return (
    <details className="stack" open={expanded}>
      <summary
        onClick={(e) => {
          e.preventDefault();
          setExpanded((prev) => !prev);
        }}
        style={{ cursor: 'pointer', fontWeight: 'bold', fontSize: '1.2rem', marginBottom: '0.5rem' }}
      >
        <span>Diagnostics</span>
      </summary>
      <div className="stack">
        {expanded ? (
          <div className="button-group" style={{ fontSize: '0.9rem', fontWeight: 'normal' }}>
            <label style={{ display: 'flex', alignItems: 'center', gap: '0.25rem' }}>
              <input type="checkbox" checked={wrapLines} onChange={(e) => setWrapLines(e.target.checked)} />
              <span className="small">Wrap lines</span>
            </label>
            <button className="secondary small" onClick={handleCopy}>Copy</button>
            <a className="button secondary small" href={downloadUrl} target="_blank" rel="noreferrer">Download</a>
          </div>
        ) : null}
        <div style={{ maxHeight: '400px', overflowY: 'auto' }}>
          <pre
            style={{
              background: '#111',
              color: '#e8e8e8',
              padding: '0.75rem',
              fontSize: '0.7rem',
              lineHeight: 1.4,
              whiteSpace: wrapLines ? 'pre-wrap' : 'pre',
              wordBreak: wrapLines ? 'break-all' : 'normal',
              borderRadius: '4px',
              margin: 0,
            }}
          >
            {diagQuery.isLoading ? 'Loading...' : diagQuery.isError ? 'Error loading diagnostics' : diagQuery.data || '(no diagnostics output)'}
          </pre>
        </div>
      </div>
    </details>
  );
}

function SessionContinuityPanel({
  apiBase,
  taskRunId,
  targetRuntime,
  isTerminal,
  onCancel,
  invalidateTaskDetail,
  cancelBusy,
  routes,
}: {
  apiBase: string;
  taskRunId: string;
  targetRuntime: string | null | undefined;
  isTerminal: boolean;
  onCancel: () => void;
  invalidateTaskDetail: () => void;
  cancelBusy: boolean;
  routes: TaskRunRouteTemplates;
}) {
  const queryClient = useQueryClient();
  const sessionId = deriveCodexSessionId(taskRunId, targetRuntime);
  const [followUpMessage, setFollowUpMessage] = useState('');
  const [panelError, setPanelError] = useState<string | null>(null);

  const projectionQuery = useQuery({
    queryKey: ['task-run-session-projection', taskRunId, sessionId],
    queryFn: () => {
      if (!sessionId) return Promise.resolve(null);
      return fetchArtifactSessionProjection(apiBase, taskRunId, sessionId, routes.artifactSession);
    },
    enabled: Boolean(taskRunId && sessionId),
    refetchInterval: (query) => {
      return getSessionProjectionRefetchInterval(
        isTerminal,
        Boolean(query.state.data),
        Boolean(query.state.error),
      );
    },
    retry: false,
  });

  const controlMutation = useMutation({
    mutationFn: async (body: { action: 'send_follow_up' | 'clear_session'; message?: string; reason?: string }) => {
      if (!sessionId) throw new Error('Managed session is unavailable.');
      return controlArtifactSession(apiBase, taskRunId, sessionId, body, routes.artifactSessionControl);
    },
    onSuccess: (result) => {
      setPanelError(null);
      void queryClient.setQueryData(
        ['task-run-session-projection', taskRunId, sessionId],
        result.projection,
      );
      invalidateTaskDetail();
      if (result.action === 'send_follow_up') {
        setFollowUpMessage('');
      }
    },
    onError: (error: Error) => setPanelError(error.message),
  });

  if (!sessionId) {
    return null;
  }
  if (projectionQuery.isLoading) {
    return (
      <section className="stack">
        <h3>Session Continuity</h3>
        <p className="small">Loading session continuity...</p>
      </section>
    );
  }
  if (projectionQuery.isError) {
    return (
      <section className="stack">
        <h3>Session Continuity</h3>
        <div className="notice error">{(projectionQuery.error as Error).message}</div>
      </section>
    );
  }
  if (!projectionQuery.data) {
    if (isTerminal) {
      return null;
    }
    return (
      <section className="stack">
        <h3>Session Continuity</h3>
        <p className="small">Waiting for session continuity artifacts...</p>
      </section>
    );
  }

  const projection = projectionQuery.data;
  const latestBadges = [
    ['Latest Summary', projection.latest_summary_ref?.artifact_id ?? null],
    ['Latest Checkpoint', projection.latest_checkpoint_ref?.artifact_id ?? null],
    ['Latest Control', projection.latest_control_event_ref?.artifact_id ?? null],
    ['Latest Reset', projection.latest_reset_boundary_ref?.artifact_id ?? null],
  ].filter(([, artifactId]) => artifactId !== null) as Array<[string, string]>;
  const busy = controlMutation.isPending || cancelBusy;

  const submitFollowUp = () => {
    const message = followUpMessage.trim();
    if (!message) return;
    setPanelError(null);
    controlMutation.mutate({
      action: 'send_follow_up',
      message,
    });
  };

  const clearSession = () => {
    setPanelError(null);
    controlMutation.mutate({
      action: 'clear_session',
    });
  };

  return (
    <section className="stack">
      <div>
        <h3>Session Continuity</h3>
        <p className="small">
          Session <code>{projection.session_id}</code> — Epoch {projection.session_epoch}
        </p>
      </div>

      {panelError ? <div className="notice error">{panelError}</div> : null}

      <div className="grid-2">
        <Card label="Session ID">
          <code className="text-xs break-all">{projection.session_id}</code>
        </Card>
        <Card label="Current Epoch">{projection.session_epoch}</Card>
      </div>

      {latestBadges.length > 0 ? (
        <div className="actions">
          {latestBadges.map(([label, artifactId]) => (
            <span key={`${label}-${artifactId}`} className="card">
              <strong>{label}:</strong> <code className="text-xs">{artifactId}</code>
            </span>
          ))}
        </div>
      ) : null}

      <div className="stack">
        {projection.grouped_artifacts.map((group) => (
          <div key={group.group_key} className="card">
            <strong>{group.title}</strong>
            <div className="stack gap-1" style={{ marginTop: '0.5rem' }}>
              {group.artifacts.length === 0 ? (
                <span className="small">No artifacts.</span>
              ) : (
                group.artifacts.map((artifact) => (
                  <code key={artifact.artifactId} className="text-xs break-all">
                    {artifact.artifactId}
                  </code>
                ))
              )}
            </div>
          </div>
        ))}
      </div>

      <div className="stack">
        <label htmlFor="session-follow-up">Follow-up message</label>
        <textarea
          id="session-follow-up"
          value={followUpMessage}
          onChange={(event) => setFollowUpMessage(event.target.value)}
          rows={3}
          placeholder="Send a follow-up turn to the managed Codex session."
          disabled={busy || isTerminal}
        />
        <div className="actions">
          <button
            type="button"
            className="secondary"
            disabled={busy || isTerminal || !followUpMessage.trim()}
            onClick={submitFollowUp}
          >
            Send follow-up
          </button>
          <button
            type="button"
            className="secondary"
            disabled={busy || isTerminal}
            onClick={clearSession}
          >
            Clear / Reset
          </button>
          <button
            type="button"
            className="queue-action queue-action-danger"
            disabled={busy || isTerminal}
            onClick={onCancel}
          >
            Cancel Execution
          </button>
        </div>
      </div>
    </section>
  );
}

type MissingTaskRunState = 'waiting_for_launch' | 'binding_missing' | 'launch_failed';

function inferMissingTaskRunState(execution: z.infer<typeof ExecutionDetailSchema>): MissingTaskRunState {
  const lifecycleState = (execution.rawState || execution.state || execution.status || '').toLowerCase();
  const temporalStatus = (execution.temporalStatus || execution.closeStatus || '').toLowerCase();
  const hasProgress = Boolean(
    execution.startedAt ||
      (execution.updatedAt && execution.createdAt && execution.updatedAt !== execution.createdAt),
  );

  if (
    execution.closedAt ||
    TERMINAL_STATES.has(lifecycleState) ||
    TERMINAL_RUN_STATUSES.has(lifecycleState) ||
    TERMINAL_RUN_STATUSES.has(temporalStatus)
  ) {
    return 'launch_failed';
  }

  if (lifecycleState === 'executing' || lifecycleState === 'running') {
    return hasProgress ? 'binding_missing' : 'waiting_for_launch';
  }

  return 'waiting_for_launch';
}

function renderMissingTaskRunCopy(state: MissingTaskRunState): string {
  if (state === 'launch_failed') {
    return 'This execution ended before a managed runtime observability record was created.';
  }
  if (state === 'binding_missing') {
    return 'This execution is running but has not received its managed runtime binding yet.';
  }
  return 'Waiting for managed runtime launch to create live logs.';
}

export function TaskDetailPage({ payload }: { payload: BootPayload }) {
  const queryClient = useQueryClient();
  const cfg = readDashboardConfig(payload);
  const taskRunRoutes = readTaskRunRouteTemplates(cfg);
  const detailPoll = cfg?.pollIntervalsMs?.detail ?? 2000;
  const actionsOn = Boolean(cfg?.features?.temporalDashboard?.actionsEnabled);
  const debugOn = Boolean(cfg?.features?.temporalDashboard?.debugFieldsEnabled);
  const logStreamingEnabled = cfg?.features?.logStreamingEnabled !== false;
  const sessionTimelineEnabled = cfg?.features?.liveLogsSessionTimelineEnabled === true;

  const taskIdMatch = window.location.pathname.match(
    /^\/tasks\/(?:temporal\/|proposals\/|schedules\/|manifests\/)?([^/]+)$/,
  );
  const taskId = decodeTaskPathSegment(taskIdMatch ? taskIdMatch[1] : null);
  const encodedTaskId = taskId ? encodeURIComponent(taskId) : null;
  const search = useMemo(() => new URLSearchParams(window.location.search), []);
  const sourceTemporal = search.get('source') === 'temporal';

  const [actionError, setActionError] = useState<string | null>(null);
  const [liveUpdates, setLiveUpdates] = useState(true);
  const [expandedSteps, setExpandedSteps] = useState<Record<string, boolean>>({});

  const detailQuery = useQuery({
    queryKey: ['task-detail', encodedTaskId, sourceTemporal],
    queryFn: async () => {
      if (!encodedTaskId) throw new Error('Task ID is required.');
      const suffix = sourceTemporal ? '?source=temporal' : '';
      const response = await fetch(`${payload.apiBase}/executions/${encodedTaskId}${suffix}`);
      if (!response.ok) {
        throw new Error(`Failed to fetch task: ${response.statusText}`);
      }
      return ExecutionDetailSchema.parse(await response.json());
    },
    enabled: Boolean(encodedTaskId),
    refetchInterval: liveUpdates ? detailPoll : false,
  });

  const execution = detailQuery.data;
  const workflowId = execution?.workflowId || execution?.taskId || taskId || '';
  const runId = execution?.temporalRunId || execution?.runId || '';
  const namespace = execution?.namespace || '';
  const summaryArtifactRef = execution?.summaryArtifactRef || execution?.summary_artifact_ref || '';
  const explicitTaskRunId = execution?.taskRunId || execution?.task_run_id || '';
  const resolvedTaskRunId = explicitTaskRunId;
  const previousTaskRunIdRef = useRef(resolvedTaskRunId);
  const [showTaskRunAttachNotice, setShowTaskRunAttachNotice] = useState(false);

  useEffect(() => {
    if (!resolvedTaskRunId) {
      previousTaskRunIdRef.current = '';
      setShowTaskRunAttachNotice(false);
      return;
    }

    if (!previousTaskRunIdRef.current) {
      previousTaskRunIdRef.current = resolvedTaskRunId;
      setShowTaskRunAttachNotice(true);
      const timeout = window.setTimeout(() => {
        setShowTaskRunAttachNotice(false);
      }, 250);
      return () => window.clearTimeout(timeout);
    }

    previousTaskRunIdRef.current = resolvedTaskRunId;
    setShowTaskRunAttachNotice(false);
    return undefined;
  }, [resolvedTaskRunId]);

  const missingTaskRunState = execution && !resolvedTaskRunId ? inferMissingTaskRunState(execution) : null;

  const stepsQuery = useQuery({
    queryKey: ['task-detail-steps', workflowId, execution?.stepsHref],
    queryFn: () => fetchStepLedger(String(execution?.stepsHref || '')),
    enabled: Boolean(execution?.stepsHref),
    refetchInterval: liveUpdates && execution?.stepsHref ? detailPoll : false,
  });
  const latestRunId = stepsQuery.data?.runId || runId;

  const artifactsQuery = useQuery({
    queryKey: ['task-detail-artifacts', namespace, workflowId, latestRunId],
    queryFn: async () => {
      const path = `${payload.apiBase}/executions/${encodeURIComponent(namespace)}/${encodeURIComponent(workflowId)}/${encodeURIComponent(latestRunId)}/artifacts`;
      const response = await fetch(path);
      if (!response.ok) {
        throw new Error(`Artifacts: ${response.statusText}`);
      }
      return ArtifactListSchema.parse(await response.json());
    },
    enabled:
      Boolean(namespace && workflowId && latestRunId)
      && (!execution?.stepsHref || stepsQuery.isSuccess || stepsQuery.isError),
    refetchInterval: liveUpdates && namespace && workflowId && latestRunId ? detailPoll : false,
  });

  const runSummaryQuery = useQuery({
    queryKey: ['task-detail-run-summary', summaryArtifactRef],
    queryFn: () => fetchRunSummaryArtifact(payload.apiBase, summaryArtifactRef),
    enabled: Boolean(summaryArtifactRef),
    refetchInterval: liveUpdates && summaryArtifactRef ? detailPoll : false,
  });
  const runSummary = runSummaryQuery.data;
  const displayedSummary = runSummary?.operatorSummary || execution?.summary || '—';
  const prUrl =
    normalizeGitHubPullRequestUrl(execution?.prUrl) ||
    normalizeGitHubPullRequestUrl(runSummary?.publishContext?.pullRequestUrl);
  const dependencyOutcomesById = useMemo(() => {
    const entries = (execution?.dependencyOutcomes || []).map((item) => [item.workflowId, item] as const);
    return new Map(entries);
  }, [execution?.dependencyOutcomes]);
  const prerequisiteRows = useMemo(() => {
    const ids = execution?.dependsOn || [];
    if (!execution) {
      return [];
    }
    if (execution.prerequisites.length > 0) {
      return execution.prerequisites;
    }
    return ids.map((workflowId) => ({
      workflowId,
      title: workflowId,
      summary: null,
      state: null,
      closeStatus: null,
      workflowType: 'MoonMind.Run',
    }));
  }, [execution]);
  const hasDependencySection = Boolean(
    execution &&
      (execution.hasDependencies ||
        execution.dependsOn.length > 0 ||
        execution.prerequisites.length > 0 ||
        execution.dependents.length > 0),
  );
  const hasStepsEndpoint = Boolean(execution?.stepsHref);
  const showExecutionObservationFallback =
    !hasStepsEndpoint || (!stepsQuery.isLoading && (stepsQuery.isError || !stepsQuery.data));

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['task-detail', encodedTaskId] });
    void queryClient.invalidateQueries({ queryKey: ['task-detail-steps', workflowId] });
    void queryClient.invalidateQueries({ queryKey: ['task-detail-artifacts', namespace, workflowId, latestRunId] });
    void queryClient.invalidateQueries({ queryKey: ['task-detail-run-summary', summaryArtifactRef] });
  };

  const updateMutation = useMutation({
    mutationFn: async (body: Record<string, unknown>) => {
      const response = await fetch(`${payload.apiBase}/executions/${encodeURIComponent(workflowId)}/update`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify(body),
      });
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || response.statusText);
      }
      return response.json();
    },
    onSuccess: invalidate,
    onError: (error: Error) => setActionError(error.message),
  });

  const signalMutation = useMutation({
    mutationFn: async ({
      signalName,
      payload: signalPayload,
    }: {
      signalName: string;
      payload?: Record<string, unknown>;
    }) => {
      const response = await fetch(`${payload.apiBase}/executions/${encodeURIComponent(workflowId)}/signal`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({
          signalName,
          payload: signalPayload ?? {},
        }),
      });
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || response.statusText);
      }
      return response.json();
    },
    onSuccess: invalidate,
    onError: (error: Error) => setActionError(error.message),
  });

  const cancelMutation = useMutation({
    mutationFn: async ({
      action = 'cancel',
      graceful = true,
      reason,
    }: {
      action?: 'cancel' | 'reject';
      graceful?: boolean;
      reason?: string;
    }) => {
      const response = await fetch(`${payload.apiBase}/executions/${encodeURIComponent(workflowId)}/cancel`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({
          action,
          graceful,
          ...(reason ? { reason } : {}),
        }),
      });
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || response.statusText);
      }
      return response.json();
    },
    onSuccess: invalidate,
    onError: (error: Error) => setActionError(error.message),
  });

  const onRename = () => {
    setActionError(null);
    const title = window.prompt('New task title', execution?.title || '');
    if (title === null || !title.trim()) return;
    updateMutation.mutate({ updateName: 'SetTitle', title: title.trim() });
  };

  const onRerun = () => {
    setActionError(null);
    if (!window.confirm('Request rerun for this task?')) return;
    updateMutation.mutate({ updateName: 'RequestRerun' });
  };

  const onPause = () => {
    setActionError(null);
    signalMutation.mutate({ signalName: 'Pause', payload: {} });
  };

  const onResume = () => {
    setActionError(null);
    signalMutation.mutate({ signalName: 'Resume', payload: {} });
  };

  const onApprove = () => {
    setActionError(null);
    signalMutation.mutate({ signalName: 'Approve', payload: {} });
  };

  const onSendMessage = (message: string) => {
    setActionError(null);
    signalMutation.mutate({ signalName: 'SendMessage', payload: { message } });
  };

  const onCancel = () => {
    setActionError(null);
    if (!window.confirm('Cancel this task?')) return;
    cancelMutation.mutate({ action: 'cancel', graceful: true });
  };

  const onReject = () => {
    setActionError(null);
    if (!window.confirm('Reject this task?')) return;
    cancelMutation.mutate({
      action: 'reject',
      graceful: true,
      reason: 'Rejected by operator.',
    });
  };

  const actions = execution?.actions;
  const busy = updateMutation.isPending || signalMutation.isPending || cancelMutation.isPending;
  const isTerminalExecution = TERMINAL_STATES.has(execution?.rawState || execution?.state || '');
  const hasTaskActions = Boolean(actions?.canSetTitle || actions?.canRerun);
  const hasInterventionSection = Boolean(
    actions &&
      (
        actions.canPause ||
        actions.canResume ||
        actions.canApprove ||
        actions.canCancel ||
        actions.canReject ||
        actions.canSendMessage ||
        (execution?.interventionAudit?.length ?? 0) > 0
      ),
  );
  const toggleStep = (logicalStepId: string) => {
    setExpandedSteps((prev) => ({
      ...prev,
      [logicalStepId]: !prev[logicalStepId],
    }));
  };

  return (
    <div className="stack">
      <div className="toolbar">
        <div>
          <h2 className="page-title">Temporal Task Detail</h2>
          <p className="page-meta">Task {taskId || '—'}</p>
        </div>
        <div className="toolbar-controls">
          <label className="queue-inline-toggle toolbar-live-toggle">
            <input
              type="checkbox"
              checked={liveUpdates}
              onChange={(event) => setLiveUpdates(event.target.checked)}
            />
            Live updates
          </label>
          <span className="small">
            {liveUpdates
              ? `Polling every ${Math.round(detailPoll / 1000)}s`
              : 'Updates paused to keep selections stable.'}
          </span>
        </div>
      </div>

      {actionError ? <div className="notice error">{actionError}</div> : null}

      {detailQuery.isLoading ? (
        <p className="loading">Loading task...</p>
      ) : detailQuery.isError ? (
        <div className="notice error">{(detailQuery.error as Error).message}</div>
      ) : execution ? (
        <>
          <div className="grid-2">
            <Card label="Title">{execution.title}</Card>
            <Card label="Status">
              <span className={executionStatusPillClasses(execution.rawState || execution.state || execution.status)}>
                {execution.rawState || execution.state || execution.status || '—'}
              </span>
            </Card>
            <Card label="Source">Temporal</Card>
            <Card label="Workflow Type">{execution.workflowType || '—'}</Card>
            <Card label="Entry">{execution.entry || '—'}</Card>
            {execution.targetRuntime ? (
              <Card label="Runtime">{formatRuntimeLabel(execution.targetRuntime)}</Card>
            ) : null}
            {execution.model ? (
              <Card label="Model">
                <code className="text-xs">{execution.model}</code>
              </Card>
            ) : null}
            {execution.profileId ? (
              <Card label="Provider Profile">{renderProviderProfileSummary(execution)}</Card>
            ) : null}
            {execution.effort ? <Card label="Effort">{execution.effort}</Card> : null}
            {execution.startingBranch ? (
              <Card label="Starting Branch">
                <code className="text-xs break-all">{execution.startingBranch}</code>
              </Card>
            ) : null}
            {execution.targetBranch ? (
              <Card label="Target Branch">
                <code className="text-xs break-all">{execution.targetBranch}</code>
              </Card>
            ) : null}
            {execution.repository ? (
              <Card label="Repository">
                <code className="text-xs break-all">{execution.repository}</code>
              </Card>
            ) : null}
            {execution.publishMode ? (
              <Card label="Publish Mode">
                <code className="text-xs">{execution.publishMode}</code>
              </Card>
            ) : null}
            {prUrl ? (
              <Card label="PR Link">
                <a className="text-xs break-all" href={prUrl} target="_blank" rel="noreferrer">
                  {prUrl}
                </a>
              </Card>
            ) : null}
            <Card label="Temporal Status">{execution.temporalStatus || '—'}</Card>
            <Card label="Current State">{execution.rawState || execution.state || '—'}</Card>
            {execution.closeStatus ? <Card label="Close Status">{execution.closeStatus}</Card> : null}
            {execution.waitingReason ? <Card label="Waiting Reason">{execution.waitingReason}</Card> : null}
            {execution.scheduledFor ? <Card label="Scheduled For">{formatWhen(execution.scheduledFor)}</Card> : null}
            <Card label="Created">{formatWhen(execution.createdAt)}</Card>
            <Card label="Latest Run">
              <code className="text-xs break-all">{latestRunId || '—'}</code>
            </Card>
            {resolvedTaskRunId ? (
              <Card label="Task Run">
                <code className="text-xs break-all">{resolvedTaskRunId}</code>
              </Card>
            ) : null}
            <Card label="Started">{formatWhen(execution.startedAt)}</Card>
            <Card label="Updated">{formatWhen(execution.updatedAt)}</Card>
            <Card label="Closed">{formatWhen(execution.closedAt)}</Card>
            <Card label="Workflow ID">
              <code className="text-xs break-all">{workflowId}</code>
            </Card>
          </div>

          <SkillProvenanceBadge 
            resolvedSkillsetRef={execution.resolvedSkillsetRef} 
            taskSkills={execution.taskSkills} 
            targetSkill={execution.targetSkill} 
          />

          <section>
            <h3>Summary</h3>
            <p className="whitespace-pre-wrap">{displayedSummary}</p>
            {runSummary?.finishOutcome?.reason && runSummary.finishOutcome.reason !== displayedSummary ? (
              <p className="small">Outcome: {runSummary.finishOutcome.reason}</p>
            ) : null}
          </section>

          {runSummary ? (
            <section className="stack">
              <h3>Run Summary</h3>
              {runSummary.finishOutcome ? (
                <div className="grid-2">
                  <Card label="Outcome Code">{runSummary.finishOutcome.code || '—'}</Card>
                  <Card label="Outcome Stage">{runSummary.finishOutcome.stage || '—'}</Card>
                </div>
              ) : null}
              {runSummary.publish ? (
                <div className="grid-2">
                  <Card label="Publish Status">{runSummary.publish.status || '—'}</Card>
                  <Card label="Publish Mode">{runSummary.publish.mode || '—'}</Card>
                </div>
              ) : null}
              {runSummary.publish?.reason ? (
                <p className="whitespace-pre-wrap">{runSummary.publish.reason}</p>
              ) : null}
              {runSummary.publishContext ? (
                <div className="grid-2">
                  {runSummary.publishContext.branch ? (
                    <Card label="Publish Branch">
                      <code className="text-xs break-all">{runSummary.publishContext.branch}</code>
                    </Card>
                  ) : null}
                  {runSummary.publishContext.baseRef ? (
                    <Card label="Base Ref">
                      <code className="text-xs break-all">{runSummary.publishContext.baseRef}</code>
                    </Card>
                  ) : null}
                  {runSummary.publishContext.commitCount !== undefined &&
                  runSummary.publishContext.commitCount !== null ? (
                    <Card label="Commit Count">{String(runSummary.publishContext.commitCount)}</Card>
                  ) : null}
                </div>
              ) : null}
              {runSummary.lastStep?.summary && runSummary.lastStep.summary !== displayedSummary ? (
                <div>
                  <strong>Last Step</strong>
                  <p className="whitespace-pre-wrap">{runSummary.lastStep.summary}</p>
                </div>
              ) : null}
              {runSummary.nextAction ? <p className="small">{runSummary.nextAction}</p> : null}
            </section>
          ) : null}

          {execution.waitingReason ? (
            <section>
              <h3>Waiting Reason</h3>
              <p>{execution.waitingReason}</p>
            </section>
          ) : null}

          {hasStepsEndpoint ? (
            <section className="stack">
              <div className="step-ledger-header">
                <div>
                  <h3>Steps</h3>
                  <p className="small">
                    Latest run <code className="text-xs break-all">{latestRunId || '—'}</code>
                  </p>
                </div>
                {stepsQuery.data ? (
                  <p className="small">
                    {stepsQuery.data.steps.length} step{stepsQuery.data.steps.length === 1 ? '' : 's'} in {stepsQuery.data.runScope}
                  </p>
                ) : null}
              </div>
              {stepsQuery.isLoading ? (
                <p className="loading">Loading steps...</p>
              ) : stepsQuery.isError ? (
                <div className="notice error">{(stepsQuery.error as Error).message}</div>
              ) : stepsQuery.data ? (
                <div className="step-ledger-list">
                  {stepsQuery.data.steps.map((row) => (
                    <StepLedgerRowCard
                      key={row.logicalStepId}
                      apiBase={payload.apiBase}
                      logStreamingEnabled={logStreamingEnabled}
                      sessionTimelineEnabled={sessionTimelineEnabled}
                      row={row}
                      runId={latestRunId}
                      expanded={Boolean(expandedSteps[row.logicalStepId])}
                      onToggle={() => toggleStep(row.logicalStepId)}
                      routes={taskRunRoutes}
                    />
                  ))}
                </div>
              ) : (
                <p className="small">No step ledger available for this execution.</p>
              )}
            </section>
          ) : null}

          {execution.attentionRequired ? (
            <section className="notice">
              <strong>Attention required.</strong> This task is waiting for external input before it can continue.
            </section>
          ) : null}

          {hasDependencySection ? (
            <section className="stack">
              <div>
                <h3>Dependencies</h3>
                <p className="small">
                  Direct prerequisite runs gate this execution before planning or execution begins.
                </p>
              </div>
              <div className="grid-2">
                <Card label="Declared Prerequisites">{String(execution.dependsOn.length)}</Card>
                <Card label="Blocked On Dependencies">{execution.blockedOnDependencies ? 'Yes' : 'No'}</Card>
                <Card label="Dependency Resolution">{formatDependencyResolution(execution.dependencyResolution)}</Card>
                <Card label="Dependency Wait Duration">
                  {formatDurationMs(execution.dependencyWaitDurationMs ?? null)}
                </Card>
                {execution.failedDependencyId ? (
                  <Card label="Failed Dependency">
                    <code className="text-xs break-all">{execution.failedDependencyId}</code>
                  </Card>
                ) : null}
              </div>
              {execution.blockedOnDependencies ? (
                <div className="notice">
                  <strong>Blocked on prerequisites.</strong> This run will not advance until every prerequisite reaches <code>completed</code>.
                </div>
              ) : null}
              <div className="stack">
                <h4>Prerequisites</h4>
                {prerequisiteRows.length === 0 ? (
                  <p className="small">No prerequisites declared.</p>
                ) : (
                  <ul className="stack" style={{ listStyle: 'none', padding: 0, margin: 0 }}>
                    {prerequisiteRows.map((item) => {
                      const outcome = dependencyOutcomesById.get(item.workflowId);
                      const stateLabel = outcome?.terminalState || item.state || 'unknown';
                      return (
                        <li key={item.workflowId} className="card">
                          <div className="stack gap-1">
                            <a href={dependencyHref(item.workflowId)}>
                              <strong>{item.title || item.workflowId}</strong>
                            </a>
                            <code className="text-xs break-all">{item.workflowId}</code>
                            <span className={executionStatusPillClasses(stateLabel)}>{stateLabel}</span>
                            {item.summary ? <p className="small">{item.summary}</p> : null}
                            {outcome?.message ? <p className="small">{outcome.message}</p> : null}
                            {outcome?.failureCategory ? (
                              <p className="small">Failure category: <code>{outcome.failureCategory}</code></p>
                            ) : null}
                            {(outcome?.closeStatus || item.closeStatus) ? (
                              <p className="small">Close status: {outcome?.closeStatus || item.closeStatus}</p>
                            ) : null}
                          </div>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>
              <div className="stack">
                <h4>Dependents</h4>
                {execution.dependents.length === 0 ? (
                  <p className="small">No downstream dependents reference this run.</p>
                ) : (
                  <ul className="stack" style={{ listStyle: 'none', padding: 0, margin: 0 }}>
                    {execution.dependents.map((item) => (
                      <li key={item.workflowId} className="card">
                        <div className="stack gap-1">
                          <a href={dependencyHref(item.workflowId)}>
                            <strong>{item.title || item.workflowId}</strong>
                          </a>
                          <code className="text-xs break-all">{item.workflowId}</code>
                          <span className={executionStatusPillClasses(item.state)}>{item.state || 'unknown'}</span>
                          {item.summary ? <p className="small">{item.summary}</p> : null}
                          {item.closeStatus ? <p className="small">Close status: {item.closeStatus}</p> : null}
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </section>
          ) : null}

          {actionsOn && actions && hasTaskActions ? (
            <section className="stack">
              <div>
                <h3>Task Actions</h3>
                <p className="small">Workflow editing actions stay separate from intervention controls.</p>
              </div>
              <div className="actions">
                {actions.canSetTitle ? (
                  <button type="button" disabled={busy} className="secondary" onClick={onRename}>
                    Rename
                  </button>
                ) : null}
                {actions.canRerun ? (
                  <button type="button" disabled={busy} className="secondary" onClick={onRerun}>
                    Rerun
                  </button>
                ) : null}
              </div>
            </section>
          ) : null}

          {actionsOn && actions && hasInterventionSection ? (
            <InterventionPanel
              actions={actions}
              busy={busy}
              audit={execution.interventionAudit || []}
              onPause={onPause}
              onResume={onResume}
              onApprove={onApprove}
              onCancel={onCancel}
              onReject={onReject}
              onSendMessage={onSendMessage}
            />
          ) : null}

          {resolvedTaskRunId ? (
            <SessionContinuityPanel
              apiBase={payload.apiBase}
              taskRunId={resolvedTaskRunId}
              targetRuntime={execution.targetRuntime}
              isTerminal={isTerminalExecution}
              onCancel={onCancel}
              invalidateTaskDetail={invalidate}
              cancelBusy={cancelMutation.isPending}
              routes={taskRunRoutes}
            />
          ) : null}

          <section className="stack">
            <h3>Timeline</h3>
            <div className="queue-table-wrapper" data-layout="table">
              <table>
                <thead>
                  <tr>
                    <th>Stage</th>
                    <th>Timestamp</th>
                    <th>Detail</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td>Started</td>
                    <td>{formatWhen(execution.startedAt)}</td>
                    <td>Execution created.</td>
                  </tr>
                  <tr>
                    <td>Last update</td>
                    <td>{formatWhen(execution.updatedAt)}</td>
                    <td>State: {(execution.state || '').replaceAll('_', ' ')}</td>
                  </tr>
                  {execution.waitingReason || execution.attentionRequired ? (
                    <tr>
                      <td>Waiting</td>
                      <td>{formatWhen(execution.updatedAt)}</td>
                      <td>
                        {execution.waitingReason || 'Awaiting external input.'}
                        {execution.attentionRequired ? ' Attention required.' : ''}
                      </td>
                    </tr>
                  ) : null}
                  {execution.closedAt ? (
                    <tr>
                      <td>Closed</td>
                      <td>{formatWhen(execution.closedAt)}</td>
                      <td>Close status: {execution.closeStatus || execution.temporalStatus || '—'}</td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          </section>

          <section className="stack">
            <h3>Artifacts</h3>
            {artifactsQuery.isLoading ? (
              <p className="loading">Loading artifacts...</p>
            ) : artifactsQuery.isError ? (
              <div className="notice error">{(artifactsQuery.error as Error).message}</div>
            ) : (
              <div className="queue-table-wrapper" data-layout="table">
                <table>
                  <thead>
                    <tr>
                      <th>Artifact</th>
                      <th>Size</th>
                      <th>Status</th>
                      <th>Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(artifactsQuery.data?.artifacts || []).length === 0 ? (
                      <tr>
                        <td colSpan={4}>No artifacts.</td>
                      </tr>
                    ) : (
                      (artifactsQuery.data?.artifacts || []).map((artifact) => (
                        <tr key={artifact.artifactId}>
                          <td>
                            <code>{artifact.artifactId}</code>
                          </td>
                          <td>{artifact.sizeBytes ?? '—'}</td>
                          <td>{String(artifact.status ?? '—')}</td>
                          <td>
                            {artifact.downloadUrl ? (
                              <a className="button secondary" href={artifact.downloadUrl}>
                                Download
                              </a>
                            ) : (
                              <a
                                className="button secondary"
                                href={`${payload.apiBase}/artifacts/${encodeURIComponent(artifact.artifactId)}/download`}
                                title="Download artifact"
                              >
                                Download
                              </a>
                            )}
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          {showExecutionObservationFallback ? (
            <section className="stack">
              <div>
                <h3>Observation</h3>
                <p className="small">
                  Live logs are passive observation only. Use the Intervention panel for control actions.
                </p>
              </div>
              {logStreamingEnabled ? (
                resolvedTaskRunId ? (
                  <>
                    {showTaskRunAttachNotice ? (
                      <p className="small">Waiting for managed runtime launch to create live logs.</p>
                    ) : null}
                    <LiveLogsPanel
                      apiBase={payload.apiBase}
                      taskRunId={resolvedTaskRunId}
                      isTerminal={isTerminalExecution}
                      autoExpand={showTaskRunAttachNotice}
                      routes={taskRunRoutes}
                      sessionTimelineEnabled={sessionTimelineEnabled}
                    />
                    <StaticLogPanel
                      apiBase={payload.apiBase}
                      taskRunId={resolvedTaskRunId}
                      stream="stdout"
                      routes={taskRunRoutes}
                    />
                    <StaticLogPanel
                      apiBase={payload.apiBase}
                      taskRunId={resolvedTaskRunId}
                      stream="stderr"
                      routes={taskRunRoutes}
                    />
                    <DiagnosticsPanel
                      apiBase={payload.apiBase}
                      taskRunId={resolvedTaskRunId}
                      routes={taskRunRoutes}
                    />
                  </>
                ) : (
                  <>
                    <h3>Live Logs</h3>
                    <p className="small">{missingTaskRunState ? renderMissingTaskRunCopy(missingTaskRunState) : 'Waiting for task details...'}</p>
                  </>
                )
              ) : (
                <>
                  <h3>Live Logs</h3>
                  <p className="small">Live log streaming is disabled in the server dashboard config.</p>
                </>
              )}
            </section>
          ) : null}

          {debugOn && execution.debugFields ? (
            <section className="stack">
              <h3>Debug Metadata</h3>
              <div className="grid-2">
                {buildDebugFieldEntries(execution).map(([key, value]) => (
                  <Card key={key} label={key}>
                    {formatDebugValue(value)}
                  </Card>
                ))}
              </div>
            </section>
          ) : null}
        </>
      ) : (
        <p>No task details.</p>
      )}
    </div>
  );
}
export default TaskDetailPage;
