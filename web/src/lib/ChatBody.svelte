<script lang="ts">
	import Markdown from './Markdown.svelte';

	interface Props {
		content: string;
		onPreviewFile?: (path: string) => void;
	}
	let { content, onPreviewFile }: Props = $props();

	interface TextSegment {
		kind: 'text';
		body: string;
	}
	interface ToolSegment {
		kind: 'tool';
		icon: '⏺' | '✗';
		label: string;
		toolName: string;
		argSummary: string;
		filePath?: string;
		result: string;
		imageUrl?: string;
	}

	// Backend wraps every tool invocation in plaintext blocks shaped like:
	//   ⏺ ToolName(arg-summary)
	//     ⎿ <result preview...>
	// Sometimes ✗ replaces the icon when the call errored. We split the
	// assistant content into alternating text + tool segments so each one
	// can be rendered with the right component without leaking the raw
	// "⏺" Unicode glyph into the user's view.
	const TOOL_RE = /(⏺|✗)\s+([A-Za-z][\w-]*)\(([^)]*)\)\n\s*⎿\s*([\s\S]*?)(?=\n\n|$)/g;

	let segments = $derived.by<Array<TextSegment | ToolSegment>>(() => {
		const out: Array<TextSegment | ToolSegment> = [];
		const text = content || '';
		let last = 0;
		const re = new RegExp(TOOL_RE.source, TOOL_RE.flags);
		let m: RegExpExecArray | null;
		while ((m = re.exec(text)) !== null) {
			if (m.index > last) {
				const before = text.slice(last, m.index);
				if (before.trim()) out.push({ kind: 'text', body: before });
			}
			const [, icon, toolName, argRaw, resultRaw] = m;
			const arg = (argRaw || '').trim();
			let filePath: string | undefined;
			// File-ops keep the full path verbatim in the parens — surface
			// it for the click-to-preview affordance later.
			if (['Read', 'Write', 'Edit', 'LS'].includes(toolName) && arg) {
				filePath = arg;
			}
			out.push({
				kind: 'tool',
				icon: (icon as '⏺' | '✗') || '⏺',
				toolName,
				label: `${toolName}(${arg.length > 60 ? arg.slice(0, 60) + '…' : arg})`,
				argSummary: arg,
				filePath,
				result: (resultRaw || '').trimEnd(),
				imageUrl: _extractImageUrl(toolName, resultRaw)
			});
			last = m.index + m[0].length;
		}
		if (last < text.length) {
			const tail = text.slice(last);
			if (tail.trim()) out.push({ kind: 'text', body: tail });
		}
		if (out.length === 0) out.push({ kind: 'text', body: text });
		return out;
	});

	let expanded = $state<Record<number, boolean>>({});

	function _extractImageUrl(
		toolName: string,
		raw: string | undefined
	): string | undefined {
		if (toolName !== 'Imagine') return undefined;
		try {
			const obj = JSON.parse(raw || '');
			if (obj.status === 'ok' && typeof obj.image_url === 'string') {
				return obj.image_url;
			}
		} catch {
			// not JSON
		}
		return undefined;
	}
</script>

<div class="chat-body">
	{#each segments as seg, idx (idx)}
		{#if seg.kind === 'text'}
			<Markdown content={seg.body.trim()} />
		{:else}
			<div class="tool-block" class:errored={seg.icon === '✗'}>
				<button
					class="tool-head"
					type="button"
					onclick={() => {
						if (seg.filePath && onPreviewFile) {
							onPreviewFile(seg.filePath);
						}
						expanded[idx] = !expanded[idx];
					}}
				>
					<span class="tool-icon">{seg.icon}</span>
					<span class="tool-name">{seg.toolName}</span>
					{#if seg.argSummary}<span class="tool-arg">({seg.argSummary})</span>{/if}
					<span class="tool-toggle">{expanded[idx] ? '▾' : '▸'}</span>
				</button>
				{#if expanded[idx]}
					{#if seg.toolName === 'Imagine' && seg.imageUrl}
						<div class="tool-result-image">
							<img src={seg.imageUrl} alt={seg.argSummary} loading="lazy" />
							<div class="tool-result-image-caption">
								<Markdown content={'_' + seg.argSummary + '_'} />
							</div>
						</div>
					{:else}
						<pre class="tool-result">{seg.result}</pre>
					{/if}
				{/if}
			</div>
		{/if}
	{/each}
</div>

<style>
	.chat-body {
		display: flex;
		flex-direction: column;
		gap: 6px;
	}
	.tool-block {
		background: var(--bg-input);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		font-size: 12px;
	}
	.tool-block.errored {
		border-color: var(--error);
	}
	.tool-head {
		display: flex;
		align-items: center;
		gap: 6px;
		width: 100%;
		text-align: left;
		background: transparent;
		border: none;
		color: var(--fg);
		padding: 6px 10px;
		cursor: pointer;
		font: inherit;
	}
	.tool-icon {
		color: var(--fg-muted);
		flex: 0 0 auto;
	}
	.tool-block.errored .tool-icon {
		color: var(--error);
	}
	.tool-name {
		font-weight: 500;
	}
	.tool-arg {
		color: var(--fg-muted);
		flex: 1;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		min-width: 0;
	}
	.tool-toggle {
		color: var(--fg-dim);
		flex: 0 0 auto;
	}
	.tool-head:hover {
		background: var(--bg-hover);
	}
	.tool-result {
		margin: 0;
		padding: 8px 12px;
		border-top: 1px solid var(--border);
		font-family: ui-monospace, monospace;
		font-size: 11px;
		white-space: pre-wrap;
		word-break: break-all;
		color: var(--fg-muted);
		max-height: 300px;
		overflow-y: auto;
	}
	.tool-result-image {
		border-top: 1px solid var(--border);
		padding: 0;
	}
	.tool-result-image img {
		display: block;
		max-width: 100%;
		height: auto;
		border-radius: 0 0 var(--radius) var(--radius);
	}
	.tool-result-image-caption {
		padding: 6px 12px;
		font-size: 11px;
		color: var(--fg-dim);
		border-top: 1px solid var(--border);
	}
</style>
