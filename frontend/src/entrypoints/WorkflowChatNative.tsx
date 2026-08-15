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
        <WorkflowTerminalChatActions apiBase={apiBase} workflowId={workflowId} />
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
        <WorkflowTerminalChatActions apiBase={apiBase} workflowId={workflowId} />
      ) : null}
      {children}
    </>
  );
}

export default WorkflowChatNative;
