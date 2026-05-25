<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/api';
	import { toasts } from '$lib/stores.svelte';
	import PageHeader from '$lib/PageHeader.svelte';
	import { Folder, FileText, ChevronRight, ArrowUp } from 'lucide-svelte';

	interface FsEntry {
		name: string;
		path: string;
		is_dir: boolean;
		size?: number;
		modified_at?: string;
	}

	let entries = $state<FsEntry[]>([]);
	let cwd = $state('');
	let loading = $state(true);

	async function load(p?: string) {
		loading = true;
		try {
			const data = await api<{ path: string; entries: FsEntry[] }>('/v1/fs/browse', { query: p ? { path: p } : {} });
			entries = data.entries || [];
			cwd = data.path || '';
		} catch (e) {
			toasts.show(`Load failed: ${(e as Error).message}`, 'error');
		} finally {
			loading = false;
		}
	}

	function open(entry: FsEntry) {
		if (entry.is_dir) load(entry.path);
	}

	function up() {
		const parts = cwd.split('/').filter(Boolean);
		parts.pop();
		const next = '/' + parts.join('/');
		load(next || '/');
	}

	onMount(() => load());
</script>

<PageHeader title="Root files" sub={cwd || 'Filesystem browser'}>
	{#snippet actions()}
		<button class="btn" type="button" onclick={up} disabled={cwd === '/' || !cwd}>
			<ArrowUp size={14} strokeWidth={2} /> Up
		</button>
	{/snippet}
</PageHeader>

<div class="page-body">
	{#if loading}<div class="empty"><span class="spinner"></span> Loading…</div>
	{:else}
		<div class="card" style="padding: 0">
			<table class="table">
				<tbody>
					{#each entries as e (e.path)}
						<tr style="cursor: {e.is_dir ? 'pointer' : 'default'}" onclick={() => open(e)}>
							<td style="width: 20px">
								{#if e.is_dir}<Folder size={14} strokeWidth={2} />{:else}<FileText size={14} strokeWidth={2} />{/if}
							</td>
							<td class="mono">{e.name}</td>
							<td class="mono" style="color: var(--fg-muted); text-align: right">{e.size ? `${(e.size / 1024).toFixed(1)} KB` : ''}</td>
							<td style="width: 20px">{#if e.is_dir}<ChevronRight size={14} strokeWidth={2} />{/if}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/if}
</div>
