import { useCallback, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { z } from 'zod';

import { DashboardActionDialog } from './DashboardActionDialog';
import { useDashboardToast } from './dashboard/DashboardToast';
import { formatStatusLabel } from '../utils/formatters';
import { navigateTo } from '../lib/navigation';
import { workflowDetailHref } from '../lib/workflowListContext';
import {
  taskCompareHref,
  taskEditForRerunHref,
  taskEditHref,
} from '../lib/temporalTaskEditing';
import {
  buildWorkflowActionMenuItems,
  ExecutionActionsSchema,
  isRemediationEligibleTarget,
  type WorkflowActionMenuItem,
} from '../lib/workflowActions';
import { WorkflowActionsMenu } from './WorkflowActionsMenu';
import {
  buildRemediationCreateDraft,
  remediationCreateDraftHref,
  storeRemediationCreateDraft,
} from '../lib/remediationCreateDraft';

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

const ACTION_AVAILABILITY_PENDING_REASON = 'Checking availability…';
const DEFAULT_WORKFLOW_ACTION_ERROR = 'The workflow action could not be completed.';

const PENDING_WORKFLOW_ACTION_CAPABILITIES = {
  canSetTitle: true,
  canUpdateInputs: true,
  canEditForRerun: true,
  canRerun: true,
  canApprove: true,
  canPause: true,
  canResume: true,
  canResumeFromFailedStep: true,
  canCancel: true,
  canReject: true,
  canSendMessage: true,
  canBypassDependencies: true,
} satisfies RowActionsExecution['actions'];

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function readableWorkflowActionError(error: unknown): string {
  const rawMessage =
    error instanceof Error
      ? error.message
      : typeof error === 'string'
        ? error
        : isRecord(error) && typeof error.message === 'string'
          ? error.message
          : '';
  const message = rawMessage.trim();
  if (!message) return DEFAULT_WORKFLOW_ACTION_ERROR;
  try {
    const parsed = JSON.parse(message) as { detail?: unknown; message?: unknown };
    const parsedMessage =
      typeof parsed.detail === 'string'
        ? parsed.detail
        : typeof parsed.message === 'string'
          ? parsed.message
          : null;
    if (parsedMessage?.trim()) return parsedMessage.trim();
  } catch {
    // Plain text API errors are already user-readable enough for this surface.
  }
  return message.replace(/\s+/g, ' ').slice(0, 240);
}

function workflowActionResultHref(result: unknown, fallbackHref: string): string {
  const execution = isRecord(result) && isRecord(result.execution) ? result.execution : null;
  const redirectPath =
    execution && typeof execution.redirectPath === 'string' ? execution.redirectPath.trim() : '';
  if (redirectPath) return redirectPath;

  const resultWorkflowId =
    execution && typeof execution.workflowId === 'string' ? execution.workflowId.trim() : '';
  if (resultWorkflowId) {
    return workflowDetailHref(resultWorkflowId, new URLSearchParams(window.location.search));
  }

  return fallbackHref;
}

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
  const toast = useDashboardToast();
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

  const onMutationError = useCallback(
    (error: unknown) => {
      const message = readableWorkflowActionError(error);
      setActionError(message);
      toast.error({
        title: 'Workflow action failed',
        message,
      });
    },
    [toast],
  );

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
    }: {
      action?: 'cancel' | 'reject';
      graceful?: boolean;
    }) => {
      const response = await fetch(`${apiBase}/executions/${encodeURIComponent(workflowId)}/cancel`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ action, graceful }),
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
      const rawContract = window.prompt(
        'Paste the admitted workflow-recovery-target/v1 JSON contract.',
      );
      if (!rawContract) {
        throw new Error('Typed recovery requires an admitted recovery contract.');
      }
      let recoveryTarget: unknown;
      try {
        recoveryTarget = JSON.parse(rawContract);
      } catch {
        throw new Error('Typed recovery contract must be valid JSON.');
      }
      const response = await fetch(
        `${apiBase}/executions/${encodeURIComponent(workflowId)}/recover`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
          body: JSON.stringify(recoveryTarget),
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

  const retryPublicationMutation = useMutation({
    mutationFn: async () => {
      const response = await fetch(
        `${apiBase}/executions/${encodeURIComponent(workflowId)}/retry-publication`,
        {
          method: 'POST',
          credentials: 'include',
          headers: { Accept: 'application/json' },
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
      if (!execution) {
        throw new Error('Workflow detail is required before remediation can be drafted.');
      }
      const draft = buildRemediationCreateDraft(execution, { runId });
      const draftId = storeRemediationCreateDraft(draft);
      navigateTo(remediationCreateDraftHref(draftId));
      return { draft };
    },
    onError: onMutationError,
  });

  const busy =
    updateMutation.isPending ||
    signalMutation.isPending ||
    cancelMutation.isPending ||
    failedStepResumeMutation.isPending ||
    retryPublicationMutation.isPending ||
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
  const actionAvailabilityPending = actionsEnabled && !actions && !detailQuery.isError;
  const workflowSubject = execution?.title?.trim() || workflowId;
  const detailHref = workflowDetailHref(workflowId, new URLSearchParams(window.location.search));
  const disabledReason = useCallback(
    (key: string): string | null => {
      if (actionAvailabilityPending) return ACTION_AVAILABILITY_PENDING_REASON;
      const reason = actions?.disabledReasons?.[key];
      return reason ? formatStatusLabel(reason) : null;
    },
    [actionAvailabilityPending, actions],
  );

  const items: WorkflowActionMenuItem[] = useMemo(() => {
    const menuActions = actionAvailabilityPending
      ? PENDING_WORKFLOW_ACTION_CAPABILITIES
      : actions;
    if (!actionsEnabled || !menuActions) return [];
    const pendingReason = actionAvailabilityPending ? ACTION_AVAILABILITY_PENDING_REASON : null;
    return buildWorkflowActionMenuItems({
      actionsOn: actionsEnabled,
      actions: menuActions,
      busy: busy || actionAvailabilityPending,
      ...(pendingReason ? { busyDisabledReason: pendingReason } : {}),
      taskEditingOn: taskEditingEnabled,
      disabledReason,
      editHref: actionAvailabilityPending ? taskEditHref(workflowId) : editHref,
      compareHref: actionAvailabilityPending ? taskCompareHref(workflowId) : compareHref,
      canShowEditWorkflow: actionAvailabilityPending ? true : canShowEditWorkflow,
      editTaskDisabledReason: editTaskUnavailableReason
        ? formatStatusLabel(editTaskUnavailableReason)
        : pendingReason,
      rerunDisabledReason: rerunUnavailableReason
        ? formatStatusLabel(rerunUnavailableReason)
        : pendingReason,
      selectedRecoveryOptionCount: 0,
      selectedRecoveryStepEligible: false,
      selectedRecoveryStepDisabledReason: null,
      canCreateRemediation: actionAvailabilityPending ? true : canCreateRemediation,
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
          updateMutation.mutate(
            { updateName: 'RequestRerun' },
            {
              onSuccess: (result) => {
                toast.success({
                  title: 'Rerun requested',
                  message: `${workflowSubject} has been queued.`,
                  action: {
                    label: 'View workflow',
                    href: workflowActionResultHref(result, detailHref),
                  },
                });
              },
            },
          );
        },
        onResumeFromFailedStep: () => {
          setActionError(null);
          failedStepResumeMutation.mutate();
        },
        onRecoverFromSelectedStep: () => {},
        onRetryPublication: () => {
          setActionError(null);
          retryPublicationMutation.mutate();
        },
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
          cancelMutation.mutate({ action: 'reject', graceful: true });
        },
        onCancel: () => {
          setActionError(null);
          cancelMutation.mutate({ action: 'cancel', graceful: true });
        },
        onForceCancel: () => {
          setActionError(null);
          cancelMutation.mutate({ action: 'cancel', graceful: false });
        },
        onSendMessage: () => {
          setActionError(null);
          setActiveDialog('send-message');
        },
        onBypassDependencies: () => {
          setActionError(null);
          signalMutation.mutate(
            {
              signalName: 'BypassDependencies',
              payload: { reason: 'Dependency wait bypassed by operator from the dashboard.' },
            },
            {
              onSuccess: () => {
                toast.success({
                  title: 'Dependency wait bypass requested',
                  message: `${workflowSubject} will continue without waiting on dependencies.`,
                  action: {
                    label: 'View workflow',
                    href: detailHref,
                  },
                });
              },
            },
          );
        },
        onCreateRemediation: () => {
          setActionError(null);
          createRemediationMutation.mutate();
        },
      },
    });
  }, [
    actionAvailabilityPending,
    actions,
    actionsEnabled,
    busy,
    canCreateRemediation,
    canShowEditWorkflow,
    cancelMutation,
    compareHref,
    createRemediationMutation,
    disabledReason,
    detailHref,
    editHref,
    editTaskUnavailableReason,
    execution,
    failedStepResumeMutation,
    rerunUnavailableReason,
    retryPublicationMutation,
    signalMutation,
    taskEditingEnabled,
    toast,
    updateMutation,
    workflowId,
    workflowSubject,
  ]);

  const emptyMessage = detailQuery.isLoading
    ? 'Loading actions…'
    : detailQuery.isError
      ? 'Unable to load workflow actions.'
      : 'No workflow actions are currently available.';
  const subject = workflowSubject;
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
