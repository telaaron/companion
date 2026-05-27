<script lang="ts">
	import { marked } from 'marked';

	interface Props {
		content: string;
	}
	let { content }: Props = $props();

	// Minimal config: GFM tables, no raw HTML (XSS-safe enough for assistant
	// responses; we don't trust user input either way).
	marked.setOptions({
		gfm: true,
		breaks: true
	});

	let html = $derived(marked.parse(content || '', { async: false }) as string);
</script>

<div class="md">{@html html}</div>

<style>
	.md :global(p) {
		margin: 0 0 var(--sp-3);
	}
	.md :global(p:last-child) {
		margin-bottom: 0;
	}
	.md :global(pre) {
		background: var(--bg-input);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: var(--sp-3);
		overflow-x: auto;
		font-family: ui-monospace, monospace;
		font-size: 12px;
		margin: var(--sp-2) 0;
	}
	.md :global(code) {
		background: var(--bg-input);
		border-radius: 3px;
		padding: 1px 5px;
		font-family: ui-monospace, monospace;
		font-size: 0.9em;
	}
	.md :global(pre code) {
		background: transparent;
		padding: 0;
	}
	.md :global(h1),
	.md :global(h2),
	.md :global(h3) {
		margin: var(--sp-3) 0 var(--sp-2);
		line-height: 1.3;
	}
	.md :global(h1) {
		font-size: 1.4em;
	}
	.md :global(h2) {
		font-size: 1.2em;
	}
	.md :global(h3) {
		font-size: 1.05em;
	}
	.md :global(ul),
	.md :global(ol) {
		margin: 0 0 var(--sp-3);
		padding-left: 22px;
	}
	.md :global(li) {
		margin: 2px 0;
	}
	.md :global(blockquote) {
		border-left: 3px solid var(--border-strong);
		padding-left: var(--sp-3);
		color: var(--fg-muted);
		margin: var(--sp-2) 0;
	}
	.md :global(table) {
		border-collapse: collapse;
		width: 100%;
		margin: var(--sp-2) 0;
		font-size: 13px;
	}
	.md :global(th),
	.md :global(td) {
		border: 1px solid var(--border);
		padding: 4px 8px;
	}
	.md :global(a) {
		color: var(--accent);
	}
	.md :global(hr) {
		border: none;
		border-top: 1px solid var(--border);
		margin: var(--sp-3) 0;
	}
</style>
