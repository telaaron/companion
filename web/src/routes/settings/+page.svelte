<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/api';
	import { toasts } from '$lib/stores.svelte';
	import type { SettingsSnapshot } from '$lib/types';
	import PageHeader from '$lib/PageHeader.svelte';
	import { Pencil, Wand2, X, Check } from 'lucide-svelte';
	import EditableRow from './EditableRow.svelte';

	let data = $state<SettingsSnapshot | null>(null);
	let loading = $state(true);
	let error = $state<string | null>(null);

	async function load() {
		loading = true;
		error = null;
		try {
			data = await api<SettingsSnapshot>('/v1/settings');
		} catch (e) {
			error = (e as Error).message;
			toasts.show(`Settings load failed: ${error}`, 'error');
		} finally {
			loading = false;
		}
	}

	onMount(load);

	function makeRows(d: SettingsSnapshot) {
		return {
			agent: [
				{ label: 'enabled', value: String(d.agent_mode.enabled), envKey: 'AGENT_MODE_ENABLED' },
				{ label: 'workspace', value: d.agent_mode.workspace ?? '', envKey: 'AGENT_DEFAULT_WORKSPACE' },
				{ label: 'max_turns', value: String(d.agent_mode.max_turns), envKey: 'AGENT_MAX_TURNS', type: 'number' as const },
				{ label: 'tool_call_limit_per_min', value: String(d.agent_mode.tool_call_limit_per_min), envKey: 'AGENT_TOOL_CALL_LIMIT_PER_MIN', type: 'number' as const },
				{ label: 'global_tool_call_limit_per_min', value: String(d.agent_mode.global_tool_call_limit_per_min), envKey: 'AGENT_GLOBAL_TOOL_CALL_LIMIT_PER_MIN', type: 'number' as const },
				{ label: 'bash_denylist', value: d.agent_mode.bash_denylist ?? '', envKey: 'AGENT_BASH_DENYLIST' },
				{ label: 'bash_extra_env', value: d.agent_mode.bash_extra_env ?? '', envKey: 'AGENT_BASH_EXTRA_ENV' }
			],
			models: [
				{ label: 'MODEL', value: d.model ?? '', envKey: 'MODEL' },
				{ label: 'MODEL_OPUS', value: d.model_opus ?? '', envKey: 'MODEL_OPUS' },
				{ label: 'MODEL_SONNET', value: d.model_sonnet ?? '', envKey: 'MODEL_SONNET' },
				{ label: 'MODEL_HAIKU', value: d.model_haiku ?? '', envKey: 'MODEL_HAIKU' },
				{ label: 'MODEL_SUBAGENT', value: d.model_subagent ?? '', envKey: 'MODEL_SUBAGENT' },
				{ label: 'MODEL_FALLBACK_CHAIN', value: d.model_fallback_chain ?? '', envKey: 'MODEL_FALLBACK_CHAIN' }
			],
			thinking: [
				{ label: 'default_enabled', value: String(d.thinking.default_enabled), envKey: 'ENABLE_MODEL_THINKING' },
				{ label: 'budget_max', value: String(d.thinking.budget_max), envKey: 'THINKING_BUDGET_MAX', type: 'number' as const }
			],
			imageGen: [
				{ label: 'provider', value: d.image_gen.provider ?? '', envKey: 'IMAGE_GEN_PROVIDER' },
				{ label: 'model', value: d.image_gen.model ?? '', envKey: 'IMAGE_GEN_MODEL' }
			],
			vision: [
				{ label: 'provider', value: d.deepseek_image_fallback.provider ?? '', envKey: 'DEEPSEEK_IMAGE_FALLBACK_PROVIDER' },
				{ label: 'model', value: d.deepseek_image_fallback.model ?? '', envKey: 'DEEPSEEK_IMAGE_FALLBACK_MODEL' }
			],
			tokens: [
				{ label: 'GITHUB_TOKEN', value: '', envKey: 'GITHUB_TOKEN', secret: true },
				{ label: 'VERCEL_TOKEN', value: '', envKey: 'VERCEL_TOKEN', secret: true },
				{ label: 'SUPABASE_URL', value: '', envKey: 'SUPABASE_URL' },
				{ label: 'SUPABASE_SERVICE_ROLE_KEY', value: '', envKey: 'SUPABASE_SERVICE_ROLE_KEY', secret: true },
				{ label: 'OPENAI_API_KEY', value: '', envKey: 'OPENAI_API_KEY', secret: true }
			]
		};
	}

	async function onSaveRow(envKey: string, value: string) {
		try {
			await api('/v1/env', { method: 'PUT', body: { key: envKey, value } });
			toasts.show(`Saved ${envKey}`, 'ok');
			await load();
		} catch (e) {
			toasts.show(`Save failed: ${(e as Error).message}`, 'error');
			throw e;
		}
	}
</script>

<PageHeader title="Settings" sub="non-sensitive snapshot of the running proxy">
	{#snippet actions()}
		<button class="btn btn-primary" type="button">
			<Wand2 size={14} strokeWidth={2} /> Setup wizard
		</button>
	{/snippet}
</PageHeader>

<div class="page-body">
	{#if loading && !data}
		<div class="empty"><span class="spinner"></span> Loading settings…</div>
	{:else if error && !data}
		<div class="empty">{error}</div>
	{:else if data}
		{@const rows = makeRows(data)}
		<div class="grid-cards">
			<div class="card">
				<div class="card-title">Agent mode</div>
				<table class="table">
					<tbody>
						{#each rows.agent as r (r.envKey)}
							<EditableRow row={r} onSave={onSaveRow} />
						{/each}
					</tbody>
				</table>
			</div>

			<div class="card">
				<div class="card-title">Models</div>
				<table class="table">
					<tbody>
						{#each rows.models as r (r.envKey)}
							<EditableRow row={r} onSave={onSaveRow} />
						{/each}
					</tbody>
				</table>
			</div>

			<div class="card">
				<div class="card-title">Thinking</div>
				<table class="table">
					<tbody>
						{#each rows.thinking as r (r.envKey)}
							<EditableRow row={r} onSave={onSaveRow} />
						{/each}
					</tbody>
				</table>
			</div>

			<div class="card">
				<div class="card-title">Image gen</div>
				<table class="table">
					<tbody>
						{#each rows.imageGen as r (r.envKey)}
							<EditableRow row={r} onSave={onSaveRow} />
						{/each}
					</tbody>
				</table>
			</div>

			<div class="card">
				<div class="card-title">Vision fallback</div>
				<table class="table">
					<tbody>
						{#each rows.vision as r (r.envKey)}
							<EditableRow row={r} onSave={onSaveRow} />
						{/each}
					</tbody>
				</table>
			</div>

			<div class="card">
				<div class="card-title">API tokens (cloud)</div>
				<table class="table">
					<tbody>
						{#each rows.tokens as r (r.envKey)}
							<EditableRow row={r} onSave={onSaveRow} />
						{/each}
					</tbody>
				</table>
			</div>

			<div class="card">
				<div class="card-title">Server (restart required)</div>
				<table class="table">
					<tbody>
						<tr><td class="mono fg-muted">host</td><td class="mono">{data.host}</td></tr>
						<tr><td class="mono fg-muted">port</td><td class="mono">{data.port}</td></tr>
						<tr><td class="mono fg-muted">auth_set</td><td class="mono">{String(data.anthropic_auth_token_set)}</td></tr>
						<tr><td class="mono fg-muted">env_file</td><td class="mono truncate" title={data.env_file}>{data.env_file}</td></tr>
						<tr><td class="mono fg-muted">pid</td><td class="mono">{data.process.pid}</td></tr>
						<tr><td class="mono fg-muted">uptime_s</td><td class="mono">{data.process.uptime_s}</td></tr>
					</tbody>
				</table>
			</div>
		</div>
	{/if}
</div>

<style>
	.fg-muted {
		color: var(--fg-muted);
		width: 40%;
	}
</style>
