import { describe, expect, it, vi } from 'vitest';

import { ApiError } from './api/client';
import {
  buildRemediationRuntimeRequestFields,
  buildWorkflowActionMenuItems,
  describeWorkflowActionDisabledReason,
  isRemediationEligibleTarget,
  requestRetryFromStep,
  type ExecutionActionCapabilities,
  type WorkflowActionHandlers,
} from './workflowActions';

function noopHandlers(): WorkflowActionHandlers {
  return {
    onRename: vi.fn(),
    onEditTask: vi.fn(),
    onCompareRun: vi.fn(),
    onRerun: vi.fn(),
    onRetryFromStep: vi.fn(),
    onContinueRemediation: vi.fn(),
    onRetryPublication: vi.fn(),
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
    canCreateRemediation: false,
    handlers: noopHandlers(),
    ...overrides,
  };
}

describe('buildWorkflowActionMenuItems', () => {
  it('keeps force cancel available when a terminal projection hides ordinary cancel', () => {
    const handlers = noopHandlers();
    const items = buildWorkflowActionMenuItems(buildParams({
      actions: { canCancel: false, canForceCancel: true }, handlers,
    }));
    expect(items.map((item) => item.id)).toEqual(['force-cancel']);
    const force = items[0];
    force?.onSelect?.();
    expect(handlers.onForceCancel).toHaveBeenCalledOnce();
  });
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
          canForceCancel: true,
          canSendMessage: true,
          canBypassDependencies: true,
        },
        canCreateRemediation: true,
      }),
    );
    expect(items.map((item) => item.id)).toEqual([
      'create-remediation-task',
      'rename',
      'pause',
      'resume',
      'approve',
      'send-message',
      'bypass-dependency-wait',
      'cancel',
      'reject',
      'force-cancel',
    ]);
    expect(items.find((item) => item.id === 'reject')?.danger).toBe(true);
    expect(items.find((item) => item.id === 'cancel')?.danger).toBe(true);
    expect(items.find((item) => item.id === 'force-cancel')?.danger).toBe(true);
    expect(items.find((item) => item.id === 'bypass-dependency-wait')?.danger).toBe(true);
  });

  it('puts safe actions first, destructive ones after them, and force cancel under troubleshooting', () => {
    const items = buildWorkflowActionMenuItems(
      buildParams({
        actions: {
          canSetTitle: true,
          canPause: true,
          canCancel: true,
          canForceCancel: true,
          canReject: true,
          canApprove: false,
          disabledReasons: { canApprove: 'state_not_eligible' },
        },
        disabledReason: (key) => (key === 'canApprove' ? 'state_not_eligible' : null),
      }),
    );
    expect(items.map((item) => [item.id, item.section])).toEqual([
      ['rename', 'primary'],
      ['pause', 'primary'],
      ['cancel', 'destructive'],
      ['reject', 'destructive'],
      ['force-cancel', 'troubleshooting'],
      ['approve', 'unavailable'],
    ]);
    expect(items.find((item) => item.id === 'force-cancel')?.description).toMatch(
      /without cleanup.*stuck/i,
    );
  });

  it('asks for confirmation before Cancel, Reject and Force cancel but not Pause or Resume', () => {
    const items = buildWorkflowActionMenuItems(
      buildParams({
        actions: {
          canPause: true,
          canResume: true,
          canCancel: true,
          canForceCancel: true,
          canReject: true,
        },
      }),
    );
    for (const id of ['cancel', 'reject', 'force-cancel']) {
      const confirmation = items.find((item) => item.id === id)?.confirmation;
      expect(confirmation?.consequence).toBeTruthy();
      expect(confirmation?.confirmLabel).toBeTruthy();
    }
    expect(items.find((item) => item.id === 'pause')?.confirmation).toBeUndefined();
    expect(items.find((item) => item.id === 'resume')?.confirmation).toBeUndefined();
  });

  it('keeps force cancel enabled on a completed workflow while ineligible actions explain why', () => {
    const disabledReasons: Record<string, string> = {
      canCancel: 'state_not_eligible',
      canPause: 'state_not_eligible',
      canSetTitle: 'state_not_eligible',
    };
    const items = buildWorkflowActionMenuItems(
      buildParams({
        actions: { canForceCancel: true, disabledReasons },
        disabledReason: (key) => describeWorkflowActionDisabledReason(disabledReasons[key]),
      }),
    );
    const forceCancel = items.find((item) => item.id === 'force-cancel');
    expect(forceCancel).toMatchObject({ section: 'troubleshooting' });
    expect(forceCancel?.disabledReason).toBeUndefined();
    const unavailable = items.filter((item) => item.section === 'unavailable');
    expect(unavailable.map((item) => item.id)).toEqual(['cancel', 'rename', 'pause']);
    for (const item of unavailable) {
      expect(item.disabledReason).toBe("Not available in the workflow's current state.");
    }
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
        actions: { canRerun: true },
      }),
    );
    expect(withEditing.map((item) => item.id)).toEqual([
      'edit-task',
      'rerun',
      'compare-run',
    ]);
    expect(withEditing.find((item) => item.id === 'edit-task')?.href).toBe('/tasks/abc/edit');
  });

  it('pins Remediate with a reason only when the caller keeps it visible', () => {
    // Default (workflow detail menu): an ineligible target drops the item.
    const collapsed = buildWorkflowActionMenuItems(
      buildParams({ canCreateRemediation: false }),
    );
    expect(collapsed.some((item) => item.id === 'create-remediation-task')).toBe(false);

    // Workflow list rows promote Remediate to a button, so it stays put and
    // explains itself rather than making the actions column ragged.
    const pinned = buildWorkflowActionMenuItems(
      buildParams({ canCreateRemediation: false, keepRemediationVisible: true }),
    );
    const remediate = pinned.find((item) => item.id === 'create-remediation-task');
    expect(remediate).toBeTruthy();
    expect(remediate?.disabledReason).toBe(
      'Available for failed, stuck, or intervention-required workflows.',
    );

    // Eligible targets are unaffected by the flag.
    const eligible = buildWorkflowActionMenuItems(
      buildParams({ canCreateRemediation: true, keepRemediationVisible: true }),
    );
    expect(
      eligible.find((item) => item.id === 'create-remediation-task')?.disabledReason,
    ).toBeUndefined();
  });

  it('exposes admitted control-stop continuation and its disabled reason', () => {
    const handlers = noopHandlers();
    const available = buildWorkflowActionMenuItems(
      buildParams({
        actions: { canContinueRemediation: true },
        handlers,
      }),
    );
    expect(available.map((item) => item.id)).toEqual(['continue-remediation']);
    available[0]?.onSelect?.();
    expect(handlers.onContinueRemediation).toHaveBeenCalledOnce();

    const unavailable = buildWorkflowActionMenuItems(
      buildParams({
        actions: {
          canContinueRemediation: false,
          disabledReasons: {
            canContinueRemediation: 'deployment canary disabled',
          },
        },
        disabledReason: (key) =>
          key === 'canContinueRemediation' ? 'deployment canary disabled' : null,
      }),
    );
    expect(unavailable[0]).toMatchObject({
      id: 'continue-remediation',
      disabledReason: 'deployment canary disabled',
    });
  });

  it('offers retry from the failed step without requiring task editing', () => {
    const handlers = noopHandlers();
    const items = buildWorkflowActionMenuItems(
      buildParams({ actions: { canRetryFromStep: true }, handlers }),
    );
    expect(items.map((item) => item.id)).toEqual(['retry-from-failed-step']);
    expect(items[0]?.label).toBe('Retry from failed step');
    items[0]?.onSelect?.();
    expect(handlers.onRetryFromStep).toHaveBeenCalledOnce();

    const unavailable = buildWorkflowActionMenuItems(
      buildParams({
        actions: { canRetryFromStep: false },
        disabledReason: (key) => (key === 'canRetryFromStep' ? 'child workflow' : null),
      }),
    );
    expect(unavailable[0]).toMatchObject({
      id: 'retry-from-failed-step',
      disabledReason: 'child workflow',
    });
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

  it('exposes publication-only retry with its projected eligibility', () => {
    const handlers = noopHandlers();
    const available = buildWorkflowActionMenuItems(
      buildParams({
        actions: { canRetryPublication: true },
        handlers,
      }),
    );
    expect(available.map((item) => item.id)).toEqual(['retry-publication']);
    available[0]?.onSelect?.();
    expect(handlers.onRetryPublication).toHaveBeenCalledOnce();

    const unavailable = buildWorkflowActionMenuItems(
      buildParams({
        actions: { canRetryPublication: false },
        disabledReason: (key) =>
          key === 'canRetryPublication' ? 'Publication evidence is incomplete' : null,
      }),
    );
    expect(unavailable[0]).toMatchObject({
      id: 'retry-publication',
      disabledReason: 'Publication evidence is incomplete',
    });
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

describe('describeWorkflowActionDisabledReason', () => {
  it('translates backend reason codes into plain sentences', () => {
    expect(describeWorkflowActionDisabledReason('already_paused')).toBe(
      'The workflow is already paused.',
    );
    expect(describeWorkflowActionDisabledReason('original_task_input_snapshot_missing')).toBe(
      "The workflow's original inputs were not saved.",
    );
  });

  it('falls back to a readable sentence for unknown codes and keeps prose unchanged', () => {
    expect(describeWorkflowActionDisabledReason('workspace_checkpoint_missing')).toBe(
      'Workspace checkpoint missing.',
    );
    expect(describeWorkflowActionDisabledReason('Workflow cannot be rerun.')).toBe(
      'Workflow cannot be rerun.',
    );
    expect(describeWorkflowActionDisabledReason('')).toBeNull();
    expect(describeWorkflowActionDisabledReason(null)).toBeNull();
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


describe('MoonLadderStudios/MoonMind#4636 remediation authored selection', () => {
  it.each([{}, { modelTier: 2 }, { model: null, effort: null }, { modelTier: 2, effort: 'max', tierFallback: 'strict' }])('preserves raw selection %j without diagnostic companions', (selection) => {
    const fields = buildRemediationRuntimeRequestFields({ targetRuntime: 'codex_cli', profileId: 'profile', resolvedModel: 'old-model', effort: 'old-effort', inputParameters: { workflow: { runtime: selection } } });
    expect(fields.runtime).toEqual({ mode: 'codex_cli', profileId: 'profile', ...selection });
  });
});

describe('requestRetryFromStep', () => {
  it('reports a refused retry as an API error with its status, code, and message', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(
        JSON.stringify({
          detail: {
            code: 'retry_from_step_unavailable',
            reason: 'history_not_replayable',
            message: 'The run history no longer replays on the installed MoonMind version.',
          },
        }),
        { status: 409, statusText: 'Conflict', headers: { 'Content-Type': 'application/json' } },
      ),
    );
    try {
      const error = await requestRetryFromStep({
        apiBase: '/api',
        workflowId: 'mm:failed',
        sourceRunId: 'run-1',
      }).catch((caught: unknown) => caught);
      expect(error).toBeInstanceOf(ApiError);
      expect((error as ApiError).status).toBe(409);
      expect((error as ApiError).code).toBe('retry_from_step_unavailable');
      expect((error as ApiError).message).toBe(
        'The run history no longer replays on the installed MoonMind version.',
      );
    } finally {
      fetchSpy.mockRestore();
    }
  });
});
