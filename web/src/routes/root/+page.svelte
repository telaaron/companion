<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/api';
	import { toasts } from '$lib/stores.svelte';
	import PageHeader from '$lib/PageHeader.svelte';
	import { FileText, Save } from 'lucide-svelte';

	interface RootFile {
		name: string;
		size: number;
		modified_at: number;
	}

	let files = $state<RootFile[]>([]);
	let root = $state('');
	let loading = $state(true);
	let active = $state<string | null>(null);
	let content = $state('');
	let saving = $state(false);

	async function load() {
		loading = true;
		try {
			const data = await api<{ root: string; files: RootFile[] }>('/v1/root-files');
			files = data.files || [];
			root = data.root;
			if (!active && files[0]) await openFile(files[0].name);
		} catch (e) {
			toasts.show(`Load failed: ${(e as Error).message}`, 'error');
		} finally {
			loading = false;
		}
	}

	async function openFile(name: string) {
		active = name;
		content = '';
		try {
			const data = await api<{ content: string }>(`/v1/root-files/${encodeURIComponent(name)}`);
			content = data.content || '';
		} catch (e) {
			toasts.show(`Open failed: ${(e as Error).message}`, 'error');
		}
	}

	async function save() {
		if (!active) return;
		saving = true;
		try {
			await api(`/v1/root-files/${encodeURIComponent(active)}`, { method: 'PUT', body: { content } });
			toasts.show(`Saved ${active}`, 'ok');
			await load();
		} catch (e) {
			toasts.show(`Save failed: ${(e as Error).message}`, 'error');
		} finally {
			saving = false;
		}
	}

	onMount(load);
</script>

<PageHeader title="Root files" sub={root || 'Editable allowlisted repo files (CLAUDE.md, BUGS.md, AGENTS.md, …)'}>
	{#snippet actions()}
		{#if active}
			<button class="btn btn-primary" onclick={save} disabled={saving}>
				<Save size={14} strokeWidth={2} /> {saving ? 'Saving…' : 'Save'}
			</button>
		{/if}
	{/snippet}
</PageHeader>

<div class="page-body" style="display: grid; grid-template-columns: 240px 1fr; gap: var(--sp-4); height: calc(100vh - 110px)">
	<div class="card" style="overflow-y: auto; padding: var(--sp-2)">
		{#if loading}<span class="spinner"></span>
		{:else if files.length === 0}<div class="empty">No root files</div>
		{:else}
			{#each files as f (f.name)}
				<button
					class="session-row"
					class:active={active === f.name}
					type="button"
					onclick={() => openFile(f.name)}
					style="width: 100%; text-align: left; padding: 8px; background: transparent; border: none; cursor: pointer; color: var(--fg); border-radius: var(--radius); display: flex; gap: 8px; align-items: center; font-size: 13px"
				>
					<FileText size={14} strokeWidth={2} />
					<span style="flex: 1">{f.name}</span>
					<span style="font-size: 11px; color: var(--fg-muted)">{(f.size / 1024).toFixed(1)}K</span>
				</button>
			{/each}
		{/if}
	</div>
	<div class="card" style="overflow: hidden; display: flex; flex-direction: column">
		{#if active}
			<div class="card-title mono" style="margin-bottom: var(--sp-2)">{active}</div>
			<textarea
				class="form-textarea"
				bind:value={content}
				style="flex: 1; min-height: 400px; font-family: ui-monospace, monospace; font-size: 12px"
			></textarea>
		{:else}
			<div class="empty">Select a file</div>
		{/if}
	</div>
</div>

<style>
	.session-row:hover {
		background: var(--bg-hover) !important;
	}
	.session-row.active {
		background: var(--bg-active) !important;
	}
</style>
