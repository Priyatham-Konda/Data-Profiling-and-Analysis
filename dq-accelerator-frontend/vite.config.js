import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { fileURLToPath, URL } from 'node:url';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Listen on all interfaces (not just localhost) so `npm run dev` always
  // prints a Network URL, without needing --host on every invocation.
  server: {
    host: true,
  },
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  test: {
    // The default forks pool cannot spawn workers in this environment.
    pool: 'threads',
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test/setup.js',
  },
});
