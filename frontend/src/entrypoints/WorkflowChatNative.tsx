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
 * Routes through the workflow-scoped captured-evidence download endpoint, which
 * authorizes the caller against the Workflow and reads the ref through the same
 * scheme-aware boundary the runtime uses. This is required because production
 * Omnigent evidence refs are gateway refs (`artifact://omnigent/<corr>/<name>`)
 * that the generic `/artifacts/{id}/download` route cannot serve (its nested
 * path never routes and it has no matching `TemporalArtifact`). The ref is
 * carried as a query parameter so its scheme and slashes survive intact. A full
 * same-origin URL passes through unchanged; a provider session id or upstream
 * path never becomes a durable link.
 */
export function capturedEvidenceHref(
  apiBase: string,
  workflowId: string,
  artifactRef: string,
): string | null {
  const ref = (artifactRef || '').trim();
  if (!ref) return null;
  if (/^https?:\/\//i.test(ref)) return ref;
  if (!workflowId) return null;
  const base = joinApiPath(
    apiBase,
    `/executions/${encodeURIComponent(workflowId)}/captured-evidence/download`,
  );
  return `${base}?ref=${encodeURIComponent(ref)}`;
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

/** Authored continuation intent collected before launching a new workflow. */
export interface ContinuationIntent {
  idempotencyKey: string;
  /** New instructions for the continuation (required — never an empty rerun). */
  instructions: string;
  title?: string;
}

/**
 * Create a linked continuation Workflow from a terminal source.
 *
 * The server pins the source run, Step lineage, and authorized evidence refs; the
 * browser authors the new intent (title + instructions) and carries a stable
 * idempotency key so a double-click resolves to the same linked Workflow rather
 * than creating two. New instructions are required so the continuation is an
 * authored follow-up rather than an accidental rerun of the source's old intent.
 */
export async function continueInNewWorkflow(
  apiBase: string,
  workflowId: string,
  intent: ContinuationIntent,
): Promise<ContinueInNewWorkflowResult> {
  const body: Record<string, unknown> = {
    idempotencyKey: intent.idempotencyKey,
    instructions: intent.instructions,
  };
  if (intent.title) body.title = intent.title;
  const resp = await fetch(
    joinApiPath(apiBase, `/executions/${encodeURIComponent(workflowId)}/continue`),
    {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
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
            const href = capturedEvidenceHref(apiBase, workflowId, item.artifactRef);
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

/**
 * Workflow-level terminal actions — **View captured evidence** and **Continue in
 * a new workflow**.
 *
 * These are governed by the Workflow Execution being terminal (the authoritative
 * execution status passed from the parent), not by the chat binding's write
 * capability or native-iframe availability. A binding can be read-only while its
 * workflow is still live, and a terminal workflow's native UI can be unavailable;
 * in both cases these workflow-scoped actions must reflect terminality alone, so
 * this component is rendered both inside the live read-only chat and in the
 * compatibility fallback (#3641 §9, §10).
 *
 * Continuing requires the operator to author new intent (instructions) first, so
 * the linked Workflow is a deliberate follow-up rather than an accidental rerun
 * of the source's old instructions.
 */
export function TerminalWorkflowActions({
  apiBase,
  workflowId,
}: {
  apiBase: string;
  workflowId: string;
}): React.ReactElement {
  const [evidenceOpen, setEvidenceOpen] = React.useState(false);
  const [formOpen, setFormOpen] = React.useState(false);
  const [title, setTitle] = React.useState('');
  const [instructions, setInstructions] = React.useState('');
  const [continueState, setContinueState] = React.useState<
    'idle' | 'pending' | 'error'
  >('idle');
  // Stable per-mount idempotency key: a double-submit resolves to the same
  // linked Workflow instead of creating two.
  const idempotencyKey = React.useRef<string>('');
  if (!idempotencyKey.current) {
    idempotencyKey.current = newIdempotencyKey();
  }

  const canSubmit =
    instructions.trim().length > 0 && continueState !== 'pending';

  const onSubmit = React.useCallback(
    async (event: React.FormEvent) => {
      event.preventDefault();
      if (instructions.trim().length === 0 || continueState === 'pending') {
        return;
      }
      setContinueState('pending');
      try {
        const intent: ContinuationIntent = {
          idempotencyKey: idempotencyKey.current,
          instructions: instructions.trim(),
        };
        const trimmedTitle = title.trim();
        if (trimmedTitle) intent.title = trimmedTitle;
        const result = await continueInNewWorkflow(apiBase, workflowId, intent);
        window.location.assign(workflowDetailHref(result.destinationWorkflowId));
      } catch {
        setContinueState('error');
      }
    },
    [apiBase, workflowId, instructions, title, continueState],
  );

  return (
    <div className="stack td-native-chat-terminal-actions">
      <div className="td-native-chat-actions">
        <button
          type="button"
          className="button secondary"
          aria-expanded={evidenceOpen}
          onClick={() => setEvidenceOpen((open) => !open)}
          data-testid="workflow-native-chat-evidence-toggle"
        >
          View captured evidence
        </button>
        <button
          type="button"
          className="button secondary"
          aria-expanded={formOpen}
          onClick={() => setFormOpen((open) => !open)}
          data-testid="workflow-native-chat-continue"
        >
          Continue in a new workflow
        </button>
      </div>
      {formOpen ? (
        <form
          className="stack td-native-chat-continue-form"
          onSubmit={onSubmit}
          data-testid="workflow-native-chat-continue-form"
        >
          <label className="field">
            <span className="small">Title (optional)</span>
            <input
              type="text"
              value={title}
              maxLength={500}
              onChange={(event) => setTitle(event.target.value)}
              data-testid="workflow-native-chat-continue-title"
            />
          </label>
          <label className="field">
            <span className="small">New instructions</span>
            <textarea
              value={instructions}
              required
              rows={3}
              onChange={(event) => setInstructions(event.target.value)}
              placeholder="Describe what the continuation workflow should do."
              data-testid="workflow-native-chat-continue-instructions"
            />
          </label>
          <button
            type="submit"
            className="button"
            disabled={!canSubmit}
            data-testid="workflow-native-chat-continue-submit"
          >
            {continueState === 'pending' ? 'Continuing…' : 'Start continuation'}
          </button>
        </form>
      ) : null}
      {continueState === 'error' ? (
        <p
          className="small td-native-chat-continue-error"
          role="alert"
          data-testid="workflow-native-chat-continue-error"
        >
          Could not start a linked workflow. Please try again.
        </p>
      ) : null}
      {evidenceOpen ? (
        <CapturedEvidencePanel apiBase={apiBase} workflowId={workflowId} />
      ) : null}
    </div>
  );
}

/** Live native chat rendering (hooks-safe: always mounted for the live case). */
function NativeChatLive({
  binding,
  apiBase,
  workflowId,
  terminal,
}: {
  binding: WorkflowChatBinding;
  apiBase: string;
  workflowId: string;
  terminal: boolean;
}): React.ReactElement {
  const openUrl = fullPageChatUrl(binding.chatUrl);

  return (
    <div className="stack td-native-chat" data-testid="workflow-native-chat">
      <div className="td-native-chat-actions">
        {terminal ? (
          <span className="small td-native-chat-readonly">
            This session is read-only.
          </span>
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
      {terminal ? (
        <TerminalWorkflowActions apiBase={apiBase} workflowId={workflowId} />
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
  /**
   * Whether the Workflow Execution is terminal, from the authoritative execution
   * status. Gates the terminal workflow actions (captured evidence + continue)
   * independently of the chat binding's write capability or native-iframe
   * availability, so a terminal workflow always exposes them (#3641 §9, §10).
   */
  terminal?: boolean;
  /** Legacy read-only compatibility projection, shown only when no native UI. */
  children?: React.ReactNode;
}

export function WorkflowChatNative({
  apiBase,
  workflowId,
  active,
  terminal = false,
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
        terminal={terminal}
      />
    );
  }

  // Native UI is unavailable for this workflow: surface the stable reason, a
  // Retry when the condition is retryable, and an authorized full-page escape
  // hatch when one exists, then fall back to the read-only diagnostic and
  // compatibility projection (docs/UI/WorkflowChatPanel.md §11,
  // MoonLadderStudios/MoonMind#3640).
  //
  // The fallback never becomes a behaviorally different interactive chat: the
  // `children` projection is read-only, so native failure does not silently swap
  // in a second composer. A terminal workflow still exposes its workflow-level
  // actions here — the captured-evidence and continuation controls must not
  // disappear just because the native iframe is unavailable (#3641 §10).
  const unavailableReason = query.isError
    ? 'native_chat_binding_unreachable'
    : binding?.unavailableReason
      ?? (binding?.state === 'starting' ? 'native_chat_session_starting' : undefined);
  const retryable =
    query.isError || binding?.state === 'starting' || binding?.state === 'unavailable';
  const escapeHatch =
    binding && binding.chatUrl ? fullPageChatUrl(binding.chatUrl) : null;
  return (
    <>
      {unavailableReason || retryable ? (
        <div className="td-native-chat-unavailable" data-testid="workflow-native-chat-unavailable">
          <p className="small">
            Native chat is unavailable{unavailableReason ? `: ${unavailableReason}` : '.'}
          </p>
          <div className="button-group td-native-chat-actions">
            {retryable ? (
              <button
                type="button"
                className="secondary"
                onClick={() => {
                  void query.refetch();
                }}
                data-testid="workflow-native-chat-retry"
              >
                Retry
              </button>
            ) : null}
            {escapeHatch ? (
              <a
                className="button secondary"
                href={escapeHatch}
                target="_blank"
                rel="noopener noreferrer"
                data-testid="workflow-native-chat-open"
              >
                Open in Omnigent
              </a>
            ) : null}
          </div>
        </div>
      ) : null}
      {terminal ? (
        <TerminalWorkflowActions apiBase={apiBase} workflowId={workflowId} />
      ) : null}
      {children}
    </>
  );
}

export default WorkflowChatNative;
