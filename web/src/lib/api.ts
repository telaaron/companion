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
 * SSE event stream. Yields parsed JSON payloads from a server-sent-events
 * endpoint. Pass an AbortSignal to stop the stream.
 */
export async function* sseStream<T = unknown>(
	path: string,
	signal?: AbortSignal
): AsyncGenerator<T, void, void> {
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
		const events = buf.split('\n\n');
		buf = events.pop() || '';
		for (const ev of events) {
			const dataLine = ev
				.split('\n')
				.find((l) => l.startsWith('data:'));
			if (!dataLine) continue;
			const data = dataLine.slice(5).trim();
			if (!data) continue;
			try {
				yield JSON.parse(data) as T;
			} catch {
				/* skip malformed */
			}
		}
	}
}
