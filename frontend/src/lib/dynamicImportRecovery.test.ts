import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  isDynamicImportLoadError,
  reloadOnceForDynamicImportError,
} from './dynamicImportRecovery';

describe('dynamic import recovery', () => {
  const reload = vi.fn();

  beforeEach(() => {
    window.sessionStorage.clear();
    reload.mockClear();
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: {
        ...window.location,
        reload,
      },
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    window.sessionStorage.clear();
  });

  it('detects browser dynamic import load failures', () => {
    expect(
      isDynamicImportLoadError(
        new Error(
          'Failed to fetch dynamically imported module: /static/workflow_console/dist/assets/schedules-old.js',
        ),
      ),
    ).toBe(true);
    expect(isDynamicImportLoadError(new Error('Importing a module script failed.'))).toBe(
      true,
    );
    expect(isDynamicImportLoadError('error loading dynamically imported module')).toBe(
      true,
    );
  });

  it('does not classify ordinary render errors as dynamic import failures', () => {
    expect(isDynamicImportLoadError(new Error('Cannot read properties of undefined'))).toBe(
      false,
    );
  });

  it('reloads once per build id and stores the session guard', () => {
    expect(reloadOnceForDynamicImportError('build-123')).toBe(true);
    expect(reload).toHaveBeenCalledTimes(1);
    expect(
      window.sessionStorage.getItem(
        'moonmind.dashboard.dynamic-import-reload:build-123',
      ),
    ).toBe('1');

    expect(reloadOnceForDynamicImportError('build-123')).toBe(false);
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it('uses an unknown build guard when no build id is available', () => {
    expect(reloadOnceForDynamicImportError(null)).toBe(true);
    expect(reload).toHaveBeenCalledTimes(1);
    expect(
      window.sessionStorage.getItem('moonmind.dashboard.dynamic-import-reload:unknown'),
    ).toBe('1');
  });
});
