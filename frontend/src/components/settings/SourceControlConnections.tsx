import { FormEvent, useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';

import type { components } from '../../generated/openapi';
import { GithubTokenProbePanel } from './GithubTokenProbePanel';
import { useSettingsDraftRegistration } from './SettingsDraftGuard';

type Connection = components['schemas']['RepositoryConnectionView'];
type ConnectionList = components['schemas']['RepositoryConnectionListResponse'];
type AppBeginResponse = components['schemas']['GitHubAppBeginResponse'];
type UpdateBody = Omit<components['schemas']['ConnectionUpdateRequest'], 'token'>;

interface PendingUpdate {
  name: string;
  publish: boolean;
  tokenFingerprint: string | null;
  body: UpdateBody;
}

interface Notice {
  level: 'ok' | 'error';
  text: string;
}

export const SOURCE_CONTROL_QUERY_KEY = ['repository-connections'] as const;
const API = '/api/v1/repository-connections';
const PENDING_APP_KEY = 'moonmind.sourceControl.pendingApp';
const PUBLISH_OPERATIONS = ['read', 'write', 'branch_write', 'review_request'];
const READ_OPERATIONS = ['read'];

export interface SourceControlConnectionsProps {
  canRunProbe: boolean;
  onNotice?: (notice: Notice | null) => void;
  /** Leaves the page for the GitHub App install screen. */
  navigate?: (url: string) => void;
}

/** A save whose outcome is unknown: the request may have committed. */
class UncertainSaveError extends Error {}

class RequestError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

function newRequestId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `req-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export function connectionIdFor(name: string): string {
  const slug = name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 63)
    .replace(/-+$/g, '');
  return slug || 'github';
}

async function sendJson<T>(url: string, method: string, body?: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch(url, {
      method,
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
  } catch {
    throw new UncertainSaveError('The save could not be confirmed.');
  }
  const payload = (await response.json().catch(() => ({}))) as { detail?: unknown };
  if (!response.ok) {
    if (response.status >= 500 && response.status !== 503) {
      throw new UncertainSaveError('The save could not be confirmed.');
    }
    const detail = typeof payload.detail === 'string' ? payload.detail : `HTTP ${response.status}`;
    throw new RequestError(detail, response.status);
  }
  return payload as T;
}

async function fetchConnection(id: string): Promise<Connection | null> {
  const response = await fetch(`${API}/${encodeURIComponent(id)}`, {
    headers: { Accept: 'application/json' },
  });
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(`Could not reload the connection (HTTP ${response.status}).`);
  return (await response.json()) as Connection;
}

async function updateCommitted(id: string, requestId: string): Promise<boolean> {
  const response = await fetch(
    `${API}/${encodeURIComponent(id)}/requests/${encodeURIComponent(requestId)}`,
    { headers: { Accept: 'application/json' } },
  );
  if (!response.ok) throw new Error('The change could not be confirmed.');
  const status = (await response.json()) as components['schemas']['ConnectionRequestStatus'];
  return status.committed === true;
}

async function tokenFingerprint(token: string): Promise<string | null> {
  if (!token) return '';
  // Retain only an in-memory digest to recognize the same secret on re-entry.
  // HTTP deployments may lack WebCrypto; never assume two tokens match there.
  if (!globalThis.crypto?.subtle) return null;
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(token));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
}

function kindLabel(connection: Connection): string {
  switch (connection.credentialKind) {
    case 'personal_access_token':
      return 'Personal access token';
    case 'github_app':
      return 'GitHub App installation';
    case 'deployment':
      return 'Deployment GitHub credential';
    default:
      return 'Other credential';
  }
}

function assignmentSummary(connection: Connection): string {
  const count = connection.assignments?.length ?? 0;
  if (count === 0) return 'No repositories assigned';
  return count === 1 ? '1 repository' : `${count} repositories`;
}

function canPublish(operations: readonly string[]): boolean {
  return PUBLISH_OPERATIONS.every((operation) => operations.includes(operation));
}

const inputClass =
  'w-full min-w-0 rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 shadow-sm focus:outline-none focus:ring-2 focus:ring-mm-accent dark:border-slate-700 dark:bg-slate-900 dark:text-white';
const primaryButton =
  'inline-flex items-center justify-center rounded-xl bg-mm-accent px-4 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-mm-accent/90 disabled:cursor-not-allowed disabled:opacity-50';
const secondaryButton =
  'inline-flex items-center justify-center rounded-xl border border-slate-300 px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50 dark:border-slate-700 dark:text-slate-200 dark:hover:bg-slate-800';

function ErrorText({ children }: { children: string | null }) {
  if (!children) return null;
  return (
    <p role="alert" className="text-sm text-rose-700 dark:text-rose-300">
      {children}
    </p>
  );
}

export function SourceControlConnections({
  canRunProbe,
  onNotice,
  navigate = (url) => window.location.assign(url),
}: SourceControlConnectionsProps) {
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [adding, setAdding] = useState<'pat' | 'app' | null>(null);
  // Stable request identities per operation, reused only to retry the same
  // uncertain operation and cleared once its outcome is known.
  const requestIds = useRef(new Map<string, string>());
  const selectedRef = useRef<string | null>(null);
  selectedRef.current = selectedId;

  const connectionsQuery = useQuery<ConnectionList>({
    queryKey: SOURCE_CONTROL_QUERY_KEY,
    queryFn: async () => {
      const response = await fetch(API, { headers: { Accept: 'application/json' } });
      if (!response.ok) throw new Error(`Failed to load connections (HTTP ${response.status}).`);
      return response.json();
    },
  });
  const connections = connectionsQuery.data?.items ?? [];
  const selected =
    connections.find((item) => item.id === selectedId) ??
    (selectedId === null && adding === null ? connections[0] : undefined);

  function requestIdFor(operation: string): string {
    const existing = requestIds.current.get(operation);
    if (existing) return existing;
    const created = newRequestId();
    requestIds.current.set(operation, created);
    return created;
  }

  function settle(operation: string) {
    requestIds.current.delete(operation);
  }

  /** Show the committed server record before anything else is offered. */
  function showCommitted(connection: Connection) {
    queryClient.setQueryData<ConnectionList>(SOURCE_CONTROL_QUERY_KEY, (current) => {
      const recorded = current?.items.find((item) => item.id === connection.id);
      // Responses can arrive out of order across save, assignment and disable.
      // A confirmed newer policy must never be replaced by an older projection.
      if (recorded && recorded.policyRevision > connection.policyRevision) return current;
      const items = (current?.items ?? []).filter((item) => item.id !== connection.id);
      return {
        items: [...items, connection].sort((a, b) => a.displayName.localeCompare(b.displayName)),
      };
    });
    void queryClient.invalidateQueries({ queryKey: SOURCE_CONTROL_QUERY_KEY });
  }

  // GitHub returns here after an App installation with its installation ID.
  const callbackStarted = useRef(false);
  const [appCallbackError, setAppCallbackError] = useState<string | null>(null);
  useEffect(() => {
    const installationId = searchParams.get('installation_id');
    const state = searchParams.get('state');
    if (!installationId || !state || callbackStarted.current) return;
    callbackStarted.current = true;
    let pending: { connectionId?: string; state?: string };
    try {
      pending = JSON.parse(window.sessionStorage.getItem(PENDING_APP_KEY) ?? '{}');
    } catch {
      pending = {};
    }
    const clearParams = () =>
      setSearchParams(
        (current) => {
          const next = new URLSearchParams(current);
          ['installation_id', 'state', 'setup_action', 'code'].forEach((key) => next.delete(key));
          return next;
        },
        { replace: true },
      );
    if (!pending.connectionId || pending.state !== state) {
      setAppCallbackError('This GitHub App installation was not started here. Start the App connection again.');
      clearParams();
      return;
    }
    const connectionId = pending.connectionId;
    void (async () => {
      try {
        await sendJson(`${API}/github-app/callback`, 'POST', { state, installationId, connectionId });
        window.sessionStorage.removeItem(PENDING_APP_KEY);
        const saved = await fetchConnection(connectionId);
        if (saved) showCommitted(saved);
        setSelectedId(connectionId);
        onNotice?.({ level: 'ok', text: 'GitHub App connection saved.' });
      } catch (err) {
        setAppCallbackError(err instanceof Error ? err.message : 'GitHub App connection failed.');
      } finally {
        clearParams();
      }
    })();
  }, [searchParams]);

  return (
    <section
      aria-label="Source Control"
      className="rounded-3xl border border-mm-border/80 bg-transparent p-6 shadow-sm"
    >
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <h3 className="text-lg font-semibold text-slate-900 dark:text-white">Source Control</h3>
          <p className="text-sm text-slate-600 dark:text-slate-400">
            Named GitHub connections that workflows use for the repositories you assign. They are
            separate from Provider Profiles, and a connection with no assigned repositories grants
            no repository access.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            className={secondaryButton}
            onClick={() => {
              setAdding('pat');
              setSelectedId(null);
            }}
          >
            Add token connection
          </button>
          <button
            type="button"
            className={secondaryButton}
            onClick={() => {
              setAdding('app');
              setSelectedId(null);
            }}
          >
            Connect GitHub App
          </button>
        </div>
      </header>

      <ErrorText>{appCallbackError}</ErrorText>

      <div className="mt-4 grid gap-4 md:grid-cols-[minmax(0,16rem)_minmax(0,1fr)]">
        <nav aria-label="Connections" className="min-w-0 space-y-2">
          {connectionsQuery.isLoading ? (
            <p className="text-sm text-slate-500">Loading connections…</p>
          ) : connectionsQuery.isError ? (
            <p role="alert" className="text-sm text-rose-700 dark:text-rose-300">
              Failed to load connections. Saved connections are unchanged.
            </p>
          ) : connections.length === 0 ? (
            <p className="text-sm text-slate-600 dark:text-slate-400">
              No connections yet. Add a token connection or connect a GitHub App.
            </p>
          ) : (
            <ul className="space-y-2">
              {connections.map((connection) => {
                const isSelected = selected?.id === connection.id;
                return (
                  <li key={connection.id}>
                    <button
                      type="button"
                      aria-current={isSelected ? 'true' : undefined}
                      onClick={() => {
                        setAdding(null);
                        setSelectedId(connection.id);
                      }}
                      className={`w-full rounded-2xl border px-3 py-2 text-left text-sm ${
                        isSelected
                          ? 'border-mm-accent bg-mm-accent/10'
                          : 'border-slate-200 hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/50'
                      }`}
                    >
                      <span className="block truncate font-medium text-slate-900 dark:text-white">
                        {connection.displayName}
                      </span>
                      <span className="block text-xs text-slate-600 dark:text-slate-400">
                        {kindLabel(connection)} · {assignmentSummary(connection)}
                        {connection.lifecycle !== 'active' ? ' · Disabled' : ''}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </nav>

        <div className="min-w-0">
          {adding === 'pat' ? (
            <PatCreateForm
              requestIdFor={requestIdFor}
              settle={settle}
              onCommitted={(connection) => {
                showCommitted(connection);
                setAdding(null);
                setSelectedId(connection.id);
                onNotice?.({ level: 'ok', text: `Saved ${connection.displayName}.` });
              }}
              onCancel={() => setAdding(null)}
            />
          ) : adding === 'app' ? (
            <AppConnectForm requestIdFor={requestIdFor} navigate={navigate} onCancel={() => setAdding(null)} />
          ) : selected ? (
            <ConnectionDetail
              key={selected.id}
              connection={selected}
              canRunProbe={canRunProbe}
              onNotice={onNotice}
              requestIdFor={requestIdFor}
              settle={settle}
              isStillSelected={(id) => (selectedRef.current ?? connections[0]?.id) === id}
              onCommitted={showCommitted}
              onReload={() => queryClient.invalidateQueries({ queryKey: SOURCE_CONTROL_QUERY_KEY })}
            />
          ) : null}
        </div>
      </div>
    </section>
  );
}

function PatCreateForm({
  requestIdFor,
  settle,
  onCommitted,
  onCancel,
}: {
  requestIdFor: (operation: string) => string;
  settle: (operation: string) => void;
  onCommitted: (connection: Connection) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState('');
  const [token, setToken] = useState('');
  const [publish, setPublish] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function discard() {
    settle(`create:${connectionIdFor(name)}`);
    setName('');
    setToken('');
    setPublish(false);
    setError(null);
  }

  useSettingsDraftRegistration('source-control-pat', Boolean(name || token || publish || saving), discard);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const connectionId = connectionIdFor(name);
    const operation = `create:${connectionId}`;
    const transient = token;
    // The token leaves component state as soon as it is handed to the request.
    setToken('');
    setSaving(true);
    setError(null);
    try {
      const saved = await sendJson<Connection>(`${API}/pat`, 'POST', {
        requestId: requestIdFor(operation),
        connectionId,
        displayName: name.trim(),
        token: transient,
        allowedOperations: publish ? PUBLISH_OPERATIONS : READ_OPERATIONS,
      });
      settle(operation);
      onCommitted(saved);
    } catch (err) {
      if (err instanceof UncertainSaveError) {
        // Reconcile the same operation instead of resubmitting the POST.
        const committed = await fetchConnection(connectionId).catch(() => null);
        if (committed) {
          settle(operation);
          onCommitted(committed);
          return;
        }
        setError('The connection was not saved. Enter the token again to retry the same request.');
      } else if (err instanceof RequestError) {
        settle(operation);
        setError(
          err.status === 409
            ? `A connection with the ID "${connectionId}" already exists. Choose another name.`
            : err.status === 401
              ? 'Your session ended. Sign in again, then re-enter the token.'
              : err.message,
        );
      }
    } finally {
      setSaving(false);
    }
  }

  return (
    <form aria-label="Add token connection" className="space-y-3" onSubmit={submit}>
      <h4 className="text-base font-semibold text-slate-900 dark:text-white">Add token connection</h4>
      <label className="flex flex-col gap-1 text-sm">
        <span className="font-medium text-slate-700 dark:text-slate-200">Connection name</span>
        <input className={inputClass} value={name} onChange={(e) => setName(e.target.value)} required autoComplete="off" />
      </label>
      <div className="flex flex-col gap-1 text-sm">
        <label className="flex flex-col gap-1">
          <span className="font-medium text-slate-700 dark:text-slate-200">GitHub personal access token</span>
          <input
            className={inputClass}
            type="password"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            required
            autoComplete="off"
            spellCheck={false}
            aria-describedby="source-control-token-help"
          />
        </label>
        <span id="source-control-token-help" className="text-xs text-slate-500 dark:text-slate-400">
          Stored encrypted as a Managed Secret. It is never shown again.
        </span>
      </div>
      <label className="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-200">
        <input type="checkbox" checked={publish} onChange={(e) => setPublish(e.target.checked)} />
        Allow publishing (push branches and open pull requests)
      </label>
      <ErrorText>{error}</ErrorText>
      <div className="flex flex-wrap gap-2">
        <button type="submit" className={primaryButton} disabled={saving || !name.trim() || !token}>
          {saving ? 'Saving…' : 'Save connection'}
        </button>
        <button
          type="button"
          className={secondaryButton}
          onClick={() => {
            discard();
            onCancel();
          }}
        >
          Cancel
        </button>
      </div>
    </form>
  );
}

function AppConnectForm({
  requestIdFor,
  navigate,
  onCancel,
}: {
  requestIdFor: (operation: string) => string;
  navigate: (url: string) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState('');
  const [appSlug, setAppSlug] = useState('');
  const [appId, setAppId] = useState('');
  const [repositories, setRepositories] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [setupUrl, setSetupUrl] = useState<string | null>(null);

  function discard() {
    setName('');
    setAppSlug('');
    setAppId('');
    setRepositories('');
    setError(null);
  }

  useSettingsDraftRegistration(
    'source-control-app',
    Boolean(name || appSlug || appId || repositories || starting),
    discard,
  );

  // Let the draft registration become clean before leaving for GitHub.
  useEffect(() => {
    if (!setupUrl) return;
    navigate(setupUrl);
    setSetupUrl(null);
  }, [navigate, setupUrl]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const connectionId = connectionIdFor(name);
    setStarting(true);
    setError(null);
    try {
      const begun = await sendJson<AppBeginResponse>(`${API}/github-app/begin`, 'POST', {
        requestId: requestIdFor(`app:${connectionId}`),
        connectionId,
        displayName: name.trim(),
        appSlug: appSlug.trim(),
        appId: appId.trim(),
        permittedRepositories: repositories
          .split(/[\s,]+/)
          .map((item) => item.trim())
          .filter(Boolean),
      });
      window.sessionStorage.setItem(
        PENDING_APP_KEY,
        JSON.stringify({ connectionId: begun.connectionId, state: begun.state }),
      );
      discard();
      setStarting(false);
      setSetupUrl(begun.setupUrl);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not start the GitHub App connection.');
      setStarting(false);
    }
  }

  return (
    <form aria-label="Connect GitHub App" className="space-y-3" onSubmit={submit}>
      <h4 className="text-base font-semibold text-slate-900 dark:text-white">Connect GitHub App</h4>
      <p className="text-sm text-slate-600 dark:text-slate-400">
        You will install the App on GitHub, then return here. MoonMind verifies the installation
        before saving it.
      </p>
      <label className="flex flex-col gap-1 text-sm">
        <span className="font-medium text-slate-700 dark:text-slate-200">Connection name</span>
        <input className={inputClass} value={name} onChange={(e) => setName(e.target.value)} required autoComplete="off" />
      </label>
      <label className="flex flex-col gap-1 text-sm">
        <span className="font-medium text-slate-700 dark:text-slate-200">App name in its GitHub URL</span>
        <input className={inputClass} value={appSlug} onChange={(e) => setAppSlug(e.target.value)} required autoComplete="off" />
      </label>
      <label className="flex flex-col gap-1 text-sm">
        <span className="font-medium text-slate-700 dark:text-slate-200">App ID</span>
        <input
          className={inputClass}
          value={appId}
          onChange={(e) => setAppId(e.target.value)}
          required
          inputMode="numeric"
          pattern="[0-9]+"
          autoComplete="off"
        />
      </label>
      <label className="flex flex-col gap-1 text-sm">
        <span className="font-medium text-slate-700 dark:text-slate-200">Limit to repositories (optional)</span>
        <input
          className={inputClass}
          value={repositories}
          onChange={(e) => setRepositories(e.target.value)}
          placeholder="owner/repo, owner/other"
          autoComplete="off"
        />
      </label>
      <ErrorText>{error}</ErrorText>
      <div className="flex flex-wrap gap-2">
        <button type="submit" className={primaryButton} disabled={starting || !name.trim() || !appSlug.trim() || !appId.trim()}>
          {starting ? 'Opening GitHub…' : 'Install on GitHub'}
        </button>
        <button type="button" className={secondaryButton} onClick={() => { discard(); onCancel(); }}>
          Cancel
        </button>
      </div>
    </form>
  );
}

function ConnectionDetail({
  connection,
  canRunProbe,
  onNotice,
  requestIdFor,
  settle,
  isStillSelected,
  onCommitted,
  onReload,
}: {
  connection: Connection;
  canRunProbe: boolean;
  onNotice?: ((notice: Notice | null) => void) | undefined;
  requestIdFor: (operation: string) => string;
  settle: (operation: string) => void;
  isStillSelected: (id: string) => boolean;
  onCommitted: (connection: Connection) => void;
  onReload: () => void;
}) {
  const [name, setName] = useState(connection.displayName);
  const [publish, setPublish] = useState(canPublish(connection.allowedOperations));
  const [token, setToken] = useState('');
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [rotationPending, setRotationPending] = useState(false);
  const pendingUpdate = useRef<PendingUpdate | null>(null);
  const currentConnection = useRef(connection);
  currentConnection.current = connection;
  const [repository, setRepository] = useState('');
  const [assignPublish, setAssignPublish] = useState(false);
  const [assignError, setAssignError] = useState<string | null>(null);
  const [assigning, setAssigning] = useState(false);
  const assignments = connection.assignments ?? [];
  const isPat = connection.credentialKind === 'personal_access_token';
  const isActive = connection.lifecycle === 'active';
  const changed =
    name.trim() !== connection.displayName ||
    publish !== canPublish(connection.allowedOperations) ||
    token.length > 0 || rotationPending || pendingUpdate.current !== null;
  const assignmentChanged = Boolean(repository || assignPublish);

  function discardEdit() {
    pendingUpdate.current = null;
    setName(connection.displayName);
    setPublish(canPublish(connection.allowedOperations));
    setToken('');
    setRotationPending(false);
    setSaveError(null);
  }

  function discardAssignment() {
    settle(`assign:${connection.id}:${repository.trim().toLowerCase()}`);
    setRepository('');
    setAssignPublish(false);
    setAssignError(null);
  }

  useSettingsDraftRegistration(`source-control-edit:${connection.id}`, changed || saving, discardEdit);
  useSettingsDraftRegistration(
    `source-control-assignment:${connection.id}`,
    assignmentChanged || assigning,
    discardAssignment,
  );

  function acceptEdit(saved: Connection) {
    pendingUpdate.current = null;
    setRotationPending(false);
    if (currentConnection.current.policyRevision > saved.policyRevision) return false;
    setName(saved.displayName);
    setPublish(canPublish(saved.allowedOperations));
    onCommitted(saved);
    return true;
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saving || (rotationPending && !token)) return;
    const transient = token;
    setRotationPending(Boolean(transient));
    setToken('');
    setSaving(true);
    setSaveError(null);
    let body: UpdateBody | undefined;
    try {
      const fingerprint = await tokenFingerprint(transient);
      const previous = pendingUpdate.current;
      const sameIntent = previous && previous.name === name.trim() && previous.publish === publish &&
        fingerprint !== null && previous.tokenFingerprint === fingerprint;
      body = sameIntent ? previous.body : {
        requestId: newRequestId(),
        // Reconcile/retry the original compare-and-set even after a refresh.
        expectedPolicyRevision: previous?.body.expectedPolicyRevision ?? connection.policyRevision,
      };
      if (!sameIntent) {
        if (name.trim() !== connection.displayName) body.displayName = name.trim();
        if (publish !== canPublish(connection.allowedOperations)) {
          const operations = publish
            ? Array.from(new Set([...connection.allowedOperations, ...PUBLISH_OPERATIONS]))
            : connection.allowedOperations.filter(
              (operation) => operation === 'read' || !PUBLISH_OPERATIONS.includes(operation),
            );
          body.allowedOperations = operations as NonNullable<UpdateBody['allowedOperations']>;
        }
      }
      pendingUpdate.current = { name: name.trim(), publish, tokenFingerprint: fingerprint, body };
      const saved = await sendJson<Connection>(`${API}/${encodeURIComponent(connection.id)}`, 'PATCH', {
        ...body,
        ...(transient ? { token: transient } : {}),
      });
      const isCurrent = acceptEdit(saved);
      if (isCurrent && isStillSelected(connection.id)) {
        onNotice?.({ level: 'ok', text: transient ? 'Token replaced.' : 'Connection saved.' });
      }
    } catch (err) {
      if (err instanceof UncertainSaveError && body) {
        // A newer revision can belong to another writer. Only this request's
        // durable receipt establishes that our edit (especially rotation) saved.
        const committed = await updateCommitted(connection.id, body.requestId).catch(() => false);
        const current = committed ? await fetchConnection(connection.id).catch(() => null) : null;
        if (current) {
          acceptEdit(current);
          return;
        }
        if (isStillSelected(connection.id)) {
          setSaveError(
            transient
              ? 'The change could not be confirmed. Enter the token again to retry; changed input starts a new request.'
              : 'The change could not be confirmed. Save again to retry; changed input starts a new request.',
          );
        }
      } else if (err instanceof RequestError) {
        pendingUpdate.current = null;
        if (isStillSelected(connection.id)) {
          setSaveError(
            err.status === 409
              ? 'This connection changed since it was loaded. Your edits are kept; review the current version and save again. Re-enter any new token.'
              : err.status === 401
                ? 'Your session ended. Sign in again, then re-enter the token.'
                : err.message,
          );
        }
        if (err.status === 409) onReload();
      } else if (isStillSelected(connection.id)) {
        setSaveError('Could not prepare the change. Re-enter any new token and try again.');
      }
    } finally {
      setSaving(false);
    }
  }

  async function disable() {
    const operation = `disable:${connection.id}`;
    try {
      const saved = await sendJson<Connection>(`${API}/${encodeURIComponent(connection.id)}/disable`, 'POST', {
        requestId: requestIdFor(operation),
      });
      settle(operation);
      onCommitted(saved);
    } catch (err) {
      if (err instanceof RequestError) settle(operation);
      if (isStillSelected(connection.id)) {
        setSaveError(err instanceof Error ? err.message : 'Could not disable the connection.');
      }
    }
  }

  async function assign(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const target = repository.trim();
    const operation = `assign:${connection.id}:${target.toLowerCase()}`;
    setAssigning(true);
    setAssignError(null);
    try {
      const saved = await sendJson<Connection>(
        `${API}/${encodeURIComponent(connection.id)}/assignments`,
        'POST',
        {
          requestId: requestIdFor(operation),
          repository: target,
          operations: assignPublish ? PUBLISH_OPERATIONS : READ_OPERATIONS,
        },
      );
      settle(operation);
      onCommitted(saved);
      if (isStillSelected(connection.id)) {
        setRepository('');
        setAssignPublish(false);
      }
    } catch (err) {
      if (err instanceof RequestError) settle(operation);
      if (isStillSelected(connection.id)) {
        setAssignError(
          err instanceof UncertainSaveError
            ? 'The assignment could not be confirmed. Existing assignments are unchanged; try again.'
            : err instanceof Error
              ? err.message
              : 'Could not assign the repository.',
        );
      }
      if (err instanceof UncertainSaveError) onReload();
    } finally {
      setAssigning(false);
    }
  }

  async function unassign(repositoryName: string, providerRepoId: string) {
    const operation = `unassign:${connection.id}:${providerRepoId}`;
    try {
      const saved = await sendJson<Connection>(
        `${API}/${encodeURIComponent(connection.id)}/assignments/remove`,
        'POST',
        { requestId: requestIdFor(operation), providerRepoId, repository: repositoryName },
      );
      settle(operation);
      onCommitted(saved);
    } catch (err) {
      if (err instanceof RequestError) settle(operation);
      if (isStillSelected(connection.id)) {
        setAssignError(err instanceof Error ? err.message : 'Could not remove the assignment.');
      }
    }
  }

  return (
    <div className="space-y-4" aria-label={`${connection.displayName} details`}>
      <div className="space-y-1">
        <h4 className="break-words text-base font-semibold text-slate-900 dark:text-white">
          {connection.displayName}
        </h4>
        <p className="text-sm text-slate-600 dark:text-slate-400">
          {kindLabel(connection)}
          {connection.account ? ` · Account ${connection.account}` : ''}
          {connection.installationId ? ` · Installation ${connection.installationId}` : ''}
          {isActive ? '' : ' · Disabled: workflows cannot use it'}
        </p>
        {connection.credentialKind === 'github_app' && (connection.permittedRepositories ?? []).length > 0 ? (
          <p className="text-xs text-slate-500 dark:text-slate-400">
            Installation limited to {(connection.permittedRepositories ?? []).join(', ')}
          </p>
        ) : null}
      </div>

      <section aria-label="Assigned repositories" className="space-y-2">
        <h5 className="text-sm font-semibold text-slate-800 dark:text-slate-100">Assigned repositories</h5>
        {assignments.length === 0 ? (
          <p className="text-sm text-slate-600 dark:text-slate-400">
            No repositories assigned. Workflows cannot use this connection until you assign one.
          </p>
        ) : (
          <ul className="space-y-1">
            {assignments.map((assignment) => (
              <li
                key={assignment.providerRepoId ?? assignment.repository}
                className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-slate-200 px-3 py-2 text-sm dark:border-slate-800"
              >
                <span className="min-w-0 break-words">
                  {assignment.repository}
                  <span className="ml-2 text-xs text-slate-500">
                    {canPublish(assignment.operations) ? 'read and publish' : 'read only'}
                  </span>
                </span>
                {assignment.providerRepoId ? (
                  <button
                    type="button"
                    className={secondaryButton}
                    onClick={() => void unassign(assignment.repository, assignment.providerRepoId as string)}
                  >
                    Remove
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
        )}
        {isActive ? (
          <form aria-label="Assign repository" className="flex flex-col gap-2 sm:flex-row sm:items-end" onSubmit={assign}>
            <label className="flex min-w-0 flex-1 flex-col gap-1 text-sm">
              <span className="font-medium text-slate-700 dark:text-slate-200">Assign repository (owner/repo)</span>
              <input className={inputClass} value={repository} onChange={(e) => setRepository(e.target.value)} required autoComplete="off" />
            </label>
            <label className="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-200">
              <input type="checkbox" checked={assignPublish} onChange={(e) => setAssignPublish(e.target.checked)} />
              Publish
            </label>
            <button type="submit" className={primaryButton} disabled={assigning || !repository.trim()}>
              {assigning ? 'Checking…' : 'Assign'}
            </button>
            {assignmentChanged ? (
              <button type="button" className={secondaryButton} onClick={discardAssignment} disabled={assigning}>
                Cancel assignment
              </button>
            ) : null}
          </form>
        ) : null}
        <ErrorText>{assignError}</ErrorText>
      </section>

      <GithubTokenProbePanel
        connection={{
          id: connection.id,
          displayName: connection.displayName,
          policyRevision: connection.policyRevision,
          credentialRevision: connection.credentialRevision,
          assignmentCount: assignments.length,
          lifecycle: connection.lifecycle,
        }}
        canRunProbe={canRunProbe}
        onNotice={onNotice}
        initialRepo={assignments[0]?.repository ?? ''}
      />

      <form aria-label="Edit connection" className="space-y-3" onSubmit={save}>
        <h5 className="text-sm font-semibold text-slate-800 dark:text-slate-100">Edit connection</h5>
        <label className="flex flex-col gap-1 text-sm">
          <span className="font-medium text-slate-700 dark:text-slate-200">Connection name</span>
          <input className={inputClass} value={name} disabled={saving} onChange={(e) => setName(e.target.value)} required autoComplete="off" />
        </label>
        <label className="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-200">
          <input type="checkbox" checked={publish} disabled={saving} onChange={(e) => setPublish(e.target.checked)} />
          Allow publishing (push branches and open pull requests)
        </label>
        {isPat ? (
          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium text-slate-700 dark:text-slate-200">Replace token (optional)</span>
            <input
              className={inputClass}
              type="password"
              value={token}
              disabled={saving}
              onChange={(e) => setToken(e.target.value)}
              autoComplete="off"
              spellCheck={false}
            />
          </label>
        ) : null}
        <ErrorText>{saveError}</ErrorText>
        <div className="flex flex-wrap gap-2">
          <button type="submit" className={primaryButton} disabled={saving || !changed || !name.trim() || (rotationPending && !token)}>
            {saving ? 'Saving…' : 'Save changes'}
          </button>
          {changed ? (
            <button type="button" className={secondaryButton} onClick={discardEdit} disabled={saving}>
              Cancel changes
            </button>
          ) : null}
          {isActive ? (
            <button type="button" className={secondaryButton} onClick={() => void disable()}>
              Disable connection
            </button>
          ) : null}
        </div>
      </form>
    </div>
  );
}

export default SourceControlConnections;
