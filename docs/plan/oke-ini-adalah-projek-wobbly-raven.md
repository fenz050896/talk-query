# Talk-Query — NL2SQL Chat Application Plan

## Context

Aplikasi chat seperti ChatGPT/Claude yang bercakap dengan database. User bertanya dalam bahasa natural, sistem menerjemahkan ke SQL, mengeksekusi, dan mengembalikan jawaban natural. Plan dari MVP sampai advanced.

---

## Phase 1: MVP — Text-to-SQL Dasar

### Stack
- **Frontend**: Next.js + React + shadcn/ui + Tailwind
- **Backend**: FastAPI (Python) — ecosystem DB connector matang (SQLAlchemy untuk SQLite)
- **LLM**: Provider-agnostic via OpenAI SDK — target DeepSeek V3 (OpenAI-compatible API). Ganti provider tinggal ubah env `LLM_BASE_URL` + `LLM_API_KEY`
- **Database target**: SQLite — tanpa install tambahan, file .db langsung pakai. Migrasi ke PostgreSQL nanti tinggal ganti connection string

### Fitur
1. Chat UI — input pertanyaan, bubble chat user/system
2. Text-to-SQL — system prompt berisi DDL schema statis
3. SQL executor — read-only, LIMIT 100 maks, timeout 10 detik
4. Response format — hasil query ditampilkan sebagai tabel + ringkasan natural
5. Error handling — SQL error ditampilkan dengan penjelasan ramah
6. Salin SQL — user bisa lihat dan salin SQL yang digenerate

### Arsitektur
```
[Browser] ←SSE streaming→ [FastAPI /api/chat] ←LLM (DeepSeek/OpenAI-compatible)
                                ↓
                         [SQLite (file.db)]
```

### LLM Abstraction
```python
# llm.py — provider-agnostic, OpenAI-compatible SDK
from openai import AsyncOpenAI

client = AsyncOpenAI(
    api_key=os.getenv("LLM_API_KEY"),
    base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1"),
)
model = os.getenv("LLM_MODEL", "deepseek-chat")
```
Ganti provider: ubah env vars saja. Support semua OpenAI-compatible API (DeepSeek, OpenAI, Groq, local Ollama via `/v1` endpoint).

### Prompt Strategy (configurable via env SYSTEM_PROMPT)
```
You are a SQL expert. Given the schema below, convert user questions to SQL.
- Only SELECT queries. No INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE.
- Always add LIMIT 100 if user doesn't specify a limit.
- Return ONLY the SQL query, nothing else. No markdown, no explanation.
- If the question is not answerable with SELECT, respond: "ERROR: Only SELECT queries allowed."

Schema:
{schema_ddl}
```

### File Structure
```
talk-query/
├── frontend/              # Next.js app
│   ├── src/
│   │   ├── app/           # App router
│   │   ├── components/    # Chat UI components
│   │   └── lib/           # API client
│   └── package.json
├── backend/               # FastAPI
│   ├── main.py            # FastAPI app + /api/chat SSE endpoint
│   ├── db.py              # SQLAlchemy + SQLite connector
│   ├── llm.py             # Provider-agnostic LLM client (OpenAI SDK)
│   ├── schema.py          # Schema extractor from SQLite
│   ├── requirements.txt
│   └── .env.example       # LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
└── data/                  # SQLite .db files live here
```

### Tasks MVP
1. Setup project structure — create directories, requirements.txt, package.json
2. Setup FastAPI + SQLite connector + schema extractor
3. Implement LLM client (provider-agnostic, OpenAI SDK → DeepSeek)
4. Implement `/api/chat` endpoint dengan SSE streaming + safety validator
5. Build Next.js chat UI dengan shadcn/ui + SSE client
6. Wire frontend ke backend
7. End-to-end test dengan SQLite sample database

---

## Phase 2: Better — RAG + Schema Exploration

### Fitur Baru
1. **RAG Schema Retrieval** — embedding schema (table descriptions, column comments, sample data), retrieve yang relevan ke prompt untuk hemat token + akurasi
2. **Historical Query Cache** — simpan pertanyaan + SQL yang berhasil, semantic search untuk reuse
3. **Multi-Turn Context** — chat menyimpan history, referensi pertanyaan sebelumnya
4. **Schema Explorer UI** — panel samping menampilkan struktur database
5. **Query History** — sidebar riwayat percakapan
6. **Feedback Loop** — thumbs up/down per jawaban, simpan ke dataset buat fine-tuning nanti

### Tech Tambahan
- ChromaDB / pgvector untuk embedding storage
- LangChain / custom tool-use untuk schema retrieval

### Arsitektur
```
[Browser] ←SSE→ [FastAPI] ←Anthropic API
                    ↓
            [ChromaDB/pgvector] ← embedding schema
                    ↓
              [PostgreSQL]
```

---

## Phase 3: Advanced — Agent + Multi-DB + Visualisasi

### Fitur Baru
1. **Agent Loop** — LLM bisa eksplor schema sendiri (SHOW TABLES, DESCRIBE, sample data), validasi query, retry kalau error
2. **Tool Use** — LLM diberi tools: `execute_sql()`, `describe_table()`, `show_tables()`, `sample_data()`
3. **Multi-Database** — support multiple DB connections, user pilih database target
4. **Visualisasi** — deteksi tipe hasil (time-series, categorical, numeric) → auto chart (bar, line, pie)
5. **Natural Language Explain** — EXPLAIN query output dijelaskan dalam bahasa natural
6. **Query Optimization Tips** — LLM review query, kasih saran index/perbaikan
7. **Export** — CSV, JSON, copy to clipboard

### Tech Tambahan
- LangChain / CrewAI agent framework
- pandas + matplotlib/plotly untuk visualisasi
- Redis untuk caching

### Arsitektur
```
[Browser] ←SSE→ [FastAPI] ←Anthropic API (agent loop with tools)
                    ↓
         ┌─────────┼─────────┐
         ↓         ↓         ↓
      [PG 1]    [PG 2]   [MySQL]
         ↓
   [pgvector + ChromaDB]
```

---

## Phase 4: Production — Multi-User + Auth + Monitoring

### Fitur Baru
1. **Auth** — login/register, user-scoped connections
2. **Multi-Tenant** — tiap user punya DB connections sendiri
3. **Rate Limiting** — per user, per IP
4. **Audit Log** — semua query tercatat
5. **Admin Panel** — monitoring usage, error rate
6. **Connection Security** — TLS, encrypted credentials storage

---

## Decisions (confirmed 2026-05-26)

1. **LLM provider**: DeepSeek (user tentukan sendiri). Backend pakai OpenAI SDK — provider-agnostic. Ganti provider = ubah env vars.
2. **Database target**: SQLite. Tanpa Docker, tanpa install. File .db langsung.
3. **Frontend**: Next.js + shadcn/ui.
4. **Eksekusi**: Phase 1 langsung setelah plan approved.

---

## Verification

### Phase 1
1. `cd backend && pip install -r requirements.txt` — dependencies terinstall
2. Copy `.env.example` ke `.env`, isi API key
3. `python backend/main.py` — FastAPI jalan di :8000
4. `cd frontend && npm install && npm run dev` — Next.js jalan di :3000
5. Buka browser, ketik "berapa tabel yang ada di database ini?"
6. System generate SQL, eksekusi, tampilkan hasil
7. Hasil muncul di chat: jawaban natural + tabel data + SQL yang digenerate
8. Test safety: ketik "hapus semua user" → system tolak, jelaskan hanya SELECT
9. Test non-SQL: ketik "halo apa kabar" → system tolak dengan sopan

### Phase 2+
- Tambah feedback, cache, schema explorer — semua harus tetap jalan
