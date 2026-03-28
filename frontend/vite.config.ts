import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { resolve } from 'path';

export default defineConfig({
  plugins: [react()],
  root: resolve(__dirname, 'src'),
  build: {
    outDir: resolve(__dirname, '../api_service/static/task_dashboard/dist'),
    emptyOutDir: true,
    manifest: true,
    rollupOptions: {
      input: {
        // Entrypoints will be added here as pages are migrated
        'tasks-home': resolve(__dirname, 'src/entrypoints/tasks-home.tsx'),
        'settings': resolve(__dirname, 'src/entrypoints/settings.tsx'),
        'secrets': resolve(__dirname, 'src/entrypoints/secrets.tsx'),
        'dashboard-alerts': resolve(__dirname, 'src/entrypoints/dashboard-alerts.tsx'),
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
  },
});
