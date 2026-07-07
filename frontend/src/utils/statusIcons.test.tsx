import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import {
  CANONICAL_STEP_STATUSES,
  StatusIcon,
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

  it('normalizes legacy persisted statuses before selecting icons', () => {
    expect(statusIconKey('running', 'step')).toBe('executing');
    expect(statusIconKey('succeeded', 'step')).toBe('completed');
    expect(statusIconKey('running', 'workflow')).toBe('executing');
    expect(statusIconKey('succeeded', 'workflow')).toBe('completed');
  });

  it('uses step-domain styling for step icons', () => {
    render(<StatusIcon status="ready" domain="step" data-testid="ready-step-icon" />);

    const className = screen.getByTestId('ready-step-icon').className;
    expect(className).toContain('status');
    expect(className).toContain('status-scheduled');
  });

  it('does not style artifact-only statuses as canonical step icons', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});

    render(<StatusIcon status="succeeded" domain="step" data-testid="succeeded-step-icon" />);

    const className = screen.getByTestId('succeeded-step-icon').className;
    expect(className).toContain('status');
    expect(className).toContain('status-neutral');
    expect(warn).toHaveBeenCalledWith('Unknown step ledger status: succeeded');
    warn.mockRestore();
  });
});
