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
    window.localStorage.clear();
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
      schemaVersion: 1,
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
    expect(Date.parse(draft.createdAt)).not.toBeNaN();
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

  it('bounds generated checkpoint selectors to the create contract limit', () => {
    const draft = buildRemediationCreateDraft({
      workflowId: 'mm:target',
      runId: 'run-target',
      checkpoints: Array.from({ length: 30 }, (_, index) => ({
        logicalStepId: `step-${index + 1}`,
        checkpointRef: `artifact://checkpoint/${index + 1}`,
      })),
    });

    expect(draft.target.stepSelectors).toHaveLength(25);
    expect(draft.remediation.target.stepSelectors).toHaveLength(25);
    expect(draft.target.stepSelectors?.[24]).toMatchObject({
      logicalStepId: 'step-25',
      checkpointRef: 'artifact://checkpoint/25',
    });

    const draftId = storeRemediationCreateDraft(draft);
    expect(inspectRemediationCreateDraft(draftId).status).toBe('valid');
  });

  it('prepopulates source/work branches, Omnigent launch identity, and retrieval controls', () => {
    const draft = buildRemediationCreateDraft({
      workflowId: 'mm:target',
      runId: 'run-target',
      targetRuntime: 'omnigent',
      inputParameters: {
        repository: {
          provider: 'git',
          repository: { name: 'MoonLadderStudios/MoonMind' },
          branch: { name: 'main' },
        },
        omnigent: {
          executionTargetRef: 'execution-profile:codex-default',
          launchPolicyRef: 'launch-policy:remediation-v3',
        },
        rag: { collections: ['docs'], required: true },
        workflow: {
          remediation: {
            checkpointBranchPolicy: {
              gitWorkBranch: 'repair/mm-3623',
            },
          },
        },
      },
    });

    expect(draft.branch).toBe('main');
    expect(draft.startingBranch).toBe('main');
    expect(draft.workBranch).toBe('repair/mm-3623');
    expect(draft.executionProfileRef).toBe('execution-profile:codex-default');
    expect(draft.launchPolicyRef).toBe('launch-policy:remediation-v3');
    expect(draft.contextRetrieval).toMatchObject({
      initial: { collections: ['docs'], required: true },
    });
    expect(draft.remediation.checkpointBranchPolicy).toMatchObject({
      gitWorkBranch: 'repair/mm-3623',
    });
  });

  it('distinguishes missing, malformed, expired, and cross-tab draft failures', () => {
    expect(inspectRemediationCreateDraft('missing')).toEqual({
      status: 'missing',
      draft: null,
    });

    window.sessionStorage.setItem(
      'moonmind.remediation-create-draft.malformed',
      JSON.stringify({ source: 'remediation', schemaVersion: 1 }),
    );
    expect(inspectRemediationCreateDraft('malformed').status).toBe('malformed');

    const draft = buildRemediationCreateDraft({
      workflowId: 'mm:target',
      runId: 'run-target',
    });
    window.sessionStorage.setItem(
      'moonmind.remediation-create-draft.malformed-selectors',
      JSON.stringify({
        ...draft,
        remediation: {
          ...draft.remediation,
          target: {
            ...draft.remediation.target,
            stepSelectors: 'tampered',
          },
        },
      }),
    );
    expect(
      inspectRemediationCreateDraft('malformed-selectors').status,
    ).toBe('malformed');
    window.sessionStorage.setItem(
      'moonmind.remediation-create-draft.unsupported-action-policy',
      JSON.stringify({
        ...draft,
        remediation: {
          ...draft.remediation,
          actionPolicyRef: 'operator_review_only',
        },
      }),
    );
    expect(
      inspectRemediationCreateDraft('unsupported-action-policy').status,
    ).toBe('malformed');
    window.sessionStorage.setItem(
      'moonmind.remediation-create-draft.expired',
      JSON.stringify({
        ...draft,
        createdAt: new Date(
          Date.now() - REMEDIATION_CREATE_DRAFT_TTL_MS - 1,
        ).toISOString(),
      }),
    );
    expect(inspectRemediationCreateDraft('expired').status).toBe('expired');
    expect(
      window.sessionStorage.getItem('moonmind.remediation-create-draft.expired'),
    ).toBeNull();

    const draftId = storeRemediationCreateDraft(draft);
    window.sessionStorage.removeItem(
      `moonmind.remediation-create-draft.${draftId}`,
    );
    expect(inspectRemediationCreateDraft(draftId).status).toBe('cross_tab');
    clearRemediationCreateDraft(draftId);
    expect(inspectRemediationCreateDraft(draftId).status).toBe('missing');
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
