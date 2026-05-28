# TalkQuery Phase 2 — Multi-DB Connection + Chat Persistence

## Context

TalkQuery value prop: "Chat with **YOUR** database." Current implementation: single hardcoded SQLite from `.env`, no persistence, no multi-chat. This phase adds multi-DB connection support with a chat-first architecture — users create independent chats, each connected to their database of choice.

## Architecture Decision: Chat-First (Model B)

```
┌──────────┬──────────────────────────────────────┐
│ Sidebar  │ Chat area                            │
│          │ ┌──────────────────────────────────┐ │
│ Chat #1  │ │ [SQLite ●] Production DB         │ │ ← connection indicator
│ (PG)  ●  │ │ How many active users?           │ │
│ Chat #2  │ │ There are 8 active users.        │ │
│ (MySQL)  │ │                                  │ │
│ Chat #3  │ │                                  │ │
│ (SQLite) │ │                                  │ │
│          │ ├──────────────────────────────────┤ │
│ [+ New]  │ │ [Ask about your data...] [Send]  │ │
└──────────┴──────────────────────────────────────┘
```

- **Chat independen dari koneksi.** Setiap chat punya `connection_id` sendiri.
- **Buat chat dulu, baru chat.** "New Chat" → pilih database → mulai tanya.
- **Sidebar mirip ChatGPT/Claude** — list chat, label database, switch/delete chat.
- **Tidak ada "active connection" global.** Hanya ada koneksi terakhir dipakai.

## Requirements

- Support: PostgreSQL, MySQL/MariaDB, SQLite
- UI: add/edit/delete/test connections
- Chat-first: create chat → pilih koneksi → tanya
- Setiap chat independen dengan koneksinya sendiri
- Password encryption at rest (Fernet, key disimpan di file)
- Engine pooling per connection
- Dynamic schema introspection
- Health indicator (dot hijau/merah) di connection selector
- Chat history persistence (survive refresh)
- Backward compatible: existing `.env` SQLite auto-created sebagai koneksi default

## Database Schema

### New tables in existing `talkquery.db`

```sql
CREATE TABLE connections (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    db_type TEXT NOT NULL CHECK(db_type IN ('sqlite', 'postgresql', 'mysql')),
    host TEXT,
    port INTEGER,
    database_name TEXT NOT NULL,       -- file path for SQLite, db name for PG/MySQL
    username TEXT,
    password_encrypted TEXT,
    ssl_mode TEXT DEFAULT 'prefer',
    last_used_at TEXT,                 -- track last usage, not is_active
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT 'New Chat',
    connection_id TEXT NOT NULL REFERENCES connections(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    sql TEXT,
    result_json TEXT,
    style TEXT DEFAULT 'normal',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    rating TEXT NOT NULL CHECK(rating IN ('up', 'down')),
    comment TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

Perubahan dari plan sebelumnya: `is_active` dihapus, diganti `last_used_at`. Konsep "active" tidak relevan di chat-first model. Tabel `conversations` dan `messages` ditambahkan sekarang karena fundamental untuk chat-first.

## API Endpoints

### Connection endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/connections` | List all (passwords redacted). Return `health_status` per connection. Check happens on-demand per request (connect, inspect, dispose — <3s for healthy, <5s timeout for dead). No background polling. |
| POST | `/api/connections` | Create connection |
| GET | `/api/connections/{id}` | Get single connection |
| PUT | `/api/connections/{id}` | Update connection. Invalidates engine cache. |
| DELETE | `/api/connections/{id}` | Delete connection. Returns 409 if referenced by conversations (must delete conversations first). Response body lists blocking conversation titles. |
| POST | `/api/connections/test` | Test unsaved connection config. Body: `ConnectionCreate`. |
| POST | `/api/connections/{id}/test` | Test existing connection. Return `{success, message, tables[], db_version, error_code?}`. |

**Dihapus:** `/api/connections/active` dan `/{id}/activate` — tidak relevan di model chat-first.

### Conversation endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/conversations` | List all conversations (id, title, connection_name, db_type, updated_at, message_count) |
| POST | `/api/conversations` | Create conversation. Body: `{connection_id, title?}`. Returns conversation with connection info. |
| GET | `/api/conversations/{id}` | Get conversation with all messages |
| DELETE | `/api/conversations/{id}` | Delete conversation + messages (CASCADE) |
| PATCH | `/api/conversations/{id}` | Update title |

### Modified endpoints

| Method | Path | Change |
|---|---|---|
| POST | `/api/chat` | `ChatRequest` gains `conversation_id` and `connection_id`. Backend: fetch last 6 messages as context, dynamic schema from connection's engine, persist user+assistant messages after response. |
| GET | `/api/schema` | Accepts `?connection_id=` param. Dynamic introspection. |
| GET | `/api/health` | Returns connection count, conversation count. |

### Feedback endpoint

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/feedback` | Body: `{message_id, rating, comment?}`. Returns 201. |

## Backend Modules

### New files

| File | Purpose |
|---|---|
| `backend/crypto.py` | Fernet encrypt/decrypt. Key from `backend/.fernet_key` file (auto-generated if missing). |
| `backend/connections.py` | Connection CRUD, engine cache, URL builder, test, health check |
| `backend/conversations.py` | Conversation + message CRUD |
| `backend/models.py` | Pydantic models for connection, conversation, feedback |

### Modified files

| File | Change |
|---|---|
| `backend/main.py` | 10+ new endpoints. Modified chat with multi-turn + persistence. Startup: init tables, auto-create default connection from `DATABASE_URL`. |
| `backend/db.py` | Remove global engine. `execute_sql(sql, timeout, engine, db_type)`. DB-specific settings per type. |
| `backend/schema.py` | All functions accept `engine` parameter. Add `get_tables_structured(engine)`. |

### Engine cache

```python
_engine_cache: dict[str, tuple[Engine, float]] = {}  # connection_id -> (engine, last_used_ts)
```

Engines created on demand, cached. Disposed on connection update. LRU eviction jika >10 engines.

### Fernet key management

```
backend/.fernet_key   ← auto-generated on first run, survive restart
backend/.gitignore    ← ignore .fernet_key
```

Jika file ada → baca key. Jika tidak → generate, tulis file, log info. Tidak ada console WARNING yang bisa hilang.

## Frontend Components

### New files

| File | Purpose |
|---|---|
| `frontend/src/lib/connection-store.tsx` | React Context: connections list, CRUD actions |
| `frontend/src/lib/conversation-store.tsx` | React Context: conversations list, activeConversationId, CRUD actions |
| `frontend/src/components/conversation-sidebar.tsx` | Left sidebar: chat list dengan DB label, "New Chat" button |
| `frontend/src/components/connection-selector.tsx` | Dropdown untuk pilih koneksi (dipakai di "New Chat" dialog) |
| `frontend/src/components/connection-manager.tsx` | Panel kelola koneksi (add, edit, delete, test) |
| `frontend/src/components/connection-form.tsx` | Form dialog: db_type toggle, conditional fields, test button, health dot |
| `frontend/src/components/new-chat-dialog.tsx` | Dialog "New Chat": pilih koneksi dari daftar, optional title |

### Modified files

| File | Change |
|---|---|
| `frontend/src/lib/api.ts` | Connection + conversation CRUD types & functions. `conversationId` + `connectionId` in `sendMessage()`. |
| `frontend/src/components/chat-panel.tsx` | Caveman/RTK toggle tetap di header (preferensi UI, bukan data). Wire conversation context. Connection indicator with health dot in chat header. |
| `frontend/src/components/message-bubble.tsx` | Add `messageId` prop, feedback thumbs. |
| `frontend/src/app/layout.tsx` | Wrap with ConnectionProvider + ConversationProvider. |
| `frontend/src/app/page.tsx` | Replace single ChatPanel with sidebar + chat layout. |

### Layout

```
┌────────────┬──────────────────────────────────────────┐
│ Sidebar    │ Chat Header                              │
│ ~260px     │ ┌──────────────────────────────────────┐ │
│            │ │ [SQLite ●] Production DB             │ │ ← connection + health
│ [+ New]    │ └──────────────────────────────────────┘ │
│ ──────     │──────────────────────────────────────────│
│ Chat #1 ●  │                                          │
│ PG Prod    │ Chat messages                            │
│ 2m ago     │                                          │
│            │                                          │
│ Chat #2    │                                          │
│ MySQL Dev  │                                          │
│ 5m ago     │                                          │
│            │──────────────────────────────────────────│
│ Chat #3    │ [Ask about your data...     ] [Send]     │
│ SQLite     │                                          │
│ 1h ago     │                                          │
└────────────┴──────────────────────────────────────────┘
```

## Data Flow: Chat-First

1. User klik **"New Chat"** → dialog muncul, pilih koneksi dari daftar (dengan health dot)
2. `POST /api/conversations {connection_id}` → backend buat conversation, return id
3. Frontend set activeConversationId, sidebar update
4. User tanya "berapa user aktif?"
5. `POST /api/chat {message, conversation_id, connection_id}` 
6. Backend: fetch conversation context (6 messages terakhir) + dynamic schema dari engine koneksi → LLM SQL generation → execute → LLM response → persist messages → SSE stream
7. Chat tampil dengan connection indicator di header
8. User bisa switch ke chat lain (sidebar click) — tiap chat independen

## Security

- **Passwords**: Fernet AES-128-CBC + HMAC-SHA256. Key from `backend/.fernet_key` file.
- **SQL injection**: existing `validate_sql()` keyword blocklist + `PRAGMA query_only` (SQLite) / `default_transaction_read_only` (PG) / `GRANT SELECT` (MySQL).
- **Transport**: SSL modes (disable → verify-full).
- **API**: Passwords never in responses.

## Dependencies

Add to `requirements.txt`:
- `cryptography` (Fernet)
- `psycopg2-binary` (PostgreSQL)
- `pymysql` (MySQL)

No new frontend dependencies.

## Build Order

1. Install Python deps
2. `backend/.gitignore` — add `.fernet_key`
3. `backend/crypto.py` — Fernet with file-based key
4. `backend/models.py` — Pydantic models
5. `backend/connections.py` — CRUD, engine cache, URL builder, test, health
6. `backend/conversations.py` — conversation + message CRUD
7. Refactor `backend/db.py` — multi-DB
8. Refactor `backend/schema.py` — dynamic engine
9. `backend/main.py` — all endpoints, modified chat, startup
10. `frontend/src/lib/api.ts` — types + functions
11. `frontend/src/lib/connection-store.tsx`
12. `frontend/src/lib/conversation-store.tsx`
13. `frontend/src/components/connection-form.tsx`
14. `frontend/src/components/connection-selector.tsx`
15. `frontend/src/components/connection-manager.tsx`
16. `frontend/src/components/new-chat-dialog.tsx`
17. `frontend/src/components/conversation-sidebar.tsx`
18. Wire `chat-panel.tsx` + `message-bubble.tsx` + `layout.tsx` + `page.tsx`
19. Integration test

## Verification

1. Backend starts, auto-creates default SQLite connection + `.fernet_key`
2. `curl POST /api/connections` — add PostgreSQL connection
3. `curl POST /api/connections/test` — test unsaved config
4. `curl POST /api/conversations` — create chat with connection_id
5. `curl POST /api/chat` — send message, verify persist, check multi-turn context
6. `curl GET /api/conversations` — list chats
7. `curl DELETE /api/conversations/{id}` — delete chat, messages cascade
8. Frontend: create chat via dialog, chat works, sidebar updates
9. Frontend: switch chats, each has own connection context
10. Frontend: connection manager — add, test, edit, delete
11. Restart backend — connections + conversations persisted, passwords decryptable
