import uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastmcp import FastMCP
from fastmcp.server.http import create_streamable_http_app, _current_http_request
from mq import create_table, get_db

@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_table()
    yield

mcp = FastMCP("generic")

def _who_am_i() -> str:
    request: Request = _current_http_request.get()
    if not request:
        raise RuntimeError("Cannot determine agent name outside of a request context.")
    return request.path_params["agent"]

@mcp.tool
async def send_to_agent(name: str, msg: str, msg_id: str | None = None) -> str:
    """Sends a message to another agent."""
    if not name or not all(c.isalnum() or c in "-_" for c in name):
        raise ValueError("Invalid agent name.")
    
    db = await get_db()
    msg_id = msg_id or str(uuid.uuid4())
    from_agent = _who_am_i()
    
    await db.execute(
        "INSERT INTO messages (id, from_agent, to_agent, content) VALUES (?, ?, ?, ?)",
        (msg_id, from_agent, name, msg),
    )
    await db.commit()
    await db.close()
    return msg_id

@mcp.tool
async def check_mail() -> dict | None:
    """Checks for the oldest unread message."""
    db = await get_db()
    to_agent = _who_am_i()
    
    async with db.execute("BEGIN IMMEDIATE"):
        cursor = await db.execute(
            "SELECT id, from_agent, content FROM messages WHERE to_agent = ? AND done = 0 ORDER BY created LIMIT 1",
            (to_agent,),
        )
        row = await cursor.fetchone()
        
        if row:
            await db.execute("UPDATE messages SET done = 1 WHERE id = ?", (row["id"],))
            await db.commit()
    
    await db.close()
    
    if row:
        return dict(row)
    return None


sub_app = create_streamable_http_app(
    server=mcp,
    streamable_http_path="/{agent}/mcp/",
    json_response=True
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with sub_app.router.lifespan_context(app):
        await create_table()
        yield

api = FastAPI(lifespan=lifespan)
api.mount("/agents", sub_app)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(api, port=7777, host="0.0.0.0")
