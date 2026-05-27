<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/api';
	import { toasts } from '$lib/stores.svelte';
	import PageHeader from '$lib/PageHeader.svelte';
	import { Sparkles, Download, Check } from 'lucide-svelte';
	import type { Skill } from '$lib/types';

	let local = $state<Skill[]>([]);
	let catalog = $state<Skill[]>([]);
	let loading = $state(true);
	let tab = $state<'local' | 'catalog'>('local');

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

	onMount(load);
</script>

<PageHeader title="Skills" sub="Installable agent skills + tools" />

<div class="page-body">
	<div class="row gap-2" style="margin-bottom: var(--sp-3)">
		<button class="btn" class:btn-primary={tab === 'local'} onclick={() => (tab = 'local')}>Installed ({local.length})</button>
		<button class="btn" class:btn-primary={tab === 'catalog'} onclick={() => (tab = 'catalog')}>Catalog ({catalog.length})</button>
	</div>

	{#if loading}<div class="empty"><span class="spinner"></span> Loading…</div>
	{:else}
		<div class="grid-cards">
			{#each tab === 'local' ? local : catalog as s (s.id)}
				<div class="card">
					<div class="row align-center gap-2"><Sparkles size={14} strokeWidth={2} /><strong>{s.name}</strong></div>
					{#if s.description}<p style="margin: var(--sp-2) 0 var(--sp-3); color: var(--fg-muted); font-size: var(--fs-13)">{s.description}</p>{/if}
					{#if tab === 'catalog' && !s.installed}
						<button class="btn btn-primary btn-sm" onclick={() => install(s)}>
							<Download size={12} strokeWidth={2} /> Install
						</button>
					{:else if s.installed}
						<span class="pill pill-ok"><Check size={10} strokeWidth={2.5} /> Installed</span>
					{/if}
				</div>
			{/each}
			{#if (tab === 'local' ? local : catalog).length === 0}
				<div class="empty">No skills {tab === 'local' ? 'installed' : 'in catalog'}</div>
			{/if}
		</div>
	{/if}
</div>
