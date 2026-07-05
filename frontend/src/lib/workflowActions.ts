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
    canResumeFromFailedStep: z.boolean().optional(),
    canCancel: z.boolean().optional(),
    canReject: z.boolean().optional(),
    canSendMessage: z.boolean().optional(),
    canBypassDependencies: z.boolean().optional(),
    disabledReasons: z.record(z.string(), z.string()).optional(),
  })
  .passthrough();

export type ExecutionActionCapabilities = z.infer<typeof ExecutionActionsSchema>;

export const DEFAULT_REMEDIATION_MODE = 'snapshot_then_follow';
export const DEFAULT_REMEDIATION_AUTHORITY = 'approval_gated';
export const DEFAULT_REMEDIATION_ACTION_POLICY = 'admin_healer_default';
export const DEFAULT_REMEDIATION_INSTRUCTIONS =
  'Investigate the target workflow execution, gather evidence, attempt the smallest safe immediate repair if one seems possible, verify the target outcome, then create a reviewable long-term MoonMind or Agent Skill fix if a recurrence-prevention change is identified.';
export const REMEDIATION_CREATE_DRAFT_PARAM = 'remediationDraft';

export type WorkflowActionMenuItem = {
  id: string;
  label: string;
  href?: string;
  danger?: boolean;
  disabledReason?: string | null;
  onSelect?: () => void;
};

export type WorkflowActionHandlers = {
  onRename: () => void;
  onEditTask: () => void;
  onCompareRun: () => void;
  onRerun: () => void;
  onResumeFromFailedStep: () => void;
  onRecoverFromSelectedStep: () => void;
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
  /**
   * Number of operator-selected recovery steps. The "Recover from selected step"
   * action only applies when a step is selected on the detail page, so the list
   * row passes 0 and that option is omitted.
   */
  selectedRecoveryOptionCount: number;
  selectedRecoveryStepEligible: boolean;
  /** Display-ready reason for an ineligible selected recovery step. */
  selectedRecoveryStepDisabledReason: string | null;
  canCreateRemediation: boolean;
  handlers: WorkflowActionHandlers;
};

/**
 * Build the ordered list of workflow action options shown in the actions dropdown.
 *
 * This is the single source of truth shared by the Workflow Detail "Workflow
 * actions" menu and the Workflows table row "Actions" menu so both surfaces
 * always present the same options, ordering, and disabled semantics.
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
    selectedRecoveryOptionCount,
    selectedRecoveryStepEligible,
    selectedRecoveryStepDisabledReason,
    canCreateRemediation,
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
    onSelect,
  }: {
    id: string;
    label: string;
    available: boolean;
    disabledReason?: string | null;
    danger?: boolean;
    onSelect: () => void;
  }) => {
    const effectiveDisabledReason = available ? pendingActionReason : itemDisabledReason ?? null;
    if (available) {
      items.push({
        id,
        label,
        ...(danger ? { danger: true } : {}),
        onSelect,
        ...(effectiveDisabledReason ? { disabledReason: effectiveDisabledReason } : {}),
      });
    } else if (effectiveDisabledReason) {
      items.push({
        id,
        label,
        ...(danger ? { danger: true } : {}),
        disabledReason: effectiveDisabledReason,
      });
    }
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
        onSelect,
        ...(effectiveDisabledReason ? { disabledReason: effectiveDisabledReason } : {}),
      });
    } else if (effectiveDisabledReason) {
      items.push({
        id,
        label,
        disabledReason: effectiveDisabledReason,
      });
    }
  };

  // Commonly used actions are surfaced at the top of the menu so operators can
  // reach them without scanning the full list.
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
    onSelect: handlers.onCancel,
  });
  addButton({
    id: 'force-cancel',
    label: 'Force cancel',
    available: Boolean(actions.canCancel),
    disabledReason: disabledReason('canCancel'),
    danger: true,
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
    disabledReason: null,
    onSelect: handlers.onCreateRemediation,
  });
  if (taskEditingOn) {
    addButton({
      id: 'rerun',
      label: 'Rerun',
      available: Boolean(actions.canRerun),
      disabledReason: rerunDisabledReason,
      onSelect: handlers.onRerun,
    });
    addButton({
      id: 'resume-from-failed-step',
      label: 'Resume from failed step',
      available: Boolean(actions.canResumeFromFailedStep),
      disabledReason: disabledReason('canResumeFromFailedStep'),
      onSelect: handlers.onResumeFromFailedStep,
    });
    if (actions.canResumeFromFailedStep && selectedRecoveryOptionCount > 0) {
      addButton({
        id: 'recover-from-selected-step',
        label: 'Recover from selected step',
        available: selectedRecoveryStepEligible,
        disabledReason: selectedRecoveryStepDisabledReason ?? 'selected step is not eligible',
        onSelect: handlers.onRecoverFromSelectedStep,
      });
    }
  }
  // Remaining, less commonly used actions follow.
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
    onSelect: handlers.onReject,
  });
  addButton({
    id: 'send-message',
    label: 'Send Message',
    available: Boolean(actions.canSendMessage),
    disabledReason: disabledReason('canSendMessage'),
    onSelect: handlers.onSendMessage,
  });
  return items;
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
  repository?: string | null | undefined;
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
  const runtime: Record<string, string> = {};
  if (mode) runtime.mode = mode;
  if (model) runtime.model = model;
  if (effort) runtime.effort = effort;
  if (profileId) runtime.profileId = profileId;

  return {
    ...(Object.keys(runtime).length > 0 ? { runtime } : {}),
  };
}

export type RemediationCreateDraft = {
  instructions: string;
  repository?: string;
  runtime?: {
    mode?: string;
    model?: string;
    effort?: string;
    profileId?: string;
  };
  remediation: {
    mode: string;
    authorityMode: string;
    target: {
      workflowId: string;
      runId?: string;
    };
    actionPolicyRef?: string;
    evidencePolicy: {
      includeStepLedger: boolean;
      includeDiagnostics: boolean;
      tailLines: number;
    };
    trigger: { type: 'manual' };
  };
};

export function buildRemediationCreateDraft({
  execution,
  workflowId,
  runId,
  mode = DEFAULT_REMEDIATION_MODE,
  authorityMode = DEFAULT_REMEDIATION_AUTHORITY,
  actionPolicyRef = DEFAULT_REMEDIATION_ACTION_POLICY,
}: {
  execution: RemediationRuntimeInput | null | undefined;
  workflowId: string;
  runId?: string | null | undefined;
  mode?: string;
  authorityMode?: string;
  actionPolicyRef?: string | null | undefined;
}): RemediationCreateDraft {
  const runtimeFields = buildRemediationRuntimeRequestFields(execution);
  const runtime = runtimeFields.runtime as RemediationCreateDraft['runtime'] | undefined;
  const repository = coalesceString(execution?.repository);
  const normalizedWorkflowId = coalesceString(workflowId);
  const normalizedRunId = coalesceString(runId);
  const normalizedActionPolicyRef = coalesceString(actionPolicyRef);

  return {
    instructions: DEFAULT_REMEDIATION_INSTRUCTIONS,
    ...(repository ? { repository } : {}),
    ...(runtime ? { runtime } : {}),
    remediation: {
      mode,
      authorityMode,
      target: {
        workflowId: normalizedWorkflowId,
        ...(normalizedRunId ? { runId: normalizedRunId } : {}),
      },
      ...(normalizedActionPolicyRef ? { actionPolicyRef: normalizedActionPolicyRef } : {}),
      evidencePolicy: {
        includeStepLedger: true,
        includeDiagnostics: true,
        tailLines: 2000,
      },
      trigger: { type: 'manual' },
    },
  };
}

export function remediationCreateHref(draft: RemediationCreateDraft): string {
  const params = new URLSearchParams();
  params.set(REMEDIATION_CREATE_DRAFT_PARAM, JSON.stringify(draft));
  return `/workflows/new?${params.toString()}`;
}

export function readRemediationCreateDraft(
  search: string,
): RemediationCreateDraft | null {
  const params = new URLSearchParams(search);
  const raw = params.get(REMEDIATION_CREATE_DRAFT_PARAM);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null;
    const record = parsed as Record<string, unknown>;
    const remediation = record.remediation;
    if (!remediation || typeof remediation !== 'object' || Array.isArray(remediation)) {
      return null;
    }
    const remediationRecord = remediation as Record<string, unknown>;
    const target = remediationRecord.target;
    if (!target || typeof target !== 'object' || Array.isArray(target)) return null;
    const targetRecord = target as Record<string, unknown>;
    const workflowId = coalesceString(targetRecord.workflowId);
    if (!workflowId) return null;

    const runtime = record.runtime;
    const runtimeRecord =
      runtime && typeof runtime === 'object' && !Array.isArray(runtime)
        ? (runtime as Record<string, unknown>)
        : {};
    return {
      instructions: coalesceString(record.instructions) || DEFAULT_REMEDIATION_INSTRUCTIONS,
      ...(coalesceString(record.repository) ? { repository: coalesceString(record.repository) } : {}),
      ...(Object.keys(runtimeRecord).length > 0
        ? {
            runtime: {
              ...(coalesceString(runtimeRecord.mode) ? { mode: coalesceString(runtimeRecord.mode) } : {}),
              ...(coalesceString(runtimeRecord.model) ? { model: coalesceString(runtimeRecord.model) } : {}),
              ...(coalesceString(runtimeRecord.effort) ? { effort: coalesceString(runtimeRecord.effort) } : {}),
              ...(coalesceString(runtimeRecord.profileId) ? { profileId: coalesceString(runtimeRecord.profileId) } : {}),
            },
          }
        : {}),
      remediation: {
        mode: coalesceString(remediationRecord.mode) || DEFAULT_REMEDIATION_MODE,
        authorityMode:
          coalesceString(remediationRecord.authorityMode) || DEFAULT_REMEDIATION_AUTHORITY,
        target: {
          workflowId,
          ...(coalesceString(targetRecord.runId) ? { runId: coalesceString(targetRecord.runId) } : {}),
        },
        ...(coalesceString(remediationRecord.actionPolicyRef)
          ? { actionPolicyRef: coalesceString(remediationRecord.actionPolicyRef) }
          : {}),
        evidencePolicy: {
          includeStepLedger: true,
          includeDiagnostics: true,
          tailLines: 2000,
        },
        trigger: { type: 'manual' },
      },
    };
  } catch {
    return null;
  }
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
