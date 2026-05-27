<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/api';
	import { toasts } from '$lib/stores.svelte';
	import PageHeader from '$lib/PageHeader.svelte';
	import { Plus, Play, Trash2, Power } from 'lucide-svelte';
	import type { Routine } from '$lib/types';

	let routines = $state<Routine[]>([]);
	let loading = $state(true);

	async function load() {
		loading = true;
		try {
			const data = await api<{ routines: Routine[] }>('/v1/routines');
			routines = data.routines || [];
		} catch (e) {
			toasts.show(`Load failed: ${(e as Error).message}`, 'error');
		} finally {
			loading = false;
		}
	}

	async function trigger(id: string) {
		try {
			await api(`/v1/routines/${id}/trigger`, { method: 'POST' });
			toasts.show('Routine triggered', 'ok');
		} catch (e) {
			toasts.show(`Trigger failed: ${(e as Error).message}`, 'error');
		}
	}

	async function toggle(r: Routine) {
		try {
			await api(`/v1/routines/${r.id}`, { method: 'PATCH', body: { enabled: !r.enabled } });
			await load();
		} catch (e) {
			toasts.show(`Toggle failed: ${(e as Error).message}`, 'error');
		}
	}

	async function remove(id: string) {
		if (!confirm('Delete this routine?')) return;
		try {
			await api(`/v1/routines/${id}`, { method: 'DELETE' });
			await load();
		} catch (e) {
			toasts.show(`Delete failed: ${(e as Error).message}`, 'error');
		}
	}

	onMount(load);
</script>

<PageHeader title="Routines" sub="Scheduled cron-based agent runs">
	{#snippet actions()}
		<button class="btn btn-primary" type="button"><Plus size={14} strokeWidth={2} /> New routine</button>
	{/snippet}
</PageHeader>

<div class="page-body">
	{#if loading}<div class="empty"><span class="spinner"></span> Loading…</div>
	{:else if routines.length === 0}<div class="empty">No routines configured</div>
	{:else}
		<div class="grid-cards">
			{#each routines as r (r.id)}
				<div class="card">
					<div class="row justify-between align-center">
						<strong>{r.name}</strong>
						<span class="pill" class:pill-ok={r.enabled} class:pill-error={!r.enabled}>{r.enabled ? 'enabled' : 'disabled'}</span>
					</div>
					<div class="mono" style="color: var(--fg-muted); font-size: 12px; margin: var(--sp-2) 0">{r.cron}</div>
					<p style="font-size: 13px; margin: 0 0 var(--sp-3)">{r.prompt}</p>
					<div class="row gap-2">
						<button class="btn btn-sm" onclick={() => trigger(r.id)}><Play size={12} strokeWidth={2} /> Run now</button>
						<button class="btn btn-sm" onclick={() => toggle(r)}><Power size={12} strokeWidth={2} /></button>
						<button class="btn btn-sm btn-ghost" onclick={() => remove(r.id)}><Trash2 size={12} strokeWidth={2} /></button>
					</div>
					{#if r.last_run_at}<div style="margin-top: var(--sp-2); font-size: 11px; color: var(--fg-muted)">Last run: {new Date(r.last_run_at).toLocaleString()}</div>{/if}
				</div>
			{/each}
		</div>
	{/if}
</div>
