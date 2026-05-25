import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
	plugins: [sveltekit()],
	server: {
		port: 5173,
		strictPort: true,
		// Proxy backend during dev so /v1/* and /ui assets resolve locally
		// against the running FastAPI server on :8082.
		proxy: {
			'/v1': {
				target: 'http://127.0.0.1:8082',
				changeOrigin: false,
				ws: false
			}
		}
	}
});
