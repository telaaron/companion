<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/api';
	import { toasts } from '$lib/stores.svelte';
	import PageHeader from '$lib/PageHeader.svelte';
	import type { FileEdit } from '$lib/types';

	let edits = $state<FileEdit[]>([]);
	let loading = $state(true);
	let active = $state<FileEdit | null>(null);

	async function load() {
		loading = true;
		try {
			const data = await api<{ edits: FileEdit[] }>('/v1/files');
			edits = data.edits || [];
		} catch (e) {
			toasts.show(`Load failed: ${(e as Error).message}`, 'error');
		} finally {
			loading = false;
		}
	}

	onMount(load);
</script>

<PageHeader title="File edits" sub="Recent file modifications made by the agent" />

<div class="page-body" style="display: grid; grid-template-columns: 320px 1fr; gap: var(--sp-4); height: calc(100vh - 100px)">
	<div class="card" style="overflow-y: auto">
		<div class="card-title">{edits.length} edits</div>
		{#if loading}<span class="spinner"></span>
		{:else}
			{#each edits as e (e.id)}
				<button class="session-row" class:active={active?.id === e.id} type="button" onclick={() => (active = e)} style="width: 100%; text-align: left; background: transparent; border: none; padding: 8px; cursor: pointer; color: var(--fg)">
					<div class="mono truncate" style="font-size: 12px">{e.path}</div>
					<div style="font-size: 11px; color: var(--fg-muted)">{new Date(e.created_at).toLocaleString()}</div>
				</button>
			{/each}
		{/if}
	</div>
	<div class="card" style="overflow-y: auto">
		{#if active}
			<div class="card-title mono">{active.path}</div>
			<pre style="font-family: ui-monospace, monospace; font-size: 12px; white-space: pre-wrap; margin: 0">{active.diff}</pre>
		{:else}
			<div class="empty">Select an edit to view diff</div>
		{/if}
	</div>
</div>
