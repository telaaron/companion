<script lang="ts">
	import { Info, X } from 'lucide-svelte';

	interface Props {
		title: string;
		body: string;
		/** localStorage key so a dismissed banner stays hidden. */
		storageKey?: string;
		tone?: 'info' | 'warn';
	}
	let { title, body, storageKey, tone = 'info' }: Props = $props();

	let dismissed = $state(
		storageKey && typeof localStorage !== 'undefined'
			? localStorage.getItem('banner-dismissed:' + storageKey) === '1'
			: false
	);

	function dismiss() {
		dismissed = true;
		if (storageKey && typeof localStorage !== 'undefined') {
			localStorage.setItem('banner-dismissed:' + storageKey, '1');
		}
	}
</script>

{#if !dismissed}
	<div class="info-banner" class:warn={tone === 'warn'}>
		<Info size={16} strokeWidth={2} class="info-banner-icon" />
		<div class="info-banner-text">
			<strong>{title}</strong>
			<p>{body}</p>
		</div>
		{#if storageKey}
			<button class="info-banner-close" type="button" onclick={dismiss} aria-label="Dismiss">
				<X size={14} strokeWidth={2} />
			</button>
		{/if}
	</div>
{/if}

<style>
	.info-banner {
		display: flex;
		align-items: flex-start;
		gap: var(--sp-3);
		padding: var(--sp-3) var(--sp-4);
		margin-bottom: var(--sp-4);
		background: color-mix(in srgb, var(--accent) 8%, var(--bg-card));
		border: 1px solid color-mix(in srgb, var(--accent) 25%, var(--border));
		border-radius: var(--radius-lg);
		font-size: var(--fs-13);
	}
	.info-banner.warn {
		background: color-mix(in srgb, #f59e0b 10%, var(--bg-card));
		border-color: color-mix(in srgb, #f59e0b 35%, var(--border));
	}
	.info-banner :global(.info-banner-icon) {
		margin-top: 2px;
		color: var(--accent);
		flex-shrink: 0;
	}
	.info-banner.warn :global(.info-banner-icon) {
		color: #f59e0b;
	}
	.info-banner-text {
		flex: 1;
		min-width: 0;
	}
	.info-banner-text strong {
		display: block;
		margin-bottom: 2px;
	}
	.info-banner-text p {
		margin: 0;
		color: var(--fg-muted);
		line-height: 1.5;
	}
	.info-banner-close {
		background: transparent;
		border: none;
		color: var(--fg-muted);
		cursor: pointer;
		padding: 2px;
		flex-shrink: 0;
	}
	.info-banner-close:hover {
		color: var(--fg);
	}
</style>
