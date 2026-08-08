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
 * authorizes the caller against the Workflow and dispatches by store: filesystem
 * gateway refs (`artifact://omnigent/<correlation>/<name>`) are served through
 * the Omnigent gateway, while plain MoonMind artifact refs are redirected to the
 * shared `/artifacts/{id}/download` contract. The browser therefore never has to
 * strip a scheme itself — a gateway ref used to 404 against the Temporal-artifact
 * route because its slashes were sent as a single opaque id. A full same-origin
 * URL passes through unchanged; an empty ref never fabricates a link.
 */
export function capturedEvidenceHref(
  apiBase: string,
  workflowId: string,
  artifactRef: string,
): string | null {
  const ref = (artifactRef || '').trim();
  if (!ref) return null;
  if (/^https?:\/\//i.test(ref)) return ref;
  return joinApiPath(
    apiBase,
    `/executions/${encodeURIComponent(workflowId)}/captured-evidence/download` +
      `?ref=${encodeURIComponent(ref)}`,
  );
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

/** Newly authored intent for a linked continuation (never the source's stale intent). */
export interface ContinuationIntent {
  title?: string;
  instructions?: string;
  boundedPurpose?: string;
}

/**
 * Create a linked continuation Workflow from a terminal source.
 *
 * The server pins the source run, Step lineage, and authorized evidence refs; the
 * browser carries a stable idempotency key (so a double-submit resolves to the
 * same linked Workflow rather than creating two) plus the operator's newly
 * authored intent. Launching with only a key would clone the source parameters
 * and rerun the old instructions — an accidental rerun, not a continuation — so
 * the caller must collect new intent first.
 */
export async function continueInNewWorkflow(
  apiBase: string,
  workflowId: string,
  idempotencyKey: string,
  intent: ContinuationIntent = {},
): Promise<ContinueInNewWorkflowResult> {
  const body: Record<string, unknown> = { idempotencyKey };
  const title = intent.title?.trim();
  const instructions = intent.instructions?.trim();
  const boundedPurpose = intent.boundedPurpose?.trim();
  if (title) body.title = title;
  if (instructions) body.instructions = instructions;
  if (boundedPurpose) body.boundedPurpose = boundedPurpose;
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
 * Workflow-level terminal actions: **View captured evidence** and **Continue in
 * a new workflow**.
 *
 * These are properties of a terminal Workflow, not of the live iframe, so they
 * render both inside the live terminal chat surface and in the fallback when the
 * native iframe is unavailable. Continuing collects newly authored intent through
 * a form before POSTing — launching with only an idempotency key would clone the
 * source parameters and rerun the old instructions (an accidental rerun rather
 * than a continuation).
 */
function TerminalWorkflowActions({
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
  const [boundedPurpose, setBoundedPurpose] = React.useState('');
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
        const result = await continueInNewWorkflow(
          apiBase,
          workflowId,
          idempotencyKey.current,
          { title, instructions, boundedPurpose },
        );
        window.location.assign(workflowDetailHref(result.destinationWorkflowId));
      } catch {
        setContinueState('error');
      }
    },
    [apiBase, workflowId, title, instructions, boundedPurpose, continueState],
  );

  return (
    <div
      className="stack td-native-chat-terminal"
      data-testid="workflow-native-chat-terminal"
    >
      <div className="td-native-chat-actions">
        <span className="small td-native-chat-readonly">
          This session is read-only.
        </span>
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
      {evidenceOpen ? (
        <CapturedEvidencePanel apiBase={apiBase} workflowId={workflowId} />
      ) : null}
      {formOpen ? (
        <form
          className="stack td-native-chat-continue-form"
          onSubmit={onSubmit}
          data-testid="workflow-native-chat-continue-form"
        >
          <p className="small">
            Start a new linked workflow from this terminal session. Describe the
            new intent — the linked workflow is created from the pinned source
            evidence, not by replaying the old instructions.
          </p>
          <label className="stack">
            <span className="small">Title (optional)</span>
            <input
              type="text"
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              data-testid="workflow-native-chat-continue-title"
            />
          </label>
          <label className="stack">
            <span className="small">New instructions</span>
            <textarea
              value={instructions}
              onChange={(event) => setInstructions(event.target.value)}
              required
              data-testid="workflow-native-chat-continue-instructions"
            />
          </label>
          <label className="stack">
            <span className="small">Bounded purpose (optional)</span>
            <input
              type="text"
              value={boundedPurpose}
              onChange={(event) => setBoundedPurpose(event.target.value)}
              data-testid="workflow-native-chat-continue-purpose"
            />
          </label>
          <button
            type="submit"
            className="button"
            disabled={!canSubmit}
            data-testid="workflow-native-chat-continue-submit"
          >
            {continueState === 'pending' ? 'Continuing…' : 'Start linked workflow'}
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
  // Terminal (workflow-level) actions are gated on the binding having *ended*,
  // not on read-only posture: a live session can be read-only too (no
  // sendMessage capability), and offering "Continue in a new workflow" there
  // would surface an action the server rejects as not-terminal.
  const terminal = binding.state === 'ended';

  return (
    <div className="stack td-native-chat" data-testid="workflow-native-chat">
      <div className="td-native-chat-actions">
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
   * Whether the parent's authoritative execution status is terminal. Terminal
   * workflow-level actions (captured evidence + continuation) depend on the
   * Workflow being terminal, not on the native iframe being available, so they
   * must still render when the native UI is unavailable.
   */
  isTerminal?: boolean;
  /** Legacy read-only compatibility projection, shown only when no native UI. */
  children?: React.ReactNode;
}

export function WorkflowChatNative({
  apiBase,
  workflowId,
  active,
  isTerminal = false,
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
  // A terminal Workflow whose native iframe is unavailable (native serving
  // disabled or upstream down) is still eligible for the captured-evidence and
  // continuation actions, so render them independently of the iframe when the
  // parent reports a terminal execution and a binding exists.
  const showTerminalActions = Boolean(binding && isTerminal);
  return (
    <>
      {showTerminalActions ? (
        <TerminalWorkflowActions apiBase={apiBase} workflowId={workflowId} />
      ) : null}
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
