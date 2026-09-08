/**
 * Diagnostics fallback shell for the Workflow Detail Debug tab
 * (MoonLadderStudios/MoonMind#3638 compatibility surface, #3641 terminal
 * actions, simplified MoonLadderStudios/MoonMind#3956).
 *
 * The single interactive chat application is the provider-maintained native
 * Omnigent UI mounted by `WorkflowNativeChatRoute` (Chat tab) through the
 * server-issued, binding-scoped `chatUrl`. This module owns no iframe, no
 * binding fetch, no readiness lifecycle, and no composer: it renders the
 * read-only diagnostic `children` projection plus the terminal workflow actions
 * (captured evidence + explicit linked continuation), which must stay visible
 * after host cleanup without recreating a session.
 *
 * Canonical live behavior (same-origin URL guards, readiness/timeout, retry,
 * unavailable states) lives in
 * `features/workflow-native-chat/` (`chatBindingModel`, `NativeChatFrame`,
 * `NativeChatUnavailableState`, `useWorkflowChatBinding`). Do not reintroduce a
 * second fetch/postMessage/iframe path here.
 */
import React from 'react';

import { WorkflowTerminalChatActions } from '../features/workflow-native-chat/WorkflowTerminalChatActions';

export interface WorkflowChatNativeProps {
  apiBase: string;
  workflowId: string;
  /**
   * Whether the Workflow Execution is terminal, from the authoritative execution
   * status. Gates the terminal workflow actions (captured evidence + continue)
   * so a terminal workflow always exposes them (#3641 §9, §10).
   */
  terminal?: boolean;
  /** Read-only diagnostic/compatibility projection; never a second composer. */
  children?: React.ReactNode;
}

export function WorkflowChatNative({
  apiBase,
  workflowId,
  terminal = false,
  children,
}: WorkflowChatNativeProps): React.ReactElement {
  return (
    <>
      {terminal ? (
        <WorkflowTerminalChatActions apiBase={apiBase} workflowId={workflowId} />
      ) : null}
      {children}
    </>
  );
}

export default WorkflowChatNative;
