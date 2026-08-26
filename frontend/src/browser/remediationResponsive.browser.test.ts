import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { page } from 'vitest/browser';

import '../styles/dashboard.css';

// Real-browser guardrail for MoonLadderStudios/MoonMind#3623. Route-level
// tests own the source Workflow Detail -> Create draft -> remediation Detail
// behavior; this harness exercises the production markup and stylesheet at
// the supported desktop/mobile boundaries that jsdom cannot lay out.

const DESKTOP = { width: 1280, height: 800 } as const;
const MOBILE = { width: 375, height: 812 } as const;

let host: HTMLElement;

beforeEach(() => {
  host = document.createElement('main');
  host.innerHTML = `
    <form class="queue-submit-form">
      <section class="card queue-remediation-draft-summary stack" aria-label="Remediation draft">
        <div class="queue-section-heading">
          <h2>Remediation Draft</h2>
          <button type="button" class="secondary" aria-label="Discard remediation draft">Discard draft</button>
        </div>
        <fieldset class="queue-remediation-pinned-target stack" aria-label="Pinned target identity">
          <legend>Pinned target identity (immutable)</legend>
          <div class="grid-2">
            <label>Target workflow<input value="mm:target-with-a-very-long-identity-that-must-not-overflow" readonly /></label>
            <label>Pinned run<input value="run:pinned" readonly /></label>
          </div>
        </fieldset>
        <fieldset class="queue-remediation-repair-intent stack" aria-label="Editable repair intent">
          <legend>Editable repair intent</legend>
          <div class="grid-2">
            <label>Starting branch<input value="main" /></label>
            <label>Checkpoint work branch<input value="remediation/mm-3623-browser-coverage" /></label>
          </div>
        </fieldset>
      </section>
    </form>
    <section class="stack td-remediation-region td-evidence-region" aria-label="Authoritative remediation lifecycle">
      <ul class="td-remediation-list">
        <li class="card">
          <code>artifact-with-a-very-long-unbroken-identity-that-must-wrap-inside-the-remediation-card-on-mobile</code>
          <div class="stack td-remediation-lifecycle">
            <details class="td-remediation-canonical">
              <summary>Authored remediation contract</summary>
              <pre>{"authorityMode":"approval_gated","bounded":true}</pre>
            </details>
          </div>
          <div class="stack td-remediation-approval-controls">
            <label>Decision rationale<textarea rows="2"></textarea></label>
            <button type="button" class="secondary">Approve remediation action</button>
          </div>
        </li>
      </ul>
    </section>
  `;
  document.body.appendChild(host);
});

afterEach(async () => {
  host.remove();
  await page.viewport(DESKTOP.width, DESKTOP.height);
});

describe('remediation authoring and lifecycle responsive layout', () => {
  it('keeps immutable/editable semantics keyboard-visible without horizontal overflow', async () => {
    for (const viewport of [DESKTOP, MOBILE]) {
      await page.viewport(viewport.width, viewport.height);

      const pinned = host.querySelector<HTMLFieldSetElement>('.queue-remediation-pinned-target')!;
      const editable = host.querySelector<HTMLFieldSetElement>('.queue-remediation-repair-intent')!;
      const target = pinned.querySelector<HTMLInputElement>('input')!;
      const startingBranch = editable.querySelector<HTMLInputElement>('input')!;
      const approval = host.querySelector<HTMLButtonElement>('.td-remediation-approval-controls button')!;

      expect(pinned.getAttribute('aria-label')).toBe('Pinned target identity');
      expect(editable.getAttribute('aria-label')).toBe('Editable repair intent');
      expect(target.readOnly).toBe(true);
      expect(startingBranch.readOnly).toBe(false);

      approval.focus();
      expect(document.activeElement).toBe(approval);
      expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(window.innerWidth);
      expect(host.getBoundingClientRect().right).toBeLessThanOrEqual(window.innerWidth);
    }
  });
});
