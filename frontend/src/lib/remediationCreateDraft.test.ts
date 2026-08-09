import { beforeEach, describe, expect, it } from 'vitest';

import {
  buildRemediationCreateDraft,
  clearRemediationCreateDraft,
  inspectRemediationCreateDraft,
  REMEDIATION_CREATE_DRAFT_TTL_MS,
  remediationCreateDraftHref,
  readRemediationCreateDraft,
  storeRemediationCreateDraft,
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
        approvalPolicy: {
          requiredForHighRisk: true,
        },
        lockPolicy: {
          targetMutationLock: true,
        },
        verificationPolicy: {
          verifyAppliedActions: true,
        },
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

  it('stores, reads, builds a href, and clears a short-lived draft', () => {
    const draft = buildRemediationCreateDraft({
      workflowId: 'mm:target',
      runId: 'run-target',
    });

    const draftId = storeRemediationCreateDraft(draft);
    const href = remediationCreateDraftHref(draftId);

    expect(href).toBe(`/workflows/new?intent=remediate&draftId=${encodeURIComponent(draftId)}`);
    expect(readRemediationCreateDraft(draftId)).toMatchObject(draft);

    clearRemediationCreateDraft(draftId);
    expect(readRemediationCreateDraft(draftId)).toBeNull();
  });

  it('distinguishes missing, malformed, and expired drafts without clearing evidence', () => {
    expect(inspectRemediationCreateDraft('other-tab')).toEqual({ status: 'missing', draft: null });

    window.sessionStorage.setItem('moonmind.remediation-create-draft.bad', '{not-json');
    expect(inspectRemediationCreateDraft('bad')).toEqual({ status: 'malformed', draft: null });
    expect(window.sessionStorage.getItem('moonmind.remediation-create-draft.bad')).toBe('{not-json');

    const expired = buildRemediationCreateDraft({ workflowId: 'mm:target', runId: 'run-target' });
    window.sessionStorage.setItem('moonmind.remediation-create-draft.old', JSON.stringify({
      ...expired,
      createdAt: '2026-07-07T00:00:00.000Z',
    }));
    expect(inspectRemediationCreateDraft(
      'old',
      Date.parse('2026-07-07T00:00:00.000Z') + REMEDIATION_CREATE_DRAFT_TTL_MS + 1,
    )).toEqual({ status: 'expired', draft: null });
    expect(window.sessionStorage.getItem('moonmind.remediation-create-draft.old')).not.toBeNull();
  });

  it('preserves the immutable agent and Provider Profile selection', () => {
    const draft = buildRemediationCreateDraft({
      workflowId: 'mm:target',
      runId: 'run-target',
      targetRuntime: 'omnigent',
      profileId: 'oauth-team',
      agentProfile: { profileId: 'team-codex', version: 2 },
    });

    expect(draft.agentProfile).toEqual({
      profileId: 'team-codex',
      version: 2,
      providerProfileRef: 'oauth-team',
    });
  });

  it('recovers the immutable selection from execution input parameters', () => {
    const draft = buildRemediationCreateDraft({
      workflowId: 'mm:target',
      runId: 'run-target',
      targetRuntime: 'omnigent',
      profileId: 'oauth-team',
      inputParameters: {
        agentProfile: { profileId: 'team-codex', version: 2 },
        agentProfileSnapshot: { providerProfileRef: 'oauth-team' },
      },
    });

    expect(draft.agentProfile).toEqual({
      profileId: 'team-codex',
      version: 2,
      providerProfileRef: 'oauth-team',
    });
  });
});
