import { afterEach, describe, expect, it, vi } from 'vitest';

import { formatIntegrationStatusLabel, integrationStatusPillProps, isIntegrationStatus } from './integrationStatus';

describe('integration status helpers', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('formats provider normalized statuses with readable labels and classes', () => {
    expect(formatIntegrationStatusLabel('queued')).toBe('Queued');
    expect(formatIntegrationStatusLabel('running')).toBe('Running');
    expect(formatIntegrationStatusLabel('completed')).toBe('Completed');
    expect(formatIntegrationStatusLabel('failed')).toBe('Failed');
    expect(formatIntegrationStatusLabel('canceled')).toBe('Canceled');
    expect(formatIntegrationStatusLabel('unknown')).toBe('Unknown');
    expect(formatIntegrationStatusLabel('awaiting_feedback')).toBe('Awaiting feedback');

    expect(integrationStatusPillProps('queued')).toEqual({ className: 'status status-scheduled' });
    expect(integrationStatusPillProps('running')).toEqual({ className: 'status status-running' });
    expect(integrationStatusPillProps('completed')).toEqual({ className: 'status status-completed' });
    expect(integrationStatusPillProps('failed')).toEqual({ className: 'status status-failed' });
    expect(integrationStatusPillProps('canceled')).toEqual({ className: 'status status-canceled' });
    expect(integrationStatusPillProps('unknown')).toEqual({ className: 'status status-neutral' });
    expect(integrationStatusPillProps('awaiting_feedback')).toEqual({
      className: 'status status-awaiting-external',
    });

    expect(isIntegrationStatus('queued')).toBe(true);
    expect(isIntegrationStatus('awaiting_feedback')).toBe(true);
  });

  it('does not accept workflow lifecycle states as integration/provider statuses', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});

    for (const status of ['scheduled', 'awaiting_slot', 'waiting_on_dependencies', 'no_commit']) {
      expect(integrationStatusPillProps(status)).toEqual({ className: 'status status-neutral' });
      expect(formatIntegrationStatusLabel(status, 'Unknown')).toBe('Unknown');
      expect(isIntegrationStatus(status)).toBe(false);
    }

    expect(warn).toHaveBeenCalledWith('Unknown integration/provider status: scheduled');
    expect(warn).toHaveBeenCalledWith('Unknown integration/provider status: no_commit');
  });
});
