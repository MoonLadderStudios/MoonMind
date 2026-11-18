#!/usr/bin/env node
// v0.22.0 dropped the explicit closed initializer present in our previous
// Omnigent revision. Restore that contract before compiling the product UI.
// Do not patch generated bundles or force state after mounting: users must
// still be able to open the sidebar, and responsive state must remain native.
import { readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const before = '  const [sidebarOpen, setSidebarOpen] = useCanvasSidebar(canvasMode, initialSidebarOpen);';
const after = `  const sidebarRequested = searchParams.get("sidebar");
  const [sidebarOpen, setSidebarOpen] = useCanvasSidebar(
    canvasMode,
    () => sidebarRequested !== "closed" && initialSidebarOpen(),
  );`;
export function patchSidebarPresentation(source) {
  if (source.split(after).length === 2 && !source.includes(before)) return source;
  if (source.split(before).length !== 2 || source.includes('const sidebarRequested =')) {
    throw new Error('Native sidebar source changed; review the upstream presentation contract before building.');
  }
  return source.replace(before, after);
}
if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const file = resolve(process.argv[2] || 'web/src/shell/AppShell.tsx');
  writeFileSync(file, patchSidebarPresentation(readFileSync(file, 'utf8')));
}
