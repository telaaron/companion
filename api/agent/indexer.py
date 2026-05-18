"""RAG file indexer — scans configured directories and watches for changes.

Chunks files into ~800-token windows using ``tiktoken`` cl100k_base, writes
each chunk as a ``kind='file'`` row in the ``memory_fts`` FTS table, and
keeps the index fresh via a debounced ``watchdog`` observer.

Lifecycle
---------
1. ``Indexer.scan(paths)`` — full scan on startup.
2. ``Indexer.start_watch(paths)`` — starts an Observer that debounces FS
   events into a background asyncio worker.
3. ``Indexer.stop()`` — graceful shutdown; stops the Observer and drains the
   queue.

The class is designed to be driven by ``api/runtime.py`` startup/shutdown
hooks.  If ``MEMORY_INDEX_PATHS`` is empty the indexer is a no-op.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import threading
import time
import types
from pathlib import Path
from typing import Any

from loguru import logger

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CHUNK_TOKENS = 800
_MAX_FILE_BYTES = 256 * 1024  # 256 KB — skip larger files
_DEBOUNCE_S = 0.25  # 250 ms debounce window

# ---------------------------------------------------------------------------
# Token counting (lazy import to avoid startup cost when indexer is disabled)
# ---------------------------------------------------------------------------


def _get_encoder():  # type: ignore[return]
    import tiktoken

    return tiktoken.get_encoding("cl100k_base")


_encoder_lock = threading.Lock()
_encoder = None


def _count_tokens(text: str) -> int:
    global _encoder
    if _encoder is None:
        with _encoder_lock:
            if _encoder is None:
                _encoder = _get_encoder()
    return len(_encoder.encode(text, disallowed_special=()))


def _token_chunks(text: str, chunk_tokens: int = _CHUNK_TOKENS) -> list[str]:
    """Split *text* into chunks of at most *chunk_tokens* tokens.

    Splits on paragraph boundaries first; accumulates paragraphs until the
    chunk would exceed the limit, then starts a new chunk.
    """
    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    current_parts: list[str] = []
    current_count = 0

    for para in paragraphs:
        para_tokens = _count_tokens(para)
        if current_parts and current_count + para_tokens > chunk_tokens:
            chunks.append("\n\n".join(current_parts))
            current_parts = []
            current_count = 0
        # If a single paragraph is huge, split it hard
        if para_tokens > chunk_tokens:
            # flush current buffer first
            if current_parts:
                chunks.append("\n\n".join(current_parts))
                current_parts = []
                current_count = 0
            # hard-split the paragraph by tokens
            enc = _get_encoder()
            token_ids = enc.encode(para, disallowed_special=())
            for start in range(0, len(token_ids), chunk_tokens):
                sub_ids = token_ids[start : start + chunk_tokens]
                chunks.append(enc.decode(sub_ids))
        else:
            current_parts.append(para)
            current_count += para_tokens

    if current_parts:
        chunks.append("\n\n".join(current_parts))
    return chunks or [text[:4000]]  # fallback for empty text


# ---------------------------------------------------------------------------
# Index state (shared between Indexer instances in tests)
# ---------------------------------------------------------------------------

_index_lock = threading.Lock()
_index_bytes: int = 0
_last_scan_ms: int = 0
_file_count: int = 0


def _reset_index_state() -> None:
    """Test helper: reset module-level counters."""
    global _index_bytes, _last_scan_ms, _file_count
    _index_bytes = 0
    _last_scan_ms = 0
    _file_count = 0


def index_status() -> dict[str, int]:
    """Return current indexer metrics for the /v1/index/status endpoint."""
    from api import datastore

    total_rows = datastore.memory_count_by_kind("file")
    return {
        "total_chunks": total_rows,
        "file_count": _file_count,
        "index_bytes": _index_bytes,
        "last_scan_ms": _last_scan_ms,
    }


# ---------------------------------------------------------------------------
# Core indexer class
# ---------------------------------------------------------------------------


class Indexer:
    """Scan + watch directories and index file chunks into memory_fts."""

    def __init__(
        self,
        *,
        max_bytes: int = 500_000_000,
        allowed_exts: frozenset[str] | None = None,
    ) -> None:
        self._max_bytes = max_bytes
        self._allowed_exts: frozenset[str] = allowed_exts or frozenset(
            {
                ".md",
                ".txt",
                ".py",
                ".ts",
                ".tsx",
                ".js",
                ".json",
                ".yaml",
                ".yml",
                ".toml",
                ".rst",
            }
        )
        self._observer: Any | None = None  # watchdog Observer
        self._queue: asyncio.Queue[tuple[str, str]] | None = None  # (action, path)
        self._worker_task: asyncio.Task[None] | None = None
        self._scanning = False

    # ------------------------------------------------------------------ scan

    def scan(self, paths: list[str]) -> int:
        """Full synchronous scan of *paths*. Returns number of files indexed."""
        global _index_bytes, _last_scan_ms, _file_count

        from api import datastore

        t0 = time.monotonic()
        count = 0
        total_bytes = 0

        for root_str in paths:
            root = Path(root_str).expanduser()
            if not root.exists():
                logger.warning("INDEXER: path does not exist, skipping: {}", root)
                continue
            for dirpath, _dirs, files in os.walk(root):
                for fname in files:
                    fpath = Path(dirpath) / fname
                    if fpath.suffix.lower() not in self._allowed_exts:
                        continue
                    try:
                        size = fpath.stat().st_size
                    except OSError:
                        continue
                    if size > _MAX_FILE_BYTES:
                        continue
                    if total_bytes + size > self._max_bytes:
                        logger.warning(
                            "INDEXER: max_bytes ({}) reached, stopping scan",
                            self._max_bytes,
                        )
                        break
                    try:
                        text = fpath.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        continue
                    self._index_file(fpath, text, datastore)
                    total_bytes += size
                    count += 1

        elapsed = time.monotonic() - t0
        with _index_lock:
            _index_bytes = total_bytes
            _last_scan_ms = int(time.time() * 1000)
            _file_count = count
        logger.info("INDEXER: scanned {} files in {:.1f} s", count, elapsed)
        return count

    # ------------------------------------------------------------ file index

    def _index_file(
        self,
        path: Path,
        text: str,
        datastore: types.ModuleType,
    ) -> None:
        """Chunk *text* and upsert all chunks for *path* into memory_fts."""
        chunks = _token_chunks(text)
        for idx, chunk in enumerate(chunks):
            ref = f"{path}::{idx}"
            title = str(path) if idx == 0 else f"{path} (chunk {idx})"
            datastore.memory_index(
                kind="file",
                title=title,
                body=chunk,
                ref=ref,
            )

    # --------------------------------------------------------------- watch

    async def start_watch(self, paths: list[str]) -> None:
        """Start the filesystem watcher and async worker."""
        from watchdog.events import (
            FileSystemEvent,
            FileSystemEventHandler,
        )
        from watchdog.observers import Observer

        self._queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        class _Handler(FileSystemEventHandler):
            def __init__(
                self,
                queue: asyncio.Queue[tuple[str, str]],
                lp: asyncio.AbstractEventLoop,
            ) -> None:
                self._q = queue
                self._lp = lp
                self._pending: dict[str, float] = {}
                self._lock = threading.Lock()

            def _schedule(self, action: str, path: str) -> None:
                with self._lock:
                    self._pending[path] = time.monotonic()
                threading.Timer(_DEBOUNCE_S, self._flush, args=(action, path)).start()

            def _flush(self, action: str, path: str) -> None:
                with self._lock:
                    enqueued_at = self._pending.pop(path, None)
                if enqueued_at is None:
                    return
                if self._lp.is_closed():
                    return
                with contextlib.suppress(RuntimeError):
                    self._lp.call_soon_threadsafe(
                        lambda: self._q.put_nowait((action, path))
                    )

            def on_modified(self, event: FileSystemEvent) -> None:
                if not event.is_directory:
                    self._schedule("index", str(event.src_path))

            def on_created(self, event: FileSystemEvent) -> None:
                if not event.is_directory:
                    self._schedule("index", str(event.src_path))

            def on_deleted(self, event: FileSystemEvent) -> None:
                if not event.is_directory:
                    self._schedule("delete", str(event.src_path))

        handler = _Handler(self._queue, loop)
        observer = Observer()
        for path_str in paths:
            root = Path(path_str).expanduser()
            if root.exists():
                observer.schedule(handler, str(root), recursive=True)
        observer.start()
        self._observer = observer

        self._worker_task = asyncio.create_task(self._worker())
        logger.info("INDEXER: watcher started on {} paths", len(paths))

    async def _worker(self) -> None:
        """Drain the event queue and re-index changed files."""
        from api import datastore

        assert self._queue is not None
        while True:
            try:
                action, path_str = await self._queue.get()
            except asyncio.CancelledError:
                break
            path = Path(path_str)
            if action == "delete":
                try:
                    datastore.memory_delete_path(path_str)
                except Exception as exc:
                    logger.warning("INDEXER: delete failed for {}: {}", path_str, exc)
            else:
                if path.suffix.lower() not in self._allowed_exts:
                    self._queue.task_done()
                    continue
                try:
                    size = path.stat().st_size
                    if size > _MAX_FILE_BYTES:
                        self._queue.task_done()
                        continue
                    text = path.read_text(encoding="utf-8", errors="replace")
                    self._index_file(path, text, datastore)
                    global _last_scan_ms
                    _last_scan_ms = int(time.time() * 1000)
                except OSError:
                    pass
                except Exception as exc:
                    logger.warning("INDEXER: reindex failed for {}: {}", path_str, exc)
            self._queue.task_done()

    # --------------------------------------------------------------- stop

    async def stop(self) -> None:
        """Stop the watcher and worker."""
        if self._worker_task is not None:
            self._worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker_task
            self._worker_task = None
        if self._observer is not None:
            obs = self._observer
            self._observer = None
            try:
                obs.stop()  # type: ignore[attr-defined]
                obs.join(timeout=3)  # type: ignore[attr-defined]
            except Exception as exc:
                logger.warning("INDEXER: observer stop error: {}", exc)
        logger.info("INDEXER: stopped")

    # ---------------------------------------------------------------- rescan

    async def rescan(self, paths: list[str]) -> int:
        """Trigger a full rescan asynchronously (runs in thread pool)."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.scan, paths)
