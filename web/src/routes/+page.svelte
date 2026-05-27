<script lang="ts">
	import { onMount, tick } from 'svelte';
	import { api, sseStream } from '$lib/api';
	import { toasts } from '$lib/stores.svelte';
	import type { Session, Message, Project } from '$lib/types';
	import PageHeader from '$lib/PageHeader.svelte';
	import Markdown from '$lib/Markdown.svelte';
	import { Plus, Send, Mic, MicOff, Trash2, Loader2, RotateCcw, Copy, ChevronDown } from 'lucide-svelte';

	let sessions = $state<Session[]>([]);
	let projects = $state<Project[]>([]);
	let upstreamModels = $state<string[]>([]);
	let activeSessionId = $state<string | null>(null);
	let activeSession = $state<(Session & { messages?: Message[]; project_id?: string }) | null>(null);
	let messages = $state<Message[]>([]);
	let composer = $state('');
	let streaming = $state(false);
	let streamBuffer = $state('');
	let thinkingBuffer = $state('');
	let showThinking = $state(false);
	let messagesEl: HTMLDivElement | undefined = $state();
	let abortCtl: AbortController | null = null;
	let voiceAvailable = $state(false);
	let voiceRecording = $state(false);
	let defaultModel = $state('deepseek/deepseek-v4-pro');
	let mediaRecorder: MediaRecorder | null = null;
	let chunks: Blob[] = [];

	async function loadSessions() {
		try {
			const data = await api<{ sessions: Session[] }>('/v1/sessions');
			sessions = data.sessions || [];
			if (!activeSessionId && sessions.length > 0) {
				await selectSession(sessions[0].id);
			}
		} catch (e) {
			toasts.show(`Sessions load failed: ${(e as Error).message}`, 'error');
		}
	}

	async function loadProjects() {
		try {
			const data = await api<{ projects: Project[] }>('/v1/projects');
			projects = data.projects || [];
		} catch {
			/* optional */
		}
	}

	async function loadUpstreamModels() {
		try {
			const data = await api<{
				providers?: Array<{ provider: string; models?: Array<{ id: string }> }>;
				configured?: Array<{ provider: string; model: string }>;
			}>('/v1/models/upstream');
			const refs: string[] = [];
			for (const p of data.providers || []) {
				for (const m of p.models || []) refs.push(`${p.provider}/${m.id}`);
			}
			for (const c of data.configured || []) {
				const ref = c.model.includes('/') ? c.model : `${c.provider}/${c.model}`;
				if (!refs.includes(ref)) refs.push(ref);
			}
			upstreamModels = refs.sort();
		} catch {
			/* keep empty list */
		}
	}

	async function loadDefaultModel() {
		try {
			const s = await api<{ model: string }>('/v1/settings');
			if (s?.model) defaultModel = s.model;
		} catch {
			/* keep fallback */
		}
	}

	async function checkVoice() {
		try {
			const v = await api<{ available: boolean }>('/v1/voice/status');
			voiceAvailable = v.available;
		} catch {
			voiceAvailable = false;
		}
	}

	async function selectSession(id: string) {
		activeSessionId = id;
		messages = [];
		streamBuffer = '';
		thinkingBuffer = '';
		try {
			const data = await api<Session & { messages?: Message[] }>(`/v1/sessions/${id}`);
			activeSession = data;
			messages = data.messages || [];
			await tick();
			scrollToBottom();
		} catch (e) {
			toasts.show(`Session load failed: ${(e as Error).message}`, 'error');
		}
	}

	async function newSession() {
		try {
			const s = await api<Session>('/v1/sessions', {
				method: 'POST',
				body: { title: 'New chat', model: defaultModel }
			});
			sessions = [s, ...sessions];
			await selectSession(s.id);
		} catch (e) {
			toasts.show(`Create failed: ${(e as Error).message}`, 'error');
		}
	}

	async function deleteSession(id: string) {
		if (!confirm('Delete this session?')) return;
		try {
			await api(`/v1/sessions/${id}`, { method: 'DELETE' });
			sessions = sessions.filter((s) => s.id !== id);
			if (activeSessionId === id) {
				activeSessionId = sessions[0]?.id || null;
				if (activeSessionId) await selectSession(activeSessionId);
				else {
					messages = [];
					activeSession = null;
				}
			}
		} catch (e) {
			toasts.show(`Delete failed: ${(e as Error).message}`, 'error');
		}
	}

	async function updateSessionField(patch: Partial<Session> & { project_id?: string | null }) {
		if (!activeSessionId || !activeSession) return;
		const body = {
			title: activeSession.title,
			model: activeSession.model,
			project_id: activeSession.project_id ?? null,
			...patch
		};
		try {
			const updated = await api<Session>(`/v1/sessions/${activeSessionId}`, {
				method: 'PUT',
				body
			});
			activeSession = { ...activeSession, ...updated };
			sessions = sessions.map((s) => (s.id === activeSessionId ? { ...s, ...updated } : s));
		} catch (e) {
			toasts.show(`Update failed: ${(e as Error).message}`, 'error');
		}
	}

	function toAnthropicMessages(msgs: Message[], next: string | null): Array<{ role: string; content: string }> {
		const out: Array<{ role: string; content: string }> = msgs
			.filter((m) => m.role === 'user' || m.role === 'assistant')
			.map((m) => ({ role: m.role, content: m.content || '' }));
		if (next != null) out.push({ role: 'user', content: next });
		return out;
	}

	async function runJob(opts: { history: Message[]; nextUser: string | null }) {
		if (!activeSessionId) return;
		streaming = true;
		streamBuffer = '';
		thinkingBuffer = '';
		try {
			const job = await api<{ id: string }>(`/v1/sessions/${activeSessionId}/jobs`, {
				method: 'POST',
				body: {
					model: activeSession?.model || defaultModel,
					messages: toAnthropicMessages(opts.history, opts.nextUser),
					max_tokens: 4096,
					project_id: activeSession?.project_id || null
				}
			});

			abortCtl = new AbortController();
			let done = false;
			for await (const frame of sseStream(`/v1/jobs/${job.id}/events`, abortCtl.signal)) {
				const data = (frame.data ?? {}) as Record<string, unknown>;
				const evName = frame.event;
				const evType = data.type as string | undefined;

				if (evName === 'content_block_delta' || evType === 'content_block_delta') {
					const delta = data.delta as { type?: string; text?: string; thinking?: string } | undefined;
					if (delta?.type === 'text_delta' && delta.text) streamBuffer += delta.text;
					else if (delta?.type === 'thinking_delta' && delta.thinking) thinkingBuffer += delta.thinking;
				} else if (
					evName === 'job_finished' ||
					evName === 'job_status' ||
					evType === 'job_finished'
				) {
					const status = data.status as string | undefined;
					if (status === 'error') toasts.show('Job failed', 'error');
					done = true;
				} else if (evName === 'error' || evType === 'error') {
					const msg = (data.error || data.message) as string | undefined;
					if (msg) toasts.show(String(msg), 'error');
					done = true;
				}

				if (streamBuffer || thinkingBuffer) {
					await tick();
					scrollToBottom();
				}
				if (done) break;
			}

			if (streamBuffer.trim()) {
				try {
					await api(`/v1/sessions/${activeSessionId}/messages`, {
						method: 'POST',
						body: { role: 'assistant', content: streamBuffer }
					});
				} catch (e) {
					console.warn('persist assistant message failed', e);
				}
				messages = [
					...messages,
					{
						id: `local-${Date.now()}`,
						session_id: activeSessionId!,
						role: 'assistant',
						content: streamBuffer,
						created_at: new Date().toISOString()
					}
				];
			}

			// Auto-rename session on first turn if title is still default.
			if ((activeSession?.title || '').match(/^(New chat|Untitled|)$/)) {
				try {
					await api(`/v1/sessions/${activeSessionId}/auto-rename`, { method: 'POST' });
					const fresh = await api<Session>(`/v1/sessions/${activeSessionId}`);
					if (fresh.title && activeSession) {
						activeSession = { ...activeSession, title: fresh.title };
						sessions = sessions.map((s) => (s.id === activeSessionId ? { ...s, title: fresh.title } : s));
					}
				} catch {
					/* best effort */
				}
			}
		} catch (e) {
			toasts.show(`Send failed: ${(e as Error).message}`, 'error');
		} finally {
			streaming = false;
			streamBuffer = '';
			thinkingBuffer = '';
			abortCtl = null;
		}
	}

	async function send() {
		const text = composer.trim();
		if (!text || streaming) return;
		if (!activeSessionId) {
			await newSession();
			if (!activeSessionId) return;
		}
		composer = '';

		const optimistic: Message = {
			id: `tmp-${Date.now()}`,
			session_id: activeSessionId!,
			role: 'user',
			content: text,
			created_at: new Date().toISOString()
		};
		messages = [...messages, optimistic];
		await tick();
		scrollToBottom();

		try {
			await api(`/v1/sessions/${activeSessionId}/messages`, {
				method: 'POST',
				body: { role: 'user', content: text }
			});
		} catch (e) {
			toasts.show(`Persist failed: ${(e as Error).message}`, 'error');
		}

		await runJob({ history: messages.slice(0, -1), nextUser: text });
	}

	async function regenerate(msgId: string) {
		if (streaming) return;
		const idx = messages.findIndex((m) => m.id === msgId);
		if (idx < 0) return;
		const m = messages[idx];
		if (m.role !== 'assistant') return;
		// Find the user message that triggered this assistant turn.
		let userIdx = idx - 1;
		while (userIdx >= 0 && messages[userIdx].role !== 'user') userIdx--;
		if (userIdx < 0) return;
		const userText = messages[userIdx].content;

		// Delete the assistant message we're replacing.
		try {
			await api(`/v1/sessions/${activeSessionId}/messages/${msgId}`, { method: 'DELETE' });
		} catch {
			/* tolerate — may be a local-only id */
		}
		messages = messages.slice(0, idx);
		await runJob({ history: messages.slice(0, userIdx), nextUser: userText });
	}

	function copyMessage(content: string) {
		navigator.clipboard.writeText(content);
		toasts.show('Copied', 'ok');
	}

	async function startVoice() {
		try {
			const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
			chunks = [];
			mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });
			mediaRecorder.ondataavailable = (e) => {
				if (e.data.size > 0) chunks.push(e.data);
			};
			mediaRecorder.onstop = async () => {
				stream.getTracks().forEach((t) => t.stop());
				const blob = new Blob(chunks, { type: 'audio/webm' });
				const form = new FormData();
				form.append('audio', blob, 'voice.webm');
				try {
					const data = await api<{ text: string }>('/v1/transcribe', {
						method: 'POST',
						body: form
					});
					if (data?.text) {
						composer = composer ? `${composer} ${data.text}` : data.text;
						toasts.show('Transcribed', 'ok');
					}
				} catch (e) {
					toasts.show(`Transcribe failed: ${(e as Error).message}`, 'error');
				}
			};
			mediaRecorder.start();
			voiceRecording = true;
		} catch (e) {
			toasts.show(`Mic blocked: ${(e as Error).message}`, 'error');
		}
	}

	function stopVoice() {
		if (mediaRecorder && voiceRecording) {
			mediaRecorder.stop();
		}
		voiceRecording = false;
	}

	function scrollToBottom() {
		if (!messagesEl) return;
		messagesEl.scrollTop = messagesEl.scrollHeight;
	}

	function onKeyDown(e: KeyboardEvent) {
		if (e.key === 'Enter' && !e.shiftKey) {
			e.preventDefault();
			send();
		}
	}

	onMount(async () => {
		await Promise.all([loadSessions(), loadProjects(), checkVoice(), loadDefaultModel(), loadUpstreamModels()]);
	});

	function fmtTime(iso: string): string {
		try {
			const d = new Date(iso);
			return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
		} catch {
			return '';
		}
	}
</script>

<div class="chat-shell">
	<aside class="sessions-pane">
		<div class="sessions-header">
			<button class="btn btn-primary" type="button" onclick={newSession}>
				<Plus size={14} strokeWidth={2} /> New chat
			</button>
		</div>
		<div class="sessions-list">
			{#each sessions as s (s.id)}
				<div
					class="session-row"
					class:active={s.id === activeSessionId}
					role="button"
					tabindex="0"
					onclick={() => selectSession(s.id)}
					onkeydown={(e) => { if (e.key === 'Enter') selectSession(s.id); }}
				>
					<div class="session-title">{s.title || 'Untitled'}</div>
					<button class="btn btn-ghost btn-icon" type="button" onclick={(e) => {
						e.stopPropagation();
						deleteSession(s.id);
					}} aria-label="Delete session">
						<Trash2 size={12} strokeWidth={2} />
					</button>
				</div>
			{/each}
			{#if sessions.length === 0}
				<div class="empty">No chats yet</div>
			{/if}
		</div>
	</aside>

	<section class="chat-main">
		<header class="chat-header">
			<div class="row gap-3 align-center" style="flex: 1; min-width: 0">
				<h2 class="chat-title">{activeSession?.title || 'Chat'}</h2>
			</div>
			{#if activeSessionId && activeSession}
				<div class="row gap-2 align-center">
					<label class="picker-label">
						<span>Project</span>
						<select
							class="form-select"
							value={activeSession.project_id || ''}
							onchange={(e) => updateSessionField({ project_id: (e.currentTarget as HTMLSelectElement).value || null })}
						>
							<option value="">— None —</option>
							{#each projects as p (p.id)}
								<option value={p.id}>{p.name}</option>
							{/each}
						</select>
					</label>
					<label class="picker-label">
						<span>Model</span>
						<select
							class="form-select"
							value={activeSession.model || defaultModel}
							onchange={(e) => updateSessionField({ model: (e.currentTarget as HTMLSelectElement).value })}
						>
							{#if !upstreamModels.includes(activeSession.model || defaultModel)}
								<option value={activeSession.model || defaultModel}>{activeSession.model || defaultModel}</option>
							{/if}
							{#each upstreamModels as m (m)}
								<option value={m}>{m}</option>
							{/each}
						</select>
					</label>
				</div>
			{/if}
		</header>

		<div class="messages" bind:this={messagesEl}>
			{#each messages as m (m.id)}
				<article class="msg msg-{m.role}">
					<header class="msg-header">
						<span class="msg-role">{m.role}</span>
						<div class="row gap-2 align-center">
							<span class="msg-time">{fmtTime(m.created_at)}</span>
							<button class="btn btn-ghost btn-icon" type="button" onclick={() => copyMessage(m.content)} title="Copy" aria-label="Copy"><Copy size={12} strokeWidth={2} /></button>
							{#if m.role === 'assistant'}
								<button class="btn btn-ghost btn-icon" type="button" onclick={() => regenerate(m.id)} title="Regenerate" aria-label="Regenerate" disabled={streaming}>
									<RotateCcw size={12} strokeWidth={2} />
								</button>
							{/if}
						</div>
					</header>
					<div class="msg-body">
						{#if m.role === 'assistant'}
							<Markdown content={m.content} />
						{:else}
							{m.content}
						{/if}
					</div>
				</article>
			{/each}

			{#if streaming}
				{#if thinkingBuffer}
					<details class="thinking-block" open={showThinking}>
						<summary>
							<Loader2 size={12} strokeWidth={2} class="spin-icon" />
							Thinking…
							<ChevronDown size={12} strokeWidth={2} />
						</summary>
						<div class="thinking-body">{thinkingBuffer}</div>
					</details>
				{/if}
				<article class="msg msg-assistant">
					<header class="msg-header">
						<span class="msg-role">assistant</span>
						<Loader2 size={12} strokeWidth={2} class="spin-icon" />
					</header>
					<div class="msg-body">
						{#if streamBuffer}
							<Markdown content={streamBuffer} />
						{:else}
							<span style="color: var(--fg-muted)">…</span>
						{/if}
					</div>
				</article>
			{/if}

			{#if !streaming && messages.length === 0}
				<div class="empty">Send a message to start.</div>
			{/if}
		</div>

		<div class="composer">
			<textarea
				class="form-textarea"
				placeholder="Type a message — Enter to send, Shift+Enter for newline"
				bind:value={composer}
				onkeydown={onKeyDown}
				rows="3"
				disabled={streaming}
			></textarea>
			<div class="composer-actions">
				{#if voiceAvailable}
					{#if voiceRecording}
						<button class="btn btn-ghost recording" type="button" onclick={stopVoice} title="Stop recording" aria-label="Stop">
							<MicOff size={14} strokeWidth={2} />
							Stop
						</button>
					{:else}
						<button class="btn btn-ghost" type="button" onclick={startVoice} title="Voice input" aria-label="Voice">
							<Mic size={14} strokeWidth={2} />
						</button>
					{/if}
				{/if}
				<button class="btn btn-primary" type="button" disabled={streaming || !composer.trim()} onclick={send}>
					<Send size={14} strokeWidth={2} />
					Send
				</button>
			</div>
		</div>
	</section>
</div>

<style>
	.chat-shell {
		display: grid;
		grid-template-columns: 260px 1fr;
		height: 100vh;
	}
	.sessions-pane {
		border-right: 1px solid var(--border);
		display: flex;
		flex-direction: column;
		min-height: 0;
	}
	.sessions-header {
		padding: var(--sp-3) var(--sp-4);
		border-bottom: 1px solid var(--border);
	}
	.sessions-header :global(.btn) {
		width: 100%;
		justify-content: center;
	}
	.sessions-list {
		overflow-y: auto;
		flex: 1;
		padding: var(--sp-2);
	}
	.session-row {
		display: flex;
		align-items: center;
		gap: var(--sp-2);
		padding: 8px 10px;
		border-radius: var(--radius);
		color: var(--fg);
		font-size: var(--fs-13);
		cursor: pointer;
		width: 100%;
		text-align: left;
	}
	.session-row:hover {
		background: var(--bg-hover);
	}
	.session-row.active {
		background: var(--bg-active);
	}
	.session-title {
		flex: 1;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.chat-main {
		display: flex;
		flex-direction: column;
		min-width: 0;
		height: 100vh;
	}
	.chat-header {
		display: flex;
		gap: var(--sp-4);
		align-items: center;
		padding: var(--sp-3) var(--sp-5);
		border-bottom: 1px solid var(--border);
		background: var(--bg);
	}
	.chat-title {
		font-size: var(--fs-18);
		font-weight: 600;
		margin: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}
	.picker-label {
		display: flex;
		flex-direction: column;
		font-size: 11px;
		color: var(--fg-muted);
		gap: 2px;
	}
	.picker-label .form-select {
		min-width: 160px;
		font-size: 12px;
		padding: 4px 6px;
	}
	.messages {
		flex: 1;
		overflow-y: auto;
		padding: var(--sp-4) var(--sp-5);
		display: flex;
		flex-direction: column;
		gap: var(--sp-3);
	}
	.msg {
		max-width: 760px;
		width: 100%;
		background: var(--bg-card);
		border: 1px solid var(--border);
		border-radius: var(--radius-lg);
		padding: var(--sp-3) var(--sp-4);
	}
	.msg-user {
		align-self: flex-end;
		background: var(--bg-active);
		white-space: pre-wrap;
	}
	.msg-assistant {
		align-self: flex-start;
	}
	.msg-header {
		display: flex;
		justify-content: space-between;
		align-items: center;
		font-size: var(--fs-12);
		color: var(--fg-muted);
		margin-bottom: 6px;
	}
	.msg-role {
		text-transform: uppercase;
		letter-spacing: 0.04em;
	}
	.msg-body {
		word-wrap: break-word;
		line-height: 1.6;
	}
	.msg-user .msg-body {
		white-space: pre-wrap;
	}
	.thinking-block {
		max-width: 760px;
		align-self: flex-start;
		background: var(--bg-input);
		border: 1px dashed var(--border);
		border-radius: var(--radius);
		padding: 6px 12px;
		font-size: 12px;
		color: var(--fg-muted);
	}
	.thinking-block summary {
		cursor: pointer;
		display: flex;
		align-items: center;
		gap: 6px;
	}
	.thinking-body {
		white-space: pre-wrap;
		font-family: ui-monospace, monospace;
		font-size: 11px;
		margin-top: 6px;
	}
	.composer {
		border-top: 1px solid var(--border);
		padding: var(--sp-3) var(--sp-4);
		display: flex;
		flex-direction: column;
		gap: var(--sp-2);
		background: var(--bg-elev);
	}
	.composer-actions {
		display: flex;
		justify-content: flex-end;
		gap: var(--sp-2);
	}
	.recording {
		color: var(--error);
		animation: pulse 1.4s ease-in-out infinite;
	}
	:global(.spin-icon) {
		animation: spin 0.8s linear infinite;
	}
	@keyframes pulse {
		0%, 100% { opacity: 1; }
		50% { opacity: 0.5; }
	}
</style>
