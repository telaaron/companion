"""Tests for api/agent/indexer.py — RAG file indexer.

Covers:
- Scanning a tmp directory and querying indexed chunks.
- Modifying a file and observing debounced re-index via the watcher.
- Respecting max_bytes cap (files beyond the cap are skipped).
- Deleting a file removes its chunks from the index.
- path_prefix filter works on memory_search.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db(tmp_path: Path):
    """Return a fresh datastore backed by a temp SQLite file."""
    from api import datastore

    db_file = tmp_path / "test.sqlite3"
    datastore.reset_for_tests(db_file)
    return datastore


# ---------------------------------------------------------------------------
# Unit tests — chunking
# ---------------------------------------------------------------------------


class TestTokenChunks:
    def test_short_text_single_chunk(self):
        from api.agent.indexer import _token_chunks

        chunks = _token_chunks("Hello world", chunk_tokens=800)
        assert len(chunks) == 1
        assert "Hello" in chunks[0]

    def test_multi_paragraph_split(self):
        from api.agent.indexer import _token_chunks

        # Build text that exceeds 800 tokens by repeating large paragraphs
        para = "word " * 200  # ~200 tokens each
        text = "\n\n".join([para] * 6)  # 6 x 200 = 1200 tokens
        chunks = _token_chunks(text, chunk_tokens=800)
        assert len(chunks) >= 2

    def test_empty_text_returns_chunk(self):
        from api.agent.indexer import _token_chunks

        chunks = _token_chunks("", chunk_tokens=800)
        assert isinstance(chunks, list)
        # Should return at least one (possibly empty) chunk
        assert len(chunks) >= 1


# ---------------------------------------------------------------------------
# Integration tests — scan
# ---------------------------------------------------------------------------


class TestIndexerScan:
    def test_scan_indexes_markdown_files(self, tmp_path: Path):
        ds = _make_db(tmp_path)
        note = tmp_path / "notes" / "hello.md"
        note.parent.mkdir()
        note.write_text("# Hello\n\nThis is a note about pricing and cold starts.")

        from api.agent.indexer import Indexer

        indexer = Indexer()
        count = indexer.scan([str(tmp_path)])
        assert count >= 1

        rows = ds.memory_search("pricing", kind="file")
        assert len(rows) >= 1
        assert any("pricing" in (r.get("body") or "") for r in rows)

    def test_scan_skips_unlisted_extensions(self, tmp_path: Path):
        _make_db(tmp_path)
        (tmp_path / "binary.bin").write_bytes(b"\x00\x01\x02")
        (tmp_path / "image.png").write_bytes(b"PNG")
        (tmp_path / "doc.md").write_text("real content here")

        from api.agent.indexer import Indexer

        indexer = Indexer()
        count = indexer.scan([str(tmp_path)])
        assert count == 1  # only doc.md

    def test_scan_respects_max_bytes(self, tmp_path: Path):
        _make_db(tmp_path)
        # Write two files; set max_bytes just below the size of both combined
        (tmp_path / "a.md").write_text("A " * 500)
        (tmp_path / "b.md").write_text("B " * 500)

        from api.agent.indexer import Indexer

        # 1 byte max — should skip after first file
        indexer = Indexer(max_bytes=1)
        count = indexer.scan([str(tmp_path)])
        assert count == 0  # first file already exceeds 1 byte

    def test_scan_skips_large_files(self, tmp_path: Path):
        _make_db(tmp_path)
        big = tmp_path / "huge.md"
        big.write_bytes(b"x" * (256 * 1024 + 1))  # just over 256 KB

        from api.agent.indexer import Indexer

        indexer = Indexer()
        count = indexer.scan([str(tmp_path)])
        assert count == 0

    def test_scan_missing_path_is_skipped(self, tmp_path: Path):
        _make_db(tmp_path)
        from api.agent.indexer import Indexer

        indexer = Indexer()
        count = indexer.scan([str(tmp_path / "nonexistent")])
        assert count == 0

    def test_scan_multiple_chunks(self, tmp_path: Path):
        ds = _make_db(tmp_path)
        # Write a file large enough to produce multiple chunks (~800 tok each)
        para = "word " * 200  # ~200 tokens
        text = "\n\n".join([para] * 8)  # ~1600 tokens → at least 2 chunks
        (tmp_path / "big.md").write_text(text)

        from api.agent.indexer import Indexer

        indexer = Indexer()
        count = indexer.scan([str(tmp_path)])
        assert count == 1

        # Multiple chunks should be stored
        total = ds.memory_count_by_kind("file")
        assert total >= 2

    def test_scan_path_prefix_filter(self, tmp_path: Path):
        ds = _make_db(tmp_path)
        subdir = tmp_path / "sub"
        subdir.mkdir()
        (subdir / "note.md").write_text("unique_keyword_xyz in sub")
        (tmp_path / "other.md").write_text("unique_keyword_xyz in root")

        from api.agent.indexer import Indexer

        indexer = Indexer()
        indexer.scan([str(tmp_path)])

        all_hits = ds.memory_search("unique_keyword_xyz", kind="file")
        assert len(all_hits) >= 2

        filtered = ds.memory_search(
            "unique_keyword_xyz", kind="file", path_prefix=str(subdir)
        )
        assert len(filtered) >= 1
        assert all(r["ref"].startswith(str(subdir)) for r in filtered)


# ---------------------------------------------------------------------------
# Integration tests — watcher (debounced re-index)
# ---------------------------------------------------------------------------


class TestIndexerWatch:
    @pytest.mark.asyncio
    async def test_watcher_reindexes_modified_file(self, tmp_path: Path):
        ds = _make_db(tmp_path)
        note = tmp_path / "watch_me.md"
        note.write_text("original content about bananas")

        from api.agent.indexer import Indexer

        indexer = Indexer()
        indexer.scan([str(tmp_path)])

        # Verify initial index
        hits = ds.memory_search("bananas", kind="file")
        assert len(hits) >= 1

        await indexer.start_watch([str(tmp_path)])

        try:
            # Modify the file — watcher should debounce and re-index
            note.write_text("updated content about strawberries")

            # Wait up to 5 s for the re-index
            deadline = time.monotonic() + 5.0
            found = False
            while time.monotonic() < deadline:
                await asyncio.sleep(0.2)
                hits = ds.memory_search("strawberries", kind="file")
                if hits:
                    found = True
                    break
            assert found, "Re-index after file modification not detected within 5 s"
        finally:
            await indexer.stop()

    @pytest.mark.asyncio
    async def test_watcher_removes_deleted_file(self, tmp_path: Path):
        """Verify that the worker processes 'delete' events from the queue.

        We inject the event directly into the queue instead of relying on
        the filesystem observer (which can be slow on macOS FSEvents) so the
        test is deterministic on all platforms.
        """
        ds = _make_db(tmp_path)
        note = tmp_path / "delete_me.md"
        note.write_text("content about mangoes")

        from api.agent.indexer import Indexer

        indexer = Indexer()
        indexer.scan([str(tmp_path)])

        hits = ds.memory_search("mangoes", kind="file")
        assert len(hits) >= 1

        await indexer.start_watch([str(tmp_path)])

        try:
            # Inject a synthetic delete event directly so the test is
            # deterministic (filesystem delete events can be slow on macOS).
            assert indexer._queue is not None
            await indexer._queue.put(("delete", str(note)))

            # Wait up to 5 s for the worker to process it
            deadline = time.monotonic() + 5.0
            cleared = False
            while time.monotonic() < deadline:
                await asyncio.sleep(0.1)
                hits = ds.memory_search("mangoes", kind="file")
                if not hits:
                    cleared = True
                    break
            assert cleared, "Delete event not processed within 5 s"
        finally:
            await indexer.stop()

    @pytest.mark.asyncio
    async def test_stop_is_idempotent(self, tmp_path: Path):
        _make_db(tmp_path)
        from api.agent.indexer import Indexer

        indexer = Indexer()
        await indexer.start_watch([str(tmp_path)])
        await indexer.stop()
        await indexer.stop()  # second stop should not raise


# ---------------------------------------------------------------------------
# Integration tests — index_status + rescan
# ---------------------------------------------------------------------------


class TestIndexerStatus:
    def test_index_status_after_scan(self, tmp_path: Path):
        _make_db(tmp_path)
        (tmp_path / "a.md").write_text("hello status")

        from api.agent.indexer import Indexer, _reset_index_state, index_status

        _reset_index_state()
        indexer = Indexer()
        indexer.scan([str(tmp_path)])

        status = index_status()
        assert status["file_count"] == 1
        assert status["last_scan_ms"] > 0
        assert status["total_chunks"] >= 1

    @pytest.mark.asyncio
    async def test_rescan_async(self, tmp_path: Path):
        _make_db(tmp_path)
        (tmp_path / "b.md").write_text("async rescan test")

        from api.agent.indexer import Indexer, _reset_index_state

        _reset_index_state()
        indexer = Indexer()
        count = await indexer.rescan([str(tmp_path)])
        assert count >= 1
