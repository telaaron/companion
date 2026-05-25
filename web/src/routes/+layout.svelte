<script lang="ts">
	import { page } from '$app/state';
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
		if (href === '/') return path === '/';
		return path === href || path.startsWith(href + '/');
	}
</script>

<div class="app">
	<aside class="sidebar">
		<div class="brand">
			<div class="brand-name">Companion</div>
			<div class="brand-status" class:offline={!health.online}>
				<span class="dot"></span>
				{health.online ? 'online' : 'offline'}
			</div>
		</div>
		<nav class="nav">
			{#each NAV as item (item.href)}
				{@const Icon = item.icon}
				<a
					class="nav-item"
					class:active={isActive(item.href)}
					href={item.href}
					data-sveltekit-preload-data="hover"
				>
					<Icon size={16} strokeWidth={2} />
					{item.label}
				</a>
			{/each}
		</nav>
		<div class="sidebar-footer">
			<div class="cost-row"><span>Today</span><span>${cost.today.toFixed(4)}</span></div>
			<div class="cost-row"><span>7d</span><span>${cost.week.toFixed(4)}</span></div>
			<button class="theme" type="button" onclick={() => theme.toggle()}>
				{theme.value === 'dark' ? '☼ light' : '☾ dark'}
			</button>
		</div>
	</aside>

	<main class="view">
		{@render children()}
	</main>
</div>

{#if toasts.items.length > 0}
	<div class="toast-stack">
		{#each toasts.items as t (t.id)}
			<div class="toast toast-{t.level}">{t.message}</div>
		{/each}
	</div>
{/if}
