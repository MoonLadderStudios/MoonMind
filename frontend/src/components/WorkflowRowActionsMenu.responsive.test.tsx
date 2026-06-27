import { readFileSync } from 'node:fs';

import postcss from 'postcss';
import type { AtRule, Root, Rule } from 'postcss';
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { fireEvent, screen } from '@testing-library/react';

import { renderWithClient } from '../utils/test-utils';
import { WorkflowRowActionsMenu } from './WorkflowRowActionsMenu';

const dashboardCss = readFileSync(
  `${process.cwd()}/frontend/src/styles/dashboard.css`,
  'utf8',
);

let parsedCss: Root | null = null;

function cssRoot(): Root {
  parsedCss ??= postcss.parse(dashboardCss);
  return parsedCss;
}

/** Returns true when the rule sits directly inside a `max-width: <maxWidth>px` media query. */
function isInsideMobileMedia(rule: Rule, maxWidth: number): boolean {
  const parent = rule.parent;
  if (!parent || parent.type !== 'atrule') return false;
  const atRule = parent as AtRule;
  return atRule.name === 'media' && atRule.params.includes(`max-width: ${maxWidth}px`);
}

/** Declarations for `selector` declared inside a `max-width: <maxWidth>px` media query. */
function mobileRuleBlock(selector: string, maxWidth = 767): string {
  const blocks: string[] = [];
  cssRoot().walkRules((rule: Rule) => {
    const selectors = rule.selector.split(',').map((item) => item.trim());
    if (selectors.includes(selector) && isInsideMobileMedia(rule, maxWidth)) {
      blocks.push(rule.nodes.map((node) => `${node.toString()};`).join('\n'));
    }
  });
  return blocks.join('\n');
}

function cssRuleBlock(selector: string): string {
  const normalizedSelector = selector.replace(/\s+/g, ' ').trim();
  const blocks: string[] = [];
  cssRoot().walkRules((rule: Rule) => {
    if (rule.selector.replace(/\s+/g, ' ').trim() === normalizedSelector) {
      blocks.push(rule.nodes.map((node) => `${node.toString()};`).join('\n'));
    }
  });
  return blocks.join('\n');
}

describe('WorkflowRowActionsMenu responsive layout', () => {
  it('renders the actions menu in-flow beneath the trigger on mobile cards', () => {
    // On mobile the absolutely-positioned, right-anchored popover used to shoot
    // off the left edge of the narrow, left-aligned card kebab and was clipped
    // by `.queue-card`'s `overflow: hidden`. The mobile media query must drop the
    // popover into normal flow so it stays on screen.
    const popoverBlock = mobileRuleBlock('.queue-card-actions .td-workflow-actions-popover');
    expect(popoverBlock).toContain('position: static;');
    expect(popoverBlock).toContain('width: 100%;');
    expect(popoverBlock).toContain('max-width: 100%;');
    expect(popoverBlock).toContain('max-height: none;');
  });

  it('lets the actions cluster span the full card width on mobile', () => {
    const rowBlock = mobileRuleBlock('.queue-card-actions .workflow-row-actions');
    expect(rowBlock).toContain('width: 100%;');
    expect(rowBlock).toContain('align-items: stretch;');

    const menuBlock = mobileRuleBlock('.queue-card-actions .td-workflow-actions-menu');
    expect(menuBlock).toContain('width: 100%;');
    expect(menuBlock).toContain('flex-direction: column;');
  });
});

describe('Workflow list table dropdown overflow', () => {
  it('lets row action popovers overflow the table edges instead of clipping them', () => {
    // `overflow-x: auto` on the wrapper is coerced to `overflow-y: auto` by the
    // browser, so the wrapper clips popovers. A highly restrictive filter leaves
    // a short table that cut the row actions popover off. While a popover is open
    // the wrapper must switch to `overflow: visible` so the popover can escape.
    const wrapperBlock = cssRuleBlock(
      `.workflow-list-data-slab .queue-table-wrapper:has(
        .td-workflow-actions-popover
      )`,
    );
    expect(wrapperBlock).toContain('overflow: visible;');
    expect(wrapperBlock).not.toContain('padding-bottom:');

    const tableBlock = cssRuleBlock('.queue-table-wrapper table');
    expect(tableBlock).not.toContain('overflow: visible;');
  });
});

describe('WorkflowRowActionsMenu card markup', () => {
  let fetchSpy: MockInstance;

  beforeEach(() => {
    fetchSpy = vi.spyOn(window, 'fetch').mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/executions/wf-123?source=temporal') {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            workflowId: 'wf-123',
            runId: 'run-1',
            title: 'Example workflow',
            state: 'executing',
            actions: { canPause: true, canCancel: true },
          }),
        } as Response);
      }
      return Promise.resolve({ ok: true, json: async () => ({}) } as Response);
    });
  });

  afterEach(() => {
    fetchSpy.mockRestore();
  });

  it('keeps the open popover nested inside .queue-card-actions so the mobile rules apply', () => {
    const { container } = renderWithClient(
      <div className="queue-card-actions">
        <WorkflowRowActionsMenu
          workflowId="wf-123"
          apiBase="/api"
          actionsEnabled
          taskEditingEnabled={false}
        />
      </div>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Actions' }));

    // The mobile media query targets `.queue-card-actions .td-workflow-actions-popover`,
    // so the rendered popover must actually live under that ancestor chain.
    const popover = container.querySelector(
      '.queue-card-actions .workflow-row-actions .td-workflow-actions-popover',
    );
    expect(popover).not.toBeNull();
    expect(popover?.getAttribute('role')).toBe('menu');
  });
});
