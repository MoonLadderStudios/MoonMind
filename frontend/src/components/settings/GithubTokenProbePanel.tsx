import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from 'react';
import type { components } from '../../generated/openapi';
import { ApiError, fetchApi, getErrorStatus } from '../../lib/api/client';
import {
  useSettingsDraftGuard,
  useSettingsDraftRegistration,
} from './SettingsDraftGuard';

type Connection = components['schemas']['ConnectionSettingsItem'];
type ConnectionList = components['schemas']['ConnectionSettingsList'];
type SetupOptions = components['schemas']['ConnectionSetupOptions'];
type Receipt = components['schemas']['ConnectionSettingsReceipt'];
type CreateRequest = components['schemas']['ConnectionSettingsCreate'];
type UpdateRequest = components['schemas']['ConnectionSettingsUpdate'];
type BeginRequest = components['schemas']['GitHubAppBeginRequest'];
type AppSelectionRequest = Pick<
  BeginRequest,
  | 'appConnectionId'
  | 'requestId'
  | 'connectionId'
  | 'displayName'
  | 'expectedAccount'
  | 'permittedRepositories'
  | 'allowedOperations'
>;
type BeginResponse = components['schemas']['GitHubAppBeginResponse'];
const ROOT = '/api/v1/repository-connections';
const PENDING_KEY = 'moonmind.repository-connection.pending';
const fieldClass =
  'min-w-0 w-full rounded-xl border border-mm-border bg-transparent px-3 py-2 text-sm';
const buttonClass =
  'rounded-xl border border-mm-border px-3 py-2 text-sm disabled:opacity-50';
interface Draft {
  name: string;
  repositories: string;
  allowChanges: boolean;
}
interface PendingOperation {
  connectionId: string;
  requestId: string;
  action: 'create' | 'save' | 'disable' | 'app';
  setupState?: string;
  setupUrl?: string;
}
export interface GithubTokenProbePanelProps {
  canReadConnections: boolean;
  canWriteConnections: boolean;
  canRotateCredentials: boolean;
  onNotice?: (notice: { level: 'ok' | 'error'; text: string }) => void;
}
function draftFor(connection?: Connection): Draft {
  return {
    name: connection?.displayName ?? '',
    repositories: connection?.repositories.join('\n') ?? '',
    allowChanges: connection?.allowedOperations.includes('write') ?? false,
  };
}
function pendingFromStorage(): PendingOperation | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(PENDING_KEY) ?? 'null');
    return value &&
      typeof value.connectionId === 'string' &&
      typeof value.requestId === 'string' &&
      ['create', 'save', 'disable', 'app'].includes(value.action)
      ? value
      : null;
  } catch {
    return null;
  }
}
function newId(): string {
  return `repository-connection:${crypto.randomUUID()}`;
}
function rejectedWithoutCommit(error: unknown): boolean {
  if (!(error instanceof ApiError)) return false;
  try {
    return JSON.parse(error.body)?.detail?.mutationCommitted === false;
  } catch {
    return false;
  }
}

/** Ordinary Source Control Settings. Credentials never enter query or draft caches. */
export function GithubTokenProbePanel({
  canReadConnections,
  canWriteConnections,
  canRotateCredentials,
  onNotice,
}: GithubTokenProbePanelProps) {
  const [items, setItems] = useState<Connection[]>([]);
  const [apps, setApps] = useState<SetupOptions['apps']>([]);
  const [selectedId, setSelectedId] = useState('');
  const [createId, setCreateId] = useState(newId);
  const [draft, setDraft] = useState<Draft>(() => draftFor());
  const [kind, setKind] = useState<'pat' | 'github_app'>('pat');
  const [appChoice, setAppChoice] = useState('');
  const [expectedAccount, setExpectedAccount] = useState('');
  const [pending, setPending] = useState<PendingOperation | null>(
    pendingFromStorage,
  );
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmDisable, setConfirmDisable] = useState(false);
  const credential = useRef<HTMLInputElement>(null);
  const generation = useRef(0);
  const refreshRequest = useRef(0);
  const initialized = useRef(false);
  const alive = useRef(true);
  const selected = items.find((item) => item.id === selectedId);
  const selectionUnavailable = selectedId !== '' && !selected;
  const current = useRef({
    selectedId,
    draft,
    selected,
    pending,
    canReadConnections,
    canWriteConnections,
  });
  current.current = {
    selectedId,
    draft,
    selected,
    pending,
    canReadConnections,
    canWriteConnections,
  };
  const dirty = JSON.stringify(draft) !== JSON.stringify(draftFor(selected));
  const { requestDeparture } = useSettingsDraftGuard();
  function clearCredential() {
    if (credential.current) credential.current.value = '';
  }
  function invalidate() {
    generation.current += 1;
    setError(null);
    setBusy(false);
    clearCredential();
  }
  function remember(operation: PendingOperation | null) {
    setPending(operation);
    current.current.pending = operation;
    try {
      if (operation)
        sessionStorage.setItem(PENDING_KEY, JSON.stringify(operation));
      else sessionStorage.removeItem(PENDING_KEY);
    } catch {
      /* State still prevents duplicate creation during this interaction. */
    }
  }
  function cancel() {
    invalidate();
    setDraft(draftFor(current.current.selected));
    setConfirmDisable(false);
  }
  useSettingsDraftRegistration('repository-connection', dirty, cancel);

  const refresh = useCallback(async () => {
    const request = ++refreshRequest.current;
    const requestGeneration = ++generation.current;
    setBusy(false);
    if (!canReadConnections) {
      setLoading(false);
      return;
    }
    setLoading(true);
    const [connections, options] = await Promise.allSettled([
      fetchApi<ConnectionList>(ROOT),
      fetchApi<SetupOptions>(`${ROOT}/setup-options`),
    ]);
    if (
      !alive.current ||
      request !== refreshRequest.current ||
      !current.current.canReadConnections
    )
      return;
    if (connections.status === 'fulfilled') {
      const next = connections.value.items;
      const previous = current.current.selected;
      const nextSelected = next.find(
        (item) => item.id === current.current.selectedId,
      );
      const draftWasClean =
        JSON.stringify(current.current.draft) ===
        JSON.stringify(draftFor(previous));
      setItems((previousItems) =>
        next.map((item) => {
          const known = previousItems.find((value) => value.id === item.id);
          return known &&
            (known.policyRevision > item.policyRevision ||
              known.credentialRevision > item.credentialRevision)
            ? known
            : item;
        }),
      );
      if (!initialized.current && !current.current.pending) {
        const initial = next[0];
        setSelectedId(initial?.id ?? '');
        setDraft(draftFor(initial));
      } else if (nextSelected && draftWasClean)
        setDraft(draftFor(nextSelected));
      if (
        nextSelected?.policyRevision !== previous?.policyRevision ||
        nextSelected?.credentialRevision !== previous?.credentialRevision
      ) {
        if (credential.current) credential.current.value = '';
      }
      initialized.current = true;
      if (requestGeneration === generation.current) setError(null);
    } else if (requestGeneration === generation.current)
      setError(
        'Connections could not be refreshed. Your draft and saved configuration are preserved.',
      );
    if (options.status === 'fulfilled') setApps(options.value.apps);
    setLoading(false);
  }, [canReadConnections]);
  useEffect(() => {
    alive.current = true;
    void refresh();
    return () => {
      alive.current = false;
      generation.current += 1;
      refreshRequest.current += 1;
    };
  }, [refresh]);
  useEffect(() => {
    if (!canWriteConnections || !canRotateCredentials || !canReadConnections) {
      generation.current += 1;
      clearCredential();
      setBusy(false);
    }
  }, [canWriteConnections, canRotateCredentials, canReadConnections]);

  function select(id: string) {
    requestDeparture(() => {
      invalidate();
      const item = items.find((value) => value.id === id);
      setSelectedId(id);
      setDraft(draftFor(item));
      setConfirmDisable(false);
      if (!id) {
        setCreateId(newId());
        setKind('pat');
      }
    }, 'Change repository connection? Your unsaved changes will be discarded.');
  }
  function edit(values: Partial<Draft>) {
    generation.current += 1;
    setError(null);
    setBusy(false);
    setDraft((value) => ({ ...value, ...values }));
  }
  function acceptSaved(
    operation: PendingOperation,
    saved: Connection,
    requestGeneration: number,
  ) {
    if (saved.id !== operation.connectionId)
      throw new Error('Mismatched connection response');
    // Keep newer recorded revisions and any newer refresh request.
    if (requestGeneration === generation.current) {
      refreshRequest.current += 1;
      setLoading(false);
    }
    setItems((values) => {
      const known = values.find((value) => value.id === saved.id);
      if (
        known &&
        (known.policyRevision > saved.policyRevision ||
          known.credentialRevision > saved.credentialRevision)
      )
        return values;
      return [...values.filter((item) => item.id !== saved.id), saved];
    });
    if (current.current.pending?.requestId === operation.requestId)
      remember(null);
    if (
      alive.current &&
      requestGeneration === generation.current &&
      current.current.canReadConnections
    ) {
      setSelectedId(saved.id);
      setDraft(draftFor(saved));
      setError(null);
      setConfirmDisable(false);
      onNotice?.({ level: 'ok', text: 'Repository connection saved.' });
    }
  }
  async function reconcile(
    operation: PendingOperation,
    requestGeneration = generation.current,
  ) {
    try {
      const receipt = await fetchApi<Receipt>(
        `${ROOT}/${encodeURIComponent(operation.connectionId)}/operations/${encodeURIComponent(operation.requestId)}`,
      );
      if (!alive.current) return;
      if (
        receipt.committed &&
        receipt.requestId === operation.requestId &&
        receipt.connection
      )
        acceptSaved(operation, receipt.connection, requestGeneration);
      else if (requestGeneration === generation.current)
        setError(
          'The saved result is not confirmed yet. Check this same operation before creating or saving again.',
        );
    } catch {
      if (alive.current && requestGeneration === generation.current)
        setError(
          'The saved result is unavailable. Your draft is preserved; check this same operation again.',
        );
    }
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!canWriteConnections || pending || busy || selectionUnavailable) {
      clearCredential();
      return;
    }
    const requestGeneration = generation.current;
    const operation: PendingOperation = {
      connectionId: selected?.id ?? createId,
      requestId: crypto.randomUUID(),
      action: selected ? 'save' : kind === 'github_app' ? 'app' : 'create',
    };
    const names = draft.repositories
      .split('\n')
      .map((value) => value.trim())
      .filter(Boolean);
    const plaintext = credential.current?.value || undefined;
    clearCredential();
    setBusy(true);
    setError(null);
    remember(operation);
    try {
      if (operation.action === 'app') {
        const body: AppSelectionRequest = {
          appConnectionId: appChoice,
          requestId: operation.requestId,
          connectionId: operation.connectionId,
          displayName: draft.name,
          expectedAccount,
          permittedRepositories: names,
          allowedOperations: draft.allowChanges ? ['read', 'write'] : ['read'],
        };
        const result = await fetchApi<BeginResponse>(
          `${ROOT}/github-app/begin`,
          { method: 'POST', body: JSON.stringify(body) },
        );
        if (
          result.connectionId !== operation.connectionId ||
          result.requestId !== operation.requestId
        )
          throw new Error('Mismatched setup response');
        if (
          alive.current &&
          current.current.pending?.requestId === operation.requestId
        )
          remember({
            ...operation,
            setupState: result.state,
            setupUrl: result.setupUrl,
          });
      } else {
        let body: CreateRequest | UpdateRequest;
        if (selected) {
          body = {
            requestId: operation.requestId,
            expectedPolicyRevision: selected.policyRevision,
            expectedCredentialRevision: selected.credentialRevision,
            displayName: draft.name,
          };
          if (draft.repositories !== selected.repositories.join('\n'))
            body.repositories = names;
          if (
            draft.allowChanges !== selected.allowedOperations.includes('write')
          )
            body.allowedOperations = draft.allowChanges
              ? ['read', 'write']
              : ['read'];
          if (plaintext && canRotateCredentials) body.plaintext = plaintext;
        } else
          body = {
            requestId: operation.requestId,
            connectionId: operation.connectionId,
            displayName: draft.name,
            plaintext: plaintext ?? '',
            repositories: names,
            allowedOperations: draft.allowChanges
              ? ['read', 'write']
              : ['read'],
          };
        const saved = await fetchApi<Connection>(
          selected ? `${ROOT}/${encodeURIComponent(selected.id)}` : ROOT,
          { method: selected ? 'PATCH' : 'POST', body: JSON.stringify(body) },
        );
        if (alive.current) acceptSaved(operation, saved, requestGeneration);
      }
    } catch (cause) {
      const status = getErrorStatus(cause);
      if (
        rejectedWithoutCommit(cause) ||
        (status !== null && status >= 400 && status < 500)
      ) {
        if (current.current.pending?.requestId === operation.requestId)
          remember(null);
        if (alive.current && requestGeneration === generation.current) {
          const message =
            status === 409
              ? 'Connection changed. Refresh and review your preserved draft before saving.'
              : 'Connection could not be saved. Your draft and existing assignments are preserved.';
          setError(message);
          onNotice?.({ level: 'error', text: message });
        }
      } else await reconcile(operation, requestGeneration);
    } finally {
      if (alive.current && requestGeneration === generation.current)
        setBusy(false);
    }
  }
  async function disable() {
    if (!selected || !canWriteConnections || pending) return;
    const requestGeneration = generation.current;
    const operation: PendingOperation = {
      connectionId: selected.id,
      requestId: crypto.randomUUID(),
      action: 'disable',
    };
    setBusy(true);
    clearCredential();
    remember(operation);
    try {
      const saved = await fetchApi<Connection>(
        `${ROOT}/${encodeURIComponent(selected.id)}/disable`,
        {
          method: 'POST',
          body: JSON.stringify({
            requestId: operation.requestId,
            expectedPolicyRevision: selected.policyRevision,
            expectedCredentialRevision: selected.credentialRevision,
          }),
        },
      );
      if (alive.current) acceptSaved(operation, saved, requestGeneration);
    } catch (cause) {
      const status = getErrorStatus(cause);
      if (status !== null && status < 500) {
        remember(null);
        if (requestGeneration === generation.current)
          setError(
            'Connection could not be disabled. Refresh and review its current revision.',
          );
      } else await reconcile(operation, requestGeneration);
    } finally {
      if (alive.current && requestGeneration === generation.current)
        setBusy(false);
    }
  }

  // The existing App callback is the only installation verifier. Callback input
  // contains provider state/installation identity, never a credential or authority.
  useEffect(() => {
    const query = new URLSearchParams(window.location.search);
    const installationId = query.get('installation_id');
    const state = query.get('state');
    if (
      !pending ||
      pending.action !== 'app' ||
      !installationId ||
      !state ||
      state !== pending.setupState ||
      !canWriteConnections
    )
      return;
    query.delete('installation_id');
    query.delete('state');
    window.history.replaceState(
      window.history.state,
      '',
      window.location.pathname +
        (query.size ? `?${query}` : '') +
        window.location.hash,
    );
    const requestGeneration = generation.current;
    setBusy(true);
    void fetchApi(`${ROOT}/github-app/callback`, {
      method: 'POST',
      body: JSON.stringify({
        state,
        installationId,
        connectionId: pending.connectionId,
      }),
    })
      .then(
        () => reconcile(pending, requestGeneration),
        () => reconcile(pending, requestGeneration),
      )
      .finally(() => {
        if (alive.current && requestGeneration === generation.current)
          setBusy(false);
      });
    // Pending operations are stable; render events must never repeat the POST.
  }, [pending?.requestId, canWriteConnections]);

  if (!canReadConnections)
    return (
      <section aria-label="Source Control">
        <h3>Source Control</h3>
        <p>Repository connection access is unavailable.</p>
      </section>
    );
  if (loading && !initialized.current)
    return (
      <section aria-label="Source Control" aria-busy="true">
        <h3>Source Control</h3>
        <p>Loading repository connections…</p>
        {error && <p role="alert">{error}</p>}
      </section>
    );
  return (
    <section
      aria-label="Source Control"
      aria-busy={loading || busy}
      className="min-w-0 space-y-4 rounded-3xl border border-mm-border p-4 sm:p-6"
    >
      <header>
        <h3 className="text-lg font-semibold">Source Control</h3>
        <p className="text-sm">
          Named repository connections are separate from model Provider
          Profiles. Assign explicit repositories; an empty assignment list
          grants no repository access.
        </p>
      </header>
      <div className="flex min-w-0 flex-wrap items-end gap-3">
        {items.length > 0 && (
          <label className="flex min-w-0 flex-1 flex-col gap-1">
            Repository connection
            <select
              className={fieldClass}
              value={selectedId}
              onChange={(event) => select(event.target.value)}
            >
              <option value="">New connection</option>
              {items.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.displayName}
                </option>
              ))}
            </select>
          </label>
        )}
        <button
          type="button"
          className={buttonClass}
          onClick={() => void refresh()}
        >
          Refresh connections
        </button>
        <button
          type="button"
          className={buttonClass}
          disabled={!canWriteConnections || pending !== null}
          onClick={() => select('')}
        >
          New connection
        </button>
      </div>
      {selected && (
        <dl className="grid min-w-0 gap-2 text-sm sm:grid-cols-2">
          <div>
            <dt>Validated account</dt>
            <dd className="break-words">
              {selected.account ?? 'Account verification required'}
            </dd>
          </div>
          <div>
            <dt>State</dt>
            <dd>
              {selected.lifecycle} ·{' '}
              <span>Revision {selected.policyRevision}</span>
            </dd>
          </div>
          {selected.installation && (
            <div>
              <dt>GitHub App installation</dt>
              <dd>{selected.installation}</dd>
            </div>
          )}
        </dl>
      )}
      {selectionUnavailable && (
        <p role="alert">
          Selected connection is no longer available. Your safe draft is
          preserved; refresh or choose another connection.
        </p>
      )}
      {error && (
        <p role="alert" className="break-words text-sm">
          {error}
        </p>
      )}
      {pending && (
        <div role="status" className="space-y-2 text-sm">
          <p>
            {pending.action === 'app' && pending.setupUrl
              ? 'Continue the GitHub installation, then return here to see the verified connection.'
              : 'This operation is awaiting confirmation. Its request identity is preserved.'}
          </p>
          {pending.setupUrl && (
            <a
              href={pending.setupUrl}
              className={buttonClass}
              target="_blank"
              rel="noreferrer noopener"
            >
              Continue installation
            </a>
          )}
          <button
            type="button"
            className={buttonClass}
            onClick={() => void reconcile(pending)}
          >
            Check saved result
          </button>
        </div>
      )}
      <form
        onSubmit={(event) => void submit(event)}
        className="grid min-w-0 gap-4 sm:grid-cols-2"
      >
        <label className="flex min-w-0 flex-col gap-1">
          Connection name
          <input
            className={fieldClass}
            value={draft.name}
            required
            autoComplete="off"
            onChange={(event) => edit({ name: event.target.value })}
            disabled={!canWriteConnections}
          />
        </label>
        {!selectedId && (
          <label className="flex min-w-0 flex-col gap-1">
            Connection method
            <select
              className={fieldClass}
              value={kind}
              onChange={(event) => {
                invalidate();
                setKind(event.target.value as typeof kind);
              }}
              disabled={!canWriteConnections}
            >
              <option value="pat">Personal access token</option>
              <option value="github_app">GitHub App</option>
            </select>
          </label>
        )}
        {!selectedId && kind === 'github_app' ? (
          <>
            <label className="flex min-w-0 flex-col gap-1">
              Configured GitHub App
              <select
                className={fieldClass}
                value={appChoice}
                required
                onChange={(event) => {
                  invalidate();
                  setAppChoice(event.target.value);
                }}
              >
                <option value="">Select an App</option>
                {apps.map((app) => (
                  <option value={app.id} key={app.id}>
                    {app.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex min-w-0 flex-col gap-1">
              GitHub account or organization
              <input
                className={fieldClass}
                value={expectedAccount}
                required
                onChange={(event) => {
                  invalidate();
                  setExpectedAccount(event.target.value);
                }}
              />
            </label>
            {apps.length === 0 && (
              <p className="text-sm sm:col-span-2">
                No configured GitHub App is available. App signing configuration
                must be enrolled through the existing App setup before it can be
                selected here.
              </p>
            )}
          </>
        ) : (
          (!selected || selected.credentialKind === 'pat') && (
            <div className="flex min-w-0 flex-col gap-1">
              <label className="flex min-w-0 flex-col gap-1">
                {selectedId
                  ? 'Replacement personal access token'
                  : 'Personal access token'}
                <input
                  className={fieldClass}
                  ref={credential}
                  type="password"
                  autoComplete="new-password"
                  required={!selectedId}
                  disabled={
                    !canWriteConnections ||
                    selectionUnavailable ||
                    (Boolean(selected) && !canRotateCredentials)
                  }
                  onChange={() => {
                    generation.current += 1;
                    setError(null);
                  }}
                />
              </label>
              <p className="text-xs">
                {selected
                  ? 'Leave empty to keep the saved credential.'
                  : 'GitHub verifies the account when you submit.'}{' '}
                Credentials clear immediately after submission.
              </p>
            </div>
          )
        )}
        <label className="flex min-w-0 flex-col gap-1 sm:col-span-2">
          Repositories (one owner/repo per line)
          <textarea
            className={fieldClass}
            rows={3}
            value={draft.repositories}
            disabled={!canWriteConnections}
            onChange={(event) => edit({ repositories: event.target.value })}
          />
        </label>
        <label className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={draft.allowChanges}
            disabled={!canWriteConnections}
            onChange={(event) => edit({ allowChanges: event.target.checked })}
          />
          Allow repository changes
        </label>
        <div className="flex flex-wrap gap-2 sm:col-span-2">
          <button
            className={buttonClass}
            type="submit"
            disabled={
              !canWriteConnections ||
              selectionUnavailable ||
              busy ||
              pending !== null ||
              (!selected && kind === 'github_app' && !appChoice)
            }
          >
            {selectedId
              ? 'Save connection'
              : kind === 'github_app'
                ? 'Install GitHub App'
                : 'Create connection'}
          </button>
          <button className={buttonClass} type="button" onClick={cancel}>
            Cancel changes
          </button>
          {selected?.lifecycle === 'active' && (
            <button
              className={buttonClass}
              type="button"
              disabled={!canWriteConnections || pending !== null}
              onClick={() => {
                clearCredential();
                setConfirmDisable(true);
              }}
            >
              Disable connection
            </button>
          )}
        </div>
      </form>
      {confirmDisable && (
        <div
          role="group"
          aria-label="Confirm disable connection"
          className="space-y-2"
        >
          <p>
            Disable {selected?.displayName}? Saved assignments and credentials
            remain available for active-work cleanup.
          </p>
          <div className="flex flex-wrap gap-2">
            <button
              className={buttonClass}
              type="button"
              disabled={busy || pending !== null}
              onClick={() => void disable()}
            >
              Confirm disable
            </button>
            <button
              className={buttonClass}
              type="button"
              onClick={() => setConfirmDisable(false)}
            >
              Keep connection
            </button>
          </div>
        </div>
      )}
      {selected && (
        <div className="space-y-2 text-sm">
          <button type="button" className={buttonClass} disabled>
            Test Connection
          </button>
          <p>
            Selected-connection testing is unavailable in this API revision.
            Saved configuration and other permitted edits remain available.
            Repository reads do not verify writes.
          </p>
        </div>
      )}
    </section>
  );
}
