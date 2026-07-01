import { describe, expect, it } from 'vitest';

import {
  CANONICAL_STEP_STATUSES,
  statusIconKey,
} from './statusIcons';

describe('statusIcons', () => {
  it('maps canonical MM-1085 step statuses to shared workflow icon keys', () => {
    expect(statusIconKey('pending', 'step')).toBe('waiting_on_dependencies');
    expect(statusIconKey('ready', 'step')).toBe('awaiting_slot');
    expect(statusIconKey('executing', 'step')).toBe('executing');
    expect(statusIconKey('awaiting_external', 'step')).toBe('awaiting_external');
    expect(statusIconKey('reviewing', 'step')).toBe('proposals');
    expect(statusIconKey('completed', 'step')).toBe('completed');
    expect(statusIconKey('failed', 'step')).toBe('failed');
    expect(statusIconKey('skipped', 'step')).toBe('canceled');
    expect(statusIconKey('canceled', 'step')).toBe('canceled');
  });

  it('keeps removed step status tokens out of the canonical step domain', () => {
    expect(CANONICAL_STEP_STATUSES).not.toContain('running');
    expect(CANONICAL_STEP_STATUSES).not.toContain('succeeded');
  });
});
