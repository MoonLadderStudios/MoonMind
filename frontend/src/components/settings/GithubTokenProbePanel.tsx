import { FormEvent, useEffect, useRef, useState } from 'react';

type ProbeMode = 'publish' | 'readiness' | 'full_pr_automation';
type ReadObservation = 'verified' | 'denied' | 'not_found' | 'unavailable' | 'not_checked';
type BranchObservation =
  | 'verified'
  | 'missing'
  | 'empty_repository'
  | 'not_found'
  | 'denied'
  | 'unavailable'
  | 'not_checked';

interface DiagnosticEntry {
  operation: string;
  httpStatus?: number | null;
  message?: string | null;
  retryable?: boolean;
}

interface ProbeResponse {
  connectionId?: string;
  repo?: string;
  credentialSource?: { resolved?: boolean };
  repositoryAccessible?: boolean | null;
  defaultBranchAccessible?: boolean | null;
  pullRequestAccessible?: boolean | null;
  remoteDefaultBranch?: string | null;
  testedBranch?: string | null;
  reportedPermissions?: Record<string, boolean> | null;
  retryAfterSeconds?: number | null;
  observations?: { read?: ReadObservation; branch?: BranchObservation; write?: string };
  diagnostics?: DiagnosticEntry[];
  limitations?: string[];
}

interface Notice {
  level: 'ok' | 'error';
  text: string;
}

/** The selected connection the test runs against; never a global token. */
export interface ProbeConnection {
  id: string;
  displayName: string;
  policyRevision: number;
  credentialRevision: number;
  assignmentCount: number;
  lifecycle: string;
}

export interface GithubTokenProbePanelProps {
  connection: ProbeConnection;
  canRunProbe: boolean;
  onNotice?: ((notice: Notice | null) => void) | undefined;
  initialRepo?: string;
}

const MODE_OPTIONS: ReadonlyArray<{ value: ProbeMode; label: string }> = [
  { value: 'publish', label: 'Branch and pull request reads' },
  { value: 'readiness', label: 'Status, checks, and issue reads' },
  { value: 'full_pr_automation', label: 'All pull request automation reads' },
];

const READ_SUMMARY: Record<ReadObservation, { text: string; tone: string }> = {
  verified: {
    text: 'Read access verified',
    tone: 'border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900/50 dark:bg-emerald-900/20 dark:text-emerald-300',
  },
  denied: {
    text: 'Read access denied for this repository',
    tone: 'border-rose-200 bg-rose-50 text-rose-800 dark:border-rose-900/50 dark:bg-rose-900/20 dark:text-rose-300',
  },
  not_found: {
    text: 'Repository not found, or this connection cannot see it. GitHub reports private repositories it does not share as not found; check the name and the connection’s repository access.',
    tone: 'border-rose-200 bg-rose-50 text-rose-800 dark:border-rose-900/50 dark:bg-rose-900/20 dark:text-rose-300',
  },
  unavailable: {
    text: 'GitHub or the credential store was unavailable, so access is unknown. This is not a denial; test again.',
    tone: 'border-amber-200 bg-amber-50 text-amber-900 dark:border-amber-900/50 dark:bg-amber-900/20 dark:text-amber-200',
  },
  not_checked: {
    text: 'Read access was not checked',
    tone: 'border-slate-200 bg-slate-50 text-slate-700 dark:border-slate-700 dark:bg-slate-800/50 dark:text-slate-300',
  },
};

function AccessibilityPill({
  label,
  value,
  falseLabel = 'not readable',
}: {
  label: string;
  value: boolean | null | undefined;
  falseLabel?: string;
}) {
  let toneClass =
    'border-slate-200 bg-slate-50 text-slate-600 dark:border-slate-700 dark:bg-slate-800/50 dark:text-slate-300';
  let valueLabel = 'unknown';
  if (value === true) {
    toneClass =
      'border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900/50 dark:bg-emerald-900/20 dark:text-emerald-300';
    valueLabel = 'readable';
  } else if (value === false) {
    toneClass =
      'border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-900/50 dark:bg-rose-900/20 dark:text-rose-300';
    valueLabel = falseLabel;
  }
  return (
    <div className={`flex items-center justify-between gap-2 rounded-2xl border px-3 py-2 text-sm ${toneClass}`}>
      <span className="font-medium">{label}</span>
      <span className="text-xs uppercase tracking-wide">{valueLabel}</span>
    </div>
  );
}

function connectionKey(connection: ProbeConnection): string {
  return `${connection.id}@${connection.policyRevision}.${connection.credentialRevision}`;
}

export function GithubTokenProbePanel({
  connection,
  canRunProbe,
  onNotice,
  initialRepo,
}: GithubTokenProbePanelProps) {
  const [repo, setRepo] = useState(initialRepo ?? '');
  const [mode, setMode] = useState<ProbeMode>('publish');
  const [baseBranch, setBaseBranch] = useState('');
  const [isRunning, setIsRunning] = useState(false);
  const [result, setResult] = useState<ProbeResponse | null>(null);
  const [resultTarget, setResultTarget] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  // Only the latest request for the current connection/revision may apply.
  const requestSeq = useRef(0);
  const currentConnectionKey = connectionKey(connection);
  const currentTarget = `${currentConnectionKey}|${repo.trim()}|${mode}|${baseBranch.trim()}`;

  useEffect(() => {
    requestSeq.current += 1;
    setResult(null);
    setResultTarget(null);
    setErrorMessage(null);
    setIsRunning(false);
    // The panel remounts per selected connection; a late response from an
    // unmounted or superseded panel must not reach the page notice.
    return () => {
      requestSeq.current += 1;
    };
  }, [currentConnectionKey]);

  async function handleRunProbe(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    if (!canRunProbe) return;
    requestSeq.current += 1;
    const seq = requestSeq.current;
    const target = currentTarget;
    const isCurrent = () => requestSeq.current === seq;
    setIsRunning(true);
    setErrorMessage(null);
    setResult(null);
    setResultTarget(null);
    try {
      const payload: Record<string, unknown> = {
        repo: repo.trim(),
        mode,
        connectionId: connection.id,
      };
      if (baseBranch.trim()) payload.baseBranch = baseBranch.trim();
      const response = await fetch('/api/v1/settings/github/token-probe', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify(payload),
      });
      const body = (await response.json().catch(() => ({}))) as ProbeResponse & { detail?: string };
      if (!isCurrent()) return;
      if (!response.ok) {
        const detail =
          (typeof body.detail === 'string' && body.detail) ||
          `Connection test failed with HTTP ${response.status}`;
        setErrorMessage(detail);
        onNotice?.({ level: 'error', text: detail });
        return;
      }
      setResult(body);
      setResultTarget(target);
      onNotice?.({ level: 'ok', text: `Tested ${connection.displayName}.` });
    } catch (err) {
      if (!isCurrent()) return;
      const message = err instanceof Error ? err.message : 'Connection test failed.';
      setErrorMessage(message);
      onNotice?.({ level: 'error', text: message });
    } finally {
      if (isCurrent()) setIsRunning(false);
    }
  }

  const resultIsCurrent = result !== null && resultTarget === currentTarget;
  const readObservation: ReadObservation = result?.observations?.read ?? 'not_checked';
  const readSummary = READ_SUMMARY[readObservation] ?? READ_SUMMARY.not_checked;
  const branchObservation = result?.observations?.branch;
  const branchFalseLabel =
    branchObservation === 'missing' || branchObservation === 'not_found'
      ? 'not found'
      : branchObservation === 'empty_repository'
        ? 'repository empty'
        : 'not readable';
  const reportsPush = result?.reportedPermissions?.push === true;

  return (
    <section
      aria-label="Test connection"
      className="min-w-0 rounded-2xl border border-mm-border/80 bg-transparent p-4"
    >
      <header className="space-y-1">
        <h4 className="text-base font-semibold text-slate-900 dark:text-white">Test connection</h4>
        <p className="text-sm text-slate-600 dark:text-slate-400">
          Reads one repository assigned to <strong>{connection.displayName}</strong>, with that
          connection only. The test never writes, so it cannot prove publishing works, and it never
          uses another credential.
        </p>
      </header>

      <form
        className="mt-3 grid gap-3 md:grid-cols-[minmax(0,1.5fr)_minmax(0,1.5fr)_minmax(0,1fr)_auto]"
        onSubmit={handleRunProbe}
      >
        <label className="flex min-w-0 flex-col gap-1 text-sm">
          <span className="font-medium text-slate-700 dark:text-slate-200">Repository (owner/repo)</span>
          <input
            type="text"
            value={repo}
            onChange={(event) => setRepo(event.target.value)}
            placeholder="owner/repo"
            className="rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 shadow-sm focus:outline-hidden focus:ring-2 focus:ring-mm-accent dark:border-slate-700 dark:bg-slate-900 dark:text-white"
            autoComplete="off"
            required
          />
        </label>
        <label className="flex min-w-0 flex-col gap-1 text-sm">
          <span className="font-medium text-slate-700 dark:text-slate-200">What to check</span>
          <select
            value={mode}
            onChange={(event) => setMode(event.target.value as ProbeMode)}
            className="rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 shadow-sm focus:outline-hidden focus:ring-2 focus:ring-mm-accent dark:border-slate-700 dark:bg-slate-900 dark:text-white"
          >
            {MODE_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex min-w-0 flex-col gap-1 text-sm">
          <span className="font-medium text-slate-700 dark:text-slate-200">
            Branch (optional, defaults to the repository default)
          </span>
          <input
            type="text"
            value={baseBranch}
            onChange={(event) => setBaseBranch(event.target.value)}
            className="rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 shadow-sm focus:outline-hidden focus:ring-2 focus:ring-mm-accent dark:border-slate-700 dark:bg-slate-900 dark:text-white"
            autoComplete="off"
          />
        </label>
        <div className="flex items-end">
          <button
            type="submit"
            disabled={
              !canRunProbe || isRunning || repo.trim().length === 0 || connection.lifecycle !== 'active'
            }
            className="inline-flex w-full items-center justify-center rounded-xl bg-mm-accent px-4 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-mm-accent/90 disabled:cursor-not-allowed disabled:opacity-50 md:w-auto"
          >
            {isRunning ? 'Testing…' : 'Test connection'}
          </button>
        </div>
      </form>

      {!canRunProbe ? (
        <p className="mt-3 text-sm text-slate-500 dark:text-slate-400">
          Testing a connection requires the settings.effective.read permission.
        </p>
      ) : null}
      {connection.lifecycle !== 'active' ? (
        <p className="mt-3 text-sm text-slate-500 dark:text-slate-400">
          Disabled connections cannot be tested.
        </p>
      ) : null}

      {errorMessage ? (
        <div
          role="alert"
          className="mt-3 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 dark:border-rose-900/50 dark:bg-rose-900/20 dark:text-rose-300"
        >
          {errorMessage}
        </div>
      ) : null}

      {result ? (
        <div className="mt-4 space-y-3" aria-label="Connection test result">
          {!resultIsCurrent ? (
            <p className="rounded-2xl border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-900/50 dark:bg-amber-900/20 dark:text-amber-200">
              The inputs changed after this test. Test again to see current results.
            </p>
          ) : null}
          <p className={`rounded-2xl border px-3 py-2 text-sm font-medium ${readSummary.tone}`}>
            {readSummary.text}
          </p>
          {typeof result.retryAfterSeconds === 'number' ? (
            <p className="rounded-2xl border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-900/50 dark:bg-amber-900/20 dark:text-amber-200">
              GitHub is limiting requests for this credential, so the test stopped early. GitHub asked
              MoonMind to wait about {result.retryAfterSeconds} seconds before testing again.
            </p>
          ) : null}
          {branchObservation === 'empty_repository' ? (
            <p className="text-sm text-slate-600 dark:text-slate-400">
              The repository is empty: it has no branches yet, so branch checks were skipped.
            </p>
          ) : null}
          {(branchObservation === 'missing' || branchObservation === 'not_found') && result.testedBranch ? (
            <p className="text-sm text-slate-600 dark:text-slate-400">
              Branch <code>{result.testedBranch}</code> was not found in this repository.
            </p>
          ) : null}
          <p className="text-sm text-slate-600 dark:text-slate-400">
            {reportsPush ? 'GitHub reports push permission for this connection, but write' : 'Write'}{' '}
            access not tested: this test only reads. Publishing is confirmed when a workflow pushes.
          </p>
          {result.remoteDefaultBranch ? (
            <p className="text-sm text-slate-600 dark:text-slate-400">
              Remote default branch: <code>{result.remoteDefaultBranch}</code>
            </p>
          ) : null}
          {connection.assignmentCount === 0 ? (
            <p className="text-sm text-slate-600 dark:text-slate-400">
              This connection has no assigned repositories, so workflows cannot use it yet. Tests
              read only assigned repositories; assign one below first.
            </p>
          ) : null}
          <div className="grid gap-2 sm:grid-cols-3">
            <AccessibilityPill label="Repository" value={result.repositoryAccessible} />
            <AccessibilityPill
              label="Branch"
              value={result.defaultBranchAccessible}
              falseLabel={branchFalseLabel}
            />
            <AccessibilityPill label="Pull requests" value={result.pullRequestAccessible} />
          </div>

          {(result.diagnostics ?? []).length > 0 ? (
            <section className="rounded-2xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-900/50 dark:bg-amber-900/20 dark:text-amber-200">
              <h5 className="text-sm font-semibold">Details</h5>
              <ul className="mt-2 space-y-2">
                {(result.diagnostics ?? []).map((entry, index) => (
                  <li
                    key={`${entry.operation}-${index}`}
                    className="break-words rounded-xl border border-amber-200/50 bg-amber-100/40 p-2 text-xs dark:border-amber-900/40 dark:bg-amber-900/30"
                  >
                    <div className="font-medium">
                      {entry.operation}
                      {typeof entry.httpStatus === 'number' ? ` — HTTP ${entry.httpStatus}` : ''}
                    </div>
                    {entry.message ? <div className="mt-1">{entry.message}</div> : null}
                    {entry.retryable ? (
                      <div className="mt-1 text-[10px] uppercase tracking-wide">retryable</div>
                    ) : null}
                  </li>
                ))}
              </ul>
            </section>
          ) : null}

          {(result.limitations ?? []).length > 0 ? (
            <ul className="list-disc space-y-1 pl-5 text-xs text-slate-600 dark:text-slate-400">
              {(result.limitations ?? []).map((item, index) => (
                <li key={index}>{item}</li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

export default GithubTokenProbePanel;
