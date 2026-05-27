import adapter from '@sveltejs/adapter-static';

/** @type {import('@sveltejs/kit').Config} */
const config = {
	compilerOptions: {
		runes: ({ filename }) => (filename.split(/[/\\]/).includes('node_modules') ? undefined : true)
	},
	kit: {
		adapter: adapter({
			pages: 'build',
			assets: 'build',
			fallback: 'index.html',
			precompress: false,
			strict: true
		}),
		// Mounted by FastAPI at /ui (legacy URL kept for back-compat with
		// existing Tauri windows + bookmarks). Override via BASE_PATH=''
		// when serving from a root mount.
		paths: {
			base: process.env.BASE_PATH ?? '/ui',
			relative: false
		},
		// SPA-only — Tauri bundles output verbatim, no SSR.
		prerender: { entries: [] }
	}
};

export default config;
