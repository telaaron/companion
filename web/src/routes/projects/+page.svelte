<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/api';
	import { toasts } from '$lib/stores.svelte';
	import type { Project } from '$lib/types';
	import PageHeader from '$lib/PageHeader.svelte';
	import { Plus, Trash2, FolderOpen } from 'lucide-svelte';

	let projects = $state<Project[]>([]);
	let loading = $state(true);
	let creating = $state(false);
	let newName = $state('');
	let newWorkspace = $state('');

	async function load() {
		loading = true;
		try {
			const data = await api<{ projects: Project[] }>('/v1/projects');
			projects = data.projects || [];
		} catch (e) {
			toasts.show(`Load failed: ${(e as Error).message}`, 'error');
		} finally {
			loading = false;
		}
	}

	async function create() {
		if (!newName.trim()) return;
		try {
			await api('/v1/projects', {
				method: 'POST',
				body: { name: newName.trim(), workspace_path: newWorkspace.trim() || null }
			});
			toasts.show(`Project created`, 'ok');
			newName = '';
			newWorkspace = '';
			creating = false;
			await load();
		} catch (e) {
			toasts.show(`Create failed: ${(e as Error).message}`, 'error');
		}
	}

	async function remove(id: string) {
		if (!confirm('Delete this project?')) return;
		try {
			await api(`/v1/projects/${id}`, { method: 'DELETE' });
			await load();
		} catch (e) {
			toasts.show(`Delete failed: ${(e as Error).message}`, 'error');
		}
	}

	async function updateWorkspace(id: string, value: string) {
		try {
			await api(`/v1/projects/${id}`, {
				method: 'PATCH',
				body: { workspace_path: value }
			});
			toasts.show('Workspace updated', 'ok');
			await load();
		} catch (e) {
			toasts.show(`Update failed: ${(e as Error).message}`, 'error');
		}
	}

	onMount(load);
</script>

<PageHeader title="Projects" sub="Group sessions, pin context, scope file access to a workspace folder">
	{#snippet actions()}
		<button class="btn btn-primary" type="button" onclick={() => (creating = !creating)}>
			<Plus size={14} strokeWidth={2} /> New project
		</button>
	{/snippet}
</PageHeader>

<div class="page-body">
	{#if creating}
		<div class="card" style="margin-bottom: var(--sp-4)">
			<div class="card-title">New project</div>
			<div class="col" style="gap: var(--sp-3)">
				<div>
					<div class="form-label">Name</div>
					<input class="form-input" bind:value={newName} placeholder="My project" />
				</div>
				<div>
					<div class="form-label">Workspace path (optional)</div>
					<input class="form-input" bind:value={newWorkspace} placeholder="/Users/me/code/myproject" />
				</div>
				<div class="row gap-2">
					<button class="btn btn-primary" onclick={create}>Create</button>
					<button class="btn btn-ghost" onclick={() => (creating = false)}>Cancel</button>
				</div>
			</div>
		</div>
	{/if}

	{#if loading}
		<div class="empty"><span class="spinner"></span> Loading…</div>
	{:else if projects.length === 0}
		<div class="empty">No projects yet. Click "New project" to start.</div>
	{:else}
		<div class="grid-cards">
			{#each projects as p (p.id)}
				<div class="card">
					<div class="row justify-between align-center" style="margin-bottom: 8px">
						<h3 style="margin: 0; font-size: var(--fs-16)">{p.name}</h3>
						<button class="btn btn-ghost btn-icon" type="button" onclick={() => remove(p.id)} title="Delete">
							<Trash2 size={14} strokeWidth={2} />
						</button>
					</div>
					{#if p.description}<p style="color: var(--fg-muted); margin: 0 0 var(--sp-3)">{p.description}</p>{/if}
					<div>
						<div class="form-label">Workspace</div>
						<input
							class="form-input mono"
							value={p.workspace_path ?? ''}
							onchange={(e) => updateWorkspace(p.id, (e.currentTarget as HTMLInputElement).value)}
							placeholder="(none)"
						/>
					</div>
					<div class="row gap-2 align-center" style="margin-top: var(--sp-3); font-size: 11px; color: var(--fg-muted)">
						<FolderOpen size={12} strokeWidth={2} />
						<span class="mono">{p.id.slice(0, 8)}</span>
						<span>·</span>
						<span>created {new Date(p.created_at).toLocaleDateString()}</span>
					</div>
				</div>
			{/each}
		</div>
	{/if}
</div>
