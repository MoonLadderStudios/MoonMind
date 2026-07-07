import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ExecutionStatusPill, StepExecutionStatusPill } from './ExecutionStatusPill';

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
