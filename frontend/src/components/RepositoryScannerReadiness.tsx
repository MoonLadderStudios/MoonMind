import { useQuery } from '@tanstack/react-query';
import { z } from 'zod';

const ReadinessSchema = z.object({
  status: z.literal('unverified'),
  imageSourceRef: z.string(),
  sourceImage: z.string().nullable(),
  scanners: z.array(z.string()),
  ruleSelection: z.string(),
  ruleCoverage: z.string(),
  databaseCacheRef: z.string(),
  refreshCacheRef: z.string(),
  feedCacheConfigured: z.boolean(),
  refreshPolicies: z.array(z.object({ policyRef: z.string(), workflowIds: z.array(z.string()) })),
  diagnostics: z.array(z.object({ code: z.string(), action: z.string() })),
  verificationNote: z.string(),
});

export function RepositoryScannerReadiness() {
  const query = useQuery({
    queryKey: ['repository-scanner-readiness'],
    queryFn: async ({ signal }) => {
      const response = await fetch('/api/v1/settings/repository-scanners/readiness', { signal });
      if (!response.ok) throw new Error('Scanner configuration unavailable');
      return ReadinessSchema.parse(await response.json());
    },
    staleTime: 30_000,
    retry: false,
  });
  const data = query.data;
  return (
    <details className="rounded-3xl border border-mm-border/80 p-4" aria-label="Repository scanner readiness">
      <summary className="cursor-pointer font-semibold">Repository scanners · Unverified</summary>
      <div className="mt-3 space-y-2 text-sm">
        {query.isPending ? <p>Loading scanner configuration...</p> : null}
        {query.isError ? <p role="status">Unable to load scanner configuration. Scanner readiness remains Unverified.</p> : null}
        {data ? <>
          <p>{data.verificationNote}</p>
          <dl className="space-y-2">
            <div><dt>Configured image ({data.imageSourceRef})</dt><dd className="break-all">{data.sourceImage ?? 'Not configured'}</dd></div>
            <div><dt>Supported scanners</dt><dd>{data.scanners.join(', ')}. Installed binaries: Unverified.</dd></div>
            <div><dt>Default rules: {data.ruleSelection}</dt><dd>{data.ruleCoverage}</dd></div>
            <div><dt>Feed cache</dt><dd>{data.databaseCacheRef} (read-only), {data.refreshCacheRef} (refresh). Shared volume: {data.feedCacheConfigured ? 'configured' : 'not configured'}. Current snapshot and freshness: Unverified.</dd></div>
            <div><dt>Refresh egress</dt><dd>{data.refreshPolicies.length ? data.refreshPolicies.map(policy => `${policy.policyRef} (workflows: ${policy.workflowIds.join(', ')})`).join('; ') : 'No matching deployment policy configured'}. Connectivity: Unverified.</dd></div>
          </dl>
          {data.diagnostics.length ? <ul className="list-disc pl-5">{data.diagnostics.map(item => <li key={item.code}><strong>{item.code}</strong>: {item.action}</li>)}</ul> : null}
          <p>Scans keep source access read-only and offline. If no usable Trivy snapshot exists, use an explicitly authorized feed refresh or offline feed import. Explicit image, rule and feed selections remain supported.</p>
        </> : null}
      </div>
    </details>
  );
}
