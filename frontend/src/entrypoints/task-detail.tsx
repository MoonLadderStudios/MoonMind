import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
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
  };
  sources?: {
    temporal?: Record<string, string>;
  };
};

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
  const seconds = value / 1000;
  if (seconds < 60) return `${seconds.toFixed(seconds >= 10 ? 0 : 1)} s`;
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = Math.round(seconds % 60);
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

/** Fetch the plain-text merged-tail from the artifact-backed API. */
async function fetchMergedTail(apiBase: string, taskRunId: string): Promise<string> {
  const resp = await fetch(
    `${apiBase}/task-runs/${encodeURIComponent(taskRunId)}/logs/merged`,
    { credentials: 'include' },
  );
  if (!resp.ok) {
    if (resp.status === 404) return '';
    throw buildObservabilityRequestError(resp.status);
  }
  return resp.text();
}

/** Fetch specific static stream (stdout or stderr) */
async function fetchStream(apiBase: string, taskRunId: string, stream: 'stdout' | 'stderr'): Promise<string> {
  const resp = await fetch(
    `${apiBase}/task-runs/${encodeURIComponent(taskRunId)}/logs/${stream}`,
    { credentials: 'include' },
  );
  if (!resp.ok) {
    if (resp.status === 404) return '';
    throw buildObservabilityRequestError(resp.status);
  }
  return resp.text();
}

/** Fetch diagnostics JSON */
async function fetchDiagnostics(apiBase: string, taskRunId: string): Promise<string> {
  const resp = await fetch(
    `${apiBase}/task-runs/${encodeURIComponent(taskRunId)}/diagnostics`,
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

/** Fetch the observability summary for a task run. */
async function fetchObservabilitySummary(
  apiBase: string,
  taskRunId: string,
): Promise<{ supportsLiveStreaming: boolean; liveStreamStatus: string; status: string } | null> {
  const resp = await fetch(
    `${apiBase}/task-runs/${encodeURIComponent(taskRunId)}/observability-summary`,
    { credentials: 'include' },
  );
  if (!resp.ok) {
    if (resp.status === 404) return null;
    throw buildObservabilityRequestError(resp.status);
  }
  const body = (await resp.json()) as { summary: Record<string, unknown> };
  const s = body.summary;
  return {
    supportsLiveStreaming: Boolean(s.supportsLiveStreaming),
    liveStreamStatus: String(s.liveStreamStatus ?? 'unavailable'),
    status: String(s.status ?? ''),
  };
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

type LogLine = {
  id: string;
  text: string;
  stream: 'stdout' | 'stderr' | 'system' | 'unknown';
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

function parseArtifactToLines(content: string): LogLine[] {
  const lines = splitLogText(content);
  let currentStream: LogLine['stream'] = 'unknown';

  return lines.map((line, i) => {
    if (line.startsWith('--- stdout ---')) currentStream = 'stdout';
    else if (line.startsWith('--- stderr ---')) currentStream = 'stderr';
    else if (line.startsWith('--- system ---')) currentStream = 'system';

    return { id: `artifact-${i}`, text: line, stream: currentStream };
  });
}

function LiveLogsPanel({
  apiBase,
  taskRunId,
  isTerminal,
  autoExpand = false,
}: {
  apiBase: string;
  taskRunId: string;
  isTerminal: boolean;
  autoExpand?: boolean;
}) {
  const [logContent, setLogContent] = useState<LogLine[]>([]);
  const [viewerState, setViewerState] = useState<LogViewerState>('starting');
  const [expanded, setExpanded] = useState(false);
  const isVisible = usePageVisibility();
  const lastSeqRef = useRef<number | null>(null);
  const esRef = useRef<EventSource | null>(null);
  const isTerminalRef = useRef(isTerminal);

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
    queryFn: () => fetchObservabilitySummary(apiBase, taskRunId),
    enabled: !!taskRunId && expanded,
    // The summary indicates stream availability; refetch occasionally if not terminal
    staleTime: 1000 * 10,
  });

  // Query for the artifact-backed tail (runs after summary resolves)
  const tailQuery = useQuery({
    queryKey: ['task-run-tail', taskRunId],
    queryFn: () => fetchMergedTail(apiBase, taskRunId),
    enabled: !!taskRunId && expanded && summaryQuery.isSuccess,
    staleTime: Infinity,
    retry: false,
  });

  // Keep viewerState in sync with query boundaries
  useEffect(() => {
    if (!expanded) {
      setViewerState('starting');
      return;
    }
    if (tailQuery.isError) {
      setViewerState('error');
    } else if (summaryQuery.isSuccess && tailQuery.isSuccess) {
      const summary = summaryQuery.data;
      const runIsTerminal = isTerminalRef.current || (summary ? TERMINAL_RUN_STATUSES.has(summary.status) : false);
      const supportsStreaming = summary?.supportsLiveStreaming ?? false;

      if (!supportsStreaming) {
        setViewerState(tailQuery.data ? 'ended' : 'not_available');
      } else if (runIsTerminal) {
        setViewerState('ended');
      }
    }
  }, [expanded, summaryQuery.isSuccess, summaryQuery.data, tailQuery.isError, tailQuery.isSuccess, tailQuery.data]);

  // Sync tail content into the local log buffer when tail fetch completes.
  useEffect(() => {
    if (tailQuery.isSuccess && tailQuery.data) {
      setLogContent((prev) => {
        if (lastSeqRef.current !== null) return prev;
        return parseArtifactToLines(tailQuery.data);
      });
    }
  }, [tailQuery.isSuccess, tailQuery.data]);

  // Connect to SSE only after tail succeeds, if streaming is supported and active
  useEffect(() => {
    if (!taskRunId || !expanded || !summaryQuery.isSuccess || !tailQuery.isSuccess || !isVisible) return;

    const summary = summaryQuery.data;
    const runIsTerminal = isTerminalRef.current || (summary ? TERMINAL_RUN_STATUSES.has(summary.status) : false);
    const supportsStreaming = summary?.supportsLiveStreaming ?? false;

    if (runIsTerminal || !supportsStreaming) return;

    let cancelled = false;

    const nextSince = lastSeqRef.current != null ? lastSeqRef.current + 1 : null;
    const since = nextSince != null ? `?since=${nextSince}` : '';
    const url = `${apiBase}/task-runs/${encodeURIComponent(taskRunId)}/logs/stream${since}`;
    const es = new EventSource(url, { withCredentials: true });
    esRef.current = es;

    es.onopen = () => {
      if (!cancelled) setViewerState('live');
    };

    const handleLogChunk = (event: MessageEvent) => {
      if (cancelled) return;
      try {
        const data = JSON.parse(event.data) as { sequence: number; text: string; stream?: string };
        lastSeqRef.current = data.sequence;

        setLogContent((prev) => {
          const lines = splitLogText(data.text);
          const mapped: LogLine[] = lines.map((l, i) => ({
            id: `live-${data.sequence}-${i}`,
            text: l,
            stream: (data.stream as LogLine['stream']) || 'unknown',
          }));
          return [...prev, ...mapped];
        });
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
  }, [apiBase, taskRunId, expanded, isVisible, summaryQuery.isSuccess, summaryQuery.data, tailQuery.isSuccess]);

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
    copyTextToClipboard(logContent.map((line) => line.text).join('\n'));
  };

  const downloadUrl = `${apiBase}/task-runs/${encodeURIComponent(taskRunId)}/logs/merged`;
  const summaryErrorMessage = summaryQuery.isError ? (summaryQuery.error as Error).message : null;

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
      <div className="stack">
        {summaryErrorMessage ? <div className="notice error">{summaryErrorMessage}</div> : null}
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
        <p className="small">
          Task run <code className="text-xs">{taskRunId}</code> — {statusLabel}
        </p>
        <div style={{ maxHeight: '400px', overflowY: 'auto' }}>
          <div
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
              fontFamily: 'monospace',
            }}
          >
            {logContent.length === 0 ? (
              <div>{emptyLabel}</div>
            ) : (
              logContent.map((line) => (
                <div
                  key={line.id}
                  data-stream={line.stream}
                  style={{
                    borderLeft:
                      line.stream === 'stdout'
                        ? '2px solid #3b82f6'
                        : line.stream === 'stderr'
                          ? '2px solid #ef4444'
                          : line.stream === 'system'
                            ? '2px solid #22c55e'
                            : '2px solid transparent',
                    paddingLeft: '6px',
                    // dim system messages
                    opacity: line.stream === 'system' ? 0.7 : 1,
                  }}
                >
                  {line.text}
                </div>
              ))
            )}
          </div>
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
  stream
}: {
  apiBase: string;
  taskRunId: string;
  stream: 'stdout' | 'stderr';
}) {
  const [expanded, setExpanded] = useState(false);
  const [wrapLines, setWrapLines] = useState(true);

  const streamQuery = useQuery({
    queryKey: ['task-run-stream', taskRunId, stream],
    queryFn: () => fetchStream(apiBase, taskRunId, stream),
    enabled: !!taskRunId && expanded,
    retry: false,
  });

  const title = stream === 'stdout' ? 'Stdout' : 'Stderr';

  const handleCopy = () => {
    if (!streamQuery.data) return;
    copyTextToClipboard(streamQuery.data);
  };

  const downloadUrl = `${apiBase}/task-runs/${encodeURIComponent(taskRunId)}/logs/${stream}`;

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
  taskRunId
}: {
  apiBase: string;
  taskRunId: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const [wrapLines, setWrapLines] = useState(true);

  const diagQuery = useQuery({
    queryKey: ['task-run-diagnostics', taskRunId],
    queryFn: () => fetchDiagnostics(apiBase, taskRunId),
    enabled: !!taskRunId && expanded,
    retry: false,
  });

  const handleCopy = () => {
    if (!diagQuery.data) return;
    copyTextToClipboard(diagQuery.data);
  };

  const downloadUrl = `${apiBase}/task-runs/${encodeURIComponent(taskRunId)}/diagnostics`;

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
  const detailPoll = cfg?.pollIntervalsMs?.detail ?? 2000;
  const actionsOn = Boolean(cfg?.features?.temporalDashboard?.actionsEnabled);
  const debugOn = Boolean(cfg?.features?.temporalDashboard?.debugFieldsEnabled);
  const logStreamingEnabled = cfg?.features?.logStreamingEnabled !== false;

  const taskIdMatch = window.location.pathname.match(
    /^\/tasks\/(?:temporal\/|proposals\/|schedules\/|manifests\/)?([^/]+)$/,
  );
  const taskId = decodeTaskPathSegment(taskIdMatch ? taskIdMatch[1] : null);
  const encodedTaskId = taskId ? encodeURIComponent(taskId) : null;
  const search = useMemo(() => new URLSearchParams(window.location.search), []);
  const sourceTemporal = search.get('source') === 'temporal';

  const [actionError, setActionError] = useState<string | null>(null);
  const [liveUpdates, setLiveUpdates] = useState(true);

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

  const artifactsQuery = useQuery({
    queryKey: ['task-detail-artifacts', namespace, workflowId, runId],
    queryFn: async () => {
      const path = `${payload.apiBase}/executions/${encodeURIComponent(namespace)}/${encodeURIComponent(workflowId)}/${encodeURIComponent(runId)}/artifacts`;
      const response = await fetch(path);
      if (!response.ok) {
        throw new Error(`Artifacts: ${response.statusText}`);
      }
      return ArtifactListSchema.parse(await response.json());
    },
    enabled: Boolean(namespace && workflowId && runId),
    refetchInterval: liveUpdates && namespace && workflowId && runId ? detailPoll : false,
  });

  const runSummaryQuery = useQuery({
    queryKey: ['task-detail-run-summary', summaryArtifactRef],
    queryFn: () => fetchRunSummaryArtifact(payload.apiBase, summaryArtifactRef),
    enabled: Boolean(summaryArtifactRef),
    refetchInterval: liveUpdates && summaryArtifactRef ? detailPoll : false,
  });
  const runSummary = runSummaryQuery.data;
  const displayedSummary = runSummary?.operatorSummary || execution?.summary || '—';
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

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['task-detail', encodedTaskId] });
    void queryClient.invalidateQueries({ queryKey: ['task-detail-artifacts', namespace, workflowId, runId] });
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
            <Card label="Temporal Status">{execution.temporalStatus || '—'}</Card>
            <Card label="Current State">{execution.rawState || execution.state || '—'}</Card>
            {execution.closeStatus ? <Card label="Close Status">{execution.closeStatus}</Card> : null}
            {execution.waitingReason ? <Card label="Waiting Reason">{execution.waitingReason}</Card> : null}
            {execution.scheduledFor ? <Card label="Scheduled For">{formatWhen(execution.scheduledFor)}</Card> : null}
            <Card label="Created">{formatWhen(execution.createdAt)}</Card>
            <Card label="Latest Run">
              <code className="text-xs break-all">{runId || '—'}</code>
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
                    isTerminal={TERMINAL_STATES.has(execution.rawState || execution.state || '')}
                    autoExpand={showTaskRunAttachNotice}
                  />
                  <StaticLogPanel apiBase={payload.apiBase} taskRunId={resolvedTaskRunId} stream="stdout" />
                  <StaticLogPanel apiBase={payload.apiBase} taskRunId={resolvedTaskRunId} stream="stderr" />
                  <DiagnosticsPanel apiBase={payload.apiBase} taskRunId={resolvedTaskRunId} />
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
