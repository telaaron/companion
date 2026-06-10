<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/api';
	import { toasts } from '$lib/stores.svelte';
	import PageHeader from '$lib/PageHeader.svelte';
	import InfoBanner from '$lib/InfoBanner.svelte';

	// Human-readable labels for the raw category/event codes the backend logs.
	const CATEGORY_LABEL: Record<string, string> = {
		env: 'Env vault',
		preferences: 'Memory',
		memory: 'Memory',
		routines: 'Routines',
		skills: 'Skills',
		sessions: 'Chats',
		projects: 'Projects',
		auth: 'Login',
		settings: 'Settings',
		tool: 'Agent',
		job: 'Agent'
	};
	const EVENT_LABEL: Record<string, string> = {
		create: 'created',
		update: 'changed',
		delete: 'deleted',
		set: 'set',
		run: 'ran',
		install: 'installed',
		login: 'signed in'
	};
	function catLabel(c: string): string {
		return CATEGORY_LABEL[c] ?? c;
	}
	function describe(e: { category: string; event: string; detail: string }): string {
		// Tool calls read better as "ran the Bash tool" than "agent tool call".
		if (e.category === 'tool') {
			return e.detail ? `ran the ${e.detail} tool` : 'ran a tool';
		}
		const verb = EVENT_LABEL[e.event] ?? e.event.replace(/_/g, ' ');
		const what = catLabel(e.category).toLowerCase();
		const d = e.detail ? ` "${e.detail}"` : '';
		return `${what} ${verb}${d}`;
	}

	interface AuditEvent {
		id: number;
		ts: number;
		category: string;
		event: string;
		detail: string;
		metadata?: string | Record<string, unknown>;
	}

	let events = $state<AuditEvent[]>([]);
	let loading = $state(true);
	let filter = $state('');

	async function load() {
		loading = true;
		try {
			const data = await api<{ events: AuditEvent[] }>('/v1/audit');
			events = data.events || [];
		} catch (e) {
			toasts.show(`Load failed: ${(e as Error).message}`, 'error');
		} finally {
			loading = false;
		}
	}

	let filtered = $derived(
		filter.trim()
			? events.filter(
					(e) =>
						e.category.includes(filter) || e.event.includes(filter) || (e.detail || '').includes(filter)
				)
			: events
	);

	onMount(load);

	function fmtTime(ts: number): string {
		try {
			// Backend stores ts as milliseconds (int(time.time() * 1000)).
			return new Date(ts).toLocaleString();
		} catch {
			return '';
		}
	}
</script>

<PageHeader title="Audit log" sub={`${events.length} events`} />

<div class="page-body">
	<InfoBanner
		title="What is this?"
		storageKey="audit"
		body="A running history of every change made inside Companion — keys set or deleted, routines run, chats created, settings changed. Nothing here is sent anywhere; it's a local record so you can see what happened and when. Use it to retrace your steps or spot something you didn't expect."
	/>
	<input class="form-input" placeholder="Search…" bind:value={filter} style="margin-bottom: var(--sp-3)" />
	{#if loading}<div class="empty"><span class="spinner"></span> Loading…</div>
	{:else if filtered.length === 0}<div class="empty">No events {filter ? 'matching filter' : 'logged yet'}</div>
	{:else}
		<div class="card" style="padding: 0">
			<table class="table">
				<thead><tr><th>When</th><th>Area</th><th>What happened</th></tr></thead>
				<tbody>
					{#each filtered as e (e.id)}
						<tr>
							<td class="mono" style="white-space: nowrap; color: var(--fg-muted)">{fmtTime(e.ts)}</td>
							<td><span class="pill">{catLabel(e.category)}</span></td>
							<td title={`${e.category}.${e.event} ${e.detail}`}>{describe(e)}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/if}
</div>
