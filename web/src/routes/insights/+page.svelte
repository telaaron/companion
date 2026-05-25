<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/api';
	import { toasts } from '$lib/stores.svelte';
	import PageHeader from '$lib/PageHeader.svelte';
	import { Lightbulb } from 'lucide-svelte';
	import type { Insight } from '$lib/types';

	let insights = $state<Insight[]>([]);
	let loading = $state(true);

	async function load() {
		loading = true;
		try {
			const data = await api<{ insights: Insight[] }>('/v1/insights');
			insights = data.insights || [];
		} catch (e) {
			toasts.show(`Load failed: ${(e as Error).message}`, 'error');
		} finally {
			loading = false;
		}
	}

	onMount(load);
</script>

<PageHeader title="Insights" sub="Usage patterns + suggested optimisations" />

<div class="page-body">
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
