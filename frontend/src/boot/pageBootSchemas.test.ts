import { describe, it, expect } from 'vitest';

import type { BootPayload } from './parseBootPayload';
import { validatePageBoot } from './pageBootSchemas';

function bootPayload(overrides: Partial<BootPayload> = {}): BootPayload {
  return { page: 'workflows-home', apiBase: '/api', ...overrides };
}

describe('validatePageBoot', () => {
  it('accepts a valid shared layout envelope', () => {
    const result = validatePageBoot(
      'workflows-home',
      bootPayload({ initialData: { layout: { dataWidePanel: true } } }),
    );
    expect(result.ok).toBe(true);
  });

  it('accepts missing initialData', () => {
    expect(validatePageBoot('workflows-home', bootPayload()).ok).toBe(true);
  });

  it('passes through unknown page-specific keys', () => {
    const result = validatePageBoot(
      'workflows-home',
      bootPayload({ initialData: { dashboardConfig: { anything: 1 } } }),
    );
    expect(result.ok).toBe(true);
  });

  it('rejects an invalid layout envelope with a clear message', () => {
    const result = validatePageBoot(
      'workflows-home',
      bootPayload({ initialData: { layout: { dataWidePanel: 'yes' } } }),
    );
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.message).toContain('layout.dataWidePanel');
    }
  });
});
