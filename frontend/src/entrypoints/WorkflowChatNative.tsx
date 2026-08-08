/**
 * Native Omnigent Workflow Chat surface (MoonLadderStudios/MoonMind#3638,
 * terminal read-only + continuation MoonLadderStudios/MoonMind#3641).
 *
 * Renders the provider-maintained native Omnigent web application inside the
 * Workflow Detail chat region through the MoonMind-scoped, binding-scoped route
 * (`chatUrl`, e.g. `/omnigent-ui/workflow-chat/{chatBindingId}?embedded=1`)
 * rather than a copied MoonMind chat projection. When no native binding is
 * available it renders its `children` — the legacy read-only compatibility
 * projection — so there is never a second ordinary composer competing with the
 * native UI (docs/UI/WorkflowChatPanel.md §4, §11).
 *
 * For a terminal (read-only) session the native transcript stays inspectable and
 * the context bar links to **View captured evidence** (immutable MoonMind
 * artifacts) and offers **Continue in a new workflow** — an explicit, authorized
 * Workflow action that creates a linked Workflow Execution from pinned source
 * identity and evidence. It never posts a message through the native composer and
 * never routes through `SubmitChatInstruction` (docs/UI/WorkflowChatPanel.md §9,
 * §10).
 *
 * The browser only ever uses the server-generated `chatUrl`/`apiBase`; it never
 * authors an upstream endpoint, provider session id, credential, or source run.
 */
import React from 'react';
import { useQuery } from '@tanstack/react-query';

import type { components } from '../generated/openapi';

export type WorkflowChatBinding = components['schemas']['WorkflowChatBinding'];

/** One authorized MoonMind evidence artifact ref (browser-safe). */
export interface CapturedEvidenceItem {
  label: string;
  kind: string;
  artifactRef: string;
}

export interface CapturedEvidence {
  workflowId: string;
  runId?: string | null;
  available: boolean;
  items: CapturedEvidenceItem[];
  summary?: string | null;
  unavailableReason?: string | null;
}

export interface ContinueInNewWorkflowResult {
  sourceWorkflowId: string;
  sourceRunId: string;
  destinationWorkflowId: string;
  relationshipType: string;
  created: boolean;
}

function joinApiPath(apiBase: string, path: string): string {
  const base = (apiBase || '/api').replace(/\/+$/, '');
  const suffix = path.startsWith('/') ? path : `/${path}`;
  return `${base}${suffix}`;
}

export async function fetchWorkflowChatBinding(
  apiBase: string,
  workflowId: string,
): Promise<WorkflowChatBinding | null> {
  const resp = await fetch(
    joinApiPath(apiBase, `/executions/${encodeURIComponent(workflowId)}/chat-binding`),
    { credentials: 'include' },
  );
  if (!resp.ok) {
    if (resp.status === 404) return null;
    throw new Error(`workflow chat binding request failed (${resp.status})`);
  }
  return (await resp.json()) as WorkflowChatBinding;
}

/**
 * Turn a MoonMind evidence ref into a download href on the MoonMind origin.
 *
 * Mirrors the existing artifact download contract (`/artifacts/{id}/download`).
 * Only MoonMind artifact refs and full same-origin URLs resolve; a provider
 * session id or upstream path never becomes a durable link.
 */
export function capturedEvidenceHref(
  apiBase: string,
  artifactRef: string,
): string | null {
  const ref = (artifactRef || '').trim();
  if (!ref) return null;
  if (/^https?:\/\//i.test(ref)) return ref;
  const id = ref.startsWith('artifact://') ? ref.slice('artifact://'.length) : ref;
  if (!id) return null;
  return joinApiPath(apiBase, `/artifacts/${encodeURIComponent(id)}/download`);
}

export async function fetchCapturedEvidence(
  apiBase: string,
  workflowId: string,
): Promise<CapturedEvidence> {
  const resp = await fetch(
    joinApiPath(
      apiBase,
      `/executions/${encodeURIComponent(workflowId)}/captured-evidence`,
    ),
    { credentials: 'include' },
  );
  if (!resp.ok) {
    throw new Error(`captured evidence request failed (${resp.status})`);
  }
  return (await resp.json()) as CapturedEvidence;
}

/**
 * Create a linked continuation Workflow from a terminal source.
 *
 * The server pins the source run, Step lineage, and authorized evidence refs; the
 * browser only carries a stable idempotency key so a double-click resolves to the
 * same linked Workflow rather than creating two.
 */
export async function continueInNewWorkflow(
  apiBase: string,
  workflowId: string,
  idempotencyKey: string,
): Promise<ContinueInNewWorkflowResult> {
  const resp = await fetch(
    joinApiPath(apiBase, `/executions/${encodeURIComponent(workflowId)}/continue`),
    {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ idempotencyKey }),
    },
  );
  if (!resp.ok) {
    throw new Error(`continue request failed (${resp.status})`);
  }
  return (await resp.json()) as ContinueInNewWorkflowResult;
}

/** Canonical Workflow Detail route for a continuation destination. */
export function workflowDetailHref(workflowId: string): string {
  return `/workflows/${encodeURIComponent(workflowId)}?source=temporal`;
}

/**
 * Return the authorized full-page **Open in Omnigent** URL for a `chatUrl`.
 *
 * The full-page surface uses the same MoonMind-scoped binding — it drops only
 * the `embedded=1` presentation flag. It never navigates directly to the
 * upstream Omnigent server, so no second login is required and no provider id is
 * exposed (docs/UI/WorkflowChatPanel.md §4).
 */
export function fullPageChatUrl(chatUrl: string): string {
  if (!chatUrl) return chatUrl;
  const questionIndex = chatUrl.indexOf('?');
  if (questionIndex === -1) return chatUrl;
  const path = chatUrl.slice(0, questionIndex);
  const params = new URLSearchParams(chatUrl.slice(questionIndex + 1));
  params.delete('embedded');
  const rest = params.toString();
  return rest ? `${path}?${rest}` : path;
}

function hasLiveNativeChat(binding: WorkflowChatBinding | null | undefined): boolean {
  return Boolean(
    binding && binding.chatUrl && binding.state !== 'unavailable',
  );
}

function newIdempotencyKey(): string {
  const cryptoObj =
    typeof globalThis !== 'undefined'
      ? (globalThis.crypto as Crypto | undefined)
      : undefined;
  if (cryptoObj && typeof cryptoObj.randomUUID === 'function') {
    return `continue:${cryptoObj.randomUUID()}`;
  }
  return `continue:${Date.now()}:${Math.random().toString(16).slice(2)}`;
}

/**
 * The captured-evidence panel rendered below the terminal context bar.
 * Lists authorized MoonMind artifact refs as download links reusing the existing
 * artifact download contract.
 */
function CapturedEvidencePanel({
  apiBase,
  workflowId,
}: {
  apiBase: string;
  workflowId: string;
}): React.ReactElement {
  const query = useQuery({
    queryKey: ['workflow-captured-evidence', workflowId],
    queryFn: () => fetchCapturedEvidence(apiBase, workflowId),
    enabled: Boolean(workflowId),
    staleTime: 60_000,
    retry: false,
  });
  const evidence = query.data ?? null;

  return (
    <div
      className="td-native-chat-evidence"
      data-testid="workflow-native-chat-evidence"
    >
      {query.isLoading ? (
        <p className="small">Loading captured evidence…</p>
      ) : query.isError ? (
        <p className="small" role="alert">
          Captured evidence is unavailable.
        </p>
      ) : evidence && evidence.available && evidence.items.length > 0 ? (
        <ul className="td-native-chat-evidence-list">
          {evidence.items.map((item, index) => {
            const href = capturedEvidenceHref(apiBase, item.artifactRef);
            return (
              <li key={`${item.kind}-${index}`}>
                {href ? (
                  <a
                    href={href}
                    target="_blank"
                    rel="noopener noreferrer"
                    data-testid="workflow-native-chat-evidence-link"
                  >
                    {item.label}
                  </a>
                ) : (
                  <span>{item.label}</span>
                )}
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="small">
          {evidence?.unavailableReason
            ? `No captured evidence: ${evidence.unavailableReason}`
            : 'No captured evidence is available for this workflow.'}
        </p>
      )}
    </div>
  );
}

/** Live native chat rendering (hooks-safe: always mounted for the live case). */
function NativeChatLive({
  binding,
  apiBase,
  workflowId,
}: {
  binding: WorkflowChatBinding;
  apiBase: string;
  workflowId: string;
}): React.ReactElement {
  const openUrl = fullPageChatUrl(binding.chatUrl);
  const terminal = Boolean(binding.readOnly);
  const [evidenceOpen, setEvidenceOpen] = React.useState(false);
  const [continueState, setContinueState] = React.useState<
    'idle' | 'pending' | 'error'
  >('idle');
  // Stable per-mount idempotency key: a double-click resolves to the same
  // linked Workflow instead of creating two.
  const idempotencyKey = React.useRef<string>('');
  if (!idempotencyKey.current) {
    idempotencyKey.current = newIdempotencyKey();
  }

  const onContinue = React.useCallback(async () => {
    if (continueState === 'pending') return;
    setContinueState('pending');
    try {
      const result = await continueInNewWorkflow(
        apiBase,
        workflowId,
        idempotencyKey.current,
      );
      window.location.assign(workflowDetailHref(result.destinationWorkflowId));
    } catch {
      setContinueState('error');
    }
  }, [apiBase, workflowId, continueState]);

  return (
    <div className="stack td-native-chat" data-testid="workflow-native-chat">
      <div className="td-native-chat-actions">
        {terminal ? (
          <span className="small td-native-chat-readonly">
            This session is read-only.
          </span>
        ) : null}
        {terminal ? (
          <button
            type="button"
            className="button secondary"
            aria-expanded={evidenceOpen}
            onClick={() => setEvidenceOpen((open) => !open)}
            data-testid="workflow-native-chat-evidence-toggle"
          >
            View captured evidence
          </button>
        ) : null}
        {terminal ? (
          <button
            type="button"
            className="button secondary"
            onClick={onContinue}
            disabled={continueState === 'pending'}
            data-testid="workflow-native-chat-continue"
          >
            {continueState === 'pending'
              ? 'Continuing…'
              : 'Continue in a new workflow'}
          </button>
        ) : null}
        <a
          className="button secondary"
          href={openUrl}
          target="_blank"
          rel="noopener noreferrer"
          data-testid="workflow-native-chat-open"
        >
          Open in Omnigent
        </a>
      </div>
      {terminal && continueState === 'error' ? (
        <p
          className="small td-native-chat-continue-error"
          role="alert"
          data-testid="workflow-native-chat-continue-error"
        >
          Could not start a linked workflow. Please try again.
        </p>
      ) : null}
      {terminal && evidenceOpen ? (
        <CapturedEvidencePanel apiBase={apiBase} workflowId={workflowId} />
      ) : null}
      <iframe
        title="Workflow chat"
        src={binding.chatUrl}
        className="td-native-chat-frame"
        data-testid="workflow-native-chat-frame"
      />
    </div>
  );
}

export interface WorkflowChatNativeProps {
  apiBase: string;
  workflowId: string;
  /** Only fetch/render while the Chat tab is active. */
  active: boolean;
  /** Legacy read-only compatibility projection, shown only when no native UI. */
  children?: React.ReactNode;
}

export function WorkflowChatNative({
  apiBase,
  workflowId,
  active,
  children,
}: WorkflowChatNativeProps): React.ReactElement | null {
  const query = useQuery({
    queryKey: ['workflow-chat-binding', workflowId],
    queryFn: () => fetchWorkflowChatBinding(apiBase, workflowId),
    enabled: active && Boolean(workflowId),
    staleTime: 15_000,
    retry: false,
  });

  const binding = query.data ?? null;

  // While the binding is still resolving (and nothing cached yet), show the
  // legacy projection to avoid a blank flash.
  if (query.isLoading && !binding) {
    return <>{children}</>;
  }

  if (hasLiveNativeChat(binding) && binding) {
    return (
      <NativeChatLive
        binding={binding}
        apiBase={apiBase}
        workflowId={workflowId}
      />
    );
  }

  // Native UI is unavailable for this workflow: surface the stable reason and an
  // authorized full-page escape hatch when one exists, then fall back to the
  // legacy read-only compatibility projection (docs/UI/WorkflowChatPanel.md §11).
  const unavailableReason = binding?.unavailableReason;
  const escapeHatch =
    binding && binding.chatUrl ? fullPageChatUrl(binding.chatUrl) : null;
  return (
    <>
      {unavailableReason ? (
        <div className="td-native-chat-unavailable" data-testid="workflow-native-chat-unavailable">
          <p className="small">Native chat is unavailable: {unavailableReason}</p>
          {escapeHatch ? (
            <a
              className="button secondary"
              href={escapeHatch}
              target="_blank"
              rel="noopener noreferrer"
            >
              Open in Omnigent
            </a>
          ) : null}
        </div>
      ) : null}
      {children}
    </>
  );
}

export default WorkflowChatNative;
