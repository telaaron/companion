<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/api';
	import { toasts } from '$lib/stores.svelte';
	import PageHeader from '$lib/PageHeader.svelte';
	import { Plus, Play, Trash2, Power, X, Clock, FileText, RefreshCw } from 'lucide-svelte';
	import type { Routine } from '$lib/types';

	interface RoutineRun {
		id: number;
		routine_id: string;
		job_id?: string;
		status: string;
		started_ms?: number;
		finished_ms?: number;
	}
	interface Template {
		slug: string;
		name: string;
		description: string;
		inputs?: Array<{ name: string; label: string; default?: string | null }>;
	}

	let routines = $state<Routine[]>([]);
	let runsByRoutine = $state<Record<string, RoutineRun[]>>({});
	let loading = $state(true);
	let drawerOpen = $state(false);
	let drawerMode = $state<'create' | 'template'>('create');
	let newName = $state('');
	let newDescription = $state('');
	let newCron = $state('0 9 * * *');
	let newPrompt = $state('');
	let newModel = $state('deepseek/deepseek-v4-pro');
	let newTz = $state('UTC');
	let saving = $state(false);
	let cronFires = $state<number[]>([]);
	let cronPreviewLoading = $state(false);
	let templates = $state<Template[]>([]);
	let expandedRoutineId = $state<string | null>(null);
	let expandedJobOutput = $state<string>('');
	let jobOutputLoading = $state(false);
	let triggerLoading = $state<Record<string, boolean>>({});

	// Custom confirm modal — native window.confirm is blocked in Tauri-WebKit.
	let confirmState = $state<{ open: boolean; message: string; resolve: ((v: boolean) => void) | null }>({ open: false, message: '', resolve: null });

	function customConfirm(message: string): Promise<boolean> {
		return new Promise((resolve) => {
			confirmState = { open: true, message, resolve };
		});
	}

	async function load() {
		loading = true;
		try {
			const data = await api<{ routines: Routine[] }>('/v1/routines');
			routines = data.routines || [];
		} catch (e) {
			toasts.show(`Load failed: ${(e as Error).message}`, 'error');
		} finally {
			loading = false;
		}
	}

	function getCron(r: Routine): string {
		try {
			const tc: Record<string, unknown> = typeof r.trigger_config === 'string' ? JSON.parse(r.trigger_config) : (r.trigger_config || {});
			return (tc.expression as string) || (r as unknown as Record<string, string>).cron || '';
		} catch {
			return (r as unknown as Record<string, string>).cron || '';
		}
	}

	function getPrompt(r: Routine): string {
		try {
			const p: Record<string, unknown> = typeof r.payload === 'string' ? JSON.parse(r.payload) : (r.payload || {});
			const msgs = Array.isArray(p.messages) ? p.messages : [];
			const userMsg = msgs.find((m: Record<string, unknown>) => m.role === 'user');
			return (userMsg?.content as string) || '';
		} catch {
			return (r as unknown as Record<string, string>).prompt || '';
		}
	}

	function getModel(r: Routine): string {
		try {
			const p: Record<string, unknown> = typeof r.payload === 'string' ? JSON.parse(r.payload) : (r.payload || {});
			return (p.model as string) || 'deepseek/deepseek-v4-pro';
		} catch {
			return 'deepseek/deepseek-v4-pro';
		}
	}

	async function previewCron() {
		if (!newCron.trim()) return;
		cronPreviewLoading = true;
		try {
			const data = await api<{ fires: number[] }>('/v1/routines/preview-cron', {
				query: { expr: newCron.trim(), tz: newTz }
			});
			cronFires = data.fires || [];
		} catch (e) {
			cronFires = [];
			toasts.show(`Preview failed: ${(e as Error).message}`, 'warn');
		} finally {
			cronPreviewLoading = false;
		}
	}

	function onCronChange(e: Event) {
		newCron = (e.target as HTMLInputElement).value;
		if (newCron.trim()) previewCron();
	}

	async function loadTemplates() {
		try {
			const data = await api<{ templates: Template[] }>('/v1/routines/templates');
			templates = data.templates || [];
		} catch {
			/* optional */
		}
	}

	function applyTemplate(t: Template) {
		newName = t.name;
		newDescription = t.description || '';
		newCron = '0 9 * * *';
		newPrompt = '';
		newModel = 'deepseek/deepseek-v4-pro';
		drawerMode = 'create';
		drawerOpen = true;
	}

	async function createRoutine() {
		if (!newName.trim()) return;
		saving = true;
		try {
			await api('/v1/routines', {
				method: 'POST',
				body: {
					name: newName.trim(),
					description: newDescription.trim(),
					trigger_type: 'cron',
					trigger_config: { expression: newCron.trim() || '0 9 * * *', tz: newTz },
					payload: {
						model: newModel,
						messages: [{ role: 'user', content: newPrompt.trim() }]
					},
					enabled: true
				}
			});
			toasts.show(`Routine "${newName}" created`, 'ok');
			closeDrawer();
			await load();
		} catch (e) {
			toasts.show(`Create failed: ${(e as Error).message}`, 'error');
		} finally {
			saving = false;
		}
	}

	function closeDrawer() {
		drawerOpen = false;
		newName = '';
		newDescription = '';
		newCron = '0 9 * * *';
		newPrompt = '';
		newModel = 'deepseek/deepseek-v4-pro';
		newTz = 'UTC';
		cronFires = [];
	}

	async function trigger(r: Routine) {
		triggerLoading[r.id] = true;
		try {
			await api(`/v1/routines/${r.id}/run`, { method: 'POST' });
			toasts.show('Routine triggered', 'ok');
			// Load runs to show the new one
			await loadRuns(r.id);
		} catch (e) {
			toasts.show(`Trigger failed: ${(e as Error).message}`, 'error');
		} finally {
			triggerLoading[r.id] = false;
		}
	}

	async function loadRuns(routineId: string) {
		try {
			const data = await api<{ runs: RoutineRun[] }>(`/v1/routines/${routineId}/runs`, {
				query: { limit: 10 }
			});
			runsByRoutine[routineId] = data.runs || [];
		} catch {
			runsByRoutine[routineId] = [];
		}
	}

	async function loadJobOutput(routineId: string, jobId: string) {
		expandedRoutineId = routineId;
		jobOutputLoading = true;
		expandedJobOutput = '';
		try {
			const data = await api<{ blocks: Array<{ kind: string; text: string }> }>(`/v1/jobs/${jobId}/output`);
			expandedJobOutput = (data.blocks || []).map((b) => b.text || '').join('\n\n');
		} catch (e) {
			expandedJobOutput = `(unable to load: ${(e as Error).message})`;
		} finally {
			jobOutputLoading = false;
		}
	}

	async function toggleRunHistory(r: Routine) {
		if (expandedRoutineId === r.id) {
			expandedRoutineId = null;
			return;
		}
		await loadRuns(r.id);
		expandedRoutineId = r.id;
	}

	async function toggle(r: Routine) {
		try {
			await api(`/v1/routines/${r.id}`, { method: 'PATCH', body: { enabled: !r.enabled } });
			await load();
		} catch (e) {
			toasts.show(`Toggle failed: ${(e as Error).message}`, 'error');
		}
	}

	async function remove(id: string) {
		if (!(await customConfirm('Delete this routine?'))) return;
		try {
			await api(`/v1/routines/${id}`, { method: 'DELETE' });
			await load();
		} catch (e) {
			toasts.show(`Delete failed: ${(e as Error).message}`, 'error');
		}
	}

	onMount(async () => {
		await Promise.all([load(), loadTemplates()]);
	});

	function fmtMs(ms: number | undefined): string {
		if (!ms) return '—';
		try { return new Date(ms).toLocaleString(); } catch { return '—'; }
	}
</script>

<PageHeader title="Routines" sub="Scheduled cron-based agent runs">
	{#snippet actions()}
		<button class="btn btn-primary" type="button" onclick={() => { drawerMode = 'create'; drawerOpen = true; }}>
			<Plus size={14} strokeWidth={2} /> New routine
		</button>
	{/snippet}
</PageHeader>

<div class="page-body">
	{#if loading}
		<div class="empty"><span class="spinner"></span> Loading…</div>
	{:else if routines.length === 0}
		<div class="empty">No routines configured</div>
	{:else}
		<div class="col" style="gap: var(--sp-3)">
			{#each routines as r (r.id)}
				<div class="card">
					<div class="row justify-between align-center">
						<div>
							<strong>{r.name}</strong>
							<span class="pill" class:pill-ok={r.enabled} class:pill-error={!r.enabled} style="margin-left: var(--sp-2)">
								{r.enabled ? 'enabled' : 'disabled'}
							</span>
						</div>
						<div class="row gap-2">
							<button class="btn btn-sm" onclick={() => trigger(r)} disabled={triggerLoading[r.id]}>
								{#if triggerLoading[r.id]}
									<span class="spinner"></span>
								{:else}
									<Play size={12} strokeWidth={2} />
								{/if}
								Run now
							</button>
							<button class="btn btn-sm" onclick={() => toggle(r)}><Power size={12} strokeWidth={2} /></button>
							<button class="btn btn-sm btn-ghost" onclick={() => remove(r.id)}><Trash2 size={12} strokeWidth={2} /></button>
						</div>
					</div>
					<div class="mono" style="color: var(--fg-muted); font-size: 12px; margin: var(--sp-2) 0">
						<Clock size={12} strokeWidth={2} style="vertical-align: middle; margin-right: 4px" />
						{getCron(r)}
					</div>
					{#if getPrompt(r)}
						<p style="font-size: 13px; margin: 0 0 var(--sp-2); white-space: pre-wrap">{getPrompt(r)}</p>
					{/if}
					<div style="font-size: 11px; color: var(--fg-muted)">
						Model: <span class="mono">{getModel(r)}</span>
						{#if r.last_run_at}<span style="margin-left: var(--sp-3)">Last run: {new Date(r.last_run_at).toLocaleString()}</span>{/if}
						{#if r.next_run_at}<span style="margin-left: var(--sp-3)">Next: {new Date(r.next_run_at).toLocaleString()}</span>{/if}
					</div>

					<!-- History toggle -->
					<button class="btn btn-sm btn-ghost" style="margin-top: var(--sp-2)" onclick={() => toggleRunHistory(r)}>
						<FileText size={12} strokeWidth={2} />
						{expandedRoutineId === r.id ? 'Hide history' : `Run history`}
					</button>

					{#if expandedRoutineId === r.id && runsByRoutine[r.id]}
						<div class="run-history" style="margin-top: var(--sp-2)">
							{#each runsByRoutine[r.id] as run (run.id)}
								<div class="run-row">
									<div class="row align-center gap-2" style="font-size: 12px">
										<span class="pill" class:pill-ok={run.status === 'completed'} class:pill-error={run.status === 'error'} class:pill-warn={run.status === 'running'}>{run.status}</span>
										<span style="color: var(--fg-muted)">{fmtMs(run.started_ms)}</span>
									</div>
									{#if run.job_id && (run.status === 'completed' || run.status === 'error')}
										<button class="btn btn-sm btn-ghost" style="margin-top: 2px" onclick={() => loadJobOutput(r.id, run.job_id!)}>
											<FileText size={11} strokeWidth={2} /> View output
										</button>
									{/if}
								</div>
							{/each}
						</div>
						{#if expandedJobOutput && jobOutputLoading}
							<div class="output-panel" style="margin-top: var(--sp-2); padding: var(--sp-3); background: var(--bg-input); border-radius: var(--radius); font-size: 12px">
								<span class="spinner"></span> Loading output…
							</div>
						{:else if expandedJobOutput}
							<div class="output-panel" style="margin-top: var(--sp-2); padding: var(--sp-3); background: var(--bg-input); border-radius: var(--radius); font-size: 12px; white-space: pre-wrap; font-family: ui-monospace, monospace; max-height: 300px; overflow-y: auto">
								{expandedJobOutput}
							</div>
						{/if}
					{:else if expandedRoutineId === r.id && !runsByRoutine[r.id]}
						<div style="font-size: 12px; color: var(--fg-muted); margin-top: var(--sp-2)">No runs recorded yet</div>
					{/if}
				</div>
			{/each}
		</div>
	{/if}
</div>

<!-- Create drawer -->
{#if drawerOpen}
	<div class="modal-backdrop" onclick={closeDrawer} role="dialog">
		<div class="drawer-panel" onclick={(e) => e.stopPropagation()}>
			<div class="row justify-between align-center" style="margin-bottom: var(--sp-4)">
				<h3 style="margin: 0">{drawerMode === 'template' ? 'Create from template' : 'New routine'}</h3>
				<button class="btn btn-ghost btn-icon" onclick={closeDrawer}><X size={16} strokeWidth={2} /></button>
			</div>

			{#if templates.length > 0 && drawerMode === 'create'}
				<div class="card-title" style="margin-bottom: var(--sp-2)">
					<FileText size={12} strokeWidth={2} /> Use a template
				</div>
				<div class="template-grid" style="margin-bottom: var(--sp-3)">
					{#each templates.slice(0, 4) as tpl (tpl.slug)}
						<button class="template-card" onclick={() => applyTemplate(tpl)}>
							<div class="mono" style="font-weight: 600">{tpl.name}</div>
							<div style="font-size: 11px; color: var(--fg-muted)">{tpl.description}</div>
						</button>
					{/each}
				</div>
				<div class="card-title" style="margin-bottom: var(--sp-2); margin-top: var(--sp-3)">Or fill in manually</div>
			{/if}

			<div class="col" style="gap: var(--sp-3)">
				<div>
					<div class="form-label">Name</div>
					<input class="form-input" bind:value={newName} placeholder="My daily summary" />
				</div>
				<div>
					<div class="form-label">Description (optional)</div>
					<input class="form-input" bind:value={newDescription} placeholder="What this routine does" />
				</div>
				<div>
					<div class="form-label">Cron expression</div>
					<div class="row gap-2">
						<input class="form-input mono" value={newCron} oninput={onCronChange} placeholder="0 9 * * *" style="flex: 1" />
						<button class="btn btn-sm" onclick={previewCron} disabled={cronPreviewLoading}>
							<RefreshCw size={12} strokeWidth={2} /> Preview
						</button>
					</div>
					{#if cronFires.length > 0}
						<div style="margin-top: var(--sp-2); font-size: 11px; color: var(--fg-muted)">
							Next {cronFires.length} fire times:
							{#each cronFires as ts}
								<div class="mono" style="font-size: 11px">{new Date(ts).toLocaleString()}</div>
							{/each}
						</div>
					{/if}
				</div>
				<div>
					<div class="form-label">Prompt</div>
					<textarea class="form-textarea" bind:value={newPrompt} placeholder="Summarise the latest changes…" rows={4}></textarea>
				</div>
				<div>
					<div class="form-label">Model</div>
					<input class="form-input mono" bind:value={newModel} placeholder="deepseek/deepseek-v4-pro" />
				</div>
				<div class="row gap-2 justify-end">
					<button class="btn btn-ghost" onclick={closeDrawer}>Cancel</button>
					<button class="btn btn-primary" onclick={createRoutine} disabled={saving || !newName.trim()}>
						{#if saving}<span class="spinner"></span>{/if}
						Create routine
					</button>
				</div>
			</div>
		</div>
	</div>
{/if}

<!-- Confirm modal -->
{#if confirmState.open}
	<div class="modal-backdrop" onclick={() => { confirmState.resolve?.(false); confirmState = { open: false, message: '', resolve: null }; }} role="dialog">
		<div class="modal-card" onclick={(e) => e.stopPropagation()}>
			<p style="margin: 0 0 var(--sp-4)">{confirmState.message}</p>
			<div class="row gap-2 justify-end">
				<button class="btn" type="button" onclick={() => { confirmState.resolve?.(false); confirmState = { open: false, message: '', resolve: null }; }}>Cancel</button>
				<button class="btn btn-primary" type="button" onclick={() => { confirmState.resolve?.(true); confirmState = { open: false, message: '', resolve: null }; }}>OK</button>
			</div>
		</div>
	</div>
{/if}

<style>
	.run-history {
		display: flex;
		flex-direction: column;
		gap: 4px;
	}
	.run-row {
		padding: 6px 8px;
		border-radius: var(--radius-sm);
		background: var(--bg-input);
	}
	.template-grid {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: var(--sp-2);
	}
	.template-card {
		text-align: left;
		padding: var(--sp-2) var(--sp-3);
		background: var(--bg-input);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		color: var(--fg);
		cursor: pointer;
		font: inherit;
		font-size: var(--fs-13);
		transition: border-color 0.1s;
	}
	.template-card:hover { border-color: var(--accent); }
	.drawer-panel {
		position: fixed;
		right: 0;
		top: 0;
		bottom: 0;
		width: 480px;
		max-width: 100vw;
		background: var(--bg-card);
		border-left: 1px solid var(--border);
		padding: var(--sp-4);
		overflow-y: auto;
		z-index: 1001;
		animation: slide-in 0.2s ease-out;
	}
	@keyframes slide-in {
		from { transform: translateX(100%); }
		to { transform: translateX(0); }
	}
	.modal-backdrop {
		position: fixed;
		inset: 0;
		background: rgba(0, 0, 0, 0.5);
		display: flex;
		align-items: stretch;
		justify-content: flex-end;
		z-index: 1000;
	}
	.modal-card {
		background: var(--bg-card);
		border: 1px solid var(--border);
		border-radius: var(--radius-lg);
		padding: var(--sp-4);
		min-width: 300px;
		max-width: 420px;
		align-self: center;
		margin: auto;
	}
	.justify-end { justify-content: flex-end; }
</style>
