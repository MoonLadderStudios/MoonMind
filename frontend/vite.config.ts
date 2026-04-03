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
        'dashboard-alerts': resolve(__dirname, 'src/entrypoints/dashboard-alerts.tsx'),
        'proposals': resolve(__dirname, 'src/entrypoints/proposals.tsx'),
        'schedules': resolve(__dirname, 'src/entrypoints/schedules.tsx'),
        'manifests': resolve(__dirname, 'src/entrypoints/manifests.tsx'),
        'manifest-submit': resolve(__dirname, 'src/entrypoints/manifest-submit.tsx'),
        'task-create': resolve(__dirname, 'src/entrypoints/task-create.tsx'),
        'skills': resolve(__dirname, 'src/entrypoints/skills.tsx'),
        'tasks-list': resolve(__dirname, 'src/entrypoints/tasks-list.tsx'),
        'task-detail': resolve(__dirname, 'src/entrypoints/task-detail.tsx'),
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
  },
});
