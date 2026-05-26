/**
 * Companion API client.
 *
 * Wraps fetch with bearer auth + JSON conventions. The bearer token is
 * persisted in localStorage; first request prompts the user once.
 */

const TOKEN_KEY = 'companion.token';
const BASE_KEY = 'companion.baseUrl';

function getBase(): string {
	if (typeof window === 'undefined') return '';
	const stored = localStorage.getItem(BASE_KEY);
	if (stored) return stored.replace(/\/$/, '');
	// Default: same-origin (dev = vite proxy, Tauri = bundled).
	return '';
}

export function getToken(): string {
	if (typeof window === 'undefined') return '';
	// Default to the dev-shipping token `freecc` if nothing is stored — the
	// proxy ships with this token enabled out of the box. Users can override
	// via the auth modal once we surface auth errors. No browser prompt() on
	// first paint: it blocks rendering and looks like a phishing dialog.
	let token = localStorage.getItem(TOKEN_KEY) || '';
	if (!token) {
		token = 'freecc';
	}
	return token;
}

export function setToken(token: string) {
	localStorage.setItem(TOKEN_KEY, token);
}

export class ApiError extends Error {
	status: number;
	detail: unknown;
	constructor(status: number, message: string, detail: unknown = null) {
		super(message);
		this.status = status;
		this.detail = detail;
	}
}

export interface ApiInit extends Omit<RequestInit, 'body'> {
	body?: BodyInit | object | null;
	query?: Record<string, string | number | boolean | null | undefined>;
}

export async function api<T = unknown>(path: string, init: ApiInit = {}): Promise<T> {
	const base = getBase();
	const token = getToken();
	const url = new URL(
		path.startsWith('http') ? path : base + path,
		typeof window === 'undefined' ? 'http://127.0.0.1:8082' : window.location.origin
	);
	if (init.query) {
		for (const [k, v] of Object.entries(init.query)) {
			if (v === null || v === undefined) continue;
			url.searchParams.set(k, String(v));
		}
	}
	const headers = new Headers(init.headers || {});
	if (!headers.has('Authorization') && token) {
		headers.set('Authorization', `Bearer ${token}`);
	}
	let body: BodyInit | null | undefined = undefined;
	if (init.body != null) {
		if (
			typeof init.body === 'string' ||
			init.body instanceof FormData ||
			init.body instanceof Blob ||
			init.body instanceof ArrayBuffer
		) {
			body = init.body as BodyInit;
		} else {
			body = JSON.stringify(init.body);
			if (!headers.has('Content-Type')) {
				headers.set('Content-Type', 'application/json');
			}
		}
	}
	const res = await fetch(url.toString(), {
		method: init.method || (body ? 'POST' : 'GET'),
		headers,
		body
	});
	const ct = res.headers.get('content-type') || '';
	const text = ct.includes('application/json') ? await res.text() : '';
	const parsed = text ? safeJson(text) : null;
	if (!res.ok) {
		const msg =
			(parsed && typeof parsed === 'object' && 'detail' in parsed
				? String((parsed as { detail: unknown }).detail)
				: text) || `HTTP ${res.status}`;
		throw new ApiError(res.status, msg, parsed);
	}
	return (parsed as T) ?? (null as T);
}

function safeJson(text: string): unknown {
	try {
		return JSON.parse(text);
	} catch {
		return null;
	}
}

/**
 * Parsed SSE frame as emitted by the Anthropic proxy. Yields one frame
 * per `event: ...\\ndata: ...\\n\\n` block, exposing both the event name
 * and the parsed JSON payload so consumers can dispatch on either.
 */
export interface SseFrame {
	id?: string;
	event?: string;
	data: Record<string, unknown> | null;
	raw: string;
}

/**
 * SSE event stream. Reads `text/event-stream` and yields parsed frames.
 * Heartbeat comments (`: heartbeat`) and empty payloads are skipped.
 * Pass an AbortSignal to stop the stream early.
 */
export async function* sseStream(
	path: string,
	signal?: AbortSignal
): AsyncGenerator<SseFrame, void, void> {
	const base = getBase();
	const token = getToken();
	const url = path.startsWith('http') ? path : base + path;
	const res = await fetch(url, {
		headers: {
			Authorization: `Bearer ${token}`,
			Accept: 'text/event-stream'
		},
		signal
	});
	if (!res.ok || !res.body) {
		throw new ApiError(res.status, `SSE failed: ${res.status}`);
	}
	const reader = res.body.getReader();
	const decoder = new TextDecoder();
	let buf = '';
	while (true) {
		const { done, value } = await reader.read();
		if (done) break;
		buf += decoder.decode(value, { stream: true });
		// SSE frames are separated by a blank line. Split, keep the
		// trailing partial frame in the buffer.
		const frames = buf.split('\n\n');
		buf = frames.pop() || '';
		for (const raw of frames) {
			if (!raw.trim() || raw.startsWith(':')) continue; // heartbeat / comment
			const frame: SseFrame = { data: null, raw };
			const dataParts: string[] = [];
			for (const line of raw.split('\n')) {
				if (line.startsWith('id:')) frame.id = line.slice(3).trim();
				else if (line.startsWith('event:')) frame.event = line.slice(6).trim();
				else if (line.startsWith('data:')) dataParts.push(line.slice(5).trim());
			}
			if (dataParts.length === 0) continue;
			const dataStr = dataParts.join('\n');
			try {
				frame.data = JSON.parse(dataStr);
			} catch {
				// Allow non-JSON data through as a string under `__raw`.
				frame.data = { __raw: dataStr } as unknown as Record<string, unknown>;
			}
			yield frame;
		}
	}
}
