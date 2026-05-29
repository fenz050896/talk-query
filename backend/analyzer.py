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

# Track in-progress analyses to prevent concurrent runs per connection
_analysis_locks: set[str] = set()


def init_database_profiles_table():
    """Create database_profiles table if it doesn't exist."""
    from connections import _get_local_conn
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
        fks = [f"{', '.join(fk['columns'])} -> {fk['ref_table']}.{', '.join(fk['ref_columns'])}" for fk in t.get("foreign_keys", [])]
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
    if not response.choices:
        return ""
    content = response.choices[0].message.content
    if not content:
        return ""
    return content.strip()


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
        fk_lines.append(f"  {', '.join(fk['columns'])} -> {fk['ref_table']}.{', '.join(fk['ref_columns'])}")

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
    if not response.choices:
        return table["name"], ""
    content = response.choices[0].message.content
    if not content:
        return table["name"], ""
    return table["name"], content.strip()


async def _generate_tier2_all(engine: Engine, tables: list[dict], max_concurrent: int = 10) -> dict[str, str]:
    """Generate Tier 2 descriptions for all tables, parallel in batches."""
    descriptions: dict[str, str] = {}

    # Sort by row count descending, limit to 50
    sorted_tables = sorted(tables, key=lambda t: t["row_count"], reverse=True)[:50]

    # Process in parallel batches
    for i in range(0, len(sorted_tables), max_concurrent):
        batch = sorted_tables[i:i + max_concurrent]
        tasks = [_generate_tier2_single(engine, t) for t in batch]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                print(f"[analyzer] Failed to analyze table in batch: {result}")
                continue
            name, desc = result
            descriptions[name] = desc

    return descriptions


def _compute_schema_hash(tables: list[dict]) -> str:
    """Hash of table names + columns to detect schema changes."""
    data = json.dumps([{
        "name": t["name"],
        "columns": [c["name"] for c in t["columns"]],
    } for t in tables], sort_keys=True)
    return hashlib.sha256(data.encode()).hexdigest()[:16]


async def analyze_database(engine: Engine, connection_id: str, db_type: str, db_name: str, force: bool = False) -> DatabaseProfile:
    """Run full database analysis: Tier 1 + Tier 2. Stores in cache.

    If force=False and schema hasn't changed, raises RuntimeError with message
    'Analysis is still valid. Use force=true to re-analyze.'
    """
    if connection_id in _analysis_locks:
        raise RuntimeError("Analysis already in progress for this connection.")

    # Check if schema changed
    if not force:
        tables = get_tables_structured(engine)
        current_hash = _compute_schema_hash(tables)
        existing = load_profile(connection_id)
        if existing and existing.schema_hash == current_hash:
            raise RuntimeError(
                f"Analysis is still valid ({len(existing.table_descriptions)} tables). "
                "Schema hasn't changed. Use 'refresh database context' to force re-analysis."
            )

    _analysis_locks.add(connection_id)
    try:
        return await _analyze_database_impl(engine, connection_id, db_type, db_name)
    finally:
        _analysis_locks.discard(connection_id)


async def _analyze_database_impl(engine: Engine, connection_id: str, db_type: str, db_name: str) -> DatabaseProfile:
    """Internal implementation — caller must hold _analysis_locks."""
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

    # Build table embeddings for semantic search
    try:
        from embeddings import build_table_index
        build_table_index(connection_id, profile.table_descriptions)
    except Exception:
        pass  # Non-critical — chat still works without embeddings

    return profile


def _parse_domains(tier1_summary: str, tables: list[dict]) -> dict[str, list[str]]:
    """Extract domain groups from Tier 1 DOMAINS section using LLM output."""
    import re

    domains: dict[str, list[str]] = {}
    all_tables = {t["name"] for t in tables}

    for line in tier1_summary.split("\n"):
        line = line.strip()
        if not line:
            continue
        # Parse "DOMAINS: Inventory: products, stock_log; Sales: orders, invoices"
        if line.startswith("DOMAINS:"):
            line = line[len("DOMAINS:"):].strip()
        # Skip other header lines
        if line.startswith("PURPOSE:") or line.startswith("STATS:") or line.startswith("NOTES:"):
            continue
        line = line.lstrip("- ")
        if ":" in line:
            domain, tables_str = line.split(":", 1)
            domain = domain.strip()
            # Split on either comma or semicolon
            table_names = re.split(r'[,;]', tables_str)
            matched = [t.strip() for t in table_names if t.strip() in all_tables]
            if matched:
                domains[domain] = matched

    return domains


def _save_profile(profile: DatabaseProfile):
    """Insert or update profile in talkquery_config.db."""
    from connections import _get_local_conn
    init_database_profiles_table()

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
    init_database_profiles_table()

    conn = _get_local_conn()
    try:
        row = conn.execute(
            "SELECT profile_json, schema_hash, created_at FROM database_profiles WHERE connection_id = ?",
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
            created_at=row["created_at"],
        )
    finally:
        conn.close()


def delete_profile(connection_id: str):
    """Delete cached profile (called when connection is deleted)."""
    from connections import _get_local_conn
    init_database_profiles_table()

    conn = _get_local_conn()
    try:
        conn.execute("DELETE FROM database_profiles WHERE connection_id = ?", (connection_id,))
        conn.commit()
    finally:
        conn.close()


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
        # Note: DDL formatting mirrors schema.py:get_schema_ddl. Keep in sync.
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
