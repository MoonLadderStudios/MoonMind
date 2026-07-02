import { describe, expect, it } from 'vitest';

import { INTEGRATION_STATUS_KEYS, formatIntegrationStatusLabel } from './integrationStatus';

describe('integration status helpers', () => {
  it('covers every provider-normalized integration status', () => {
    expect(INTEGRATION_STATUS_KEYS).toEqual([
      'queued',
      'running',
      'completed',
      'failed',
      'canceled',
      'unknown',
    ]);

    for (const status of INTEGRATION_STATUS_KEYS) {
      expect(formatIntegrationStatusLabel(status, 'Missing')).not.toBe('Missing');
    }
  });

  it('keeps provider extension labels at the integration boundary', () => {
    expect(formatIntegrationStatusLabel('awaiting_feedback')).toBe('Awaiting feedback');
    expect(formatIntegrationStatusLabel('awaiting_slot', 'Missing')).toBe('Missing');
    expect(formatIntegrationStatusLabel('no_commit', 'Missing')).toBe('Missing');
  });
});
