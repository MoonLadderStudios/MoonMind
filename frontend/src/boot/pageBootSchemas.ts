import { z } from 'zod';

import type { BootPayload } from './parseBootPayload';

/**
 * Page-specific boot validation (MM-960).
 *
 * After the shell dispatches to a page, the boot payload's `initialData` is
 * validated against a page-specific schema. This catches malformed server-rendered
 * boot data early and lets the shell render a clear configuration error instead of
 * letting a page crash on bad data.
 *
 * This is additive: it does not replace page-local Zod validation. Pages without a
 * registered schema are validated against {@link SharedInitialDataSchema}, which only
 * checks the shell-owned `layout` envelope and passes through everything else.
 */

const SharedLayoutSchema = z
  .object({
    dataWidePanel: z.boolean().optional(),
  })
  .passthrough();

/** Envelope shared by every page: the shell reads `initialData.layout`. */
export const SharedInitialDataSchema = z
  .object({
    layout: SharedLayoutSchema.optional(),
  })
  .passthrough();

/**
 * Per-page overrides keyed by page id. Pages not listed here fall back to
 * {@link SharedInitialDataSchema}. Keep these intentionally narrow so they only
 * assert the shape the shell depends on.
 */
const PAGE_BOOT_SCHEMAS: Record<string, z.ZodTypeAny> = {};

export type PageBootValidation =
  | { ok: true }
  | { ok: false; message: string };

/** Validate a page's boot `initialData` after dispatch. */
export function validatePageBoot(page: string, payload: BootPayload): PageBootValidation {
  const schema = PAGE_BOOT_SCHEMAS[page] ?? SharedInitialDataSchema;
  const initialData = payload.initialData ?? {};
  const result = schema.safeParse(initialData);
  if (result.success) {
    return { ok: true };
  }
  const issue = result.error.issues[0];
  const path = issue?.path?.length ? `${issue.path.join('.')}: ` : '';
  const message = issue ? `${path}${issue.message}` : 'Invalid page configuration data.';
  return { ok: false, message };
}
