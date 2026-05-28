<script lang="ts">
	import { api } from '$lib/api';
	import { toasts } from '$lib/stores.svelte';
	import { Wand2, ChevronLeft, ChevronRight, Check, X, ArrowRight } from 'lucide-svelte';

	let { open = $bindable(false) } = $props();

	const STEPS = [
		'Welcome',
		'Provider',
		'API Key',
		'Models',
		'Workspace',
		'Voice',
		'Review'
	] as const;

	type Provider = 'deepseek' | 'openrouter' | 'anthropic' | 'ollama' | 'lmstudio' | 'llamacpp' | 'custom';

	interface Preset {
		key: Provider;
		label: string;
		desc: string;
		apiKeyEnv: string;
		needsApiKey: boolean;
		defaultModel: string;
		opusModel: string;
		sonnetModel: string;
		haikuModel: string;
		baseUrlEnv?: string;
		baseUrlDefault?: string;
	}

	const PRESETS: Preset[] = [
		{
			key: 'deepseek',
			label: 'DeepSeek',
			desc: 'Best cost/quality ratio. Vision + thinking support.',
			apiKeyEnv: 'DEEPSEEK_API_KEY',
			needsApiKey: true,
			defaultModel: 'deepseek/deepseek-v4-pro',
			opusModel: 'deepseek/deepseek-v4-pro',
			sonnetModel: 'deepseek/deepseek-v4-flash',
			haikuModel: 'deepseek/deepseek-v4-flash'
		},
		{
			key: 'openrouter',
			label: 'OpenRouter',
			desc: 'Multi-provider gateway. Access Claude, Gemini, and more.',
			apiKeyEnv: 'OPENROUTER_API_KEY',
			needsApiKey: true,
			defaultModel: 'open_router/anthropic/claude-sonnet-4',
			opusModel: 'open_router/anthropic/claude-opus-4',
			sonnetModel: 'open_router/anthropic/claude-sonnet-4',
			haikuModel: 'open_router/anthropic/claude-haiku-4'
		},
		{
			key: 'anthropic',
			label: 'Anthropic (direct)',
			desc: 'Direct Claude API. Requires paid plan.',
			apiKeyEnv: 'ANTHROPIC_AUTH_TOKEN',
			needsApiKey: true,
			defaultModel: 'anthropic/claude-sonnet-4-20250514',
			opusModel: 'anthropic/claude-opus-4-20250514',
			sonnetModel: 'anthropic/claude-sonnet-4-20250514',
			haikuModel: 'anthropic/claude-haiku-3-5-20241022'
		},
		{
			key: 'ollama',
			label: 'Ollama (local)',
			desc: 'Run models locally via Ollama. Free, no API key.',
			apiKeyEnv: '',
			needsApiKey: false,
			defaultModel: 'ollama/llama3',
			opusModel: 'ollama/llama3:70b',
			sonnetModel: 'ollama/llama3',
			haikuModel: 'ollama/llama3',
			baseUrlEnv: 'OLLAMA_BASE_URL',
			baseUrlDefault: 'http://localhost:11434'
		},
		{
			key: 'lmstudio',
			label: 'LM Studio (local)',
			desc: 'Run models locally via LM Studio. Free, no API key.',
			apiKeyEnv: '',
			needsApiKey: false,
			defaultModel: 'lmstudio/default',
			opusModel: 'lmstudio/default',
			sonnetModel: 'lmstudio/default',
			haikuModel: 'lmstudio/default',
			baseUrlEnv: 'LM_STUDIO_BASE_URL',
			baseUrlDefault: 'http://localhost:1234/v1'
		},
		{
			key: 'custom',
			label: 'Custom provider',
			desc: 'Bring your own provider/model strings.',
			apiKeyEnv: 'ANTHROPIC_AUTH_TOKEN',
			needsApiKey: false,
			defaultModel: '',
			opusModel: '',
			sonnetModel: '',
			haikuModel: ''
		}
	];

	let stepIdx = $state(0);
	let saving = $state(false);

	// Form state
	let selectedProvider = $state<Preset>(PRESETS[0]);
	let providerRadio = $state<Provider>('deepseek');
	let apiKey = $state('');
	let defaultModel = $state(PRESETS[0].defaultModel);
	let opusModel = $state(PRESETS[0].opusModel);
	let sonnetModel = $state(PRESETS[0].sonnetModel);
	let haikuModel = $state(PRESETS[0].haikuModel);
	let workspace = $state('');
	let voiceEnabled = $state(false);
	let whisperBinary = $state('');
	let whisperModel = $state('base');
	let localBaseUrl = $state('');

	function selectProvider(key: Provider) {
		providerRadio = key;
		const p = PRESETS.find((x) => x.key === key) || PRESETS[0];
		selectedProvider = p;
		defaultModel = p.defaultModel;
		opusModel = p.opusModel;
		sonnetModel = p.sonnetModel;
		haikuModel = p.haikuModel;
		localBaseUrl = p.baseUrlDefault || '';
	}

	function prev() {
		stepIdx = Math.max(0, stepIdx - 1);
	}

	function next() {
		// Skip API key step if provider doesn't need one
		if (stepIdx === 2 && !selectedProvider.needsApiKey) {
			stepIdx = Math.min(STEPS.length - 1, stepIdx + 2);
		} else {
			stepIdx = Math.min(STEPS.length - 1, stepIdx + 1);
		}
	}

	function reset() {
		stepIdx = 0;
		selectedProvider = PRESETS[0];
		providerRadio = 'deepseek';
		apiKey = '';
		defaultModel = PRESETS[0].defaultModel;
		opusModel = PRESETS[0].opusModel;
		sonnetModel = PRESETS[0].sonnetModel;
		haikuModel = PRESETS[0].haikuModel;
		workspace = '';
		voiceEnabled = false;
		whisperBinary = '';
		whisperModel = 'base';
		localBaseUrl = '';
	}

	function closeWizard() {
		open = false;
		reset();
	}

	async function saveAll() {
		saving = true;
		const pairs: [string, string][] = [];

		// API key
		if (selectedProvider.needsApiKey && apiKey && selectedProvider.apiKeyEnv) {
			pairs.push([selectedProvider.apiKeyEnv, apiKey]);
		}

		// Models
		pairs.push(['MODEL', defaultModel]);
		if (opusModel && opusModel !== defaultModel) pairs.push(['MODEL_OPUS', opusModel]);
		if (sonnetModel && sonnetModel !== defaultModel) pairs.push(['MODEL_SONNET', sonnetModel]);
		if (haikuModel && haikuModel !== defaultModel) pairs.push(['MODEL_HAIKU', haikuModel]);

		// Workspace
		if (workspace) pairs.push(['AGENT_DEFAULT_WORKSPACE', workspace]);
		pairs.push(['AGENT_MODE_ENABLED', 'true']);

		// Voice
		pairs.push(['VOICE_NOTE_ENABLED', voiceEnabled ? 'true' : 'false']);
		if (whisperBinary) pairs.push(['WHISPER_BINARY', whisperBinary]);
		if (whisperModel) pairs.push(['WHISPER_MODEL', whisperModel]);

		// Local base URL
		if (selectedProvider.baseUrlEnv && localBaseUrl) {
			pairs.push([selectedProvider.baseUrlEnv, localBaseUrl]);
		}

		let failed = false;
		for (const [key, value] of pairs) {
			try {
				await api('/v1/env', { method: 'PUT', body: { key, value } });
			} catch (e) {
				toasts.show(`Failed to save ${key}: ${(e as Error).message}`, 'error');
				failed = true;
			}
		}

		saving = false;
		if (!failed) {
			toasts.show(`${pairs.length} settings saved. Restart the server to apply changes.`, 'ok');
			closeWizard();
		}
	}
</script>

{#if open}
	<div class="wizard-backdrop" onclick={closeWizard}>
		<div class="wizard" onclick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">
			<!-- Header -->
			<div class="wizard-header">
				<div class="wizard-title-row">
					<Wand2 size={18} strokeWidth={1.5} />
					<h2>Setup Wizard</h2>
					<button class="btn-close" onclick={closeWizard} type="button" aria-label="Close"><X size={14} /></button>
				</div>
				<div class="step-indicator">
					{#each STEPS as label, i}
						{@const done = i < stepIdx}
						{@const current = i === stepIdx}
						<div class="step-dot" class:done class:current>
							{#if done}
								<Check size={10} />
							{:else}
								<span class="step-num">{i + 1}</span>
							{/if}
						</div>
						{#if i < STEPS.length - 1}
							<div class="step-line" class:filled={i < stepIdx}></div>
						{/if}
					{/each}
				</div>
				<div class="step-labels">
					{#each STEPS as label, i}
						<span class="step-label" class:active={i === stepIdx}>{label}</span>
					{/each}
				</div>
			</div>

			<!-- Body -->
			<div class="wizard-body">
				{#if stepIdx === 0}
					<!-- Welcome -->
					<h3>Welcome to Companion</h3>
					<p class="wizard-desc">This wizard will help you configure Companion in a few quick steps.
						You'll pick a provider, set up your API key, choose models, and configure
						your workspace.</p>
					<div class="wizard-cards">
						<div class="wizard-card">
							<strong>Provider</strong>
							<span>DeepSeek, OpenRouter, Anthropic, or local models</span>
						</div>
						<div class="wizard-card">
							<strong>API Key</strong>
							<span>One key to unlock your chosen provider</span>
						</div>
						<div class="wizard-card">
							<strong>Models</strong>
							<span>Configure which models to use for each tier</span>
						</div>
						<div class="wizard-card">
							<strong>Workspace</strong>
							<span>Where Companion can read and write files</span>
						</div>
					</div>

				{:else if stepIdx === 1}
					<!-- Provider selection -->
					<h3>Choose your provider</h3>
					<p class="wizard-desc">Select the AI provider you want to use with Companion.</p>
					<div class="provider-list">
						{#each PRESETS as p (p.key)}
							<button
								type="button"
								class="provider-option"
								class:selected={providerRadio === p.key}
								onclick={() => selectProvider(p.key)}
							>
								<div class="provider-radio">
									<div class="radio-dot" class:checked={providerRadio === p.key}></div>
								</div>
								<div class="provider-info">
									<strong>{p.label}</strong>
									<span>{p.desc}</span>
								</div>
							</button>
						{/each}
					</div>

				{:else if stepIdx === 2 && selectedProvider.needsApiKey}
					<!-- API Key -->
					<h3>Enter your API key</h3>
					<p class="wizard-desc">
						Paste your <strong>{selectedProvider.label}</strong> API key. It will be stored in
						<code>~/.config/companion/.env</code> on your machine.
					</p>
					<div class="form-group">
						<label class="form-label" for="wiz-apikey">{selectedProvider.apiKeyEnv}</label>
						<input
							id="wiz-apikey"
							type="password"
							class="form-input"
							bind:value={apiKey}
							placeholder="sk-..."
							autocomplete="off"
						/>
						<span class="form-hint">
							Get a key from
							{#if selectedProvider.key === 'deepseek'}
								<a href="https://platform.deepseek.com" target="_blank" rel="noopener">platform.deepseek.com</a>
							{:else if selectedProvider.key === 'openrouter'}
								<a href="https://openrouter.ai/keys" target="_blank" rel="noopener">openrouter.ai/keys</a>
							{:else if selectedProvider.key === 'anthropic'}
								<a href="https://console.anthropic.com" target="_blank" rel="noopener">console.anthropic.com</a>
							{/if}
						</span>
					</div>

				{:else if stepIdx === 3 || (stepIdx === 2 && !selectedProvider.needsApiKey)}
					<!-- Models -->
					<h3>Configure models</h3>
					<p class="wizard-desc">
						Select which models to use for each capability tier.
						Provider path format: <code>{selectedProvider.key}/model-name</code>
					</p>
					<div class="form-group">
						<label class="form-label" for="wiz-model">MODEL (default)</label>
						<input id="wiz-model" class="form-input mono" bind:value={defaultModel} placeholder="{selectedProvider.key}/model" />
						<span class="form-hint">Used when no tier-specific model is set</span>
					</div>
					<div class="form-row-3">
						<div class="form-group">
							<label class="form-label" for="wiz-opus">MODEL_OPUS</label>
							<input id="wiz-opus" class="form-input mono" bind:value={opusModel} />
						</div>
						<div class="form-group">
							<label class="form-label" for="wiz-sonnet">MODEL_SONNET</label>
							<input id="wiz-sonnet" class="form-input mono" bind:value={sonnetModel} />
						</div>
						<div class="form-group">
							<label class="form-label" for="wiz-haiku">MODEL_HAIKU</label>
							<input id="wiz-haiku" class="form-input mono" bind:value={haikuModel} />
						</div>
					</div>
					{#if selectedProvider.baseUrlEnv}
						<div class="form-group">
							<label class="form-label" for="wiz-url">{selectedProvider.baseUrlEnv}</label>
							<input id="wiz-url" class="form-input mono" bind:value={localBaseUrl} placeholder={selectedProvider.baseUrlDefault || ''} />
						</div>
					{/if}

				{:else if stepIdx === 4}
					<!-- Workspace -->
					<h3>Set your workspace</h3>
					<p class="wizard-desc">
						The workspace is the root directory where Companion can read and write files.
						For safety, file operations are scoped to this directory.
					</p>
					<div class="form-group">
						<label class="form-label" for="wiz-ws">AGENT_DEFAULT_WORKSPACE</label>
						<input
							id="wiz-ws"
							class="form-input"
							bind:value={workspace}
							placeholder="/Users/yourname/projects"
						/>
						<span class="form-hint">Can be changed later in Settings → Agent mode → workspace</span>
					</div>

				{:else if stepIdx === 5}
					<!-- Voice -->
					<h3>Voice input (optional)</h3>
					<p class="wizard-desc">
						Enable voice-to-text for chat input. Requires Whisper to be installed on your machine.
					</p>
					<label class="toggle-row">
						<span>Enable voice input</span>
						<input type="checkbox" bind:checked={voiceEnabled} />
					</label>
					{#if voiceEnabled}
						<div class="form-group">
							<label class="form-label" for="wiz-whisper">WHISPER_BINARY (path)</label>
							<input
								id="wiz-whisper"
								class="form-input mono"
								bind:value={whisperBinary}
								placeholder="/usr/local/bin/whisper"
							/>
						</div>
						<div class="form-group">
							<label class="form-label" for="wiz-whisper-model">WHISPER_MODEL</label>
							<select id="wiz-whisper-model" class="form-select" bind:value={whisperModel}>
								<option value="tiny">tiny</option>
								<option value="base">base</option>
								<option value="small">small</option>
								<option value="medium">medium</option>
								<option value="large">large</option>
							</select>
						</div>
					{/if}

				{:else if stepIdx === 6}
					<!-- Review -->
					<h3>Review &amp; save</h3>
					<p class="wizard-desc">These settings will be written to <code>~/.config/companion/.env</code></p>
					<div class="review-table">
						<div class="review-row"><span class="review-label">Provider</span><span>{selectedProvider.label}</span></div>
						{#if selectedProvider.needsApiKey}
							<div class="review-row"><span class="review-label">{selectedProvider.apiKeyEnv}</span><span class="mono">{apiKey ? '••••••••' + apiKey.slice(-4) : '(not set)'}</span></div>
						{/if}
						<div class="review-row"><span class="review-label">MODEL</span><span class="mono">{defaultModel || '(not set)'}</span></div>
						<div class="review-row"><span class="review-label">MODEL_OPUS</span><span class="mono">{opusModel || '(not set)'}</span></div>
						<div class="review-row"><span class="review-label">MODEL_SONNET</span><span class="mono">{sonnetModel || '(not set)'}</span></div>
						<div class="review-row"><span class="review-label">MODEL_HAIKU</span><span class="mono">{haikuModel || '(not set)'}</span></div>
						{#if selectedProvider.baseUrlEnv}
							<div class="review-row"><span class="review-label">{selectedProvider.baseUrlEnv}</span><span class="mono">{localBaseUrl || '(not set)'}</span></div>
						{/if}
						<div class="review-row"><span class="review-label">Workspace</span><span class="mono">{workspace || '(not set)'}</span></div>
						<div class="review-row"><span class="review-label">Voice</span><span>{voiceEnabled ? 'enabled' : 'disabled'}</span></div>
					</div>
				{/if}
			</div>

			<!-- Footer -->
			<div class="wizard-footer">
				<div class="footer-left">
					{#if stepIdx > 0}
						<button class="btn" type="button" onclick={prev}>
							<ChevronLeft size={14} /> Back
						</button>
					{/if}
				</div>
				<div class="footer-right">
					{#if stepIdx < STEPS.length - 1}
						<button class="btn btn-primary" type="button" onclick={next}>
							Next <ArrowRight size={14} />
						</button>
					{:else}
						<button class="btn btn-primary" type="button" onclick={saveAll} disabled={saving}>
							{#if saving}
								<span class="spinner"></span> Saving…
							{:else}
								Save settings
							{/if}
						</button>
					{/if}
				</div>
			</div>
		</div>
	</div>
{/if}

<style>
	.wizard-backdrop {
		position: fixed;
		inset: 0;
		z-index: 1001;
		background: rgba(0, 0, 0, 0.55);
		backdrop-filter: blur(4px);
		display: flex;
		align-items: center;
		justify-content: center;
	}

	.wizard {
		width: 580px;
		max-width: calc(100vw - 32px);
		max-height: 85vh;
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
		from { opacity: 0; transform: translateY(-8px) scale(0.98); }
		to { opacity: 1; transform: translateY(0) scale(1); }
	}

	.wizard-header {
		padding: var(--sp-5) var(--sp-5) var(--sp-3);
		border-bottom: 1px solid var(--border);
	}

	.wizard-title-row {
		display: flex;
		align-items: center;
		gap: var(--sp-3);
		margin-bottom: var(--sp-4);
	}

	.wizard-title-row h2 {
		margin: 0;
		font-size: var(--fs-18);
		font-weight: 600;
		flex: 1;
	}

	.btn-close {
		background: transparent;
		border: none;
		color: var(--fg-muted);
		cursor: pointer;
		padding: 4px;
		border-radius: var(--radius-sm);
		display: flex;
		align-items: center;
	}
	.btn-close:hover { color: var(--fg); background: var(--bg-hover); }

	.step-indicator {
		display: flex;
		align-items: center;
		justify-content: center;
		gap: 0;
	}

	.step-dot {
		width: 22px;
		height: 22px;
		border-radius: 50%;
		border: 2px solid var(--border-strong);
		display: flex;
		align-items: center;
		justify-content: center;
		font-size: 11px;
		font-weight: 600;
		color: var(--fg-muted);
		background: var(--bg-card);
		flex-shrink: 0;
	}

	.step-dot.done {
		border-color: var(--ok);
		background: var(--ok);
		color: #000;
	}

	.step-dot.current {
		border-color: var(--accent);
		color: var(--accent);
	}

	.step-num {
		font-size: 10px;
	}

	.step-line {
		width: 40px;
		height: 2px;
		background: var(--border);
		margin: 0 -2px;
	}

	.step-line.filled {
		background: var(--ok);
	}

	.step-labels {
		display: flex;
		justify-content: space-between;
		margin-top: var(--sp-1);
	}

	.step-label {
		font-size: 10px;
		color: var(--fg-dim);
		text-align: center;
		width: 0;
		overflow: visible;
		white-space: nowrap;
	}
	.step-label.active { color: var(--accent); font-weight: 500; }

	.wizard-body {
		flex: 1;
		overflow-y: auto;
		padding: var(--sp-5);
	}

	.wizard-body h3 {
		margin: 0 0 var(--sp-1);
		font-size: var(--fs-16);
	}

	.wizard-desc {
		margin: 0 0 var(--sp-4);
		color: var(--fg-muted);
		font-size: var(--fs-13);
		line-height: 1.6;
	}

	.wizard-desc code {
		background: var(--bg-input);
		padding: 1px 4px;
		border-radius: 3px;
		font-size: 12px;
	}

	.wizard-cards {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: var(--sp-3);
	}

	.wizard-card {
		background: var(--bg-elev);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: var(--sp-3) var(--sp-4);
	}

	.wizard-card strong {
		display: block;
		font-size: var(--fs-13);
		margin-bottom: 2px;
	}

	.wizard-card span {
		font-size: var(--fs-12);
		color: var(--fg-muted);
	}

	.provider-list {
		display: flex;
		flex-direction: column;
		gap: var(--sp-2);
	}

	.provider-option {
		display: flex;
		align-items: flex-start;
		gap: var(--sp-3);
		padding: var(--sp-3) var(--sp-4);
		background: var(--bg-elev);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		cursor: pointer;
		color: inherit;
		text-align: left;
		width: 100%;
		transition: border-color 0.1s, background 0.1s;
	}

	.provider-option:hover {
		border-color: var(--border-strong);
	}

	.provider-option.selected {
		border-color: var(--accent);
		background: rgba(99, 102, 241, 0.08);
	}

	.provider-radio {
		flex-shrink: 0;
		width: 18px;
		height: 18px;
		border-radius: 50%;
		border: 2px solid var(--border-strong);
		display: flex;
		align-items: center;
		justify-content: center;
		margin-top: 2px;
	}

	.radio-dot {
		width: 8px;
		height: 8px;
		border-radius: 50%;
		background: transparent;
		transition: background 0.1s;
	}

	.radio-dot.checked {
		background: var(--accent);
	}

	.provider-info {
		display: flex;
		flex-direction: column;
		gap: 2px;
	}

	.provider-info strong {
		font-size: var(--fs-14);
	}

	.provider-info span {
		font-size: var(--fs-12);
		color: var(--fg-muted);
	}

	.form-group {
		margin-bottom: var(--sp-3);
	}

	.form-row-3 {
		display: grid;
		grid-template-columns: 1fr 1fr 1fr;
		gap: var(--sp-3);
	}

	.form-hint {
		font-size: var(--fs-12);
		color: var(--fg-dim);
		margin-top: 4px;
		display: block;
	}

	.form-hint a {
		color: var(--accent);
	}

	.toggle-row {
		display: flex;
		align-items: center;
		justify-content: space-between;
		padding: var(--sp-3) 0;
		font-size: var(--fs-14);
		cursor: pointer;
	}

	.toggle-row input[type='checkbox'] {
		width: 18px;
		height: 18px;
		accent-color: var(--accent);
	}

	.review-table {
		background: var(--bg-elev);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		overflow: hidden;
	}

	.review-row {
		display: flex;
		justify-content: space-between;
		align-items: center;
		padding: 8px 12px;
		font-size: var(--fs-13);
		border-bottom: 1px solid var(--border);
	}

	.review-row:last-child {
		border-bottom: none;
	}

	.review-label {
		color: var(--fg-muted);
		flex-shrink: 0;
	}

	.wizard-footer {
		display: flex;
		align-items: center;
		justify-content: space-between;
		padding: var(--sp-3) var(--sp-5);
		border-top: 1px solid var(--border);
	}

	.footer-left, .footer-right {
		display: flex;
		gap: var(--sp-2);
	}
</style>
