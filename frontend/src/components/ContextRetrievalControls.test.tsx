import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ContextRetrievalControls } from './ContextRetrievalControls';
import {
  ContextRetrievalAuthoring,
  defaultContextRetrievalAuthoring,
} from '../lib/contextRetrievalAuthoring';

function renderControls(overrides?: Partial<ContextRetrievalAuthoring>) {
  let value: ContextRetrievalAuthoring = {
    ...defaultContextRetrievalAuthoring(),
    ...overrides,
  };
  const onChange = vi.fn((next: ContextRetrievalAuthoring) => {
    value = next;
  });
  const utils = render(
    <ContextRetrievalControls value={value} onChange={onChange} />,
  );
  return { ...utils, onChange, getValue: () => value };
}

describe('ContextRetrievalControls', () => {
  it('renders policy ceilings so the operator sees the boundary', () => {
    renderControls();
    expect(
      screen.getByText(/Policy ceilings/i).textContent,
    ).toContain('top_k ≤ 50');
  });

  it('enables follow-up retrieval through the toggle', () => {
    const { onChange } = renderControls();
    fireEvent.click(
      screen.getByLabelText(/request additional context during the run/i),
    );
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({
        followUp: expect.objectContaining({ enabled: true }),
      }),
    );
  });

  it('warns when follow-up retrieval is enabled without collections', () => {
    const value = defaultContextRetrievalAuthoring();
    value.followUp.enabled = true;
    render(<ContextRetrievalControls value={value} onChange={vi.fn()} />);
    expect(screen.getByText(/no collections are selected/i)).toBeTruthy();
  });

  it('hides initial-injection controls when showInitialControls is false', () => {
    render(
      <ContextRetrievalControls
        value={defaultContextRetrievalAuthoring()}
        onChange={vi.fn()}
        showInitialControls={false}
      />,
    );
    expect(screen.queryByText(/Initial context injection/i)).toBeNull();
    expect(screen.getByText(/In-session follow-up retrieval/i)).toBeTruthy();
  });
});
