import { describe, it, expect } from 'vitest';
import { BootPayloadSchema } from './parseBootPayload';

describe('parseBootPayload', () => {
  it('validates a basic payload correctly', () => {
    const valid = { page: 'test' };
    const result = BootPayloadSchema.parse(valid);
    expect(result.page).toBe('test');
    expect(result.apiBase).toBe('/api'); // Default
  });

  it('fails to parse without a page', () => {
    const invalid = { apiBase: '/api' };
    expect(() => BootPayloadSchema.parse(invalid)).toThrow();
  });
});