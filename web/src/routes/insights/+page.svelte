<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { api } from '$lib/api';
	import { toasts } from '$lib/stores.svelte';
	import PageHeader from '$lib/PageHeader.svelte';
	import { Lightbulb } from 'lucide-svelte';
	import type { Insight } from '$lib/types';

	const RANGES = [
		{ label: '1h', value: '1h' },
		{ label: '24h', value: '24h' },
		{ label: '7 days', value: '7d' },
		{ label: '30 days', value: '30d' },
		{ label: 'All', value: 'all' }
	] as const;

	let insights = $state<Insight[]>([]);
	let loading = $state(true);
	let range = $state<string>('7d');

	function readRangeFromUrl(): string {
		const r = page.url.searchParams.get('range');
		if (r && RANGES.some((rg) => rg.value === r)) return r;
		return '7d';
	}

	async function load(rangeParam?: string) {
		loading = true;
		const r = rangeParam || range;
		try {
			const data = await api<{ insights: Insight[] }>('/v1/insights', { query: { range: r } });
			insights = data.insights || [];
		} catch (e) {
			toasts.show(`Load failed: ${(e as Error).message}`, 'error');
		} finally {
			loading = false;
		}
	}

	function setRange(r: string) {
		if (r === range) return;
		range = r;
		const url = new URL(page.url.href);
		if (r === '7d') url.searchParams.delete('range');
		else url.searchParams.set('range', r);
		window.history.replaceState({}, '', url.toString());
		load(r);
	}

	onMount(async () => {
		range = readRangeFromUrl();
		await load(range);
	});
</script>

<PageHeader title="Insights" sub="Usage patterns + suggested optimisations" />

<div class="page-body">
	<div class="pill-group" style="margin-bottom: var(--sp-4)">
		{#each RANGES as r}
			<button
				class="pill-btn"
				class:active={range === r.value}
				onclick={() => setRange(r.value)}
				type="button"
			>
				{r.label}
			</button>
		{/each}
	</div>

	{#if loading}<div class="empty"><span class="spinner"></span> Loading…</div>
	{:else if insights.length === 0}<div class="empty">No insights yet — use Companion for a while and check back.</div>
	{:else}
		<div class="col" style="gap: var(--sp-3)">
			{#each insights as i (i.id)}
				<div class="card">
					<div class="row align-center gap-2"><Lightbulb size={14} strokeWidth={2} /><strong>{i.title}</strong><span class="pill">{i.kind}</span></div>
					<p style="margin: var(--sp-2) 0 0">{i.body}</p>
				</div>
			{/each}
		</div>
	{/if}
</div>

<style>
	.pill-group {
		display: flex;
		gap: var(--sp-2);
		flex-wrap: wrap;
	}
	.pill-btn {
		padding: 6px 16px;
		border-radius: 999px;
		border: 1px solid var(--border);
		background: var(--bg-card);
		color: var(--fg-muted);
		font-size: var(--fs-13);
		cursor: pointer;
		transition: background 0.1s, color 0.1s, border-color 0.1s;
	}
	.pill-btn:hover {
		background: var(--bg-hover);
		color: var(--fg);
	}
	.pill-btn.active {
		background: var(--accent);
		border-color: var(--accent);
		color: var(--accent-fg);
	}
</style>
