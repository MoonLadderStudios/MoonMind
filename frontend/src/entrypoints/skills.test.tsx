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
    window.history.pushState({}, 'Skills', '/skills');
    let listCallCount = 0;
    fetchSpy = vi.spyOn(window, 'fetch').mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/skills/imports' && init?.method === 'POST') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            status: 'saved',
            name: 'zip-skill',
            skill_id: 'zip-skill',
            version_id: 'zip-skill-v1',
            version_number: 1,
            warnings: [],
          }),
        } as Response);
      }
      if (url.startsWith('/api/workflows/skills') && !url.includes('includeContent=true')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            items: { worker: ['speckit-orchestrate', 'pr-resolver'] },
            legacyItems: [],
          }),
        } as Response);
      }
      if (url.startsWith('/api/workflows/skills?includeContent=true')) {
        listCallCount += 1;
        return Promise.resolve({
          ok: true,
          json: async () => ({
            items: {
              worker: [
                'speckit-orchestrate',
                'pr-resolver',
                ...(listCallCount > 1 ? ['fresh-skill', 'zip-skill'] : []),
              ],
            },
            legacyItems: [
              { id: 'speckit-orchestrate', markdown: '# Speckit\n\nExisting **worker** skill.\n\n- Plans\n- Tests' },
              { id: 'pr-resolver', markdown: '# PR Resolver\n\nResolves pull requests.' },
              ...(listCallCount > 1
                ? [
                    { id: 'fresh-skill', markdown: '# Fresh Skill\n\nCreated from the UI.' },
                    { id: 'zip-skill', markdown: '# Zip Skill\n\nUploaded from a zip.' },
                  ]
                : []),
            ],
          }),
        } as Response);
      }
      if (url === '/api/workflows/skills') {
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
  });

  afterEach(() => {
    fetchSpy.mockRestore();
  });

  it('renders page-matched loading placeholders for skill catalog and preview regions', () => {
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith('/api/workflows/skills?includeContent=true')) {
        return new Promise(() => {}) as Promise<Response>;
      }
      return Promise.resolve({
        ok: false,
        status: 404,
        text: async () => 'Unhandled fetch',
      } as Response);
    });

    renderWithClient(<SkillsPage payload={mockPayload} />);

    expect(screen.getByRole('heading', { name: 'Skills' })).toBeTruthy();
    expect(screen.getByRole('status', { name: 'Skills catalog loading placeholder' })).toBeTruthy();
    expect(screen.getByRole('status', { name: 'Skills preview loading placeholder' })).toBeTruthy();
    expect(screen.getByTestId('loading-placeholder-catalog')).toBeTruthy();
  });

  it('lists skills and previews markdown for the selected item', async () => {
    renderWithClient(<SkillsPage payload={mockPayload} />);

    fireEvent.click(await screen.findByRole('button', { name: 'speckit-orchestrate' }));

    await waitFor(() => {
      expect(screen.getByText('Speckit')).toBeTruthy();
      expect(screen.getByText('worker')).toBeTruthy();
      expect(screen.getByText('Plans')).toBeTruthy();
      expect(document.querySelector('strong')?.textContent).toBe('worker');
    });
  });

  it('renders inline markdown inside list items and preserves code language classes', async () => {
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith('/api/workflows/skills?includeContent=true')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            items: { worker: ['markdown-skill'] },
            legacyItems: [
              {
                id: 'markdown-skill',
                markdown: '- **bold** item with [docs](/docs)\n\n```ts\nconst ok = true;\n```',
              },
            ],
          }),
        } as Response);
      }
      if (url.startsWith('/api/workflows/skills')) {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            items: { worker: ['markdown-skill'] },
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

    fireEvent.click(await screen.findByRole('button', { name: 'markdown-skill' }));

    await waitFor(() => {
      const preview = screen.getByTestId('skill-markdown-preview');
      const listItem = preview.querySelector('li');
      expect(listItem?.querySelector('strong')?.textContent).toBe('bold');
      expect(listItem?.querySelector('a')?.getAttribute('href')).toBe('/docs');
      expect(preview.querySelector('code.language-ts')?.textContent).toBe('const ok = true;');
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
        '/api/workflows/skills',
        expect.objectContaining({
          method: 'POST',
        }),
      );
    });

    const createCall = fetchSpy.mock.calls.find(
      ([url, init]) => String(url) === '/api/workflows/skills' && init?.method === 'POST',
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

  it('uploads a skill zip, refreshes the list, and selects the uploaded skill', async () => {
    renderWithClient(<SkillsPage payload={mockPayload} />);

    fireEvent.click(await screen.findByRole('button', { name: 'Create New Skill' }));
    const file = new File(['zip-content'], 'zip-skill.zip', { type: 'application/zip' });
    fireEvent.change(screen.getByLabelText('Skill Zip'), {
      target: { files: [file] },
    });

    fireEvent.click(screen.getByRole('button', { name: 'Upload Zip' }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith(
        '/api/skills/imports',
        expect.objectContaining({
          method: 'POST',
          body: expect.any(FormData),
        }),
      );
    });

    await waitFor(() => {
      expect(screen.getByText('Zip Skill')).toBeTruthy();
      expect(screen.getByText('Uploaded from a zip.')).toBeTruthy();
    });
  });

  it('filters available skills by ID', async () => {
    renderWithClient(<SkillsPage payload={mockPayload} />);

    expect(await screen.findByRole('button', { name: 'speckit-orchestrate' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'pr-resolver' })).toBeTruthy();

    fireEvent.change(screen.getByLabelText('Filter skills by ID'), {
      target: { value: 'pr-' },
    });

    await waitFor(() => {
      expect(screen.queryByRole('button', { name: 'speckit-orchestrate' })).toBeNull();
      expect(screen.getByRole('button', { name: 'pr-resolver' })).toBeTruthy();
    });

    fireEvent.change(screen.getByLabelText('Filter skills by ID'), {
      target: { value: 'does-not-exist' },
    });

    await waitFor(() => {
      expect(screen.getByText('No skills match your filter.')).toBeTruthy();
    });
  });

  it('shows the raw markdown and metadata preview tabs', async () => {
    renderWithClient(<SkillsPage payload={mockPayload} />);

    fireEvent.click(await screen.findByRole('button', { name: 'speckit-orchestrate' }));

    fireEvent.click(await screen.findByRole('tab', { name: 'Raw Markdown' }));
    await waitFor(() => {
      expect(screen.getByTestId('skill-raw-markdown').textContent).toContain('# Speckit');
    });

    fireEvent.click(screen.getByRole('tab', { name: 'Metadata' }));
    await waitFor(() => {
      const metadata = screen.getByTestId('skill-metadata');
      expect(metadata.textContent).toContain('speckit-orchestrate');
    });
  });

  it('validates the skill name before submitting a create request', async () => {
    renderWithClient(<SkillsPage payload={mockPayload} />);

    fireEvent.click(await screen.findByRole('button', { name: 'Create New Skill' }));
    fireEvent.change(screen.getByLabelText('Skill Markdown'), {
      target: { value: '# Body\n\nNeeds a name.' },
    });

    fireEvent.click(screen.getByRole('button', { name: 'Save Skill' }));

    await waitFor(() => {
      expect(screen.getByText('Skill name is required.')).toBeTruthy();
    });

    fireEvent.change(screen.getByLabelText('Skill Name'), {
      target: { value: 'bad name!' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save Skill' }));

    await waitFor(() => {
      expect(
        screen.getByText('Skill name may only contain letters, numbers, dots, dashes, and underscores.'),
      ).toBeTruthy();
    });

    const createCall = fetchSpy.mock.calls.find(
      ([url, init]) => String(url) === '/api/workflows/skills' && init?.method === 'POST',
    );
    expect(createCall).toBeUndefined();
  });

  it('shows an error when uploading a zip without choosing a file', async () => {
    renderWithClient(<SkillsPage payload={mockPayload} />);

    fireEvent.click(await screen.findByRole('button', { name: 'Create New Skill' }));
    fireEvent.click(screen.getByRole('button', { name: 'Upload Zip' }));

    await waitFor(() => {
      expect(screen.getByText('Choose a skill zip file to upload.')).toBeTruthy();
    });

    const importCall = fetchSpy.mock.calls.find(
      ([url, init]) => String(url) === '/api/skills/imports' && init?.method === 'POST',
    );
    expect(importCall).toBeUndefined();
  });

  it('sends the collision policy when uploading a zip and does not expose the unsupported new-version mode', async () => {
    renderWithClient(<SkillsPage payload={mockPayload} />);

    fireEvent.click(await screen.findByRole('button', { name: 'Create New Skill' }));
    const file = new File(['zip-content'], 'zip-skill.zip', { type: 'application/zip' });
    fireEvent.change(screen.getByLabelText('Skill Zip'), {
      target: { files: [file] },
    });

    const collisionSelect = screen.getByLabelText('Collision Policy') as HTMLSelectElement;
    const optionValues = Array.from(collisionSelect.options).map((option) => option.value);
    expect(optionValues).toEqual(['reject']);

    fireEvent.click(screen.getByRole('button', { name: 'Upload Zip' }));

    await waitFor(() => {
      const importCall = fetchSpy.mock.calls.find(
        ([url, init]) => String(url) === '/api/skills/imports' && init?.method === 'POST',
      );
      expect(importCall).toBeTruthy();
      const body = importCall![1]?.body as FormData;
      expect(body.get('collision_policy')).toBe('reject');
    });
  });

  it('closes the create drawer when cancel or escape is used', async () => {
    renderWithClient(<SkillsPage payload={mockPayload} />);

    fireEvent.click(await screen.findByRole('button', { name: 'Create New Skill' }));
    expect(screen.getByRole('dialog', { name: 'Create or upload skill' })).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: 'Create or upload skill' })).toBeNull();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Create New Skill' }));
    const dialog = screen.getByRole('dialog', { name: 'Create or upload skill' });
    expect(dialog).toBeTruthy();

    fireEvent.keyDown(dialog, { key: 'Escape' });
    await waitFor(() => {
      expect(screen.queryByRole('dialog', { name: 'Create or upload skill' })).toBeNull();
    });
  });

  it('renders markdown without unsafe HTML or links', async () => {
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith('/api/workflows/skills?includeContent=true')) {
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
      if (url.startsWith('/api/workflows/skills')) {
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
      const preview = screen.getByTestId('skill-markdown-preview');
      expect(preview?.innerHTML).not.toContain('onerror');
      expect(preview?.innerHTML).not.toContain('javascript:alert(1)');
      expect(preview?.innerHTML).not.toContain('<img');
    });
  });
});
