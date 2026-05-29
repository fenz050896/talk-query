# Talk-Query — Arsitektur & Referensi Teknikal

## Overview

Natural language ke SQL chat application. User bertanya dalam bahasa natural (Indonesia/Inggris), sistem menerjemahkan ke SQL via LLM, mengeksekusi di database user, mengembalikan jawaban natural.

## Arsitektur Fisik

```
[Browser] ──nginx── [Next.js :3001] ──SSE── [FastAPI :8001] ── [LLM DeepSeek]
                         │                       │
                         └───────────────────────┤
                                                 │
                              ┌──────────────────┼──────────────────┐
                              │                  │                  │
                         [SQLite]          [PostgreSQL]         [MySQL]
                    (talkquery_config.db)  (user databases)  (user databases)
```

## Workflow Per Pertanyaan

```
User: "berapa pesanan bulan lalu?"
        │
        ▼
┌─────────────────────────────────────────────────────────┐
│ 1. Query Cache Lookup (sqlite-vec, < 50ms, lokal)       │
│    Embed pertanyaan → L2 distance search → kalau mirip   │
│    > 0.85, reuse SQL lama. Skip LLM.                    │
│    Cache miss → lanjut                                  │
└─────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────┐
│ 2. Table Pre-filter (sqlite-vec, < 50ms, lokal)         │
│    Table descriptions sudah di-embed offline.            │
│    Embed pertanyaan → cosine similarity → top-15 tabel.  │
│    "pesanan" (ID) cocok ke "orders" (EN).               │
└─────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────┐
│ 3. LLM Re-rank + Generate (DeepSeek, ~1.5s)             │
│    Kirim: Tier 1 summary + 15 tabel + pertanyaan.       │
│    LLM pilih tabel relevan + generate SQL/EXPLAIN.      │
│    2 tipe response:                                     │
│    - EXPLAIN: "Database ini berisi 7 tabel..."          │
│    - SELECT:  SELECT COUNT(*) FROM orders LIMIT 100     │
└─────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────┐
│ 4. Validate + Execute                                   │
│    - Cek hanya SELECT/WITH                              │
│    - Auto LIMIT 100 kalau belum ada                      │
│    - Execute di engine koneksi (SQLite/PostgreSQL/MySQL) │
│    - Timeout 10 detik                                   │
└─────────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────┐
│ 5. LLM Response + Cache Store                           │
│    - Hasil query → LLM → ringkasan natural (ID/EN)      │
│    - Simpan (pertanyaan, SQL, response) ke query cache   │
│    - Stream SSE: status → sql → executing → result      │
└─────────────────────────────────────────────────────────┘
```

### Token Budget

| Stage | Sebelum Phase 2 | Sesudah Phase 2 |
|-------|-----------------|-----------------|
| Table ranking | ~7,000 (LLM) | 0 (lokal, 50ms) |
| Generate | ~500 (LLM) | ~1,500 (LLM, merged) |
| Response | ~300 (LLM) | ~300 (LLM) |
| **Total** | ~7,800 token, 3 LLM call | ~1,800 token, 2 LLM call |
| **Cache hit** | — | ~300 token, 1 LLM call |

---

## 1. LLM Client (`backend/llm.py`)

Provider-agnostic via OpenAI SDK. Ganti provider tinggal ubah 3 env var: `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`.

```python
from openai import AsyncOpenAI

client = AsyncOpenAI(
    api_key=os.getenv("LLM_API_KEY"),
    base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1"),
)
MODEL = os.getenv("LLM_MODEL", "deepseek-chat")
```

### Prompt

**`SYSTEM_PROMPT`** — fallback kalau gak ada database profile:
- DDL schema dimasukkan langsung
- Hanya SELECT, auto LIMIT 100
- Return ONLY SQL, no markdown

**`SYSTEM_PROMPT_WITH_CONTEXT`** — normal flow, ada profile:
- Database context (Tier 1) + schema relevan
- 2 response format: `EXPLAIN: <jawaban>` atau `SELECT <query>`

**`RE_RANK_PROMPT`** (Phase 2) — dengan pre-filtered 15 tabel:
- Hanya 15 tabel dikirim
- LLM pilih yang relevan + generate SQL/EXPLAIN dalam 1 call

### Fungsi

| Fungsi | Input | Output | Use |
|--------|-------|--------|-----|
| `generate_sql()` | question, schema | SQL string | Fallback tanpa profile |
| `generate_answer()` | question, schema, context | (type, content) | Phase 1 — semua tabel |
| `generate_with_re_rank()` | question, db_context, relevant_tables | (type, content) | Phase 2 — 15 tabel |
| `generate_response()` | question, sql, results | ringkasan natural | Semua flow |
| `_clean_sql()` | raw LLM output | SQL bersih | Strip markdown fences |

---

## 2. Schema Extractor (`backend/schema.py`)

Pakai SQLAlchemy `inspect()` — baca metadata database, bukan parse DDL.

```python
inspector = inspect(engine)
tables = inspector.get_table_names()
columns = inspector.get_columns("table_name")
fks = inspector.get_foreign_keys("table_name")
```

### Fungsi Output

| Fungsi | Format | Use |
|--------|--------|-----|
| `get_schema_ddl(engine)` | CREATE TABLE statements + row count | Prompt LLM normal |
| `get_schema_compact(engine)` | One-line per tabel | RTK/caveman mode |
| `get_tables_structured(engine)` | List of dict | Schema explorer UI |
| `get_table_sample(engine, table, limit=5)` | Sample rows dict | Analyzer input |

Semua fungsi terima `engine` parameter — bisa di-inject untuk koneksi mana aja.

---

## 3. SQL Executor (`backend/db.py`)

Satu fungsi, 22 baris. 3 safety layer per DB type:

```python
def execute_sql(sql, timeout, engine, db_type):
    with engine.connect() as conn:
        if db_type == "sqlite":
            conn.execute(text("PRAGMA query_only = ON"))
        elif db_type == "postgresql":
            conn.execute(text(f"SET statement_timeout = '{timeout * 1000}'"))
        elif db_type == "mysql":
            conn.execute(text(f"SET SESSION max_execution_time = {timeout * 1000}"))
        
        result = conn.execute(text(sql))
        rows = result.fetchall()
        columns = list(result.keys())
        return {"columns": columns, "rows": [dict(zip(columns, r)) for r in rows], "row_count": len(rows)}
```

### Defense in Depth

1. **Regex validator** (`validate_sql()` di main.py) — cek keyword + auto LIMIT
2. **Database-level constraint** — PRAGMA query_only, default_transaction_read_only, max_execution_time
3. Kalau satu layer tembus, layer berikutnya masih aktif

### Forbidden Keywords

INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, CREATE, REPLACE, GRANT, REVOKE, EXEC, EXECUTE, ATTACH, DETACH

---

## 4. Database Analyzer (`backend/analyzer.py`)

Bikin AI "paham" database — bukan cuma lihat DDL, tapi ngerti konteks bisnis.

### Tiga Tier

```
Tier 1: Database Summary (~500 token, SELALU di prompt)
   ↓
Tier 2: Per-Table Descriptions (~300 token/tabel, DI-RANKING on demand)
   ↓
Tier 3: Column Semantic Profile (deferred, belum implementasi)
```

### Proses Analysis

1. `get_tables_structured(engine)` → semua tabel + kolom + FK + row count
2. **Tier 1** (LLM): kirim schema overview + sample data → output: PURPOSE, DOMAINS, STATS, NOTES
3. **Tier 2** (LLM, parallel batch 10): per tabel dikirim kolom + FK + sample → deskripsi 2-4 kalimat
4. Disimpan di `database_profiles` table

### Data Structure

```python
@dataclass
class DatabaseProfile:
    connection_id: str
    tier1_summary: str                    # "PURPOSE: ... DOMAINS: ... STATS: ..."
    domain_groups: dict[str, list[str]]   # {"Inventory": ["products", "stock"], "Sales": ["orders"]}
    table_descriptions: dict[str, str]    # {"orders": "Customer orders with...", ...}
    table_stats: dict[str, dict]          # {"orders": {row_count: 20, ...}}
    schema_hash: str                      # SHA256 first 16 chars
```

### Validitas

Hash dari nama tabel + nama kolom. Kalau struktur berubah → hash beda → re-analysis. Ubah data isi gak trigger.

Phase 2 tambahan: cek embeddings exist. Kalau belum (upgrade dari versi lama) → force rebuild.

---

## 5. Embeddings Module (`backend/embeddings.py`)

Jantung Phase 2. Ganti LLM ranking (~7,000 token) dengan vector search lokal (<50ms, 0 token).

### Embedding Model

```python
_model = SentenceTransformer(
    "paraphrase-multilingual-MiniLM-L12-v2",
    local_files_only=True,  # gak cek HF Hub setelah download
)
```

- 384 dimensi, ~470MB
- Cross-lingual: Indonesia ↔ Inggris
- L2-normalized: distance 0 = identik, 2 = berlawanan
- Fallback: download dari HF Hub kalau pertama kali

### Vector Storage

```sql
-- Metadata
CREATE TABLE table_embeddings (
    connection_id TEXT, table_name TEXT, description TEXT,
    PRIMARY KEY (connection_id, table_name)
);

-- sqlite-vec virtual table (vector index)
CREATE VIRTUAL TABLE table_embeddings_vec USING vec0(
    embedding float[384]
);

-- Query cache
CREATE TABLE query_cache (
    id INTEGER PRIMARY KEY, connection_id TEXT,
    question TEXT, sql TEXT, response TEXT, hit_count INTEGER DEFAULT 1
);

CREATE VIRTUAL TABLE query_cache_vec USING vec0(
    embedding float[384]
);
```

### Fungsi Kunci

| Fungsi | Purpose |
|--------|---------|
| `embed(texts)` | Text → (N, 384) float32, L2-normalized |
| `build_table_index(conn_id, descriptions)` | Embed + simpan semua table descriptions |
| `search_tables(conn_id, question, k=15)` | Cosine similarity search, return top-K |
| `cache_lookup(conn_id, question)` | Cek query cache, return cached SQL/response |
| `cache_store(conn_id, question, sql, response)` | Simpan Q&A ke cache |
| `delete_connection_cache(conn_id)` | Bersihin semua data untuk koneksi |

### Similarity Formula

```python
d = float(row["distance"])           # L2 distance dari sqlite-vec
sim = max(0.0, 1.0 - (d * d) / 2.0)  # Konversi ke cosine similarity (L2-normalized vectors)
```

Kenapa L2 bukan cosine? sqlite-vec 0.1.9 cuma support `vec_distance_L2`. Cosine dihitung post-query, cuma untuk 15 hasil.

---

## 6. Connection Manager (`backend/connections.py` + `crypto.py`)

### Crypto

```python
# Key dari file (auto-generate), bukan env var
KEY_PATH = "backend/.fernet_key"

def encrypt_password(password: str) -> str: ...
def decrypt_password(encrypted: str) -> str: ...
```

- Fernet AES-128-CBC + HMAC-SHA256
- Key persisten di file, di `.gitignore`
- Password tidak pernah di response API

### Schema

```sql
CREATE TABLE connections (
    id TEXT PRIMARY KEY,           -- UUID
    name TEXT NOT NULL,            -- "Production PG"
    db_type TEXT NOT NULL,         -- 'sqlite' | 'postgresql' | 'mysql'
    host TEXT,
    port INTEGER,
    database_name TEXT NOT NULL,
    username TEXT,
    password_encrypted TEXT,       -- Fernet
    ssl_mode TEXT DEFAULT 'prefer',
    last_used_at TEXT,             -- chat-first model, bukan is_active
    created_at, updated_at
);
```

### Engine Cache

```python
_engine_cache: dict[str, Engine] = {}

def get_engine(conn_id):
    # Cek cache → kalau gak ada: build URL, create engine, pool_size=5, pool_pre_ping=True
    # Update last_used_at
    # Return engine
```

Engine cached selamanya sampai koneksi dihapus/diupdate.

### URL Builder

SQLite: `sqlite:///path/to/file.db`

PostgreSQL: `postgresql+psycopg2://user:pass@host:port/dbname?options=-c default_transaction_read_only=on`

MySQL: `mysql+pymysql://user:pass@host:port/dbname`

### Test Connection

Connect → inspect → ambil daftar tabel + db_version → dispose. Gak masuk cache engine.

### Health Check

`conn.execute(SELECT 1)` → return "ok" atau "error". Muncul sebagai dot hijau/merah di UI.

---

## 7. Conversations (`backend/conversations.py`)

### Schema

```sql
CREATE TABLE conversations (
    id TEXT PRIMARY KEY,           -- UUID
    title TEXT DEFAULT 'New Chat',
    connection_id TEXT NOT NULL,   -- FK → connections
    created_at, updated_at
);

CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,  -- FK → conversations ON DELETE CASCADE
    role TEXT CHECK(role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    sql TEXT,
    result_json TEXT,
    style TEXT DEFAULT 'normal',
    created_at
);

CREATE TABLE feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL,    -- FK → messages ON DELETE CASCADE
    rating TEXT CHECK(rating IN ('up', 'down')),
    comment TEXT,
    created_at
);
```

### Multi-Turn Context

6 pesan terakhir diformat sebagai konteks, dikirim ke LLM:

```
Previous conversation context:
User: berapa jumlah orders?
Assistant: Ada 20 pesanan.
SQL: SELECT COUNT(*) FROM orders
Result: {"columns": ["count"], "rows": [{"count": 20}], "row_count": 1}
Current question: dari jumlah tadi, yang pending berapa?
```

### Auto-Title

Pertanyaan pertama user → judul chat, dipotong 60 karakter. Tanpa LLM call tambahan.

### Delete Cascade

Hapus conversation → messages + feedback ikut terhapus otomatis.

---

## 8. Main App + SSE Stream (`backend/main.py`)

### SSE Format

```
data: {"type": "status", "status": "generating_sql"}\n\n
data: {"type": "sql", "sql": "SELECT COUNT(*) FROM orders"}\n\n
data: {"type": "status", "status": "executing"}\n\n
data: {"type": "status", "status": "generating_response"}\n\n
data: {"type": "result", "question": "...", "sql": "...", "columns": [...], "rows": [...], "response": "..."}\n\n
data: {"type": "done"}\n\n
```

`X-Accel-Buffering: no` — matiin nginx buffering.

### SQL Validator (`validate_sql`)

1. Harus diawali SELECT atau WITH
2. Keyword terlarang di-cek dengan regex word boundary (`\bINSERT\b`)
3. Auto tambah `LIMIT 100` kalau belum ada

### Full Pipeline (`stream_chat`)

1. **Resolve connection** — pakai `connection_id` diberikan, atau fallback ke koneksi pertama
2. **Analyze trigger check** — "refresh database context" → force re-analysis
3. **[Phase 2] Cache lookup** — cache_lookup(conn_id, message)
   - HIT (sim > 0.85) → execute SQL cached → response → done
   - MISS → lanjut
4. **[Phase 2] Embedding pre-filter** — search_tables(conn_id, message, k=15)
   - Ada hasil → relevant_tables = top-15
   - Fallback → semua table descriptions
5. **[Phase 2] LLM re-rank** — generate_with_re_rank(message, tier1_context, relevant_tables)
   - Return ("explain", answer) atau ("sql", query)
6. **Routing** — EXPLAIN langsung stream, SELECT lanjut validasi
7. **Validate SQL** — validate_sql(content)
8. **Execute** — execute_sql(sql, QUERY_TIMEOUT, engine, db_type)
9. **Generate response** — generate_response(message, sql, results, style)
10. **SSE output** — format_output(...) → yield SSE events
11. **[Phase 2] Cache store** — cache_store(conn_id, message, sql, response)
12. **Persist** — save_message(user) + save_message(assistant) + auto-title

### Endpoint Lengkap

| Endpoint | Method | Fungsi |
|----------|--------|--------|
| `/api/chat` | POST | SSE chat (endpoint utama) |
| `/api/health` | GET | `{status, connections_count, conversations_count}` |
| `/api/schema` | GET | Full DDL |
| `/api/schema/tables` | GET | Structured table metadata |
| `/api/schema/tables/{name}/sample` | GET | 5 sample rows |
| `/api/connections` | GET/POST | List / Create |
| `/api/connections/{id}` | GET/PUT/DELETE | CRUD single |
| `/api/connections/test` | POST | Test unsaved config |
| `/api/connections/{id}/test` | POST | Test saved connection |
| `/api/connections/{id}/analyze` | POST | Trigger re-analysis |
| `/api/conversations` | GET/POST | List / Create |
| `/api/conversations/{id}` | GET/PATCH/DELETE | CRUD single |
| `/api/feedback` | POST | Thumbs up/down |

---

## File Structure

```
talk-query/
├── backend/
│   ├── main.py            # FastAPI app + SSE endpoint (511 lines)
│   ├── llm.py             # Provider-agnostic LLM client + prompts
│   ├── analyzer.py        # Database analysis engine (Tier 1 + Tier 2)
│   ├── embeddings.py      # sqlite-vec + sentence-transformers (Phase 2)
│   ├── db.py              # SQL executor with DB-specific safety
│   ├── schema.py          # SQLAlchemy schema introspection
│   ├── connections.py     # Connection CRUD + engine cache
│   ├── conversations.py   # Chat persistence + multi-turn context
│   ├── crypto.py          # Fernet password encryption
│   ├── models.py          # Pydantic models
│   ├── seed.py            # Sample data seeder
│   ├── requirements.txt   # Python dependencies
│   └── .fernet_key        # Encryption key (auto-gen, gitignored)
├── frontend/              # Next.js 16 + shadcn/ui
│   └── src/
│       ├── app/           # App router
│       ├── components/    # Chat UI, sidebar, connection manager
│       └── lib/           # API client + stores
├── data/
│   ├── talkquery.db       # Sample SQLite database
│   └── talkquery_config.db # App config (connections, conversations, embeddings)
├── docs/
│   ├── plan/              # Planning documents
│   └── superpowers/       # Specs + implementation plans
├── start.sh               # Background launcher with safe restart
└── README.md
```

---

## Tech Stack

| Layer | Technology | Notes |
|-------|-----------|-------|
| Frontend | Next.js 16 + React | Production mode (dev Turbopack has UI freeze bug) |
| UI | shadcn/ui + Tailwind | |
| Backend | FastAPI (Python 3.12) | SSE streaming |
| LLM | OpenAI SDK → DeepSeek V3 | Provider-agnostic, ganti env var aja |
| App DB | SQLite (WAL mode) | Config, conversations, embeddings |
| Vector DB | sqlite-vec 0.1.9 | Embedded, zero infra |
| Embeddings | sentence-transformers | paraphrase-multilingual-MiniLM-L12-v2, 384d |
| User DB | SQLite / PostgreSQL / MySQL | Multi-DB support |
| Encryption | Fernet (AES-128-CBC) | Password at rest |
| Proxy | nginx | Reverse proxy, SSL termination |
