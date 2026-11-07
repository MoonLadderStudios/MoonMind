import { afterEach, expect, it, vi } from 'vitest';
import { screen, renderWithClient } from '../utils/test-utils';
import { RepositoryScannerReadiness } from './RepositoryScannerReadiness';

afterEach(() => vi.restoreAllMocks());

it('shows configuration and diagnostics without claiming a verified scan or starting work', async () => {
  const fetcher = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({
    status: 'unverified', imageSourceRef: 'repository-security', sourceImage: 'example/scanner:custom',
    scanners: ['gitleaks', 'opengrep', 'trivy'], ruleSelection: 'image-default',
    ruleCoverage: 'Narrow Python, JavaScript and TypeScript eval/shell rules.',
    databaseCacheRef: 'scanner-db', refreshCacheRef: 'scanner-feeds', feedCacheConfigured: false,
    feedSnapshot: 'unverified', refreshPolicies: [],
    diagnostics: [{ code: 'feed_cache_unavailable', action: 'Configure shared cache aliases.' }],
    verificationNote: 'Configuration only. No scan or refresh is started here.',
  }), { status: 200 }));
  renderWithClient(<RepositoryScannerReadiness />);
  expect(await screen.findByText('example/scanner:custom')).toBeTruthy();
  expect(screen.getByText(/Configure shared cache aliases/)).toBeTruthy();
  expect(screen.getByText(/image-default/)).toBeTruthy();
  expect(screen.getAllByText(/Unverified/).length).toBeGreaterThan(0);
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(fetcher.mock.calls[0]?.[0]).toBe('/api/v1/settings/repository-scanners/readiness');
  expect(fetcher.mock.calls[0]?.[1]?.method ?? 'GET').toBe('GET');
});

it('shows unavailable evidence honestly on failure', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{}', { status: 503 }));
  renderWithClient(<RepositoryScannerReadiness />);
  expect(await screen.findByText(/Unable to load scanner configuration/)).toBeTruthy();
  expect(screen.queryByText('Ready')).toBeNull();
});
