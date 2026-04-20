interface MarkedGlobal {
  parse(markdown: string): string;
}

interface Window {
  marked?: MarkedGlobal;
}

declare const process: {
  cwd(): string;
};

declare module "node:fs" {
  export function readFileSync(path: string, encoding: "utf8"): string;
}
