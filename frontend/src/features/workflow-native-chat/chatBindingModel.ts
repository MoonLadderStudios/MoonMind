// Browser-safe model + pure presentation helpers for the Workflow Detail native
// Omnigent Chat route (MoonLadderStudios/MoonMind#3639).
//
// This module holds no React and no I/O so the binding-state derivation and the
// URL guards can be unit tested directly. The route renders exclusively from the
// authoritative binding returned by GET /api/executions/{workflowId}/chat-binding;
// it never infers, repairs, or authors an upstream Omnigent URL in the browser.
import type { components } from '../../generated/openapi';

/** Authoritative browser-safe binding (MoonLadderStudios/MoonMind#3633). */
export type WorkflowChatBinding = components['schemas']['WorkflowChatBinding'];

/** Server-owned lifecycle state carried in the binding body. */
export type WorkflowChatBindingServerState = WorkflowChatBinding['state'];

/**
 * Resolved presentation status for the native chat route. This is a superset of
 * the server `state`: it folds in read-only posture, the `unavailableReason`
 * discriminant, transport/auth failures, and client-detected native-application
 * failures so each surface is explicit and independently actionable.
 */
export type NativeChatStatus =
  | 'loading' // binding request in flight
  | 'starting' // binding exists but the provider session is not attached yet
  | 'available' // native session available and writable
  | 'readOnly' // native session available but read-only
  | 'terminal' // terminal session, read-only transcript
  | 'unsupported' // runtime does not serve a native chat session
  | 'missing' // no session bound, or a terminal session was cleaned up / stale
  | 'unauthorized' // caller is not authorized against the workflow
  | 'notFound' // workflow unknown to the caller
  | 'ambiguous' // multiple active chat-capable sessions
  | 'incompatible' // native UI compatibility/version failure (client-detected)
  | 'disconnected' // scoped transport disconnected with durable fallback available
  | 'unavailable'; // upstream/server unavailable

/** Statuses where the native Omnigent application should be mounted. */
export const NATIVE_CHAT_MOUNTED_STATUSES: readonly NativeChatStatus[] = [
  'available',
  'readOnly',
  'terminal',
];

export function isMountableNativeChatStatus(status: NativeChatStatus): boolean {
  return NATIVE_CHAT_MOUNTED_STATUSES.includes(status);
}

/**
 * Map the authoritative binding (or a transport/auth error) to a resolved
 * presentation status. The browser renders from server truth only: a stale,
 * missing, or unauthorized binding fails closed and never falls back to an
 * arbitrary provider session.
 */
export function deriveNativeChatStatus(input: {
  isLoading: boolean;
  binding: WorkflowChatBinding | null | undefined;
  errorStatus: number | null;
}): NativeChatStatus {
  const { isLoading, binding, errorStatus } = input;
  if (errorStatus != null) {
    if (errorStatus === 401 || errorStatus === 403) {
      return 'unauthorized';
    }
    if (errorStatus === 404) {
      return 'notFound';
    }
    if (errorStatus === 409) {
      return 'ambiguous';
    }
    // 5xx, network failures, and unexpected statuses are upstream unavailability.
    return 'unavailable';
  }
  if (!binding) {
    return isLoading ? 'loading' : 'unavailable';
  }
  switch (binding.state) {
    case 'starting':
      return 'starting';
    case 'available':
      return binding.readOnly ? 'readOnly' : 'available';
    case 'ended':
      return 'terminal';
    case 'unavailable':
      return binding.unavailableReason === 'unsupported_runtime'
        ? 'unsupported'
        : 'missing';
    default:
      // Degraded / unknown provider input fails closed rather than mounting.
      return 'unavailable';
  }
}

/**
 * Resolve the server-generated embedded chat URL to a same-origin path safe to
 * use as an iframe `src`. Returns `null` for a blank URL or any URL that does
 * not resolve to the MoonMind origin, so the route can never mount an upstream
 * Omnigent surface even if the server contract regresses.
 */
export function resolveSameOriginChatUrl(
  chatUrl: string | null | undefined,
  origin: string,
): string | null {
  if (!chatUrl) {
    return null;
  }
  let url: URL;
  try {
    url = new URL(chatUrl, origin);
  } catch {
    return null;
  }
  if (url.origin !== origin) {
    return null;
  }
  return `${url.pathname}${url.search}${url.hash}`;
}

/**
 * Derive the MoonMind-scoped full-page "Open in Omnigent" href from the
 * server-generated embedded chat URL. The `embedded` presentational flag is
 * dropped so the full-page surface renders native chrome, but the path stays
 * within the same MoonMind origin — the browser never navigates directly to the
 * upstream server.
 */
export function fullPageChatHref(
  chatUrl: string | null | undefined,
  origin: string,
): string | null {
  if (!chatUrl) {
    return null;
  }
  let url: URL;
  try {
    url = new URL(chatUrl, origin);
  } catch {
    return null;
  }
  if (url.origin !== origin) {
    return null;
  }
  url.searchParams.delete('embedded');
  return `${url.pathname}${url.search}${url.hash}`;
}

export interface NativeChatStateCopy {
  title: string;
  description: string;
  /** `status` for polite/transient states, `alert` for actionable failures. */
  role: 'status' | 'alert';
  canRetry: boolean;
}

/**
 * User-facing copy for every non-mounted status. Kept pure so the wording and
 * the retry/announcement affordance for each state are covered by unit tests.
 */
export function nativeChatStateCopy(
  status: NativeChatStatus,
  unavailableReason?: string | null,
): NativeChatStateCopy {
  switch (status) {
    case 'loading':
      return {
        title: 'Loading conversation',
        description: 'Resolving the workflow chat binding.',
        role: 'status',
        canRetry: false,
      };
    case 'starting':
      return {
        title: 'Starting session',
        description:
          'The native session is starting. This view will open automatically once it is ready.',
        role: 'status',
        canRetry: true,
      };
    case 'unsupported':
      return {
        title: 'Chat unavailable for this runtime',
        description:
          'This workflow runs on a runtime that does not provide a native chat session.',
        role: 'alert',
        canRetry: false,
      };
    case 'missing':
      return {
        title: 'No chat session available',
        description:
          unavailableReason === 'session_cleaned_up'
            ? 'The terminal session transcript has been captured to durable evidence and is no longer live.'
            : 'No native chat session is bound to this workflow yet.',
        role: 'alert',
        canRetry: true,
      };
    case 'unauthorized':
      return {
        title: 'Not authorized',
        description: 'You do not have permission to open chat for this workflow.',
        role: 'alert',
        canRetry: false,
      };
    case 'notFound':
      return {
        title: 'Workflow not found',
        description: 'This workflow could not be found for your account.',
        role: 'alert',
        canRetry: false,
      };
    case 'ambiguous':
      return {
        title: 'Multiple active sessions',
        description:
          'More than one active chat session is bound to this workflow. Resolve the conflict before opening chat.',
        role: 'alert',
        canRetry: true,
      };
    case 'incompatible':
      return {
        title: 'Native chat could not load',
        description:
          'The native chat application failed to load in this browser. Open it in a full page or review captured evidence instead.',
        role: 'alert',
        canRetry: true,
      };
    case 'disconnected':
      return {
        title: 'Chat disconnected',
        description:
          'The live chat connection dropped. Retry the live session or review captured evidence.',
        role: 'alert',
        canRetry: true,
      };
    case 'unavailable':
    default:
      return {
        title: 'Chat is temporarily unavailable',
        description: 'The chat service could not be reached. Try again in a moment.',
        role: 'alert',
        canRetry: true,
      };
  }
}

/** Bounded read-only/unavailable label for the context bar, or null when writable. */
export function nativeChatContextStatusLabel(
  status: NativeChatStatus,
): string | null {
  switch (status) {
    case 'readOnly':
      return 'Read-only';
    case 'terminal':
      return 'Session ended · read-only';
    case 'starting':
      return 'Starting';
    case 'disconnected':
      return 'Disconnected';
    case 'incompatible':
      return 'Unavailable';
    case 'unsupported':
    case 'missing':
    case 'unavailable':
    case 'unauthorized':
    case 'notFound':
    case 'ambiguous':
      return 'Chat unavailable';
    default:
      return null;
  }
}
