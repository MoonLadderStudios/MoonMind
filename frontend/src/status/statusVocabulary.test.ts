import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  CHECKPOINT_BRANCH_STATUS,
  CHECKPOINT_BRANCH_TURN_STATUS,
  DEPENDENCY_OUTCOME_STATUS,
  STEP_CHECK_STATUS,
  type StatusVocabulary,
} from './statusVocabulary';

// Values the API emits for each domain (moonmind/statuses/checkpoint_branch.py,
// the MoonMind.Run dependency gate and step-ledger check results).
const EMITTED: Array<[string, StatusVocabulary, string[]]> = [
  ['checkpoint branch', CHECKPOINT_BRANCH_STATUS, [
    'created', 'preparing', 'active', 'blocked', 'failed', 'succeeded', 'promotable', 'promoted', 'archived', 'superseded',
  ]],
  ['checkpoint branch turn', CHECKPOINT_BRANCH_TURN_STATUS, [
    'created', 'preparing', 'running', 'checking', 'succeeded', 'failed', 'blocked', 'canceled', 'superseded',
  ]],
  ['dependency outcome', DEPENDENCY_OUTCOME_STATUS, [
    'terminated', 'timed_out', 'bypassed', 'unknown', 'waiting_for_successful_rerun',
  ]],
  ['step check', STEP_CHECK_STATUS, ['pending', 'passed', 'failed', 'inconclusive']],
];

describe('status vocabularies', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it.each(EMITTED)('labels every emitted %s status without warning', (_domain, vocabulary, values) => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    for (const value of values) {
      expect(vocabulary.label(value)).not.toBe('-');
      expect(vocabulary.pillProps(value).className).toMatch(/^status status-/);
    }
    expect(warn).not.toHaveBeenCalled();
  });

  it('gives outcomes tones that match their meaning', () => {
    expect(CHECKPOINT_BRANCH_STATUS.pillProps('promoted').className).toBe('status status-completed');
    expect(CHECKPOINT_BRANCH_STATUS.pillProps('failed').className).toBe('status status-failed');
    expect(CHECKPOINT_BRANCH_TURN_STATUS.pillProps('running').className).toBe('status status-running');
    expect(DEPENDENCY_OUTCOME_STATUS.label('waiting_for_successful_rerun')).toBe('Waiting for rerun');
    expect(DEPENDENCY_OUTCOME_STATUS.pillProps('timed_out').className).toBe('status status-failed');
    expect(STEP_CHECK_STATUS.label('passed')).toBe('Passed');
  });

  it('humanizes an unlisted status on a neutral pill and warns once per value', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});

    expect(CHECKPOINT_BRANCH_STATUS.label('awaiting_review')).toBe('Awaiting review');
    expect(CHECKPOINT_BRANCH_STATUS.pillProps('awaiting_review').className).toBe('status status-neutral');
    CHECKPOINT_BRANCH_STATUS.label('awaiting_review');

    expect(warn).toHaveBeenCalledTimes(1);
    expect(warn).toHaveBeenCalledWith('Unknown checkpoint branch status: awaiting_review');
  });

  it('labels a missing status as unknown without warning', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});

    expect(CHECKPOINT_BRANCH_TURN_STATUS.label(null)).toBe('Unknown');
    expect(CHECKPOINT_BRANCH_TURN_STATUS.pillProps('').className).toBe('status status-neutral');
    expect(warn).not.toHaveBeenCalled();
  });
});
