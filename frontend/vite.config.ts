import { defineConfig } from 'vitest/config';

export default defineConfig({
  esbuild: { jsx: 'automatic' },
  server: { proxy: { '/api': {
    target: 'http://127.0.0.1:8000', changeOrigin: true,
    rewrite: (path) => path.replace(/^\/api/, ''), timeout: 190_000, proxyTimeout: 190_000,
  } } },
  test: { environment: 'jsdom', setupFiles: ['./src/test/setup.ts'], restoreMocks: true },
});
