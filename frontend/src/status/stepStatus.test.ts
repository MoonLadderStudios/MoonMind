import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  STEP_EXECUTION_STATUS_KEYS,
  STEP_LEDGER_STATUS_KEYS,
  formatStepStatusLabel,
  stepStatusPillProps,
} from './stepStatus';

describe('step status helpers', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

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
    expect(formatStepStatusLabel('pending')).toBe('Pending');
    expect(formatStepStatusLabel('ready')).toBe('Ready');
    expect(formatStepStatusLabel('running')).toBe('Running');
    expect(formatStepStatusLabel('awaiting_external')).toBe('Awaiting external');
    expect(formatStepStatusLabel('reviewing')).toBe('Reviewing');
    expect(formatStepStatusLabel('succeeded')).toBe('Succeeded');
    expect(formatStepStatusLabel('failed')).toBe('Failed');
    expect(formatStepStatusLabel('skipped')).toBe('Skipped');
    expect(formatStepStatusLabel('preparing')).toBe('Preparing');
    expect(formatStepStatusLabel('checking')).toBe('Checking');

    expect(stepStatusPillProps('pending')).toEqual({ className: 'status status-scheduled' });
    expect(stepStatusPillProps('ready')).toEqual({ className: 'status status-scheduled' });
    expect(stepStatusPillProps('running')).toEqual({ className: 'status status-running' });
    expect(stepStatusPillProps('awaiting_external')).toEqual({ className: 'status status-awaiting-external' });
    expect(stepStatusPillProps('reviewing')).toEqual({ className: 'status status-awaiting-external' });
    expect(stepStatusPillProps('succeeded')).toEqual({ className: 'status status-succeeded' });
    expect(stepStatusPillProps('failed')).toEqual({ className: 'status status-failed' });
    expect(stepStatusPillProps('skipped')).toEqual({ className: 'status status-neutral' });
    expect(stepStatusPillProps('preparing')).toEqual({ className: 'status status-running' });
    expect(stepStatusPillProps('checking')).toEqual({ className: 'status status-awaiting-external' });
  });

  it('rejects workflow-only states and prototype keys with neutral diagnostics', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});

    for (const status of [
      'scheduled',
      'initializing',
      'waiting_on_dependencies',
      'planning',
      'awaiting_slot',
      'proposals',
      'finalizing',
      'no_commit',
    ]) {
      expect(stepStatusPillProps(status)).toEqual({ className: 'status status-neutral' });
      expect(formatStepStatusLabel(status, 'Unknown')).toBe('Unknown');
    }
    expect(stepStatusPillProps('constructor')).toEqual({ className: 'status status-neutral' });
    expect(formatStepStatusLabel('constructor', 'Unknown')).toBe('Unknown');

    expect(warn).toHaveBeenCalledWith('Unknown step ledger status: scheduled');
    expect(warn).toHaveBeenCalledWith('Unknown step ledger status: no_commit');
  });
});
