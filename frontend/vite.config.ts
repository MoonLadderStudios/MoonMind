import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { resolve } from 'path';
import { dashboardViteBase } from './src/vite-base';

export default defineConfig(({ command, mode }) => ({
  base: dashboardViteBase(command),
  plugins: [react()],
  root: mode === 'test' ? undefined : resolve(__dirname, 'src'),
  resolve: {
    preserveSymlinks: mode === 'test',
  },
  build: {
    outDir: resolve(__dirname, '../api_service/static/workflow_console/dist'),
    emptyOutDir: true,
    manifest: true,
    rollupOptions: {
      input: {
        'dashboard': resolve(__dirname, 'src/entrypoints/dashboard.tsx'),
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    include: ['frontend/src/**/*.test.{ts,tsx}'],
    css: {
      include: /dashboard\.css$/,
    },
  },
}));
