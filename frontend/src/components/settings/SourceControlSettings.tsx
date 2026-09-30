import { useCallback, useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';

import type { components } from '../../generated/openapi';
import { useSettingsDraftRegistration } from './SettingsDraftGuard';

type ConnectionView = components['schemas']['RepositoryConnectionView'];
type ConnectionList = components['schemas']['RepositoryConnectionListResponse'];
type ProbeMode = components['schemas']['ProbeModeView'];
type ProbeResult = components['schemas']['ConnectionProbeResponse'];
type ProbeDiagnostic = components['schemas']['ProbeDiagnostic'];
type Discovery = components['schemas']['RepositoryDiscoveryResponse'];
type Removal = components['schemas']['ConnectionRemovalResponse'];

export const REPOSITORY_CONNECTIONS_QUERY_KEY = ['repository-connections'] as const;
const API = '/api/v1/repository-connections';

interface Notice {
  level: 'ok' | 'error';
  text: string;
}

/**
 * One request outcome. `status` is null when MoonMind never answered, and
 * `structured` is true only when the server reported a known outcome.
 */
interface ApiFailure {
  status: number | null;
  kind: string;
  message: string;
  structured: boolean;
}

class ApiError extends Error {
  constructor(readonly failure: ApiFailure) {
    super(failure.message);
  }
}

async function requestJson<T>(url: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url, {
      ...init,
      headers: {
        Accept: 'application/json',
        ...(init.body ? { 'Content-Type': 'application/json' } : {}),
      },
    });
  } catch (error) {
    if (init.signal?.aborted) throw error;
    throw new ApiError({
      status: null,
      kind: 'no_response',
      message: 'MoonMind did not respond.',
      structured: false,
    });
  }
  const body = (await response.json().catch(() => null)) as { detail?: unknown } | null;
  if (!response.ok) {
    const detail = body?.detail;
    if (detail && typeof detail === 'object' && !Array.isArray(detail) && 'kind' in detail) {
      const { kind, message } = detail as { kind?: unknown; message?: unknown };
      throw new ApiError({
        status: response.status,
        kind: String(kind),
        message: typeof message === 'string' ? message : `Request failed with HTTP ${response.status}.`,
        structured: true,
      });
    }
    throw new ApiError({
      status: response.status,
      kind: response.status === 401 ? 'admission' : response.status >= 500 ? 'no_response' : 'validation',
      message:
        response.status === 401
          ? 'Your session ended. Sign in again to continue.'
          : typeof detail === 'string'
            ? detail
            : `Request failed with HTTP ${response.status}.`,
      structured: response.status === 401 || response.status < 500,
    });
  }
  return body as T;
}

function asFailure(error: unknown): ApiFailure {
  if (error instanceof ApiError) return error.failure;
  return {
    status: null,
    kind: 'no_response',
    message: error instanceof Error ? error.message : 'Request failed.',
    structured: false,
  };
}

/** The save may or may not have committed: reconcile before offering it again. */
function isUncertain(failure: ApiFailure): boolean {
  return !failure.structured;
}

function newRequestId(): string {
  return typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : `req-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function slugify(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 64)
    .replace(/-+$/g, '');
}

const FAILURE_LABELS: Record<string, string> = {
  authentication: 'Authentication failed',
  permission: 'Permission denied',
  not_found: 'Not found or not visible',
  rate_limited: 'Rate limited',
  unavailable: 'GitHub unavailable',
  credential_unavailable: 'Credential unavailable',
  conflict: 'Conflict',
  account_mismatch: 'Different account',
  validation: 'Check the form',
  disabled: 'Connection disabled',
  admission: 'Signed out',
  no_response: 'Not confirmed',
  not_checked: 'Not checked',
  truncated: 'Partial list',
  rejected: 'Rejected',
};

function failureLabel(kind: string): string {
  return FAILURE_LABELS[kind] ?? 'Failed';
}

const CHECK_STATUS_LABELS: Record<string, string> = {
  passed: 'Read verified',
  verified_read_access: 'Read verified; write not tested',
  failed: 'Failed',
  not_checked: 'Not checked',
  unavailable: 'Unavailable',
};

const STATE_LABELS: Record<ConnectionView['state'], string> = {
  ready: 'Ready',
  no_repositories: 'No repositories',
  disabled: 'Disabled',
  credential_unavailable: 'Credential unavailable',
};

const inputClass =
  'rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 shadow-sm focus:outline-none focus:ring-2 focus:ring-mm-accent dark:border-slate-700 dark:bg-slate-900 dark:text-white';
const primaryButtonClass =
  'inline-flex items-center justify-center rounded-xl bg-mm-accent px-4 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-mm-accent/90 disabled:cursor-not-allowed disabled:opacity-50';
const secondaryButtonClass =
  'inline-flex items-center justify-center rounded-xl border border-slate-300 px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800';

function FailureMessage({ failure, children }: { failure: ApiFailure; children?: ReactNode }) {
  return (
    <div
      role="alert"
      className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700 dark:border-rose-900/50 dark:bg-rose-900/20 dark:text-rose-300"
    >
      <span className="font-semibold">{failureLabel(failure.kind)}:</span> {failure.message}
      {children}
    </div>
  );
}

function replaceConnection(queryClient: QueryClient, view: ConnectionView) {
  queryClient.setQueryData<ConnectionList>(REPOSITORY_CONNECTIONS_QUERY_KEY, (current) => {
    if (!current) return current;
    const exists = current.items.some((item) => item.id === view.id);
    return {
      ...current,
      items: exists
        ? current.items.map((item) => (item.id === view.id ? view : item))
        : [...current.items, view],
    };
  });
}

function fetchConnections(signal?: AbortSignal): Promise<ConnectionList> {
  return requestJson<ConnectionList>(API, signal ? { signal } : {});
}

async function reconcileConnection(
  queryClient: QueryClient,
  connectionId: string,
): Promise<ConnectionView | null> {
  const list = await queryClient.fetchQuery({
    queryKey: REPOSITORY_CONNECTIONS_QUERY_KEY,
    queryFn: ({ signal }) => fetchConnections(signal),
    staleTime: 0,
  });
  return list.items.find((item) => item.id === connectionId) ?? null;
}

export interface SourceControlSettingsProps {
  onNotice?: ((notice: Notice | null) => void) | undefined;
}

export function SourceControlSettings({ onNotice }: SourceControlSettingsProps) {
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const connectionsQuery = useQuery<ConnectionList>({
    queryKey: REPOSITORY_CONNECTIONS_QUERY_KEY,
    queryFn: ({ signal }) => fetchConnections(signal),
  });
  const items = connectionsQuery.data?.items ?? [];
  const probeModes = connectionsQuery.data?.probeModes ?? [];
  const selected = items.find((item) => item.id === selectedId) ?? null;
  const refreshFailure = connectionsQuery.error ? asFailure(connectionsQuery.error) : null;
  const admissionLost = refreshFailure?.kind === 'admission';

  const handleSaved = useCallback(
    (view: ConnectionView, reconciled: boolean) => {
      replaceConnection(queryClient, view);
      void queryClient.invalidateQueries({ queryKey: REPOSITORY_CONNECTIONS_QUERY_KEY });
      setSelectedId(view.id);
      onNotice?.({
        level: 'ok',
        text: reconciled
          ? `Connection ${view.displayName} was saved; MoonMind confirmed it after the response was lost.`
          : `Connection ${view.displayName} saved.`,
      });
    },
    [onNotice, queryClient],
  );

  return (
    <section
      aria-labelledby="source-control-heading"
      className="rounded-3xl border border-mm-border/80 bg-transparent p-6 shadow-sm"
    >
      <header className="space-y-2">
        <h3 id="source-control-heading" className="text-lg font-semibold text-slate-900 dark:text-white">
          Source Control
        </h3>
        <p className="text-sm text-slate-600 dark:text-slate-400">
          Named GitHub connections for repository access. Each connection keeps its own token, is
          tested with only that token, and reaches only the repositories assigned to it. Repository
          connections are separate from Provider Profiles.
        </p>
      </header>

      {refreshFailure && connectionsQuery.data ? (
        <p
          role="status"
          className="mt-4 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:border-amber-900/50 dark:bg-amber-900/20 dark:text-amber-300"
        >
          Connections could not be refreshed ({refreshFailure.message}). Showing the last loaded
          list; saved connections are unchanged.
        </p>
      ) : null}

      {connectionsQuery.isLoading ? (
        <p className="mt-4 text-sm text-slate-500 dark:text-slate-400">Loading connections…</p>
      ) : refreshFailure && !connectionsQuery.data ? (
        <div className="mt-4">
          <FailureMessage failure={refreshFailure}>
            {' '}
            <button
              type="button"
              className="ml-2 underline"
              onClick={() => void connectionsQuery.refetch()}
            >
              Retry
            </button>
          </FailureMessage>
        </div>
      ) : items.length === 0 ? (
        <p className="mt-4 text-sm text-slate-600 dark:text-slate-400">
          No connections yet. Public repositories can still be read without one.
        </p>
      ) : (
        <ul aria-label="Repository connections" className="mt-4 grid gap-2">
          {items.map((item) => (
            <li key={item.id}>
              <button
                type="button"
                aria-pressed={item.id === selectedId}
                onClick={() => setSelectedId(item.id === selectedId ? null : item.id)}
                className={`flex w-full flex-col gap-1 rounded-2xl border px-4 py-3 text-left text-sm transition sm:flex-row sm:items-center sm:justify-between ${
                  item.id === selectedId
                    ? 'border-mm-accent bg-mm-accent/5'
                    : 'border-slate-200 hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-900'
                }`}
              >
                <span className="min-w-0">
                  <span className="block font-semibold text-slate-900 dark:text-white">{item.displayName}</span>
                  <span className="block text-xs text-slate-600 dark:text-slate-400">
                    {accountLabel(item)} · {item.assignments.length}{' '}
                    {item.assignments.length === 1 ? 'repository' : 'repositories'}
                  </span>
                </span>
                <StateBadge state={item.state} />
              </button>
            </li>
          ))}
        </ul>
      )}

      {selected ? (
        <ConnectionDetail
          key={selected.id}
          connection={selected}
          probeModes={probeModes}
          admissionLost={admissionLost}
          onNotice={onNotice}
          onRemoved={() => setSelectedId(null)}
        />
      ) : null}

      <ConnectionSetupForm admissionLost={admissionLost} onSaved={handleSaved} />
    </section>
  );
}

function accountLabel(connection: ConnectionView): string {
  if (connection.credentialKind === 'github_app') {
    return connection.account
      ? `GitHub App for ${connection.account}`
      : `GitHub App installation ${connection.installation ?? 'unknown'}`;
  }
  return connection.account ? `Token for ${connection.account}` : 'Token account unknown';
}

function StateBadge({ state }: { state: ConnectionView['state'] }) {
  const tone =
    state === 'ready'
      ? 'border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900/50 dark:bg-emerald-900/20 dark:text-emerald-300'
      : 'border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-900/50 dark:bg-amber-900/20 dark:text-amber-300';
  return (
    <span className={`inline-flex shrink-0 items-center rounded-full border px-2 py-0.5 text-xs font-medium ${tone}`}>
      {STATE_LABELS[state]}
    </span>
  );
}

function ConnectionSetupForm({
  admissionLost,
  onSaved,
}: {
  admissionLost: boolean;
  onSaved: (view: ConnectionView, reconciled: boolean) => void;
}) {
  const queryClient = useQueryClient();
  const formId = useId();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState('');
  const [idOverride, setIdOverride] = useState<string | null>(null);
  const [token, setToken] = useState('');
  const [allowWrite, setAllowWrite] = useState(false);
  const [requestId, setRequestId] = useState(newRequestId);
  const [submitting, setSubmitting] = useState(false);
  const [failure, setFailure] = useState<ApiFailure | null>(null);
  const connectionId = idOverride ?? slugify(name);

  const reset = useCallback(() => {
    setOpen(false);
    setName('');
    setIdOverride(null);
    setToken('');
    setAllowWrite(false);
    setFailure(null);
    setRequestId(newRequestId());
  }, []);

  useEffect(() => {
    // Losing admission ends the protected interaction: drop the token.
    if (admissionLost) setToken('');
  }, [admissionLost]);

  useSettingsDraftRegistration(
    'source-control-connection',
    open && (name.trim().length > 0 || token.length > 0),
    reset,
  );

  const finish = (view: ConnectionView, reconciled: boolean) => {
    reset();
    onSaved(view, reconciled);
  };

  const reconcile = async (): Promise<boolean> => {
    try {
      const committed = await reconcileConnection(queryClient, connectionId);
      if (committed) {
        finish(committed, true);
        return true;
      }
      setFailure({
        status: null,
        kind: 'no_response',
        message:
          'MoonMind did not confirm the save and the connection is not in the saved list. Re-enter the token and submit again; resubmitting this form cannot create a duplicate.',
        structured: false,
      });
    } catch (error) {
      setFailure({
        ...asFailure(error),
        kind: 'no_response',
        structured: false,
        message:
          'MoonMind could not confirm whether the connection was saved. Check saved connections before submitting again.',
      });
    }
    return false;
  };

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting) return;
    // The token leaves component state as soon as it is handed to the request.
    const submittedToken = token;
    setToken('');
    setSubmitting(true);
    setFailure(null);
    try {
      const view = await requestJson<ConnectionView>(`${API}/pat`, {
        method: 'POST',
        body: JSON.stringify({
          requestId,
          connectionId,
          displayName: name.trim(),
          token: submittedToken,
          allowedOperations: allowWrite ? ['read', 'write'] : ['read'],
        }),
      });
      finish(view, false);
    } catch (error) {
      const outcome = asFailure(error);
      if (isUncertain(outcome)) {
        await reconcile();
      } else {
        setFailure(outcome);
      }
    } finally {
      setSubmitting(false);
    }
  }

  if (!open) {
    return (
      <div className="mt-6">
        <button type="button" className={secondaryButtonClass} onClick={() => setOpen(true)}>
          Add connection
        </button>
      </div>
    );
  }

  return (
    <form
      aria-labelledby={`${formId}-title`}
      className="mt-6 space-y-4 rounded-2xl border border-slate-200 p-4 dark:border-slate-800"
      onSubmit={handleSubmit}
      autoComplete="off"
    >
      <h4 id={`${formId}-title`} className="text-sm font-semibold text-slate-800 dark:text-slate-100">
        Add a token connection
      </h4>
      <p className="text-xs text-slate-600 dark:text-slate-400">
        MoonMind validates only this token, stores it as a Managed Secret, and never shows it again.
      </p>
      <div className="grid gap-4 md:grid-cols-2">
        <label className="flex flex-col gap-1 text-sm">
          <span className="font-medium text-slate-700 dark:text-slate-200">Connection name</span>
          <input
            className={inputClass}
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Work GitHub"
            required
          />
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="font-medium text-slate-700 dark:text-slate-200">Personal access token</span>
          <input
            className={inputClass}
            type="password"
            value={token}
            onChange={(event) => setToken(event.target.value)}
            autoComplete="new-password"
            spellCheck={false}
            required
          />
        </label>
      </div>
      <label className="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-200">
        <input type="checkbox" checked={allowWrite} onChange={(event) => setAllowWrite(event.target.checked)} />
        Allow publishing (write) through this connection
      </label>
      <details className="text-sm">
        <summary className="cursor-pointer text-slate-600 dark:text-slate-400">Advanced</summary>
        <label className="mt-2 flex flex-col gap-1">
          <span className="font-medium text-slate-700 dark:text-slate-200">Connection ID</span>
          <input
            className={inputClass}
            value={connectionId}
            onChange={(event) => setIdOverride(event.target.value)}
            aria-describedby={`${formId}-id-help`}
          />
        </label>
        <p id={`${formId}-id-help`} className="mt-1 text-xs text-slate-500 dark:text-slate-400">
          Lowercase letters, digits, and hyphens. IDs of removed connections cannot be reused.
        </p>
      </details>
      {failure ? (
        <FailureMessage failure={failure}>
          {isUncertain(failure) ? (
            <button type="button" className="ml-2 underline" onClick={() => void reconcile()}>
              Check saved connections
            </button>
          ) : null}
        </FailureMessage>
      ) : null}
      <div className="flex gap-2">
        <button
          type="submit"
          className={primaryButtonClass}
          disabled={submitting || !name.trim() || !token || !connectionId}
        >
          {submitting ? 'Saving…' : 'Save connection'}
        </button>
        <button type="button" className={secondaryButtonClass} onClick={reset} disabled={submitting}>
          Cancel
        </button>
      </div>
    </form>
  );
}

function ConnectionDetail({
  connection,
  probeModes,
  admissionLost,
  onNotice,
  onRemoved,
}: {
  connection: ConnectionView;
  probeModes: ProbeMode[];
  admissionLost: boolean;
  onNotice?: ((notice: Notice | null) => void) | undefined;
  onRemoved: () => void;
}) {
  const queryClient = useQueryClient();
  const [removeFailure, setRemoveFailure] = useState<ApiFailure | null>(null);
  const [removing, setRemoving] = useState(false);
  const active = connection.lifecycle === 'active';

  const apply = (view: ConnectionView) => {
    replaceConnection(queryClient, view);
    void queryClient.invalidateQueries({ queryKey: REPOSITORY_CONNECTIONS_QUERY_KEY });
  };

  async function handleRemove() {
    if (
      !window.confirm(
        `Remove ${connection.displayName}? Its stored token is deleted too, and this ID cannot be reused.`,
      )
    ) {
      return;
    }
    setRemoving(true);
    setRemoveFailure(null);
    try {
      await requestJson<Removal>(
        `${API}/${encodeURIComponent(connection.id)}?requestId=${encodeURIComponent(newRequestId())}`,
        { method: 'DELETE' },
      );
      queryClient.setQueryData<ConnectionList>(REPOSITORY_CONNECTIONS_QUERY_KEY, (current) =>
        current ? { ...current, items: current.items.filter((item) => item.id !== connection.id) } : current,
      );
      void queryClient.invalidateQueries({ queryKey: REPOSITORY_CONNECTIONS_QUERY_KEY });
      onNotice?.({ level: 'ok', text: `Connection ${connection.displayName} removed.` });
      onRemoved();
    } catch (error) {
      const failure = asFailure(error);
      if (isUncertain(failure)) {
        try {
          const still = await reconcileConnection(queryClient, connection.id);
          if (!still) {
            onNotice?.({ level: 'ok', text: `Connection ${connection.displayName} removed.` });
            onRemoved();
            return;
          }
        } catch {
          // Keep the original outcome below.
        }
      }
      setRemoveFailure(failure);
    } finally {
      setRemoving(false);
    }
  }

  return (
    <section
      aria-label={`Connection ${connection.displayName}`}
      className="mt-6 space-y-5 rounded-2xl border border-slate-200 p-4 dark:border-slate-800"
    >
      <header className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h4 className="text-base font-semibold text-slate-900 dark:text-white">{connection.displayName}</h4>
          <p className="text-sm text-slate-600 dark:text-slate-400">{accountLabel(connection)}</p>
          <p className="mt-1 text-sm text-slate-700 dark:text-slate-300">{connection.stateSummary}</p>
          <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
            Allows: {connection.allowedOperations.join(', ')}
          </p>
        </div>
        <StateBadge state={connection.state} />
      </header>

      <AssignmentsSection connection={connection} active={active} onChanged={apply} />

      {active ? <ProbePanel connection={connection} probeModes={probeModes} /> : null}

      {connection.credentialKind === 'pat' ? (
        <RotationForm connection={connection} admissionLost={admissionLost} onRotated={apply} onNotice={onNotice} />
      ) : null}

      <details className="text-sm">
        <summary className="cursor-pointer text-slate-600 dark:text-slate-400">Advanced details</summary>
        <dl className="mt-2 grid gap-2 text-xs text-slate-600 dark:text-slate-300 sm:grid-cols-2">
          <div>
            <dt className="font-medium">Connection ID</dt>
            <dd>{connection.id}</dd>
          </div>
          <div>
            <dt className="font-medium">Endpoint</dt>
            <dd>{connection.endpoint}</dd>
          </div>
          <div>
            <dt className="font-medium">Policy revision</dt>
            <dd>{connection.policyRevision}</dd>
          </div>
          <div>
            <dt className="font-medium">Token revision</dt>
            <dd>{connection.secretRevision ?? '—'}</dd>
          </div>
        </dl>
      </details>

      <div className="space-y-2 border-t border-slate-200 pt-4 dark:border-slate-800">
        {removeFailure ? <FailureMessage failure={removeFailure} /> : null}
        <button
          type="button"
          className={secondaryButtonClass}
          onClick={() => void handleRemove()}
          disabled={removing || connection.assignments.length > 0}
          aria-describedby={connection.assignments.length > 0 ? `remove-help-${connection.id}` : undefined}
        >
          {removing ? 'Removing…' : 'Remove connection'}
        </button>
        {connection.assignments.length > 0 ? (
          <p id={`remove-help-${connection.id}`} className="text-xs text-slate-500 dark:text-slate-400">
            Remove its repository assignments before removing the connection.
          </p>
        ) : null}
      </div>
    </section>
  );
}

function AssignmentsSection({
  connection,
  active,
  onChanged,
}: {
  connection: ConnectionView;
  active: boolean;
  onChanged: (view: ConnectionView) => void;
}) {
  const [repository, setRepository] = useState('');
  const [write, setWrite] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [failure, setFailure] = useState<ApiFailure | null>(null);
  const [discovery, setDiscovery] = useState<{ result?: Discovery; failure?: ApiFailure } | null>(null);
  const [discovering, setDiscovering] = useState(false);
  const discoveryAbort = useRef<AbortController | null>(null);
  const canWrite = connection.allowedOperations.includes('write');
  const assigned = new Set(connection.assignments.map((row) => row.providerRepoId));

  useEffect(() => () => discoveryAbort.current?.abort(), []);

  async function assign(name: string) {
    setBusy(name);
    setFailure(null);
    try {
      const existing = connection.assignments.find(
        (row) => row.repository.toLowerCase() === name.toLowerCase(),
      );
      const view = await requestJson<ConnectionView>(
        `${API}/${encodeURIComponent(connection.id)}/assignments`,
        {
          method: 'POST',
          body: JSON.stringify({
            requestId: newRequestId(),
            repository: name,
            operations: write && canWrite ? ['read', 'write'] : ['read'],
            ...(existing ? { expectedRevision: existing.revision } : {}),
          }),
        },
      );
      setRepository('');
      onChanged(view);
    } catch (error) {
      setFailure(asFailure(error));
    } finally {
      setBusy(null);
    }
  }

  async function unassign(row: ConnectionView['assignments'][number]) {
    setBusy(row.repository);
    setFailure(null);
    try {
      const view = await requestJson<ConnectionView>(
        `${API}/${encodeURIComponent(connection.id)}/assignments/remove`,
        {
          method: 'POST',
          body: JSON.stringify({
            requestId: newRequestId(),
            providerRepoId: row.providerRepoId,
            repository: row.repository,
          }),
        },
      );
      onChanged(view);
    } catch (error) {
      setFailure(asFailure(error));
    } finally {
      setBusy(null);
    }
  }

  async function discover() {
    discoveryAbort.current?.abort();
    const controller = new AbortController();
    discoveryAbort.current = controller;
    setDiscovering(true);
    try {
      const result = await requestJson<Discovery>(
        `${API}/${encodeURIComponent(connection.id)}/repositories`,
        { signal: controller.signal },
      );
      if (controller.signal.aborted) return;
      setDiscovery({ result });
    } catch (error) {
      if (controller.signal.aborted) return;
      setDiscovery({ failure: asFailure(error) });
    } finally {
      if (discoveryAbort.current === controller) {
        discoveryAbort.current = null;
        setDiscovering(false);
      }
    }
  }

  return (
    <section aria-label="Assigned repositories" className="space-y-3">
      <h5 className="text-sm font-semibold text-slate-800 dark:text-slate-100">Assigned repositories</h5>
      {connection.assignments.length === 0 ? (
        <p className="text-sm text-slate-600 dark:text-slate-400">
          None. Without an assignment this connection reaches no repositories.
        </p>
      ) : (
        <ul className="space-y-2">
          {connection.assignments.map((row) => (
            <li
              key={row.providerRepoId ?? row.repository}
              className="flex items-center justify-between gap-3 rounded-xl border border-slate-200 px-3 py-2 text-sm dark:border-slate-800"
            >
              <span>
                <span className="font-medium text-slate-800 dark:text-slate-100">{row.repository}</span>{' '}
                <span className="text-xs text-slate-500 dark:text-slate-400">({row.operations.join(', ')})</span>
              </span>
              <button
                type="button"
                className={secondaryButtonClass}
                onClick={() => void unassign(row)}
                disabled={busy !== null}
                aria-label={`Unassign ${row.repository}`}
              >
                Unassign
              </button>
            </li>
          ))}
        </ul>
      )}
      {failure ? <FailureMessage failure={failure} /> : null}
      {active ? (
        <>
          <form
            className="flex flex-col gap-2 sm:flex-row sm:items-end"
            onSubmit={(event) => {
              event.preventDefault();
              void assign(repository.trim());
            }}
          >
            <label className="flex flex-1 flex-col gap-1 text-sm">
              <span className="font-medium text-slate-700 dark:text-slate-200">Assign repository (owner/name)</span>
              <input
                className={inputClass}
                value={repository}
                onChange={(event) => setRepository(event.target.value)}
                placeholder="owner/repo"
                autoComplete="off"
              />
            </label>
            {canWrite ? (
              <label className="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-200">
                <input type="checkbox" checked={write} onChange={(event) => setWrite(event.target.checked)} />
                Write
              </label>
            ) : null}
            <button
              type="submit"
              className={primaryButtonClass}
              disabled={busy !== null || !repository.trim()}
            >
              Assign
            </button>
            <button
              type="button"
              className={secondaryButtonClass}
              onClick={() => void discover()}
              disabled={discovering}
            >
              {discovering ? 'Loading…' : 'Browse repositories'}
            </button>
          </form>
          {discovery?.failure ? <FailureMessage failure={discovery.failure} /> : null}
          {discovery?.result ? (
            <div className="space-y-2">
              {!discovery.result.complete ? (
                <p
                  role="status"
                  className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-900/50 dark:bg-amber-900/20 dark:text-amber-300"
                >
                  Partial list: showing {discovery.result.repositories.length} repositories from{' '}
                  {discovery.result.pagesRead} page(s).{' '}
                  {discovery.result.diagnostics.map((entry) => `${failureLabel(entry.kind)}: ${entry.message ?? ''}`).join(' ')}{' '}
                  Existing assignments are unchanged.
                </p>
              ) : null}
              <ul aria-label="Discovered repositories" className="max-h-64 space-y-1 overflow-y-auto">
                {discovery.result.repositories.map((repo) => (
                  <li key={repo.providerRepoId} className="flex items-center justify-between gap-2 text-sm">
                    <span>
                      {repo.fullName}{' '}
                      <span className="text-xs text-slate-500 dark:text-slate-400">
                        default branch {repo.defaultBranch ?? 'unknown'}
                      </span>
                    </span>
                    <button
                      type="button"
                      className={secondaryButtonClass}
                      disabled={busy !== null || assigned.has(repo.providerRepoId)}
                      onClick={() => void assign(repo.fullName)}
                      aria-label={`Assign ${repo.fullName}`}
                    >
                      {assigned.has(repo.providerRepoId) ? 'Assigned' : 'Assign'}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </>
      ) : null}
    </section>
  );
}

interface ProbeEvidence {
  key: string;
  tested: { repo: string; mode: string; branch: string };
  result?: ProbeResult;
  failure?: ApiFailure;
}

function ProbePanel({ connection, probeModes }: { connection: ConnectionView; probeModes: ProbeMode[] }) {
  const panelId = useId();
  const [repo, setRepo] = useState(connection.assignments[0]?.repository ?? '');
  const [mode, setMode] = useState(probeModes[0]?.mode ?? 'indexing');
  const [branch, setBranch] = useState('');
  const [running, setRunning] = useState(false);
  const [evidence, setEvidence] = useState<ProbeEvidence | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const selectedMode = probeModes.find((option) => option.mode === mode) ?? null;
  // Everything a result depends on: a change makes earlier evidence historical.
  const inputsKey = JSON.stringify([
    connection.id,
    connection.policyRevision,
    connection.credentialRevision,
    connection.secretRevision,
    repo.trim(),
    mode,
    branch.trim(),
  ]);
  const runningKey = useRef<string | null>(null);

  useEffect(() => () => abortRef.current?.abort(), []);

  useEffect(() => {
    // An answer to superseded inputs must not arrive as current evidence.
    if (runningKey.current !== null && runningKey.current !== inputsKey) {
      abortRef.current?.abort();
      abortRef.current = null;
      runningKey.current = null;
      setRunning(false);
    }
  }, [inputsKey]);

  async function handleRun(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    runningKey.current = inputsKey;
    const key = inputsKey;
    const tested = { repo: repo.trim(), mode, branch: branch.trim() };
    setRunning(true);
    try {
      const result = await requestJson<ProbeResult>(
        `${API}/${encodeURIComponent(connection.id)}/probe`,
        {
          method: 'POST',
          signal: controller.signal,
          body: JSON.stringify({
            repo: tested.repo,
            mode: tested.mode,
            ...(tested.branch ? { baseBranch: tested.branch } : {}),
          }),
        },
      );
      if (controller.signal.aborted) return;
      setEvidence({ key, tested, result });
    } catch (error) {
      if (controller.signal.aborted) return;
      setEvidence({ key, tested, failure: asFailure(error) });
    } finally {
      if (abortRef.current === controller) {
        abortRef.current = null;
        runningKey.current = null;
        setRunning(false);
      }
    }
  }

  const historical = evidence !== null && evidence.key !== inputsKey;

  return (
    <section aria-labelledby={`${panelId}-title`} className="space-y-3">
      <h5 id={`${panelId}-title`} className="text-sm font-semibold text-slate-800 dark:text-slate-100">
        Test connection
      </h5>
      <p className="text-xs text-slate-600 dark:text-slate-400">
        Uses only this connection&apos;s credential and only reads. A passing test does not prove write
        permission.
      </p>
      <form className="grid gap-3 md:grid-cols-[minmax(0,1.5fr)_minmax(0,1.5fr)_minmax(0,1fr)_auto]" onSubmit={handleRun}>
        <label className="flex flex-col gap-1 text-sm">
          <span className="font-medium text-slate-700 dark:text-slate-200">Repository (owner/name)</span>
          <input
            className={inputClass}
            value={repo}
            onChange={(event) => setRepo(event.target.value)}
            list={`${panelId}-repos`}
            autoComplete="off"
            required
          />
          <datalist id={`${panelId}-repos`}>
            {connection.assignments.map((row) => (
              <option key={row.repository} value={row.repository} />
            ))}
          </datalist>
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="font-medium text-slate-700 dark:text-slate-200">Check</span>
          <select
            className={inputClass}
            value={mode}
            onChange={(event) => setMode(event.target.value)}
            aria-describedby={selectedMode ? `${panelId}-mode-help` : undefined}
          >
            {probeModes.map((option) => (
              <option key={option.mode} value={option.mode}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-sm">
          <span className="font-medium text-slate-700 dark:text-slate-200">Branch (optional)</span>
          <input
            className={inputClass}
            value={branch}
            onChange={(event) => setBranch(event.target.value)}
            placeholder="Repository default"
            autoComplete="off"
          />
        </label>
        <div className="flex items-end">
          <button type="submit" className={primaryButtonClass} disabled={running || !repo.trim() || !selectedMode}>
            {running ? 'Testing…' : 'Test connection'}
          </button>
        </div>
      </form>
      {selectedMode ? (
        <p id={`${panelId}-mode-help`} className="text-xs text-slate-600 dark:text-slate-400">
          {selectedMode.description}
        </p>
      ) : null}
      {evidence ? <ProbeEvidenceView evidence={evidence} historical={historical} /> : null}
    </section>
  );
}

function tri(value: boolean | null | undefined): string {
  if (value === true) return 'yes';
  if (value === false) return 'no';
  return 'unknown';
}

function DiagnosticList({ diagnostics }: { diagnostics: ProbeDiagnostic[] }) {
  if (diagnostics.length === 0) return null;
  return (
    <ul aria-label="Test diagnostics" className="space-y-1">
      {diagnostics.map((entry, index) => (
        <li
          key={`${entry.operation}-${index}`}
          className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-900/50 dark:bg-amber-900/20 dark:text-amber-200"
        >
          <span className="font-semibold">{failureLabel(entry.kind)}</span> ({entry.operation}
          {typeof entry.httpStatus === 'number' ? `, HTTP ${entry.httpStatus}` : ''})
          {entry.message ? `: ${entry.message}` : ''}
          {entry.retryable ? ' Try again later.' : ''}
        </li>
      ))}
    </ul>
  );
}

function ProbeEvidenceView({ evidence, historical }: { evidence: ProbeEvidence; historical: boolean }) {
  const { result, failure, tested } = evidence;
  return (
    <div
      aria-label={historical ? 'Earlier test result' : 'Test result'}
      role="region"
      className={`space-y-3 rounded-2xl border border-slate-200 p-4 text-sm dark:border-slate-800 ${historical ? 'opacity-70' : ''}`}
    >
      {historical ? (
        <p role="status" className="text-xs font-semibold text-slate-600 dark:text-slate-300">
          Earlier result: the inputs changed since this test ran ({tested.repo}, {tested.mode}
          {tested.branch ? `, ${tested.branch}` : ''}). Run the test again for current evidence.
        </p>
      ) : null}
      {failure ? <FailureMessage failure={failure} /> : null}
      {result ? (
        <>
          <dl className="grid gap-2 text-xs sm:grid-cols-3">
            <div>
              <dt className="font-medium text-slate-500 dark:text-slate-400">Repository readable</dt>
              <dd>{tri(result.repositoryAccessible)}</dd>
            </div>
            <div>
              <dt className="font-medium text-slate-500 dark:text-slate-400">Branch</dt>
              <dd>
                {result.resolvedBranch
                  ? `${result.resolvedBranch} (${result.branchSource === 'remote_default' ? 'repository default' : 'requested'})`
                  : 'unknown'}
                {result.defaultBranchAccessible !== null && result.defaultBranchAccessible !== undefined
                  ? `, readable: ${tri(result.defaultBranchAccessible)}`
                  : ''}
              </dd>
            </div>
            <div>
              <dt className="font-medium text-slate-500 dark:text-slate-400">Pull requests readable</dt>
              <dd>{tri(result.pullRequestAccessible)}</dd>
            </div>
          </dl>
          {!result.writeVerified ? (
            <p className="text-xs text-slate-600 dark:text-slate-400">
              Write permission was not tested. It is confirmed only by a real publish.
            </p>
          ) : null}
          <div className="min-w-0 overflow-x-auto">
            <table className="w-full text-xs">
              <caption className="sr-only">Permission checklist</caption>
              <thead>
                <tr className="text-left text-slate-500 dark:text-slate-400">
                  <th scope="col" className="py-1 pr-3 font-medium">Permission</th>
                  <th scope="col" className="py-1 pr-3 font-medium">Needed</th>
                  <th scope="col" className="py-1 font-medium">Result</th>
                </tr>
              </thead>
              <tbody>
                {result.permissionChecklist.map((item) => (
                  <tr key={`${item.permission}-${item.level}`} className="border-t border-slate-100 dark:border-slate-800">
                    <td className="py-1 pr-3">{item.permission}</td>
                    <td className="py-1 pr-3">
                      {item.level} {item.required ? '(required)' : '(optional)'}
                    </td>
                    <td className="py-1">{CHECK_STATUS_LABELS[item.status] ?? item.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <DiagnosticList diagnostics={result.diagnostics} />
        </>
      ) : null}
    </div>
  );
}

function RotationForm({
  connection,
  admissionLost,
  onRotated,
  onNotice,
}: {
  connection: ConnectionView;
  admissionLost: boolean;
  onRotated: (view: ConnectionView) => void;
  onNotice?: ((notice: Notice | null) => void) | undefined;
}) {
  const queryClient = useQueryClient();
  const [token, setToken] = useState('');
  const [requestId, setRequestId] = useState(newRequestId);
  const [submitting, setSubmitting] = useState(false);
  const [failure, setFailure] = useState<ApiFailure | null>(null);
  const expected = connection.secretRevision;

  useEffect(() => {
    if (admissionLost) setToken('');
  }, [admissionLost]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (expected === null || expected === undefined) return;
    const submittedToken = token;
    setToken('');
    setSubmitting(true);
    setFailure(null);
    try {
      const view = await requestJson<ConnectionView>(`${API}/${encodeURIComponent(connection.id)}/rotate`, {
        method: 'POST',
        body: JSON.stringify({ requestId, token: submittedToken, expectedSecretRevision: expected }),
      });
      setRequestId(newRequestId());
      onRotated(view);
      onNotice?.({ level: 'ok', text: `Token rotated for ${connection.displayName}.` });
    } catch (error) {
      const outcome = asFailure(error);
      if (isUncertain(outcome)) {
        try {
          const current = await reconcileConnection(queryClient, connection.id);
          if (current && (current.secretRevision ?? 0) > expected) {
            setRequestId(newRequestId());
            onNotice?.({ level: 'ok', text: `Token rotated for ${connection.displayName}.` });
            return;
          }
        } catch {
          // Report the unconfirmed outcome below.
        }
        setFailure({
          ...outcome,
          message: 'MoonMind did not confirm the rotation. The previous token stays active until a rotation is confirmed.',
        });
        return;
      }
      setFailure(outcome);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form className="space-y-2" onSubmit={handleSubmit} autoComplete="off">
      <h5 className="text-sm font-semibold text-slate-800 dark:text-slate-100">Rotate token</h5>
      <p className="text-xs text-slate-600 dark:text-slate-400">
        The replacement must belong to the same GitHub account. The current token stays active if validation fails.
      </p>
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
        <label className="flex flex-1 flex-col gap-1 text-sm">
          <span className="font-medium text-slate-700 dark:text-slate-200">Replacement token</span>
          <input
            className={inputClass}
            type="password"
            value={token}
            onChange={(event) => setToken(event.target.value)}
            autoComplete="new-password"
            spellCheck={false}
          />
        </label>
        <button
          type="submit"
          className={primaryButtonClass}
          disabled={submitting || !token || expected === null || expected === undefined}
        >
          {submitting ? 'Rotating…' : 'Rotate token'}
        </button>
      </div>
      {failure ? <FailureMessage failure={failure} /> : null}
    </form>
  );
}

export default SourceControlSettings;
