import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';

import type { BootPayload } from '../boot/parseBootPayload';
import { renderWithClient } from '../utils/test-utils';
import { SkillsPage } from './skills';

describe('Skills Entrypoint', () => {
  const mockPayload: BootPayload = {
    page: 'skills',
    apiBase: '/api',
  };

  let fetchSpy: MockInstance;

  beforeEach(() => {
    window.history.pushState({}, 'Skills', '/tasks/skills');
    let listCallCount = 0;
    fetchSpy = vi.spyOn(window, 'fetch').mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith('/api/tasks/skills') && !url.includes('includeContent=true')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            items: { worker: ['speckit-orchestrate', 'pr-resolver'] },
            legacyItems: [],
          }),
        } as Response);
      }
      if (url.startsWith('/api/tasks/skills?includeContent=true')) {
        listCallCount += 1;
        return Promise.resolve({
          ok: true,
          json: async () => ({
            items: { worker: ['speckit-orchestrate', 'pr-resolver', ...(listCallCount > 1 ? ['fresh-skill'] : [])] },
            legacyItems: [
              { id: 'speckit-orchestrate', markdown: '# Speckit\n\nExisting worker skill.' },
              { id: 'pr-resolver', markdown: '# PR Resolver\n\nResolves pull requests.' },
              ...(listCallCount > 1
                ? [{ id: 'fresh-skill', markdown: '# Fresh Skill\n\nCreated from the UI.' }]
                : []),
            ],
          }),
        } as Response);
      }
      if (url === '/api/tasks/skills') {
        return Promise.resolve({
          ok: true,
          json: async () => ({ status: 'success' }),
        } as Response);
      }
      return Promise.resolve({
        ok: false,
        status: 404,
        text: async () => 'Unhandled fetch',
      } as Response);
    });
    // Provide the global marked surface that the template normally loads.
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (window as any).marked = {
      parse(markdown: string) {
        const [heading = '', ...rest] = markdown.split('\n');
        return `<h1>${heading.replace(/^#\s*/, '')}</h1><p>${rest.join(' ').trim()}</p>`;
      },
    };
  });

  afterEach(() => {
    fetchSpy.mockRestore();
  });

  it('lists skills and previews markdown for the selected item', async () => {
    renderWithClient(<SkillsPage payload={mockPayload} />);

    fireEvent.click(await screen.findByRole('button', { name: 'speckit-orchestrate' }));

    await waitFor(() => {
      expect(screen.getByText('Speckit')).toBeTruthy();
      expect(screen.getByText('Existing worker skill.')).toBeTruthy();
    });
  });

  it('creates a new skill, refreshes the list, and selects the created skill', async () => {
    renderWithClient(<SkillsPage payload={mockPayload} />);

    fireEvent.click(await screen.findByRole('button', { name: 'Create New Skill' }));
    fireEvent.change(screen.getByLabelText('Skill Name'), {
      target: { value: 'fresh-skill' },
    });
    fireEvent.change(screen.getByLabelText('Skill Markdown'), {
      target: { value: '# Fresh Skill\n\nCreated from the UI.' },
    });

    fireEvent.click(screen.getByRole('button', { name: 'Save Skill' }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/tasks/skills',
        expect.objectContaining({
          method: 'POST',
        }),
      );
    });

    const createCall = fetchSpy.mock.calls.find(
      ([url, init]) => String(url) === '/api/tasks/skills' && init?.method === 'POST',
    );
    expect(createCall).toBeTruthy();
    expect(JSON.parse(String(createCall![1]?.body))).toEqual({
      name: 'fresh-skill',
      markdown: '# Fresh Skill\n\nCreated from the UI.',
    });

    await waitFor(() => {
      expect(screen.getByText('Fresh Skill')).toBeTruthy();
      expect(screen.getByText('Created from the UI.')).toBeTruthy();
    });
  });

  it('sanitizes rendered markdown before injecting preview HTML', async () => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (window as any).marked = {
      parse() {
        return '<h1>Unsafe</h1><p><img src="x" onerror="alert(1)"></p><a href="javascript:alert(1)">click</a>';
      },
    };
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith('/api/tasks/skills?includeContent=true')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            items: { worker: ['unsafe-skill'] },
            legacyItems: [
              {
                id: 'unsafe-skill',
                markdown: '# Unsafe\n\n<img src=x onerror="alert(1)"> [click](javascript:alert(1))',
              },
            ],
          }),
        } as Response);
      }
      if (url.startsWith('/api/tasks/skills')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            items: { worker: ['unsafe-skill'] },
            legacyItems: [],
          }),
        } as Response);
      }
      return Promise.resolve({
        ok: false,
        status: 404,
        text: async () => 'Unhandled fetch',
      } as Response);
    });

    renderWithClient(<SkillsPage payload={mockPayload} />);

    fireEvent.click(await screen.findByRole('button', { name: 'unsafe-skill' }));

    await waitFor(() => {
      const preview = document.querySelector('[class*="leading-7"]');
      expect(preview?.innerHTML).not.toContain('onerror');
      expect(preview?.innerHTML).not.toContain('javascript:alert(1)');
      expect(preview?.innerHTML).not.toContain('<img');
    });
  });
});
