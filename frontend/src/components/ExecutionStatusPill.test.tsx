import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { ExecutionStatusPill } from './ExecutionStatusPill';

describe('ExecutionStatusPill', () => {
  it('keeps step statuses visible when they are not workflow lifecycle states', () => {
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
  });

  it('still prefers workflow lifecycle styling when domains overlap', () => {
    render(<ExecutionStatusPill status="awaiting_external" />);

    expect(screen.getByText('Awaiting external').className).toContain('status-awaiting-external');
  });
});
