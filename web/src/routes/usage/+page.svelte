<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/api';
	import { toasts } from '$lib/stores.svelte';
	import PageHeader from '$lib/PageHeader.svelte';

	interface UsageData {
		today_cost_usd?: number;
		week_cost_usd?: number;
		month_cost_usd?: number;
		by_model?: Array<{ model: string; cost_usd: number; tokens: number }>;
		[key: string]: unknown;
	}

	let data = $state<UsageData | null>(null);
	let loading = $state(true);

	async function load() {
		loading = true;
		try {
			data = await api<UsageData>('/v1/usage');
		} catch (e) {
			toasts.show(`Load failed: ${(e as Error).message}`, 'error');
		} finally {
			loading = false;
		}
	}

	onMount(load);
</script>

<PageHeader title="Usage" sub="Token + cost rollup across providers" />

<div class="page-body">
	{#if loading}
		<div class="empty"><span class="spinner"></span> Loading…</div>
	{:else if data}
		<div class="grid-cards" style="margin-bottom: var(--sp-4)">
			<div class="card">
				<div class="card-title">Today</div>
				<div style="font-size: var(--fs-22); font-weight: 600">${(data.today_cost_usd ?? 0).toFixed(4)}</div>
			</div>
			<div class="card">
				<div class="card-title">Last 7 days</div>
				<div style="font-size: var(--fs-22); font-weight: 600">${(data.week_cost_usd ?? 0).toFixed(4)}</div>
			</div>
			<div class="card">
				<div class="card-title">Last 30 days</div>
				<div style="font-size: var(--fs-22); font-weight: 600">${(data.month_cost_usd ?? 0).toFixed(4)}</div>
			</div>
		</div>

		{#if data.by_model && data.by_model.length > 0}
			<div class="card">
				<div class="card-title">By model</div>
				<table class="table">
					<thead>
						<tr><th>Model</th><th>Tokens</th><th>Cost (USD)</th></tr>
					</thead>
					<tbody>
						{#each data.by_model as row}
							<tr>
								<td class="mono">{row.model}</td>
								<td class="mono">{row.tokens.toLocaleString()}</td>
								<td class="mono">${row.cost_usd.toFixed(4)}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{/if}
	{/if}
</div>
