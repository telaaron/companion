<script lang="ts">
	import { page } from '$app/state';
	import { base } from '$app/paths';
	import { onMount } from 'svelte';
	import { theme, toasts, cost, health } from '$lib/stores.svelte';
	import {
		MessageSquare,
		FolderKanban,
		TrendingUp,
		FileEdit,
		ClipboardList,
		KeyRound,
		FolderTree,
		Sparkles,
		Brain,
		Clock,
		Lightbulb,
		Settings as SettingsIcon
	} from 'lucide-svelte';
	import CommandPalette from '$lib/CommandPalette.svelte';
	import ConfirmModal from '$lib/ConfirmModal.svelte';
	import '../app.css';

	const NAV = [
		{ href: '/', label: 'Chat', icon: MessageSquare },
		{ href: '/projects', label: 'Projects', icon: FolderKanban },
		{ href: '/usage', label: 'Usage', icon: TrendingUp },
		{ href: '/files', label: 'File edits', icon: FileEdit },
		{ href: '/audit', label: 'Audit log', icon: ClipboardList },
		{ href: '/env', label: 'Env vault', icon: KeyRound },
		{ href: '/root', label: 'Root files', icon: FolderTree },
		{ href: '/skills', label: 'Skills', icon: Sparkles },
		{ href: '/memory', label: 'Memory', icon: Brain },
		{ href: '/routines', label: 'Routines', icon: Clock },
		{ href: '/insights', label: 'Insights', icon: Lightbulb },
		{ href: '/settings', label: 'Settings', icon: SettingsIcon }
	] as const;

	let { children } = $props();
	let paletteOpen = $state(false);

	onMount(() => {
		document.documentElement.setAttribute('data-theme', theme.value);
		cost.refresh();
		health.probe();
		const id = setInterval(() => {
			cost.refresh();
			health.probe();
		}, 30_000);
		return () => clearInterval(id);
	});

	function isActive(href: string): boolean {
		const path = page.url.pathname;
		const full = base + (href === '/' ? '' : href);
		if (href === '/') return path === base + '/' || path === base || path === '/';
		return path === full || path.startsWith(full + '/');
	}
</script>

<div class="app">
	<aside class="sidebar">
		<nav class="nav">
			{#each NAV as item (item.href)}
				{@const Icon = item.icon}
				<a
					class="nav-item"
					class:active={isActive(item.href)}
					href={item.href === '/' ? base + '/' : base + item.href}
					data-sveltekit-preload-data="hover"
				>
					<Icon size={16} strokeWidth={2} />
					{item.label}
				</a>
			{/each}
		</nav>
		<div class="sidebar-footer">
			<div class="cost-row">
				<span class="status-dot" class:offline={!health.online} title={health.online ? 'online' : 'offline'}></span>
				<span style="flex: 1">Today</span>
				<span>${cost.today.toFixed(4)}</span>
			</div>
			<div class="cost-row"><span style="flex: 1; padding-left: 14px">7d</span><span>${cost.week.toFixed(4)}</span></div>
			<button class="theme" type="button" onclick={() => theme.toggle()}>
				{theme.value === 'dark' ? '☼ light' : '☾ dark'}
			</button>
			<div class="search-hint" onclick={() => (paletteOpen = true)}><kbd>⌘K</kbd> Search</div>
		</div>
	</aside>

	<main class="view">
		{@render children()}
	</main>
</div>

<CommandPalette bind:open={paletteOpen} />
<ConfirmModal />

{#if toasts.items.length > 0}
	<div class="toast-stack">
		{#each toasts.items as t (t.id)}
			<div class="toast toast-{t.level}">{t.message}</div>
		{/each}
	</div>
{/if}
