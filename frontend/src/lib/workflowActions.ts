import { responseError } from './api/client';
import { readModelSelection } from "./modelSelection";
import { z } from 'zod';

/**
 * Shared workflow-action contracts used by both the Workflow Detail page and the
 * Workflows list page. The list page exposes the same dropdown options directly
 * from each table row, so the option-building logic, capability schema, and
 * remediation request helpers live here as a single source of truth.
 */

export const ExecutionActionsSchema = z
  .object({
    canSetTitle: z.boolean().optional(),
    canUpdateInputs: z.boolean().optional(),
    canEditForRerun: z.boolean().optional(),
    canRerun: z.boolean().optional(),
    canApprove: z.boolean().optional(),
    canPause: z.boolean().optional(),
    canResume: z.boolean().optional(),
    canRetryFromStep: z.boolean().optional(),
    canContinueRemediation: z.boolean().optional(),
    canRetryPublication: z.boolean().optional(),
    canPublishSavedWork: z.boolean().optional(),
    canFullRetry: z.boolean().optional(),
    actionEvidence: z.record(
      z.string(),
      z.object({
        candidateRef: z.string().nullable().optional(),
        remainingWorkRef: z.string().nullable().optional(),
        controlStopId: z.string().nullable().optional(),
        sourceBudget: z.object({
          maxAttempts: z.number().int().positive(),
          consumedAttempts: z.number().int().nonnegative(),
          exhaustedDimension: z.string(),
        }).passthrough().optional(),
        continuationBudget: z.object({
          grantId: z.string(),
          maxAttempts: z.number().int().positive(),
          maxConsecutiveNoProgressAttempts: z.number().int().positive(),
          consumedAttempts: z.number().int().nonnegative().optional(),
          consecutiveNoProgressAttempts: z.number().int().nonnegative().optional(),
        }).passthrough().optional(),
        destinationWorkflowId: z.string().nullable().optional(),
        restorationEvidenceRef: z.string().nullable().optional(),
        restorationEvidenceDigest: z.string().nullable().optional(),
        hostSessionLifecycle: z.record(z.string(), z.unknown()).nullable().optional(),
      }).passthrough(),
    ).optional(),
    canCancel: z.boolean().optional(),
    canForceCancel: z.boolean().optional(),
    canReject: z.boolean().optional(),
    canSendMessage: z.boolean().optional(),
    canBypassDependencies: z.boolean().optional(),
    disabledReasons: z.record(z.string(), z.string()).optional(),
  })
  .passthrough();

export type ExecutionActionCapabilities = z.infer<typeof ExecutionActionsSchema>;

/**
 * Menu placement, in display order: safe actions, destructive actions after a
 * divider, the Troubleshooting group, then ineligible actions collapsed under
 * "Unavailable (n)". Items without a section are safe.
 */
export const WORKFLOW_ACTION_MENU_SECTIONS = [
  'primary',
  'destructive',
  'troubleshooting',
  'unavailable',
] as const;
export type WorkflowActionMenuSection = (typeof WORKFLOW_ACTION_MENU_SECTIONS)[number];

export type WorkflowActionConfirmation = {
  title: string;
  /** One line stating what happens. */
  consequence: string;
  confirmLabel: string;
};

export type WorkflowActionMenuItem = {
  id: string;
  label: string;
  href?: string;
  danger?: boolean;
  section?: WorkflowActionMenuSection;
  /** One-line explanation shown under the label. */
  description?: string;
  disabledReason?: string | null;
  /** When set, the menu asks first and calls onSelect only after confirmation. */
  confirmation?: WorkflowActionConfirmation;
  onSelect?: () => void;
};

const DISABLED_REASON_SENTENCES: Record<string, string> = {
  state_not_eligible: "Not available in the workflow's current state.",
  already_paused: 'The workflow is already paused.',
  actions_disabled: 'Workflow actions are turned off on this deployment.',
  historical_workflow_type: 'Retired workflow history is read-only.',
  unsupported_workflow_type: 'This workflow type does not support this action.',
  temporal_workflow_editing_disabled: 'Workflow editing is turned off on this deployment.',
  original_task_input_snapshot_missing: "The workflow's original inputs were not saved.",
  temporal_submit_disabled: 'Workflow submission is turned off on this deployment.',
  remediation_not_admitted: 'No remediation was admitted for this failure.',
  control_stop_not_available: 'There is no saved stopping point to continue from.',
  publication_retry_not_applicable: 'This workflow has no publication to retry.',
  publication_not_failed: 'Publication did not fail, so there is nothing to retry.',
  publication_retry_not_eligible: 'The failed publication cannot be retried.',
  child_workflow: 'This run belongs to a parent workflow, which owns its retries.',
};

/**
 * Turn an action's disabled reason into a plain sentence. The API reports
 * snake_case codes; text that is already prose is returned unchanged.
 */
export function describeWorkflowActionDisabledReason(
  reason: string | null | undefined,
): string | null {
  const text = String(reason ?? '').trim();
  if (!text) return null;
  if (/\s/.test(text)) return text;
  const known = DISABLED_REASON_SENTENCES[text.toLowerCase()];
  if (known) return known;
  const words = text.replace(/[_-]+/g, ' ').trim();
  return `${words.charAt(0).toUpperCase()}${words.slice(1)}.`;
}

const CANCEL_CONFIRMATION: WorkflowActionConfirmation = {
  title: 'Cancel workflow?',
  consequence:
    'The workflow stops gracefully, runs its cleanup, and ends as canceled. This cannot be undone.',
  confirmLabel: 'Cancel workflow',
};
const REJECT_CONFIRMATION: WorkflowActionConfirmation = {
  title: 'Reject workflow?',
  consequence:
    'The pending approval is rejected and the workflow ends as canceled. This cannot be undone.',
  confirmLabel: 'Reject workflow',
};
const FORCE_CANCEL_CONFIRMATION: WorkflowActionConfirmation = {
  title: 'Force cancel workflow?',
  consequence: 'The workflow is terminated immediately, without cleanup. This cannot be undone.',
  confirmLabel: 'Force cancel',
};

export type WorkflowActionHandlers = {
  onRename: () => void;
  onEditTask: () => void;
  onCompareRun: () => void;
  onRerun: () => void;
  onRetryFromStep: () => void;
  onContinueRemediation: () => void;
  onRetryPublication: () => void;
  onPause: () => void;
  onResume: () => void;
  onApprove: () => void;
  onReject: () => void;
  onCancel: () => void;
  onForceCancel: () => void;
  onSendMessage: () => void;
  onBypassDependencies: () => void;
  onCreateRemediation: () => void;
};

export type WorkflowActionMenuBuilderParams = {
  actionsOn: boolean;
  actions: ExecutionActionCapabilities | null | undefined;
  busy: boolean;
  /** Display-ready reason for temporarily disabled actions while work is pending. */
  busyDisabledReason?: string;
  taskEditingOn: boolean;
  /** Returns a display-ready disabled reason for a capability key, or null. */
  disabledReason: (key: string) => string | null;
  editHref: string;
  compareHref: string;
  canShowEditWorkflow: boolean;
  /** Display-ready reason for an unavailable Edit task action, or null. */
  editTaskDisabledReason: string | null;
  /** Display-ready reason for an unavailable Rerun action, or null. */
  rerunDisabledReason: string | null;
  canCreateRemediation: boolean;
  /**
   * List Remediate as unavailable, with a reason, even when the target is not
   * eligible, so the row menu explains why remediation is not offered.
   */
  keepRemediationVisible?: boolean;
  handlers: WorkflowActionHandlers;
};

/**
 * Build the ordered list of workflow action options shown in the actions dropdown.
 *
 * This is the single source of truth shared by the Workflow Detail "Workflow
 * actions" menu and the Workflows table row "Actions" menu so both surfaces
 * always present the same options, ordering, confirmations, and disabled
 * semantics. Items come back sorted by section.
 */
export function buildWorkflowActionMenuItems(
  params: WorkflowActionMenuBuilderParams,
): WorkflowActionMenuItem[] {
  const {
    actionsOn,
    actions,
    busy,
    busyDisabledReason = 'action pending',
    taskEditingOn,
    disabledReason,
    editHref,
    compareHref,
    canShowEditWorkflow,
    editTaskDisabledReason,
    rerunDisabledReason,
    canCreateRemediation,
    keepRemediationVisible = false,
    handlers,
  } = params;

  if (!actionsOn || !actions) return [];
  const items: WorkflowActionMenuItem[] = [];
  const pendingActionReason = busy ? busyDisabledReason : null;
  const addButton = ({
    id,
    label,
    available,
    disabledReason: itemDisabledReason,
    danger,
    troubleshooting,
    description,
    confirmation,
    onSelect,
  }: {
    id: string;
    label: string;
    available: boolean;
    disabledReason?: string | null;
    danger?: boolean;
    troubleshooting?: boolean;
    description?: string;
    confirmation?: WorkflowActionConfirmation;
    onSelect: () => void;
  }) => {
    const effectiveDisabledReason = available ? pendingActionReason : itemDisabledReason ?? null;
    if (!available && !effectiveDisabledReason) return;
    const section: WorkflowActionMenuSection = !available
      ? 'unavailable'
      : troubleshooting
        ? 'troubleshooting'
        : danger
          ? 'destructive'
          : 'primary';
    items.push({
      id,
      label,
      section,
      ...(danger ? { danger: true } : {}),
      ...(description ? { description } : {}),
      ...(available ? { onSelect } : {}),
      ...(available && confirmation ? { confirmation } : {}),
      ...(effectiveDisabledReason ? { disabledReason: effectiveDisabledReason } : {}),
    });
  };
  const addLink = ({
    id,
    label,
    href,
    available,
    disabledReason: itemDisabledReason,
    onSelect,
  }: {
    id: string;
    label: string;
    href: string;
    available: boolean;
    disabledReason?: string | null;
    onSelect: () => void;
  }) => {
    const effectiveDisabledReason = available ? pendingActionReason : itemDisabledReason ?? null;
    if (available && href) {
      items.push({
        id,
        label,
        href,
        section: 'primary',
        onSelect,
        ...(effectiveDisabledReason ? { disabledReason: effectiveDisabledReason } : {}),
      });
    } else if (effectiveDisabledReason) {
      items.push({
        id,
        label,
        section: 'unavailable',
        disabledReason: effectiveDisabledReason,
      });
    }
  };

  addButton({
    id: 'bypass-dependency-wait',
    label: 'Bypass Dependencies',
    available: Boolean(actions.canBypassDependencies),
    disabledReason: disabledReason('canBypassDependencies'),
    danger: true,
    onSelect: handlers.onBypassDependencies,
  });
  addButton({
    id: 'cancel',
    label: 'Cancel',
    available: Boolean(actions.canCancel),
    disabledReason: disabledReason('canCancel'),
    danger: true,
    confirmation: CANCEL_CONFIRMATION,
    onSelect: handlers.onCancel,
  });
  // The API keeps force cancel enabled even when the projected state is
  // terminal, because that projection can be stale; never hide it here.
  addButton({
    id: 'force-cancel',
    label: 'Force cancel',
    available: Boolean(actions.canForceCancel),
    disabledReason: disabledReason('canForceCancel'),
    danger: true,
    troubleshooting: true,
    description:
      'Stops the workflow immediately without cleanup. Use it only for stuck workflows or a status that looks wrong.',
    confirmation: FORCE_CANCEL_CONFIRMATION,
    onSelect: handlers.onForceCancel,
  });
  if (taskEditingOn) {
    addLink({
      id: 'edit-task',
      label: 'Edit',
      href: editHref,
      available: Boolean(canShowEditWorkflow && editHref),
      disabledReason: editTaskDisabledReason,
      onSelect: handlers.onEditTask,
    });
  }
  addButton({
    id: 'create-remediation-task',
    label: 'Remediate',
    available: canCreateRemediation,
    disabledReason: keepRemediationVisible
      ? 'Available for failed, stuck, or intervention-required workflows.'
      : null,
    onSelect: handlers.onCreateRemediation,
  });
  addButton({
    id: 'continue-remediation',
    label: 'Continue remediation',
    available: Boolean(actions.canContinueRemediation),
    disabledReason: disabledReason('canContinueRemediation'),
    onSelect: handlers.onContinueRemediation,
  });
  addButton({
    id: 'retry-publication',
    label: 'Retry publication',
    available: Boolean(actions.canRetryPublication),
    disabledReason: disabledReason('canRetryPublication'),
    onSelect: handlers.onRetryPublication,
  });
  // Retry keeps the workflow's recorded inputs, so it does not need editing.
  addButton({
    id: 'retry-from-failed-step',
    label: 'Retry from failed step',
    available: Boolean(actions.canRetryFromStep),
    disabledReason: disabledReason('canRetryFromStep'),
    onSelect: handlers.onRetryFromStep,
  });
  if (taskEditingOn) {
    addButton({
      id: 'rerun',
      label: 'Rerun',
      available: Boolean(actions.canRerun),
      disabledReason: rerunDisabledReason,
      onSelect: handlers.onRerun,
    });
  }
  addButton({
    id: 'rename',
    label: 'Rename',
    available: Boolean(actions.canSetTitle),
    disabledReason: disabledReason('canSetTitle'),
    onSelect: handlers.onRename,
  });
  if (taskEditingOn) {
    addLink({
      id: 'compare-run',
      label: 'Compare run',
      href: compareHref,
      available: Boolean(compareHref),
      disabledReason: disabledReason('canEditForRerun'),
      onSelect: handlers.onCompareRun,
    });
  }
  addButton({
    id: 'pause',
    label: 'Pause',
    available: Boolean(actions.canPause),
    disabledReason: disabledReason('canPause'),
    onSelect: handlers.onPause,
  });
  addButton({
    id: 'resume',
    label: 'Resume',
    available: Boolean(actions.canResume),
    disabledReason: disabledReason('canResume'),
    onSelect: handlers.onResume,
  });
  addButton({
    id: 'approve',
    label: 'Approve',
    available: Boolean(actions.canApprove),
    disabledReason: disabledReason('canApprove'),
    onSelect: handlers.onApprove,
  });
  addButton({
    id: 'reject',
    label: 'Reject',
    available: Boolean(actions.canReject),
    disabledReason: disabledReason('canReject'),
    danger: true,
    confirmation: REJECT_CONFIRMATION,
    onSelect: handlers.onReject,
  });
  addButton({
    id: 'send-message',
    label: 'Send Message',
    available: Boolean(actions.canSendMessage),
    disabledReason: disabledReason('canSendMessage'),
    onSelect: handlers.onSendMessage,
  });
  const sectionOrder = (item: WorkflowActionMenuItem) =>
    WORKFLOW_ACTION_MENU_SECTIONS.indexOf(item.section ?? 'primary');
  return items.sort((left, right) => sectionOrder(left) - sectionOrder(right));
}

export type RetryFromStepRequest = {
  apiBase: string;
  workflowId: string;
  /** The failed run being retried; a newer run makes the request conflict. */
  sourceRunId?: string | null;
  /** Omit to restart from the failed step the server records. */
  stepId?: string | null;
};

export type RetryFromStepResult = {
  workflowId: string;
  runId: string;
  sourceRunId: string;
  stepId: string;
  stepTitle?: string | null;
  message: string;
};

/**
 * A fixed-length key for one retry intent, so repeated clicks deduplicate and
 * long workflow or step ids stay within the API's 128-character limit. The
 * server compares keys per workflow, so a 64-bit hash is ample.
 */
export function retryFromStepIdempotencyKey(
  workflowId: string,
  sourceRunId?: string | null,
  stepId?: string | null,
): string {
  const identity = [workflowId, sourceRunId || 'latest', stepId || 'failed'].join('\n');
  // Two FNV-1a passes with different offsets; crypto.subtle needs a secure
  // context, which a LAN deployment over plain HTTP does not have.
  const fnv1a = (offset: number) => {
    let hash = offset;
    for (let index = 0; index < identity.length; index += 1) {
      hash ^= identity.charCodeAt(index);
      hash = Math.imul(hash, 0x01000193);
    }
    return (hash >>> 0).toString(16).padStart(8, '0');
  };
  return `retry-${fnv1a(0x811c9dc5)}${fnv1a(0x050c5d1f)}`;
}

/**
 * Restart a closed run from one of its steps. Earlier steps are kept and the
 * workflow keeps its id; a refusal throws an ApiError with its reason code.
 */
export async function requestRetryFromStep({
  apiBase,
  workflowId,
  sourceRunId,
  stepId,
}: RetryFromStepRequest): Promise<RetryFromStepResult> {
  const response = await fetch(
    `${apiBase}/executions/${encodeURIComponent(workflowId)}/retry-from-step`,
    {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({
        idempotencyKey: retryFromStepIdempotencyKey(workflowId, sourceRunId, stepId),
        ...(sourceRunId ? { sourceRunId } : {}),
        ...(stepId ? { stepId } : {}),
      }),
    },
  );
  if (!response.ok) {
    throw await responseError(response);
  }
  return response.json();
}

function coalesceString(...values: unknown[]): string {
  for (const value of values) {
    const normalized = String(value ?? '').trim();
    if (normalized) {
      return normalized;
    }
  }
  return '';
}

export type RemediationRuntimeInput = {
  inputParameters?: Record<string, unknown> | null | undefined;
  targetRuntime?: string | null | undefined;
  profileId?: string | null | undefined;
  model?: string | null | undefined;
  resolvedModel?: string | null | undefined;
  requestedModel?: string | null | undefined;
  effort?: string | null | undefined;
};

export function buildRemediationRuntimeRequestFields(
  input: RemediationRuntimeInput | null | undefined,
): Record<string, unknown> {
  const mode = coalesceString(input?.targetRuntime);
  const profileId = coalesceString(input?.profileId);
  const model = coalesceString(input?.model, input?.resolvedModel, input?.requestedModel);
  const effort = coalesceString(input?.effort);
  const parameters = input?.inputParameters;
  const workflow = parameters?.workflow ?? parameters?.task;
  const authoredRuntime = workflow && typeof workflow === 'object' && 'runtime' in workflow
    ? (workflow as Record<string, unknown>).runtime
    : parameters?.runtime;
  const hasAuthoredRuntime = authoredRuntime != null && typeof authoredRuntime === 'object';
  const runtime: Record<string, unknown> = hasAuthoredRuntime ? readModelSelection(authoredRuntime) : {};
  if (mode) runtime.mode = mode;
  if (!hasAuthoredRuntime && model) runtime.model = model;
  if (!hasAuthoredRuntime && effort) runtime.effort = effort;
  if (profileId) runtime.profileId = profileId;

  return {
    ...(Object.keys(runtime).length > 0 ? { runtime } : {}),
  };
}

export type RemediationEligibilityInput = {
  rawState?: string | null | undefined;
  state?: string | null | undefined;
  status?: string | null | undefined;
  attentionRequired?: boolean | null | undefined;
  waitingReason?: string | null | undefined;
};

export function isRemediationEligibleTarget(
  input: RemediationEligibilityInput,
): boolean {
  const state = (input.rawState || input.state || input.status || '').toLowerCase();
  return (
    input.attentionRequired === true ||
    Boolean(input.waitingReason) ||
    state.includes('failed') ||
    state.includes('stuck') ||
    state === 'awaiting_external'
  );
}
