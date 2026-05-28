import os
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()

client = AsyncOpenAI(
    api_key=os.getenv("LLM_API_KEY", "sk-placeholder"),
    base_url=os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1"),
)

MODEL = os.getenv("LLM_MODEL", "deepseek-chat")

SYSTEM_PROMPT = os.getenv(
    "SYSTEM_PROMPT",
    """You are a SQL expert. Given the schema below, convert user questions to SQL.
- Only SELECT queries. No INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE.
- Always add LIMIT 100 if user doesn't specify a limit.
- Return ONLY the SQL query, nothing else. No markdown, no explanation.
- If the question is not answerable with SELECT, respond: ERROR: Only SELECT queries allowed.

Schema:
{schema}""",
)

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

RESPONSE_SYSTEM_PROMPT = """You are a helpful data assistant. Given the user's question, the SQL query, and the query results, provide a natural language answer.
- Summarize the results clearly in 1-3 sentences.
- Mention the key numbers or insights.
- Keep it conversational and friendly.
- If results are empty, say so and suggest why."""

CAVEMAN_RESPONSE_PROMPT = """You are a terse data assistant. Given the user's question, the SQL query, and the query results, provide an ultra-brief answer.
- No articles, no filler, no pleasantries. Fragments OK.
- State the numbers and key facts directly.
- One short paragraph maximum.
- If results are empty, say so in one line."""


async def generate_sql(question: str, schema: str, style: str = "normal") -> str:
    """Generate SQL from user question using LLM."""
    system = SYSTEM_PROMPT.format(schema=schema)

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
        max_tokens=500,
    )
    return response.choices[0].message.content.strip()


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
        # Try to extract SQL from mixed response (narration + SQL)
        import re
        sql_match = re.search(
            r'\b(SELECT\b[\s\S]+?)(?:;|\Z)', content,
            re.IGNORECASE,
        )
        if sql_match:
            return ("sql", sql_match.group(1).strip())
        # If response mentions SELECT but doesn't have real SQL, treat as explain
        return ("explain", content)


async def generate_response(question: str, sql: str, results: dict, style: str = "normal") -> str:
    """Generate natural language response from query results."""
    result_summary = format_results_for_llm(results)

    sys_prompt = CAVEMAN_RESPONSE_PROMPT if style in ("caveman", "caveman+rtk") else RESPONSE_SYSTEM_PROMPT

    response = await client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": sys_prompt},
            {
                "role": "user",
                "content": f"Question: {question}\nSQL: {sql}\nResults: {result_summary}",
            },
        ],
        temperature=0.3,
        max_tokens=300,
    )
    return response.choices[0].message.content.strip()


def format_results_for_llm(results: dict) -> str:
    """Format query results compactly for LLM consumption."""
    if not results["columns"]:
        return "Query executed successfully (no rows returned)."
    cols = results["columns"]
    rows = results["rows"][:20]  # limit rows sent to LLM
    lines = [", ".join(cols)]
    for row in rows:
        lines.append(", ".join(str(row[c]) for c in cols))
    extra = ""
    if results["row_count"] > 20:
        extra = f"\n... and {results['row_count'] - 20} more rows."
    return "\n".join(lines) + extra
