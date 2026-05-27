<script lang="ts">
	interface Props {
		title: string;
		sub?: string;
		actions?: import('svelte').Snippet;
	}

	let { title, sub, actions }: Props = $props();
</script>

<header class="page-header">
	<div>
		<h1>{title}</h1>
		{#if sub}<p>{sub}</p>{/if}
	</div>
	{#if actions}
		<div class="row gap-2 align-center">{@render actions()}</div>
	{/if}
</header>
