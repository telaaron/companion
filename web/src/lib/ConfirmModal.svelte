<script lang="ts">
	import { confirmStore } from '$lib/stores.svelte';
</script>

{#if confirmStore.open}
	<div
		class="modal-backdrop"
		role="dialog"
		aria-modal="true"
		tabindex="-1"
		onclick={() => confirmStore.resolve(false)}
		onkeydown={(e) => {
			if (e.key === 'Escape') confirmStore.resolve(false);
			else if (e.key === 'Enter') confirmStore.resolve(true);
		}}
	>
		<div class="modal-card" role="document" onclick={(e) => e.stopPropagation()}>
			<p class="modal-message">{confirmStore.message}</p>
			<div class="modal-actions">
				<button class="btn" type="button" onclick={() => confirmStore.resolve(false)}>Cancel</button>
				<button class="btn btn-primary" type="button" onclick={() => confirmStore.resolve(true)}>OK</button>
			</div>
		</div>
	</div>
{/if}

<style>
	.modal-backdrop {
		position: fixed;
		inset: 0;
		background: rgba(0, 0, 0, 0.5);
		display: flex;
		align-items: center;
		justify-content: center;
		z-index: 2000;
	}
	.modal-card {
		background: var(--bg-card);
		border: 1px solid var(--border);
		border-radius: var(--radius-lg);
		padding: var(--sp-4);
		min-width: 300px;
		max-width: 420px;
	}
	.modal-message {
		margin: 0 0 var(--sp-4);
	}
	.modal-actions {
		display: flex;
		gap: var(--sp-2);
		justify-content: flex-end;
	}
</style>
