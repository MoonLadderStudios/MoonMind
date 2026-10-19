// @vitest-environment node
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

import postcss, { type Declaration } from 'postcss';
import { describe, expect, it } from 'vitest';

// Static guard for CSS custom properties in the dashboard stylesheet. A var()
// with no fallback must name a property that something defines: any rule in
// the stylesheet (scoped variables such as .button's --button-bg count) or a
// component that sets it inline. Colour tokens hold bare RGB triplets
// ("214 220 235"), so they only work inside rgb(); used directly as a colour
// the declaration is invalid and silently dropped.

const srcRoot = `${process.cwd()}/frontend/src`;
const css = readFileSync(`${srcRoot}/styles/dashboard.css`, 'utf8');
const root = postcss.parse(css);

function sourceFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return entry.name === 'generated' ? [] : sourceFiles(path);
    return /\.(ts|tsx)$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name) ? [path] : [];
  });
}

const definitions = new Map<string, string[]>();
root.walkDecls((decl) => {
  if (decl.prop.startsWith('--')) {
    definitions.set(decl.prop, [...(definitions.get(decl.prop) ?? []), decl.value.trim()]);
  }
});

// Properties components set through inline styles or setProperty().
const runtimeProperties = new Set(
  sourceFiles(srcRoot).flatMap((file) =>
    Array.from(readFileSync(file, 'utf8').matchAll(/['"`](--[a-z][\w-]*)['"`]/g), (match) => match[1]!),
  ),
);

type VarUse = { name: string; hasFallback: boolean; decl: Declaration; offset: number };

function varUses(): VarUse[] {
  const uses: VarUse[] = [];
  root.walkDecls((decl) => {
    for (const match of decl.value.matchAll(/var\(\s*(--[\w-]+)\s*(,)?/g)) {
      uses.push({ name: match[1]!, hasFallback: Boolean(match[2]), decl, offset: match.index ?? 0 });
    }
  });
  return uses;
}

function describeUse(use: VarUse): string {
  const parent = use.decl.parent as { selector?: string } | undefined;
  return `${parent?.selector?.replace(/\s+/g, ' ') ?? '?'} { ${use.decl.prop}: ${use.decl.value.replace(/\s+/g, ' ')} }`;
}

describe('dashboard CSS custom properties', () => {
  it('defines every variable used without a fallback', () => {
    const undefinedUses = varUses().filter(
      (use) => !use.hasFallback && !definitions.has(use.name) && !runtimeProperties.has(use.name),
    );
    expect(undefinedUses.map(describeUse)).toEqual([]);
  });

  it('only reads bare RGB triplet tokens inside rgb()', () => {
    // A property that only ever aliases a triplet (`--x: var(--mm-accent)`) is
    // a triplet too, and the alias declaration itself is not a colour use.
    const alias = /^var\(\s*(--[\w-]+)\s*\)$/;
    const triplets = new Set<string>();
    for (let changed = true; changed; ) {
      changed = false;
      for (const [name, values] of definitions) {
        if (triplets.has(name)) continue;
        if (values.every((value) => /^\d+ \d+ \d+$/.test(value) || triplets.has(value.match(alias)?.[1] ?? ''))) {
          triplets.add(name);
          changed = true;
        }
      }
    }
    expect(triplets.has('--mm-border')).toBe(true);
    const misused = varUses().filter(
      (use) =>
        triplets.has(use.name) &&
        !(use.decl.prop.startsWith('--') && alias.test(use.decl.value.trim())) &&
        !/rgba?\(\s*$/.test(use.decl.value.slice(0, use.offset)),
    );
    expect(misused.map(describeUse)).toEqual([]);
  });
});
