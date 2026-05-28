# Database Context Understanding — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Talk-Query LLM semantic understanding of databases so it can answer meta questions ("what is this database about?") naturally, not just generate SQL.

**Architecture:** New `analyzer.py` module builds a tiered database profile (Tier 1 summary, Tier 2 per-table analysis) via parallel LLM calls. Profile cached in `database_profiles` table. Per-query: LLM ranks relevant tables, assembles filtered context (Tier 1 + Tier 2 + filtered DDL), and a flexible system prompt lets the LLM choose between EXPLAIN (meta answer) or SELECT (data query).

**Tech Stack:** Python, FastAPI, SQLAlchemy, AsyncOpenAI (DeepSeek), sqlite3 for config DB

**Known Gaps (Future Work):**
- **Tier 3 (Column Semantic Profile):** Spec defines per-column deep analysis for cryptic columns (~50 token each). Not in this plan — Tier 2 table descriptions already cover column-level semantics. Add Tier 3 if users report cryptic column names not being understood.
- **Stale profile detection:** Schema hash is computed and stored, but no background job checks it. Profile staleness is only caught when user manually triggers re-analysis. A periodic health check could auto-detect and flag stale profiles.
- **LLM classifier fallback:** Plan uses flexible prompt for EXPLAIN/SELECT routing. If LLM routing accuracy becomes an issue, add a lightweight rule-based or LLM classifier before the main prompt (as described in spec).

---

### File Map

| File | Action | Purpose |
|------|--------|---------|
| `backend/analyzer.py` | Create | Analysis engine: profile builder, table ranking, context assembly |
| `backend/connections.py` | Modify | Init `database_profiles` table + profile CRUD helpers |
| `backend/llm.py` | Modify | New flexible system prompt, `generate_answer()` replacing `generate_sql()` |
| `backend/main.py` | Modify | Integrate profile context into `stream_chat()`, add analyze endpoint |

---

### Task 1: Create `backend/analyzer.py` — Database Profile Builder

**Files:**
- Create: `backend/analyzer.py`

- [ ] **Step 1: Write the module skeleton and `DatabaseProfile` dataclass**

```python
"""Database analysis engine — builds semantic profiles and assembles per-query context."""
from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import Engine
from schema import get_tables_structured, get_table_sample, get_schema_ddl
from llm import client, MODEL


@dataclass
class DatabaseProfile:
    """Cached analysis result for a database connection."""
    connection_id: str
    db_type: str
    db_name: str
    tier1_summary: str                    # ~500 token narrative
    domain_groups: dict[str, list[str]]   # domain_name -> [table_names]
    table_descriptions: dict[str, str]    # table_name -> ~300 token description
    table_stats: dict[str, dict]          # table_name -> {row_count, columns, ...}
    schema_hash: str = ""
    created_at: str = ""
```

- [ ] **Step 2: Write the data sampling helper**

```python
def _sample_table_data(engine: Engine, table_name: str, limit: int = 5) -> dict:
    """Get sample rows + column stats for a single table."""
    sample = get_table_sample(engine, table_name, limit)
    if not sample["columns"]:
        return {"rows": [], "stats": {}}

    # Per-column stats
    stats = {}
    for col in sample["columns"]:
        values = [row[col] for row in sample["rows"] if row[col] is not None]
        distinct = len(set(str(v) for v in values))
        null_count = sum(1 for row in sample["rows"] if row[col] is None)
        stats[col] = {
            "distinct_sample": distinct,
            "null_sample": null_count,
            "sample_values": values[:3],
        }

    return {"rows": sample["rows"], "stats": stats}
```

- [ ] **Step 3: Write the Tier 1 summary generator**

```python
TIER1_PROMPT = """Analyze this database schema and provide a concise summary.

Tables and columns:
{schema_overview}

Sample data from key tables:
{samples}

Respond in English with this exact format:
PURPOSE: <2-3 sentences guessing what this database is for based on table names and structure>
DOMAINS: <group tables into business domains, e.g. "Inventory: products, stock_log, warehouses">
STATS: <total tables, approximate total rows, largest tables>
NOTES: <any interesting patterns, naming conventions, or observations>
"""


async def _generate_tier1(engine: Engine, tables: list[dict]) -> str:
    """Generate Tier 1 database summary via LLM."""
    # Build compact schema overview
    overview_lines = []
    for t in tables:
        col_list = ", ".join(f"{c['name']} ({c['type']})" for c in t["columns"][:10])
        fks = [f"{fk['columns']}->{fk['ref_table']}.{fk['ref_columns']}" for fk in t.get("foreign_keys", [])]
        fk_str = f"  FK: {', '.join(fks)}" if fks else ""
        overview_lines.append(f"- {t['name']} ({t['row_count']} rows): {col_list}{fk_str}")

    schema_overview = "\n".join(overview_lines)

    # Sample top 5 tables by row count
    top_tables = sorted(tables, key=lambda t: t["row_count"], reverse=True)[:5]
    sample_lines = []
    for t in top_tables:
        d = _sample_table_data(engine, t["name"])
        if d["rows"]:
            sample_lines.append(f"\n{t['name']} ({t['row_count']} rows):")
            sample_lines.append(json.dumps(d["rows"][:3], default=str))

    samples = "\n".join(sample_lines) if sample_lines else "(no data samples available)"

    response = await client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": TIER1_PROMPT.format(
            schema_overview=schema_overview, samples=samples,
        )}],
        temperature=0.3,
        max_tokens=800,
    )
    return response.choices[0].message.content.strip()
```

- [ ] **Step 4: Write the Tier 2 per-table analysis generator**

```python
TIER2_PROMPT = """Analyze this database table and describe its purpose.

Table: {table_name}
Columns:
{columns}

Foreign Keys:
{foreign_keys}

Sample rows (up to 5):
{sample_data}

Describe this table in 2-4 sentences. Include:
- What business entity or concept this table represents
- How it relates to other tables (based on FK references)
- Any notable patterns in the data (date ranges, code formats, enum-like values)

Respond in English. Keep it concise — max 100 words."""


async def _generate_tier2_single(engine: Engine, table: dict) -> tuple[str, str]:
    """Generate Tier 2 description for one table. Returns (table_name, description)."""
    col_lines = []
    for c in table["columns"]:
        pk = " PK" if c.get("pk") else ""
        nullable = "" if c.get("nullable", True) else " NOT NULL"
        col_lines.append(f"  {c['name']} {c['type']}{pk}{nullable}")

    fk_lines = []
    for fk in table.get("foreign_keys", []):
        fk_lines.append(f"  {fk['columns']} -> {fk['ref_table']}.{fk['ref_columns']}")

    sample = get_table_sample(engine, table["name"], 5)
    sample_str = json.dumps(sample["rows"][:5], default=str) if sample["rows"] else "(empty table)"

    response = await client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": TIER2_PROMPT.format(
            table_name=table["name"],
            columns="\n".join(col_lines),
            foreign_keys="\n".join(fk_lines) if fk_lines else "(none)",
            sample_data=sample_str,
        )}],
        temperature=0.3,
        max_tokens=400,
    )
    return table["name"], response.choices[0].message.content.strip()


async def _generate_tier2_all(engine: Engine, tables: list[dict], max_concurrent: int = 10) -> dict[str, str]:
    """Generate Tier 2 descriptions for all tables, parallel in batches."""
    descriptions: dict[str, str] = {}

    # Sort by row count descending, limit to 50
    sorted_tables = sorted(tables, key=lambda t: t["row_count"], reverse=True)[:50]

    semaphore = asyncio.Semaphore(max_concurrent)

    async def bounded(table):
        async with semaphore:
            return await _generate_tier2_single(engine, table)

    # Process in parallel batches
    for i in range(0, len(sorted_tables), max_concurrent):
        batch = sorted_tables[i:i + max_concurrent]
        tasks = [bounded(t) for t in batch]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                continue  # skip failed tables
            name, desc = result
            descriptions[name] = desc

    return descriptions
```

- [ ] **Step 5: Write the `analyze_database()` main function**

```python
def _compute_schema_hash(tables: list[dict]) -> str:
    """Hash of table names + columns to detect schema changes."""
    data = json.dumps([{
        "name": t["name"],
        "columns": [c["name"] for c in t["columns"]],
    } for t in tables], sort_keys=True)
    return hashlib.sha256(data.encode()).hexdigest()[:16]


async def analyze_database(engine: Engine, connection_id: str, db_type: str, db_name: str) -> DatabaseProfile:
    """Run full database analysis: Tier 1 + Tier 2. Stores in cache."""
    from connections import _get_local_conn

    tables = get_tables_structured(engine)

    if not tables:
        profile = DatabaseProfile(
            connection_id=connection_id,
            db_type=db_type,
            db_name=db_name,
            tier1_summary="No tables found in database.",
            domain_groups={},
            table_descriptions={},
            table_stats={},
        )
        _save_profile(profile)
        return profile

    # Generate Tier 1 and Tier 2 in parallel
    tier1_task = _generate_tier1(engine, tables)
    tier2_task = _generate_tier2_all(engine, tables)

    tier1_summary, table_descriptions = await asyncio.gather(tier1_task, tier2_task)

    # Parse domain groups from Tier 1
    domain_groups = _parse_domains(tier1_summary, tables)

    table_stats = {
        t["name"]: {
            "row_count": t["row_count"],
            "column_count": len(t["columns"]),
            "fk_count": len(t.get("foreign_keys", [])),
        }
        for t in tables
    }

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
    return profile


def _parse_domains(tier1_summary: str, tables: list[dict]) -> dict[str, list[str]]:
    """Extract domain groups from Tier 1 DOMAINS section using LLM output."""
    domains: dict[str, list[str]] = {}
    all_tables = {t["name"] for t in tables}

    for line in tier1_summary.split("\n"):
        if line.startswith("DOMAINS:") or not line.strip():
            continue
        # Try to parse patterns like: "Inventory: products, stock_log, warehouses"
        # or "- Inventory: products, stock_log"
        line = line.strip().lstrip("- ")
        if ":" in line:
            domain, tables_str = line.split(":", 1)
            domain = domain.strip()
            matched = [t.strip() for t in tables_str.split(",") if t.strip() in all_tables]
            if matched:
                domains[domain] = matched

    return domains
```

- [ ] **Step 6: Write the profile cache functions**

```python
def _save_profile(profile: DatabaseProfile):
    """Insert or update profile in talkquery_config.db."""
    from connections import _get_local_conn
    import sqlite3

    profile_json = json.dumps({
        "db_type": profile.db_type,
        "db_name": profile.db_name,
        "tier1_summary": profile.tier1_summary,
        "domain_groups": profile.domain_groups,
        "table_descriptions": profile.table_descriptions,
        "table_stats": profile.table_stats,
    })

    conn = _get_local_conn()
    try:
        conn.execute(
            """INSERT INTO database_profiles (connection_id, profile_json, schema_hash, updated_at)
               VALUES (?, ?, ?, datetime('now'))
               ON CONFLICT(connection_id) DO UPDATE SET
               profile_json = excluded.profile_json,
               schema_hash = excluded.schema_hash,
               updated_at = datetime('now')""",
            (profile.connection_id, profile_json, profile.schema_hash),
        )
        conn.commit()
    finally:
        conn.close()


def load_profile(connection_id: str) -> Optional[DatabaseProfile]:
    """Load cached profile from database, or None if not available."""
    from connections import _get_local_conn

    conn = _get_local_conn()
    try:
        row = conn.execute(
            "SELECT profile_json, schema_hash FROM database_profiles WHERE connection_id = ?",
            (connection_id,),
        ).fetchone()
        if not row:
            return None

        data = json.loads(row["profile_json"])
        return DatabaseProfile(
            connection_id=connection_id,
            db_type=data["db_type"],
            db_name=data["db_name"],
            tier1_summary=data["tier1_summary"],
            domain_groups=data["domain_groups"],
            table_descriptions=data["table_descriptions"],
            table_stats=data["table_stats"],
            schema_hash=row["schema_hash"],
        )
    finally:
        conn.close()


def delete_profile(connection_id: str):
    """Delete cached profile (called when connection is deleted)."""
    from connections import _get_local_conn

    conn = _get_local_conn()
    try:
        conn.execute("DELETE FROM database_profiles WHERE connection_id = ?", (connection_id,))
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 7: Write the table ranking function**

```python
RANKING_PROMPT = """Given this user question: "{question}"

And these table descriptions:
{table_list}

Return the top 5 most relevant tables for answering this question, ordered by relevance.
Respond ONLY with table names, one per line. No explanation."""


async def _rank_tables_by_relevance(
    question: str, table_descriptions: dict[str, str]
) -> list[str]:
    """Use LLM to rank table relevance for a user question."""
    if not table_descriptions:
        return []

    lines = [f"- {name}: {desc}" for name, desc in table_descriptions.items()]
    table_list = "\n".join(lines)

    try:
        response = await client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": RANKING_PROMPT.format(
                question=question, table_list=table_list,
            )}],
            temperature=0,
            max_tokens=150,
        )
        # Parse table names from response
        result = []
        for line in response.choices[0].message.content.strip().split("\n"):
            name = line.strip().lstrip("- ").split(":")[0].strip()
            if name in table_descriptions:
                result.append(name)
        return result[:5]
    except Exception:
        return []  # Fallback to keyword matching
```

- [ ] **Step 8: Write the FK traversal helper**

```python
def _fk_connected_tables(
    tables: list[str], all_tables: list[dict], max_depth: int = 1
) -> list[str]:
    """Expand table set to include FK-connected tables."""
    connected = set(tables)

    # Build FK graph
    fk_graph: dict[str, list[str]] = {}
    for t in all_tables:
        fk_graph[t["name"]] = []
        for fk in t.get("foreign_keys", []):
            ref = fk.get("ref_table", "")
            if ref:
                fk_graph[t["name"]].append(ref)
                # Also add reverse edge
                if ref not in fk_graph:
                    fk_graph[ref] = []
                fk_graph[ref].append(t["name"])

    for _ in range(max_depth):
        new = set(connected)
        for t in connected:
            if t in fk_graph:
                new.update(fk_graph[t])
        connected = new

    return list(connected)
```

- [ ] **Step 9: Write the `get_relevant_context()` assembly function**

```python
def _keyword_fallback(question: str, table_descriptions: dict[str, str]) -> list[str]:
    """Simple keyword matching fallback when LLM ranking fails."""
    question_lower = question.lower()
    matches = []
    for name in table_descriptions:
        if name.lower() in question_lower:
            matches.append(name)
    return matches[:5]


async def get_relevant_context(profile: DatabaseProfile, user_question: str, engine) -> str:
    """Assemble Tier 1 + relevant Tier 2 + filtered DDL for a user question."""
    # Rank relevant tables
    ranked = await _rank_tables_by_relevance(user_question, profile.table_descriptions)

    if not ranked:
        ranked = _keyword_fallback(user_question, profile.table_descriptions)

    # Get full table metadata for FK traversal
    tables = get_tables_structured(engine)
    relevant = _fk_connected_tables(ranked, tables)

    # Add domain group peers
    for domain, domain_tables in profile.domain_groups.items():
        if any(t in relevant for t in domain_tables):
            relevant.extend(domain_tables)

    # Deduplicate while preserving order
    relevant = list(dict.fromkeys(relevant))

    # Build context
    parts = ["=== Database Summary ===", profile.tier1_summary]

    if relevant:
        parts.append("\n=== Relevant Table Details ===")
        for table_name in relevant:
            if table_name in profile.table_descriptions:
                parts.append(f"\n--- {table_name} ---")
                parts.append(profile.table_descriptions[table_name])

    # Filtered DDL
    parts.append("\n=== Relevant Schema (DDL) ===")
    if relevant:
        # Build DDL manually for relevant tables only
        inspector = __import__('sqlalchemy').inspect(engine)
        for table_name in relevant:
            try:
                columns = inspector.get_columns(table_name)
                fks = inspector.get_foreign_keys(table_name)
                col_defs = []
                for col in columns:
                    line = f"  {col['name']} {col['type']}"
                    if col.get('primary_key'):
                        line += " PRIMARY KEY"
                    if not col.get('nullable', True):
                        line += " NOT NULL"
                    col_defs.append(line)
                for fk in fks:
                    cols = ", ".join(fk['constrained_columns'])
                    ref_cols = ", ".join(fk['referred_columns'])
                    col_defs.append(f"  FOREIGN KEY ({cols}) REFERENCES {fk['referred_table']}({ref_cols})")
                ddl = f"CREATE TABLE {table_name} (\n" + ",\n".join(col_defs) + "\n);"
                parts.append(ddl)
            except Exception:
                parts.append(f"-- {table_name}: (schema unavailable)")
    else:
        # No relevant tables found, use full DDL
        parts.append(get_schema_ddl(engine))

    return "\n\n".join(parts)


def get_tier1_only_context(profile: DatabaseProfile) -> str:
    """Return just Tier 1 summary — for meta questions with no specific tables."""
    return f"=== Database Summary ===\n{profile.tier1_summary}"
```

- [ ] **Step 10: Commit**

```bash
git add backend/analyzer.py
git commit -m "feat(api): add database analysis engine with tiered context builder"
```

---

### Task 2: Add `database_profiles` Table to Connections Module

**Files:**
- Modify: `backend/connections.py`

- [ ] **Step 1: Add `init_database_profiles_table()` function after `init_connections_table()`**

In `backend/connections.py`, add after line 51 (`conn.close()` in `init_connections_table`):

```python
def init_database_profiles_table():
    conn = _get_local_conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS database_profiles (
                connection_id TEXT PRIMARY KEY,
                profile_json TEXT NOT NULL,
                schema_hash TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (connection_id) REFERENCES connections(id) ON DELETE CASCADE
            )
        """)
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 2: Call `init_database_profiles_table()` and cascade delete in `delete_connection()`**

In `delete_connection()` (around line 157), after `conn.execute("DELETE FROM connections...")`, profile is already cascade-deleted via FK. No change needed.

- [ ] **Step 3: Commit**

```bash
git add backend/connections.py
git commit -m "feat(api): add database_profiles table for caching analysis results"
```

---

### Task 3: Modify `backend/llm.py` — Flexible Prompt + `generate_answer()`

**Files:**
- Modify: `backend/llm.py`

- [ ] **Step 1: Add the new context-aware system prompt**

In `backend/llm.py`, add after the existing `SYSTEM_PROMPT` (after line 24):

```python
SYSTEM_PROMPT_WITH_CONTEXT = """You are a SQL expert and data analyst. Given the database context and schema below, answer user questions.

Response format:
- If the question asks ABOUT the database (purpose, structure, relationships, patterns):
  EXPLAIN: <natural language answer from the context, in the same language as the question>

- If the question asks for specific DATA (list, count, filter, aggregate):
  SELECT <query>

SQL rules:
- Only SELECT. No INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE.
- Always add LIMIT 100 if user doesn't specify a limit.

Database Context:
{context}

Relevant Schema:
{schema}"""
```

- [ ] **Step 2: Add the `generate_answer()` function**

After the existing `generate_sql()` function, add:

```python
async def generate_answer(question: str, schema: str, context: str, style: str = "normal") -> tuple[str, str]:
    """Generate either SQL or EXPLAIN response. Returns (type, content).

    type is "sql", "explain", or "fallback" (use old behavior).
    """
    system = SYSTEM_PROMPT_WITH_CONTEXT.format(context=context, schema=schema)

    user_message = question
    if style in ("rtk", "caveman+rtk"):
        user_message = f"Terse query: {question}"

    response = await client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_message},
        ],
        temperature=0,
        max_tokens=1000,
    )
    content = response.choices[0].message.content.strip()

    # Parse response format
    upper = content.upper()
    if upper.startswith("EXPLAIN:"):
        return ("explain", content[len("EXPLAIN:"):].strip())
    elif upper.startswith("SELECT") or upper.startswith("WITH"):
        return ("sql", content)
    else:
        # Fallback: try to extract SQL, otherwise treat as explain
        if "SELECT" in upper:
            return ("sql", content)
        return ("explain", content)
```

- [ ] **Step 3: Commit**

```bash
git add backend/llm.py
git commit -m "feat(api): add flexible prompt with EXPLAIN/SELECT response routing"
```

---

### Task 4: Modify `backend/main.py` — Integrate Context into Chat

**Files:**
- Modify: `backend/main.py`

- [ ] **Step 1: Import new modules**

In `backend/main.py`, update imports (around line 10-14):

```python
from llm import generate_sql, generate_response, generate_answer
from schema import get_schema_ddl, get_schema_compact
# Add:
from analyzer import load_profile, get_relevant_context, get_tier1_only_context, analyze_database
import asyncio
```

- [ ] **Step 2: Modify `stream_chat()` to use context when available**

Replace the current `stream_chat()` schema + SQL generation block (lines 155-186 in the original) with:

```python
    # Load profile and build context
    profile = load_profile(conn_row["id"])
    context = ""
    schema = ""

    if profile:
        # Check if user question is meta or data
        try:
            context = await get_relevant_context(profile, message, engine)
        except Exception:
            # Fallback: Tier 1 only
            context = get_tier1_only_context(profile)

        # Use filtered DDL from context, or full DDL if no profile
        schema = get_schema_compact(engine) if style in ("rtk", "caveman+rtk") else get_schema_ddl(engine)
    else:
        # No profile yet — fallback to existing behavior
        schema = get_schema_compact(engine) if style in ("rtk", "caveman+rtk") else get_schema_ddl(engine)

    # Multi-turn context
    llm_message = message
    if conversation_id:
        ctx = get_conversation_context(conversation_id)
        if ctx:
            ctx_text = _format_context(ctx)
            if style in ("rtk", "caveman+rtk"):
                llm_message = f"Context:\n{ctx_text}\n\nTerse query: {message}"
            else:
                llm_message = f"{ctx_text}\n\nCurrent question: {message}"

    try:
        if profile:
            answer_type, answer_content = await generate_answer(llm_message, schema, context, style)
        else:
            # Old behavior: SQL only
            answer_type = "sql"
            answer_content = await generate_sql(llm_message, schema, style)
    except Exception as e:
        yield _sse_event({"type": "error", "message": f"LLM Error: {str(e)}"})
        yield _sse_event({"type": "done"})
        return

    # Handle EXPLAIN responses (meta questions)
    if answer_type == "explain":
        yield _sse_event({"type": "result", "question": message, "sql": "",
                          "columns": [], "rows": [], "row_count": 0,
                          "response": answer_content})
        yield _sse_event({"type": "done"})
        # Persist
        if conversation_id:
            try:
                save_message(conversation_id, "user", message, style=style)
                save_message(conversation_id, "assistant", answer_content, sql="",
                            result_json=json.dumps({"columns": [], "rows": [], "row_count": 0}),
                            style=style)
            except Exception:
                pass
        return

    # Handle SELECT responses (data questions) — existing flow
    if answer_content.upper().startswith("ERROR:"):
        yield _sse_event({"type": "rejected", "message": answer_content[7:].strip()})
        yield _sse_event({"type": "done"})
        return

    valid, sql_or_error = validate_sql(answer_content)
    if not valid:
        yield _sse_event({"type": "rejected", "message": sql_or_error})
        yield _sse_event({"type": "done"})
        return

    sql = sql_or_error
    yield _sse_event({"type": "sql", "sql": sql})
    yield _sse_event({"type": "status", "status": "executing"})

    # ... rest of existing execution + response generation + persistence unchanged
```

- [ ] **Step 3: Add async analysis trigger on connection creation**

In the `create_connection_endpoint()` (around line 320), add after `conn = create_connection(data)`:

```python
@app.post("/api/connections", status_code=201)
async def create_connection_endpoint(data: ConnectionCreate):
    conn = create_connection(data)
    # Trigger async analysis in background
    try:
        engine = get_engine(conn.id)
        db_type = data.db_type
        db_name = data.database_name
        asyncio.create_task(analyze_database(engine, conn.id, db_type, db_name))
    except Exception:
        pass  # Analysis is non-blocking; failure is silent
    return conn
```

- [ ] **Step 4: Add analyze endpoint**

After the test connection endpoints (around line 363), add:

```python
@app.post("/api/connections/{conn_id}/analyze")
async def analyze_connection(conn_id: str):
    """Trigger (re-)analysis of a database connection."""
    conn_row = get_connection_with_password(conn_id)
    if not conn_row:
        raise HTTPException(status_code=404, detail="Connection not found")

    engine = get_engine(conn_id)
    try:
        profile = await analyze_database(
            engine, conn_id,
            db_type=conn_row["db_type"],
            db_name=conn_row["database_name"],
        )
        return {
            "status": "ok",
            "message": "Analysis complete",
            "tables_analyzed": len(profile.table_descriptions),
            "tier1_summary": profile.tier1_summary,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")
```

- [ ] **Step 5: Update startup to init the new table**

In the `startup()` function (around line 56), add `init_database_profiles_table()`:

```python
@app.on_event("startup")
async def startup():
    from connections import init_database_profiles_table
    init_connections_table()
    init_conversations_table()
    init_database_profiles_table()
    # Auto-create default connection from DATABASE_URL if none exist
    conn = ensure_default_connection()
    if conn:
        print(f"[startup] Created default connection: {conn.name}")
```

- [ ] **Step 6: Commit**

```bash
git add backend/main.py
git commit -m "feat(api): integrate database context into chat flow with async analysis"
```

---

### Task 5: Remove Stale Context on Connection Delete

**Files:**
- Modify: `backend/connections.py`

- [ ] **Step 1: Call `delete_profile()` in `delete_connection()`**

In `delete_connection()` (around line 157), before deleting the connection row, also delete the profile. The FK `ON DELETE CASCADE` handles this automatically, but we also call explicit cleanup:

Actually — `ON DELETE CASCADE` already handles it. If it doesn't fire (sqlite sometimes misses cascades on errors), add an explicit call. The current `delete_connection` already has inline delete. The FK cascade is sufficient since we enable `PRAGMA foreign_keys = ON` in `_get_local_conn()`. No changes needed.

- [ ] **Step 2: Commit**

```bash
# No code changes — FK cascade already handles it
echo "No changes needed — cascade delete already configured"
```

---

### Task 6: Integration Test (Manual)

**Files:**
- None

- [ ] **Step 1: Start the backend**

```bash
cd backend && source venv/bin/activate && python main.py
```

Expected: Server starts on port 8000, no errors, `database_profiles` table created.

- [ ] **Step 2: Create a connection and verify async analysis**

```bash
# Create SQLite connection to sample database
curl -X POST http://localhost:8000/api/connections \
  -H "Content-Type: application/json" \
  -d '{"name": "Test DB", "db_type": "sqlite", "database_name": "../data/talkquery.db"}'
```

Expected: Returns connection JSON. Then wait 10-30 seconds.

- [ ] **Step 3: Verify profile was created**

```bash
# Check the profile exists (via manual SQLite query)
sqlite3 data/talkquery_config.db "SELECT connection_id, schema_hash, length(profile_json) FROM database_profiles;"
```

Expected: Row with connection_id and non-empty profile_json.

- [ ] **Step 4: Test meta question**

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Apa isi database ini?", "style": "normal"}'
```

Expected: SSE stream with `type: "result"`, empty SQL, `response` containing a natural language explanation about the database contents.

- [ ] **Step 5: Test data question still works**

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "tampilkan semua tabel yang ada", "style": "normal"}'
```

Expected: SSE stream with `type: "sql"`, `type: "result"` with rows.

- [ ] **Step 6: Test manual re-analysis**

```bash
curl -X POST http://localhost:8000/api/connections/<CONN_ID>/analyze
```

Expected: `{"status": "ok", "message": "Analysis complete", ...}`

- [ ] **Step 7: Commit (if any fixes applied)**

```bash
git add -A
git commit -m "test: verify database context understanding integration"
```
