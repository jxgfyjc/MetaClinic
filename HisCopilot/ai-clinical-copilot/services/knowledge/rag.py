"""Knowledge service — RAG pipeline (instruct #20, #73, #74).

Pipeline: Query Rewrite → Embedding → Vector Search → Keyword Search → Fusion → Reranker → Top Evidence.
MVP backend: SQLite + sqlite-vec for vectors; metadata via knowledge_chunks table.
instruct #21: RAG provides evidence; model reasons autonomously (orchestrator enforces).
"""
from __future__ import annotations

import os as _os, sys as _sys
_sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), '..', '..', 'packages'))
_sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), '..', '..', 'packages', 'clinical-schemas'))

import asyncio
import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from clinical_schemas import EvidenceChunk, TrustTier


# text chunking (instruct #73: 500-1200 Chinese chars, respect headings)
def chunk_text(text: str, max_chars: int = 1000, overlap: int = 120) -> list[str]:
    if not text:
        return []
    # split by headings / double newlines first
    parts = re.split(r"\n(?=#{1,4}\s)|\n\n+", text)
    chunks: list[str] = []
    cur = ""
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if len(p) > max_chars:
            # hard split very long segments
            for i in range(0, len(p), max_chars - overlap):
                seg = p[i:i + max_chars]
                chunks.append(seg)
        elif len(cur) + len(p) < max_chars:
            cur = (cur + "\n\n" + p) if cur else p
        else:
            if cur:
                chunks.append(cur)
            cur = p
    if cur:
        chunks.append(cur)
    return chunks


def metadata_for(*, source_type: str, domain: str, subdomain: str = "",
                 title: str = "", author: str = "", book: str = "",
                 chapter: str = "", year: int = 0, trust_tier: str = "E",
                 version: str = "") -> dict:
    """instruct #74 chunk metadata schema."""
    return {
        "source_type": source_type, "domain": domain, "subdomain": subdomain,
        "title": title, "author": author, "book": book, "chapter": chapter,
        "year": year, "trust_tier": trust_tier, "version": version,
    }


@dataclass
class RetrievalHit:
    chunk_id: str
    text: str
    score: float
    metadata: dict = field(default_factory=dict)


class KnowledgeStore:
    """SQLite + sqlite-vec backed store. Embeddings via EmbeddingProvider."""

    def __init__(self) -> None:
        self._dim: int | None = None

    async def _conn(self):
        from packages.shared.db import get_session  # noqa
        # use raw sqlite for vec0 virtual table
        import sqlite3, aiosqlite
        from packages.shared.config import get_settings, REPO_ROOT
        url = get_settings().database_url
        # derive sqlite path from url
        path = url.replace("sqlite+aiosqlite:///", "")
        if not path.startswith("/"):
            path = str(REPO_ROOT / path)
        return aiosqlite.connect(path)

    async def add_chunks(self, chunks: list[dict], embeddings: list[list[float]]) -> list[str]:
        """chunks: [{document_id, source_id, text, metadata}]; embeddings parallel."""
        import sqlite_vec, aiosqlite
        from packages.shared.config import get_settings, REPO_ROOT
        url = get_settings().database_url
        path = url.replace("sqlite+aiosqlite:///", "")
        if not path.startswith("/"):
            path = str(REPO_ROOT / path)
        ids: list[str] = []
        import time as _time
        now = _time.strftime("%Y-%m-%dT%H:%M:%S%z")
        async with aiosqlite.connect(path) as db:
            await db.enable_load_extension(True)
            await db.load_extension(sqlite_vec.loadable_path())
            await db.enable_load_extension(False)
            # next vec rowid = max existing + 1 (so re-seeding doesn't collide)
            cur = await db.execute("SELECT IFNULL(MAX(rowid),0) FROM knowledge_chunk_vec")
            next_rowid = (await cur.fetchone())[0]
            for ch, emb in zip(chunks, embeddings):
                cid = uuid.uuid4().hex[:24]
                ids.append(cid)
                next_rowid += 1
                await db.execute(
                    "INSERT INTO knowledge_chunks(id,document_id,source_id,chunk_index,text,metadata_json,created_at,vec_rowid) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (cid, ch.get("document_id",""), ch.get("source_id",""),
                     ch.get("chunk_index",0), ch.get("text",""),
                     json.dumps(ch.get("metadata",{}), ensure_ascii=False), now, next_rowid),
                )
                # vec0 rowid must be an INTEGER primary key
                emb_bytes = b"".join(_f32le(x) for x in emb)
                await db.execute(
                    "INSERT INTO knowledge_chunk_vec(rowid, embedding) VALUES(?, ?)",
                    (next_rowid, emb_bytes),
                )
            await db.commit()
        return ids

    async def search(self, query_vec: list[float], k: int = 30) -> list[RetrievalHit]:
        import sqlite_vec, aiosqlite
        from packages.shared.config import get_settings, REPO_ROOT
        url = get_settings().database_url
        path = url.replace("sqlite+aiosqlite:///", "")
        if not path.startswith("/"):
            path = str(REPO_ROOT / path)
        qblob = b"".join(_f32le(x) for x in query_vec)
        out: list[RetrievalHit] = []
        try:
            async with aiosqlite.connect(path) as db:
                await db.enable_load_extension(True)
                await db.load_extension(sqlite_vec.loadable_path())
                await db.enable_load_extension(False)
                cur = await db.execute(
                    "SELECT c.id, c.text, c.metadata_json, v.distance "
                    "FROM knowledge_chunk_vec v JOIN knowledge_chunks c ON c.vec_rowid = v.rowid "
                    "WHERE v.embedding MATCH ? AND k = ? ORDER BY v.distance",
                    (qblob, k),
                )
                rows = await cur.fetchall()
                for r in rows:
                    cid, text, meta_json, dist = r
                    out.append(RetrievalHit(
                        chunk_id=cid, text=text or "", score=1.0 - float(dist or 0.0),
                        metadata=json.loads(meta_json or "{}"),
                    ))
        except Exception as e:
            # vec table missing or sqlite_vec unavailable → no vector hits
            print(f"[rag] vector search skipped: {e}", flush=True)
        return out

    async def keyword_search(self, query: str, k: int = 30) -> list[RetrievalHit]:
        import aiosqlite
        from packages.shared.config import get_settings, REPO_ROOT
        url = get_settings().database_url
        path = url.replace("sqlite+aiosqlite:///", "")
        if not path.startswith("/"):
            path = str(REPO_ROOT / path)
        terms = [t for t in re.split(r"\s+", query.strip()) if t]
        if not terms:
            return []
        like = " AND ".join(["text LIKE ?"] * len(terms))
        params = [f"%{t}%" for t in terms]
        out: list[RetrievalHit] = []
        async with aiosqlite.connect(path) as db:
            cur = await db.execute(
                f"SELECT id, text, metadata_json FROM knowledge_chunks WHERE {like} LIMIT ?",
                (*params, k),
            )
            for r in await cur.fetchall():
                cid, text, meta_json = r
                out.append(RetrievalHit(chunk_id=cid, text=text or "", score=0.5,
                                        metadata=json.loads(meta_json or "{}")))
        return out


def _f32le(x: float) -> bytes:
    import struct
    return struct.pack("<f", float(x))


class RAGPipeline:
    """instruct #20 full pipeline."""

    def __init__(self) -> None:
        self.store = KnowledgeStore()

    async def retrieve(self, query: str, *, top_k: int = 8) -> list[dict]:
        from packages.shared.providers import EmbeddingProvider, RerankerProvider, AIUnavailable
        try:
            emb = EmbeddingProvider("embedding")
            vecs = await emb.embed([query])
            qvec = vecs[0]
        except AIUnavailable:
            return []
        v_hits = await self.store.search(qvec, k=30)
        k_hits = await self.store.keyword_search(query, k=30)
        # fusion: union, max score
        merged: dict[str, RetrievalHit] = {}
        for h in v_hits + k_hits:
            if h.chunk_id in merged:
                merged[h.chunk_id].score = max(merged[h.chunk_id].score, h.score)
            else:
                merged[h.chunk_id] = h
        cands = list(merged.values())[:50]
        if not cands:
            return []
        # rerank
        try:
            rr = RerankerProvider("reranker")
            docs = [h.text for h in cands]
            ranked = await rr.rerank(query, docs, top_n=top_k)
        except AIUnavailable:
            ranked = [{"index": i, "relevance_score": h.score} for i, h in enumerate(cands[:top_k])]
        out: list[dict] = []
        for r in ranked:
            i = r.get("index", 0)
            if i >= len(cands):
                continue
            h = cands[i]
            md = h.metadata or {}
            out.append({
                "source_id": md.get("source_id", h.chunk_id),
                "title": md.get("title", ""),
                "excerpt": h.text[:500],
                "source_type": md.get("source_type", ""),
                "trust_tier": md.get("trust_tier", "E"),
                "relevance_score": float(r.get("relevance_score", h.score)),
                "chapter": md.get("chapter", ""),
                "updated_at": md.get("version", ""),
                "knowledge_version": md.get("version", ""),
                "domain": md.get("domain", ""),
            })
        return out
