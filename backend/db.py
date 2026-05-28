from sqlalchemy import text


def execute_sql(sql: str, timeout: int, engine, db_type: str) -> dict:
    """Execute a SELECT query against any supported database type."""
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
        return {
            "columns": columns,
            "rows": [dict(zip(columns, row)) for row in rows],
            "row_count": len(rows),
        }
