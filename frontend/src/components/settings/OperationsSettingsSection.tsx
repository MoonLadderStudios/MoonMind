import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { z } from 'zod';

const WorkerSnapshotSchema = z.object({
  system: z
    .object({
      workersPaused: z.boolean().optional(),
      mode: z.string().optional(),
      version: z.string().optional(),
      updatedAt: z.string().optional(),
      reason: z.string().optional(),
    })
    .optional(),
  metrics: z
    .object({
      queued: z.number().optional(),
      running: z.number().optional(),
      staleRunning: z.number().optional(),
      isDrained: z.boolean().optional(),
    })
    .optional(),
  audit: z
    .object({
      latest: z
        .array(
          z.object({
            action: z.string().optional(),
            mode: z.string().optional(),
            reason: z.string().optional(),
            createdAt: z.string().optional(),
          }),
        )
        .optional(),
    })
    .optional(),
});

type WorkerSnapshot = z.infer<typeof WorkerSnapshotSchema>;

export interface WorkerPauseConfig {
  get: string;
  post: string;
  pollIntervalMs?: number;
}

export function OperationsSettingsSection({
  workerPauseConfig,
}: {
  workerPauseConfig: WorkerPauseConfig | null;
}) {
  const queryClient = useQueryClient();
  const [notice, setNotice] = useState<{ level: 'ok' | 'error'; text: string } | null>(
    null,
  );
  const [pauseMode, setPauseMode] = useState('drain');
  const [pauseReason, setPauseReason] = useState('');
  const [resumeReason, setResumeReason] = useState('');

  const {
    data: snapshot,
    isLoading,
    isError,
    error,
  } = useQuery<WorkerSnapshot>({
    queryKey: ['workers-snapshot'],
    queryFn: async () => {
      if (!workerPauseConfig || !workerPauseConfig.get) {
        throw new Error('Worker pause controls are not configured for this deployment.');
      }
      const response = await fetch(workerPauseConfig.get, {
        headers: { Accept: 'application/json' },
      });
      if (!response.ok) {
        throw new Error(`Failed to fetch worker status: ${response.statusText}`);
      }
      return WorkerSnapshotSchema.parse(await response.json());
    },
    enabled: workerPauseConfig !== null,
    refetchInterval:
      workerPauseConfig !== null
        ? Math.max(1000, Number(workerPauseConfig.pollIntervalMs) || 5000)
        : false,
  });

  const actionMutation = useMutation({
    mutationFn: async (
      payload:
        | { action: 'pause'; mode: string; reason: string }
        | { action: 'resume'; reason: string; forceResume?: boolean },
    ) => {
      if (!workerPauseConfig || !workerPauseConfig.post) {
        throw new Error('Worker pause controls are not configured for this deployment.');
      }
      const response = await fetch(workerPauseConfig.post, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/json',
        },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        const errorPayload = await response.json().catch(() => ({}));
        const detail =
          typeof errorPayload.detail === 'string'
            ? errorPayload.detail
            : `Server error: ${response.status}`;
        throw new Error(detail);
      }
      return WorkerSnapshotSchema.parse(await response.json());
    },
    onSuccess: (data, variables) => {
      setNotice({
        level: 'ok',
        text:
          variables.action === 'pause'
            ? 'Workers paused successfully.'
            : 'Workers resumed successfully.',
      });
      if (variables.action === 'pause') {
        setPauseReason('');
      } else {
        setResumeReason('');
      }
      queryClient.setQueryData(['workers-snapshot'], data);
    },
    onError: (mutationError: Error, variables) => {
      setNotice({
        level: 'error',
        text: `Failed to ${variables.action} workers: ${mutationError.message}`,
      });
    },
  });

  const handlePause = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!pauseMode || !pauseReason) {
      setNotice({ level: 'error', text: 'Pause mode and reason are required.' });
      return;
    }
    actionMutation.mutate({ action: 'pause', mode: pauseMode, reason: pauseReason });
  };

  const handleResume = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!resumeReason) {
      setNotice({ level: 'error', text: 'Resume reason is required.' });
      return;
    }
    let forceResume = false;
    if (
      snapshot?.metrics &&
      Object.prototype.hasOwnProperty.call(snapshot.metrics, 'isDrained') &&
      !snapshot.metrics.isDrained
    ) {
      const confirmed = window.confirm('Workers are not drained yet. Resume anyway?');
      if (!confirmed) {
        return;
      }
      forceResume = true;
    }
    actionMutation.mutate({ action: 'resume', reason: resumeReason, forceResume });
  };

  if (!workerPauseConfig) {
    return (
      <section className="rounded-3xl border border-amber-200 dark:border-amber-900/50 bg-amber-50 dark:bg-amber-900/20 p-6 text-sm text-amber-900 dark:text-amber-400 shadow-sm">
        Worker pause controls are not configured for this deployment.
      </section>
    );
  }

  const system = snapshot?.system ?? {};
  const metrics = snapshot?.metrics ?? {};
  const isPaused = Boolean(system.workersPaused);
  const stateLabel = isPaused
    ? system.mode === 'quiesce'
      ? 'Workers quiesced'
      : 'Workers draining'
    : 'Workers running';

  return (
    <div className="space-y-6">
      <section className="rounded-3xl border border-mm-border/80 bg-transparent p-6 shadow-sm">
        <div className="space-y-2">
          <h3 className="text-lg font-semibold text-slate-900 dark:text-white">Operations</h3>
          <p className="max-w-3xl text-sm text-slate-600 dark:text-slate-400">
            Worker pause, drain, quiesce, and recent operational audit actions live
            here under Settings.
          </p>
        </div>
      </section>

      <section className="rounded-3xl border border-mm-border/80 bg-transparent p-6 shadow-sm">
        {notice ? (
          <div
            className={`mb-4 rounded-2xl border px-4 py-3 text-sm shadow-sm ${
              notice.level === 'error'
                ? 'border-rose-200 dark:border-rose-900/50 bg-rose-50 dark:bg-rose-900/20 text-rose-700 dark:text-rose-400'
                : 'border-emerald-200 dark:border-emerald-900/50 bg-emerald-50 dark:bg-emerald-900/20 text-emerald-700 dark:text-emerald-400'
            }`}
          >
            {notice.text}
          </div>
        ) : null}

        {isLoading ? (
          <p className="text-sm text-slate-500 dark:text-slate-400">Loading worker status...</p>
        ) : isError ? (
          <p className="text-sm text-rose-700 dark:text-rose-400">{(error as Error).message}</p>
        ) : (
          <div className="space-y-6">
            <div className="space-y-2">
              <h4 className="text-lg font-semibold text-slate-900 dark:text-white">{stateLabel}</h4>
              <p className="text-sm text-slate-600 dark:text-slate-400">{system.reason || 'Normal operation'}</p>
              <p className="text-xs text-slate-500 dark:text-slate-400">
                Mode: {system.mode || (isPaused ? 'paused' : 'running')} | Version:{' '}
                {system.version || '-'} | Updated: {system.updatedAt || '-'}
              </p>
            </div>

            <div className="grid gap-4 md:grid-cols-4">
              <div className="rounded-2xl bg-slate-50 dark:bg-slate-800/50 p-4 text-center">
                <div className="text-sm font-medium text-slate-500 dark:text-slate-400">Queued</div>
                <div className="text-2xl font-bold text-slate-900 dark:text-white">{metrics.queued || 0}</div>
              </div>
              <div className="rounded-2xl bg-slate-50 dark:bg-slate-800/50 p-4 text-center">
                <div className="text-sm font-medium text-slate-500 dark:text-slate-400">Running</div>
                <div className="text-2xl font-bold text-slate-900 dark:text-white">{metrics.running || 0}</div>
              </div>
              <div className="rounded-2xl bg-slate-50 dark:bg-slate-800/50 p-4 text-center">
                <div className="text-sm font-medium text-slate-500 dark:text-slate-400">Stale</div>
                <div className="text-2xl font-bold text-slate-900 dark:text-white">
                  {metrics.staleRunning || 0}
                </div>
              </div>
              <div className="rounded-2xl bg-slate-50 dark:bg-slate-800/50 p-4 text-center">
                <div className="text-sm font-medium text-slate-500 dark:text-slate-400">Drained</div>
                <div className="text-2xl font-bold text-slate-900 dark:text-white">
                  {metrics.isDrained ? 'Yes' : 'No'}
                </div>
              </div>
            </div>

            <div className="grid gap-6 lg:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
              <section className="rounded-3xl border border-slate-200 dark:border-slate-800 p-5">
                <h4 className="text-base font-semibold text-slate-900 dark:text-white">Worker controls</h4>
                <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
                  Drain lets running jobs finish. Quiesce stops new claims immediately.
                </p>

                <form className="mt-5 space-y-4" onSubmit={handlePause}>
                  <fieldset className="space-y-3" disabled={actionMutation.isPending}>
                    <legend className="text-sm font-medium text-slate-700 dark:text-300">
                      Pause workers
                    </legend>
                    <label className="block space-y-2 text-sm font-medium text-slate-700 dark:text-slate-300">
                      <span>Mode</span>
                      <select
                        className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-white shadow-sm"
                        value={pauseMode}
                        onChange={(event) => setPauseMode(event.target.value)}
                      >
                        <option value="drain">Drain</option>
                        <option value="quiesce">Quiesce</option>
                      </select>
                    </label>
                    <label className="block space-y-2 text-sm font-medium text-slate-700 dark:text-slate-300">
                      <span>Reason</span>
                      <input
                        className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-white shadow-sm"
                        type="text"
                        maxLength={160}
                        value={pauseReason}
                        onChange={(event) => setPauseReason(event.target.value)}
                        required
                      />
                    </label>
                    <button
                      type="submit"
                      className="inline-flex items-center justify-center rounded-full bg-slate-900 dark:bg-slate-100 px-5 py-2.5 text-sm font-semibold text-white dark:text-slate-900 transition hover:bg-slate-800 dark:hover:bg-slate-200"
                    >
                      Pause workers
                    </button>
                  </fieldset>
                </form>

                <div className="my-6 border-t border-slate-200 dark:border-slate-800" />

                <form className="space-y-4" onSubmit={handleResume}>
                  <fieldset className="space-y-3" disabled={actionMutation.isPending}>
                    <legend className="text-sm font-medium text-slate-700 dark:text-slate-300">
                      Resume workers
                    </legend>
                    <label className="block space-y-2 text-sm font-medium text-slate-700 dark:text-slate-300">
                      <span>Reason</span>
                      <input
                        className="w-full rounded-xl border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-sm text-slate-900 dark:text-white shadow-sm"
                        type="text"
                        maxLength={160}
                        value={resumeReason}
                        onChange={(event) => setResumeReason(event.target.value)}
                        required
                      />
                    </label>
                    <button
                      type="submit"
                      className="inline-flex items-center justify-center rounded-full bg-emerald-600 dark:bg-emerald-500 px-5 py-2.5 text-sm font-semibold text-white dark:text-white transition hover:bg-emerald-500 dark:hover:bg-emerald-400"
                    >
                      Resume workers
                    </button>
                  </fieldset>
                </form>
              </section>

              <section className="rounded-3xl border border-slate-200 dark:border-slate-800 p-5">
                <h4 className="text-base font-semibold text-slate-900 dark:text-white">Recent actions</h4>
                <div className="mt-4 space-y-3">
                  {snapshot?.audit?.latest && snapshot.audit.latest.length > 0 ? (
                    snapshot.audit.latest.map((event, index) => (
                      <div
                        key={`${event.createdAt || 'event'}-${index}`}
                        className="rounded-2xl bg-slate-50 dark:bg-slate-800/50 p-4"
                      >
                        <div className="text-sm font-semibold text-slate-900 dark:text-white">
                          {(event.action || '-').toUpperCase()}
                          {event.mode ? ` | ${event.mode.toUpperCase()}` : ''}
                        </div>
                        <div className="mt-1 text-sm text-slate-600 dark:text-slate-400">
                          {event.reason || '(no reason)'}
                        </div>
                        <time
                          dateTime={event.createdAt}
                          className="mt-2 block text-xs text-slate-500 dark:text-slate-400"
                        >
                          {event.createdAt || '-'}
                        </time>
                      </div>
                    ))
                  ) : (
                    <p className="text-sm text-slate-500 dark:text-slate-400">No recent pause or resume actions.</p>
                  )}
                </div>
              </section>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
