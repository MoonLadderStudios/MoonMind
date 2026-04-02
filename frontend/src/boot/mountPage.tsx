import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { parseBootPayload } from './parseBootPayload';
import '../styles/mission-control.css';

const queryClient = new QueryClient();

export function mountPage(App: React.ComponentType<{ payload: ReturnType<typeof parseBootPayload> }>, rootId = 'mission-control-root') {
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
          <App payload={payload} />
        </QueryClientProvider>
      </StrictMode>
    );
  } catch (e) {
    console.error("Failed to boot app:", e);
    // You might want to render a fallback UI here instead of just throwing
    rootElement.innerHTML = `<div class="p-4 text-red-600 bg-red-50 border border-red-200 rounded-md">
      Failed to initialize application. See console for details.
    </div>`;
  }
}
