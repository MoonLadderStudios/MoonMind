import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { mountPage } from '../boot/mountPage';
import { BootPayload } from '../boot/parseBootPayload';

interface WorkerSnapshot {
  system?: {
    workersPaused?: boolean;
    mode?: string;
    version?: string;
    updatedAt?: string;
    reason?: string;
  };
  metrics?: {
    queued?: number;
    running?: number;
    staleRunning?: number;
    isDrained?: boolean;
  };
  audit?: {
    latest?: { action?: string; mode?: string; reason?: string; createdAt?: string; }[];
  };
}

interface WorkerPauseConfig {
  get: string;
  post: string;
  pollIntervalMs?: number;
}

function WorkersPage({ payload }: { payload: BootPayload }) {
  const queryClient = useQueryClient();
  const workerPauseConfig = (payload.initialData as { workerPause?: WorkerPauseConfig })?.workerPause || null;
  const [notice, setNotice] = useState<{ level: 'ok' | 'error', text: string } | null>(null);

  // Form states
  const [pauseMode, setPauseMode] = useState('drain');
  const [pauseReason, setPauseReason] = useState('');
  const [resumeReason, setResumeReason] = useState('');

  const { data: snapshot, isLoading, isError, error } = useQuery<WorkerSnapshot>({
    queryKey: ['workers-snapshot'],
    queryFn: async () => {
      if (!workerPauseConfig || !workerPauseConfig.get) {
        throw new Error('Worker pause controls are not configured for this deployment.');
      }
      const response = await fetch(workerPauseConfig.get);
      if (!response.ok) {
        throw new Error(`Failed to fetch worker status: ${response.statusText}`);
      }
      return response.json();
    },
    enabled: !!workerPauseConfig,
    refetchInterval: workerPauseConfig ? Math.max(1000, Number(workerPauseConfig.pollIntervalMs) || 5000) : false,
  });

  const actionMutation = useMutation({
    mutationFn: async (payload: { action: 'pause'; mode?: string; reason: string } | { action: 'resume'; reason: string; forceResume?: boolean }) => {
      if (!workerPauseConfig || !workerPauseConfig.post) throw new Error('Worker pause controls are not configured for this deployment.');
      const response = await fetch(workerPauseConfig.post, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        const errData = await response.json().catch(() => ({}));
        throw new Error(errData.detail || errData.message || `Server error: ${response.status}`);
      }
      return response.json();
    },
    onSuccess: (data, variables) => {
      setNotice({ level: 'ok', text: variables.action === 'pause' ? 'Workers paused successfully.' : 'Workers resumed successfully.' });
      if (variables.action === 'pause') {
        setPauseReason('');
      } else {
        setResumeReason('');
      }
      queryClient.setQueryData(['workers-snapshot'], data);
    },
    onError: (error: Error, variables) => {
      setNotice({ level: 'error', text: `Failed to ${variables.action} workers: ${error.message}` });
    },
  });

  const handlePause = (e: React.FormEvent) => {
    e.preventDefault();
    if (!pauseMode || !pauseReason) {
      setNotice({ level: 'error', text: 'Pause mode and reason are required.' });
      return;
    }
    actionMutation.mutate({ action: 'pause', mode: pauseMode, reason: pauseReason });
  };

  const handleResume = (e: React.FormEvent) => {
    e.preventDefault();
    if (!resumeReason) {
      setNotice({ level: 'error', text: 'Resume reason is required.' });
      return;
    }
    let forceResume = false;
    if (snapshot?.metrics && Object.prototype.hasOwnProperty.call(snapshot.metrics, "isDrained") && !snapshot.metrics.isDrained) {
      const confirmed = window.confirm("Workers are not drained yet. Resume anyway?");
      if (!confirmed) return;
      forceResume = true;
    }
    actionMutation.mutate({ action: 'resume', reason: resumeReason, forceResume });
  };

  const system = snapshot?.system || {};
  const metrics = snapshot?.metrics || {};
  const isPaused = Boolean(system.workersPaused);
  const stateLabel = isPaused ? (system.mode === 'quiesce' ? 'Workers Quiesced' : 'Workers Draining') : 'Workers Running';

  return (
    <div className="max-w-6xl mx-auto p-6 space-y-6">
      <header className="border-b border-gray-200 pb-4 mb-6">
        <h2 className="text-2xl font-bold text-gray-900 tracking-tight">Worker controls</h2>
        <p className="text-sm text-gray-500 mt-1">Pause or resume worker processing.</p>
      </header>

      <div className="bg-white rounded-lg shadow-sm p-6 border border-gray-100">
        {notice && (
          <div data-system-settings-notice>
            <div className={`p-4 rounded-md mb-4 border ${notice.level === 'error' ? 'bg-red-50 text-red-700 border-red-200' : 'bg-green-50 text-green-700 border-green-200'}`}>{notice.text}</div>
          </div>
        )}

        <div className="system-settings">
          <section className="card mb-6">
            <div data-system-settings-summary>
              {isLoading ? (
                <p className="text-gray-500 italic animate-pulse">Loading worker status...</p>
              ) : isError ? (
                <p className="error">{(error as Error).message}</p>
              ) : (
                <div className="stack space-y-4">
                  <div className="stack">
                    <h3 className="text-lg font-semibold">{stateLabel}</h3>
                    <p className="page-meta">{system.reason || 'Normal operation'}</p>
                    <p className="text-sm text-gray-500">
                      Mode: {system.mode || (isPaused ? 'paused' : 'running')} | Version: {system.version || '-'} | Updated: {system.updatedAt || '-'}
                    </p>
                  </div>
                  <div className="grid grid-cols-4 gap-4">
                    <div className="system-settings-metric bg-gray-50 p-4 rounded text-center">
                      <span className="block text-sm font-medium text-gray-500">Queued</span>
                      <span className="block text-2xl font-bold">{metrics.queued || 0}</span>
                    </div>
                    <div className="system-settings-metric bg-gray-50 p-4 rounded text-center">
                      <span className="block text-sm font-medium text-gray-500">Running</span>
                      <span className="block text-2xl font-bold">{metrics.running || 0}</span>
                    </div>
                    <div className="system-settings-metric bg-gray-50 p-4 rounded text-center">
                      <span className="block text-sm font-medium text-gray-500">Stale</span>
                      <span className="block text-2xl font-bold">{metrics.staleRunning || 0}</span>
                    </div>
                    <div className="system-settings-metric bg-gray-50 p-4 rounded text-center">
                      <span className="block text-sm font-medium text-gray-500">Drained</span>
                      <span className="block text-2xl font-bold">{metrics.isDrained ? 'Yes' : 'No'}</span>
                    </div>
                  </div>
                </div>
              )}
            </div>
          </section>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <section className="card system-settings-forms border p-4 rounded-lg">
              <h3 className="font-semibold mb-2">Worker Controls</h3>
              <p className="text-sm text-gray-500 mb-4">
                Drain lets running jobs finish; Quiesce stops new claims immediately.
              </p>
              <form data-system-settings-form="pause" className="space-y-4" onSubmit={handlePause}>
                <fieldset disabled={actionMutation.isPending} className="space-y-3">
                  <legend className="font-medium">Pause Workers</legend>
                  <label className="block">
                    <span className="block text-sm font-medium text-gray-700">Mode</span>
                    <select name="mode" required value={pauseMode} onChange={(e) => setPauseMode(e.target.value)} className="mt-1 block w-full rounded-md border-gray-300 shadow-sm">
                      <option value="drain">Drain (default)</option>
                      <option value="quiesce">Quiesce</option>
                    </select>
                  </label>
                  <label className="block">
                    <span className="block text-sm font-medium text-gray-700">Reason</span>
                    <input type="text" name="reason" maxLength={160} required value={pauseReason} onChange={(e) => setPauseReason(e.target.value)} className="mt-1 block w-full rounded-md border-gray-300 shadow-sm" />
                  </label>
                  <button type="submit" className="bg-blue-600 text-white px-4 py-2 rounded shadow hover:bg-blue-700">Pause Workers</button>
                </fieldset>
              </form>
              <div className="my-6 border-t border-gray-200"></div>
              <form data-system-settings-form="resume" className="space-y-4" onSubmit={handleResume}>
                <fieldset disabled={actionMutation.isPending} className="space-y-3">
                  <legend className="font-medium">Resume Workers</legend>
                  <label className="block">
                    <span className="block text-sm font-medium text-gray-700">Reason</span>
                    <input type="text" name="reason" maxLength={160} required value={resumeReason} onChange={(e) => setResumeReason(e.target.value)} className="mt-1 block w-full rounded-md border-gray-300 shadow-sm" />
                  </label>
                  <button type="submit" className="bg-green-600 text-white px-4 py-2 rounded shadow hover:bg-green-700">Resume Workers</button>
                </fieldset>
              </form>
            </section>

            <section className="system-settings-audit border p-4 rounded-lg">
              <h3 className="font-semibold mb-4">Recent Actions</h3>
              <div data-system-settings-audit>
                {snapshot?.audit?.latest && snapshot.audit.latest.length > 0 ? (
                  <ul className="space-y-3">
                    {snapshot.audit.latest.map((event: { action?: string; mode?: string; reason?: string; createdAt?: string; }, i: number) => (
                      <li key={i} className="text-sm">
                        <strong className="block">{(event?.action || '-').toUpperCase()}{event?.mode ? ` | ${event.mode.toUpperCase()}` : ''}</strong>
                        <span className="block text-gray-600">{event?.reason || '(no reason)'}</span>
                        <time dateTime={event?.createdAt} className="block text-xs text-gray-400">{event?.createdAt || '-'}</time>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-sm text-gray-500">No recent pause or resume actions.</p>
                )}
              </div>
            </section>
          </div>
        </div>
      </div>
    </div>
  );
}

mountPage(WorkersPage);
