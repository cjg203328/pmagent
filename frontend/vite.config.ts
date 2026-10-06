import { fileURLToPath, URL } from 'node:url'

import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// ArtPM Agent React 前端。
// 开发端口固定 1501（Crow5 前端端口规范区间 1001~2000，已登记于 README）。
// /api 反向代理指向 FastAPI 网关 8765，浏览器联调不依赖 CORS。
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    // Keep the `@/` alias identical to tsconfig "paths" so imports resolve in
    // both the type checker and the bundler.
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    host: '127.0.0.1',
    port: 1501,
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8765',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
  preview: {
    host: '127.0.0.1',
    port: 1501,
    strictPort: true,
  },
})
