<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/api';
	import { toasts } from '$lib/stores.svelte';
	import PageHeader from '$lib/PageHeader.svelte';
	import { Search, FolderKanban } from 'lucide-svelte';

	interface DiffLine {
		sign: '+' | '-' | ' ' | '@';
		text: string;
	}

	interface FileEdit {
		id: number;
		ts: number;
		op: string;
		path: string;
		bytes_delta: number;
		project_id?: string;
		project_name?: string;
		session_id?: string;
		metadata?: string;
	}

	let edits = $state<FileEdit[]>([]);
	let projects = $state<Array<{ id: string; name: string }>>([]);
	let loading = $state(true);
	let active = $state<FileEdit | null>(null);
	let preview = $state<string>('');
	let diffLines = $state<DiffLine[]>([]);
	let previewLoading = $state(false);
	let searchQuery = $state('');

	async function load() {
		loading = true;
		try {
			const [editsData, projectsData] = await Promise.all([
				api<{ edits: FileEdit[] }>('/v1/files', { query: { limit: 500 } }),
				api<{ projects: Array<{ id: string; name: string }> }>('/v1/projects')
			]);
			edits = (editsData.edits || []);
			projects = (projectsData.projects || []);
		} catch (e) {
			toasts.show(`Load failed: ${(e as Error).message}`, 'error');
		} finally {
			loading = false;
		}
	}

	function parseUnifiedDiff(raw: string): DiffLine[] {
		const out: DiffLine[] = [];
		for (const line of raw.split('\n')) {
			if (line.startsWith('--- ') || line.startsWith('+++ ')) continue;
			if (line.startsWith('@@')) out.push({ sign: '@', text: line });
			else if (line.startsWith('+')) out.push({ sign: '+', text: line.slice(1) });
			else if (line.startsWith('-')) out.push({ sign: '-', text: line.slice(1) });
			else out.push({ sign: ' ', text: line.startsWith(' ') ? line.slice(1) : line });
		}
		// Trim trailing empty context line the splitter leaves behind.
		while (out.length && out[out.length - 1].sign === ' ' && out[out.length - 1].text === '') out.pop();
		return out;
	}

	async function openEdit(e: FileEdit) {
		active = e;
		preview = '';
		diffLines = [];
		previewLoading = true;
		try {
			// The backend already stored a unified diff in metadata.diff — use
			// it directly (no need to refetch + recompute). Fall back to a
			// plain preview only when no diff was recorded (e.g. binary write).
			let meta: Record<string, unknown> = {};
			try {
				meta = e.metadata ? JSON.parse(e.metadata) : {};
			} catch { /* ignore */ }

			const diffStr = typeof meta.diff === 'string' ? meta.diff : '';
			if (diffStr.trim()) {
				diffLines = parseUnifiedDiff(diffStr);
				previewLoading = false;
				return;
			}

			const data = await api<{ content: string }>(`/v1/preview/file`, {
				query: { path: e.path, session_id: e.session_id || '' }
			});
			preview = data.content || '';
		} catch (err) {
			preview = `(unable to load preview: ${(err as Error).message})`;
		} finally {
			previewLoading = false;
		}
	}

	function projName(pid?: string): string {
		if (!pid) return 'No project';
		const p = projects.find((p) => p.id === pid);
		return p?.name ?? 'Unknown';
	}

	function projColor(pid?: string): string {
		if (!pid) return '';
		const p = projects.find((p) => p.id === pid) as Record<string, unknown> | undefined;
		return (p?.color as string) || '';
	}

	// Group edits by project_id, then sort each group by ts desc
	let grouped = $derived.by(() => {
		type Bucket = { projectId: string | null; projectName: string; color: string; edits: FileEdit[] };
		const buckets = new Map<string | null, Bucket>();
		for (const e of filtered) {
			const pid = e.project_id || null;
			if (!buckets.has(pid)) {
				buckets.set(pid, {
					projectId: pid,
					projectName: projName(pid ?? undefined),
					color: projColor(pid ?? undefined),
					edits: []
				});
			}
			buckets.get(pid)!.edits.push(e);
		}
		// Sort: projects with edits first, no-project last
		const sorted = [...buckets.values()];
		sorted.sort((a, b) => {
			if (!a.projectId && b.projectId) return 1;
			if (a.projectId && !b.projectId) return -1;
			return a.projectName.localeCompare(b.projectName);
		});
		return sorted;
	});

	let filtered = $derived.by(() => {
		if (!searchQuery.trim()) return edits;
		const q = searchQuery.toLowerCase().trim();
		return edits.filter(
			(e) => e.path.toLowerCase().includes(q) || e.op.toLowerCase().includes(q)
		);
	});

	function fmtTime(ts: number): string {
		try { return new Date(ts * 1000).toLocaleString(); } catch { return ''; }
	}
	function fmtBytes(n: number): string {
		const sign = n > 0 ? '+' : n < 0 ? '−' : '';
		return `${sign}${Math.abs(n)}B`;
	}

	onMount(load);
</script>

<PageHeader title="File edits" sub={`${edits.length} recorded operations`} />

<div class="page-body" style="display: grid; grid-template-columns: 360px 1fr; gap: var(--sp-4); height: calc(100vh - 110px)">
	<!-- Left panel -->
	<div class="card" style="overflow-y: auto; padding: var(--sp-2); display: flex; flex-direction: column">
		<div style="margin-bottom: var(--sp-2); position: relative">
			<Search size={14} strokeWidth={2} style="position: absolute; left: 8px; top: 50%; transform: translateY(-50%); color: var(--fg-muted); pointer-events: none" />
			<input
				class="form-input"
				style="padding-left: 30px"
				placeholder="Filter by path or op…"
				bind:value={searchQuery}
			/>
		</div>

		{#if loading}
			<div class="empty"><span class="spinner"></span></div>
		{:else if edits.length === 0}
			<div class="empty">No file edits recorded yet</div>
		{:else if filtered.length === 0}
			<div class="empty">No edits match "{searchQuery}"</div>
		{:else}
			{#each grouped as bucket (bucket.projectId ?? '__none__')}
				<div class="section-label" style={bucket.color ? `border-left: 3px solid ${bucket.color}; padding-left: 8px; margin: var(--sp-2) 4px var(--sp-1); font-size: 11px; font-weight: 600; color: var(--fg-muted); text-transform: uppercase; display: flex; align-items: center; gap: 6px` : `margin: var(--sp-2) 4px var(--sp-1); font-size: 11px; font-weight: 600; color: var(--fg-muted); text-transform: uppercase; display: flex; align-items: center; gap: 6px`}>
					<FolderKanban size={12} strokeWidth={2} />
					{bucket.projectName}
				</div>
				{#each bucket.edits as e (e.id)}
					<button
						class="edit-row"
						class:active={active?.id === e.id}
						type="button"
						onclick={() => openEdit(e)}
					>
						<div class="row align-center gap-2" style="margin-bottom: 2px">
							<span class="pill" class:pill-accent={e.op === 'edit'} class:pill-ok={e.op === 'write' || e.op === 'create'} class:pill-error={e.op === 'delete'}>{e.op}</span>
							<span class="mono" style="font-size: 11px; color: var(--fg-muted)">{fmtBytes(e.bytes_delta)}</span>
						</div>
						<div class="mono truncate" style="font-size: 12px">{e.path}</div>
						<div style="font-size: 11px; color: var(--fg-muted)">{fmtTime(e.ts)}</div>
					</button>
				{/each}
			{/each}
		{/if}
	</div>

	<!-- Right panel -->
	<div class="card" style="overflow: hidden; display: flex; flex-direction: column">
		{#if active}
			<div class="card-title mono" style="margin-bottom: var(--sp-2)">{active.path}</div>
			{#if previewLoading}
				<div class="empty"><span class="spinner"></span> Loading preview…</div>
			{:else if diffLines.length > 0}
				<div class="diff-viewer">
					{#each diffLines as line, i (i)}
						<div
							class="diff-line"
							class:diff-add={line.sign === '+'}
							class:diff-rem={line.sign === '-'}
							class:diff-hunk={line.sign === '@'}
						>
							<span class="diff-sign">{line.sign === '@' ? '' : line.sign}</span>
							<span class="diff-text">{line.text}</span>
						</div>
					{/each}
				</div>
			{:else}
				<pre style="font-family: ui-monospace, monospace; font-size: 12px; white-space: pre-wrap; word-break: break-all; margin: 0; overflow: auto; flex: 1">{preview}</pre>
			{/if}
		{:else}
			<div class="empty">Select an edit to view file contents</div>
		{/if}
	</div>
</div>

<style>
	.edit-row {
		display: block;
		width: 100%;
		text-align: left;
		background: transparent;
		border: none;
		color: var(--fg);
		padding: 8px;
		border-radius: var(--radius);
		cursor: pointer;
		margin-bottom: 4px;
	}
	.edit-row:hover { background: var(--bg-hover); }
	.edit-row.active { background: var(--bg-active); }
	.diff-viewer {
		overflow: auto;
		flex: 1;
		font-family: ui-monospace, monospace;
		font-size: 12px;
		line-height: 1.55;
	}
	.diff-line {
		display: flex;
		white-space: pre-wrap;
		word-break: break-all;
		padding: 0 4px;
	}
	.diff-sign {
		width: 16px;
		display: inline-block;
		text-align: center;
		color: var(--fg-dim);
		user-select: none;
		flex-shrink: 0;
	}
	.diff-text {
		flex: 1;
	}
	.diff-add {
		background: rgba(34, 197, 94, 0.12);
	}
	.diff-add .diff-sign {
		color: rgb(34, 197, 94);
	}
	.diff-rem {
		background: rgba(239, 68, 68, 0.12);
	}
	.diff-rem .diff-sign {
		color: rgb(239, 68, 68);
	}
	.diff-hunk {
		background: var(--bg-input);
		color: var(--fg-muted);
		margin: 4px 0;
		font-size: 11px;
	}
</style>
