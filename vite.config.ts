import path from 'path';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      src: path.resolve(__dirname, './src'),
    },
  },
  build: {
    outDir: 'build',
    assetsDir: 'static',
    chunkSizeWarningLimit: 1000,
    rolldownOptions: {
      output: {
        manualChunks(id) {
          if (
            id.includes('node_modules/react') ||
            id.includes('node_modules/react-dom') ||
            id.includes('node_modules/react-router')
          )
            return 'vendor-react';
        },
      },
    },
  },
  server: {
    host: '0.0.0.0',
    port: 3000,
    proxy: {
      // changeOrigin:false preserves the browser's original Host header
      // (localhost:3000) when proxying to seizu:8080. Required so the
      // backend's request.url_for() — used to build the OIDC redirect_uri
      // — emits localhost:3000, not the docker-internal hostname.
      '/api': { target: 'http://seizu:8080', changeOrigin: false },
      '/healthcheck': { target: 'http://seizu:8080', changeOrigin: false },
    },
    watch: {
      ignored: [
        '**/.mypy_cache/**',
        '**/coverage/**',
        '**/__pycache__/**',
        '**/tests/**',
        '**/__tests__/**',
        '**/*.tmp.*',
      ],
    },
  },
});
