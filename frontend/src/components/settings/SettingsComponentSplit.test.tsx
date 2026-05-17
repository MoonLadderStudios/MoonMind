/**
 * MM-713 — §26 component-split reference test.
 *
 * The Settings System design (`docs/Security/SettingsSystem.md` §26) names a
 * suggested frontend component split:
 *
 *   - SettingsPage
 *   - SettingsCatalogSection (a.k.a. GeneratedSettingsSection)
 *   - SettingControlRenderer (lives inside GeneratedSettingsSection)
 *   - SecretRefPicker (lives inside GeneratedSettingsSection)
 *   - ManagedSecretsPanel (rendered via SecretManager)
 *   - ProviderProfilesPanel (rendered via ProviderProfilesManager)
 *   - OperationsPanel (rendered via OperationsSettingsSection)
 *
 * This file is a static-import reference test: it asserts that the actual
 * frontend modules exist and export the components we intend to expose. The
 * intent is to catch accidental renames / deletions during refactors so the
 * Settings page stays aligned with the §26 layout.
 */

import { describe, expect, it } from 'vitest';

import { GeneratedSettingsSection } from './GeneratedSettingsSection';
import { OperationsSettingsSection } from './OperationsSettingsSection';
import { ProviderProfilesManager } from './ProviderProfilesManager';
import { SecretManager } from '../secrets/SecretManager';
import SettingsPage from '../../entrypoints/settings';

describe('Settings System §26 component split', () => {
  it('exposes a SettingsPage that owns top-level section navigation', () => {
    expect(typeof SettingsPage).toBe('function');
  });

  it('exposes GeneratedSettingsSection as the catalog-driven section renderer', () => {
    expect(typeof GeneratedSettingsSection).toBe('function');
  });

  it('exposes a ManagedSecretsPanel component (SecretManager)', () => {
    expect(typeof SecretManager).toBe('function');
  });

  it('exposes a ProviderProfilesPanel component (ProviderProfilesManager)', () => {
    expect(typeof ProviderProfilesManager).toBe('function');
  });

  it('exposes an OperationsPanel component (OperationsSettingsSection)', () => {
    expect(typeof OperationsSettingsSection).toBe('function');
  });
});
