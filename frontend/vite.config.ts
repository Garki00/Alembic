import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发时前端跑在 5173，后端跑在 8000；/api 请求由 Vite 代理，
// 这样浏览器里是同源请求，不需要为开发环境配置 CORS。
// 如果后端换了地址，在 frontend/.env.local 里设置 VITE_BACKEND_URL。
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '')
  const target = env.VITE_BACKEND_URL || 'http://127.0.0.1:8000'
  const proxy = {
    '/api': {
      target,
      changeOrigin: true,
    },
  }

  return {
    plugins: [vue()],
    server: {
      host: '127.0.0.1',
      port: 5173,
      strictPort: true,
      proxy,
      // 某些编辑器 / 智能体工具会在源文件旁边创建临时目录，
      // 文件监听器可能对它们报 EBUSY 并把开发服务器带崩。
      watch: {
        ignored: ['**/.*.tmpdir/**', '**/.*.tmp'],
      },
    },
    preview: {
      host: '127.0.0.1',
      port: 4173,
      strictPort: true,
      proxy,
    },
    build: {
      outDir: 'dist',
      sourcemap: false,
    },
  }
})
