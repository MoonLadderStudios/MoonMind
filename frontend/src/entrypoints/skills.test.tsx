import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import type { BootPayload } from '../boot/parseBootPayload';
import { renderWithClient } from '../utils/test-utils';
import { SkillsPage } from './skills';

type SkillsDisplayMode = 'hidden' | 'sidebar' | 'table';

function skillsPayload(path: string, mode?: SkillsDisplayMode): BootPayload {
  return {
    page: 'skills',
    apiBase: '/api',
    initialData: {
      ...(mode ? { skillsListDisplayMode: mode } : {}),
      dashboardConfig: { initialPath: path },
    },
  };
}

function renderSkills({ path = '/skills', mode }: { path?: string; mode?: SkillsDisplayMode } = {}) {
  const payload = skillsPayload(path, mode);
  return renderWithClient(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/skills" element={<SkillsPage payload={payload} />} />
        <Route path="/skills/:skillId" element={<SkillsPage payload={payload} />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('Skills Entrypoint', () => {
  let fetchSpy: MockInstance;

  beforeEach(() => {
    window.localStorage.clear();
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
              {
                id: 'speckit-orchestrate',
                label: 'Speckit Orchestrate',
                description: 'Plans and executes spec workflows.',
                hasInputSchema: true,
                source: { kind: 'file', path: '/skills/speckit-orchestrate/SKILL.md' },
                markdown: '# Speckit\n\nExisting **worker** skill.\n\n- Plans\n- Tests',
              },
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
      if (url.startsWith('/api/workflows/skills') && !url.includes('includeContent=true') && init?.method !== 'POST') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            items: { worker: ['speckit-orchestrate', 'pr-resolver'] },
            legacyItems: [],
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

  describe('route-derived selection and display modes', () => {
    it('renders the full catalog table on /skills without a sidebar or detail pane', async () => {
      renderSkills({ path: '/skills', mode: 'table' });

      expect(await screen.findByRole('table', { name: 'Skills catalog' })).toBeTruthy();
      expect(screen.queryByRole('complementary', { name: 'Skill navigation' })).toBeNull();
      expect(screen.queryByText('Skill Preview')).toBeNull();
      expect(await screen.findByRole('link', { name: /^Speckit Orchestrate/ })).toBeTruthy();
    });

    it('derives the selected skill from the detail route and mounts exactly one sidebar', async () => {
      renderSkills({ path: '/skills/pr-resolver', mode: 'sidebar' });

      await waitFor(() => {
        expect(screen.getByRole('heading', { name: 'pr-resolver' })).toBeTruthy();
      });
      expect(screen.getAllByRole('complementary', { name: 'Skill navigation' })).toHaveLength(1);
      expect(screen.getByText('Resolves pull requests.')).toBeTruthy();
    });

    it('honors hidden mode on a detail route without mounting the sidebar', async () => {
      renderSkills({ path: '/skills/pr-resolver', mode: 'hidden' });

      await waitFor(() => {
        expect(screen.getByRole('heading', { name: 'pr-resolver' })).toBeTruthy();
      });
      expect(screen.queryByRole('complementary', { name: 'Skill navigation' })).toBeNull();
    });

    it('coerces a persisted table mode on a direct detail visit to sidebar without redirecting', async () => {
      renderSkills({ path: '/skills/pr-resolver', mode: 'table' });

      await waitFor(() => {
        expect(screen.getByRole('heading', { name: 'pr-resolver' })).toBeTruthy();
      });
      expect(screen.getAllByRole('complementary', { name: 'Skill navigation' })).toHaveLength(1);
      expect(screen.queryByRole('table', { name: 'Skills catalog' })).toBeNull();
    });

    it('shows a localized not-found state for a stale skill ID while keeping the catalog sidebar usable', async () => {
      renderSkills({ path: '/skills/ghost-skill', mode: 'sidebar' });

      await waitFor(() => {
        expect(screen.getByTestId('skill-not-found').textContent).toContain('ghost-skill');
      });
      const navigation = screen.getByRole('complementary', { name: 'Skill navigation' });
      expect(within(navigation).getByRole('link', { name: 'pr-resolver' })).toBeTruthy();
    });
  });

  describe('catalog table', () => {
    it('renders normalized catalog metadata columns', async () => {
      renderSkills({ path: '/skills' });

      const table = await screen.findByRole('table', { name: 'Skills catalog' });
      const headerTexts = within(table).getAllByRole('columnheader').map((cell) => cell.textContent);
      expect(headerTexts.join(' ')).toContain('Skill');
      expect(headerTexts).toEqual(expect.arrayContaining([
        'Description', 'Source', 'Inputs', 'Content', 'Action',
      ]));

      const speckitLink = await within(table).findByRole('link', { name: /^Speckit Orchestrate/ });
      expect(speckitLink.textContent).toContain('speckit-orchestrate');
      const speckitRow = speckitLink.closest('tr')!;
      expect(speckitRow.textContent).toContain('Plans and executes spec workflows.');
      expect(speckitRow.textContent).toContain('File');
      expect(speckitRow.textContent).toContain('Structured inputs');
      expect(speckitRow.textContent).toContain('6 lines');

      const prRow = within(table).getByRole('link', { name: 'pr-resolver' }).closest('tr')!;
      expect(prRow.textContent).toContain('—');
    });

    it('navigates to the detail route when a row is opened and records the remembered skill', async () => {
      renderSkills({ path: '/skills' });

      fireEvent.click(await screen.findByRole('link', { name: 'Open skill pr-resolver' }));

      await waitFor(() => {
        expect(screen.getByRole('heading', { name: 'pr-resolver' })).toBeTruthy();
        expect(screen.getByText('Resolves pull requests.')).toBeTruthy();
      });
      const stored = JSON.parse(window.localStorage.getItem('moonmind.dashboard.preferences') ?? '{}');
      expect(stored.preferences.lastSelectedSkillId).toBe('pr-resolver');
    });

    it('filters catalog rows through the column filter popover', async () => {
      renderSkills({ path: '/skills' });

      expect(await screen.findByRole('link', { name: /^Speckit Orchestrate/ })).toBeTruthy();

      fireEvent.click(screen.getByRole('button', { name: 'Skill sidebar filter. No filter applied.' }));
      fireEvent.change(screen.getByLabelText('Skill sidebar filter value'), { target: { value: 'pr-' } });

      await waitFor(() => {
        expect(screen.queryByRole('link', { name: /^Speckit Orchestrate/ })).toBeNull();
        expect(screen.getByRole('link', { name: 'pr-resolver' })).toBeTruthy();
      });

      fireEvent.change(screen.getByLabelText('Skill sidebar filter value'), { target: { value: 'does-not-exist' } });
      await waitFor(() => {
        expect(screen.getByText('No skills match your filter.')).toBeTruthy();
      });
    });
  });

  describe('sidebar rows and filter popover', () => {
    it('renders sidebar rows as links with aria-current on the active route', async () => {
      renderSkills({ path: '/skills/speckit-orchestrate', mode: 'sidebar' });

      const navigation = await screen.findByRole('complementary', { name: 'Skill navigation' });
      expect(navigation.classList.contains('collection-sidebar')).toBe(true);
      expect(within(navigation).getByRole('table', { name: 'Skill list table slice' })).toBeTruthy();
      expect(within(navigation).queryByRole('button', { name: 'pr-resolver' })).toBeNull();

      const active = await within(navigation).findByRole('link', { name: /^Speckit Orchestrate/ });
      expect(active.getAttribute('aria-current')).toBe('page');
      const inactive = within(navigation).getByRole('link', { name: 'pr-resolver' });
      expect(inactive.getAttribute('aria-current')).toBeNull();

      fireEvent.click(inactive);

      await waitFor(() => {
        expect(inactive.getAttribute('aria-current')).toBe('page');
        expect(document.activeElement).toBe(screen.getByRole('heading', { name: 'pr-resolver' }));
      });
    });

    it('keeps the filter field hidden until the filter icon opens the popover', async () => {
      renderSkills({ path: '/skills/speckit-orchestrate', mode: 'sidebar' });

      await screen.findByRole('complementary', { name: 'Skill navigation' });
      expect(screen.queryByLabelText('Skill sidebar filter value')).toBeNull();

      const trigger = screen.getByRole('button', { name: 'Skill sidebar filter. No filter applied.' });
      fireEvent.click(trigger);

      const dialog = screen.getByRole('dialog', { name: 'Skill sidebar filter' });
      expect(within(dialog).getByText('Skill filter')).toBeTruthy();
      const input = within(dialog).getByLabelText('Skill sidebar filter value') as HTMLInputElement;
      expect(input.placeholder).toBe('Filter skills');
      expect(document.activeElement).toBe(input);

      fireEvent.pointerDown(trigger);
      fireEvent.click(trigger);
      expect(screen.queryByRole('dialog', { name: 'Skill sidebar filter' })).toBeNull();
    });

    it('supports reset, apply, escape, and outside-click with focus restoration', async () => {
      renderSkills({ path: '/skills/speckit-orchestrate', mode: 'sidebar' });

      await screen.findByRole('complementary', { name: 'Skill navigation' });
      const trigger = screen.getByRole('button', { name: 'Skill sidebar filter. No filter applied.' });
      fireEvent.click(trigger);

      const reset = screen.getByRole('button', { name: 'Reset skill sidebar filter' });
      expect((reset as HTMLButtonElement).disabled).toBe(true);

      fireEvent.change(screen.getByLabelText('Skill sidebar filter value'), { target: { value: 'pr-' } });
      expect((reset as HTMLButtonElement).disabled).toBe(false);
      expect(screen.getByRole('button', { name: 'Skill sidebar filter: pr-' })).toBeTruthy();

      fireEvent.click(reset);
      expect((screen.getByLabelText('Skill sidebar filter value') as HTMLInputElement).value).toBe('');

      fireEvent.click(screen.getByRole('button', { name: 'Apply skill sidebar filter' }));
      await waitFor(() => {
        expect(screen.queryByRole('dialog', { name: 'Skill sidebar filter' })).toBeNull();
      });
      expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Skill sidebar filter. No filter applied.' }));

      fireEvent.click(screen.getByRole('button', { name: 'Skill sidebar filter. No filter applied.' }));
      const dialog = screen.getByRole('dialog', { name: 'Skill sidebar filter' });
      fireEvent.keyDown(dialog, { key: 'Escape' });
      await waitFor(() => {
        expect(screen.queryByRole('dialog', { name: 'Skill sidebar filter' })).toBeNull();
      });
      expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Skill sidebar filter. No filter applied.' }));

      fireEvent.click(screen.getByRole('button', { name: 'Skill sidebar filter. No filter applied.' }));
      expect(screen.getByRole('dialog', { name: 'Skill sidebar filter' })).toBeTruthy();
      fireEvent.pointerDown(document.body);
      await waitFor(() => {
        expect(screen.queryByRole('dialog', { name: 'Skill sidebar filter' })).toBeNull();
      });
    });

    it('filters sidebar rows and pins the current skill when it does not match', async () => {
      renderSkills({ path: '/skills/speckit-orchestrate', mode: 'sidebar' });

      const navigation = await screen.findByRole('complementary', { name: 'Skill navigation' });
      expect(await within(navigation).findByRole('link', { name: 'pr-resolver' })).toBeTruthy();

      fireEvent.click(screen.getByRole('button', { name: 'Skill sidebar filter. No filter applied.' }));
      fireEvent.change(screen.getByLabelText('Skill sidebar filter value'), { target: { value: 'pr-' } });

      await waitFor(() => {
        expect(within(navigation).getByRole('link', { name: 'pr-resolver' })).toBeTruthy();
        expect(screen.getByRole('rowgroup', { name: 'Current skill' })).toBeTruthy();
        expect(within(navigation).getByRole('link', { name: /Current skill.*Speckit Orchestrate/ })).toBeTruthy();
      });
      // Narrowing the list must not clear the selected detail.
      expect(screen.getByRole('heading', { name: 'Speckit Orchestrate' })).toBeTruthy();

      fireEvent.change(screen.getByLabelText('Skill sidebar filter value'), { target: { value: 'does-not-exist' } });
      await waitFor(() => {
        expect(screen.getByText('No skills match your filter.')).toBeTruthy();
        expect(within(navigation).getByRole('link', { name: /Current skill.*Speckit Orchestrate/ })).toBeTruthy();
      });
    });
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

    renderSkills({ path: '/skills/speckit-orchestrate', mode: 'sidebar' });

    expect(screen.getByRole('heading', { name: 'Skills' })).toBeTruthy();
    expect(screen.getByText('Loading skills...')).toBeTruthy();
    expect(screen.getByText('Skills preview loading placeholder').closest('[role="status"]')).toBeTruthy();
  });

  it('previews markdown for the route-selected skill', async () => {
    renderSkills({ path: '/skills/speckit-orchestrate', mode: 'sidebar' });

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
      return Promise.resolve({
        ok: false,
        status: 404,
        text: async () => 'Unhandled fetch',
      } as Response);
    });

    renderSkills({ path: '/skills/markdown-skill', mode: 'sidebar' });

    await waitFor(() => {
      const preview = screen.getByTestId('skill-markdown-preview');
      const listItem = preview.querySelector('li');
      expect(listItem?.querySelector('strong')?.textContent).toBe('bold');
      expect(listItem?.querySelector('a')?.getAttribute('href')).toBe('/docs');
      expect(preview.querySelector('code.language-ts')?.textContent).toBe('const ok = true;');
    });
  });

  it('creates a new skill, refreshes the list, and navigates to the created skill', async () => {
    renderSkills({ path: '/skills' });

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
      expect(screen.getByRole('heading', { name: 'fresh-skill' })).toBeTruthy();
      expect(screen.getByText('Fresh Skill')).toBeTruthy();
      expect(screen.getByText('Created from the UI.')).toBeTruthy();
    });
    const stored = JSON.parse(window.localStorage.getItem('moonmind.dashboard.preferences') ?? '{}');
    expect(stored.preferences.lastSelectedSkillId).toBe('fresh-skill');
  });

  it('uploads a skill zip, refreshes the list, and navigates to the uploaded skill', async () => {
    renderSkills({ path: '/skills' });

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
      expect(screen.getByRole('heading', { name: 'zip-skill' })).toBeTruthy();
      expect(screen.getByText('Zip Skill')).toBeTruthy();
      expect(screen.getByText('Uploaded from a zip.')).toBeTruthy();
    });
  });

  it('keeps the shared skill sidebar mounted while create and upload are open', async () => {
    renderSkills({ path: '/skills/speckit-orchestrate', mode: 'sidebar' });

    fireEvent.click(await screen.findByRole('button', { name: 'Create New Skill' }));

    expect(screen.getByRole('dialog', { name: 'Create or upload skill' })).toBeTruthy();
    expect(screen.getByRole('complementary', { name: 'Skill navigation' })).toBeTruthy();
    expect(screen.getByRole('table', { name: 'Skill list table slice' })).toBeTruthy();
    expect(screen.queryByText('Available Skills')).toBeNull();
    expect(screen.queryByText('Workflow')).toBeNull();
  });

  it('shows the raw markdown and metadata preview tabs', async () => {
    renderSkills({ path: '/skills/speckit-orchestrate', mode: 'sidebar' });

    const renderedTab = await screen.findByRole('tab', { name: 'Rendered' });
    const rawTab = screen.getByRole('tab', { name: 'Raw Markdown' });
    expect(renderedTab.tabIndex).toBe(0);
    expect(rawTab.tabIndex).toBe(-1);

    fireEvent.click(rawTab);
    await waitFor(() => {
      const rawMarkdown = screen.getByLabelText('Raw Markdown Content');
      expect(rawMarkdown.textContent).toContain('# Speckit');
      expect(rawMarkdown.tabIndex).toBe(0);
      expect(rawTab.tabIndex).toBe(0);
      expect(renderedTab.tabIndex).toBe(-1);
    });

    fireEvent.click(screen.getByRole('tab', { name: 'Metadata' }));
    await waitFor(() => {
      const metadata = screen.getByTestId('skill-metadata');
      expect(metadata.textContent).toContain('speckit-orchestrate');
      expect(metadata.textContent).toContain('File');
      expect(metadata.textContent).toContain('Structured inputs');
    });
  });

  it('validates the skill name before submitting a create request', async () => {
    renderSkills({ path: '/skills' });

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
    renderSkills({ path: '/skills' });

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
    renderSkills({ path: '/skills' });

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
    renderSkills({ path: '/skills' });

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

  it('traps focus in the create drawer and restores it to the trigger', async () => {
    renderSkills({ path: '/skills' });

    const trigger = await screen.findByRole('button', { name: 'Create New Skill' });
    trigger.focus();
    fireEvent.click(trigger);
    const dialog = screen.getByRole('dialog', { name: 'Create or upload skill' });
    const close = screen.getByRole('button', { name: 'Close create skill' });
    const lastAction = screen.getByRole('button', { name: 'Upload Zip' });
    dialog.querySelectorAll<HTMLElement>('button, input, textarea, select').forEach((element) => {
      Object.defineProperty(element, 'offsetWidth', { configurable: true, value: 1 });
    });

    close.focus();
    fireEvent.keyDown(dialog, { key: 'Tab', shiftKey: true });
    expect(document.activeElement).toBe(lastAction);

    lastAction.focus();
    fireEvent.keyDown(dialog, { key: 'Tab' });
    expect(document.activeElement).toBe(close);

    fireEvent.keyDown(dialog, { key: 'Escape' });
    await waitFor(() => expect(document.activeElement).toBe(trigger));
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
      return Promise.resolve({
        ok: false,
        status: 404,
        text: async () => 'Unhandled fetch',
      } as Response);
    });

    renderSkills({ path: '/skills/unsafe-skill', mode: 'sidebar' });

    await waitFor(() => {
      const preview = screen.getByTestId('skill-markdown-preview');
      expect(preview?.innerHTML).not.toContain('onerror');
      expect(preview?.innerHTML).not.toContain('javascript:alert(1)');
      expect(preview?.innerHTML).not.toContain('<img');
    });
  });
});
