import inputSchema from '../runtime/fixtures/assessment-authorization-scope.json';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, renderWithClient, screen, waitFor, within } from './test-utils';
import { InstanceSettingsPage } from '../entrypoints/settings';
import type { BootPayload } from '../boot/parseBootPayload';
export function assessmentAuthorizationNativeCases(user: {
  click: (element: HTMLElement) => Promise<unknown>;
  fill: (element: HTMLElement, value: string) => Promise<unknown>;
  selectOptions: (element: HTMLElement, value: string | string[]) => Promise<unknown>;
}) {
const key = 'security.assessment_authorizations';
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
it.each(['cloud', 'web'])('authors native %s authorization from the actual descriptor schema', async kind => {
  let saved: Record<string, unknown> | undefined;
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (_url, init) => {
    if (init?.method === 'PATCH') {
      saved = JSON.parse(String(init.body)).changes[key].authorizations[0];
      return new Response('{}');
    }
    return new Response(JSON.stringify({ categories: { Security: [{ key, input_schema: inputSchema, effective_value: { authorizations: [] }, value_version: 9, read_only: false }] } }));
  });
  renderWithClient(<MemoryRouter><InstanceSettingsPage payload={{} as BootPayload} /></MemoryRouter>);
  await user.click(await screen.findByRole('button', { name: 'Add authorization' }));
  const fill = async (label: string, value: string) => {
    const element = screen.getByLabelText(content => content.toLowerCase() === label.toLowerCase());
    if (element.tagName === 'SELECT') await user.selectOptions(element, value);
    else await user.fill(element, value);
  };
  await fill('Authorization reference', `${kind}-review`); await fill('Authorization name', `${kind} review`); await fill('Authority source', 'Explicit fixture permission');
  await fill('Valid from (ISO time with timezone)', '2026-01-01T00:00:00Z'); await fill('Valid until (ISO time with timezone)', '2026-12-01T00:00:00Z');
  await fill('Total job seconds', '3600'); await fill('Assessment ref', `${kind}-assessment`); await fill('Profile refs', kind === 'cloud' ? 'cloud-posture' : 'web-assessment');
  await fill('Action classes', kind === 'cloud' ? 'cloud-posture' : 'web-passive'); await fill('Max job seconds', '1800'); await fill('Evidence intent', 'Retain bounded reports');
  await user.click(screen.getByRole('button', { name: 'Add Targets item' }));
  if (kind === 'cloud') {
    await fill('Cloud account', '123456789012'); await fill('Cloud provider', 'aws'); await fill('Cloud regions', 'us-east-1');
    await user.click(screen.getByRole('button', { name: 'Add Grants item' }));
    const grant = within(screen.getByRole('group', { name: 'Grants item 1' }));
    for (const [label, value] of Object.entries({ Ref: 'vault://aws-reader', Purpose: 'cloud_inventory', Account: '123456789012', 'Role arn': 'arn:aws:iam::123456789012:role/ReadOnly', 'Expires at': '2026-11-30T00:00:00Z', 'Profile refs': 'cloud-posture' })) {
      const element = grant.getByLabelText(content => content.toLowerCase() === label.toLowerCase());
      if (element.tagName === 'SELECT') await user.selectOptions(element, value); else await user.fill(element, value);
    }
  } else {
    await fill('Web origin', 'https://example.test');
    await user.click(screen.getByRole('button', { name: /Add Web routes item/i }));
    const route = within(screen.getByRole('group', { name: /Web routes item 1/i }));
    await user.fill(route.getByLabelText('Path'), '/approved');
    const methods = route.getByLabelText('Methods') as HTMLSelectElement;
    await user.selectOptions(methods, ['GET']);
  }
  await user.click(screen.getByRole('button', { name: 'Save assessment authorizations' }));
  await waitFor(() => expect(saved).toBeTruthy());
  expect(saved!.enabled).toBe(false);
  const scope = saved!.scope as Record<string, unknown>;
  expect(scope.targets).toEqual(kind === 'cloud'
    ? [{ cloudAccount: '123456789012', cloudProvider: 'aws', cloudRegions: ['us-east-1'] }]
    : [{ webOrigin: 'https://example.test', webRoutes: [{ path: '/approved', methods: ['GET'] }] }]);
  expect(scope.profileRefs).toEqual([kind === 'cloud' ? 'cloud-posture' : 'web-assessment']);
  expect(scope.grants).toEqual(kind === 'cloud' ? [{ ref: 'vault://aws-reader', purpose: 'cloud_inventory', account: '123456789012', roleArn: 'arn:aws:iam::123456789012:role/ReadOnly', expiresAt: '2026-11-30T00:00:00Z', profileRefs: ['cloud-posture'] }] : []);
  expect(Object.hasOwn(scope, 'engagement')).toBe(false);
});

}
