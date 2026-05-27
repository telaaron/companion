<script lang="ts">
	import { Pencil, X, Check } from 'lucide-svelte';

	interface Row {
		label: string;
		value: string;
		envKey: string;
		type?: 'text' | 'number';
		secret?: boolean;
	}

	interface Props {
		row: Row;
		onSave: (envKey: string, value: string) => Promise<void>;
	}

	let { row, onSave }: Props = $props();

	let editing = $state(false);
	let buffer = $state('');
	let saving = $state(false);
	let inputEl = $state<HTMLInputElement | undefined>(undefined);

	$effect(() => {
		if (editing && inputEl) inputEl.focus();
	});

	function display(): string {
		if (row.secret && row.value) return '•'.repeat(8);
		if (!row.value) return '—';
		return row.value;
	}

	function begin() {
		buffer = row.value;
		editing = true;
	}

	function cancel() {
		editing = false;
		buffer = '';
	}

	async function save() {
		saving = true;
		try {
			await onSave(row.envKey, buffer);
			editing = false;
		} catch {
			/* toast handled upstream */
		} finally {
			saving = false;
		}
	}

	function onKey(e: KeyboardEvent) {
		if (e.key === 'Enter') save();
		if (e.key === 'Escape') cancel();
	}
</script>

<tr>
	<td class="mono fg-muted">{row.label}</td>
	<td class="mono">
		{#if editing}
			<div class="row gap-2 align-center">
				<input
					bind:this={inputEl}
					class="form-input"
					type={row.secret ? 'password' : row.type === 'number' ? 'number' : 'text'}
					bind:value={buffer}
					onkeydown={onKey}
					style="flex: 1; padding: 4px 8px; font-size: 13px;"
				/>
				<button class="btn btn-primary btn-sm" type="button" disabled={saving} onclick={save}>
					<Check size={12} strokeWidth={2.5} />
				</button>
				<button class="btn btn-ghost btn-sm" type="button" onclick={cancel}>
					<X size={12} strokeWidth={2.5} />
				</button>
			</div>
		{:else}
			<div class="row gap-2 align-center">
				<span class="truncate" style="flex: 1" title={row.value}>{display()}</span>
				<button class="btn btn-ghost btn-icon" type="button" title="Edit" onclick={begin}>
					<Pencil size={14} strokeWidth={2} />
				</button>
			</div>
		{/if}
	</td>
</tr>

<style>
	.fg-muted {
		color: var(--fg-muted);
		width: 40%;
	}
</style>
