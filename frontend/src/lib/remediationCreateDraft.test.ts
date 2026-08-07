import { beforeEach, describe, expect, it } from 'vitest';

import {
  buildRemediationCreateDraft,
  clearRemediationCreateDraft,
  loadRemediationCreateDraft,
  remediationCreateDraftHref,
  readRemediationCreateDraft,
  REMEDIATION_DRAFT_TTL_MS,
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
    expect(readRemediationCreateDraft(draftId)).toEqual(draft);

    clearRemediationCreateDraft(draftId);
    expect(readRemediationCreateDraft(draftId)).toBeNull();
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

  it('prepopulates branch, execution profile, launch policy, and retrieval from the target run', () => {
    const draft = buildRemediationCreateDraft({
      workflowId: 'mm:target',
      runId: 'run-target',
      targetRuntime: 'omnigent',
      inputParameters: {
        branch: 'feature/work',
        git: { baseBranch: 'main' },
        omnigent: {
          executionTargetRef: 'exec:profile-a',
          launchPolicyRef: 'launch:policy-a',
        },
        rag: { collections: ['docs'], required: true },
      },
    });

    expect(draft.branch).toBe('feature/work');
    expect(draft.startingBranch).toBe('main');
    expect(draft.executionProfileRef).toBe('exec:profile-a');
    expect(draft.launchPolicyRef).toBe('launch:policy-a');
    expect(draft.contextRetrieval).toMatchObject({
      initial: { collections: ['docs'], required: true },
    });
    expect(typeof draft.createdAt).toBe('number');
  });

  it('distinguishes missing, malformed, and expired drafts on load', () => {
    expect(loadRemediationCreateDraft('does-not-exist')).toEqual({
      status: 'missing',
      draft: null,
    });

    window.sessionStorage.setItem(
      'moonmind.remediation-create-draft.corrupt',
      '{not valid json',
    );
    expect(loadRemediationCreateDraft('corrupt').status).toBe('malformed');

    window.sessionStorage.setItem(
      'moonmind.remediation-create-draft.foreign',
      JSON.stringify({ source: 'not-remediation' }),
    );
    expect(loadRemediationCreateDraft('foreign').status).toBe('malformed');

    const draft = buildRemediationCreateDraft({
      workflowId: 'mm:target',
      runId: 'run-target',
    });
    const stale = {
      ...draft,
      createdAt: Date.now() - (REMEDIATION_DRAFT_TTL_MS + 60_000),
    };
    window.sessionStorage.setItem(
      'moonmind.remediation-create-draft.stale',
      JSON.stringify(stale),
    );
    const expired = loadRemediationCreateDraft('stale');
    expect(expired.status).toBe('expired');
    expect(expired.draft).toBeNull();
    // Expired drafts are removed on read so they cannot be reapplied.
    expect(
      window.sessionStorage.getItem('moonmind.remediation-create-draft.stale'),
    ).toBeNull();
    expect(readRemediationCreateDraft('stale')).toBeNull();
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
