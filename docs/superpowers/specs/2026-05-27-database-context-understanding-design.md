# Database Context Understanding — Design Spec

**Status:** draft | **Date:** 2026-05-28 | **Revision:** 2

## Problem

Talk-Query saat ini hanya memberikan structural schema (DDL) ke LLM — nama tabel, nama kolom, tipe data, PK/FK. Ketika user bertanya meta-level questions seperti "database ini tentang apa?", "apa fungsi database ini?", atau "bagaimana relasi antar data ini?", LLM tidak punya konteks cukup. LLM terpaksa menebak dari nama tabel dan menghasilkan jawaban dalam bentuk SQL (`SELECT 'jawaban hardcoded' as description`) alih-alih memberikan pemahaman genuine.

## Goal

AI bisa memahami konteks database secara keseluruhan — purpose, relasi bisnis, pola data — dan menjawab pertanyaan meta secara natural, bukan sekadar generate SQL literal.

Target kemampuan:
- Menjelaskan purpose dan fungsi database
- Menjelaskan relasi antar tabel dan logika bisnis
- Menyarankan query yang berguna untuk eksplorasi data
- Menghasilkan dokumentasi otomatis (deskripsi tabel, use case)
- Reasoning tingkat tinggi tentang data yang ada

## Constraints

- **Auto-analysis only** — tidak ada input metadata manual dari user
- **Natural conversation interface** — tidak ada dashboard/panel tambahan, semua lewat chat
- **Tidak merusak flow existing** — query SQL simpel harus tetap cepat
- **Scalable** — bisa handle puluhan sampai ratusan tabel

## Approach: Tiered Context Builder

### Konsep

Analisis database dilakukan sekali saat koneksi pertama, menghasilkan tiga tier informasi. Tier 1 selalu ada di context, Tier 2 & 3 di-load on-demand berdasarkan pertanyaan user.

### Tier 1: Database Summary (~500 token, selalu di system prompt)

Ringkasan tingkat tinggi yang menjawab "database ini tentang apa?":

- Nama database dan type (SQLite/PostgreSQL/MySQL)
- Purpose guess — hasil analisis LLM dari struktur keseluruhan
- Katalog tabel dikelompokkan per domain: `inventory (products, stock_log, warehouses)`, `sales (orders, invoices, payments)`
- Statistik global: jumlah tabel, total row, engine version
- Observasi penting: tabel terbesar, tabel kosong, relasi siklik

Format: teks naratif ringkas. Contoh:

```
Database: slims (MySQL v8.0)
Purpose: Library management system (Senayan Library Management System).
         Manages bibliographic records, member circulation, and library inventory.

Domain groups:
- Bibliographic: biblio, biblio_topic, biblio_author, biblio_attachment
- Membership: member, member_type, member_custom
- Circulation: loan, loan_rules, reservation, fines
- Inventory: item, item_status, location, collection_types

Stats: 47 tables, ~15,000 rows. Largest: biblio (~2,500 rows), loan (~3,000 rows).
Note: item_code pattern detected (panggil/eksemplar).
```

### Tier 2: Table Detailed Analysis (on-demand, per tabel ~300 token)

Saat user bertanya tentang domain/tabel tertentu, load detail tabel yang relevan:

- Deskripsi tabel (inferred dari nama, kolom, FK)
- Daftar kolom dengan semantic meaning (bukan cuma nama & tipe)
- Contoh data (3-5 baris sample)
- Value distribution: distinct count, null rate, min/max untuk numeric
- Relationship quality: join cardinality, orphan check
- Column notes: pattern terdeteksi (format kode, enum-like values, timestamp range)

Format: teks per tabel. Hanya tabel yang relevan dengan query yang di-load.

### Tier 3: Column Semantic Profile (deferred — future iteration)

Untuk kolom-kolom dengan nama cryptic atau ambiguous:

- Inferred meaning dari nama, tipe, dan isi
- Detected pattern (email, phone, date range, enum, foreign key lookup)
- Distinct values sample (untuk low-cardinality columns)

**Status: Deferred.** Tier 2 table descriptions already cover column-level semantics. Tier 3 akan ditambah jika user melaporkan kolom cryptic tidak terbaca dengan baik.

### Trigger: Kapan Analysis Terjadi

1. **On connection (async, non-blocking)** — saat user connect, analysis jalan di background. User bisa langsung query SQL tanpa context. Kalau analysis selesai, Tier 1 otomatis terpasang di prompt berikutnya. Kalau gagal, fallback ke schema-only.
2. **On manual request** — endpoint `POST /api/connections/{id}/analyze` atau user bilang "refresh database context"
3. **Per-query (Tier 2 & 3 loading)** — otomatis saat pertanyaan menyebut tabel atau domain tertentu. Embedding matching menentukan tabel relevan.

### Query Routing: Meta vs Data

Tidak semua pertanyaan perlu SQL. System prompt harus bisa handle dua jenis:

**Meta questions** ("database ini tentang apa?", "bagaimana relasi datanya?", "tabel apa yang paling penting?")
→ Jawab langsung dari Tier 1 + Tier 2 context, tanpa generate SQL.

**Data questions** ("tampilkan 10 buku terbaru", "berapa total pinjaman bulan ini?")
→ Generate SQL dari filtered DDL + Tier 2 context.

Implementasi: **Flexible system prompt** — prompt instruksikan LLM untuk decide sendiri apakah perlu SQL atau cukup jawab dari context. Prompt format:

```
You are a SQL expert and data analyst. Given the database context and schema below, answer user questions.

Response format:
- If the question asks ABOUT the database (purpose, structure, relationships, patterns):
  EXPLAIN: <natural language answer from the context>

- If the question asks for specific DATA (list, count, filter, aggregate):
  SELECT <query>

SQL rules:
- Only SELECT. No INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE.
- Always add LIMIT 100 if user doesn't specify a limit.

Database Context:
{context}

Relevant Schema:
{schema}
```

Backend parse: line starts with `EXPLAIN:` → return text as-is. Line starts with `SELECT` or `WITH` → validate & execute SQL.

Routing decision ada di LLM, bukan classifier terpisah. Untuk production: bisa tambah classifier kecil kalau LLM sering salah route.

### Context Assembly Per Query

```
User: "Bagaimana data peminjaman bulan ini?"

1. LLM ranking: "data peminjaman bulan ini" + 47 table descriptions → top: loan, reservation, member, item
2. FK traversal: `loan` → FK ke `item`, `member` → include `item` juga
3. Assemble context:
   a. Tier 1: Database Summary (selalu)
   b. Tier 2: Detailed analysis untuk loan, reservation, member, item
   c. Filtered DDL: Hanya DDL untuk 4 tabel tersebut (bukan semua 47 tabel)
   d. Total context: ~500 (Tier 1) + ~1200 (Tier 2, 4 tabel × 300) + ~400 (DDL) = ~2100 token (vs ~4000 token DDL penuh)
4. LLM generate SQL (data question) → response starts with SELECT

User: "Apa database ini tentang?"

1. LLM ranking: tidak ada tabel spesifik → Tier 1 cukup
2. LLM detect meta question → response starts with EXPLAIN:
3. Backend parse EXPLAIN: → return text langsung, no SQL execution
```

### Relevance Matching (LLM-Based Ranking)

Keyword matching tidak reliable untuk cross-language queries (user tanya "data peminjaman" tapi tabel bernama `loan`). Pakai LLM relevance ranking — tidak perlu embedding model terpisah:

1. **Saat analysis:** Setiap Tier 2 table description di-cache di profile
2. **Saat query:** Kirim user question + list of (table_name, description) ke LLM, minta ranking top-N tabel relevan
3. **Prompt ranking:**
   ```
   Given this user question: "{question}"
   
   And these table descriptions:
   - loan: Records book borrowing transactions...
   - member: Library member profiles...
   - biblio: Bibliographic catalog entries...
   ...
   
   Return the top 5 most relevant tables for answering this question, ordered by relevance.
   Respond with table names only, one per line.
   ```
4. **Selection:** Ambil top-N tabel + tabel terhubung via FK (1 level) + domain group peers
5. **Cost:** 1 LLM call ringan (~200 token output) per query. Model flash/low-cost cukup.

Fallback kalau LLM ranking gagal: keyword matching sederhana (match nama tabel/kolom + FK traversal).

### Cache Invalidation

- Database profile di-cache di DB config (talkquery_config.db)
- Invalidate saat: connection di-refresh, schema changed, user request manual refresh
- Tabel dengan row_count = -1 di schema (permission error) di-skip dari analysis

## Implementation Outline

### File baru: `backend/analyzer.py`

```
analyze_database(engine) → DatabaseProfile
  - Extracts schema metadata (pakai schema.py existing)
  - Samples data from each table (pakai get_table_sample existing)
  - Generates Tier 1 summary via LLM (1 call)
  - Generates Tier 2 per-table analysis (parallel batch, 5-10 concurrent LLM calls)
  - Stores profile as JSON in talkquery_config.db

get_relevant_context(profile, user_question, engine) → str
  - LLM ranking: user question + all table descriptions → top 5 tables
  - Include FK-connected tables (1 level)
  - Include domain group peers
  - Assembly: Tier 1 + relevant Tier 2 + filtered DDL (hanya tabel relevan)
  - Return combined context string
```

### Modifikasi: `backend/llm.py`

```
SYSTEM_PROMPT baru: flexible prompt dengan {context} dan {schema}
- {context} = hasil get_relevant_context() atau Tier 1 only
- {schema} = filtered DDL (bukan semua tabel)
- LLM response format: "EXPLAIN: ..." atau "SELECT ..."
- Backend parse prefix untuk tentukan execute SQL atau return text
- Fallback ke schema-only + old prompt kalau profile belum dibuat

generate_sql() di-refactor jadi generate_answer():
  - Returns tuple (type: "sql"|"explain", content: str)
  - type="sql" → validate & execute
  - type="explain" → return text langsung ke user
```

### Modifikasi: `backend/main.py`

```
stream_chat():
  - Load database profile dari cache
  - Dapatkan relevant context berdasarkan user question
  - Masukkan ke generate_sql() sebagai pengganti schema-only
  - Fallback ke flow existing kalau profile belum ada
```

### New endpoint: `POST /api/connections/{conn_id}/analyze`

Trigger manual analysis & regeneration.

### Storage

Tabel baru di `talkquery_config.db`:

```sql
CREATE TABLE IF NOT EXISTS database_profiles (
    connection_id TEXT PRIMARY KEY,
    profile_json TEXT NOT NULL,          -- Full Tier 1 + Tier 2 data
    schema_hash TEXT,                     -- Untuk deteksi schema change
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (connection_id) REFERENCES connections(id) ON DELETE CASCADE
);
```

## Edge Cases & Error Handling

- **Schema terlalu besar:** Batasi Tier 2 analysis ke 50 tabel teratas (berdasarkan row count), sisanya hanya Tier 1 summary
- **Sample data gagal:** Skip tabel yang permission error, tandai di profile
- **LLM timeout saat analysis:** Retry 1x per batch, fallback ke schema-only kalau batch gagal
- **Database kosong (0 tabel):** Profile berisi "No tables found"
- **Schema berubah setelah analysis:** Deteksi lewat schema hash, flag profile as stale, suggest user refresh
- **Connection dihapus:** Cascade delete profile
- **LLM ranking gagal:** Fallback ke keyword matching + FK traversal
- **LLM response format salah:** Tidak ada EXPLAIN: atau SELECT prefix → fallback ke old behavior (generate SQL only)
- **Parallel analysis limit:** Max 10 concurrent LLM calls. Untuk >50 tabel, pakai queue bertahap (10 batch × 5 round)

## What This Is NOT

- Bukan ERD generator visual (no diagram)
- Bukan data catalog dengan UI (tetap chat-based)
- Bukan real-time sync (analysis manual/event-based, bukan CDC)
- Bukan BI/reporting tool

## Decisions Log

1. **LLM model untuk analysis?** Pakai model yang sama (DeepSeek). Untuk 47 tabel, cost analysis ~$0.01-0.02, tidak signifikan. Parallel batch (10 concurrent) lebih penting untuk latency.
2. **Auto-analyze on connection?** ✅ Auto, async non-blocking. User bisa query SQL langsung, analysis jalan di background.
3. **Bahasa output?** ✅ Profile dalam bahasa Inggris (teknis, precision). Response ke user dalam bahasa user (Indonesia). LLM translate context saat generate response.
4. **Timeout limit?** Analysis async, jadi tidak ada timeout yang user rasakan. Internal timeout: 60s per LLM call, max 5 menit total per batch.

## Open Questions

(Tidak ada — semua resolved.)
