import { render, screen } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

import { CollectionWorkspace } from './CollectionWorkspace';

describe('CollectionWorkspace', () => {
  it('places an optional sidebar before the sibling primary pane and exposes neutral state', () => {
    const { container } = render(
      <CollectionWorkspace
        collection="workflow"
        mode="sidebar"
        sidebar={<aside aria-label="Workflow navigation">Rows</aside>}
        utilities={<button type="button">Display mode</button>}
        sidebarState="error"
        primaryState="ready"
        primaryLabel="Workflow detail"
      >
        <h1>Selected workflow</h1>
      </CollectionWorkspace>,
    );

    const root = container.querySelector('.collection-workspace') as HTMLElement;
    expect(root.firstElementChild).toBe(screen.getByRole('complementary', { name: 'Workflow navigation' }));
    expect(root.children[1]).toBe(screen.getByRole('main', { name: 'Workflow detail' }));
    expect(root.dataset.collection).toBe('workflow');
    expect(root.dataset.sidebarState).toBe('error');
    expect(root.dataset.primaryState).toBe('ready');
    expect(screen.getByRole('button', { name: 'Display mode' })).toBeTruthy();
    expect(screen.getByRole('heading', { name: 'Selected workflow' })).toBeTruthy();
  });

  it('leaves no sidebar track when the sidebar is absent', () => {
    const { container } = render(
      <CollectionWorkspace collection="recurring" mode="single">
        <p>Recurring schedules</p>
      </CollectionWorkspace>,
    );

    const root = container.querySelector('.collection-workspace') as HTMLElement;
    expect(root.dataset.sidebarPresent).toBe('false');
    expect(root.classList.contains('collection-workspace--single')).toBe(true);
    expect(root.children).toHaveLength(1);
  });

  it('defines a fluid, unconstrained root instead of a centered page wrapper', () => {
    const css = readFileSync(`${process.cwd()}/frontend/src/styles/dashboard.css`, 'utf8');

    expect(css).toMatch(/\.collection-workspace\s*\{[^}]*width:\s*100%;[^}]*max-width:\s*none;/);
    expect(css).toMatch(/\.collection-workspace--with-sidebar:not\(\.workflow-workspace-shell\)\s*\{[^}]*grid-template-columns:[^}]*var\(--mm-collection-sidebar-width\)[^}]*minmax\(0,\s*1fr\)/);
    expect(css).toMatch(/\.collection-workspace--with-sidebar:not\(\.workflow-workspace-shell\) > \.collection-sidebar\s*\{[^}]*grid-column:\s*sidebar-start \/ primary-start/);
    expect(css).toMatch(/\.collection-workspace:not\(\.workflow-workspace-shell\) > \.collection-workspace__primary\s*\{[^}]*grid-column:\s*primary-start \/ primary-end/);
    expect(css).toMatch(/\.collection-workspace--single\s*\{[^}]*display:\s*block;/);
  });

  it('keeps required collection adapters on the shared sidebar primitive', () => {
    const workflowSidebar = readFileSync(
      `${process.cwd()}/frontend/src/components/workflows/WorkflowWorkspaceSidebar.tsx`,
      'utf8',
    );
    const recurringPage = readFileSync(`${process.cwd()}/frontend/src/entrypoints/schedules.tsx`, 'utf8');
    const skillsPage = readFileSync(`${process.cwd()}/frontend/src/entrypoints/skills.tsx`, 'utf8');

    for (const source of [workflowSidebar, recurringPage, skillsPage]) {
      expect(source).toContain('CollectionSidebar');
    }
    expect(skillsPage).not.toContain('sidebar={<nav');
    expect(skillsPage).toContain('landmarkLabel="Skill navigation"');
    expect(recurringPage).toContain('landmarkLabel="Recurring schedule navigation"');
    expect(workflowSidebar).toContain('landmarkLabel="Workflow navigation"');
  });
});
