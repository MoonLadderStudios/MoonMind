import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ContextRetrievalControls } from './ContextRetrievalControls';
import { defaultContextRetrievalAuthoring } from '../lib/contextRetrievalAuthoring';

describe('ContextRetrievalControls (retired #4105)', () => {
  it('renders the retirement notice with no editable inputs', () => {
    const { container } = render(
      <ContextRetrievalControls
        value={defaultContextRetrievalAuthoring()}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByText(/retired/i)).toBeTruthy();
    expect(container.querySelectorAll('input, select, textarea').length).toBe(0);
  });

  it('renders no hidden retrieval state', () => {
    const { container } = render(
      <ContextRetrievalControls
        value={defaultContextRetrievalAuthoring()}
        onChange={vi.fn()}
      />,
    );
    expect(container.querySelector('input[type="hidden"]')).toBeNull();
  });
});
