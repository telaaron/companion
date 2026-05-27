<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from './api';
	import { toasts } from './stores.svelte';
	import { Folder, ArrowUp, Check, X } from 'lucide-svelte';

	interface Props {
		initial?: string | null;
		open: boolean;
		onChoose: (path: string) => void;
		onClose: () => void;
	}

	let { initial, open, onChoose, onClose }: Props = $props();

	interface QuickPath {
		label: string;
		path: string;
	}
	interface FsChild {
		name: string;
		path: string;
	}

	let cwd = $state('');
	let parent = $state<string | null>(null);
	let children = $state<FsChild[]>([]);
	let quick = $state<QuickPath[]>([]);
	let loading = $state(false);
	let typed = $state('');

	async function loadPath(p?: string) {
		loading = true;
		try {
			const data = await api<{ path: string; parent: string | null; children: FsChild[] }>(
				'/v1/fs/browse',
				{ query: p ? { path: p } : {} }
			);
			cwd = data.path;
			parent = data.parent;
			children = data.children || [];
			typed = cwd;
		} catch (e) {
			toasts.show(`Browse failed: ${(e as Error).message}`, 'error');
		} finally {
			loading = false;
		}
	}

	async function loadQuick() {
		try {
			const data = await api<{ paths: QuickPath[] }>('/v1/fs/quick-paths');
			quick = data.paths || [];
		} catch {
			/* optional */
		}
	}

	$effect(() => {
		if (open) {
			loadQuick();
			loadPath(initial || undefined);
		}
	});

	function choose() {
		onChoose(typed.trim() || cwd);
		onClose();
	}
</script>

{#if open}
	<div class="picker-backdrop" role="dialog" aria-modal="true" onclick={onClose} onkeydown={(e) => { if (e.key === 'Escape') onClose(); }}>
		<div class="picker" onclick={(e) => e.stopPropagation()} role="document">
			<header class="picker-header">
				<strong>Pick a folder</strong>
				<button class="btn btn-ghost btn-icon" onclick={onClose} aria-label="Close"><X size={14} strokeWidth={2} /></button>
			</header>

			<div class="picker-body">
				<aside class="quick-list">
					<div class="card-title">Quick paths</div>
					{#each quick as q (q.path)}
						<button class="quick-row" type="button" onclick={() => loadPath(q.path)}>{q.label}</button>
					{/each}
				</aside>

				<div class="browser">
					<div class="row gap-2 align-center" style="margin-bottom: var(--sp-2)">
						<button class="btn btn-sm" disabled={!parent} onclick={() => parent && loadPath(parent)}>
							<ArrowUp size={12} strokeWidth={2} /> Up
						</button>
						<input class="form-input mono" bind:value={typed} onkeydown={(e) => { if (e.key === 'Enter') loadPath(typed); }} placeholder="/path/to/folder" />
						<button class="btn btn-sm" onclick={() => loadPath(typed)}>Go</button>
					</div>
					<div class="children-list">
						{#if loading}<div class="empty"><span class="spinner"></span></div>
						{:else if children.length === 0}<div class="empty">No sub-folders here</div>
						{:else}
							{#each children as c (c.path)}
								<button class="child-row" type="button" onclick={() => loadPath(c.path)}>
									<Folder size={14} strokeWidth={2} />
									<span>{c.name}</span>
								</button>
							{/each}
						{/if}
					</div>
				</div>
			</div>

			<footer class="picker-footer">
				<div class="mono" style="flex: 1; color: var(--fg-muted); font-size: 12px">{typed || cwd}</div>
				<button class="btn" onclick={onClose}>Cancel</button>
				<button class="btn btn-primary" onclick={choose}><Check size={12} strokeWidth={2} /> Pick this folder</button>
			</footer>
		</div>
	</div>
{/if}

<style>
	.picker-backdrop {
		position: fixed;
		inset: 0;
		background: rgba(0, 0, 0, 0.5);
		z-index: 100;
		display: flex;
		align-items: center;
		justify-content: center;
		padding: var(--sp-4);
	}
	.picker {
		background: var(--bg-elev);
		border: 1px solid var(--border);
		border-radius: var(--radius-lg);
		width: 720px;
		max-width: 100%;
		height: 480px;
		display: flex;
		flex-direction: column;
		box-shadow: var(--shadow);
	}
	.picker-header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		padding: var(--sp-3) var(--sp-4);
		border-bottom: 1px solid var(--border);
	}
	.picker-body {
		flex: 1;
		display: grid;
		grid-template-columns: 180px 1fr;
		min-height: 0;
	}
	.quick-list {
		border-right: 1px solid var(--border);
		padding: var(--sp-3);
		overflow-y: auto;
	}
	.quick-row {
		display: block;
		width: 100%;
		text-align: left;
		background: transparent;
		border: none;
		color: var(--fg);
		padding: 6px 8px;
		font-size: var(--fs-13);
		cursor: pointer;
		border-radius: var(--radius);
	}
	.quick-row:hover {
		background: var(--bg-hover);
	}
	.browser {
		padding: var(--sp-3);
		display: flex;
		flex-direction: column;
		min-height: 0;
	}
	.children-list {
		flex: 1;
		overflow-y: auto;
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: var(--sp-1);
	}
	.child-row {
		display: flex;
		gap: var(--sp-2);
		align-items: center;
		width: 100%;
		text-align: left;
		background: transparent;
		border: none;
		color: var(--fg);
		padding: 6px 8px;
		font-size: var(--fs-13);
		cursor: pointer;
		border-radius: var(--radius);
	}
	.child-row:hover {
		background: var(--bg-hover);
	}
	.picker-footer {
		display: flex;
		align-items: center;
		gap: var(--sp-2);
		padding: var(--sp-3) var(--sp-4);
		border-top: 1px solid var(--border);
	}
</style>
