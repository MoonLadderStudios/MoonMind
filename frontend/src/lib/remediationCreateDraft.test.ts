import { beforeEach, describe, expect, it, vi } from 'vitest';

import {
  buildRemediationCreateDraft,
  clearRemediationCreateDraft,
  consumeRemediationCreateDraft,
  loadRemediationCreateDraft,
  remediationCreateDraftUrl,
  storeRemediationCreateDraft,
} from './remediationCreateDraft';

const executionDetail = {
  workflowId: 'mm:target-workflow',
  runId: 'run-target-1',
  title: 'Target workflow',
  state: 'failed',
  repository: 'MoonLadderStudios/MoonMind',
  targetRuntime: 'codex_cli',
  model: 'gpt-5',
  effort: 'high',
  profileId: 'profile-codex',
};

describe('remediationCreateDraft', () => {
  beforeEach(() => {
    window.sessionStorage.clear();
    vi.useRealTimers();
  });

  it('builds a pinned remediation draft with create-first defaults', () => {
    const draft = buildRemediationCreateDraft(executionDetail, {
      now: new Date('2026-07-07T12:00:00.000Z'),
    });

    expect(draft).toMatchObject({
      source: 'remediation',
      createdAt: '2026-07-07T12:00:00.000Z',
      target: {
        workflowId: 'mm:target-workflow',
        runId: 'run-target-1',
        title: 'Target workflow',
        state: 'failed',
      },
      repository: 'MoonLadderStudios/MoonMind',
      runtime: {
        mode: 'codex_cli',
        model: 'gpt-5',
        effort: 'high',
        profileId: 'profile-codex',
      },
      remediation: {
        mode: 'snapshot_then_follow',
        authorityMode: 'approval_gated',
        actionPolicyRef: 'admin_healer_default',
        target: {
          workflowId: 'mm:target-workflow',
          runId: 'run-target-1',
        },
        trigger: { type: 'manual' },
      },
    });
  });

  it('stores, loads, consumes, and clears session-scoped drafts', () => {
    const draft = buildRemediationCreateDraft(executionDetail);
    const draftId = storeRemediationCreateDraft(draft);

    expect(remediationCreateDraftUrl(draftId)).toBe(
      `/workflows/new?intent=remediate&draftId=${encodeURIComponent(draftId)}`,
    );
    expect(loadRemediationCreateDraft(draftId)).toMatchObject({
      target: { workflowId: 'mm:target-workflow', runId: 'run-target-1' },
    });
    expect(consumeRemediationCreateDraft(draftId)).toMatchObject({
      target: { workflowId: 'mm:target-workflow', runId: 'run-target-1' },
    });
    expect(loadRemediationCreateDraft(draftId)).toBeNull();

    const secondId = storeRemediationCreateDraft(draft);
    clearRemediationCreateDraft(secondId);
    expect(loadRemediationCreateDraft(secondId)).toBeNull();
  });

  it('rejects malformed and expired drafts without throwing', () => {
    window.sessionStorage.setItem('moonmind.remediationDraft.bad', '{"source":"wrong"}');
    expect(loadRemediationCreateDraft('bad')).toBeNull();

    const draft = buildRemediationCreateDraft(executionDetail, {
      now: new Date('2026-07-07T12:00:00.000Z'),
    });
    const draftId = storeRemediationCreateDraft(draft);
    vi.setSystemTime(new Date('2026-07-08T12:00:01.000Z'));
    expect(loadRemediationCreateDraft(draftId)).toBeNull();
  });
});
