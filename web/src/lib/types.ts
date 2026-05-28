/**
 * Backend types — hand-written subset matching api/dashboard_routes.py.
 * Keep narrow; expand as pages need more fields.
 */

export interface SettingsSnapshot {
	model: string;
	model_opus?: string;
	model_sonnet?: string;
	model_haiku?: string;
	model_subagent?: string;
	model_fallback_chain?: string;
	thinking: { default_enabled: boolean; budget_max: number };
	deepseek_image_fallback: { provider: string | null; model: string | null };
	image_gen: { provider: string | null; model: string | null };
	voice: { enabled: boolean; whisper_binary: string | null; whisper_model: string | null };
	agent_mode: {
		enabled: boolean;
		max_turns: number;
		workspace: string | null;
		bash_denylist: string | null;
		bash_extra_env: string | null;
		tool_call_limit_per_min: number;
		global_tool_call_limit_per_min: number;
	};
	host: string;
	port: number;
	anthropic_auth_token_set: boolean;
	env_file: string;
	build?: { sha: string; ts: string };
	process: { pid: number; uptime_s: number };
}

export interface Project {
	id: string;
	name: string;
	description?: string;
	workspace_path?: string;
	created_at: string;
	updated_at: string;
	color?: string;
}

export interface Session {
	id: string;
	title: string;
	project_id?: string;
	model?: string;
	created_at: string;
	updated_at: string;
	message_count?: number;
}

export interface Message {
	id: string;
	session_id: string;
	role: 'user' | 'assistant' | 'system';
	content: string;
	created_at: string;
	model?: string;
	cost_usd?: number;
	tokens_in?: number;
	tokens_out?: number;
}

export interface Job {
	id: string;
	session_id: string;
	status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';
	model?: string;
	created_at: string;
	updated_at: string;
	turns?: number;
	cost_usd?: number;
	error?: string;
}

export interface JobEvent {
	type:
		| 'text_delta'
		| 'tool_use'
		| 'tool_result'
		| 'usage'
		| 'turn_complete'
		| 'job_complete'
		| 'error';
	delta?: string;
	tool_name?: string;
	tool_input?: unknown;
	tool_result?: unknown;
	usage?: { tokens_in: number; tokens_out: number; cost_usd: number };
	error?: string;
}

export interface EnvEntry {
	key: string;
	value: string;
	secret: boolean;
	comment?: string;
}

export interface VoiceStatus {
	available: boolean;
	backend: 'whisper' | 'disabled' | string;
	reason?: string;
}

export interface UsageSummary {
	today_cost_usd: number;
	week_cost_usd: number;
	month_cost_usd: number;
	by_model: Array<{ model: string; cost_usd: number; tokens: number }>;
}

export interface Capability {
	id: string;
	label: string;
	status: 'active' | 'inactive' | 'suggested';
	hint?: string;
}

export interface Routine {
	id: string;
	name: string;
	description?: string;
	trigger_type?: string;
	trigger_config?: string | Record<string, unknown>;
	payload?: string | Record<string, unknown>;
	enabled: boolean;
	project_id?: string;
	cron?: string;    // deprecated — use trigger_config
	prompt?: string;  // deprecated — use payload
	last_run_at?: string;
	last_run_ms?: number;
	next_run_at?: string;
	next_run_ms?: number;
	created_at?: string;
}

export interface Skill {
	id: string;
	name: string;
	description?: string;
	installed: boolean;
	source?: string;
}

export interface AuditEntry {
	id: string;
	category: string;
	event: string;
	detail?: string;
	created_at: string;
	metadata?: Record<string, unknown>;
}

export interface FileEdit {
	id: string;
	session_id: string;
	path: string;
	diff: string;
	created_at: string;
}

export interface RootFile {
	path: string;
	size: number;
	modified_at: string;
	is_dir: boolean;
}

export interface Insight {
	id: string;
	kind: string;
	title: string;
	body: string;
	created_at: string;
}

export interface Preference {
	key: string;
	value: string;
	category?: string;
}

export interface SearchResult {
	kind: string;
	title: string;
	subtitle: string;
	navigate_url: string;
	score: number;
	ts: number;
	ref: string;
}
