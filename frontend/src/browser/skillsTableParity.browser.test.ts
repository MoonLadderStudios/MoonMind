import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import '../styles/dashboard.css';

// Real-browser guardrail for MoonLadderStudios/MoonMind#3346: the desktop Skills
// table must match the Workflow table's visual treatment (transparent body over
// the page surface, header/row dividers drawn with the shared list token and
// extended to the collection-content edges). jsdom cannot compute backgrounds,
// border colors, sticky positioning, or layout geometry, and string assertions
// against dashboard.css cannot prove the deployed appearance — so these
// invariants are asserted against computed styles and bounding boxes in a real
// browser (Chromium and Firefox via `npm run ui:test:browser`).
//
// The harness mirrors the production chrome rendered by
// `frontend/src/entrypoints/skills.tsx` (CollectionWorkspace with
// `skills-catalog-page` + `skills-catalog-primary`) and `DataTable`
// (`.data-table-slab` > `table.data-table` with sticky headers). The default
// browser viewport is 1280x800, which satisfies the issue's desktop check
// above the 1181px navigation breakpoint. Tailwind's `px-4`/`sm:px-6` primary
// padding is replicated with an explicit inline padding: at this viewport the
// `sm:` variant applies, so the primary carries 1.5rem of horizontal padding
// and `--skills-table-bleed` resolves to the same 1.5rem.

const LONG_DESCRIPTION =
  'A very long description that must wrap inside the description column instead of stretching the table past the viewport. '.repeat(
    6,
  );

function skillsMarkup(): string {
  return `
    <div class="collection-workspace collection-workspace--single skills-page skills-catalog-page" data-collection="skill">
      <div
        class="collection-workspace__primary skills-catalog-primary"
        aria-label="Skills catalog"
        style="padding-left: 1.5rem; padding-right: 1.5rem;"
      >
        <div class="data-table-slab" data-layout="table" data-density="comfortable" data-sticky="on" data-responsive="off">
          <table class="data-table" aria-label="Skills catalog">
            <thead>
              <tr>
                <th scope="col" data-align="left" data-column-key="skill" style="width: var(--workflow-list-column-workflow-width);">Skill</th>
                <th scope="col" data-align="left" data-column-key="description">Description</th>
                <th scope="col" data-align="left" data-column-key="source">Source</th>
                <th scope="col" data-align="left" data-column-key="inputs">Inputs</th>
                <th scope="col" data-align="left" data-column-key="content">Content</th>
                <th scope="col" data-align="right" data-column-key="actions">Action</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td data-align="left" data-column-key="skill">
                  <a class="skills-catalog-title-link" href="/skills/pr-resolver">
                    <span class="skills-catalog-title">PR Resolver</span>
                    <span class="skills-catalog-id">pr-resolver</span>
                  </a>
                </td>
                <td data-align="left" data-column-key="description">Resolves pull requests.</td>
                <td data-align="left" data-column-key="source">File</td>
                <td data-align="left" data-column-key="inputs">Structured inputs</td>
                <td data-align="left" data-column-key="content">Markdown</td>
                <td class="data-table__row-actions" data-align="right" data-column-key="actions"><a href="/skills/pr-resolver">Open</a></td>
              </tr>
              <tr>
                <td data-align="left" data-column-key="skill">
                  <a class="skills-catalog-title-link" href="/skills/long-skill">
                    <span class="skills-catalog-title">A very long skill label that keeps going and going and going</span>
                    <span class="skills-catalog-id">a-very-long-skill-name-that-keeps-going-and-going</span>
                  </a>
                </td>
                <td data-align="left" data-column-key="description">${LONG_DESCRIPTION}</td>
                <td data-align="left" data-column-key="source">File</td>
                <td data-align="left" data-column-key="inputs">—</td>
                <td data-align="left" data-column-key="content">Markdown</td>
                <td class="data-table__row-actions" data-align="right" data-column-key="actions"><a href="/skills/long-skill">Open</a></td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>`;
}

// Workflow reference slice: the divider treatment the Skills table must match
// (`.workflow-list-data-slab .queue-table-wrapper td` draws
// `var(--workflow-list-divider-color)`). The reference cell is read only for
// its computed divider color, but the wrapper bleeds edge to edge by design
// (width 100% + 2x `--workflow-list-slab-bleed-inline` with matching negative
// margins), which assumes a padded panel ancestor like production provides.
// Host it with that same 1rem inset so the scaffolding never spills past the
// viewport and pollutes the document-overflow assertions below.
function workflowReferenceMarkup(): string {
  return `
    <div style="padding-left: 1rem; padding-right: 1rem;">
      <div class="workflow-list-data-slab">
        <div class="queue-table-wrapper">
          <table>
            <tbody>
              <tr><td data-workflow-divider-reference="true">reference</td></tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>`;
}

let container: HTMLElement;

function queries() {
  const primary = container.querySelector<HTMLElement>('.skills-catalog-primary')!;
  const slab = container.querySelector<HTMLElement>('.skills-catalog-page .data-table-slab')!;
  const table = container.querySelector<HTMLElement>('.skills-catalog-page .data-table')!;
  const headerCells = Array.from(
    container.querySelectorAll<HTMLElement>('.skills-catalog-page .data-table thead th'),
  );
  const bodyRows = Array.from(
    container.querySelectorAll<HTMLElement>('.skills-catalog-page .data-table tbody tr'),
  );
  const bodyCells = Array.from(
    container.querySelectorAll<HTMLElement>('.skills-catalog-page .data-table tbody td'),
  );
  const referenceCell = container.querySelector<HTMLElement>('[data-workflow-divider-reference]')!;
  return { primary, slab, table, headerCells, bodyRows, bodyCells, referenceCell };
}

function bleedPx(primary: HTMLElement): number {
  const raw = getComputedStyle(primary).getPropertyValue('--skills-table-bleed').trim();
  const rootFont = Number.parseFloat(getComputedStyle(document.documentElement).fontSize);
  const match = raw.match(/^([\d.]+)rem$/);
  expect(raw, '--skills-table-bleed resolves to a rem length').toMatch(/^([\d.]+)rem$/);
  return Number.parseFloat(match![1]!) * rootFont;
}

function alphaOf(color: string): number | null {
  const match = color.match(
    /rgba?\(\s*[\d.]+\s*[, ]\s*[\d.]+\s*[, ]\s*[\d.]+(?:\s*[,/]\s*([\d.]+))?\s*\)/,
  );
  if (!match) return null;
  return match[1] === undefined ? 1 : Number.parseFloat(match[1]);
}

beforeEach(() => {
  document.body.style.margin = '0';
  document.documentElement.classList.remove('dark');
  container = document.createElement('div');
  container.innerHTML = `${skillsMarkup()}${workflowReferenceMarkup()}`;
  document.body.appendChild(container);
});

afterEach(() => {
  container.remove();
  document.documentElement.classList.remove('dark');
});

describe('skills table visual parity (MoonMind#3346)', () => {
  it.each(['light', 'dark'] as const)(
    'runs body rows transparent over the page surface in the %s theme with legible text and no faded-table opacity',
    (theme) => {
      if (theme === 'dark') {
        document.documentElement.classList.add('dark');
      }
      const { slab, table, bodyRows, headerCells, bodyCells } = queries();

      // The slab is not a card: transparent fill, no border or shadow.
      expect(getComputedStyle(slab).backgroundColor).toBe('rgba(0, 0, 0, 0)');
      expect(getComputedStyle(slab).borderTopWidth).toBe('0px');

      // Both odd and even body rows run transparent (never `opacity` on the
      // whole table, which would also fade text and controls).
      for (const row of bodyRows) {
        expect(getComputedStyle(row).backgroundColor).toBe('rgba(0, 0, 0, 0)');
      }
      expect(getComputedStyle(table).opacity).toBe('1');

      // The sticky header keeps the translucent workflow treatment: neither
      // fully opaque nor fully transparent.
      for (const th of headerCells) {
        const alpha = alphaOf(getComputedStyle(th).backgroundColor);
        expect(alpha).not.toBeNull();
        expect(alpha!).toBeGreaterThan(0.9);
        expect(alpha!).toBeLessThan(1);
      }
      expect(getComputedStyle(headerCells[0]!).position).toBe('sticky');

      // Text stays legible: header and body cells render in the ink color,
      // not faded out with the transparent surfaces.
      expect(alphaOf(getComputedStyle(headerCells[0]!).color)).toBe(1);
      expect(alphaOf(getComputedStyle(bodyCells[0]!).color)).toBe(1);
    },
  );

  it('draws row dividers and the header rule with the shared workflow list token', () => {
    const { bodyCells, headerCells, referenceCell } = queries();

    const expectedDivider = getComputedStyle(referenceCell).borderBottomColor;
    expect(expectedDivider).not.toBe('rgba(0, 0, 0, 0)');

    // Every Skills body cell divider resolves to the same computed color as
    // the Workflow list divider — not the generic 0.65 table rule.
    for (const cell of bodyCells) {
      expect(getComputedStyle(cell).borderBottomColor).toBe(expectedDivider);
    }

    // The header rule uses the same token via box-shadow (both computed in
    // the same engine, so the serialized color must match exactly).
    const headerShadow = getComputedStyle(headerCells[0]!).boxShadow;
    expect(headerShadow).not.toBe('none');
    expect(headerShadow.replace(/\s+/g, ' ')).toContain(expectedDivider.replace(/\s+/g, ' '));
  });

  it('extends header and divider edges to the collection-content boundary without document overflow', () => {
    const { primary, slab, table, headerCells, bodyCells } = queries();
    const bleed = bleedPx(primary);

    // The bleed token tracks the primary's own responsive padding.
    expect(Math.abs(Number.parseFloat(getComputedStyle(primary).paddingLeft) - bleed)).toBeLessThan(1);
    expect(Math.abs(Number.parseFloat(getComputedStyle(primary).paddingRight) - bleed)).toBeLessThan(1);

    // The bled slab spans the primary border-box edge to edge, so header and
    // row-divider edges land on the collection boundary.
    const primaryRect = primary.getBoundingClientRect();
    const slabRect = slab.getBoundingClientRect();
    expect(Math.abs(slabRect.left - primaryRect.left)).toBeLessThan(1.5);
    expect(Math.abs(slabRect.right - primaryRect.right)).toBeLessThan(1.5);

    // The header spans the full slab width: it must reach (or pass) both
    // slab edges rather than ending inside a side margin. One-sided
    // containment keeps this robust when the fixed-width catalog legitimately
    // overflows the slab (the narrow-viewport scroll container then owns it).
    const headerRow = headerCells[0]!.closest('tr')!.getBoundingClientRect();
    expect(headerRow.left).toBeLessThanOrEqual(slabRect.left + 1.5);
    expect(headerRow.right).toBeGreaterThanOrEqual(slabRect.right - 1.5);

    // Readable cell padding is preserved: edge cells re-inset text by the
    // same bleed amount.
    expect(Math.abs(Number.parseFloat(getComputedStyle(headerCells[0]!).paddingLeft) - bleed)).toBeLessThan(1);
    const lastHeader = headerCells[headerCells.length - 1]!;
    expect(Math.abs(Number.parseFloat(getComputedStyle(lastHeader).paddingRight) - bleed)).toBeLessThan(1);

    // Long prose wraps inside the description column instead of forcing
    // horizontal document overflow, even with a long skill name present.
    const descriptionCell = container.querySelector<HTMLElement>(
      'td[data-column-key="description"]',
    )!;
    expect(getComputedStyle(descriptionCell).overflowWrap).toBe('anywhere');
    expect(getComputedStyle(table).whiteSpace).toBe('normal');
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(
      document.documentElement.clientWidth + 1,
    );
    expect(bodyCells.length).toBeGreaterThan(0);
  });

  it('keeps long names wrapped with no document overflow at a smaller desktop width', () => {
    // Emulate an ordinary smaller desktop/tablet primary width, left-aligned
    // so catalog spill extends into empty viewport instead of centering
    // slack. The table is not responsive, so the description column must wrap
    // rather than push the document wider than the viewport.
    const { primary } = queries();
    primary.style.maxWidth = '700px';

    const descriptionCell = container.querySelector<HTMLElement>(
      'td[data-column-key="description"]',
    )!;
    expect(getComputedStyle(descriptionCell).overflowWrap).toBe('anywhere');
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(
      document.documentElement.clientWidth + 1,
    );
  });
});
