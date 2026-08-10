// Canonical Workflow Detail native Chat route (MoonLadderStudios/MoonMind#3639).
//
// Renders a thin MoonMind context bar over the native Omnigent application for
// the authoritative WorkflowChatBinding. MoonMind owns navigation, workflow
// context, and loading/error/unsupported states; the native application owns the
// transcript, composer, queue, approvals, tools, file rail, terminals, and
// session lifecycle. Ordinary native messages never invoke Workflow actions and
// this route never calls /chat-instructions.
import { useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';

import {
  workflowDetailSubrouteFromPath,
  workflowDetailSubrouteHref,
  type WorkflowDetailSubroute,
} from '../../lib/workflowDetailRoutes';
import { TerminalWorkflowActions } from '../../entrypoints/WorkflowChatNative';
import {
  deriveNativeChatStatus,
  fullPageChatHref,
  isMountableNativeChatStatus,
  nativeChatContextStatusLabel,
  resolveSameOriginChatUrl,
} from './chatBindingModel';
import { NativeChatFrame, type NativeChatFrameSignal } from './NativeChatFrame';
import { NativeChatUnavailableState } from './NativeChatUnavailableState';
import { WorkflowChatContextBar } from './WorkflowChatContextBar';
import { useWorkflowChatBinding } from './useWorkflowChatBinding';

interface WorkflowNativeChatRouteProps {
  apiBase: string;
  /** Canonical workflow id used to resolve the binding. */
  workflowId: string;
  /** Workflow id used to build MoonMind navigation hrefs. */
  routeWorkflowId: string;
  search: URLSearchParams;
  workflowTitle: string;
  statusPill?: ReactNode;
  runtimeLabel?: string | null;
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
  statusPill = null,
  runtimeLabel = null,
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
  const chatUrl = binding?.chatUrl ?? null;
  useEffect(() => {
    setFrameSignal(null);
  }, [chatUrl]);

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

  const overviewHref = workflowDetailSubrouteHref(routeWorkflowId, 'overview', search);
  const evidenceHref = workflowDetailSubrouteHref(routeWorkflowId, 'evidence', search);
  const debugHref = workflowDetailSubrouteHref(routeWorkflowId, 'debug', search);

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
  const stepLabel = binding?.logicalStepId ? `Step ${binding.logicalStepId}` : null;

  return (
    <section className="stack wf-native-chat" aria-label="Workflow chat">
      <WorkflowChatContextBar
        workflowTitle={workflowTitle}
        statusPill={statusPill}
        stepLabel={stepLabel}
        runtimeLabel={runtimeLabel}
        statusLabel={nativeChatContextStatusLabel(status)}
        overviewHref={overviewHref}
        evidenceHref={evidenceHref}
        fullPageHref={fullHref}
        onNavigate={navigate}
      />
      {workflowTerminal ? (
        <TerminalWorkflowActions apiBase={apiBase} workflowId={workflowId} />
      ) : null}
      <div className="wf-native-chat__surface">
        {mountFrame && iframeSrc ? (
          <NativeChatFrame
            src={iframeSrc}
            title={`${workflowTitle} — Omnigent chat`}
            readOnly={readOnly}
            onSignal={setFrameSignal}
          />
        ) : (
          <NativeChatUnavailableState
            status={status}
            unavailableReason={binding?.unavailableReason ?? null}
            onRetry={() => {
              setFrameSignal(null);
              void query.refetch();
            }}
            fullPageHref={fullHref}
            evidenceHref={evidenceHref}
            diagnosticsHref={debugHref}
            onNavigate={navigate}
          />
        )}
      </div>
    </section>
  );
}
