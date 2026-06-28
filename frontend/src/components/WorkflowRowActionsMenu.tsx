import { useCallback, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { z } from 'zod';

import { DashboardActionDialog } from './DashboardActionDialog';
import { formatStatusLabel } from '../utils/formatters';
import {
  taskCompareHref,
  taskEditForRerunHref,
  taskEditHref,
} from '../lib/temporalTaskEditing';
import {
  buildRemediationRuntimeRequestFields,
  buildWorkflowActionMenuItems,
  DEFAULT_REMEDIATION_ACTION_POLICY,
  DEFAULT_REMEDIATION_AUTHORITY,
  DEFAULT_REMEDIATION_MODE,
  ExecutionActionsSchema,
  isRemediationEligibleTarget,
  type WorkflowActionMenuItem,
} from '../lib/workflowActions';
import { WorkflowActionsMenu } from './WorkflowActionsMenu';

/**
 * Focused projection of the execution detail payload. The row actions menu only
 * needs the action capabilities plus the few fields required to build control
 * and remediation requests; the full detail schema lives on the detail page.
 */
const RowActionsExecutionSchema = z
  .object({
    workflowId: z.string().nullable().optional(),
    runId: z.string().nullable().optional(),
    temporalRunId: z.string().nullable().optional(),
    title: z.string().nullable().optional(),
    repository: z.string().nullable().optional(),
    state: z.string().nullable().optional(),
    rawState: z.string().nullable().optional(),
    status: z.string().nullable().optional(),
    attentionRequired: z.boolean().nullable().optional(),
    waitingReason: z.string().nullable().optional(),
    targetRuntime: z.string().nullable().optional(),
    profileId: z.string().nullable().optional(),
    model: z.string().nullable().optional(),
    resolvedModel: z.string().nullable().optional(),
    requestedModel: z.string().nullable().optional(),
    effort: z.string().nullable().optional(),
    resume: z
      .object({
        checkpointRef: z.string().nullable().optional(),
        sourceRunId: z.string().nullable().optional(),
      })
      .passthrough()
      .nullable()
      .optional(),
    actions: ExecutionActionsSchema.optional(),
  })
  .passthrough();

type RowActionsExecution = z.infer<typeof RowActionsExecutionSchema>;
type RowActionDialogKind =
  | 'rename'
  | 'resume-from-failed-step'
  | 'bypass-dependencies'
  | 'cancel'
  | 'force-cancel'
  | 'reject'
  | 'send-message';

const KEBAB_ICON = (
  <svg
    aria-hidden="true"
    className="td-workflow-actions-trigger-icon"
    viewBox="0 0 16 16"
    focusable="false"
  >
    <circle cx="8" cy="3" r="1.5" />
    <circle cx="8" cy="8" r="1.5" />
    <circle cx="8" cy="13" r="1.5" />
  </svg>
);

export type WorkflowRowActionsMenuProps = {
  workflowId: string;
  apiBase: string;
  actionsEnabled: boolean;
  taskEditingEnabled: boolean;
};

/**
 * Self-contained actions dropdown rendered per Workflows table row. It lazily
 * loads the workflow's action capabilities the first time the menu is opened,
 * then exposes the same options as the Workflow Detail "Workflow actions" menu
 * without requiring navigation to the detail page.
 */
export function WorkflowRowActionsMenu({
  workflowId,
  apiBase,
  actionsEnabled,
  taskEditingEnabled,
}: WorkflowRowActionsMenuProps) {
  const queryClient = useQueryClient();
  const [hasOpened, setHasOpened] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [activeDialog, setActiveDialog] = useState<RowActionDialogKind | null>(null);

  const detailQuery = useQuery({
    queryKey: ['workflow-row-actions-detail', workflowId],
    enabled: actionsEnabled && hasOpened && Boolean(workflowId),
    staleTime: 5000,
    queryFn: async () => {
      const response = await fetch(
        `${apiBase}/executions/${encodeURIComponent(workflowId)}?source=temporal`,
      );
      if (!response.ok) {
        throw new Error(`Workflow actions: ${response.statusText}`);
      }
      return RowActionsExecutionSchema.parse(await response.json());
    },
  });

  const execution: RowActionsExecution | undefined = detailQuery.data;
  const actions = execution?.actions;
  const runId = execution?.temporalRunId?.trim() || execution?.runId?.trim() || '';

  const invalidate = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ['workflow-row-actions-detail', workflowId] });
    void queryClient.invalidateQueries({ queryKey: ['workflow-list'] });
  }, [queryClient, workflowId]);

  const onMutationError = useCallback((error: Error) => {
    setActionError(error.message);
  }, []);

  const updateMutation = useMutation({
    mutationFn: async (body: Record<string, unknown>) => {
      const response = await fetch(`${apiBase}/executions/${encodeURIComponent(workflowId)}/update`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify(body),
      });
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || response.statusText);
      }
      return response.json();
    },
    onSuccess: invalidate,
    onError: onMutationError,
  });

  const signalMutation = useMutation({
    mutationFn: async ({
      signalName,
      payload: signalPayload,
    }: {
      signalName: string;
      payload?: Record<string, unknown>;
    }) => {
      const response = await fetch(`${apiBase}/executions/${encodeURIComponent(workflowId)}/signal`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ signalName, payload: signalPayload ?? {} }),
      });
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || response.statusText);
      }
      return response.json();
    },
    onSuccess: invalidate,
    onError: onMutationError,
  });

  const cancelMutation = useMutation({
    mutationFn: async ({
      action = 'cancel',
      graceful = true,
      reason,
    }: {
      action?: 'cancel' | 'reject';
      graceful?: boolean;
      reason?: string;
    }) => {
      const response = await fetch(`${apiBase}/executions/${encodeURIComponent(workflowId)}/cancel`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ action, graceful, ...(reason ? { reason } : {}) }),
      });
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || response.statusText);
      }
      return response.json();
    },
    onSuccess: invalidate,
    onError: onMutationError,
  });

  const failedStepResumeMutation = useMutation({
    mutationFn: async () => {
      const response = await fetch(
        `${apiBase}/executions/${encodeURIComponent(workflowId)}/recover-from-failed-step`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
          body: JSON.stringify({
            idempotencyKey: `resume-${workflowId}-${runId || 'latest'}`,
            ...(execution?.resume?.checkpointRef
              ? { recoveryCheckpointRef: execution.resume.checkpointRef }
              : {}),
            operatorMetadata: { requestedFrom: 'workflow-list' },
          }),
        },
      );
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || response.statusText);
      }
      return response.json();
    },
    onSuccess: invalidate,
    onError: onMutationError,
  });

  const createRemediationMutation = useMutation({
    mutationFn: async () => {
      const response = await fetch(`${apiBase}/executions/${encodeURIComponent(workflowId)}/remediation`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({
          repository: execution?.repository ?? null,
          ...buildRemediationRuntimeRequestFields(execution),
          instructions: `Investigate and remediate target execution ${workflowId} using bounded evidence.`,
          remediation: {
            mode: DEFAULT_REMEDIATION_MODE,
            authorityMode: DEFAULT_REMEDIATION_AUTHORITY,
            target: { runId: runId || undefined },
            actionPolicyRef: DEFAULT_REMEDIATION_ACTION_POLICY,
            evidencePolicy: {
              includeStepLedger: true,
              includeDiagnostics: true,
              tailLines: 2000,
            },
            trigger: { type: 'manual' },
          },
        }),
      });
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || response.statusText);
      }
      return response.json();
    },
    onSuccess: invalidate,
    onError: onMutationError,
  });

  const busy =
    updateMutation.isPending ||
    signalMutation.isPending ||
    cancelMutation.isPending ||
    failedStepResumeMutation.isPending ||
    createRemediationMutation.isPending;

  const editHref = workflowId
    ? actions?.canEditForRerun
      ? taskEditForRerunHref(workflowId)
      : taskEditHref(workflowId)
    : '';
  const compareHref =
    workflowId && actions?.canEditForRerun ? taskCompareHref(workflowId) : '';
  const canShowEditWorkflow = Boolean(actions?.canUpdateInputs || actions?.canEditForRerun);
  const editTaskUnavailableReason = canShowEditWorkflow
    ? null
    : actions?.disabledReasons?.canEditForRerun ||
      actions?.disabledReasons?.canUpdateInputs ||
      null;
  const rerunUnavailableReason = actions?.disabledReasons?.canRerun || null;
  const canCreateRemediation = Boolean(execution && isRemediationEligibleTarget(execution));
  const disabledReason = useCallback(
    (key: string): string | null => {
      const reason = actions?.disabledReasons?.[key];
      return reason ? formatStatusLabel(reason) : null;
    },
    [actions],
  );

  const items: WorkflowActionMenuItem[] = useMemo(() => {
    if (!actionsEnabled || !actions) return [];
    return buildWorkflowActionMenuItems({
      actionsOn: actionsEnabled,
      actions,
      busy,
      taskEditingOn: taskEditingEnabled,
      disabledReason,
      editHref,
      compareHref,
      canShowEditWorkflow,
      editTaskDisabledReason: editTaskUnavailableReason
        ? formatStatusLabel(editTaskUnavailableReason)
        : null,
      rerunDisabledReason: rerunUnavailableReason
        ? formatStatusLabel(rerunUnavailableReason)
        : null,
      selectedRecoveryOptionCount: 0,
      selectedRecoveryStepEligible: false,
      selectedRecoveryStepDisabledReason: null,
      canCreateRemediation,
      handlers: {
        onRename: () => {
          setActionError(null);
          setActiveDialog('rename');
        },
        onEditTask: () => {},
        onCompareRun: () => {},
        onRerun: () => {
          setActionError(null);
          if (busy || !workflowId) return;
          updateMutation.mutate({ updateName: 'RequestRerun' });
        },
        onResumeFromFailedStep: () => {
          setActionError(null);
          setActiveDialog('resume-from-failed-step');
        },
        onRecoverFromSelectedStep: () => {},
        onPause: () => {
          setActionError(null);
          signalMutation.mutate({ signalName: 'Pause', payload: {} });
        },
        onResume: () => {
          setActionError(null);
          signalMutation.mutate({ signalName: 'Resume', payload: {} });
        },
        onApprove: () => {
          setActionError(null);
          signalMutation.mutate({ signalName: 'Approve', payload: {} });
        },
        onReject: () => {
          setActionError(null);
          setActiveDialog('reject');
        },
        onCancel: () => {
          setActionError(null);
          setActiveDialog('cancel');
        },
        onForceCancel: () => {
          setActionError(null);
          setActiveDialog('force-cancel');
        },
        onSendMessage: () => {
          setActionError(null);
          setActiveDialog('send-message');
        },
        onBypassDependencies: () => {
          setActionError(null);
          setActiveDialog('bypass-dependencies');
        },
        onCreateRemediation: () => {
          setActionError(null);
          createRemediationMutation.mutate();
        },
      },
    });
  }, [
    actions,
    actionsEnabled,
    busy,
    canCreateRemediation,
    canShowEditWorkflow,
    cancelMutation,
    compareHref,
    createRemediationMutation,
    disabledReason,
    editHref,
    editTaskUnavailableReason,
    execution,
    failedStepResumeMutation,
    rerunUnavailableReason,
    signalMutation,
    taskEditingEnabled,
    updateMutation,
    workflowId,
  ]);

  const emptyMessage = detailQuery.isLoading
    ? 'Loading actions…'
    : detailQuery.isError
      ? 'Unable to load workflow actions.'
      : 'No workflow actions are currently available.';
  const subject = execution?.title?.trim() || workflowId;
  const closeDialog = () => {
    setActiveDialog(null);
    setActionError(null);
  };
  const confirmDialog = (value: string) => {
    const closeOnSuccess = { onSuccess: () => setActiveDialog(null) };
    switch (activeDialog) {
      case 'rename':
        updateMutation.mutate({ updateName: 'SetTitle', title: value }, closeOnSuccess);
        break;
      case 'resume-from-failed-step':
        failedStepResumeMutation.mutate(undefined, closeOnSuccess);
        break;
      case 'bypass-dependencies':
        signalMutation.mutate(
          {
            signalName: 'BypassDependencies',
            payload: { reason: value || 'Dependency wait bypassed by operator from the dashboard.' },
          },
          closeOnSuccess,
        );
        break;
      case 'cancel':
        cancelMutation.mutate(
          { action: 'cancel', graceful: true, ...(value ? { reason: value } : {}) },
          closeOnSuccess,
        );
        break;
      case 'force-cancel':
        cancelMutation.mutate(
          {
            action: 'cancel',
            graceful: false,
            reason: value || 'Force canceled by operator from the dashboard.',
          },
          closeOnSuccess,
        );
        break;
      case 'reject':
        cancelMutation.mutate(
          { action: 'reject', graceful: true, reason: value || 'Rejected by operator.' },
          closeOnSuccess,
        );
        break;
      case 'send-message':
        signalMutation.mutate(
          { signalName: 'SendMessage', payload: { message: value } },
          closeOnSuccess,
        );
        break;
      default:
        break;
    }
  };

  return (
    <div className="workflow-row-actions">
      <WorkflowActionsMenu
        items={items}
        triggerContent={KEBAB_ICON}
        triggerAriaLabel="Actions"
        triggerClassName="secondary td-workflow-actions-trigger td-workflow-actions-trigger-compact"
        menuAriaLabel="Actions"
        emptyMessage={emptyMessage}
        onOpenChange={(open) => {
          if (open) setHasOpened(true);
        }}
      />
      <DashboardActionDialog
        open={activeDialog === 'rename'}
        title="Rename workflow"
        subject={subject}
        compactId={workflowId}
        consequence="Set a dashboard title for this workflow. Execution history, artifacts, and workflow identity stay unchanged."
        valueLabel="Workflow title"
        valueRequired
        initialValue={execution?.title || ''}
        confirmLabel={updateMutation.isPending ? 'Renaming' : 'Rename workflow'}
        confirmPending={updateMutation.isPending}
        disabledReason={disabledReason('canSetTitle')}
        error={activeDialog === 'rename' ? actionError : null}
        onCancel={closeDialog}
        onConfirm={confirmDialog}
      />
      <DashboardActionDialog
        open={activeDialog === 'resume-from-failed-step'}
        title="Resume from failed step"
        subject={subject}
        compactId={workflowId}
        consequence="Resume from the failed step using the original workflow input snapshot."
        confirmLabel={failedStepResumeMutation.isPending ? 'Resuming' : 'Resume workflow'}
        confirmPending={failedStepResumeMutation.isPending}
        disabledReason={disabledReason('canResumeFromFailedStep')}
        error={activeDialog === 'resume-from-failed-step' ? actionError : null}
        onCancel={closeDialog}
        onConfirm={confirmDialog}
      />
      <DashboardActionDialog
        open={activeDialog === 'bypass-dependencies'}
        title="Bypass dependencies"
        subject={subject}
        compactId={workflowId}
        consequence="Bypass dependency waiting for this workflow. Downstream work may proceed before prerequisites finish."
        valueLabel="Reason"
        valuePlaceholder="Dependency wait bypassed by operator from the dashboard."
        valueMultiline
        confirmLabel={signalMutation.isPending ? 'Bypassing' : 'Bypass dependencies'}
        confirmPending={signalMutation.isPending}
        danger
        disabledReason={disabledReason('canBypassDependencies')}
        error={activeDialog === 'bypass-dependencies' ? actionError : null}
        onCancel={closeDialog}
        onConfirm={confirmDialog}
      />
      <DashboardActionDialog
        open={activeDialog === 'cancel'}
        title="Cancel workflow"
        subject={subject}
        compactId={workflowId}
        consequence="Request a graceful workflow cancellation. Running work may stop at the next cancellation boundary."
        valueLabel="Reason"
        valuePlaceholder="Optional operator reason"
        valueMultiline
        confirmLabel={cancelMutation.isPending ? 'Cancelling' : 'Cancel workflow'}
        confirmPending={cancelMutation.isPending}
        danger
        disabledReason={disabledReason('canCancel')}
        error={activeDialog === 'cancel' ? actionError : null}
        onCancel={closeDialog}
        onConfirm={confirmDialog}
      />
      <DashboardActionDialog
        open={activeDialog === 'force-cancel'}
        title="Force cancel workflow"
        subject={subject}
        compactId={workflowId}
        consequence="Terminate the Temporal workflow immediately. This is a high-risk operator action and may skip graceful cleanup."
        valueLabel="Reason"
        valuePlaceholder="Force canceled by operator from the dashboard."
        valueMultiline
        confirmLabel={cancelMutation.isPending ? 'Force cancelling' : 'Force cancel workflow'}
        confirmPending={cancelMutation.isPending}
        danger
        destructive
        confirmationText="FORCE CANCEL"
        disabledReason={disabledReason('canCancel')}
        error={activeDialog === 'force-cancel' ? actionError : null}
        onCancel={closeDialog}
        onConfirm={confirmDialog}
      />
      <DashboardActionDialog
        open={activeDialog === 'reject'}
        title="Reject workflow"
        subject={subject}
        compactId={workflowId}
        consequence="Reject the workflow and record an operator rejection outcome."
        valueLabel="Reason"
        valuePlaceholder="Rejected by operator."
        valueMultiline
        confirmLabel={cancelMutation.isPending ? 'Rejecting' : 'Reject workflow'}
        confirmPending={cancelMutation.isPending}
        danger
        destructive
        confirmationText="REJECT"
        disabledReason={disabledReason('canReject')}
        error={activeDialog === 'reject' ? actionError : null}
        onCancel={closeDialog}
        onConfirm={confirmDialog}
      />
      <DashboardActionDialog
        open={activeDialog === 'send-message'}
        title="Send operator message"
        subject={subject}
        compactId={workflowId}
        consequence="Send a message into the workflow's operator intervention channel."
        valueLabel="Message"
        valueRequired
        valueMultiline
        confirmLabel={signalMutation.isPending ? 'Sending' : 'Send message'}
        confirmPending={signalMutation.isPending}
        disabledReason={disabledReason('canSendMessage')}
        error={activeDialog === 'send-message' ? actionError : null}
        onCancel={closeDialog}
        onConfirm={confirmDialog}
      />
      {actionError ? (
        <p className="workflow-row-actions-error" role="alert">
          {actionError}
        </p>
      ) : null}
    </div>
  );
}
