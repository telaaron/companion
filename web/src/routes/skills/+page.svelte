<script lang="ts">
	import { onMount } from 'svelte';
	import { api, getToken } from '$lib/api';
	import { toasts, confirmStore } from '$lib/stores.svelte';
	import PageHeader from '$lib/PageHeader.svelte';
	import InfoBanner from '$lib/InfoBanner.svelte';
	import { Sparkles, Download, Check, Upload, Link2, Plus, Trash2, FolderInput } from 'lucide-svelte';
	import type { Skill } from '$lib/types';

	let local = $state<Skill[]>([]);
	let catalog = $state<Skill[]>([]);
	let loading = $state(true);
	let tab = $state<'local' | 'catalog' | 'claude'>('local');

	// Import + create modal state
	let showImport = $state(false);
	let showCreate = $state(false);
	let importUrl = $state('');
	let busy = $state(false);
	let fileInput: HTMLInputElement | undefined = $state();

	// Create form
	let cName = $state('');
	let cDescription = $state('');
	let cInstructions = $state('');
	let cEntry = $state('');

	let installed = $derived(local.filter((s) => s.source !== 'claude'));
	let claudeSkills = $derived(local.filter((s) => s.source === 'claude'));

	async function load() {
		loading = true;
		try {
			const [l, c] = await Promise.all([
				api<{ skills: Skill[] }>('/v1/skills/local'),
				api<{ skills: Skill[] }>('/v1/skills/catalog').catch(() => ({ skills: [] }))
			]);
			local = l.skills || [];
			catalog = c.skills || [];
		} catch (e) {
			toasts.show(`Load failed: ${(e as Error).message}`, 'error');
		} finally {
			loading = false;
		}
	}

	async function install(skill: Skill) {
		try {
			await api(`/v1/skills/install/${encodeURIComponent(skill.id)}`, { method: 'POST' });
			toasts.show(`Installed ${skill.name}`, 'ok');
			await load();
		} catch (e) {
			toasts.show(`Install failed: ${(e as Error).message}`, 'error');
		}
	}

	async function importClaude(skill: Skill) {
		busy = true;
		try {
			await api(`/v1/skills/import-claude/${encodeURIComponent(skill.id)}`, { method: 'POST' });
			toasts.show(`Imported ${skill.name} — now runnable`, 'ok');
			await load();
		} catch (e) {
			toasts.show(`Import failed: ${(e as Error).message}`, 'error');
		} finally {
			busy = false;
		}
	}

	async function importFromUrl() {
		if (!importUrl.trim()) return;
		busy = true;
		try {
			// Derive a slug from the URL's last path segment.
			const slug = (importUrl.split('/').pop() || 'skill').replace(/\.(tar\.gz|tgz|zip)$/, '');
			await api(`/v1/skills/install/${encodeURIComponent(slug)}`, {
				method: 'POST',
				body: { url: importUrl.trim() }
			});
			toasts.show('Skill imported', 'ok');
			importUrl = '';
			showImport = false;
			await load();
		} catch (e) {
			toasts.show(`Import failed: ${(e as Error).message}`, 'error');
		} finally {
			busy = false;
		}
	}

	async function uploadFile(file: File) {
		busy = true;
		try {
			const form = new FormData();
			form.append('file', file);
			const res = await fetch('/v1/skills/upload', {
				method: 'POST',
				headers: { Authorization: `Bearer ${getToken()}` },
				body: form
			});
			if (!res.ok) throw new Error(`HTTP ${res.status}: ${(await res.text()).slice(0, 160)}`);
			toasts.show('Skill uploaded', 'ok');
			showImport = false;
			await load();
		} catch (e) {
			toasts.show(`Upload failed: ${(e as Error).message}`, 'error');
		} finally {
			busy = false;
		}
	}

	function onFilePicked(e: Event) {
		const f = (e.currentTarget as HTMLInputElement).files?.[0];
		if (f) uploadFile(f);
	}

	async function createSkill() {
		if (!cName.trim()) return;
		busy = true;
		try {
			await api('/v1/skills/create', {
				method: 'POST',
				body: {
					name: cName.trim(),
					description: cDescription.trim(),
					instructions: cInstructions.trim(),
					entry_code: cEntry.trim() || null
				}
			});
			toasts.show(`Created ${cName.trim()}`, 'ok');
			cName = cDescription = cInstructions = cEntry = '';
			showCreate = false;
			await load();
		} catch (e) {
			toasts.show(`Create failed: ${(e as Error).message}`, 'error');
		} finally {
			busy = false;
		}
	}

	async function removeSkill(skill: Skill) {
		if (!(await confirmStore.ask(`Delete skill "${skill.name}"?`))) return;
		try {
			await api(`/v1/skills/local/${encodeURIComponent(skill.id)}`, { method: 'DELETE' });
			toasts.show('Deleted', 'ok');
			await load();
		} catch (e) {
			toasts.show(`Delete failed: ${(e as Error).message}`, 'error');
		}
	}

	onMount(load);
</script>

<PageHeader title="Skills" sub="Installable agent skills + tools">
	{#snippet actions()}
		<button class="btn" type="button" onclick={() => (showImport = true)}>
			<FolderInput size={14} strokeWidth={2} /> Import
		</button>
		<button class="btn btn-primary" type="button" onclick={() => (showCreate = true)}>
			<Plus size={14} strokeWidth={2} /> Create
		</button>
	{/snippet}
</PageHeader>

<div class="page-body">
	<InfoBanner
		title="What are skills?"
		storageKey="skills"
		body="Skills are reusable capability packs the agent can pull in for a task — scraping, image generation, document handling and more. Installed skills are runnable by the agent. 'From Claude Code' shows skills detected in your ~/.claude setup; import one to make it runnable here. You can also upload a skill, pull one from a URL, or create a new one — or just ask in chat and the agent will build it with you."
	/>
	<div class="row gap-2" style="margin-bottom: var(--sp-3)">
		<button class="btn" class:btn-primary={tab === 'local'} onclick={() => (tab = 'local')}>Installed ({installed.length})</button>
		<button class="btn" class:btn-primary={tab === 'claude'} onclick={() => (tab = 'claude')}>From Claude Code ({claudeSkills.length})</button>
		<button class="btn" class:btn-primary={tab === 'catalog'} onclick={() => (tab = 'catalog')}>Catalog ({catalog.length})</button>
	</div>

	{#if loading}<div class="empty"><span class="spinner"></span> Loading…</div>
	{:else}
		{@const list = tab === 'local' ? installed : tab === 'claude' ? claudeSkills : catalog}
		<div class="grid-cards">
			{#each list as s (s.id)}
				<div class="card">
					<div class="row align-center gap-2" style="justify-content: space-between">
						<div class="row align-center gap-2" style="min-width: 0">
							<Sparkles size={14} strokeWidth={2} />
							<strong class="truncate">{s.name}</strong>
						</div>
						{#if tab === 'local'}
							<button class="btn btn-ghost btn-icon" type="button" title="Delete" onclick={() => removeSkill(s)}>
								<Trash2 size={12} strokeWidth={2} />
							</button>
						{/if}
					</div>
					{#if s.description}<p style="margin: var(--sp-2) 0 var(--sp-3); color: var(--fg-muted); font-size: var(--fs-13)">{s.description}</p>{/if}
					{#if tab === 'claude'}
						<button class="btn btn-primary btn-sm" disabled={busy} onclick={() => importClaude(s)}>
							<Download size={12} strokeWidth={2} /> Import (make runnable)
						</button>
					{:else if tab === 'catalog' && !s.installed}
						<button class="btn btn-primary btn-sm" disabled={busy} onclick={() => install(s)}>
							<Download size={12} strokeWidth={2} /> Install
						</button>
					{:else if tab === 'local'}
						<span class="pill pill-ok"><Check size={10} strokeWidth={2.5} /> Runnable</span>
					{/if}
				</div>
			{/each}
			{#if list.length === 0}
				<div class="empty">
					{#if tab === 'claude'}No Claude Code skills detected{:else if tab === 'catalog'}No skills in catalog{:else}No skills installed yet — Import or Create one{/if}
				</div>
			{/if}
		</div>
	{/if}
</div>

<!-- Import modal -->
{#if showImport}
	<div class="modal-backdrop" role="dialog" tabindex="-1" onclick={() => (showImport = false)} onkeydown={(e) => { if (e.key === 'Escape') showImport = false; }}>
		<div class="modal-card" role="document" onclick={(e) => e.stopPropagation()}>
			<h3 style="margin: 0 0 var(--sp-3)">Import a skill</h3>

			<div class="import-row">
				<div>
					<strong><Upload size={13} strokeWidth={2} /> Upload a file</strong>
					<p class="hint">A .zip, .tar.gz, or a single SKILL.md.</p>
				</div>
				<button class="btn btn-sm" disabled={busy} onclick={() => fileInput?.click()}>Choose file…</button>
				<input bind:this={fileInput} type="file" accept=".zip,.tar.gz,.tgz,.md" style="display:none" onchange={onFilePicked} />
			</div>

			<div class="import-row">
				<div style="flex: 1">
					<strong><Link2 size={13} strokeWidth={2} /> From a URL</strong>
					<p class="hint">A .tar.gz / .zip skill archive.</p>
					<input class="form-input mono" placeholder="https://…/skill.tar.gz" bind:value={importUrl} style="margin-top: 6px" />
				</div>
				<button class="btn btn-sm btn-primary" disabled={busy || !importUrl.trim()} onclick={importFromUrl}>Import</button>
			</div>

			<p class="hint" style="margin-top: var(--sp-3)">
				Claude Code skills can be imported from the “From Claude Code” tab.
			</p>

			<div class="row gap-2 justify-end" style="margin-top: var(--sp-3)">
				<button class="btn" onclick={() => (showImport = false)}>Close</button>
			</div>
		</div>
	</div>
{/if}

<!-- Create modal -->
{#if showCreate}
	<div class="modal-backdrop" role="dialog" tabindex="-1" onclick={() => (showCreate = false)} onkeydown={(e) => { if (e.key === 'Escape') showCreate = false; }}>
		<div class="modal-card" role="document" style="max-width: 560px" onclick={(e) => e.stopPropagation()}>
			<h3 style="margin: 0 0 var(--sp-2)">Create a skill</h3>
			<p class="hint" style="margin: 0 0 var(--sp-3)">
				Or just ask in chat — the agent can build a skill with you using SkillCreate.
			</p>

			<label class="form-label">Name</label>
			<input class="form-input" bind:value={cName} placeholder="My Skill" />

			<label class="form-label" style="margin-top: var(--sp-2)">Description</label>
			<input class="form-input" bind:value={cDescription} placeholder="One line: what it does" />

			<label class="form-label" style="margin-top: var(--sp-2)">Instructions (SKILL.md body)</label>
			<textarea class="form-textarea" rows="4" bind:value={cInstructions} placeholder="When and how to use this skill…"></textarea>

			<label class="form-label" style="margin-top: var(--sp-2)">Entry code (optional Python)</label>
			<textarea class="form-textarea mono" rows="5" bind:value={cEntry} placeholder={'import sys, json\nargs = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}\nprint("hello")'}></textarea>

			<div class="row gap-2 justify-end" style="margin-top: var(--sp-3)">
				<button class="btn" onclick={() => (showCreate = false)}>Cancel</button>
				<button class="btn btn-primary" disabled={busy || !cName.trim()} onclick={createSkill}>Create</button>
			</div>
		</div>
	</div>
{/if}

<style>
	.modal-backdrop {
		position: fixed;
		inset: 0;
		background: rgba(0, 0, 0, 0.5);
		display: flex;
		align-items: center;
		justify-content: center;
		z-index: 2000;
	}
	.modal-card {
		background: var(--bg-card);
		border: 1px solid var(--border);
		border-radius: var(--radius-lg);
		padding: var(--sp-4);
		min-width: 420px;
		max-width: 480px;
		width: 90%;
		max-height: 85vh;
		overflow-y: auto;
	}
	.import-row {
		display: flex;
		align-items: flex-start;
		gap: var(--sp-3);
		padding: var(--sp-3) 0;
		border-top: 1px solid var(--border);
	}
	.import-row strong {
		display: inline-flex;
		align-items: center;
		gap: 6px;
	}
	.hint {
		margin: 2px 0 0;
		font-size: 12px;
		color: var(--fg-muted);
	}
</style>
