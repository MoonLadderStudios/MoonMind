// Temporary, credential-free documentation capture. Uses the production UI unchanged.
import { createServer } from 'node:http';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { resolve, extname } from 'node:path';
import { chromium } from 'playwright';

const root = process.cwd();
const output = resolve(root, 'readme-preview');
await mkdir(output, { recursive: true });
const manifest = JSON.parse(await readFile('api_service/static/workflow_console/dist/.vite/manifest.json', 'utf8'));
const entry = manifest['entrypoints/dashboard.tsx'];
const base = '/static/workflow_console/dist/';
const assets = [...(entry.css || []).map(path => `<link rel="stylesheet" href="${base}${path}">`), `<script type="module" src="${base}${entry.file}"></script>`].join('\n');
const shell = (await readFile('api_service/templates/react_dashboard.html', 'utf8'))
  .replace('{{ assets_html | safe }}', assets)
  .replace('{{ boot_payload | safe }}', JSON.stringify({ page: 'dashboard', apiBase: '/api' }));
const stamp = '2026-10-07T10:30:00Z';
const names = ['Review the API changes', 'Add retry coverage', 'Refresh the setup guide', 'Check dependency updates'];
const statuses = ['executing', 'scheduled', 'completed', 'failed'];
const rows = names.map((title, i) => ({
  taskId: `example-${i + 1}`, workflowId: `example-${i + 1}`, source: 'temporal',
  workflowType: 'MoonMind.UserWorkflow', title, status: statuses[i], state: statuses[i], rawState: statuses[i],
  repository: 'MoonLadderStudios/MoonMind', targetRuntime: 'omnigent',
  targetSkill: i === 0 ? 'pr-review' : null, createdAt: stamp, updatedAt: stamp,
  scheduledFor: i === 1 ? '2026-10-08T02:00:00Z' : null,
  progress: { total: 3, completed: i === 0 ? 1 : i === 2 ? 3 : 0, executing: i === 0 ? 1 : 0,
    failed: i === 3 ? 1 : 0, currentStepTitle: ['Review changes', 'Waiting for schedule', 'Complete', 'Check dependencies'][i] },
}));
const detail = {
  ...rows[2], namespace: 'default', temporalRunId: 'example-run', runId: 'example-run',
  summary: 'Updated the setup guide and checked the documented commands. Changes and verification notes are saved with this run.',
  taskInstructions: 'Simplify the setup guide. Check the startup commands and keep the examples current.',
  startingBranch: 'main', targetBranch: 'main', profileId: 'docs-example', providerLabel: 'Codex',
  taskSkills: ['document-author'], publishMode: 'none',
  startedAt: '2026-10-07T10:15:00Z', closedAt: stamp,
  stepsHref: '/api/executions/example-3/steps', actions: {},
};
const steps = {
  workflowId: 'example-3', runId: 'example-run', runScope: 'latest',
  steps: ['Read the existing guide', 'Update the documentation', 'Verify commands and links'].map((title, index) => ({
    logicalStepId: `step-${index + 1}`, order: index + 1, title, tool: { type: 'skill', name: 'document-author', version: '1' },
    dependsOn: [], status: 'completed', attentionRequired: false, executionOrdinal: 1,
    startedAt: '2026-10-07T10:15:00Z', updatedAt: stamp, summary: 'Complete', checks: [], refs: {}, artifacts: {},
  })),
};
const requests = [];
const unknown = new Set();
const mime = { '.js': 'text/javascript', '.css': 'text/css', '.woff2': 'font/woff2', '.png': 'image/png', '.svg': 'image/svg+xml' };
const server = createServer(async (req, res) => {
  try {
    const url = new URL(req.url, 'http://127.0.0.1');
    const path = decodeURIComponent(url.pathname);
    requests.push(path);
    if (path.startsWith('/static/')) {
      const file = resolve(root, 'api_service', `.${path}`);
      if (!file.startsWith(resolve(root, 'api_service/static') + '/')) { res.writeHead(403); res.end(); return; }
      res.writeHead(200, { 'Content-Type': mime[extname(file)] || 'application/octet-stream' });
      res.end(await readFile(file)); return;
    }
    if (path.startsWith('/workflows')) { res.writeHead(200, { 'Content-Type': 'text/html' }); res.end(shell); return; }
    let data;
    if (path === '/api/ui/info') data = { app: 'MoonMind', apiBase: '/api', features: { workflowList: true, workflowActions: true, settingsProvidersSecrets: true, settingsInstance: true, settingsOperations: true } };
    else if (path === '/api/v1/provider-profiles') data = [{ profile_id: 'docs-example', enabled: true, launch_ready: true }];
    else if (path === '/api/executions') data = { items: rows, count: rows.length, total: rows.length };
    else if (path === '/api/executions/facets') data = { facet: url.searchParams.get('facet') || 'status', items: [], values: [] };
    else if (path === '/api/executions/example-3') data = detail;
    else if (path === '/api/executions/example-3/steps') data = steps;
    else if (path.endsWith('/artifacts')) data = { artifacts: [] };
    else { unknown.add(path); res.writeHead(404); res.end('{}'); return; }
    res.writeHead(200, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(data));
  } catch (error) { res.writeHead(500); res.end(String(error)); }
});
await new Promise(resolveReady => server.listen(0, '127.0.0.1', resolveReady));
const origin = `http://127.0.0.1:${server.address().port}`;
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1, colorScheme: 'dark' });
const errors = [];
page.on('pageerror', error => errors.push(error.message));
await page.route('**/*', route => route.request().url().startsWith(origin + '/') ? route.continue() : route.abort());
await page.addInitScript(() => localStorage.setItem('moonmind.theme', 'dark'));
await page.clock.install({ time: new Date('2026-10-07T11:00:00Z') });
try {
  await page.goto(`${origin}/workflows`);
  await page.getByRole('row', { name: /Refresh the setup guide/ }).waitFor();
  await page.evaluate(() => document.fonts.ready);
  if (await page.locator('html').getAttribute('data-theme') !== 'dark') throw new Error('Dark theme not applied');
  await page.screenshot({ path: resolve(output, 'workflow-list.png') });
  await page.goto(`${origin}/workflows/example-3/overview?source=temporal`);
  await page.getByRole('heading', { name: 'Refresh the setup guide' }).waitFor();
  await page.getByText(detail.summary, { exact: true }).waitFor();
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: resolve(output, 'workflow-detail.png'), fullPage: true });
  await writeFile(resolve(output, 'capture-evidence.json'), JSON.stringify({ theme: 'dark', viewport: { width: 1440, height: 1000 }, syntheticData: true, unknownRoutes: [...unknown], pageErrors: errors, requests }, null, 2));
  if (errors.length) throw new Error(errors.join('\n'));
} finally { await browser.close(); server.close(); }
