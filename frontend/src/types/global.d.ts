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

declare module "node:module" {
  export function createRequire(path: string): (id: string) => unknown;
}

declare module "node:path" {
  export function resolve(...paths: string[]): string;
}
