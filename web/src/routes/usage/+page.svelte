<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/api';
	import { toasts } from '$lib/stores.svelte';
	import PageHeader from '$lib/PageHeader.svelte';

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

	let today = $state<UsageResponse | null>(null);
	let week = $state<UsageResponse | null>(null);
	let month = $state<UsageResponse | null>(null);
	let loading = $state(true);

	async function load() {
		loading = true;
		try {
			[today, week, month] = await Promise.all([
				api<UsageResponse>('/v1/usage', { query: { range: '24h' } }),
				api<UsageResponse>('/v1/usage', { query: { range: '7d' } }),
				api<UsageResponse>('/v1/usage', { query: { range: '30d' } })
			]);
		} catch (e) {
			toasts.show(`Load failed: ${(e as Error).message}`, 'error');
		} finally {
			loading = false;
		}
	}

	onMount(load);

	function fmtTime(ts: number): string {
		try {
			return new Date(ts * 1000).toLocaleString();
		} catch {
			return '';
		}
	}
</script>

<PageHeader title="Usage" sub="Token + cost rollup across providers" />

<div class="page-body">
	{#if loading}
		<div class="empty"><span class="spinner"></span> Loading…</div>
	{:else}
		<div class="grid-cards" style="margin-bottom: var(--sp-4)">
			<div class="card">
				<div class="card-title">Today</div>
				<div style="font-size: var(--fs-22); font-weight: 600">${(today?.summary.totals.cost_usd ?? 0).toFixed(4)}</div>
				<div style="font-size: 12px; color: var(--fg-muted); margin-top: 4px">
					{(today?.summary.totals.input_tokens ?? 0).toLocaleString()} in · {(today?.summary.totals.output_tokens ?? 0).toLocaleString()} out
				</div>
			</div>
			<div class="card">
				<div class="card-title">Last 7 days</div>
				<div style="font-size: var(--fs-22); font-weight: 600">${(week?.summary.totals.cost_usd ?? 0).toFixed(4)}</div>
				<div style="font-size: 12px; color: var(--fg-muted); margin-top: 4px">
					{(week?.summary.totals.events ?? 0)} events
				</div>
			</div>
			<div class="card">
				<div class="card-title">Last 30 days</div>
				<div style="font-size: var(--fs-22); font-weight: 600">${(month?.summary.totals.cost_usd ?? 0).toFixed(4)}</div>
				<div style="font-size: 12px; color: var(--fg-muted); margin-top: 4px">
					{(month?.summary.totals.events ?? 0)} events
				</div>
			</div>
		</div>

		{#if week?.summary.by_provider && week.summary.by_provider.length > 0}
			<div class="card" style="margin-bottom: var(--sp-4)">
				<div class="card-title">By provider (7d)</div>
				<table class="table">
					<thead>
						<tr><th>Provider</th><th>Input</th><th>Output</th><th>Cost</th></tr>
					</thead>
					<tbody>
						{#each week.summary.by_provider as row}
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

		{#if week?.recent_events && week.recent_events.length > 0}
			<div class="card">
				<div class="card-title">Recent events ({week.recent_events.length})</div>
				<table class="table">
					<thead>
						<tr><th>When</th><th>Provider</th><th>Model</th><th>Tokens</th><th>Cost</th></tr>
					</thead>
					<tbody>
						{#each week.recent_events.slice(0, 50) as ev (ev.ts)}
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
