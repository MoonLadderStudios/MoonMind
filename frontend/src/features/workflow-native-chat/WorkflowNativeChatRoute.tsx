// Canonical Workflow Detail native Chat route (MoonLadderStudios/MoonMind#3639).
//
// Renders a thin MoonMind context bar over the native Omnigent application for
// the authoritative WorkflowChatBinding. MoonMind owns navigation, workflow
// context, and loading/error/unsupported states; the native application owns the
// transcript, composer, queue, approvals, tools, file rail, terminals, and
// session lifecycle. Ordinary native messages never invoke Workflow actions and
// this route never calls /chat-instructions.
import { useEffect, useMemo, useRef, useState } from 'react';

import {
  SESSION_DIAGNOSTICS_ANCHOR,
  workflowDetailSubrouteFromPath,
  workflowDetailSubrouteHref,
  type WorkflowDetailSubroute,
} from '../../lib/workflowDetailRoutes';
import {
  deriveNativeChatStatus,
  fullPageChatHref,
  isMountableNativeChatStatus,
  nativeChatContextStatusLabel,
  nativeChatStepLabel,
  resolveSameOriginChatUrl,
  type NativeChatStepRef,
} from './chatBindingModel';
import { NativeChatFrame, type NativeChatFrameSignal } from './NativeChatFrame';
import { NativeChatUnavailableState } from './NativeChatUnavailableState';
import { WorkflowChatContextBar } from './WorkflowChatContextBar';
import { WorkflowTerminalChatActions } from './WorkflowTerminalChatActions';
import { useWorkflowChatBinding } from './useWorkflowChatBinding';

interface WorkflowNativeChatRouteProps {
  apiBase: string;
  /** Canonical workflow id used to resolve the binding. */
  workflowId: string;
  /** Workflow id used to build MoonMind navigation hrefs. */
  routeWorkflowId: string;
  search: URLSearchParams;
  workflowTitle: string;
  /** Step ledger rows used to name the bound step; null until it loads. */
  steps?: readonly NativeChatStepRef[] | null;
  /** Called once when the loaded ledger does not list the bound step yet. */
  onUnknownStep?: (logicalStepId: string) => void;
  enabled?: boolean;
  /** Whether the parent workflow has reached a terminal state. */
  workflowTerminal?: boolean;
  pollIntervalMs?: number;
  onNavigate: (subroute: WorkflowDetailSubroute, href: string) => void;
}

export function WorkflowNativeChatRoute({
  apiBase,
  workflowId,
  routeWorkflowId,
  search,
  workflowTitle,
  steps = null,
  onUnknownStep,
  enabled = true,
  workflowTerminal = false,
  pollIntervalMs,
  onNavigate,
}: WorkflowNativeChatRouteProps) {
  const query = useWorkflowChatBinding({
    apiBase,
    workflowId,
    enabled,
    workflowTerminal,
    ...(pollIntervalMs != null ? { pollIntervalMs } : {}),
  });
  const binding = query.data ?? null;
  const errorStatus = query.error ? query.error.status : null;

  const serverStatus = deriveNativeChatStatus({
    isLoading: query.isLoading,
    binding,
    errorStatus,
  });

  // Client-detected native-application failures (compatibility / disconnect)
  // override an otherwise-mountable server status so the fallback is explicit.
  const [frameSignal, setFrameSignal] = useState<NativeChatFrameSignal | null>(null);
  const [frameFailure, setFrameFailure] = useState<string | null>(null);
  const chatUrl = binding?.chatUrl ?? null;
  const bindingState = binding?.state ?? null;
  useEffect(() => {
    setFrameSignal(null);
    setFrameFailure(null);
  }, [chatUrl, bindingState]);

  const status = useMemo(() => {
    if (
      isMountableNativeChatStatus(serverStatus) &&
      frameSignal &&
      frameSignal !== 'ready'
    ) {
      return frameSignal === 'disconnected' ? 'disconnected' : 'incompatible';
    }
    return serverStatus;
  }, [serverStatus, frameSignal]);

  const origin = window.location.origin;
  const iframeSrc = resolveSameOriginChatUrl(binding?.chatUrl, origin);
  const fullHref = fullPageChatHref(binding?.chatUrl, origin);

  const evidenceHref = workflowDetailSubrouteHref(routeWorkflowId, 'evidence', search);
  const diagnosticsHref = `${workflowDetailSubrouteHref(routeWorkflowId, 'execution', search)}#${SESSION_DIAGNOSTICS_ANCHOR}`;

  const navigate = (href: string) => {
    let pathname: string;
    try {
      pathname = new URL(href, origin).pathname;
    } catch {
      pathname = href;
    }
    onNavigate(workflowDetailSubrouteFromPath(pathname), href);
  };

  const mountFrame = isMountableNativeChatStatus(status) && Boolean(iframeSrc);
  const readOnly =
    Boolean(binding?.readOnly) || status === 'readOnly' || status === 'terminal';
  const { label: stepLabel, known: stepKnown } = nativeChatStepLabel(binding?.logicalStepId, steps);
  const unlistedStepId = binding?.logicalStepId && steps && !stepKnown ? binding.logicalStepId : null;
  const onUnknownStepRef = useRef(onUnknownStep);
  useEffect(() => {
    onUnknownStepRef.current = onUnknownStep;
  }, [onUnknownStep]);
  useEffect(() => {
    if (unlistedStepId) {
      onUnknownStepRef.current?.(unlistedStepId);
    }
  }, [unlistedStepId]);

  return (
    <section className="stack wf-native-chat" aria-label="Workflow chat">
      <WorkflowChatContextBar
        stepLabel={stepLabel}
        statusLabel={nativeChatContextStatusLabel(status)}
        fullPageHref={fullHref}
      />
      {workflowTerminal ? (
        <WorkflowTerminalChatActions apiBase={apiBase} workflowId={workflowId} />
      ) : null}
      <div className="wf-native-chat__surface">
        {mountFrame && errorStatus != null ? (
          <p className="small" role="status">Unable to refresh chat status. Reconnecting…</p>
        ) : null}
        {mountFrame && iframeSrc ? (
          <NativeChatFrame
            // The state is part of the identity: a session that ends while
            // mounted reloads into its captured final transcript instead of
            // leaving the live view frozen on its last streamed state.
            key={`${binding?.chatBindingId}:${iframeSrc}:${bindingState}`}
            chatBindingId={binding?.chatBindingId ?? ''}
            src={iframeSrc}
            title={`${workflowTitle} — Omnigent chat`}
            readOnly={readOnly}
            onSignal={setFrameSignal}
            onFailure={setFrameFailure}
          />
        ) : (
          <NativeChatUnavailableState
            status={status}
            unavailableReason={frameFailure ?? binding?.unavailableReason ?? null}
            errorStatus={errorStatus}
            onRetry={() => {
              setFrameSignal(null);
              setFrameFailure(null);
              void query.refetch();
            }}
            fullPageHref={fullHref}
            evidenceHref={evidenceHref}
            diagnosticsHref={diagnosticsHref}
            onNavigate={navigate}
          />
        )}
      </div>
    </section>
  );
}
