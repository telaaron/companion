<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/api';
	import { toasts } from '$lib/stores.svelte';
	import type { Project } from '$lib/types';
	import PageHeader from '$lib/PageHeader.svelte';
	import FolderPicker from '$lib/FolderPicker.svelte';
	import { Plus, Trash2, FolderOpen, FolderSearch, Pin, X } from 'lucide-svelte';

	interface Memory {
		id: string;
		project_id: string;
		title: string;
		body: string;
		pinned: boolean;
		created_at: string;
	}

	let projects = $state<Project[]>([]);
	let memoriesById = $state<Record<string, Memory[]>>({});
	let loading = $state(true);
	let creating = $state(false);
	let newName = $state('');
	let newWorkspace = $state('');
	let newDescription = $state('');
	let pickerOpen = $state(false);
	let pickerTargetId = $state<string | 'new' | null>(null);
	let memoryDrafts = $state<Record<string, { title: string; body: string }>>({});

	async function load() {
		loading = true;
		try {
			const data = await api<{ projects: Project[] }>('/v1/projects');
			projects = data.projects || [];
			await Promise.all(projects.map((p) => loadMemories(p.id)));
		} catch (e) {
			toasts.show(`Load failed: ${(e as Error).message}`, 'error');
		} finally {
			loading = false;
		}
	}

	async function loadMemories(projectId: string) {
		try {
			const data = await api<{ memories: Memory[] }>(`/v1/projects/${projectId}/memories`);
			memoriesById = { ...memoriesById, [projectId]: data.memories || [] };
		} catch {
			memoriesById = { ...memoriesById, [projectId]: [] };
		}
	}

	async function create() {
		if (!newName.trim()) return;
		try {
			await api('/v1/projects', {
				method: 'POST',
				body: {
					name: newName.trim(),
					workspace_path: newWorkspace.trim() || null,
					description: newDescription.trim() || null
				}
			});
			toasts.show(`Project created`, 'ok');
			newName = '';
			newWorkspace = '';
			newDescription = '';
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

	async function updateProject(id: string, patch: Partial<Project>) {
		const existing = projects.find((p) => p.id === id);
		if (!existing) return;
		try {
			await api(`/v1/projects/${id}`, {
				method: 'PUT',
				body: { ...existing, ...patch }
			});
			toasts.show('Saved', 'ok');
			await load();
		} catch (e) {
			toasts.show(`Update failed: ${(e as Error).message}`, 'error');
		}
	}

	async function addMemory(projectId: string) {
		const draft = memoryDrafts[projectId];
		if (!draft || !draft.title.trim()) return;
		try {
			await api(`/v1/projects/${projectId}/memories`, {
				method: 'POST',
				body: { title: draft.title.trim(), body: draft.body.trim(), pinned: true }
			});
			memoryDrafts = { ...memoryDrafts, [projectId]: { title: '', body: '' } };
			await loadMemories(projectId);
		} catch (e) {
			toasts.show(`Add memory failed: ${(e as Error).message}`, 'error');
		}
	}

	async function removeMemory(projectId: string, memoryId: string) {
		try {
			await api(`/v1/projects/${projectId}/memories/${memoryId}`, { method: 'DELETE' });
			await loadMemories(projectId);
		} catch (e) {
			toasts.show(`Delete memory failed: ${(e as Error).message}`, 'error');
		}
	}

	function openPicker(target: string | 'new') {
		pickerTargetId = target;
		pickerOpen = true;
	}

	function onPickedPath(path: string) {
		if (pickerTargetId === 'new') {
			newWorkspace = path;
		} else if (pickerTargetId) {
			updateProject(pickerTargetId, { workspace_path: path });
		}
		pickerTargetId = null;
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
		<div class="card" style="margin-bottom: var(--sp-4); max-width: 640px">
			<div class="card-title">New project</div>
			<div class="col" style="gap: var(--sp-3)">
				<div>
					<div class="form-label">Name</div>
					<input class="form-input" bind:value={newName} placeholder="My project" />
				</div>
				<div>
					<div class="form-label">Description (optional)</div>
					<input class="form-input" bind:value={newDescription} placeholder="What this project is for" />
				</div>
				<div>
					<div class="form-label">Workspace path</div>
					<div class="row gap-2">
						<input class="form-input mono" bind:value={newWorkspace} placeholder="/Users/me/code/myproject" style="flex: 1" />
						<button class="btn" type="button" onclick={() => openPicker('new')}><FolderSearch size={14} strokeWidth={2} /> Pick…</button>
					</div>
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
		<div class="col" style="gap: var(--sp-4)">
			{#each projects as p (p.id)}
				<div class="card">
					<div class="row justify-between align-center" style="margin-bottom: var(--sp-3)">
						<h3 style="margin: 0; font-size: var(--fs-16)">{p.name}</h3>
						<button class="btn btn-ghost btn-icon" type="button" onclick={() => remove(p.id)} title="Delete project">
							<Trash2 size={14} strokeWidth={2} />
						</button>
					</div>

					<div class="grid-2col">
						<div>
							<div class="form-label">Description</div>
							<input
								class="form-input"
								value={p.description ?? ''}
								onchange={(e) => updateProject(p.id, { description: (e.currentTarget as HTMLInputElement).value })}
								placeholder="(none)"
							/>
						</div>
						<div>
							<div class="form-label">Workspace</div>
							<div class="row gap-2">
								<input
									class="form-input mono"
									value={p.workspace_path ?? ''}
									onchange={(e) => updateProject(p.id, { workspace_path: (e.currentTarget as HTMLInputElement).value })}
									placeholder="(none)"
									style="flex: 1"
								/>
								<button class="btn btn-sm" onclick={() => openPicker(p.id)} title="Pick folder"><FolderSearch size={14} strokeWidth={2} /></button>
							</div>
						</div>
					</div>

					<div class="memory-section">
						<div class="card-title" style="display: flex; align-items: center; gap: 6px">
							<Pin size={12} strokeWidth={2} /> Pinned memories
							<span style="color: var(--fg-dim); font-weight: 400">({memoriesById[p.id]?.length ?? 0})</span>
						</div>
						{#if memoriesById[p.id]?.length}
							<ul class="memory-list">
								{#each memoriesById[p.id] as mem (mem.id)}
									<li>
										<div style="flex: 1">
											<strong>{mem.title}</strong>
											{#if mem.body}<div style="font-size: 12px; color: var(--fg-muted); margin-top: 2px">{mem.body}</div>{/if}
										</div>
										<button class="btn btn-ghost btn-icon" onclick={() => removeMemory(p.id, mem.id)} title="Delete memory">
											<X size={12} strokeWidth={2} />
										</button>
									</li>
								{/each}
							</ul>
						{/if}
						<div class="row gap-2" style="margin-top: var(--sp-2)">
							<input
								class="form-input"
								placeholder="Title"
								value={memoryDrafts[p.id]?.title ?? ''}
								oninput={(e) => {
									const t = (e.currentTarget as HTMLInputElement).value;
									memoryDrafts = { ...memoryDrafts, [p.id]: { title: t, body: memoryDrafts[p.id]?.body ?? '' } };
								}}
								style="flex: 1"
							/>
							<input
								class="form-input"
								placeholder="Body (optional)"
								value={memoryDrafts[p.id]?.body ?? ''}
								oninput={(e) => {
									const b = (e.currentTarget as HTMLInputElement).value;
									memoryDrafts = { ...memoryDrafts, [p.id]: { title: memoryDrafts[p.id]?.title ?? '', body: b } };
								}}
								style="flex: 2"
							/>
							<button class="btn btn-primary btn-sm" onclick={() => addMemory(p.id)} disabled={!memoryDrafts[p.id]?.title?.trim()}>
								<Plus size={12} strokeWidth={2} /> Pin
							</button>
						</div>
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

<FolderPicker
	open={pickerOpen}
	initial={pickerTargetId && pickerTargetId !== 'new' ? projects.find((p) => p.id === pickerTargetId)?.workspace_path ?? null : newWorkspace || null}
	onChoose={onPickedPath}
	onClose={() => { pickerOpen = false; pickerTargetId = null; }}
/>

<style>
	.grid-2col {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: var(--sp-3);
	}
	.memory-section {
		margin-top: var(--sp-4);
		padding-top: var(--sp-3);
		border-top: 1px solid var(--border);
	}
	.memory-list {
		list-style: none;
		padding: 0;
		margin: var(--sp-2) 0 0;
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
	.memory-list li {
		display: flex;
		align-items: flex-start;
		gap: var(--sp-2);
		padding: 8px 10px;
		background: var(--bg-input);
		border-radius: var(--radius);
		font-size: 13px;
	}
</style>
