import { describe, expect, it, vi } from 'vitest';

import {
  buildRemediationCreateDraft,
  buildRemediationRuntimeRequestFields,
  buildWorkflowActionMenuItems,
  readRemediationCreateDraft,
  remediationCreateHref,
  isRemediationEligibleTarget,
  type ExecutionActionCapabilities,
  type WorkflowActionHandlers,
} from './workflowActions';

function noopHandlers(): WorkflowActionHandlers {
  return {
    onRename: vi.fn(),
    onEditTask: vi.fn(),
    onCompareRun: vi.fn(),
    onRerun: vi.fn(),
    onResumeFromFailedStep: vi.fn(),
    onRecoverFromSelectedStep: vi.fn(),
    onPause: vi.fn(),
    onResume: vi.fn(),
    onApprove: vi.fn(),
    onReject: vi.fn(),
    onCancel: vi.fn(),
    onForceCancel: vi.fn(),
    onSendMessage: vi.fn(),
    onBypassDependencies: vi.fn(),
    onCreateRemediation: vi.fn(),
  };
}

function buildParams(
  overrides: Partial<Parameters<typeof buildWorkflowActionMenuItems>[0]> = {},
) {
  return {
    actionsOn: true,
    actions: {} as ExecutionActionCapabilities,
    busy: false,
    taskEditingOn: false,
    disabledReason: () => null,
    editHref: '',
    compareHref: '',
    canShowEditWorkflow: false,
    editTaskDisabledReason: null,
    rerunDisabledReason: null,
    selectedRecoveryOptionCount: 0,
    selectedRecoveryStepEligible: false,
    selectedRecoveryStepDisabledReason: null,
    canCreateRemediation: false,
    handlers: noopHandlers(),
    ...overrides,
  };
}

describe('buildWorkflowActionMenuItems', () => {
  it('returns no items when actions are disabled', () => {
    expect(buildWorkflowActionMenuItems(buildParams({ actionsOn: false }))).toEqual([]);
  });

  it('returns no items when no capability payload is present', () => {
    expect(buildWorkflowActionMenuItems(buildParams({ actions: null }))).toEqual([]);
  });

  it('builds the available intervention and lifecycle options', () => {
    const items = buildWorkflowActionMenuItems(
      buildParams({
        actions: {
          canSetTitle: true,
          canPause: true,
          canResume: true,
          canApprove: true,
          canReject: true,
          canCancel: true,
          canSendMessage: true,
          canBypassDependencies: true,
        },
        canCreateRemediation: true,
      }),
    );
    expect(items.map((item) => item.id)).toEqual([
      'bypass-dependency-wait',
      'cancel',
      'force-cancel',
      'create-remediation-task',
      'rename',
      'pause',
      'resume',
      'approve',
      'reject',
      'send-message',
    ]);
    expect(items.find((item) => item.id === 'reject')?.danger).toBe(true);
    expect(items.find((item) => item.id === 'cancel')?.danger).toBe(true);
    expect(items.find((item) => item.id === 'force-cancel')?.danger).toBe(true);
    expect(items.find((item) => item.id === 'bypass-dependency-wait')?.danger).toBe(true);
  });

  it('includes the task-editing options only when task editing is enabled', () => {
    const withoutEditing = buildWorkflowActionMenuItems(
      buildParams({ actions: { canRerun: true } }),
    );
    expect(withoutEditing.some((item) => item.id === 'rerun')).toBe(false);

    const withEditing = buildWorkflowActionMenuItems(
      buildParams({
        taskEditingOn: true,
        canShowEditWorkflow: true,
        editHref: '/tasks/abc/edit',
        compareHref: '/tasks/abc/compare',
        actions: { canRerun: true, canResumeFromFailedStep: true },
      }),
    );
    expect(withEditing.map((item) => item.id)).toEqual([
      'edit-task',
      'rerun',
      'resume-from-failed-step',
      'compare-run',
    ]);
    expect(withEditing.find((item) => item.id === 'edit-task')?.href).toBe('/tasks/abc/edit');
  });

  it('omits recover-from-selected-step unless a recovery option is selected', () => {
    const base = {
      taskEditingOn: true,
      actions: { canResumeFromFailedStep: true } as ExecutionActionCapabilities,
    };
    const withoutSelection = buildWorkflowActionMenuItems(buildParams(base));
    expect(withoutSelection.some((item) => item.id === 'recover-from-selected-step')).toBe(false);

    const withSelection = buildWorkflowActionMenuItems(
      buildParams({
        ...base,
        selectedRecoveryOptionCount: 1,
        selectedRecoveryStepEligible: true,
      }),
    );
    expect(withSelection.some((item) => item.id === 'recover-from-selected-step')).toBe(true);
  });

  it('surfaces a disabled reason for unavailable actions that report one', () => {
    const items = buildWorkflowActionMenuItems(
      buildParams({
        actions: { canPause: false },
        disabledReason: (key) => (key === 'canPause' ? 'Not pausable' : null),
      }),
    );
    const pause = items.find((item) => item.id === 'pause');
    expect(pause?.disabledReason).toBe('Not pausable');
    expect(pause?.onSelect).toBeUndefined();
  });

  it('marks available actions as pending while another action is in flight', () => {
    const items = buildWorkflowActionMenuItems(
      buildParams({ busy: true, actions: { canPause: true } }),
    );
    expect(items.find((item) => item.id === 'pause')?.disabledReason).toBe('action pending');
  });

  it('uses a caller-provided pending reason for temporarily disabled actions', () => {
    const items = buildWorkflowActionMenuItems(
      buildParams({
        busy: true,
        busyDisabledReason: 'Checking availability…',
        actions: { canPause: true },
      }),
    );
    expect(items.find((item) => item.id === 'pause')?.disabledReason).toBe(
      'Checking availability…',
    );
  });
});

describe('buildRemediationRuntimeRequestFields', () => {
  it('returns an empty object when no runtime fields are present', () => {
    expect(buildRemediationRuntimeRequestFields({})).toEqual({});
    expect(buildRemediationRuntimeRequestFields(null)).toEqual({});
  });

  it('coalesces model candidates and trims runtime fields', () => {
    expect(
      buildRemediationRuntimeRequestFields({
        targetRuntime: 'codex_cli',
        profileId: 'profile-1',
        model: '',
        resolvedModel: 'gpt-x',
        effort: 'high',
      }),
    ).toEqual({
      runtime: {
        mode: 'codex_cli',
        model: 'gpt-x',
        effort: 'high',
        profileId: 'profile-1',
      },
    });
  });
});

describe('remediation create drafts', () => {
  it('builds a create-page draft with explicit target identity and runtime fields', () => {
    const draft = buildRemediationCreateDraft({
      workflowId: 'mm:target',
      runId: 'run-1',
      execution: {
        repository: 'MoonLadderStudios/MoonMind',
        targetRuntime: 'codex_cli',
        resolvedModel: 'gpt-5',
        effort: 'high',
        profileId: 'codex-profile',
      },
    });

    expect(draft).toMatchObject({
      repository: 'MoonLadderStudios/MoonMind',
      runtime: {
        mode: 'codex_cli',
        model: 'gpt-5',
        effort: 'high',
        profileId: 'codex-profile',
      },
      remediation: {
        mode: 'snapshot_then_follow',
        authorityMode: 'approval_gated',
        actionPolicyRef: 'admin_healer_default',
        target: {
          workflowId: 'mm:target',
          runId: 'run-1',
        },
        evidencePolicy: {
          includeStepLedger: true,
          includeDiagnostics: true,
          tailLines: 2000,
        },
        trigger: { type: 'manual' },
      },
    });
  });

  it('round-trips remediation create drafts through the Create Workflow URL', () => {
    const draft = buildRemediationCreateDraft({
      workflowId: 'mm:target',
      runId: 'run-1',
      execution: { repository: 'MoonLadderStudios/MoonMind' },
    });

    const href = remediationCreateHref(draft);
    expect(href.startsWith('/workflows/new?remediationDraft=')).toBe(true);
    expect(readRemediationCreateDraft(href.slice('/workflows/new'.length))).toEqual(draft);
  });
});

describe('isRemediationEligibleTarget', () => {
  it('is eligible for failed, stuck, or awaiting-external states', () => {
    expect(isRemediationEligibleTarget({ state: 'failed' })).toBe(true);
    expect(isRemediationEligibleTarget({ rawState: 'stuck_runtime' })).toBe(true);
    expect(isRemediationEligibleTarget({ status: 'awaiting_external' })).toBe(true);
  });

  it('is eligible when attention is required or a waiting reason is present', () => {
    expect(isRemediationEligibleTarget({ attentionRequired: true })).toBe(true);
    expect(isRemediationEligibleTarget({ waitingReason: 'blocked' })).toBe(true);
  });

  it('is not eligible for healthy terminal states', () => {
    expect(isRemediationEligibleTarget({ state: 'completed' })).toBe(false);
  });
});
