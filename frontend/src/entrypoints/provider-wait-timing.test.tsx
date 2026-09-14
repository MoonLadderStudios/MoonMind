import { describe, it, expect } from 'vitest';

import {
  formatProviderWaitTiming,
  parseProviderQueuePosition,
} from './workflow-detail';

describe('provider wait timing labels (MoonLadderStudios/MoonMind#1130)', () => {
  it('renders queue position only from an ordered fresh snapshot', () => {
    expect(
      formatProviderWaitTiming({ queuePosition: 3, queueOrdered: true, queueFresh: true }).queueLabel,
    ).toBe('Queue position 3 (ordered snapshot)');
    expect(
      formatProviderWaitTiming({ queuePosition: 3, queueOrdered: false, queueFresh: true }).queueLabel,
    ).toBeNull();
    expect(
      formatProviderWaitTiming({ queuePosition: 3, queueOrdered: true, queueFresh: false }).queueLabel,
    ).toBeNull();
    expect(formatProviderWaitTiming({}).queueLabel).toBeNull();
  });

  it('renders authoritative cooldown deadlines and labels next-check timing honestly', () => {
    const labels = formatProviderWaitTiming({
      cooldownUntil: '2026-09-14T08:00:00+00:00',
      nextCheck: '2026-09-14T07:01:00+00:00',
    });
    expect(labels.cooldownLabel).toBe('Cooldown until 2026-09-14T08:00:00+00:00');
    expect(labels.nextCheckLabel ?? '').toContain('not a promised start time');
    const missing = formatProviderWaitTiming({});
    expect(missing.cooldownLabel).toBeNull();
    expect(missing.nextCheckLabel).toBeNull();
  });

  it('parses queue position from the canonical waiting reason without inventing one', () => {
    expect(parseProviderQueuePosition('awaiting_provider_capacity; queue_position=3')).toBe(3);
    expect(parseProviderQueuePosition('awaiting_provider_capacity')).toBeNull();
    expect(parseProviderQueuePosition(null)).toBeNull();
    expect(parseProviderQueuePosition('queue_position=0')).toBeNull();
  });
});
