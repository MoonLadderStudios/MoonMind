import { beforeEach, describe, expect, it } from 'vitest';

import {
  buildRemediationCreateDraft,
  clearRemediationCreateDraft,
  navigateToRemediationCreateDraft,
  readRemediationCreateDraft,
} from './remediationCreateDraft';

describe('remediationCreateDraft', () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    window.history.pushState({}, '', '/workflows');
  });

  it('builds a Create-page remediation draft from execution detail', () => {
    const draft = buildRemediationCreateDraft({
      workflowId: 'mm:target',
      runId: 'run-target',
      title: 'Failed target',
      state: 'failed',
      repository: '',
      targetRuntime: 'codex_cli',
      model: 'gpt-5',
      effort: 'high',
      profileId: 'profile:codex',
      resume: { checkpointRef: 'artifact://checkpoint/failed-step' },
    });

    expect(draft).toMatchObject({
      source: 'remediation',
      repository: 'MoonLadderStudios/MoonMind',
      target: {
        workflowId: 'mm:target',
        runId: 'run-target',
        title: 'Failed target',
        state: 'failed',
      },
      runtime: {
        mode: 'codex_cli',
        model: 'gpt-5',
        effort: 'high',
        profileId: 'profile:codex',
      },
      remediation: {
        target: {
          workflowId: 'mm:target',
          runId: 'run-target',
        },
        mode: 'snapshot_then_follow',
        authorityMode: 'approval_gated',
        actionPolicyRef: 'admin_healer_default',
        checkpointBranchPolicy: {
          actionKind: 'checkpoint_branch.create_from_remediation_context',
          runtimeContextPolicy: 'fresh_agent_run',
        },
        trigger: { type: 'manual' },
      },
    });
    expect(draft.target.stepSelectors?.[0]).toMatchObject({
      checkpointRef: 'artifact://checkpoint/failed-step',
    });
  });

  it('stores, reads, navigates, and clears a short-lived draft', () => {
    const draft = buildRemediationCreateDraft({
      workflowId: 'mm:target',
      runId: 'run-target',
    });

    const draftId = navigateToRemediationCreateDraft(draft);

    expect(window.location.pathname).toBe('/workflows/new');
    expect(window.location.search).toContain('intent=remediate');
    expect(window.location.search).toContain(`draftId=${encodeURIComponent(draftId)}`);
    expect(readRemediationCreateDraft(draftId)).toEqual(draft);

    clearRemediationCreateDraft(draftId);
    expect(readRemediationCreateDraft(draftId)).toBeNull();
  });
});
