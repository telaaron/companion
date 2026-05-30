/**
 * Svelte 5 reactive stores using runes. Keep state global + minimal.
 */

class ThemeStore {
	value = $state<'dark' | 'light'>('dark');

	constructor() {
		if (typeof window !== 'undefined') {
			const stored = localStorage.getItem('companion.theme') as 'dark' | 'light' | null;
			if (stored === 'dark' || stored === 'light') this.value = stored;
		}
	}

	toggle() {
		this.value = this.value === 'dark' ? 'light' : 'dark';
		if (typeof window !== 'undefined') {
			localStorage.setItem('companion.theme', this.value);
			document.documentElement.setAttribute('data-theme', this.value);
		}
	}

	set(v: 'dark' | 'light') {
		this.value = v;
		if (typeof window !== 'undefined') {
			localStorage.setItem('companion.theme', v);
			document.documentElement.setAttribute('data-theme', v);
		}
	}
}

export const theme = new ThemeStore();

class ToastStore {
	items = $state<Array<{ id: number; message: string; level: 'ok' | 'error' | 'warn' }>>([]);
	private nextId = 1;

	show(message: string, level: 'ok' | 'error' | 'warn' = 'ok') {
		const id = this.nextId++;
		this.items = [...this.items, { id, message, level }];
		setTimeout(() => {
			this.items = this.items.filter((t) => t.id !== id);
		}, 3200);
	}
}

export const toasts = new ToastStore();

class CostStore {
	today = $state<number>(0);
	week = $state<number>(0);
	loading = $state<boolean>(false);

	async refresh() {
		this.loading = true;
		try {
			const { api } = await import('./api');
			const [today, week] = await Promise.all([
				api<{ summary: { totals: { cost_usd: number } } }>('/v1/usage', { query: { range: '24h' } }),
				api<{ summary: { totals: { cost_usd: number } } }>('/v1/usage', { query: { range: '7d' } })
			]);
			this.today = today?.summary?.totals?.cost_usd ?? 0;
			this.week = week?.summary?.totals?.cost_usd ?? 0;
		} catch {
			/* silent */
		} finally {
			this.loading = false;
		}
	}
}

export const cost = new CostStore();

class HealthStore {
	online = $state<boolean>(true);

	async probe() {
		try {
			const { api } = await import('./api');
			await api('/v1/me');
			this.online = true;
		} catch {
			this.online = false;
		}
	}
}

export const health = new HealthStore();

/**
 * Global confirm dialog. Native window.confirm is a no-op (returns false /
 * is blocked) inside the Tauri WebKit shell, which silently swallowed every
 * delete action. This store backs a single <ConfirmModal> mounted in the
 * root layout; ``confirmStore.ask(msg)`` resolves to the user's choice.
 */
class ConfirmStore {
	open = $state(false);
	message = $state('');
	private resolver: ((v: boolean) => void) | null = null;

	ask(message: string): Promise<boolean> {
		this.message = message;
		this.open = true;
		return new Promise<boolean>((resolve) => {
			this.resolver = resolve;
		});
	}

	resolve(value: boolean) {
		this.open = false;
		this.message = '';
		const r = this.resolver;
		this.resolver = null;
		r?.(value);
	}
}

export const confirmStore = new ConfirmStore();
