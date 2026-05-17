import { FormEvent, useState } from 'react';

type ProbeMode = 'indexing' | 'publish' | 'readiness' | 'full_pr_automation';

interface ChecklistItem {
  permission: string;
  level: string;
  required: boolean;
  status: string;
}

interface DiagnosticEntry {
  operation: string;
  httpStatus?: number | null;
  message?: string | null;
  retryable?: boolean;
}

interface CredentialSource {
  sourceKind: string;
  sourceName: string | null;
  resolved: boolean;
}

interface ProbeResponse {
  repo?: string;
  mode?: string;
  credentialSource?: CredentialSource;
  repositoryAccessible?: boolean | null;
  defaultBranchAccessible?: boolean | null;
  pullRequestAccessible?: boolean | null;
  permissionChecklist?: ChecklistItem[];
  diagnostics?: DiagnosticEntry[];
  limitations?: string[];
}

interface Notice {
  level: 'ok' | 'error';
  text: string;
}

export interface GithubTokenProbePanelProps {
  canRunProbe: boolean;
  onNotice?: (notice: Notice | null) => void;
  initialRepo?: string;
}

const MODE_OPTIONS: ReadonlyArray<{ value: ProbeMode; label: string; description: string }> = [
  {
    value: 'indexing',
    label: 'Indexing (read-only contents)',
    description: 'Validates read access used for repository indexing and retrieval.',
  },
  {
    value: 'publish',
    label: 'Publish (PRs + contents write)',
    description: 'Validates write access to push commits and open pull requests.',
  },
  {
    value: 'readiness',
    label: 'PR readiness (status + checks)',
    description: 'Validates read access to commit statuses, checks, issues, and pull requests.',
  },
  {
    value: 'full_pr_automation',
    label: 'Full PR automation (publish + workflow edits)',
    description: 'Validates the publish profile plus workflow edits and readiness reads.',
  },
];

function StatusBadge({ status }: { status: string }) {
  const palette: Record<string, string> = {
    passed:
      'border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900/50 dark:bg-emerald-900/20 dark:text-emerald-300',
    failed:
      'border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-900/50 dark:bg-rose-900/20 dark:text-rose-300',
    not_checked:
      'border-slate-200 bg-slate-50 text-slate-600 dark:border-slate-700 dark:bg-slate-800/50 dark:text-slate-300',
  };
  const verifiedRead = status.startsWith('verified_');
  const className =
    palette[status] ??
    (verifiedRead
      ? 'border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-900/50 dark:bg-amber-900/20 dark:text-amber-300'
      : 'border-slate-200 bg-slate-50 text-slate-600 dark:border-slate-700 dark:bg-slate-800/50 dark:text-slate-300');
  const label = verifiedRead
    ? status.replace(/_/g, ' ')
    : status.replace(/_/g, ' ');
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${className}`}
    >
      {label}
    </span>
  );
}

function AccessibilityPill({
  label,
  value,
}: {
  label: string;
  value: boolean | null | undefined;
}) {
  let toneClass = 'border-slate-200 bg-slate-50 text-slate-600 dark:border-slate-700 dark:bg-slate-800/50 dark:text-slate-300';
  let valueLabel = 'not checked';
  if (value === true) {
    toneClass =
      'border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900/50 dark:bg-emerald-900/20 dark:text-emerald-300';
    valueLabel = 'accessible';
  } else if (value === false) {
    toneClass =
      'border-rose-200 bg-rose-50 text-rose-700 dark:border-rose-900/50 dark:bg-rose-900/20 dark:text-rose-300';
    valueLabel = 'not accessible';
  }
  return (
    <div
      className={`flex items-center justify-between rounded-2xl border px-3 py-2 text-sm ${toneClass}`}
    >
      <span className="font-medium">{label}</span>
      <span className="text-xs uppercase tracking-wide">{valueLabel}</span>
    </div>
  );
}

export function GithubTokenProbePanel({
  canRunProbe,
  onNotice,
  initialRepo,
}: GithubTokenProbePanelProps) {
  const [repo, setRepo] = useState(initialRepo ?? '');
  const [mode, setMode] = useState<ProbeMode>('publish');
  const [baseBranch, setBaseBranch] = useState('');
  const [isRunning, setIsRunning] = useState(false);
  const [result, setResult] = useState<ProbeResponse | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  async function handleRunProbe(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    if (!canRunProbe) {
      return;
    }
    setIsRunning(true);
    setErrorMessage(null);
    setResult(null);
    try {
      const payload: Record<string, unknown> = {
        repo: repo.trim(),
        mode,
      };
      const trimmedBaseBranch = baseBranch.trim();
      if (trimmedBaseBranch.length > 0) {
        payload.baseBranch = trimmedBaseBranch;
      }
      const response = await fetch('/api/v1/settings/github/token-probe', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Accept: 'application/json',
        },
        body: JSON.stringify(payload),
      });
      const body = (await response.json().catch(() => ({}))) as
        | (ProbeResponse & { detail?: string })
        | { detail?: string };
      if (!response.ok) {
        const detail =
          (typeof (body as { detail?: unknown }).detail === 'string' &&
            ((body as { detail?: string }).detail as string)) ||
          `Probe failed with HTTP ${response.status}`;
        setErrorMessage(detail);
        onNotice?.({ level: 'error', text: detail });
        return;
      }
      setResult(body as ProbeResponse);
      onNotice?.({ level: 'ok', text: 'GitHub token probe completed.' });
    } catch (err) {
      const message = err instanceof Error ? err.message : 'GitHub token probe failed.';
      setErrorMessage(message);
      onNotice?.({ level: 'error', text: message });
    } finally {
      setIsRunning(false);
    }
  }

  return (
    <section className="rounded-3xl border border-mm-border/80 bg-transparent p-6 shadow-sm">
      <header className="space-y-2">
        <h3 className="text-lg font-semibold text-slate-900 dark:text-white">
          GitHub token probe
        </h3>
        <p className="text-sm text-slate-600 dark:text-slate-400">
          Validate the configured GitHub fine-grained PAT against a specific repository and MoonMind mode.
          The probe never exposes the token; it returns the resolved credential source, repository / branch /
          pull-request access, and a per-mode permission checklist.
        </p>
      </header>

      <form
        className="mt-4 grid gap-4 md:grid-cols-[minmax(0,1.5fr)_minmax(0,1.5fr)_minmax(0,1fr)_auto]"
        onSubmit={handleRunProbe}
      >
        <label className="flex flex-col gap-1 text-sm">
          <span className="font-medium text-slate-700 dark:text-slate-200">Repository (owner/repo)</span>
          <input
            type="text"
            value={repo}
            onChange={(event) => setRepo(event.target.value)}
            placeholder="owner/repo"
            className="rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 shadow-sm focus:outline-none focus:ring-2 focus:ring-mm-accent dark:border-slate-700 dark:bg-slate-900 dark:text-white"
            autoComplete="off"
            required
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="font-medium text-slate-700 dark:text-slate-200">MoonMind mode</span>
          <select
            value={mode}
            onChange={(event) => setMode(event.target.value as ProbeMode)}
            className="rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 shadow-sm focus:outline-none focus:ring-2 focus:ring-mm-accent dark:border-slate-700 dark:bg-slate-900 dark:text-white"
          >
            {MODE_OPTIONS.map((option) => (
              <option key={option.value} value={option.value} title={option.description}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="font-medium text-slate-700 dark:text-slate-200">
            Branch (optional, defaults to main)
          </span>
          <input
            type="text"
            value={baseBranch}
            onChange={(event) => setBaseBranch(event.target.value)}
            placeholder="main"
            className="rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 shadow-sm focus:outline-none focus:ring-2 focus:ring-mm-accent dark:border-slate-700 dark:bg-slate-900 dark:text-white"
            autoComplete="off"
          />
        </label>
        <div className="flex items-end">
          <button
            type="submit"
            disabled={!canRunProbe || isRunning || repo.trim().length === 0}
            className="inline-flex items-center justify-center rounded-xl bg-mm-accent px-4 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-mm-accent/90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {isRunning ? 'Running…' : 'Run probe'}
          </button>
        </div>
      </form>

      {!canRunProbe ? (
        <p className="mt-3 text-sm text-slate-500 dark:text-slate-400">
          Workspace admin permission required to run the GitHub token probe (settings.effective.read).
        </p>
      ) : null}

      <section
        aria-label="SecretRef alias precedence"
        className="mt-4 rounded-2xl border border-slate-200 bg-slate-50 p-4 text-sm text-slate-700 dark:border-slate-800 dark:bg-slate-800/50 dark:text-slate-300"
      >
        <h4 className="text-sm font-semibold text-slate-800 dark:text-slate-100">
          SecretRef alias precedence
        </h4>
        <p className="mt-1 text-xs text-slate-600 dark:text-slate-400">
          MoonMind resolves the GitHub token from the following sources, in order. The first non-empty
          value wins:
        </p>
        <ol className="mt-2 list-decimal space-y-1 pl-5 text-xs text-slate-600 dark:text-slate-400">
          <li>Explicit token passed at the call site (workflows only; not exposed in the UI).</li>
          <li>
            Direct token env vars: <code>GITHUB_TOKEN</code>, <code>GH_TOKEN</code>,{' '}
            <code>WORKFLOW_GITHUB_TOKEN</code>.
          </li>
          <li>
            Secret-ref env vars: <code>GITHUB_TOKEN_SECRET_REF</code>,{' '}
            <code>WORKFLOW_GITHUB_TOKEN_SECRET_REF</code>.
          </li>
          <li>
            Settings catalog entry <code>settings.github.github_token_secret_ref</code>.
          </li>
          <li>
            Settings token-ref env var: <code>MOONMIND_GITHUB_TOKEN_REF</code>.
          </li>
        </ol>
        <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">
          The probe response&apos;s <code>credentialSource</code> reports which source actually resolved the
          token for this call without exposing the token value itself.
        </p>
      </section>

      {errorMessage ? (
        <div
          role="alert"
          className="mt-4 rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 dark:border-rose-900/50 dark:bg-rose-900/20 dark:text-rose-300"
        >
          {errorMessage}
        </div>
      ) : null}

      {result ? (
        <div className="mt-6 space-y-4">
          <section className="rounded-2xl border border-slate-200 bg-white p-4 text-sm shadow-sm dark:border-slate-800 dark:bg-slate-900">
            <h4 className="text-sm font-semibold text-slate-800 dark:text-slate-100">
              Resolved credential source
            </h4>
            <dl className="mt-2 grid gap-2 text-xs text-slate-600 dark:text-slate-300 sm:grid-cols-3">
              <div>
                <dt className="font-medium text-slate-500 dark:text-slate-400">Source kind</dt>
                <dd>{result.credentialSource?.sourceKind ?? 'unknown'}</dd>
              </div>
              <div>
                <dt className="font-medium text-slate-500 dark:text-slate-400">Source name</dt>
                <dd>{result.credentialSource?.sourceName ?? '—'}</dd>
              </div>
              <div>
                <dt className="font-medium text-slate-500 dark:text-slate-400">Resolved?</dt>
                <dd>{result.credentialSource?.resolved ? 'yes' : 'no'}</dd>
              </div>
            </dl>
          </section>

          <section className="grid gap-2 sm:grid-cols-3">
            <AccessibilityPill label="Repository accessible" value={result.repositoryAccessible} />
            <AccessibilityPill
              label="Default branch accessible"
              value={result.defaultBranchAccessible}
            />
            <AccessibilityPill
              label="Pull request endpoint accessible"
              value={result.pullRequestAccessible}
            />
          </section>

          <section className="rounded-2xl border border-slate-200 bg-white p-4 text-sm shadow-sm dark:border-slate-800 dark:bg-slate-900">
            <h4 className="text-sm font-semibold text-slate-800 dark:text-slate-100">
              Permission checklist
            </h4>
            <table className="mt-2 w-full text-xs">
              <thead>
                <tr className="text-left text-slate-500 dark:text-slate-400">
                  <th scope="col" className="py-1 pr-3 font-medium">Permission</th>
                  <th scope="col" className="py-1 pr-3 font-medium">Level</th>
                  <th scope="col" className="py-1 pr-3 font-medium">Requirement</th>
                  <th scope="col" className="py-1 font-medium">Status</th>
                </tr>
              </thead>
              <tbody>
                {(result.permissionChecklist ?? []).map((item) => (
                  <tr
                    key={`${item.permission}-${item.level}`}
                    className="border-t border-slate-100 dark:border-slate-800"
                  >
                    <td className="py-1 pr-3 text-slate-700 dark:text-slate-200">{item.permission}</td>
                    <td className="py-1 pr-3 text-slate-600 dark:text-slate-300">{item.level}</td>
                    <td className="py-1 pr-3 text-slate-600 dark:text-slate-300">
                      {item.required ? 'required' : 'optional'}
                    </td>
                    <td className="py-1">
                      <StatusBadge status={item.status} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>

          {(result.diagnostics ?? []).length > 0 ? (
            <section className="rounded-2xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900 dark:border-amber-900/50 dark:bg-amber-900/20 dark:text-amber-200">
              <h4 className="text-sm font-semibold">Diagnostics</h4>
              <ul className="mt-2 space-y-2">
                {(result.diagnostics ?? []).map((entry, index) => (
                  <li
                    key={`${entry.operation}-${index}`}
                    className="rounded-xl border border-amber-200/50 bg-amber-100/40 p-2 text-xs dark:border-amber-900/40 dark:bg-amber-900/30"
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
            <section className="rounded-2xl border border-slate-200 bg-slate-50 p-4 text-xs text-slate-600 dark:border-slate-800 dark:bg-slate-800/50 dark:text-slate-300">
              <h4 className="text-sm font-semibold text-slate-800 dark:text-slate-100">Known limitations</h4>
              <ul className="mt-2 list-disc space-y-1 pl-5">
                {(result.limitations ?? []).map((item, index) => (
                  <li key={index}>{item}</li>
                ))}
              </ul>
            </section>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

export default GithubTokenProbePanel;
