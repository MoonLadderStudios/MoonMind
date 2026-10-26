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
    expect(statusIconKey('reviewing', 'step')).toBe('finalizing');
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

  it.each([
    [{ objectiveOutcome: 'failed' }, 'Failed', 'status-failed', 'lucide-x'],
    [{ objectiveOutcome: 'verification_blocked' }, 'Verification blocked', 'status-failed', 'lucide-x'],
    [{ objectiveOutcome: 'cancelled' }, 'Cancelled', 'status-canceled', 'lucide-ban'],
    [{ objectiveOutcome: 'idle' }, 'Idle', 'status-neutral', 'lucide-minus'],
    [{ objectiveOutcome: 'unknown' }, 'Outcome unknown', 'status-neutral', 'lucide-circle-question-mark'],
    [{ objectiveOutcome: 'failed', completionDisposition: 'gated_continuation' }, 'Handed off', 'status-neutral', 'lucide-forward'],
  ] as const)('shows the completed workflow outcome %j instead of a success check', (outcome, label, statusClass, iconClass) => {
    render(<StatusIcon status="completed" domain="workflow" {...outcome} data-testid="outcome-icon" />);

    const icon = screen.getByTestId('outcome-icon');
    expect(icon.getAttribute('aria-label')).toBe(`Status: ${label}`);
    expect(icon.className).toContain(statusClass);
    expect(icon.querySelector('svg')?.getAttribute('class')).toContain(iconClass);
    expect(icon.querySelector('svg')?.getAttribute('class')).not.toContain('lucide-check');
  });

  it('keeps the success check for a succeeded objective and the lifecycle icon for non-completed workflows', () => {
    render(
      <>
        <StatusIcon status="completed" domain="workflow" objectiveOutcome="succeeded" data-testid="succeeded" />
        <StatusIcon status="failed" domain="workflow" objectiveOutcome="idle" data-testid="lifecycle-failed" />
      </>,
    );

    expect(screen.getByTestId('succeeded').querySelector('svg')?.getAttribute('class')).toContain('lucide-check');
    expect(screen.getByTestId('succeeded').className).toContain('status-completed');
    expect(screen.getByTestId('lifecycle-failed').querySelector('svg')?.getAttribute('class')).toContain('lucide-x');
    expect(screen.getByTestId('lifecycle-failed').getAttribute('aria-label')).toBe('Status: Failed');
  });
});
