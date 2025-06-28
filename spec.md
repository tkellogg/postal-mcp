### 1  Problem Statement

We want **any two (or more) LLM-powered “agents” to carry out an asynchronous, bidirectional conversation** using the Model-Context-Protocol (MCP) *streamable-HTTP* transport.
Key constraints:

* Each agent speaks *only* MCP; it cannot open sockets or subscribe to external queues.
* We cannot predetermine the complete set of agents ahead of time.
* The solution must be lightweight enough to run on a single developer laptop.

### 2  Strategy in One Sentence

> **Give every agent its own FIFO “mailbox” in a shared SQLite database and expose a single HTTP endpoint `/agents/{agent}/mcp/`; the FastMCP runtime uses two MCP tools—`send_to_agent` and `check_mail`—to push to and pop from those mailboxes.**

### 3  High-level Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                SQLite: db/messages.sqlite                        │
│  id TEXT PK | from_agent | to_agent | content | created | done   │
└──────┬──────────────┬───────────────┬──────────┬─────────┬───────┘
       │              │               │
       │              │               │
/agents/alice/mcp/  /agents/bob/mcp/  /agents/charlie/mcp/
(Starlette sub-app) (same sub-app)    (same sub-app)
       │              │               │
       └──────────────┴───────────────┴─────→ FastAPI root server
```

* **Single FastAPI “root” app** mounted at `/agents`.
* **One reusable Starlette sub-app**, created with
  `fastmcp.server.http.create_streamable_http_app()`, mounted only once but declared internally at `/{agent}/mcp/`.
* Each HTTP POST arrives with the path parameter `agent`, so the same Python objects can service *any* caller.

### 4  Detailed Component Spec

#### 4.1 SQLite Mailbox

| Column       | Type      | Purpose                                                    |
| ------------ | --------- | ---------------------------------------------------------- |
| `id`         | TEXT PK   | Unique UUID. The sender may supply it or we auto-generate. |
| `from_agent` | TEXT      | Filled **after** a send; required for receipts.            |
| `to_agent`   | TEXT      | Destination agent name (matches `{agent}` path param).     |
| `content`    | TEXT      | The actual message payload (UTF-8).                        |
| `created`    | TIMESTAMP | `UTC` insertion time.                                      |
| `done`       | INTEGER   | `0` = unread, `1` = popped by the recipient.               |

* **Atomic FIFO pop**:

  ```
  BEGIN IMMEDIATE;
  SELECT ... FOR UPDATE WHERE to_agent=? AND done=0 ORDER BY created LIMIT 1;
  UPDATE messages SET done=1 WHERE id=?;
  COMMIT;
  ```

  Using SQLite’s `BEGIN IMMEDIATE` prevents two agents from popping the same row.

* **Scale expectations**: tested at \~100 msg/s on a laptop; swap to Postgres or Redis when that is not enough.

#### 4.2 MCP Tools

1. **`send_to_agent(name: str, msg: str, msg_id: str | None)` → `str`**

   * Validates `name` (non-empty, URL safe).
   * Inserts a row with `from_agent = <caller>`, `to_agent = name`, `done = 0`.
   * Returns the `msg_id` (existing or generated).

2. **`check_mail()` → `null | {id, from, content}`**

   * Pops the *oldest* unread row where `to_agent = <caller>`.
   * Marks it `done = 1`.
   * Returns `null` when mailbox is empty.

Both tools rely on an **internal helper `_who_am_i()`** that inspects the current `Request` object (`fastmcp.server.http._current_http_request`) and extracts `request.path_params["agent"]`.  No global state or per-agent FastMCP objects are required.

#### 4.3 Starlette / FastAPI Mount Logic

```python
from fastapi import FastAPI
from fastmcp import FastMCP
from fastmcp.server.http import create_streamable_http_app, _current_http_request

mcp = FastMCP("generic")                       # Name overridden per request

# Tools (see §4.2) -----------------------------------------------
# ...

sub_app = create_streamable_http_app(
    server=mcp,
    streamable_http_path="/{agent}/mcp/",      # <-- dynamic segment
    json_response=True
)

api = FastAPI(lifespan=sub_app.lifespan)       # keeps StreamableHTTPSessionManager alive
api.mount("/agents", sub_app)                  # literal prefix only
```

* **Why a literal mount?** `FastAPI.mount()` passes through unchanged path segments; Starlette’s `Mount` does not evaluate template variables.  Therefore `/agents` is fixed, everything *after* is handled by the sub-app’s own router where `/{agent}/mcp/` is a *normal* path parameter.
* **Lifespan shimming**: We re-use the sub-app’s `lifespan` on the root so the FastMCP session-manager starts exactly once.

#### 4.4 Agent Execution Model

Each agent (Claude-Code, gemini-cli, etc.) runs a **client loop**:

1. `POST /agents/<me>/mcp/` with an MCP request that invokes `check_mail()`.
2. If a message is returned, process locally (LLM reasoning).
3. When replying, call `send_to_agent(<other>, <response>, msg_id=<same or new>)`.
4. Sleep *N* seconds or long-poll until the call finishes; repeat.

Optional enhancements:

* Use Server-Sent Events (`Accept: text/event-stream`) to reduce polling latency.
* Add bearer-token auth: check `Authorization` header inside the two tools.
* Back-pressure: limit mailbox depth per agent to prevent flooding.

### 5  Non-functional Requirements

| Requirement               | Comment                                                                                         |
| ------------------------- | ----------------------------------------------------------------------------------------------- |
| **Persistence**           | SQLite file is persisted locally; ensure durable storage in prod.                               |
| **Idempotency**           | `send_to_agent` must be safe on retry: primary‐key collision is OK.                             |
| **Security**              | Input sanitisation on `agent` names (path) and message payloads.                                |
| **Extensibility**         | Adding a new agent requires **no code change**—only point its LLM loop at `/agents/<new>/mcp/`. |
| **Observability**         | Add `created`/`processed` timestamps for basic latency metrics.                                 |
| **Licence compatibility** | FastMCP (Apache-2.0) and Starlette/FastAPI (BSD) impose no copyleft.                            |

---

### 6  Checklist to “definition-of-done”

1. **Database schema created automatically** on first import (`mq.py`).
2. **`uvicorn server:api` boots** with no warnings.
3. **Manual smoke test**:

   ```bash
   curl -X POST http://localhost:8000/agents/alice/mcp/ \
        -H "Content-Type: application/json" \
        -d '{"tool": "send_to_agent", "args": {"name":"bob","msg":"ping"}}'
   curl -X POST http://localhost:8000/agents/bob/mcp/ \
        -H "Content-Type: application/json" \
        -d '{"tool": "check_mail", "args": {}}'
   ```

   returns the “ping”.
4. **Two CLI loops** (one for Claude-Code, one for Gemini) exchange at least 20 messages without loss.
5. **No duplicate deliveries** under high (≥50 QPS) synthetic load.

Once these pass, the system is production-ready for lightweight inter-LLM mail.
