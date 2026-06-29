import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClientProvider } from '@tanstack/react-query';
import { parseBootPayload } from './parseBootPayload';
import { createDashboardQueryClient } from './queryClient';
import { DashboardErrorState } from '../components/DashboardErrorState';
import { DashboardToastProvider } from '../components/dashboard';
import '@fontsource/ibm-plex-sans/latin-400.css';
import '@fontsource/ibm-plex-sans/latin-500.css';
import '@fontsource/ibm-plex-sans/latin-600.css';
import '@fontsource/ibm-plex-sans/latin-700.css';
import '@fontsource/ibm-plex-mono/latin-400.css';
import '@fontsource/ibm-plex-mono/latin-700.css';
import '../styles/dashboard.css';

const queryClient = createDashboardQueryClient();

const RAW_BOOT_FALLBACK_HTML = `<div class="p-4 text-red-600 bg-red-50 border border-red-200 rounded-md">
      Failed to initialize application. See console for details.
    </div>`;

export function mountPage(App: React.ComponentType<{ payload: ReturnType<typeof parseBootPayload> }>, rootId = 'dashboard-app-root') {
  const rootElement = document.getElementById(rootId);
  if (!rootElement) {
    console.error(`Root element #${rootId} not found. Cannot mount app.`);
    return;
  }

  try {
    const payload = parseBootPayload();

    createRoot(rootElement).render(
      <StrictMode>
        <QueryClientProvider client={queryClient}>
          <DashboardToastProvider>
            <App payload={payload} />
          </DashboardToastProvider>
        </QueryClientProvider>
      </StrictMode>
    );
  } catch (e) {
    console.error('Failed to boot app:', e);
    const detail = e instanceof Error ? e.message : String(e);
    // Render a dashboard-styled boot failure. Only fall back to raw HTML if React
    // itself cannot mount (e.g. createRoot/render throws).
    try {
      createRoot(rootElement).render(
        <StrictMode>
          <DashboardErrorState
            title="Failed to initialize application"
            description="The dashboard could not start. Please reload, and check the console for details if the problem persists."
            detail={detail}
          />
        </StrictMode>
      );
    } catch (renderError) {
      console.error('Failed to render boot failure UI:', renderError);
      rootElement.innerHTML = RAW_BOOT_FALLBACK_HTML;
    }
  }
}
