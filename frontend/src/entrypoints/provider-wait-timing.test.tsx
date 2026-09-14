import { describe, it, expect } from 'vitest';

import {
  formatProviderWaitTiming,
  formatWaitElapsedSince,
  parseProviderCooldownUntil,
  parseProviderNextCheck,
  parseProviderQueueFresh,
  parseProviderQueueOrdered,
  parseProviderQueuePosition,
  parseProviderWaitEnteredAt,
  ProviderWaitDetails,
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

  it('parses legacy cooldown deadlines without inventing immediate admission', () => {
    expect(
      parseProviderCooldownUntil('Waiting for provider cooldown; cooldown_until=2026-09-14T08:00:00+00:00.'),
    ).toBe('2026-09-14T08:00:00+00:00');
    expect(parseProviderCooldownUntil('awaiting_provider_capacity; queue_position=3')).toBeNull();
    expect(parseProviderCooldownUntil(null)).toBeNull();
  });

  it('parses canonical cooldown fragments surfaced through the parent signal', () => {
    const reason =
      'provider_cooldown; queue_position=2; cooldown_until=2026-09-14T08:00:00+00:00';
    expect(parseProviderQueuePosition(reason)).toBe(2);
    expect(parseProviderCooldownUntil(reason)).toBe('2026-09-14T08:00:00+00:00');
    const labels = formatProviderWaitTiming({
      queuePosition: parseProviderQueuePosition(reason),
      queueOrdered: true,
      queueFresh: true,
      cooldownUntil: parseProviderCooldownUntil(reason),
      nextCheck: null,
    });
    expect(labels.queueLabel).toBe('Queue position 2 (ordered snapshot)');
    expect(labels.cooldownLabel).toBe('Cooldown until 2026-09-14T08:00:00+00:00');
    // A missing deadline never renders as immediate admission.
    expect(parseProviderCooldownUntil('provider_cooldown')).toBeNull();
  });

  it('surfaces structured cooldown/next-check/elapsed without implying an ETA', () => {
    expect(ProviderWaitDetails).toBeDefined();
    // Structured observation fields flow through the honest timing labels;
    // a missing deadline never renders as immediate admission.
    const labels = formatProviderWaitTiming({
      queuePosition: 2,
      queueOrdered: true,
      queueFresh: true,
      cooldownUntil: '2026-09-14T08:00:00+00:00',
      nextCheck: '2026-09-14T07:01:00+00:00',
    });
    expect(labels.queueLabel).toBe('Queue position 2 (ordered snapshot)');
    expect(labels.cooldownLabel).toBe('Cooldown until 2026-09-14T08:00:00+00:00');
    expect(labels.nextCheckLabel ?? '').toContain('not a promised start time');
  });

  it('propagates authoritative ordered/fresh flags instead of inferring them', () => {
    // Explicit attestations travel as fragments in the parent reason.
    const attested =
      'awaiting_provider_capacity; queue_position=2; queue_ordered=1; queue_fresh=1';
    expect(parseProviderQueueOrdered(attested)).toBe(true);
    expect(parseProviderQueueFresh(attested)).toBe(true);
    // Legacy reasons without flag fragments keep the historical inference
    // path (position presence implies the ordered precondition).
    const legacy = 'awaiting_provider_capacity; queue_position=2';
    expect(parseProviderQueueOrdered(legacy)).toBeNull();
    expect(parseProviderQueueFresh(legacy)).toBeNull();
    expect(parseProviderQueuePosition(legacy)).toBe(2);
    // An unordered queue never lends its index as a display position and
    // carries no attestation flags.
    const unordered = 'awaiting_provider_capacity';
    expect(parseProviderQueueOrdered(unordered)).toBeNull();
    expect(parseProviderQueueFresh(unordered)).toBeNull();
    expect(parseProviderQueuePosition(unordered)).toBeNull();
  });

  it('parses next-check and wait-entered fragments without implying an ETA', () => {
    const reason =
      'provider_cooldown; cooldown_until=2026-09-14T08:00:00+00:00; next_check=2026-09-14T07:01:00+00:00; wait_entered_at=2026-09-14T07:00:00+00:00';
    expect(parseProviderNextCheck(reason)).toBe('2026-09-14T07:01:00+00:00');
    expect(parseProviderWaitEnteredAt(reason)).toBe('2026-09-14T07:00:00+00:00');
    expect(parseProviderNextCheck('awaiting_provider_capacity')).toBeNull();
    expect(parseProviderWaitEnteredAt('awaiting_provider_capacity')).toBeNull();
    // Unknown stays unknown: no fragment means no next-check label.
    expect(
      formatProviderWaitTiming({ nextCheck: parseProviderNextCheck('awaiting_provider_capacity') })
        .nextCheckLabel,
    ).toBeNull();
  });

  it('formats honest elapsed wait from the observed entry time', () => {
    expect(
      formatWaitElapsedSince('2026-09-14T07:00:00+00:00', Date.parse('2026-09-14T07:00:45+00:00')),
    ).toBe('45s');
    expect(
      formatWaitElapsedSince('2026-09-14T07:00:00+00:00', Date.parse('2026-09-14T07:02:30+00:00')),
    ).toBe('2m 30s');
    // Unparseable or future entry times never fabricate elapsed wait.
    expect(formatWaitElapsedSince('not-a-timestamp', Date.parse('2026-09-14T07:00:45+00:00'))).toBeNull();
    expect(
      formatWaitElapsedSince('2026-09-14T08:00:00+00:00', Date.parse('2026-09-14T07:00:00+00:00')),
    ).toBeNull();
    expect(formatWaitElapsedSince(null)).toBeNull();
  });
});
