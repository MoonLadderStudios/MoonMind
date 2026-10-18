import { describe, expect, it, vi } from 'vitest';
import { BrowserRouter } from 'react-router-dom';

import { renderWithClient, screen } from '../utils/test-utils';
import { WorkflowsWorkspacePage } from './workflows-workspace';

// Its own file so no other test has already imported the Create or Detail
// module: each mock factory runs only when its module is first imported.
const importedRouteModules = vi.hoisted(() => [] as string[]);

vi.mock('./workflow-list', () => ({
  default: () => <div data-testid="workflow-list-page">Workflow list</div>,
}));

vi.mock('./workflow-detail', () => {
  importedRouteModules.push('workflow-detail');
  return { default: () => null, WorkflowDetailPage: () => null };
});

vi.mock('./workflow-start', () => {
  importedRouteModules.push('workflow-start');
  return { default: () => null };
});

describe('WorkflowsWorkspacePage chunk loading', () => {
  it('MoonLadderStudios/MoonMind#4870 does not import Create or Detail for the list route', async () => {
    window.history.pushState({}, 'Workspace List Test', '/workflows');

    renderWithClient(
      <BrowserRouter>
        <WorkflowsWorkspacePage payload={{ page: 'dashboard', apiBase: '/api' }} />
      </BrowserRouter>,
    );

    expect(await screen.findByTestId('workflow-list-page')).toBeTruthy();
    expect(importedRouteModules).toEqual([]);
  });
});
