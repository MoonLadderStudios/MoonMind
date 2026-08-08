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

export async function fetchWorkflowChatBinding(
  apiBase: string,
  workflowId: string,
): Promise<WorkflowChatBinding> {
  const response = await fetch(chatBindingEndpoint(apiBase, workflowId), {
    credentials: 'include',
    headers: { Accept: 'application/json' },
  });
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
  pollIntervalMs?: number;
}): UseQueryResult<WorkflowChatBinding, ChatBindingRequestError> {
  const { apiBase, workflowId, enabled, pollIntervalMs = 5000 } = args;
  return useQuery<WorkflowChatBinding, ChatBindingRequestError>({
    queryKey: workflowChatBindingQueryKey(workflowId),
    queryFn: () => fetchWorkflowChatBinding(apiBase, workflowId),
    enabled: enabled && Boolean(workflowId),
    retry: false,
    // Only poll while the session is still coming up; a resolved binding is
    // stable and the native application owns its own liveness afterwards.
    refetchInterval: (query) =>
      query.state.data?.state === 'starting' ? pollIntervalMs : false,
    staleTime: pollIntervalMs,
  });
}
