# Phase 2 — Efficient Schema Retrieval + Query Cache

## Context

Talk-Query user scale: 7 database × 70 tabel = 490 tabel. Current flow: LLM ranks ALL table descriptions every query (~7,000 token + 1 LLM call just for ranking). At this scale, cost and latency are significant.

Goal: replace full-table LLM ranking with embedding-based pre-filter + LLM re-rank. Add semantic query cache. Zero new infrastructure.

## Architecture

```
User question
     │
     ▼
┌─────────────────────────────────────────────────┐
│ 1. Query Cache Lookup (sqlite-vec)              │
│    Semantic search over historical queries       │
│    Similarity > 0.90 → reuse cached SQL          │
│    ~50ms                                         │
└─────────────────────────────────────────────────┘
     │ (no cache hit)
     ▼
┌─────────────────────────────────────────────────┐
│ 2. Table Pre-filter (sqlite-vec)                │
│    Pre-computed embeddings per table description │
│    Cosine similarity search → top-15 tables      │
│    ~50ms, entirely local                         │
└─────────────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────────────┐
│ 3. LLM Re-rank + Generate                       │
│    Only 15 table descriptions (~1,500 tokens)    │
│    LLM picks top 5 + generates SQL/EXPLAIN       │
│    ~1,500ms                                      │
└─────────────────────────────────────────────────┘
     │
     ▼
┌─────────────────────────────────────────────────┐
│ 4. Execute SQL → LLM Response → Store in Cache  │
│    Flow same as current                          │
└─────────────────────────────────────────────────┘
```

## Tech Choices

### sqlite-vec

- Embedded vector engine, zero server, zero config
- `pip install sqlite-vec`
- Same philosophy as SQLite (file-based, no dependency)
- Handles 10K-100K vectors at < 10ms

### sentence-transformers (paraphrase-multilingual-MiniLM-L12-v2)

- Local embedding model, no API call, no cost
- 384 dimensions, ~470MB model
- Supports 50+ languages including Indonesian + English
- Cross-lingual: Indonesian questions match English table descriptions
- Part of sentence-transformers ecosystem, mature and well-tested

## File Changes

| File | Action | Purpose |
|------|--------|---------|
| `backend/requirements.txt` | Add | `sqlite-vec`, `sentence-transformers` |
| `backend/embeddings.py` | **New** | sqlite-vec wrapper: init DB, build table index, search, query cache CRUD |
| `backend/analyzer.py` | Modify | After Tier 2 analysis → embed all table descriptions |
| `backend/llm.py` | Modify | Re-rank prompt: only 15 tables instead of all. Merge SQL generation. |
| `backend/main.py` | Modify | Replace `get_relevant_context` flow with embed→pre-filter→re-rank |
| `frontend/` | No changes | API contract unchanged |

## New Module: embeddings.py

```python
# Core functions
init_vector_db(connection_id: str)       # Create sqlite-vec virtual table per connection
embed(texts: list[str]) -> ndarray       # sentence-transformers encode
build_table_index(connection_id, tables) # Embed all table descriptions, store in vec table
search_tables(connection_id, question, k=15) -> list[str]  # Cosine similarity search
cache_lookup(connection_id, question, threshold=0.90) -> CachedQuery | None
cache_store(connection_id, question, sql, response)
```

## Modified Flow in main.py

Current:
```
profile → get_relevant_context() 
  → _rank_tables_by_relevance(ALL tables via LLM)  ← 7,000 tokens
  → _fk_connected_tables() expansion
  → assemble context
```

New:
```
profile → get_relevant_context_hybrid()
  → cache_lookup()                                   ← 0 LLM calls if hit
  → search_tables(sqlite-vec, k=15)                  ← local, 50ms
  → _rank_and_generate(only 15 tables)               ← 1,500 tokens, 1 LLM call
  → cache_store()
```

## Token Budget Comparison

| Stage | Current | Phase 2 |
|-------|---------|---------|
| Table ranking | ~7,000 (LLM) | 50ms (local) |
| Re-rank + SQL gen | n/a (separate call) | ~1,500 (merged) |
| SQL generation | ~500 | merged above |
| Response generation | ~300 | ~300 |
| **Total tokens** | ~7,800 | ~1,800 (77% saving) |
| **LLM calls** | 3 | 2 (or 1 if cache hit) |

## Build Order

1. Install deps: `sqlite-vec`, `sentence-transformers`
2. `backend/embeddings.py` — vector store + embedding engine
3. Modify `backend/analyzer.py` — auto-build table embeddings after Tier 2
4. Modify `backend/llm.py` — merged re-rank + generate prompt
5. Modify `backend/main.py` — hybrid context assembly flow
6. Add query cache endpoints (optional, for cache stats)

## Verification

1. Create connection → analyze → verify `_table_embeddings` virtual table populated
2. Ask question → verify embedding search returns relevant tables (not just keyword match)
3. Compare answer quality before/after — should be equal or better
4. Measure latency: embedding search should be < 100ms
5. Ask same question twice → second should hit cache (check logs)
6. Test with 7-DB scenario: switch connections, each uses own index
