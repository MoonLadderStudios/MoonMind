import { describe, expect, it } from 'vitest';

import { formatStepStatusLabel, stepStatusPillProps } from './stepStatus';

describe('step status helpers', () => {
  it('formats step ledger and execution statuses with visible labels', () => {
    expect(formatStepStatusLabel('ready')).toBe('Ready');
    expect(formatStepStatusLabel('reviewing')).toBe('Reviewing');
    expect(formatStepStatusLabel('succeeded')).toBe('Succeeded');
    expect(formatStepStatusLabel('skipped')).toBe('Skipped');
    expect(formatStepStatusLabel('preparing')).toBe('Preparing');
    expect(formatStepStatusLabel('checking')).toBe('Checking');
  });

  it('returns known status pill classes without accepting prototype keys', () => {
    expect(stepStatusPillProps('ready')).toEqual({ className: 'status status-scheduled' });
    expect(stepStatusPillProps('checking')).toEqual({ className: 'status status-awaiting-external' });
    expect(stepStatusPillProps('succeeded')).toEqual({ className: 'status status-succeeded' });
    expect(stepStatusPillProps('constructor')).toEqual({ className: 'status status-neutral' });
    expect(formatStepStatusLabel('constructor', 'Unknown')).toBe('Unknown');
  });
});
