"""Vector embeddings and semantic search via sqlite-vec + sentence-transformers."""
from __future__ import annotations

import json
import sqlite3
from typing import Optional

import numpy as np
import sqlite_vec
from sentence_transformers import SentenceTransformer

from connections import _get_local_conn

VECTOR_DIM = 384
CACHE_SIMILARITY_THRESHOLD = 0.85

_model: Optional[SentenceTransformer] = None


def _get_model() -> SentenceTransformer:
    """Lazy-load embedding model singleton (~470MB, load once)."""
    global _model
    if _model is None:
        try:
            _model = SentenceTransformer(
                "paraphrase-multilingual-MiniLM-L12-v2",
                local_files_only=True,
            )
        except Exception:
            _model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
    return _model


def embed(texts: list[str]) -> np.ndarray:
    """Embed a list of texts. Returns (N, 384) float32 L2-normalized array."""
    embeddings = _get_model().encode(texts, convert_to_numpy=True)
    # L2 normalize so L2 distance maps to [0,2] and 1-d/2 gives valid cosine similarity
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    return embeddings / norms


def _ensure_vec_loaded(db: sqlite3.Connection):
    """Ensure sqlite-vec extension is loaded on this connection."""
    db.enable_load_extension(True)
    sqlite_vec.load(db)
    db.enable_load_extension(False)


def _vec_f32(arr: np.ndarray) -> str:
    """Convert numpy array to vec_f32 literal string."""
    inner = ",".join(str(float(v)) for v in arr)
    return f"vec_f32('[{inner}]')"


def init_table_embeddings(connection_id: str):
    """Create virtual table for table embeddings if not exists."""
    conn = _get_local_conn()
    try:
        _ensure_vec_loaded(conn)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS table_embeddings (
                connection_id TEXT NOT NULL,
                table_name TEXT NOT NULL,
                description TEXT NOT NULL,
                PRIMARY KEY (connection_id, table_name)
            )
        """)
        conn.execute(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS table_embeddings_vec USING vec0(
                embedding float[{VECTOR_DIM}]
            )
        """)
        conn.commit()
    finally:
        conn.close()


def init_query_cache():
    """Create tables for semantic query cache."""
    conn = _get_local_conn()
    try:
        _ensure_vec_loaded(conn)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS query_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                connection_id TEXT NOT NULL,
                question TEXT NOT NULL,
                sql TEXT NOT NULL,
                response TEXT NOT NULL,
                hit_count INTEGER DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS query_cache_vec USING vec0(
                embedding float[{VECTOR_DIM}]
            )
        """)
        conn.commit()
    finally:
        conn.close()


def build_table_index(connection_id: str, table_descriptions: dict[str, str]):
    """Build or rebuild embedding index for all table descriptions of a connection.

    Clears old entries, embeds all descriptions, stores in vec table.
    """
    if not table_descriptions:
        return

    init_table_embeddings(connection_id)
    conn = _get_local_conn()
    _ensure_vec_loaded(conn)

    try:
        # Clear old vec entries first (before metadata — FK via rowid matching)
        conn.execute("DELETE FROM table_embeddings_vec WHERE rowid IN (SELECT rowid FROM table_embeddings WHERE connection_id = ?)", (connection_id,))
        # Then clear metadata
        conn.execute("DELETE FROM table_embeddings WHERE connection_id = ?", (connection_id,))

        texts = []
        table_names = list(table_descriptions.keys())
        for name in table_names:
            texts.append(table_descriptions[name])

        embeddings = embed(texts)

        for i, name in enumerate(table_names):
            vec_literal = _vec_f32(embeddings[i])
            cursor = conn.execute(
                "INSERT INTO table_embeddings (connection_id, table_name, description) VALUES (?, ?, ?)",
                (connection_id, name, table_descriptions[name]),
            )
            rowid = cursor.lastrowid
            conn.execute(
                f"INSERT INTO table_embeddings_vec (rowid, embedding) VALUES (?, {vec_literal})",
                (rowid,),
            )

        conn.commit()
    finally:
        conn.close()


def search_tables(connection_id: str, question: str, k: int = 15) -> list[dict]:
    """Search for tables relevant to the question via cosine similarity (using L2 distance).

    Returns list of {table_name, description, similarity}.
    """
    init_table_embeddings(connection_id)
    conn = _get_local_conn()
    _ensure_vec_loaded(conn)

    try:
        question_emb = embed([question])[0]
        vec_literal = _vec_f32(question_emb)

        rows = conn.execute(f"""
            SELECT
                te.table_name,
                te.description,
                vec_distance_L2(tev.embedding, {vec_literal}) AS distance
            FROM table_embeddings_vec tev
            JOIN table_embeddings te ON tev.rowid = te.rowid
            WHERE te.connection_id = ?
            ORDER BY distance ASC
            LIMIT ?
        """, (connection_id, k)).fetchall()

        results = []
        for row in rows:
            # For L2-normalized vectors: cosine_sim = 1 - d²/2
            d = float(row["distance"])
            sim = max(0.0, 1.0 - (d * d) / 2.0)
            results.append({
                "table_name": row["table_name"],
                "description": row["description"],
                "similarity": round(sim, 4),
            })

        return results
    finally:
        conn.close()


def cache_lookup(connection_id: str, question: str, threshold: float = CACHE_SIMILARITY_THRESHOLD) -> Optional[dict]:
    """Search query cache for a semantically similar question. Returns cached dict or None."""
    init_query_cache()
    conn = _get_local_conn()
    _ensure_vec_loaded(conn)

    try:
        question_emb = embed([question])[0]
        vec_literal = _vec_f32(question_emb)

        row = conn.execute(f"""
            SELECT
                qc.id,
                qc.question,
                qc.sql,
                qc.response,
                vec_distance_L2(qcv.embedding, {vec_literal}) AS distance
            FROM query_cache_vec qcv
            JOIN query_cache qc ON qcv.rowid = qc.id
            WHERE qc.connection_id = ?
            ORDER BY distance ASC
            LIMIT 1
        """, (connection_id,)).fetchone()

        if not row:
            return None

        d = float(row["distance"])
        sim = max(0.0, 1.0 - (d * d) / 2.0)
        if sim < threshold:
            return None

        # Update hit count
        conn.execute(
            "UPDATE query_cache SET hit_count = hit_count + 1, updated_at = datetime('now') WHERE id = ?",
            (row["id"],),
        )
        conn.commit()

        return {"question": row["question"], "sql": row["sql"], "response": row["response"], "similarity": round(sim, 4)}
    finally:
        conn.close()


def cache_store(connection_id: str, question: str, sql: str, response: str):
    """Store a successful Q&A pair in the query cache."""
    init_query_cache()
    conn = _get_local_conn()
    _ensure_vec_loaded(conn)

    try:
        question_emb = embed([question])[0]
        vec_literal = _vec_f32(question_emb)

        cursor = conn.execute(
            "INSERT INTO query_cache (connection_id, question, sql, response) VALUES (?, ?, ?, ?)",
            (connection_id, question, sql, response),
        )
        rowid = cursor.lastrowid
        conn.execute(
            f"INSERT INTO query_cache_vec (rowid, embedding) VALUES (?, {vec_literal})",
            (rowid,),
        )
        conn.commit()
    finally:
        conn.close()


def delete_connection_cache(connection_id: str):
    """Delete all embeddings + cache entries for a connection (called on connection delete)."""
    conn = _get_local_conn()
    _ensure_vec_loaded(conn)

    try:
        conn.execute(
            "DELETE FROM table_embeddings_vec WHERE rowid IN (SELECT rowid FROM table_embeddings WHERE connection_id = ?)",
            (connection_id,),
        )
        conn.execute("DELETE FROM table_embeddings WHERE connection_id = ?", (connection_id,))
        conn.execute(
            "DELETE FROM query_cache_vec WHERE rowid IN (SELECT id FROM query_cache WHERE connection_id = ?)",
            (connection_id,),
        )
        conn.execute("DELETE FROM query_cache WHERE connection_id = ?", (connection_id,))
        conn.commit()
    finally:
        conn.close()
