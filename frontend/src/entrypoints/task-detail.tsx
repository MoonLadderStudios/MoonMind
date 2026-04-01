import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { z } from 'zod';

import { mountPage } from '../boot/mountPage';
import { BootPayload } from '../boot/parseBootPayload';
import { executionStatusPillClasses } from '../utils/executionStatusPillClasses';

type DashboardConfig = {
  pollIntervalsMs?: { list?: number; detail?: number; events?: number };
  features?: {
    temporalDashboard?: {
      actionsEnabled?: boolean;
      debugFieldsEnabled?: boolean;
    };
    logTailingEnabled?: boolean;
  };
  sources?: {
    temporal?: Record<string, string>;
  };
};

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
    attentionRequired: z.boolean().optional(),
    targetRuntime: z.string().nullable().optional(),
    targetSkill: z.string().nullable().optional(),
    model: z.string().nullable().optional(),
    effort: z.string().nullable().optional(),
    startingBranch: z.string().nullable().optional(),
    targetBranch: z.string().nullable().optional(),
    repository: z.string().nullable().optional(),
    publishMode: z.string().nullable().optional(),
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
        disabledReasons: z.record(z.string(), z.string()).optional(),
      })
      .passthrough()
      .optional(),
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

/** Fetch the plain-text merged-tail from the artifact-backed API. */
async function fetchMergedTail(apiBase: string, taskRunId: string): Promise<string> {
  const resp = await fetch(
    `${apiBase}/task-runs/${encodeURIComponent(taskRunId)}/logs/merged`,
    { credentials: 'include' },
  );
  if (!resp.ok) {
    if (resp.status === 404) return '';
    throw new Error(`Merged tail fetch failed: ${resp.status}`);
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
    throw new Error(`Stream ${stream} fetch failed: ${resp.status}`);
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
    throw new Error(`Diagnostics fetch failed: ${resp.status}`);
  }
  return resp.text();
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
  if (!resp.ok) return null;
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
}: {
  apiBase: string;
  taskRunId: string;
  isTerminal: boolean;
}) {
  const [logContent, setLogContent] = useState<LogLine[]>([]);
  const [viewerState, setViewerState] = useState<LogViewerState>('starting');
  const [expanded, setExpanded] = useState(false);
  const isVisible = usePageVisibility();
  const lastSeqRef = useRef<number | null>(null);
  const esRef = useRef<EventSource | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
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

  // Auto-scroll to the bottom when new content arrives.
  useEffect(() => {
    bottomRef.current?.scrollIntoView?.({ behavior: 'smooth' });
  }, [logContent]);

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
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span>Live Logs</span>
          {expanded && (
            <div className="button-group" style={{ fontSize: '0.9rem', fontWeight: 'normal' }} onClick={(e) => e.stopPropagation()}>
              <label style={{ display: 'flex', alignItems: 'center', gap: '0.25rem' }}>
                <input type="checkbox" checked={wrapLines} onChange={(e) => setWrapLines(e.target.checked)} />
                <span className="small">Wrap lines</span>
              </label>
              <button className="secondary small" onClick={handleCopy}>Copy</button>
              <a className="button secondary small" href={downloadUrl} target="_blank" rel="noreferrer">Download</a>
            </div>
          )}
        </div>
      </summary>
      <div className="stack">
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
            fontFamily: 'monospace'
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
                  borderLeft: line.stream === 'stdout' ? '2px solid #3b82f6' : 
                              line.stream === 'stderr' ? '2px solid #ef4444' : 
                              line.stream === 'system' ? '2px solid #22c55e' : '2px solid transparent',
                  paddingLeft: '6px',
                  // dim system messages
                  opacity: line.stream === 'system' ? 0.7 : 1
                }}
              >
                {line.text}
              </div>
            ))
          )}
        </div>
        <div ref={bottomRef} />
      </div>
    </div>
    </details>
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
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span>{title}</span>
          {expanded && (
            <div className="button-group" style={{ fontSize: '0.9rem', fontWeight: 'normal' }} onClick={(e) => e.stopPropagation()}>
              <label style={{ display: 'flex', alignItems: 'center', gap: '0.25rem' }}>
                <input type="checkbox" checked={wrapLines} onChange={(e) => setWrapLines(e.target.checked)} />
                <span className="small">Wrap lines</span>
              </label>
              <button className="secondary small" onClick={handleCopy}>Copy</button>
              <a className="button secondary small" href={downloadUrl} target="_blank" rel="noreferrer">Download</a>
            </div>
          )}
        </div>
      </summary>
      <div className="stack">
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
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span>Diagnostics</span>
          {expanded && (
            <div className="button-group" style={{ fontSize: '0.9rem', fontWeight: 'normal' }} onClick={(e) => e.stopPropagation()}>
              <label style={{ display: 'flex', alignItems: 'center', gap: '0.25rem' }}>
                <input type="checkbox" checked={wrapLines} onChange={(e) => setWrapLines(e.target.checked)} />
                <span className="small">Wrap lines</span>
              </label>
              <button className="secondary small" onClick={handleCopy}>Copy</button>
              <a className="button secondary small" href={downloadUrl} target="_blank" rel="noreferrer">Download</a>
            </div>
          )}
        </div>
      </summary>
      <div className="stack">
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

export function TaskDetailPage({ payload }: { payload: BootPayload }) {
  const queryClient = useQueryClient();
  const cfg = readDashboardConfig(payload);
  const detailPoll = cfg?.pollIntervalsMs?.detail ?? 2000;
  const actionsOn = Boolean(cfg?.features?.temporalDashboard?.actionsEnabled);
  const debugOn = Boolean(cfg?.features?.temporalDashboard?.debugFieldsEnabled);
  const logTailingEnabled = cfg?.features?.logTailingEnabled !== false;

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

  const isUuidLike = (val: string) =>
    /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(val);
  const explicitTaskRunId = execution?.taskRunId || execution?.task_run_id || '';
  const resolvedTaskRunId = explicitTaskRunId || (isUuidLike(runId) ? runId : '');

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

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['task-detail', encodedTaskId] });
    void queryClient.invalidateQueries({ queryKey: ['task-detail-artifacts', namespace, workflowId, runId] });
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

  const cancelMutation = useMutation({
    mutationFn: async () => {
      const response = await fetch(`${payload.apiBase}/executions/${encodeURIComponent(workflowId)}/cancel`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({}),
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
    updateMutation.mutate({ updateName: 'Pause' });
  };

  const onResume = () => {
    setActionError(null);
    updateMutation.mutate({ updateName: 'Resume' });
  };

  const onApprove = () => {
    setActionError(null);
    updateMutation.mutate({ updateName: 'Approve' });
  };

  const onCancel = () => {
    setActionError(null);
    if (!window.confirm('Cancel this task?')) return;
    cancelMutation.mutate();
  };

  const actions = execution?.actions;
  const busy = updateMutation.isPending || cancelMutation.isPending;

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
            {execution.targetRuntime ? <Card label="Runtime">{execution.targetRuntime}</Card> : null}
            {execution.targetSkill ? <Card label="Skill">{execution.targetSkill}</Card> : null}
            {execution.model ? (
              <Card label="Model">
                <code className="text-xs">{execution.model}</code>
              </Card>
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

          <section>
            <h3>Summary</h3>
            <p className="whitespace-pre-wrap">{execution.summary || '—'}</p>
          </section>

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

          {actionsOn && actions ? (
            <section className="stack">
              <div>
                <h3>Actions</h3>
                <p className="small">Only actions valid for the current task state are shown.</p>
              </div>
              <div className="actions">
                {actions.canSetTitle ? (
                  <button type="button" disabled={busy} className="secondary" onClick={onRename}>
                    Rename
                  </button>
                ) : null}
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
                {actions.canRerun ? (
                  <button type="button" disabled={busy} className="secondary" onClick={onRerun}>
                    Rerun
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
            </section>
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
            {logTailingEnabled ? (
              resolvedTaskRunId ? (
                <>
                  <LiveLogsPanel
                    apiBase={payload.apiBase}
                    taskRunId={resolvedTaskRunId}
                    isTerminal={TERMINAL_STATES.has(execution.rawState || execution.state || '')}
                  />
                  <StaticLogPanel apiBase={payload.apiBase} taskRunId={resolvedTaskRunId} stream="stdout" />
                  <StaticLogPanel apiBase={payload.apiBase} taskRunId={resolvedTaskRunId} stream="stderr" />
                  <DiagnosticsPanel apiBase={payload.apiBase} taskRunId={resolvedTaskRunId} />
                </>
              ) : (
                <>
                  <h3>Live Logs</h3>
                  <p className="small">
                    Live log tailing requires a task run id. Waiting for the task to start executing...
                  </p>
                </>
              )
            ) : (
              <>
                <h3>Live Logs</h3>
                <p className="small">Live log tailing is disabled in the server dashboard config.</p>
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

mountPage(TaskDetailPage);
