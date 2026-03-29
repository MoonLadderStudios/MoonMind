import { useMemo, useState, type ReactNode } from 'react';
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
    publishMode: z.string().nullable().optional(),
    scheduledFor: z.string().nullable().optional(),
    createdAt: z.string(),
    startedAt: z.string().nullable().optional(),
    updatedAt: z.string().optional(),
    closedAt: z.string().nullable().optional(),
    taskRunId: z.string().nullable().optional(),
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

const ArtifactListSchema = z.object({
  artifacts: z
    .array(
      z
        .object({
          artifactId: z.string(),
          contentType: z.string().nullable().optional(),
          sizeBytes: z.number().nullable().optional(),
          status: z.string().optional(),
          downloadUrl: z.string().nullable().optional(),
        })
        .passthrough(),
    )
    .default([]),
});

function readDashboardConfig(payload: BootPayload): DashboardConfig | undefined {
  const raw = payload.initialData as { dashboardConfig?: DashboardConfig } | undefined;
  return raw?.dashboardConfig;
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
  const taskId = taskIdMatch ? taskIdMatch[1] : null;
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
            {execution.taskRunId ? (
              <Card label="Task Run">
                <code className="text-xs break-all">{execution.taskRunId}</code>
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
                              '—'
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

          <section>
            <h3>Live Logs</h3>
            {logTailingEnabled && execution.taskRunId ? (
              <p className="small">
                Task run <code className="text-xs">{execution.taskRunId}</code> can use the same live tailing
                endpoints as the legacy dashboard client.
              </p>
            ) : (
              <p className="small">
                Live log tailing requires a task run id and enabled log tailing in the server dashboard config.
              </p>
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
