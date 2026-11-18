import { afterEach, expect, it, vi } from 'vitest';
import { BrowserRouter } from 'react-router-dom';
import { renderWithClient, screen, fireEvent } from '../utils/test-utils';
import { OperationsSettingsPage, ProvidersSecretsSettingsPage } from './settings';
vi.mock('../components/settings/GeneratedSettingsSection', () => ({
 fetchCatalog: () => new Promise(() => {}),
 GeneratedSettingsSection: ({ keys, excludeKeys }: {keys?: string[]; excludeKeys?: string[]}) => <div data-testid="settings-owner">{JSON.stringify({keys, excludeKeys})}</div>,
}));
vi.mock('../components/settings/NewWorkDefaultsCard', () => ({NewWorkDefaultsCard: () => <div>New Work Defaults</div>}));
vi.mock('../components/settings/OperationsSettingsSection', () => ({OperationsSettingsSection: () => <div>Operational controls</div>}));
afterEach(() => vi.unstubAllGlobals());
const payload = {page:'settings-operations', apiBase:'/api', initialData:{}};
it('keeps advanced workflow defaults beside New Work Defaults, not assessment records', async () => {
 vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})));
 renderWithClient(<BrowserRouter><ProvidersSecretsSettingsPage payload={payload}/></BrowserRouter>);
 fireEvent.click(screen.getByText('Advanced new work defaults, reset and audit'));
 expect((await screen.findByTestId('settings-owner')).textContent).toContain('workflow.default_runtime');
 expect(screen.queryByText('Reusable assessment authorizations')).toBeNull();
});
it('keeps all other advanced controls and local preference reset in Operations', async () => {
 renderWithClient(<BrowserRouter><OperationsSettingsPage payload={payload}/></BrowserRouter>);
 fireEvent.click(screen.getByText('Advanced'));
 expect((await screen.findByTestId('settings-owner')).textContent).toContain('excludeKeys');
 fireEvent.click(screen.getByRole('button', {name:'Reset dashboard preferences'}));
 expect(screen.getByText('Dashboard preferences reset.')).toBeTruthy();
});
