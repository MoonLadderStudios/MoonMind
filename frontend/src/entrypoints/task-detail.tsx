import { useMemo, useState, type ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { mountPage } from '../boot/mountPage';
import { BootPayload } from '../boot/parseBootPayload';

import { z } from 'zod';

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
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

function Card({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <div className="rounded border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/40 p-3 text-sm">
      <div className="font-semibold text-gray-600 dark:text-gray-400 text-xs uppercase tracking-wide mb-1">
        {label}
      </div>
      <div className="text-gray-900 dark:text-gray-100 break-words">{children}</div>
    </div>
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
  const taskId = taskIdMatch ? taskIdMatch[1] : null;
  const encodedTaskId = taskId ? encodeURIComponent(taskId) : null;
  const search = useMemo(() => new URLSearchParams(window.location.search), []);
  const sourceTemporal = search.get('source') === 'temporal';

  const [actionError, setActionError] = useState<string | null>(null);

  const detailQuery = useQuery({
    queryKey: ['task-detail', encodedTaskId, sourceTemporal],
    queryFn: async () => {
      if (!encodedTaskId) throw new Error('Task ID is required.');
      const q = sourceTemporal ? '?source=temporal' : '';
      const response = await fetch(`${payload.apiBase}/executions/${encodedTaskId}${q}`);
      if (!response.ok) {
        throw new Error(`Failed to fetch task: ${response.statusText}`);
      }
      return ExecutionDetailSchema.parse(await response.json());
    },
    enabled: Boolean(encodedTaskId),
    refetchInterval: detailPoll,
  });

  const ex = detailQuery.data;
  const wfId = ex?.workflowId || ex?.taskId || taskId || '';
  const runId = ex?.temporalRunId || ex?.runId || '';
  const ns = ex?.namespace || '';

  const artifactsQuery = useQuery({
    queryKey: ['task-detail-artifacts', ns, wfId, runId],
    queryFn: async () => {
      const path = `${payload.apiBase}/executions/${encodeURIComponent(ns)}/${encodeURIComponent(wfId)}/${encodeURIComponent(runId)}/artifacts`;
      const response = await fetch(path);
      if (!response.ok) {
        throw new Error(`Artifacts: ${response.statusText}`);
      }
      return ArtifactListSchema.parse(await response.json());
    },
    enabled: Boolean(ns && wfId && runId),
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['task-detail', encodedTaskId] });
    void queryClient.invalidateQueries({ queryKey: ['task-detail-artifacts', ns, wfId, runId] });
  };

  const updateMutation = useMutation({
    mutationFn: async (body: Record<string, unknown>) => {
      const response = await fetch(`${payload.apiBase}/executions/${encodeURIComponent(wfId)}/update`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify(body),
      });
      if (!response.ok) {
        const t = await response.text();
        throw new Error(t || response.statusText);
      }
      return response.json();
    },
    onSuccess: invalidate,
    onError: (e: Error) => setActionError(e.message),
  });

  const cancelMutation = useMutation({
    mutationFn: async () => {
      const response = await fetch(`${payload.apiBase}/executions/${encodeURIComponent(wfId)}/cancel`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({}),
      });
      if (!response.ok) {
        const t = await response.text();
        throw new Error(t || response.statusText);
      }
      return response.json();
    },
    onSuccess: invalidate,
    onError: (e: Error) => setActionError(e.message),
  });

  const onRename = () => {
    setActionError(null);
    const title = window.prompt('New task title', ex?.title || '');
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

  const actions = ex?.actions;
  const busy = updateMutation.isPending || cancelMutation.isPending;

  return (
    <div className="max-w-5xl mx-auto p-6 space-y-6 text-gray-900 dark:text-gray-100">
      <header className="border-b border-gray-200 dark:border-gray-700 pb-4">
        <h2 className="text-2xl font-bold tracking-tight">Temporal Task Detail</h2>
        <p className="text-sm text-gray-500 dark:text-gray-400 mt-1 font-mono break-all">
          {taskId}
        </p>
      </header>

      {actionError ? (
        <div className="p-3 rounded border border-red-300 bg-red-50 dark:bg-red-900/20 text-red-800 dark:text-red-200 text-sm">
          {actionError}
        </div>
      ) : null}

      {detailQuery.isLoading ? (
        <p className="text-gray-500 animate-pulse">Loading task…</p>
      ) : detailQuery.isError ? (
        <div className="p-4 rounded border border-red-200 text-red-700">
          {(detailQuery.error as Error).message}
        </div>
      ) : ex ? (
        <>
          {actionsOn && actions ? (
            <div className="flex flex-wrap gap-2">
              {actions.canSetTitle ? (
                <button
                  type="button"
                  disabled={busy}
                  className="px-3 py-1.5 rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 text-sm"
                  onClick={onRename}
                >
                  Rename
                </button>
              ) : null}
              {actions.canPause ? (
                <button
                  type="button"
                  disabled={busy}
                  className="px-3 py-1.5 rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 text-sm"
                  onClick={onPause}
                >
                  Pause
                </button>
              ) : null}
              {actions.canResume ? (
                <button
                  type="button"
                  disabled={busy}
                  className="px-3 py-1.5 rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 text-sm"
                  onClick={onResume}
                >
                  Resume
                </button>
              ) : null}
              {actions.canApprove ? (
                <button
                  type="button"
                  disabled={busy}
                  className="px-3 py-1.5 rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 text-sm"
                  onClick={onApprove}
                >
                  Approve
                </button>
              ) : null}
              {actions.canRerun ? (
                <button
                  type="button"
                  disabled={busy}
                  className="px-3 py-1.5 rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 text-sm"
                  onClick={onRerun}
                >
                  Rerun
                </button>
              ) : null}
              {actions.canCancel ? (
                <button
                  type="button"
                  disabled={busy}
                  className="px-3 py-1.5 rounded border border-red-300 text-red-800 dark:text-red-300 text-sm"
                  onClick={onCancel}
                >
                  Cancel
                </button>
              ) : null}
            </div>
          ) : null}

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <Card label="Status">{ex.status}</Card>
            <Card label="Source">Temporal</Card>
            <Card label="Title">{ex.title}</Card>
            <Card label="Workflow Type">{ex.workflowType || '—'}</Card>
            {ex.targetRuntime ? <Card label="Runtime">{ex.targetRuntime}</Card> : null}
            {ex.model ? (
              <Card label="Model">
                <code className="text-xs">{ex.model}</code>
              </Card>
            ) : null}
            {ex.effort ? <Card label="Effort">{ex.effort}</Card> : null}
            {ex.targetSkill ? <Card label="Skill">{ex.targetSkill}</Card> : null}
            {ex.startingBranch ? (
              <Card label="Starting Branch">
                <code className="text-xs">{ex.startingBranch}</code>
              </Card>
            ) : null}
            {ex.targetBranch ? (
              <Card label="Target Branch">
                <code className="text-xs">{ex.targetBranch}</code>
              </Card>
            ) : null}
            {ex.publishMode ? (
              <Card label="Publish Mode">
                <code className="text-xs">{ex.publishMode}</code>
              </Card>
            ) : null}
            {ex.scheduledFor ? <Card label="Scheduled For">{formatWhen(ex.scheduledFor)}</Card> : null}
            <Card label="Created">{formatWhen(ex.createdAt)}</Card>
            <Card label="Latest Run">
              <code className="text-xs break-all">{runId || '—'}</code>
            </Card>
            <Card label="Started">{formatWhen(ex.startedAt)}</Card>
            <Card label="Updated">{formatWhen(ex.updatedAt)}</Card>
            <Card label="Closed">{formatWhen(ex.closedAt)}</Card>
            <Card label="Workflow ID">
              <code className="text-xs break-all">{wfId}</code>
            </Card>
          </div>

          <section className="space-y-2">
            <h3 className="text-lg font-semibold">Summary</h3>
            <p className="text-sm text-gray-700 dark:text-gray-300 whitespace-pre-wrap">{ex.summary || '—'}</p>
          </section>

          {ex.waitingReason ? (
            <section className="space-y-2">
              <h3 className="text-lg font-semibold">Waiting Reason</h3>
              <p className="text-sm">{ex.waitingReason}</p>
            </section>
          ) : null}

          {ex.attentionRequired ? (
            <section className="rounded border border-amber-300 bg-amber-50 dark:bg-amber-900/20 p-3 text-sm">
              <strong>Attention required</strong> — this task is waiting for external input before it can
              continue.
            </section>
          ) : null}

          <section className="space-y-2">
            <h3 className="text-lg font-semibold">Timeline</h3>
            <div className="overflow-x-auto border border-gray-200 dark:border-gray-700 rounded">
              <table className="min-w-full text-sm">
                <thead className="bg-gray-50 dark:bg-gray-800">
                  <tr>
                    <th className="text-left px-3 py-2">Stage</th>
                    <th className="text-left px-3 py-2">Timestamp</th>
                    <th className="text-left px-3 py-2">Detail</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
                  <tr>
                    <td className="px-3 py-2">Started</td>
                    <td className="px-3 py-2">{formatWhen(ex.startedAt)}</td>
                    <td className="px-3 py-2">Execution created.</td>
                  </tr>
                  <tr>
                    <td className="px-3 py-2">Last update</td>
                    <td className="px-3 py-2">{formatWhen(ex.updatedAt)}</td>
                    <td className="px-3 py-2">State: {(ex.state || '').replaceAll('_', ' ')}</td>
                  </tr>
                  {ex.waitingReason || ex.attentionRequired ? (
                    <tr>
                      <td className="px-3 py-2">Waiting</td>
                      <td className="px-3 py-2">{formatWhen(ex.updatedAt)}</td>
                      <td className="px-3 py-2">
                        {ex.waitingReason || 'Awaiting external input.'}
                        {ex.attentionRequired ? ' Attention required.' : ''}
                      </td>
                    </tr>
                  ) : null}
                  {ex.closedAt ? (
                    <tr>
                      <td className="px-3 py-2">Closed</td>
                      <td className="px-3 py-2">{formatWhen(ex.closedAt)}</td>
                      <td className="px-3 py-2">
                        Close status: {ex.closeStatus || ex.temporalStatus || '—'}
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          </section>

          <section className="space-y-2">
            <h3 className="text-lg font-semibold">Artifacts</h3>
            {artifactsQuery.isLoading ? (
              <p className="text-sm text-gray-500">Loading artifacts…</p>
            ) : artifactsQuery.isError ? (
              <p className="text-sm text-amber-700 dark:text-amber-300">
                {(artifactsQuery.error as Error).message}
              </p>
            ) : (
              <div className="overflow-x-auto border border-gray-200 dark:border-gray-700 rounded">
                <table className="min-w-full text-sm">
                  <thead className="bg-gray-50 dark:bg-gray-800">
                    <tr>
                      <th className="text-left px-3 py-2">Artifact</th>
                      <th className="text-left px-3 py-2">Size</th>
                      <th className="text-left px-3 py-2">Status</th>
                      <th className="text-left px-3 py-2">Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(artifactsQuery.data?.artifacts || []).length === 0 ? (
                      <tr>
                        <td colSpan={4} className="px-3 py-4 text-center text-gray-500">
                          No artifacts.
                        </td>
                      </tr>
                    ) : (
                      (artifactsQuery.data?.artifacts || []).map((a) => (
                        <tr key={a.artifactId} className="border-t border-gray-200 dark:border-gray-700">
                          <td className="px-3 py-2 font-mono text-xs break-all">{a.artifactId}</td>
                          <td className="px-3 py-2">{a.sizeBytes ?? '—'}</td>
                          <td className="px-3 py-2">{String(a.status ?? '—')}</td>
                          <td className="px-3 py-2">
                            {a.downloadUrl ? (
                              <a
                                className="text-blue-600 dark:text-blue-400 hover:underline"
                                href={a.downloadUrl}
                              >
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

          <section className="space-y-2">
            <h3 className="text-lg font-semibold">Live Logs</h3>
            {logTailingEnabled && ex.taskRunId ? (
              <p className="text-sm text-gray-600 dark:text-gray-400">
                Task run <code className="text-xs">{ex.taskRunId}</code>: live EventSource tailing matches the
                legacy <code className="text-xs">dashboard.js</code> client (same server endpoints). A React
                stream UI can be wired to the same URLs from{' '}
                <code className="text-xs">dashboardConfig.sources.temporal</code> when needed.
              </p>
            ) : (
              <p className="text-sm text-gray-600 dark:text-gray-400">
                Live log tailing requires a task run id and enabled log tailing (see server dashboard config).
              </p>
            )}
          </section>

          {debugOn && ex.debugFields ? (
            <section className="space-y-2">
              <h3 className="text-lg font-semibold">Debug Metadata</h3>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-sm">
                {Object.entries(ex.debugFields).map(([k, v]) => (
                  <Card key={k} label={k}>
                    {typeof v === 'object' ? JSON.stringify(v) : String(v)}
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
