<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/api';
	import { toasts } from '$lib/stores.svelte';
	import PageHeader from '$lib/PageHeader.svelte';
	import type { AuditEntry } from '$lib/types';

	let entries = $state<AuditEntry[]>([]);
	let loading = $state(true);
	let filter = $state('');

	async function load() {
		loading = true;
		try {
			const data = await api<{ entries: AuditEntry[] }>('/v1/audit');
			entries = data.entries || [];
		} catch (e) {
			toasts.show(`Load failed: ${(e as Error).message}`, 'error');
		} finally {
			loading = false;
		}
	}

	let filtered = $derived(
		filter.trim()
			? entries.filter(
					(e) =>
						e.category.includes(filter) || e.event.includes(filter) || (e.detail || '').includes(filter)
				)
			: entries
	);

	onMount(load);
</script>

<PageHeader title="Audit log" sub={`${entries.length} events`} />

<div class="page-body">
	<input class="form-input" placeholder="Filter category/event/detail…" bind:value={filter} style="margin-bottom: var(--sp-3)" />
	{#if loading}<div class="empty"><span class="spinner"></span> Loading…</div>
	{:else if filtered.length === 0}<div class="empty">No events</div>
	{:else}
		<div class="card" style="padding: 0">
			<table class="table">
				<thead><tr><th>When</th><th>Category</th><th>Event</th><th>Detail</th></tr></thead>
				<tbody>
					{#each filtered as e (e.id)}
						<tr>
							<td class="mono" style="white-space: nowrap">{new Date(e.created_at).toLocaleString()}</td>
							<td><span class="pill">{e.category}</span></td>
							<td class="mono">{e.event}</td>
							<td class="mono truncate" title={e.detail}>{e.detail || ''}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/if}
</div>
