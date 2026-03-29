# Memory System Spec

## Modules

- `src/memory/short_term.py`
- `src/memory/long_term.py`

## Short-term Memory

### Implementation
`collections.deque` with configurable max length (default 50).

### API
- `add(msg: Message)` — append message
- `add_user(content: str)` — convenience for user messages
- `add_assistant(content: str, tool_calls?)` — convenience for assistant messages
- `add_tool_result(tool_call_id, content)` — tool response messages
- `clear()` — reset conversation
- `format_context() -> list[dict]` — OpenAI-compatible format for API

## Long-term Memory

### Implementation
SQLite database at `{data_dir}/memory.db`. 3 tables:

#### facts table
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto-increment |
| category | TEXT | Fact category (e.g. "personal", "preference") |
| key | TEXT | Unique key for upsert |
| content | TEXT | Fact content |
| importance | REAL | 0.0-1.0 importance score |
| created_at | TIMESTAMP | Creation time |
| updated_at | TIMESTAMP | Last update time |

#### conversations table
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto-increment |
| role | TEXT | Message role |
| content | TEXT | Message content |
| timestamp | TIMESTAMP | Message time |

#### user_preferences table
| Column | Type | Description |
|--------|------|-------------|
| key | TEXT PK | Preference key |
| value | TEXT | Preference value |
| updated_at | TIMESTAMP | Last update |

### API
- `add_fact(category, key, content, importance=0.5)` — INSERT OR REPLACE
- `get_facts(limit=100)` — return all facts sorted by importance desc
- `search_facts(query)` — LIKE search across content
- `delete_fact(fact_id)` — remove by id
- `build_context_block() -> str` — formatted context for system prompt injection
- `save_message(role, content)` — log conversation to DB
- `get_recent_conversations(limit=20)` — recent chat history
- `set_preference(key, value)` / `get_preference(key)` — user preferences
