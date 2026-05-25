<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/api';
	import { toasts } from '$lib/stores.svelte';
	import PageHeader from '$lib/PageHeader.svelte';
	import { Save, Trash2, Plus, Eye, EyeOff } from 'lucide-svelte';

	interface EnvEntry {
		key: string;
		value: string;
		secret: boolean;
		comment?: string;
	}

	let entries = $state<EnvEntry[]>([]);
	let path = $state('');
	let loading = $state(true);
	let newKey = $state('');
	let newValue = $state('');
	let revealed = $state<Record<string, boolean>>({});

	async function load() {
		loading = true;
		try {
			const data = await api<{ path: string; entries: EnvEntry[] }>('/v1/env');
			entries = data.entries || [];
			path = data.path;
		} catch (e) {
			toasts.show(`Load failed: ${(e as Error).message}`, 'error');
		} finally {
			loading = false;
		}
	}

	async function save(key: string, value: string) {
		try {
			await api('/v1/env', { method: 'PUT', body: { key, value } });
			toasts.show(`Saved ${key}`, 'ok');
			await load();
		} catch (e) {
			toasts.show(`Save failed: ${(e as Error).message}`, 'error');
		}
	}

	async function remove(key: string) {
		if (!confirm(`Delete ${key}?`)) return;
		try {
			await api(`/v1/env/${encodeURIComponent(key)}`, { method: 'DELETE' });
			await load();
		} catch (e) {
			toasts.show(`Delete failed: ${(e as Error).message}`, 'error');
		}
	}

	async function addNew() {
		if (!newKey.trim()) return;
		await save(newKey.trim().toUpperCase(), newValue);
		newKey = '';
		newValue = '';
	}

	onMount(load);
</script>

<PageHeader title="Env vault" sub={path || 'Persistent environment variables for the proxy'} />

<div class="page-body">
	<div class="card" style="margin-bottom: var(--sp-4)">
		<div class="card-title">Add new key</div>
		<div class="row gap-2 align-center">
			<input class="form-input mono" placeholder="KEY_NAME" bind:value={newKey} style="flex: 1" />
			<input class="form-input mono" placeholder="value" bind:value={newValue} style="flex: 2" />
			<button class="btn btn-primary" onclick={addNew} disabled={!newKey.trim()}>
				<Plus size={14} strokeWidth={2} /> Add
			</button>
		</div>
	</div>

	{#if loading}
		<div class="empty"><span class="spinner"></span> Loading…</div>
	{:else if entries.length === 0}
		<div class="empty">Env file is empty</div>
	{:else}
		<div class="card">
			<table class="table">
				<thead>
					<tr>
						<th style="width: 30%">Key</th>
						<th>Value</th>
						<th style="width: 80px"></th>
					</tr>
				</thead>
				<tbody>
					{#each entries as e (e.key)}
						<tr>
							<td class="mono">{e.key}</td>
							<td>
								<div class="row gap-2 align-center">
									<input
										class="form-input mono"
										type={e.secret && !revealed[e.key] ? 'password' : 'text'}
										value={e.value}
										onchange={(ev) => save(e.key, (ev.currentTarget as HTMLInputElement).value)}
										style="flex: 1"
									/>
									{#if e.secret}
										<button class="btn btn-ghost btn-icon" type="button" onclick={() => (revealed[e.key] = !revealed[e.key])}>
											{#if revealed[e.key]}<EyeOff size={14} strokeWidth={2} />{:else}<Eye size={14} strokeWidth={2} />{/if}
										</button>
									{/if}
								</div>
							</td>
							<td>
								<button class="btn btn-ghost btn-icon" type="button" onclick={() => remove(e.key)} title="Delete">
									<Trash2 size={14} strokeWidth={2} />
								</button>
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/if}
</div>
