// @vitest-environment node
import { beforeAll, describe, expect, it } from "vitest";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import postcss, { type Plugin, type Root } from "postcss";

const require = createRequire(import.meta.url);
const config = require("../../postcss.config.cjs") as {
  plugins: Record<string, Record<string, unknown>>;
};
let styles: Root;

beforeAll(async () => {
  const from = resolve("frontend/src/styles/dashboard.css");
  const plugins = Object.entries(config.plugins).map(([name, options]) => {
    const createPlugin = require(name) as (options: Record<string, unknown>) => Plugin;
    return createPlugin(options);
  });
  const result = await postcss(plugins).process(readFileSync(from, "utf8"), {
    from,
  });
  styles = result.root;
});

describe("dashboard Tailwind compilation", () => {
  it("compiles the production stylesheet and source-scanned utilities", () => {
    const selectors: string[] = [];
    styles.walkRules((rule) => { selectors.push(rule.selector); });
    expect(selectors).toContain(".grid-cols-2");
    expect(selectors).toContain(".text-mm-ink");
    expect(selectors).toContain(".rounded-mm");
    const remainingDirectives: string[] = [];
    styles.walkAtRules("tailwind", (rule) => { remainingDirectives.push(rule.params); });
    expect(remainingDirectives).toEqual([]);
  });

  it("retains the dashboard's own base styles without adding Preflight", () => {
    const preflightResets: string[] = [];
    styles.walkRules((rule) => {
      if (rule.selector.includes("::file-selector-button")) {
        preflightResets.push(rule.selector);
      }
    });
    expect(preflightResets).toEqual([]);
    expect(styles.toString()).toContain("--mm-font-sans");
  });

  it("compiles theme tokens, class dark mode, and existing small effects", () => {
    const declarations = (selector: string, property: string) => {
      const values: string[] = [];
      styles.walkRules(selector, (rule) => {
        rule.walkDecls(property, (declaration) => { values.push(declaration.value); });
      });
      return values;
    };
    expect(declarations(".text-mm-ink", "color")).toEqual(["rgb(var(--mm-ink) / 1)"]);
    expect(declarations(".rounded-mm", "border-radius")).toEqual(["0.9rem"]);
    expect(declarations(".shadow-sm", "--tw-shadow")[0]).toContain("0 1px 2px 0");
    expect(declarations(".backdrop-blur-sm", "--tw-backdrop-blur")).toEqual(["blur(4px)"]);
    const darkSelectors: string[] = [];
    styles.walkRules((rule) => {
      if (rule.selector.includes(".dark\\:text-white")) darkSelectors.push(rule.selector);
    });
    expect(darkSelectors).toEqual([".dark\\:text-white:is(.dark *)"]);
  });
});
