# Talk-Query

Natural language to SQL — chat with your databases.

**Connect any database, ask questions in plain language, get answers.** Supports SQLite, MySQL, and PostgreSQL. AI-powered with semantic database understanding.

## Features

- **Multi-DB support** — SQLite, MySQL, PostgreSQL
- **Natural language queries** — ask questions in English or Indonesian
- **Database context understanding** — AI automatically analyzes your schema and understands what your database is about
- **Meta questions** — "What is this database about?", "How do these tables relate?"
- **Multi-turn conversations** — follow-up questions with context
- **Read-only** — only SELECT queries, no data modification
- **RTK/caveman mode** — terse responses for speed

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | FastAPI, SQLAlchemy, AsyncOpenAI |
| LLM | DeepSeek (v4-pro / v4-flash) |
| Frontend | Next.js 16, Tailwind CSS, shadcn/ui |
| Config DB | SQLite (talkquery_config.db) |

## Quick Start

### Prerequisites

- Python 3.12+
- Node.js 20+
- LLM API key (DeepSeek)

### Backend

```bash
cd backend
cp .env.example .env
# Edit .env — set LLM_API_KEY and LLM_BASE_URL

python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python main.py
# Backend runs on http://localhost:8000
```

### Frontend

```bash
cd frontend
cp .env.example .env.local  # if exists
npm install
npm run dev
# Frontend runs on http://localhost:3000
```

### Production

```bash
./start.sh
# Backend: http://localhost:8001
# Frontend: http://localhost:3001
```

## Architecture

```
User (browser) → Frontend (Next.js) → Backend (FastAPI) → User's Database
                                              ↓
                                         LLM (DeepSeek)
                                              ↓
                                    Database Profile Cache
                                    (talkquery_config.db)
```

### Database Context Understanding

The AI automatically analyzes connected databases in three tiers:

| Tier | Content | When |
|------|---------|------|
| **Tier 1** | Database summary — purpose, domain groups, stats (~500 tokens) | Always in system prompt |
| **Tier 2** | Per-table description — columns, relationships, sample data (~300 tokens/table) | On-demand, when user asks about specific tables |
| **Tier 3** | Column semantic profile (~50 tokens/column) | Future |

Analysis runs async on connection and can be re-triggered via:
- **UI** — "Analyze" button in chat header or Connection Manager
- **Chat** — type "analisis ulang database" or "refresh database context"
- **API** — `POST /api/connections/{id}/analyze`

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/health` | Health check |
| `POST` | `/api/chat` | Send chat message (SSE stream) |
| `GET` | `/api/schema` | Get database schema |
| `GET` | `/api/connections` | List connections |
| `POST` | `/api/connections` | Create connection |
| `PUT` | `/api/connections/{id}` | Update connection |
| `DELETE` | `/api/connections/{id}` | Delete connection |
| `POST` | `/api/connections/test` | Test unsaved connection |
| `POST` | `/api/connections/{id}/test` | Test saved connection |
| `POST` | `/api/connections/{id}/analyze` | Trigger database analysis |
| `GET` | `/api/conversations` | List conversations |
| `POST` | `/api/conversations` | Create conversation |
| `PATCH` | `/api/conversations/{id}` | Update conversation |
| `DELETE` | `/api/conversations/{id}` | Delete conversation |
| `POST` | `/api/feedback` | Submit message feedback |

## Project Structure

```
talk-query/
├── backend/
│   ├── main.py            # FastAPI app, endpoints, chat flow
│   ├── analyzer.py        # Database analysis engine (Tier 1-2)
│   ├── llm.py             # LLM client, prompts, generate_answer()
│   ├── schema.py          # Schema extraction (DDL, compact, samples)
│   ├── connections.py     # Connection CRUD + engine management
│   ├── conversations.py   # Conversation + message persistence
│   ├── db.py              # SQL execution
│   ├── models.py          # Pydantic models
│   ├── crypto.py          # Password encryption
│   └── seed.py            # Sample data seeder
├── frontend/
│   └── src/
│       ├── app/           # Next.js app router
│       ├── components/    # React components
│       └── lib/           # API client, stores, utils
├── data/                  # Runtime DB files (gitignored)
├── docs/
│   └── superpowers/
│       ├── specs/         # Design specs
│       └── plans/         # Implementation plans
└── start.sh               # Production launcher
```
