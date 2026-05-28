<script lang="ts">
	import { goto } from '$app/navigation';
	import { api } from '$lib/api';
	import type { SearchResult } from '$lib/types';
	import {
		Search,
		MessageSquare,
		File,
		FolderKanban,
		Brain,
		ClipboardList,
		FileEdit,
		Settings,
		Clock,
		CornerDownLeft
	} from 'lucide-svelte';

	let { open = $bindable(false) } = $props();
	let query = $state('');
	let results = $state<SearchResult[]>([]);
	let loading = $state(false);
	let selected = $state(0);
	let inputEl = $state<HTMLInputElement>();
	let debounceTimer: ReturnType<typeof setTimeout>;

	const kindIcons: Record<string, typeof MessageSquare> = {
		message: MessageSquare,
		file: File,
		session: MessageSquare,
		project: FolderKanban,
		memory: Brain,
		audit: ClipboardList,
		'file-edit': FileEdit,
		setting: Settings,
		routine: Clock
	};

	const kindLabels: Record<string, string> = {
		message: 'Message',
		file: 'File content',
		session: 'Session',
		project: 'Project',
		memory: 'Memory',
		audit: 'Audit',
		'file-edit': 'File edit',
		setting: 'Setting',
		routine: 'Routine'
	};

	function onKeydown(e: KeyboardEvent) {
		if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
			e.preventDefault();
			open = true;
			return;
		}
		if (!open) return;

		if (e.key === 'Escape') {
			close();
			return;
		}
		if (e.key === 'ArrowDown') {
			e.preventDefault();
			selected = Math.min(selected + 1, results.length - 1);
			return;
		}
		if (e.key === 'ArrowUp') {
			e.preventDefault();
			selected = Math.max(selected - 1, 0);
			return;
		}
		if (e.key === 'Enter') {
			e.preventDefault();
			navigateTo(selected);
			return;
		}
	}

	function close() {
		open = false;
		query = '';
		results = [];
		selected = 0;
	}

	function navigateTo(idx: number) {
		const r = results[idx];
		if (!r) return;
		// Use sessionStorage for session deep-links (matching chat page pattern).
		// The chat page checks sessionStorage in onMount and auto-selects.
		if (r.kind === 'session' || r.kind === 'message') {
			const url = new URL(r.navigate_url, window.location.origin);
			const sessionId = url.searchParams.get('session');
			if (sessionId) {
				sessionStorage.setItem('companion.selectSession', sessionId);
				close();
				goto('/');
				return;
			}
		}
		close();
		goto(r.navigate_url);
	}

	async function search() {
		selected = 0;
		const term = query.trim();
		if (!term) {
			results = [];
			return;
		}
		loading = true;
		try {
			const data = await api<{ results: SearchResult[] }>('/v1/search', {
				query: { q: term, limit: 40 }
			});
			results = data.results || [];
		} catch {
			results = [];
		} finally {
			loading = false;
		}
	}

	function onInput() {
		clearTimeout(debounceTimer);
		debounceTimer = setTimeout(search, 150);
	}

	$effect(() => {
		if (open) {
			document.addEventListener('keydown', onKeydown);
			// Focus the input on next tick
			queueMicrotask(() => inputEl?.focus());
			return () => document.removeEventListener('keydown', onKeydown);
		} else {
			document.addEventListener('keydown', onKeydown);
			return () => document.removeEventListener('keydown', onKeydown);
		}
	});
</script>

{#if open}
	<!-- svelte-ignore a11y_click_events_have_key_events -->
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div class="backdrop" onclick={close}>
		<div class="palette" onclick={(e) => e.stopPropagation()}>
			<!-- Search input -->
			<div class="search-bar">
				<Search size={16} class="search-icon" />
				<input
					bind:this={inputEl}
					bind:value={query}
					oninput={onInput}
					placeholder="Search messages, sessions, projects, files, settings…"
					class="search-input"
					type="text"
					spellcheck={false}
				/>
			</div>

			<!-- Results -->
			<div class="results">
				{#if loading}
					<div class="empty">Searching…</div>
				{:else if query.trim() && results.length === 0}
					<div class="empty">No results for "{query}"</div>
				{:else if results.length === 0}
					<div class="empty">
						Type to search across all companion data
						<div class="hint">Messages, sessions, projects, files, settings, audit log, routines…</div>
					</div>
				{:else}
					{#each results as r, i (r.ref + r.kind)}
						{@const Icon = kindIcons[r.kind] || File}
						{@const label = kindLabels[r.kind] || r.kind}
						<!-- svelte-ignore a11y_click_events_have_key_events -->
						<!-- svelte-ignore a11y_no_static_element_interactions -->
						<div
							class="result-item"
							class:selected={i === selected}
							onclick={() => navigateTo(i)}
							onmouseenter={() => (selected = i)}
						>
							<span class="kind-icon">
								<Icon size={14} />
							</span>
							<div class="result-text">
								<div class="result-title">{r.title}</div>
								<div class="result-subtitle">{r.subtitle}</div>
							</div>
							<span class="kind-label">{label}</span>
							{#if i === selected}
								<span class="enter-hint"><CornerDownLeft size={12} /></span>
							{/if}
						</div>
					{/each}
				{/if}
			</div>

			<!-- Footer -->
			<div class="footer">
				<span class="footer-item"><kbd>↑↓</kbd> Navigate</span>
				<span class="footer-item"><kbd>↵</kbd> Open</span>
				<span class="footer-item"><kbd>Esc</kbd> Close</span>
			</div>
		</div>
	</div>
{/if}

<style>
	.backdrop {
		position: fixed;
		inset: 0;
		z-index: 1000;
		background: rgba(0, 0, 0, 0.6);
		backdrop-filter: blur(4px);
		display: flex;
		justify-content: center;
		padding-top: 12vh;
	}

	.palette {
		width: 600px;
		max-width: calc(100vw - 32px);
		max-height: 70vh;
		background: var(--bg-card);
		border: 1px solid var(--border-strong);
		border-radius: var(--radius-lg);
		box-shadow: 0 16px 64px rgba(0, 0, 0, 0.5);
		display: flex;
		flex-direction: column;
		overflow: hidden;
		animation: slide-in 0.15s ease-out;
	}

	@keyframes slide-in {
		from {
			opacity: 0;
			transform: translateY(-8px) scale(0.98);
		}
		to {
			opacity: 1;
			transform: translateY(0) scale(1);
		}
	}

	.search-bar {
		display: flex;
		align-items: center;
		gap: var(--sp-3);
		padding: var(--sp-4);
		border-bottom: 1px solid var(--border);
	}

	.search-icon {
		color: var(--fg-muted);
		flex-shrink: 0;
	}

	.search-input {
		flex: 1;
		background: transparent;
		border: none;
		outline: none;
		color: var(--fg);
		font-size: var(--fs-16);
	}

	.search-input::placeholder {
		color: var(--fg-dim);
	}

	.results {
		flex: 1;
		overflow-y: auto;
		padding: var(--sp-2);
		max-height: 50vh;
	}

	.empty {
		padding: var(--sp-6) var(--sp-4);
		text-align: center;
		color: var(--fg-muted);
		font-size: var(--fs-13);
	}

	.hint {
		font-size: var(--fs-12);
		color: var(--fg-dim);
		margin-top: var(--sp-2);
	}

	.result-item {
		display: flex;
		align-items: center;
		gap: var(--sp-3);
		padding: 8px 12px;
		border-radius: var(--radius);
		cursor: pointer;
		transition: background 0.05s;
	}

	.result-item:hover,
	.result-item.selected {
		background: var(--bg-hover);
	}

	.kind-icon {
		width: 28px;
		height: 28px;
		display: flex;
		align-items: center;
		justify-content: center;
		border-radius: var(--radius-sm);
		background: var(--bg-input);
		color: var(--fg-muted);
		flex-shrink: 0;
	}

	.result-text {
		flex: 1;
		min-width: 0;
	}

	.result-title {
		font-size: var(--fs-14);
		font-weight: 500;
		color: var(--fg);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.result-subtitle {
		font-size: var(--fs-12);
		color: var(--fg-muted);
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		margin-top: 1px;
	}

	.kind-label {
		font-size: 11px;
		color: var(--fg-dim);
		padding: 2px 6px;
		background: var(--bg-input);
		border-radius: 999px;
		flex-shrink: 0;
	}

	.enter-hint {
		color: var(--fg-dim);
	}

	.footer {
		display: flex;
		gap: var(--sp-4);
		padding: var(--sp-2) var(--sp-4);
		border-top: 1px solid var(--border);
		font-size: var(--fs-12);
		color: var(--fg-dim);
	}

	.footer-item {
		display: flex;
		align-items: center;
		gap: 4px;
	}

	kbd {
		padding: 1px 5px;
		font-family: inherit;
		font-size: 11px;
		border: 1px solid var(--border);
		border-radius: 3px;
		background: var(--bg-input);
	}
</style>
