import { describe, expect, it } from 'vitest';

import { savedScheduleAgent, scheduleAgentPatch } from './scheduleAgentSelection';

const target = (parameters: Record<string, unknown>) => ({
  workflowType: 'MoonMind.UserWorkflow',
  initialParameters: parameters,
});

describe('savedScheduleAgent', () => {
  it('reads the authored selection, not what admission resolved from it', () => {
    expect(savedScheduleAgent(target({
      profileId: 'claude-work',
      model: 'claude-opus-5-5',
      modelSource: 'requested_tier',
      effort: 'high',
      workflow: { runtime: { mode: 'omnigent', modelTier: 2, resolvedModel: 'claude-opus-5-5', tierPreview: {} } },
    }))).toEqual({ profileId: 'claude-work', harness: '', selection: { modelTier: 2 } });
  });

  it('leaves a default-derived selection omitted', () => {
    expect(savedScheduleAgent(target({
      profileId: 'claude-work',
      model: 'claude-opus-5-5',
      modelSource: 'profile_default_tier',
      modelTierResolution: { effortSource: 'profile_default_tier' },
      effort: 'high',
      task: { runtime: { providerProfileRef: 'claude-work' } },
    }))?.selection).toEqual({});
  });

  it('keeps a legacy flat pair that was authored', () => {
    expect(savedScheduleAgent(target({
      model: 'pinned-model',
      effort: 'low',
      workflow: { instructions: 'Run' },
    }))).toEqual({ profileId: '', harness: '', selection: { model: 'pinned-model', effort: 'low' } });
  });

  it('offers no agent for tool-only work or another workflow type', () => {
    expect(savedScheduleAgent(target({ workflow: { runtime: { mode: 'omnigent', authored: false } } }))).toBeNull();
    expect(savedScheduleAgent(target({ repository: 'a/b' }))).toBeNull();
    expect(savedScheduleAgent({ kind: 'queue_task', job: { payload: {} } })).toBeNull();
  });
});

describe('scheduleAgentPatch', () => {
  const saved = { profileId: 'claude-work', harness: '', selection: { modelTier: 2 } };

  it('sends nothing for an untouched form', () => {
    expect(scheduleAgentPatch(saved, { ...saved, harness: 'claude_code' })).toBeNull();
  });

  it('sends a tier without concrete overrides', () => {
    expect(scheduleAgentPatch(saved, { ...saved, selection: { modelTier: 1 } })).toEqual({ modelTier: 1 });
  });

  it('sends Custom as the complete nullable pair', () => {
    expect(scheduleAgentPatch(saved, { ...saved, selection: { model: 'x', effort: null } }))
      .toEqual({ model: 'x', effort: null });
    expect(scheduleAgentPatch(
      { ...saved, selection: {} },
      { ...saved, selection: { model: null, effort: null } },
    )).toEqual({ model: null, effort: null });
  });

  it('sends only a changed profile and keeps the saved selection implicit', () => {
    expect(scheduleAgentPatch(saved, { ...saved, profileId: 'codex-work' })).toEqual({ profileId: 'codex-work' });
    expect(scheduleAgentPatch(saved, { ...saved, profileId: '' })).toBeNull();
  });
});
