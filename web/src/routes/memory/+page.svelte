<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/api';
	import { toasts, confirmStore } from '$lib/stores.svelte';
	import PageHeader from '$lib/PageHeader.svelte';
	import { Plus, Trash2 } from 'lucide-svelte';
	import type { Preference } from '$lib/types';

	let prefs = $state<Preference[]>([]);
	let loading = $state(true);
	let newKey = $state('');
	let newValue = $state('');

	async function load() {
		loading = true;
		try {
			const data = await api<{ preferences: Preference[] }>('/v1/preferences');
			prefs = data.preferences || [];
		} catch (e) {
			toasts.show(`Load failed: ${(e as Error).message}`, 'error');
		} finally {
			loading = false;
		}
	}

	async function save(key: string, value: string) {
		try {
			await api('/v1/preferences', { method: 'POST', body: { key, value } });
			toasts.show(`Saved ${key}`, 'ok');
			await load();
		} catch (e) {
			toasts.show(`Save failed: ${(e as Error).message}`, 'error');
		}
	}

	async function remove(key: string) {
		if (!(await confirmStore.ask(`Delete ${key}?`))) return;
		try {
			await api(`/v1/preferences/${encodeURIComponent(key)}`, { method: 'DELETE' });
			await load();
		} catch (e) {
			toasts.show(`Delete failed: ${(e as Error).message}`, 'error');
		}
	}

	async function add() {
		if (!newKey.trim()) return;
		await save(newKey.trim(), newValue);
		newKey = '';
		newValue = '';
	}

	onMount(load);
</script>

<PageHeader title="Memory" sub="Long-term preferences the agent reads at start of each session" />

<div class="page-body">
	<div class="card" style="margin-bottom: var(--sp-4)">
		<div class="card-title">Add preference</div>
		<div class="row gap-2 align-center">
			<input class="form-input mono" placeholder="key" bind:value={newKey} style="flex: 1" />
			<input class="form-input mono" placeholder="value" bind:value={newValue} style="flex: 2" />
			<button class="btn btn-primary" onclick={add}><Plus size={14} strokeWidth={2} /> Add</button>
		</div>
	</div>

	{#if loading}<div class="empty"><span class="spinner"></span> Loading…</div>
	{:else if prefs.length === 0}<div class="empty">No preferences yet</div>
	{:else}
		<div class="card" style="padding: 0">
			<table class="table">
				<thead><tr><th>Key</th><th>Value</th><th></th></tr></thead>
				<tbody>
					{#each prefs as p (p.key)}
						<tr>
							<td class="mono" style="color: var(--fg-muted)">{p.key}</td>
							<td><input class="form-input mono" value={p.value} onchange={(e) => save(p.key, (e.currentTarget as HTMLInputElement).value)} /></td>
							<td style="width: 50px"><button class="btn btn-ghost btn-icon" onclick={() => remove(p.key)}><Trash2 size={14} strokeWidth={2} /></button></td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/if}
</div>
