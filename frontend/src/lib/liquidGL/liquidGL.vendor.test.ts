import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

describe("liquidGL vendor html2canvas integration", () => {
  it("sanitizes modern CSS color functions before html2canvas parses snapshots", () => {
    const source = readFileSync(
      `${process.cwd()}/frontend/src/lib/liquidGL/liquidGL.vendor.js`,
      "utf8",
    );

    expect(source).toContain("function sanitizeCloneForHtml2Canvas");
    expect(source).toContain("colorFunctionToRgba");
    expect(source).toContain("color-mix\\(");
    expect(source.match(/onclone:\s*sanitizeCloneForHtml2Canvas/g)).toHaveLength(
      2,
    );
  });
});
