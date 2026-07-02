import { describe, expect, it } from 'vitest';

import {
  STEP_EXECUTION_STATUS_KEYS,
  STEP_LEDGER_STATUS_KEYS,
  formatStepStatusLabel,
  stepStatusPillProps,
} from './stepStatus';

describe('step status helpers', () => {
  it('covers every canonical step ledger status', () => {
    expect(STEP_LEDGER_STATUS_KEYS).toEqual([
      'pending',
      'ready',
      'executing',
      'awaiting_external',
      'reviewing',
      'completed',
      'failed',
      'skipped',
      'canceled',
    ]);

    for (const status of STEP_LEDGER_STATUS_KEYS) {
      expect(formatStepStatusLabel(status, 'Unknown')).not.toBe('Unknown');
      expect(stepStatusPillProps(status).className).toContain('status');
    }
  });

  it('covers every step execution artifact status accepted by step helpers', () => {
    expect(STEP_EXECUTION_STATUS_KEYS).toEqual([
      'pending',
      'preparing',
      'executing',
      'running',
      'checking',
      'completed',
      'succeeded',
      'failed',
      'blocked',
      'canceled',
      'superseded',
    ]);

    for (const status of STEP_EXECUTION_STATUS_KEYS) {
      expect(formatStepStatusLabel(status, 'Unknown')).not.toBe('Unknown');
      expect(stepStatusPillProps(status).className).toContain('status');
    }
  });

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
