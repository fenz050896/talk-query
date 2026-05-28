# Dev Log

## 2026-05-28 — Database Context Understanding

**Implemented:** Tiered database context builder. AI now understands what a database is about — not just generates SQL from DDL.

### Changes

| File | Change |
|------|--------|
| `backend/analyzer.py` | **New.** Analysis engine: Tier 1 summary, Tier 2 per-table descriptions, LLM table ranking, context assembly, profile cache |
| `backend/llm.py` | **Modified.** Added `SYSTEM_PROMPT_WITH_CONTEXT` and `generate_answer()` with EXPLAIN/SELECT routing |
| `backend/main.py` | **Modified.** Integrated profile loading, context assembly, EXPLAIN response handling, async analysis trigger, `/analyze` endpoint |
| `backend/connections.py` | **Modified.** Added `init_database_profiles_table()` |
| `frontend/src/lib/api.ts` | **Modified.** Added `analyzeConnection()` API function |
| `frontend/src/components/chat-panel.tsx` | **Modified.** Added "Analyze" button in header |
| `frontend/src/components/connection-manager.tsx` | **Modified.** Added "Analyze" button per connection card |

### Architecture Decisions

- **Tiered context** — Tier 1 always in prompt (~500 tokens), Tier 2 on-demand via LLM ranking
- **LLM relevance ranking** — no embedding model needed, use same LLM to rank tables by description
- **EXPLAIN/SELECT routing** — flexible system prompt, LLM decides meta answer vs SQL query
- **Async non-blocking analysis** — database analysis runs in background, user can query immediately
- **Schema hash check** — prevents redundant re-analysis when schema hasn't changed
- **Analysis lock** — prevents concurrent analysis per connection (anti-spam)

### Known Gaps

- **Tier 3 (Column Semantic Profile)** — deferred. Tier 2 covers column-level semantics for now.
- **Stale profile detection** — schema hash stored but no periodic background check. Manual re-trigger needed.

---

## 2026-05-27 — Initial Build

### Features

- Multi-database support: SQLite, MySQL, PostgreSQL
- Natural language to SQL via DeepSeek LLM
- SSE streaming chat API
- Multi-turn conversations with context
- Connection management (CRUD, test, health check)
- Password encryption (Fernet)
- RTK/caveman response modes
- Schema DDL + compact format extraction
- Frontend: chat UI with connection manager, conversation sidebar
