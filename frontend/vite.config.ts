import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { resolve } from 'path';
import { missionControlViteBase } from './src/vite-base';

export default defineConfig(({ command }) => ({
  base: missionControlViteBase(command),
  plugins: [react()],
  root: resolve(__dirname, 'src'),
  build: {
    outDir: resolve(__dirname, '../api_service/static/task_dashboard/dist'),
    emptyOutDir: true,
    manifest: true,
    rollupOptions: {
      input: {
        'mission-control': resolve(__dirname, 'src/entrypoints/mission-control.tsx'),
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
  },
}));
