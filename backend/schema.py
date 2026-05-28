from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError


def _safe_inspect_columns(inspector, table: str) -> tuple[list, list]:
    """Get columns and FKs, handling permission errors gracefully."""
    try:
        columns = inspector.get_columns(table)
    except SQLAlchemyError:
        columns = []

    try:
        fks = inspector.get_foreign_keys(table)
    except SQLAlchemyError:
        fks = []

    return columns, fks


def _safe_row_count(engine: Engine, table: str) -> int:
    try:
        with engine.connect() as conn:
            return conn.execute(text(f'SELECT COUNT(*) FROM "{table}"')).scalar()
    except Exception:
        return -1  # -1 means "unknown"


def get_schema_ddl(engine: Engine) -> str:
    """Extract full schema DDL from the given engine."""
    inspector = inspect(engine)
    tables = inspector.get_table_names()

    if not tables:
        return "No tables found in database."

    lines = []
    for table in tables:
        columns, fks = _safe_inspect_columns(inspector, table)

        col_defs = []
        for col in columns:
            col_line = f"  {col['name']} {col['type']}"
            if col.get('primary_key'):
                col_line += " PRIMARY KEY"
            if not col.get('nullable', True):
                col_line += " NOT NULL"
            if col.get('default'):
                col_line += f" DEFAULT {col['default']}"
            col_defs.append(col_line)

        for fk in fks:
            cols = ", ".join(fk['constrained_columns'])
            ref_cols = ", ".join(fk['referred_columns'])
            col_defs.append(f"  FOREIGN KEY ({cols}) REFERENCES {fk['referred_table']}({ref_cols})")

        create_sql = f"CREATE TABLE {table} (\n" + ",\n".join(col_defs) + "\n);"
        lines.append(create_sql)

        count = _safe_row_count(engine, table)
        if count >= 0:
            lines.append(f"-- {count} rows")
        else:
            lines.append("-- (row count unavailable)")

    return "\n\n".join(lines)


def get_schema_compact(engine: Engine) -> str:
    """Extract schema in terse one-line-per-table format for RTK mode."""
    inspector = inspect(engine)
    tables = inspector.get_table_names()

    if not tables:
        return "No tables."

    lines = []
    for table in tables:
        columns, fks = _safe_inspect_columns(inspector, table)

        col_parts = []
        for col in columns:
            part = f"{col['name']} {col['type']}"
            if col.get('primary_key'):
                part += " PK"
            if not col.get('nullable', True):
                part += " NOTNULL"
            col_parts.append(part)

        for fk in fks:
            cols = ", ".join(fk['constrained_columns'])
            ref_cols = ", ".join(fk['referred_columns'])
            col_parts.append(f"FK({cols}→{fk['referred_table']}.{ref_cols})")

        count = _safe_row_count(engine, table)
        if count >= 0:
            lines.append(f"{table}: {', '.join(col_parts)} -- {count} rows")
        else:
            lines.append(f"{table}: {', '.join(col_parts)}")

    return "\n".join(lines)


def get_table_names(engine: Engine) -> list[str]:
    """Get list of table names."""
    inspector = inspect(engine)
    return inspector.get_table_names()


def get_tables_structured(engine: Engine) -> list[dict]:
    """Get structured table metadata for the schema explorer."""
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    result = []
    for table in tables:
        columns, fks = _safe_inspect_columns(inspector, table)

        col_list = [{
            "name": c.get("name", "?"),
            "type": str(c.get("type", "?")),
            "pk": c.get("primary_key", False),
            "nullable": c.get("nullable", True),
        } for c in columns]

        fk_list = [{
            "columns": fk.get("constrained_columns", []),
            "ref_table": fk.get("referred_table", ""),
            "ref_columns": fk.get("referred_columns", []),
        } for fk in fks]

        count = _safe_row_count(engine, table)

        result.append({
            "name": table,
            "columns": col_list,
            "foreign_keys": fk_list,
            "row_count": count,
        })
    return result


def get_table_sample(engine: Engine, table_name: str, limit: int = 5) -> dict:
    """Get sample rows from a table."""
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    if table_name not in tables:
        return {"columns": [], "rows": [], "row_count": 0}

    try:
        with engine.connect() as conn:
            result = conn.execute(text(f'SELECT * FROM "{table_name}" LIMIT {limit}'))
            rows = result.fetchall()
            columns = list(result.keys())
            return {
                "columns": columns,
                "rows": [dict(zip(columns, row)) for row in rows],
                "row_count": len(rows),
            }
    except Exception:
        return {"columns": [], "rows": [], "row_count": 0}
