import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ExecutionStatusPill, StepExecutionStatusPill, WorkflowLifecycleStatusPill } from './ExecutionStatusPill';

describe('ExecutionStatusPill', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('keeps step ledger statuses visible when they are not workflow lifecycle states', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});

    render(
      <>
        <ExecutionStatusPill status="ready" />
        <ExecutionStatusPill status="reviewing" />
        <ExecutionStatusPill status="completed" />
      </>,
    );

    expect(screen.getByText('Ready').className).toContain('status-scheduled');
    expect(screen.getByText('Reviewing').className).toContain('status-awaiting-external');
    expect(screen.getByText('Completed').className).toContain('status-completed');
    expect(warn).not.toHaveBeenCalled();
  });

  it('keeps integration statuses visible when they are not workflow or step states', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});

    render(
      <>
        <ExecutionStatusPill status="queued" />
        <ExecutionStatusPill status="awaiting_feedback" />
      </>,
    );

    expect(screen.getByText('Queued').className).toContain('status-scheduled');
    expect(screen.getByText('Awaiting feedback').className).toContain('status-awaiting-external');
    expect(warn).not.toHaveBeenCalled();
  });

  it('still prefers workflow lifecycle styling when domains overlap', () => {
    render(<ExecutionStatusPill status="awaiting_external" />);

    expect(screen.getByText('Awaiting external').className).toContain('status-awaiting-external');
  });

  it('keeps step execution artifact statuses visible in execution history', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});

    render(
      <>
        <StepExecutionStatusPill status="running" />
        <StepExecutionStatusPill status="checking" />
        <StepExecutionStatusPill status="succeeded" />
      </>,
    );

    expect(screen.getByText('Running').className).toContain('status-running');
    expect(screen.getByText('Checking').className).toContain('status-running');
    expect(screen.getByText('Succeeded').className).toContain('status-completed');
    expect(warn).not.toHaveBeenCalled();
  });
});


it('shows a terminal continuation as handed off, preserving failures and ordinary completion', () => {
  const { rerender } = render(<WorkflowLifecycleStatusPill status="completed" completionDisposition="gated_continuation" />);
  expect(screen.getByText('Handed off').className).toContain('status-neutral');
  expect(screen.queryByText('Completed')).toBeNull();
  rerender(<WorkflowLifecycleStatusPill status="failed" completionDisposition="gated_continuation" />);
  expect(screen.getByText('Failed')).toBeTruthy();
  rerender(<WorkflowLifecycleStatusPill status="completed" />);
  expect(screen.getByText('Completed')).toBeTruthy();
});

it('shows a completed idle objective neutrally and preserves a continuation handoff', () => {
  const { rerender } = render(<WorkflowLifecycleStatusPill status="completed" objectiveOutcome="idle" />);
  expect(screen.getByText('Idle').className).toContain('status-neutral');
  expect(screen.queryByText('Completed')).toBeNull();

  rerender(<WorkflowLifecycleStatusPill status="completed" objectiveOutcome="idle" completionDisposition="gated_continuation" />);
  expect(screen.getByText('Handed off')).toBeTruthy();
  expect(screen.queryByText('Idle')).toBeNull();
});

it.each([
  ['failed', 'Failed', 'status-failed'],
  ['verification_blocked', 'Verification blocked', 'status-failed'],
  ['cancelled', 'Cancelled', 'status-canceled'],
])('shows terminal objective %s instead of a completed-success pill', (objectiveOutcome, label, statusClass) => {
  const { rerender } = render(<WorkflowLifecycleStatusPill status="completed" objectiveOutcome={objectiveOutcome} />);
  expect(screen.getByText(label).className).toContain(statusClass);
  expect(screen.queryByText('Completed')).toBeNull();

  rerender(<WorkflowLifecycleStatusPill status="completed" objectiveOutcome={objectiveOutcome} completionDisposition="gated_continuation" />);
  expect(screen.getByText('Handed off').className).toContain('status-neutral');
  expect(screen.queryByText(label)).toBeNull();
});

it.each(([
  ['failed', 'Failed'],
  ['canceled', 'Canceled'],
  ['executing', 'Executing'],
  ['no_commit', 'No commit'],
] as const).flatMap(([status, label]) => ['idle', 'failed', 'verification_blocked', 'cancelled'].map((objectiveOutcome) => [status, label, objectiveOutcome] as const)))('preserves %s/%s lifecycle presentation with stale objective %s', (status, label, objectiveOutcome) => {
  render(<WorkflowLifecycleStatusPill status={status} objectiveOutcome={objectiveOutcome} enableMotion={false} />);
  expect(screen.getByText(label)).toBeTruthy();
  expect(screen.queryByText('Idle')).toBeNull();
});
