<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { api } from '$lib/api';
	import { toasts } from '$lib/stores.svelte';
	import PageHeader from '$lib/PageHeader.svelte';

	const RANGES = [
		{ label: '24h', value: '24h' },
		{ label: '7 days', value: '7d' },
		{ label: '30 days', value: '30d' }
	] as const;

	interface Totals {
		input_tokens: number;
		output_tokens: number;
		images: number;
		cost_usd: number;
		events: number;
	}
	interface UsageSummary {
		totals: Totals;
		by_provider?: Array<{
			provider: string;
			input_tokens: number;
			output_tokens: number;
			cost_usd: number;
			events: number;
		}>;
		by_model?: Array<{ model: string; input_tokens: number; output_tokens: number; cost_usd: number }>;
	}

	interface UsageResponse {
		range: string;
		since_ts: number;
		summary: UsageSummary;
		recent_events?: Array<{
			ts: number;
			provider: string;
			model: string;
			input_tokens: number;
			output_tokens: number;
			cost_usd: number;
		}>;
	}

	let range = $state<string>('24h');
	let data = $state<UsageResponse | null>(null);
	let loading = $state(true);

	function readRangeFromUrl(): string {
		const r = page.url.searchParams.get('range');
		if (r && RANGES.some((rg) => rg.value === r)) return r;
		return '24h';
	}

	async function load(rangeParam?: string) {
		const r = rangeParam || range;
		loading = true;
		try {
			data = await api<UsageResponse>('/v1/usage', { query: { range: r } });
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
		if (r === '24h') url.searchParams.delete('range');
		else url.searchParams.set('range', r);
		window.history.replaceState({}, '', url.toString());
		load(r);
	}

	onMount(async () => {
		range = readRangeFromUrl();
		await load(range);
	});

	function fmtTime(ts: number): string {
		try {
			// Backend stores ts as milliseconds (int(time.time() * 1000)).
			return new Date(ts).toLocaleString();
		} catch {
			return '';
		}
	}
</script>

<PageHeader title="Usage" sub="Token + cost rollup across providers" />

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

	{#if loading}
		<div class="empty"><span class="spinner"></span> Loading…</div>
	{:else if data}
		<div class="grid-cards" style="margin-bottom: var(--sp-4)">
			<div class="card">
				<div class="card-title">Cost</div>
				<div style="font-size: var(--fs-22); font-weight: 600">${(data.summary.totals.cost_usd).toFixed(4)}</div>
			</div>
			<div class="card">
				<div class="card-title">Input tokens</div>
				<div style="font-size: var(--fs-22); font-weight: 600">{data.summary.totals.input_tokens.toLocaleString()}</div>
			</div>
			<div class="card">
				<div class="card-title">Output tokens</div>
				<div style="font-size: var(--fs-22); font-weight: 600">{data.summary.totals.output_tokens.toLocaleString()}</div>
			</div>
			<div class="card">
				<div class="card-title">Events</div>
				<div style="font-size: var(--fs-22); font-weight: 600">{data.summary.totals.events}</div>
			</div>
		</div>

		{#if data.summary.by_provider && data.summary.by_provider.length > 0}
			<div class="card" style="margin-bottom: var(--sp-4)">
				<div class="card-title">By provider ({data.range})</div>
				<table class="table">
					<thead>
						<tr><th>Provider</th><th>Input</th><th>Output</th><th>Cost</th></tr>
					</thead>
					<tbody>
						{#each data.summary.by_provider as row}
							<tr>
								<td class="mono">{row.provider}</td>
								<td class="mono">{row.input_tokens.toLocaleString()}</td>
								<td class="mono">{row.output_tokens.toLocaleString()}</td>
								<td class="mono">${row.cost_usd.toFixed(4)}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{/if}

		{#if data.recent_events && data.recent_events.length > 0}
			<div class="card">
				<div class="card-title">Recent events ({data.recent_events.length})</div>
				<table class="table">
					<thead>
						<tr><th>When</th><th>Provider</th><th>Model</th><th>Tokens</th><th>Cost</th></tr>
					</thead>
					<tbody>
						{#each data.recent_events.slice(0, 50) as ev (ev.ts)}
							<tr>
								<td class="mono" style="white-space: nowrap">{fmtTime(ev.ts)}</td>
								<td class="mono">{ev.provider}</td>
								<td class="mono truncate" title={ev.model}>{ev.model}</td>
								<td class="mono">{ev.input_tokens + ev.output_tokens}</td>
								<td class="mono">${ev.cost_usd.toFixed(4)}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{:else}
			<div class="empty">No usage data yet. Send a chat and check back.</div>
		{/if}
	{/if}
</div>

<style>
	.pill-group {
		display: flex;
		gap: var(--sp-2);
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
