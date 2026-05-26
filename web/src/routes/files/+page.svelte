<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/api';
	import { toasts } from '$lib/stores.svelte';
	import PageHeader from '$lib/PageHeader.svelte';

	interface FileEdit {
		id: number;
		ts: number;
		op: string;
		path: string;
		bytes_delta: number;
		project_id?: string;
		session_id?: string;
		metadata?: string;
	}

	let edits = $state<FileEdit[]>([]);
	let loading = $state(true);
	let active = $state<FileEdit | null>(null);
	let preview = $state<string>('');
	let previewLoading = $state(false);

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

	async function openEdit(e: FileEdit) {
		active = e;
		preview = '';
		previewLoading = true;
		try {
			const data = await api<{ content: string }>(`/v1/preview/file`, { query: { path: e.path } });
			preview = data.content || '';
		} catch (err) {
			preview = `(unable to load preview: ${(err as Error).message})`;
		} finally {
			previewLoading = false;
		}
	}

	function fmtTime(ts: number): string {
		try { return new Date(ts * 1000).toLocaleString(); } catch { return ''; }
	}
	function fmtBytes(n: number): string {
		const sign = n > 0 ? '+' : n < 0 ? '−' : '';
		return `${sign}${Math.abs(n)}B`;
	}

	onMount(load);
</script>

<PageHeader title="File edits" sub={`${edits.length} recorded operations`} />

<div class="page-body" style="display: grid; grid-template-columns: 360px 1fr; gap: var(--sp-4); height: calc(100vh - 110px)">
	<div class="card" style="overflow-y: auto; padding: var(--sp-2)">
		{#if loading}<span class="spinner"></span>
		{:else if edits.length === 0}<div class="empty">No file edits recorded yet</div>
		{:else}
			{#each edits as e (e.id)}
				<button
					class="edit-row"
					class:active={active?.id === e.id}
					type="button"
					onclick={() => openEdit(e)}
				>
					<div class="row align-center gap-2" style="margin-bottom: 2px">
						<span class="pill">{e.op}</span>
						<span class="mono" style="font-size: 11px; color: var(--fg-muted)">{fmtBytes(e.bytes_delta)}</span>
					</div>
					<div class="mono truncate" style="font-size: 12px">{e.path}</div>
					<div style="font-size: 11px; color: var(--fg-muted)">{fmtTime(e.ts)}</div>
				</button>
			{/each}
		{/if}
	</div>
	<div class="card" style="overflow: hidden; display: flex; flex-direction: column">
		{#if active}
			<div class="card-title mono" style="margin-bottom: var(--sp-2)">{active.path}</div>
			{#if previewLoading}
				<div class="empty"><span class="spinner"></span> Loading preview…</div>
			{:else}
				<pre style="font-family: ui-monospace, monospace; font-size: 12px; white-space: pre-wrap; word-break: break-all; margin: 0; overflow: auto; flex: 1">{preview}</pre>
			{/if}
		{:else}
			<div class="empty">Select an edit to view file contents</div>
		{/if}
	</div>
</div>

<style>
	.edit-row {
		display: block;
		width: 100%;
		text-align: left;
		background: transparent;
		border: none;
		color: var(--fg);
		padding: 8px;
		border-radius: var(--radius);
		cursor: pointer;
		margin-bottom: 4px;
	}
	.edit-row:hover { background: var(--bg-hover); }
	.edit-row.active { background: var(--bg-active); }
</style>
