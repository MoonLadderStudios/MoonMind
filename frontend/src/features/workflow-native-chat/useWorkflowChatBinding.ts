// Data hook for the authoritative Workflow Chat binding
// (MoonLadderStudios/MoonMind#3639). Fetches GET
// /api/executions/{workflowId}/chat-binding and exposes the raw binding plus the
// request error. Status derivation lives in `chatBindingModel` so it can be unit
// tested without a query client. The browser never repairs the binding here.
import { useQuery } from '@tanstack/react-query';
import type { UseQueryResult } from '@tanstack/react-query';

import type { WorkflowChatBinding } from './chatBindingModel';

/** Error carrying the HTTP status of a failed chat-binding request. */
export class ChatBindingRequestError extends Error {
  readonly status: number;
  readonly code: string | null;

  constructor(status: number, message: string, code: string | null = null) {
    super(message);
    this.name = 'ChatBindingRequestError';
    this.status = status;
    this.code = code;
  }
}

export function workflowChatBindingQueryKey(workflowId: string) {
  return ['workflow-chat-binding', workflowId] as const;
}

export function chatBindingEndpoint(apiBase: string, workflowId: string): string {
  const base = apiBase.replace(/\/+$/, '');
  return `${base}/executions/${encodeURIComponent(workflowId)}/chat-binding`;
}

/** Default abort deadline for a single chat-binding request. */
export const DEFAULT_CHAT_BINDING_TIMEOUT_MS = 15000;

export async function fetchWorkflowChatBinding(
  apiBase: string,
  workflowId: string,
  requestTimeoutMs: number = DEFAULT_CHAT_BINDING_TIMEOUT_MS,
): Promise<WorkflowChatBinding> {
  // A stalled proxy or hung upstream must not leave the request pending forever:
  // bound it with an abort deadline so React Query resolves to an actionable,
  // retryable error state instead of an indefinite loading spinner.
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), requestTimeoutMs);
  let response: Response;
  try {
    response = await fetch(chatBindingEndpoint(apiBase, workflowId), {
      credentials: 'include',
      headers: { Accept: 'application/json' },
      signal: controller.signal,
    });
  } catch (error) {
    if (controller.signal.aborted) {
      throw new ChatBindingRequestError(
        408,
        'Timed out resolving chat binding',
        'chat_binding_request_timeout',
      );
    }
    throw new ChatBindingRequestError(
      0,
      `Failed to reach chat binding service: ${(error as Error).message}`,
      'chat_binding_request_failed',
    );
  } finally {
    clearTimeout(timer);
  }
  if (!response.ok) {
    let code: string | null = null;
    try {
      const body = (await response.clone().json()) as { detail?: unknown };
      const detail = body?.detail;
      if (typeof detail === 'string') {
        code = detail;
      } else if (
        detail &&
        typeof detail === 'object' &&
        typeof (detail as { code?: unknown }).code === 'string'
      ) {
        code = (detail as { code: string }).code;
      }
    } catch {
      // Non-JSON error body; the status alone drives the presentation status.
    }
    throw new ChatBindingRequestError(
      response.status,
      `Failed to resolve chat binding (${response.status})`,
      code,
    );
  }
  return (await response.json()) as WorkflowChatBinding;
}

export function useWorkflowChatBinding(args: {
  apiBase: string;
  workflowId: string;
  enabled: boolean;
  /**
   * Whether the parent workflow has reached a terminal state. While the workflow
   * is nonterminal a later Step can create a different active chat-capable
   * session (and therefore a new authoritative `chatBindingId`), so the resolver
   * must keep polling to discover the replacement rather than staying attached to
   * a superseded session.
   */
  workflowTerminal?: boolean;
  pollIntervalMs?: number;
  requestTimeoutMs?: number;
}): UseQueryResult<WorkflowChatBinding, ChatBindingRequestError> {
  const {
    apiBase,
    workflowId,
    enabled,
    workflowTerminal = false,
    pollIntervalMs = 5000,
    requestTimeoutMs,
  } = args;
  return useQuery<WorkflowChatBinding, ChatBindingRequestError>({
    queryKey: workflowChatBindingQueryKey(workflowId),
    queryFn: () => fetchWorkflowChatBinding(apiBase, workflowId, requestTimeoutMs),
    enabled: enabled && Boolean(workflowId),
    retry: false,
    // Keep resolving the authoritative binding while the workflow is nonterminal
    // so a new active Step's session is discovered; stop only once the workflow
    // is terminal, when no further chat-capable session can appear.
    refetchInterval: () => (workflowTerminal ? false : pollIntervalMs),
    staleTime: pollIntervalMs,
  });
}
