import { fetchFrameworkPacks, frameworkLabel, frameworkSelection } from './assessmentProjects';

/**
 * Options a schema field reads from a server catalog instead of a static enum.
 * A field opts in with `x-moonmind-options: <source>`; new catalog entries
 * become choices without a frontend change, and unavailable entries keep
 * their reason and cannot be chosen.
 */
export interface SchemaSourceOption {
  key: string;
  value: unknown;
  label: string;
  disabled: boolean;
  reason?: string;
}

/** Stable identity for an option value, independent of key order. */
export function schemaOptionKey(value: unknown): string {
  const canonical = (item: unknown): unknown =>
    item && typeof item === 'object' && !Array.isArray(item)
      ? Object.fromEntries(Object.keys(item as Record<string, unknown>).sort().map((key) => [key, canonical((item as Record<string, unknown>)[key])]))
      : Array.isArray(item) ? item.map(canonical) : item;
  return typeof value === 'string' ? value : JSON.stringify(canonical(value));
}

const SOURCES: Record<string, () => Promise<SchemaSourceOption[]>> = {
  'framework-packs': async () => (await fetchFrameworkPacks()).map((pack) => {
    const value = frameworkSelection(pack);
    const selectable = pack.available && value !== undefined;
    const reason = pack.reason || (value === undefined ? 'This entry has no selectable edition.' : undefined);
    return {
      key: value === undefined ? `unselectable:${pack.source}:${pack.id}` : schemaOptionKey(value),
      value: value ?? null,
      label: [frameworkLabel(pack), pack.edition, pack.revision && `rev ${pack.revision}`].filter(Boolean).join(' · '),
      disabled: !selectable,
      ...(!selectable && reason ? { reason } : {}),
    };
  }),
};

// Last loaded options per source, so submission validation can refuse a
// saved or rerun selection that has since become unavailable.
const loaded = new Map<string, SchemaSourceOption[]>();

export function schemaOptionSourceName(schema: Record<string, unknown>): string | null {
  const name = schema['x-moonmind-options'];
  return typeof name === 'string' && name in SOURCES ? name : null;
}

export async function loadSchemaOptions(name: string): Promise<SchemaSourceOption[]> {
  const options = await SOURCES[name]!();
  loaded.set(name, options);
  return options;
}

/** Why a selected value cannot be submitted, when the source is loaded. */
export function unavailableSchemaOption(name: string, value: unknown): string | null {
  const options = loaded.get(name);
  if (!options) return null;
  const match = options.find((option) => option.key === schemaOptionKey(value));
  if (!match) return 'is no longer in the catalog';
  return match.disabled ? `is unavailable: ${match.reason || 'not selectable'}` : null;
}

export function schemaOptionLabel(name: string, value: unknown): string {
  const match = loaded.get(name)?.find((option) => option.key === schemaOptionKey(value));
  if (match) return match.label;
  const record = value && typeof value === 'object' ? value as Record<string, unknown> : {};
  return String(record.id || record.packId || value);
}
