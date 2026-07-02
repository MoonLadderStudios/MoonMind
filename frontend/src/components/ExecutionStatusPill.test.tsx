import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ExecutionStatusPill } from './ExecutionStatusPill';

describe('ExecutionStatusPill', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('keeps step statuses visible when they are not workflow lifecycle states', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});

    render(
      <>
        <ExecutionStatusPill status="ready" />
        <ExecutionStatusPill status="checking" />
        <ExecutionStatusPill status="succeeded" />
      </>,
    );

    expect(screen.getByText('Ready').className).toContain('status-scheduled');
    expect(screen.getByText('Checking').className).toContain('status-awaiting-external');
    expect(screen.getByText('Succeeded').className).toContain('status-succeeded');
    expect(warn).not.toHaveBeenCalled();
  });

  it('still prefers workflow lifecycle styling when domains overlap', () => {
    render(<ExecutionStatusPill status="awaiting_external" />);

    expect(screen.getByText('Awaiting external').className).toContain('status-awaiting-external');
  });
});
