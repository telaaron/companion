<script lang="ts">
	import { onMount, tick } from 'svelte';
	import { api, sseStream } from '$lib/api';
	import { toasts } from '$lib/stores.svelte';
	import type { Session, Message, Project, JobEvent } from '$lib/types';
	import PageHeader from '$lib/PageHeader.svelte';
	import { Plus, Send, Mic, RotateCcw, Trash2, Loader2 } from 'lucide-svelte';

	let sessions = $state<Session[]>([]);
	let projects = $state<Project[]>([]);
	let activeSessionId = $state<string | null>(null);
	let messages = $state<Message[]>([]);
	let composer = $state('');
	let streaming = $state(false);
	let streamBuffer = $state('');
	let messagesEl: HTMLDivElement | undefined;
	let abortCtl: AbortController | null = null;
	let voiceAvailable = $state(false);

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
			/* projects optional */
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
		try {
			const data = await api<{ messages: Message[] }>(`/v1/sessions/${id}/messages`);
			messages = data.messages || [];
			await tick();
			scrollToBottom();
		} catch (e) {
			toasts.show(`Messages load failed: ${(e as Error).message}`, 'error');
		}
	}

	async function newSession() {
		try {
			const s = await api<Session>('/v1/sessions', { method: 'POST', body: {} });
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
				else messages = [];
			}
		} catch (e) {
			toasts.show(`Delete failed: ${(e as Error).message}`, 'error');
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
		streaming = true;
		streamBuffer = '';
		// optimistic user message
		const userMsg: Message = {
			id: `tmp-${Date.now()}`,
			session_id: activeSessionId!,
			role: 'user',
			content: text,
			created_at: new Date().toISOString()
		};
		messages = [...messages, userMsg];
		await tick();
		scrollToBottom();

		try {
			const job = await api<{ id: string }>(`/v1/sessions/${activeSessionId}/jobs`, {
				method: 'POST',
				body: { message: text }
			});
			abortCtl = new AbortController();
			for await (const ev of sseStream<JobEvent>(`/v1/jobs/${job.id}/events`, abortCtl.signal)) {
				if (ev.type === 'text_delta' && ev.delta) streamBuffer += ev.delta;
				else if (ev.type === 'job_complete') break;
				else if (ev.type === 'error' && ev.error) {
					toasts.show(ev.error, 'error');
					break;
				}
				await tick();
				scrollToBottom();
			}
			// Reload final assistant message from DB to get authoritative state.
			await selectSession(activeSessionId!);
		} catch (e) {
			toasts.show(`Send failed: ${(e as Error).message}`, 'error');
		} finally {
			streaming = false;
			streamBuffer = '';
			abortCtl = null;
		}
	}

	function scrollToBottom() {
		if (!messagesEl) return;
		messagesEl.scrollTop = messagesEl.scrollHeight;
	}

	function onKeyDown(e: KeyboardEvent) {
		if (e.key === 'Enter' && !e.shiftKey && (e.metaKey || e.ctrlKey || !e.shiftKey)) {
			if (e.key === 'Enter' && !e.shiftKey) {
				e.preventDefault();
				send();
			}
		}
	}

	onMount(async () => {
		await Promise.all([loadSessions(), loadProjects(), checkVoice()]);
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
					{#if s.message_count}
						<span class="pill">{s.message_count}</span>
					{/if}
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
		<PageHeader title="Chat" sub={activeSessionId ? `Session ${activeSessionId.slice(0, 8)}` : 'Start a new chat'} />

		<div class="messages" bind:this={messagesEl}>
			{#each messages as m (m.id)}
				<article class="msg msg-{m.role}">
					<header class="msg-header">
						<span class="msg-role">{m.role}</span>
						<span class="msg-time">{fmtTime(m.created_at)}</span>
					</header>
					<div class="msg-body">{m.content}</div>
				</article>
			{/each}
			{#if streaming && streamBuffer}
				<article class="msg msg-assistant">
					<header class="msg-header">
						<span class="msg-role">assistant</span>
						<Loader2 size={12} class="spin-icon" />
					</header>
					<div class="msg-body">{streamBuffer}</div>
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
					<button class="btn btn-ghost" type="button" title="Voice input">
						<Mic size={14} strokeWidth={2} />
					</button>
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
	}
	.sessions-header {
		padding: var(--sp-3) var(--sp-4);
		border-bottom: 1px solid var(--border);
	}
	.sessions-header .btn {
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
		background: transparent;
		border: none;
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
	}
	.msg-assistant {
		align-self: flex-start;
	}
	.msg-header {
		display: flex;
		justify-content: space-between;
		font-size: var(--fs-12);
		color: var(--fg-muted);
		margin-bottom: 4px;
	}
	.msg-role {
		text-transform: uppercase;
		letter-spacing: 0.04em;
	}
	.msg-body {
		white-space: pre-wrap;
		word-wrap: break-word;
		line-height: 1.6;
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
	:global(.spin-icon) {
		animation: spin 0.8s linear infinite;
	}
</style>
