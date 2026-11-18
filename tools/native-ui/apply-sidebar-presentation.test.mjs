import { test } from 'node:test';
import assert from 'node:assert/strict';
import { patchSidebarPresentation } from './apply-sidebar-presentation.mjs';
const source = '  const [sidebarOpen, setSidebarOpen] = useCanvasSidebar(canvasMode, initialSidebarOpen);';
test('closed overrides viewport default; other presentation modes preserve it', () => {
  const patched = patchSidebarPresentation(source);
  for (const requested of ['closed', 'open', null]) {
    for (const desktop of [true, false]) {
      const state = new Function('searchParams', 'useCanvasSidebar', 'canvasMode', 'initialSidebarOpen', patched + '\nreturn sidebarOpen;')(
        new URLSearchParams(requested ? { sidebar: requested } : {}),
        (_, initializer) => [initializer(), () => {}], false, () => desktop,
      );
      assert.equal(state, requested === 'closed' ? false : desktop);
    }
  }
  assert.equal(patchSidebarPresentation(patched), patched);
});
test('unexpected or ambiguous upstream source fails closed', () => {
  assert.throws(() => patchSidebarPresentation('new upstream implementation'));
  assert.throws(() => patchSidebarPresentation(source + '\n' + source));
});
