interface MarkedGlobal {
  parse(markdown: string): string;
}

interface Window {
  marked?: MarkedGlobal;
}
