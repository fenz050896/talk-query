# Phase 2 — Efficient Schema Retrieval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace full-table LLM ranking with embedding-based pre-filter (sqlite-vec + sentence-transformers) and add semantic query cache. Cut ranking tokens ~78% and LLM calls from 3 to 2.

**Architecture:** Embedding model (`paraphrase-multilingual-MiniLM-L12-v2`, 384d, multilingual) runs locally. sqlite-vec stores table embeddings and historical queries in `talkquery_config.db`. Flow: query → cache lookup → embedding pre-filter (top-15 tables) → LLM re-rank + generate → execute → cache store.

**Tech Stack:** sqlite-vec 0.1.9, sentence-transformers 5.5.x, sqlite3 (existing), numpy (transitive dep)

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `backend/embeddings.py` | **New** | Embedding model singleton, sqlite-vec wrapper, cache CRUD, table index search |
| `backend/analyzer.py` | Modify | Call `build_table_index()` after Tier 2 analysis completes |
| `backend/llm.py` | Modify | Add merged re-rank + SQL generation prompt (15 tables max) |
| `backend/main.py` | Modify | Replace `get_relevant_context` flow with hybrid pipeline: cache → pre-filter → re-rank |
| `backend/requirements.txt` | Modify | Add `sqlite-vec`, `sentence-transformers` |

---

### Task 1: Add dependencies and verify

**Files:**
- Modify: `backend/requirements.txt`

- [ ] **Step 1: Add dependencies to requirements.txt**

```
fastapi==0.115.0
uvicorn[standard]==0.30.6
sqlalchemy==2.0.35
openai==1.51.0
python-dotenv==1.0.1
sse-starlette==2.1.3
cryptography>=41.0.0
psycopg2-binary>=2.9.0
pymysql>=1.0.0
sqlite-vec>=0.1.9
sentence-transformers>=5.5.0
```

- [ ] **Step 2: Install and verify**

```bash
cd backend && source venv/bin/activate && pip install -r requirements.txt
```

- [ ] **Step 3: Verify imports work**

```bash
cd backend && source venv/bin/activate && python -c "
import sqlite3, sqlite_vec
db = sqlite3.connect(':memory:')
db.enable_load_extension(True)
sqlite_vec.load(db)
db.enable_load_extension(False)
db.execute('CREATE VIRTUAL TABLE t USING vec0(a float[384])')
db.execute(\"INSERT INTO t(rowid, a) VALUES (1, vec_f32('[{}]'))\".format(','.join(['0.1']*384)))
print('sqlite-vec OK')

from sentence_transformers import SentenceTransformer
model = SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')
emb = model.encode(['test'])
assert emb.shape == (1, 384), f'Expected (1,384) got {emb.shape}'
print('sentence-transformers OK')
"
```

Expected:
```
sqlite-vec OK
sentence-transformers OK
```

- [ ] **Step 5: Commit**

```bash
git add backend/requirements.txt
git commit -m "chore(deps): add sqlite-vec and sentence-transformers"
```

---

### Task 2: Create embeddings module

**Files:**
- Create: `backend/embeddings.py`

- [ ] **Step 1: Write embeddings.py**

```python
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
CACHE_SIMILARITY_THRESHOLD = 0.90

_model: Optional[SentenceTransformer] = None


def _get_model() -> SentenceTransformer:
    """Lazy-load embedding model singleton (~470MB, load once)."""
    global _model
    if _model is None:
        _model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
    return _model


def embed(texts: list[str]) -> np.ndarray:
    """Embed a list of texts. Returns (N, 384) float32 array."""
    return _get_model().encode(texts, convert_to_numpy=True)


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
        # Clear old entries for this connection
        conn.execute("DELETE FROM table_embeddings WHERE connection_id = ?", (connection_id,))
        # Delete corresponding vec entries (by rowid match)
        conn.execute("DELETE FROM table_embeddings_vec WHERE rowid IN (SELECT rowid FROM table_embeddings WHERE connection_id = ?)", (connection_id,))

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

        # L2 distance: smaller = more similar. Convert to approximate similarity.
        # Get top k * matches since we filter by connection_id after
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
            # Convert L2 distance to approximate similarity [0, 1]
            # L2(0) → sim=1, L2(2) → sim≈0
            sim = max(0.0, 1.0 - float(row["distance"]) / 2.0)
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

        sim = max(0.0, 1.0 - float(row["distance"]) / 2.0)
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
        # Delete vec entries first (by rowid from metadata table)
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
```

- [ ] **Step 2: Verify imports and basic functions**

```bash
cd backend && source venv/bin/activate && python -c "
from embeddings import embed, init_table_embeddings, init_query_cache
# Test embed
emb = embed(['hello world', 'test'])
assert emb.shape == (2, 384)
# Test table init (uses talkquery_config.db)
init_table_embeddings('test-conn')
init_query_cache()
print('All OK')
"
```

Expected: `All OK`

- [ ] **Step 3: Test table index build and search**

```bash
cd backend && source venv/bin/activate && python -c "
from embeddings import build_table_index, search_tables, embed
import uuid

conn_id = str(uuid.uuid4())
descs = {
    'products': 'Product catalog with name, price, category, and stock quantity',
    'orders': 'Customer orders with date, total amount, and status',
    'employees': 'Employee records with name, department, hire date, and salary',
    'weather_log': 'Daily weather observations with temperature, humidity, and wind speed',
}

# Build index
build_table_index(conn_id, descs)

# Search with English query
results = search_tables(conn_id, 'how many orders last month')
for r in results:
    print(f'  {r[\"table_name\"]}: sim={r[\"similarity\"]:.4f}')

# Search with Indonesian query
results = search_tables(conn_id, 'berapa gaji karyawan')
for r in results:
    print(f'  {r[\"table_name\"]}: sim={r[\"similarity\"]:.4f}')

print('Search OK')
"
```

Expected output shows `orders` and `products` high for first query, `employees` high for second.

- [ ] **Step 4: Test query cache**

```bash
cd backend && source venv/bin/activate && python -c "
from embeddings import cache_store, cache_lookup
import uuid

conn_id = str(uuid.uuid4())

# Store
cache_store(conn_id, 'how many active users?', 'SELECT COUNT(*) FROM users WHERE active=1', 'There are 42 active users.')

# Lookup — same question
result = cache_lookup(conn_id, 'how many active users?')
print('Exact match:', result['similarity'] if result else 'MISS')

# Lookup — similar question
result = cache_lookup(conn_id, 'count of active users')
print('Similar match:', result['similarity'] if result else 'MISS')

# Lookup — unrelated question (should miss or low similarity)
result = cache_lookup(conn_id, 'what is the weather today')
print('Unrelated:', result['similarity'] if result else 'MISS (expected)')

print('Cache OK')
"
```

Expected: Exact match > 0.95, Similar match > 0.85, Unrelated MISS.

- [ ] **Step 5: Commit**

```bash
git add backend/embeddings.py
git commit -m "feat(api): add sqlite-vec embedding module with table index and query cache"
```

---

### Task 3: Build table embeddings after Tier 2 analysis

**Files:**
- Modify: `backend/analyzer.py`

- [ ] **Step 1: Add embedding build call after Tier 2**

Read `backend/analyzer.py`. Find function `_analyze_database_impl` (line ~240). After the profile is saved (after `_save_profile(profile)` and before `return profile`), add:

```python
    # Build table embeddings for semantic search
    try:
        from embeddings import build_table_index
        build_table_index(connection_id, profile.table_descriptions)
    except Exception:
        pass  # Non-critical — chat still works without embeddings
```

The section after modification should look like:

```python
    profile = DatabaseProfile(
        connection_id=connection_id,
        db_type=db_type,
        db_name=db_name,
        tier1_summary=tier1_summary,
        domain_groups=domain_groups,
        table_descriptions=table_descriptions,
        table_stats=table_stats,
        schema_hash=_compute_schema_hash(tables),
    )

    _save_profile(profile)

    # Build table embeddings for semantic search
    try:
        from embeddings import build_table_index
        build_table_index(connection_id, profile.table_descriptions)
    except Exception:
        pass  # Non-critical — chat still works without embeddings

    return profile
```

- [ ] **Step 2: Add delete_connection_cache call in connection deletion**

Read `backend/connections.py`. Find `delete_connection` function (line ~164). Inside the `try` block, after the conversation check and DELETE statement, add embedding cleanup:

```python
# After conn.execute("DELETE FROM connections WHERE id = ?", (conn_id,))
try:
    from embeddings import delete_connection_cache
    delete_connection_cache(conn_id)
except Exception:
    pass
```

- [ ] **Step 3: Verify embedding build with existing analysis**

```bash
cd backend && source venv/bin/activate && python -c "
from connections import _get_local_conn, get_all_connections
from embeddings import search_tables

conns = get_all_connections()
if conns:
    conn_id = conns[0].id
    results = search_tables(conn_id, 'test query')
    print(f'Found {len(results)} tables for connection {conn_id}')
    for r in results[:5]:
        print(f'  {r[\"table_name\"]}: sim={r[\"similarity\"]:.4f}')
else:
    print('No connections yet — verify after creating one')
"
```

- [ ] **Step 4: Commit**

```bash
git add backend/analyzer.py backend/connections.py
git commit -m "feat(api): auto-build table embeddings after database analysis"
```

---

### Task 4: Add merged re-rank + SQL generation prompt to LLM module

**Files:**
- Modify: `backend/llm.py`

- [ ] **Step 1: Add re-rank prompt and function**

At the end of `backend/llm.py`, add:

```python
RE_RANK_PROMPT = """You are a SQL expert and data analyst. Given the user question and the relevant table descriptions below, select the most relevant tables and answer the question.

{context_section}

User question: {question}

Respond in this format:
- If the question asks ABOUT the database (purpose, structure, relationships, patterns):
  EXPLAIN: <natural language answer, in the same language as the question>

- If the question asks for specific DATA (list, count, filter, aggregate):
  TABLES: <list relevant table names, comma separated>
  SELECT <SQL query>

SQL rules:
- Only SELECT. No INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE.
- Always add LIMIT 100 if user doesn't specify a limit."""


async def generate_with_re_rank(
    question: str,
    db_context: str,
    relevant_tables: list[dict],
    style: str = "normal",
) -> tuple[str, str]:
    """Re-rank pre-filtered tables + generate SQL/EXPLAIN in one LLM call.

    Args:
        question: User's natural language question
        db_context: Tier 1 database summary
        relevant_tables: List of {table_name, description} from embedding search (top 15)
        style: Response style

    Returns:
        (type, content) where type is "sql", "explain", or "error"
    """
    # Build the subset of table descriptions
    table_lines = []
    for t in relevant_tables:
        table_lines.append(f"- {t['table_name']}: {t['description']}")

    if table_lines:
        context_section = f"""Database Summary:
{db_context}

Relevant Tables:
{chr(10).join(table_lines)}"""
    else:
        context_section = f"Database Summary:\n{db_context}"

    user_message = question
    if style in ("rtk", "caveman+rtk"):
        user_message = f"Terse query: {question}"

    response = await client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": RE_RANK_PROMPT.format(
                context_section=context_section,
                question=user_message,
            )},
        ],
        temperature=0,
        max_tokens=1000,
    )
    content = response.choices[0].message.content.strip()

    # Parse response
    upper = content.upper()
    if upper.startswith("EXPLAIN:"):
        return ("explain", content[len("EXPLAIN:"):].strip())
    elif "SELECT" in upper or upper.startswith("WITH"):
        # Extract SQL — might have TABLES: prefix
        sql = content
        if "SELECT" in sql:
            idx = sql.upper().index("SELECT")
            sql = sql[idx:]
        sql = _clean_sql(sql)
        return ("sql", sql)
    else:
        return ("explain", content)
```

- [ ] **Step 2: Verify the function compiles**

```bash
cd backend && source venv/bin/activate && python -c "
from llm import generate_with_re_rank
print('Function import OK')
"
```

- [ ] **Step 3: Commit**

```bash
git add backend/llm.py
git commit -m "feat(api): add merged LLM re-rank + SQL generation prompt"
```

---

### Task 5: Wire hybrid pipeline into main chat flow

**Files:**
- Modify: `backend/main.py`

- [ ] **Step 1: Replace context assembly in stream_chat**

Read `backend/main.py`. Find `stream_chat` function. The current flow (lines ~186-214) does:

```python
if profile:
    context = await get_relevant_context(profile, message, engine)
    schema = ...
else:
    schema = ...
```

Replace the entire `if profile:` block with the hybrid pipeline:

```python
if profile:
    # Hybrid pipeline: cache → embedding pre-filter → LLM re-rank
    cache_hit = None
    try:
        from embeddings import cache_lookup, search_tables
        cache_hit = cache_lookup(conn_row["id"], message)
    except Exception:
        pass

    if cache_hit:
        # Cache hit: reuse cached SQL, skip LLM for SQL generation
        yield _sse_event({"type": "sql", "sql": cache_hit["sql"]})
        yield _sse_event({"type": "status", "status": "executing"})

        try:
            results = await asyncio.to_thread(execute_sql, cache_hit["sql"], QUERY_TIMEOUT, engine, conn_row["db_type"])
        except Exception as e:
            yield _sse_event({"type": "status", "status": "generating_response"})
            # SQL might be stale — regenerate via LLM
            cache_hit = None

        if cache_hit:
            yield _sse_event({"type": "status", "status": "generating_response"})
            response = await generate_response(message, cache_hit["sql"], results, style)
            output = format_output(message, cache_hit["sql"], results, response)
            yield _sse_event({"type": "result", **output})
            yield _sse_event({"type": "done"})

            if conversation_id:
                try:
                    save_message(conversation_id, "user", message, style=style)
                    save_message(conversation_id, "assistant", response,
                                sql=cache_hit["sql"],
                                result_json=json.dumps({
                                    "columns": results["columns"],
                                    "rows": results["rows"][:20],
                                    "row_count": results["row_count"],
                                }),
                                style=style)
                except Exception:
                    pass
            return

    # Cache miss: embedding pre-filter → LLM re-rank + generate
    try:
        relevant = search_tables(conn_row["id"], message, k=15)
    except Exception:
        relevant = []

    # Get Tier 1 context
    tier1_context = profile.tier1_summary

    # Build relevant table descriptions list for the LLM
    relevant_tables = []
    if relevant:
        for r in relevant:
            relevant_tables.append({
                "table_name": r["table_name"],
                "description": r.get("description", ""),
            })
    else:
        # Fallback: no embeddings yet, use all tables from profile
        for name, desc in profile.table_descriptions.items():
            relevant_tables.append({"table_name": name, "description": desc})

    schema = get_schema_compact(engine) if style in ("rtk", "caveman+rtk") else get_schema_ddl(engine)

    # Multi-turn context
    llm_message = message
    if conversation_id:
        context_msgs = get_conversation_context(conversation_id)
        if context_msgs:
            ctx_text = _format_context(context_msgs)
            if style in ("rtk", "caveman+rtk"):
                llm_message = f"Context:\n{ctx_text}\n\nTerse query: {message}"
            else:
                llm_message = f"{ctx_text}\n\nCurrent question: {message}"
```

Then replace the LLM call section (the `try:` block that calls `generate_answer` / `generate_sql`):

```python
    try:
        if profile:
            answer_type, answer_content = await generate_with_re_rank(
                llm_message, tier1_context, relevant_tables, style,
            )
        else:
            answer_type = "sql"
            answer_content = await generate_sql(llm_message, schema, style)
    except Exception as e:
        yield _sse_event({"type": "error", "message": f"LLM Error: {str(e)}"})
        yield _sse_event({"type": "done"})
        return
```

- [ ] **Step 2: Add cache_store after successful responses**

Find the section after `generate_response` succeeds (around line ~258 in current code). After the result is formatted and persisted, add cache storage:

```python
    # Store successful query in cache
    try:
        from embeddings import cache_store
        await asyncio.to_thread(cache_store, conn_row["id"], message, sql, response)
    except Exception:
        pass
```

- [ ] **Step 3: Update import at top of main.py**

Add to the imports at the top of `backend/main.py`:

```python
from llm import generate_sql, generate_response, generate_answer, generate_with_re_rank
```

- [ ] **Step 4: Verify the full flow**

```bash
cd backend && source venv/bin/activate && python -c "
import main  # Verify no import errors
print('main.py imports OK')
"
```

- [ ] **Step 5: Commit**

```bash
git add backend/main.py
git commit -m "feat(api): wire hybrid schema retrieval pipeline into chat flow"
```

---

### Task 6: End-to-end verification

- [ ] **Step 1: Start backend**

```bash
cd backend && source venv/bin/activate && python main.py &
sleep 3
```

- [ ] **Step 2: Trigger database analysis to build embeddings**

```bash
# Get first connection ID
CONN_ID=$(curl -s http://localhost:8000/api/connections | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['connections'][0]['id'])")
echo "Connection: $CONN_ID"

# Trigger analysis
curl -s -X POST "http://localhost:8000/api/connections/$CONN_ID/analyze?force=true" | python3 -m json.tool
```

Expected: `"status": "ok"` with tables_analyzed count.

- [ ] **Step 3: Test chat with semantic table matching**

```bash
curl -s -X POST http://localhost:8000/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message": "tabel apa saja yang ada di database ini?"}' \
  --no-buffer
```

Expected: SSE events with `type: "result"`, natural language listing the tables.

- [ ] **Step 4: Test cache hit**

```bash
# Send same question twice
for i in 1 2; do
  echo "=== Request $i ==="
  curl -s -X POST http://localhost:8000/api/chat \
    -H 'Content-Type: application/json' \
    -d '{"message": "berapa total baris di tabel terbesar?"}' \
    --no-buffer | head -5
  sleep 1
done
```

Second request should be faster (cache hit avoids embedding search + LLM SQL generation).

- [ ] **Step 5: Stop backend**

```bash
kill %1 2>/dev/null || true
```

- [ ] **Step 6: Commit any remaining changes**

```bash
git status
```
