import { beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';

import { BootPayload } from '../boot/parseBootPayload';
import { renderWithClient } from '../utils/test-utils';
import { WorkflowListPage } from './workflow-list';

const INTERACTIVE_SELECTOR = 'a[href], button, input, select, textarea, summary, [role="button"], [tabindex]:not([tabindex="-1"])';

const listRow = {
  taskId: 'task-123',
  workflowId: 'task-123',
  source: 'temporal',
  title: 'Example task',
  status: 'completed',
  state: 'succeeded',
  rawState: 'succeeded',
  repository: 'MoonLadderStudios/MoonMind',
  targetSkill: 'pr-resolver',
  startedAt: '2026-03-28T00:00:01Z',
  createdAt: '2026-03-28T00:00:00Z',
};

function jsonResponse(body: unknown): Response {
  return { ok: true, json: async () => body } as Response;
}

function facetResponse(facet: string, values: string[], extra: Record<string, unknown> = {}) {
  return jsonResponse({
    facet,
    items: values.map((value) => ({ value, label: value, count: 1 })),
    stateItems: [],
    blankCount: 0,
    source: 'authoritative',
    countMode: 'exact',
    truncated: false,
    nextPageToken: null,
    ...extra,
  });
}

function sectionDetails(drawer: HTMLElement, label: string): HTMLDetailsElement {
  const section = within(drawer).getByRole('region', { name: `${label} filter` });
  const details = section.querySelector('details');
  expect(details).not.toBeNull();
  return details as HTMLDetailsElement;
}

describe('Workflow list progressive disclosure and cards', () => {
  const payload: BootPayload = { page: 'workflows-workspace', apiBase: '/api' };
  let fetchSpy: MockInstance;

  const listCalls = () =>
    fetchSpy.mock.calls.map(([url]) => String(url)).filter((url) => url.startsWith('/api/executions?'));
  const facetCalls = (facet: string) =>
    fetchSpy.mock.calls
      .map(([url]) => String(url))
      .filter((url) => url.startsWith('/api/executions/facets?') && url.includes(`facet=${facet}`));

  beforeEach(() => {
    window.localStorage.clear();
    window.history.pushState({}, 'Test', '/workflows');
    document.title = '';
    fetchSpy = vi.spyOn(window, 'fetch').mockReset().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith('/api/executions/facets?')) {
        const params = new URLSearchParams(url.split('?')[1]);
        const facet = params.get('facet') || '';
        if (facet === 'targetSkill') {
          const search = params.get('search');
          if (search === 'deploy') return Promise.resolve(facetResponse('targetSkill', ['deploy-preview']));
          if (params.get('nextPageToken') === 'skills-2') {
            return Promise.resolve(facetResponse('targetSkill', ['zeta-skill']));
          }
          return Promise.resolve(
            facetResponse('targetSkill', ['pr-resolver'], { truncated: true, nextPageToken: 'skills-2' }),
          );
        }
        if (facet === 'repository') {
          return Promise.resolve(
            facetResponse('repository', ['MoonLadderStudios/MoonMind'], { truncated: true, nextPageToken: 'repo-2' }),
          );
        }
        return Promise.resolve(facetResponse(facet, [], { truncated: true, nextPageToken: 'more' }));
      }
      return Promise.resolve(jsonResponse({ items: [listRow] }));
    });
  });

  it('renders the page title as the single h1 and sets the tab title', async () => {
    renderWithClient(<WorkflowListPage payload={payload} />);

    await screen.findAllByText('Example task');
    expect(screen.getAllByRole('heading', { level: 1 }).map((heading) => heading.textContent)).toEqual([
      'Workflows',
    ]);
    expect(document.title).toBe('Workflows | MoonMind');
  });

  it('gives mobile cards one detail link and a separate menu target with no nested controls', async () => {
    renderWithClient(
      <WorkflowListPage
        payload={{
          ...payload,
          initialData: { dashboardConfig: { features: { temporalDashboard: { actionsEnabled: true } } } },
        }}
      />,
    );

    const cardList = await waitFor(() => {
      const node = document.querySelector<HTMLElement>('.queue-card-list');
      expect(node).not.toBeNull();
      return node as HTMLElement;
    });
    const card = (await within(cardList).findByRole('link', { name: 'Example task' })).closest<HTMLElement>(
      '.queue-card',
    )!;

    const links = within(card).getAllByRole('link');
    expect(links).toHaveLength(1);
    expect(links[0]!.getAttribute('href')).toBe('/workflows/task-123?limit=50&source=temporal');
    expect(within(card).queryByRole('button', { name: 'View details' })).toBeNull();
    expect(within(card).getByRole('button', { name: 'More actions' })).toBeTruthy();

    for (const control of Array.from(card.querySelectorAll<HTMLElement>(INTERACTIVE_SELECTOR))) {
      expect(control.parentElement?.closest(INTERACTIVE_SELECTOR)).toBeNull();
    }
  });

  it('offers a sort control outside the filter drawer for card layouts', async () => {
    renderWithClient(<WorkflowListPage payload={payload} />);
    await screen.findAllByText('Example task');

    const sortSelect = screen.getByLabelText('Sort workflows') as HTMLSelectElement;
    fireEvent.change(sortSelect, { target: { value: 'progress:asc' } });
    expect(sortSelect.value).toBe('progress:asc');

    fireEvent.click(screen.getByRole('button', { name: 'Filters' }));
    const drawer = await screen.findByRole('dialog', { name: 'Advanced filters' });
    expect(within(drawer).queryByRole('button', { name: /complete first/ })).toBeNull();
  });

  it('collapses inactive filter sections and summarizes active ones', async () => {
    window.history.pushState({}, 'Test', '/workflows?stateNotIn=failed&finishedBlank=include');
    renderWithClient(<WorkflowListPage payload={payload} />);
    await screen.findAllByText('Example task');

    fireEvent.click(screen.getByRole('button', { name: 'Filters' }));
    const drawer = await screen.findByRole('dialog', { name: 'Advanced filters' });

    const statusSection = sectionDetails(drawer, 'Status');
    expect(statusSection.open).toBe(true);
    expect(within(statusSection).getByText('not failed')).toBeTruthy();

    const finishedSection = sectionDetails(drawer, 'Finished');
    expect(finishedSection.open).toBe(true);
    expect(within(finishedSection).getByText('blank')).toBeTruthy();

    const titleSection = sectionDetails(drawer, 'Title');
    expect(titleSection.open).toBe(false);
    expect(within(drawer).getByText('2 sections active')).toBeTruthy();
  });

  it('keeps exclude and blank semantics when applying drawer filters', async () => {
    renderWithClient(<WorkflowListPage payload={payload} />);
    await screen.findAllByText('Example task');

    fireEvent.click(screen.getByRole('button', { name: 'Filters' }));
    const drawer = await screen.findByRole('dialog', { name: 'Advanced filters' });
    fireEvent.change(within(drawer).getByLabelText('Status filter mode'), { target: { value: 'exclude' } });
    fireEvent.change(within(drawer).getByLabelText('Status filter value'), { target: { value: 'failed' } });
    fireEvent.change(within(drawer).getByLabelText('Finished blank values'), { target: { value: 'exclude' } });
    fireEvent.click(screen.getByRole('button', { name: 'Apply filters' }));

    await waitFor(() => {
      const url = listCalls().at(-1) || '';
      expect(url).toContain('stateNotIn=failed');
      expect(url).toContain('finishedBlank=exclude');
    });
  });

  it('replaces the truncation notice with searchable, loadable "More values" for skills', async () => {
    renderWithClient(<WorkflowListPage payload={payload} />);
    await screen.findAllByText('Example task');

    fireEvent.click(screen.getByRole('button', { name: 'Filters' }));
    const drawer = await screen.findByRole('dialog', { name: 'Advanced filters' });
    const skillSection = sectionDetails(drawer, 'Skill');

    const moreButton = await within(skillSection).findByText('More values…');
    expect(within(drawer).queryByText(/truncated by the server/i)).toBeNull();
    fireEvent.click(moreButton);

    fireEvent.click(await within(skillSection).findByText('Load more values'));
    expect(await within(skillSection).findByLabelText('Add zeta-skill')).toBeTruthy();

    fireEvent.change(within(skillSection).getByLabelText('Search Skill values'), { target: { value: 'deploy' } });
    fireEvent.click(await within(skillSection).findByLabelText('Add deploy-preview'));
    expect(facetCalls('targetSkill').some((url) => url.includes('search=deploy'))).toBe(true);

    const selected = within(skillSection).getByRole('list', { name: 'Selected skill filters' });
    expect(within(selected).getByText('deploy-preview')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Apply filters' }));
    await waitFor(() => expect(listCalls().at(-1)).toContain('targetSkillIn=deploy-preview'));
  });

  it('lets a repository found through "More values" become the repository filter', async () => {
    fetchSpy.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith('/api/executions/facets?')) {
        const params = new URLSearchParams(url.split('?')[1]);
        if (params.get('facet') === 'repository' && params.get('search') === 'other') {
          return Promise.resolve(facetResponse('repository', ['Acme/other-repo']));
        }
        return Promise.resolve(
          facetResponse(params.get('facet') || '', ['MoonLadderStudios/MoonMind'], {
            truncated: true,
            nextPageToken: 'more',
          }),
        );
      }
      return Promise.resolve(jsonResponse({ items: [listRow] }));
    });
    renderWithClient(<WorkflowListPage payload={payload} />);
    await screen.findAllByText('Example task');

    fireEvent.click(screen.getByRole('button', { name: 'Filters' }));
    const drawer = await screen.findByRole('dialog', { name: 'Advanced filters' });
    const repoSection = sectionDetails(drawer, 'Repo');
    fireEvent.click(await within(repoSection).findByText('More values…'));
    fireEvent.change(within(repoSection).getByLabelText('Search Repo values'), { target: { value: 'other' } });
    fireEvent.click(await within(repoSection).findByLabelText('Add Acme/other-repo'));
    fireEvent.click(screen.getByRole('button', { name: 'Apply filters' }));

    await waitFor(() => expect(listCalls().at(-1)).toContain('repoIn=Acme%2Fother-repo'));
  });
});
