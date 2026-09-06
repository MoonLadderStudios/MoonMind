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
import React, { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';

import type { components } from '../generated/openapi';
import { WorkflowTerminalChatActions } from '../features/workflow-native-chat/WorkflowTerminalChatActions';

export type WorkflowChatBinding = components['schemas']['WorkflowChatBinding'];

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

/** Bounded startup deadline for the native iframe to announce readiness. */
export const NATIVE_CHAT_READY_TIMEOUT_MS = 15_000;

export const NATIVE_CHAT_READY_TYPE = 'moonmind.omnigent.chat.ready';
export const NATIVE_CHAT_FATAL_TYPE = 'moonmind.omnigent.chat.fatal';

/**
 * Validate a `postMessage` payload as a readiness signal for `bindingId`.
 *
 * The receiver additionally checks exact origin, iframe window, and mount
 * generation at the call site; this helper owns only the bounded payload shape
 * so it is unit-testable in isolation (MoonLadderStudios/MoonMind#4013
 * AC7/PLAN5). Payloads carry presentation state only — never provider
 * identity, transcript content, or credentials.
 */
export function isNativeChatReadySignal(data: unknown, bindingId: string): boolean {
  if (!data || typeof data !== 'object') return false;
  const record = data as Record<string, unknown>;
  return (
    record['type'] === NATIVE_CHAT_READY_TYPE &&
    record['chatBindingId'] === bindingId
  );
}

/** Return the bounded fatal reason when `data` is a fatal signal, else null. */
export function nativeChatFatalReason(data: unknown, bindingId: string): string | null {
  if (!data || typeof data !== 'object') return null;
  const record = data as Record<string, unknown>;
  if (
    record['type'] !== NATIVE_CHAT_FATAL_TYPE ||
    record['chatBindingId'] !== bindingId
  ) {
    return null;
  }
  const reason = typeof record['reason'] === 'string' ? record['reason'] : 'native_crash';
  return reason.slice(0, 128) || 'native_crash';
}

type NativeChatStatus = 'loading' | 'ready' | 'timeout' | 'fatal';

function hasLiveNativeChat(binding: WorkflowChatBinding | null | undefined): boolean {
  return Boolean(
    binding && binding.chatUrl && binding.state !== 'unavailable',
  );
}

/** Live native chat rendering (hooks-safe: always mounted for the live case). */
function NativeChatLive({
  binding,
  apiBase,
  workflowId,
  terminal,
  children,
}: {
  binding: WorkflowChatBinding;
  apiBase: string;
  workflowId: string;
  terminal: boolean;
  children?: React.ReactNode;
}): React.ReactElement {
  const openUrl = fullPageChatUrl(binding.chatUrl);
  const frameRef = useRef<HTMLIFrameElement | null>(null);
  const generationRef = useRef(0);
  const [status, setStatus] = useState<NativeChatStatus>('loading');
  const [fatalReason, setFatalReason] = useState<string | null>(null);
  // Remount the iframe on bounded retry so a crashed frame is replaced while
  // the mount generation guard keeps stale signals from the old frame out.
  const [retryCount, setRetryCount] = useState(0);

  useEffect(() => {
    generationRef.current += 1;
    const generation = generationRef.current;
    setStatus('loading');
    setFatalReason(null);

    const onMessage = (event: MessageEvent) => {
      // Exact-origin check first: cross-origin frames can never mark chat
      // ready or failed.
      if (event.origin !== window.location.origin) return;
      // The signal must come from the currently mounted iframe window.
      const frameWindow = frameRef.current?.contentWindow;
      if (frameWindow && event.source !== null && event.source !== frameWindow) {
        return;
      }
      // Stale signals from a replaced binding/mount are ignored.
      if (generationRef.current !== generation) return;
      if (isNativeChatReadySignal(event.data, binding.chatBindingId)) {
        // A late ready still clears a prior timeout: the application rendered.
        setStatus('ready');
        return;
      }
      const fatal = nativeChatFatalReason(event.data, binding.chatBindingId);
      if (fatal !== null) {
        setFatalReason(fatal);
        setStatus('fatal');
      }
    };
    window.addEventListener('message', onMessage);
    const timer = window.setTimeout(() => {
      if (generationRef.current !== generation) return;
      setStatus((current) => (current === 'loading' ? 'timeout' : current));
    }, NATIVE_CHAT_READY_TIMEOUT_MS);
    return () => {
      window.removeEventListener('message', onMessage);
      window.clearTimeout(timer);
    };
    // Re-run only when the authorized binding (or a bounded retry) changes.
  }, [binding.chatBindingId, binding.chatUrl, retryCount]);

  const showRecovery = status === 'timeout' || status === 'fatal';
  const reason = status === 'fatal' ? (fatalReason ?? 'native_crash') : 'native_chat_not_ready';

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
        <WorkflowTerminalChatActions apiBase={apiBase} workflowId={workflowId} />
      ) : null}
      {showRecovery ? (
        <div
          className="td-native-chat-unavailable"
          data-testid={
            status === 'fatal'
              ? 'workflow-native-chat-fatal'
              : 'workflow-native-chat-timeout'
          }
        >
          <p className="small">
            {status === 'fatal'
              ? `Native chat reported a failure: ${reason}.`
              : 'Native chat is taking too long to become ready.'}{' '}
            The conversation transcript below remains available.
          </p>
          <div className="button-group td-native-chat-actions">
            <button
              type="button"
              className="secondary"
              onClick={() => {
                setRetryCount((count) => count + 1);
              }}
              data-testid="workflow-native-chat-retry"
            >
              Retry
            </button>
          </div>
        </div>
      ) : null}
      <iframe
        ref={frameRef}
        key={`${binding.chatBindingId}:${retryCount}`}
        title="Workflow chat"
        src={binding.chatUrl}
        className="td-native-chat-frame"
        data-testid="workflow-native-chat-frame"
      />
      {showRecovery ? children : null}
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
  /** Legacy read-only compatibility projection: shown when no native UI is
   * available, and again below the iframe when a live mount reports a timeout
   * or fatal failure, so recovery never hides terminal evidence (#4013 AC8). */
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
      >
        {children}
      </NativeChatLive>
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
        <WorkflowTerminalChatActions apiBase={apiBase} workflowId={workflowId} />
      ) : null}
      {children}
    </>
  );
}

export default WorkflowChatNative;
